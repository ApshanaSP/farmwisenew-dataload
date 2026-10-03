"""Shared HTTP client: retries with backoff, per-domain rate limiting, robots.txt checks."""
from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.logging_setup import get_logger

log = get_logger("http")


def enable_system_trust_store(enabled: bool) -> None:
    """Make Python verify TLS against the operating-system certificate store.

    Needed on machines where antivirus software or a proxy re-signs HTTPS traffic
    with its own root certificate (trusted by Windows, unknown to ``certifi``).

    Args:
        enabled: Value of ``http.use_system_trust_store`` in config.
    """
    if not enabled:
        return
    try:
        import truststore

        truststore.inject_into_ssl()
        log.debug("Using the operating-system certificate store (truststore).")
    except ImportError:
        log.warning("truststore is not installed; falling back to certifi CA bundle.")


class DomainRateLimiter:
    """Thread-safe minimum interval between requests to the same host."""

    def __init__(self, default_interval: float, overrides: dict[str, float] | None = None) -> None:
        self._default = default_interval
        self._overrides = dict(overrides or {})
        self._next_slot: dict[str, float] = {}
        self._lock = threading.Lock()

    def set_interval(self, host: str, seconds: float) -> None:
        """Raise the interval for a host (e.g. to honour robots.txt Crawl-delay)."""
        with self._lock:
            self._overrides[host] = max(seconds, self._overrides.get(host, self._default))

    def wait(self, host: str) -> None:
        """Block until a request to ``host`` is allowed."""
        with self._lock:
            interval = self._overrides.get(host, self._default)
            now = time.monotonic()
            slot = max(now, self._next_slot.get(host, 0.0))
            self._next_slot[host] = slot + interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


class HttpClient:
    """requests.Session wrapper used by every fetcher."""

    def __init__(self, http_cfg: dict[str, Any]) -> None:
        """
        Args:
            http_cfg: The ``http`` section of config.yaml.
        """
        self.timeout: float = float(http_cfg["timeout_seconds"])
        self.robots_agent: str = http_cfg["robots_user_agent"]
        self.limiter = DomainRateLimiter(
            float(http_cfg["default_min_interval_seconds"]),
            http_cfg.get("per_domain_min_interval_seconds") or {},
        )
        retry = Retry(
            total=int(http_cfg["retries"]),
            backoff_factor=float(http_cfg["backoff_factor"]),
            status_forcelist=list(http_cfg["retry_on_status"]),
            allowed_methods=["GET"],
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
        self.session = requests.Session()
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(
            {
                "User-Agent": http_cfg["user_agent"],
                "Accept-Language": "en-IN,en;q=0.9,ta;q=0.8",
            }
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_lock = threading.Lock()

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        """Rate-limited GET with retries. Raises ``requests.HTTPError`` on 4xx/5xx.

        Args:
            url: URL to fetch.
            **kwargs: Passed to ``requests.Session.get`` (params, headers, ...).
        """
        host = (urlsplit(url).hostname or "").lower()
        self.limiter.wait(host)
        resp = self.session.get(url, timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        return resp

    def _load_robots(self, scheme: str, netloc: str) -> RobotFileParser | None:
        """Download and parse robots.txt. Returns None when it could not be reached."""
        robots_url = f"{scheme}://{netloc}/robots.txt"
        parser = RobotFileParser(robots_url)
        try:
            self.limiter.wait(netloc.lower())
            resp = self.session.get(robots_url, timeout=self.timeout)
        except requests.RequestException as exc:
            log.warning("robots.txt unreachable for %s (%s); skipping that site.", netloc, exc)
            return None
        # Same conventions as urllib.robotparser.RobotFileParser.read().
        if resp.status_code in (401, 403):
            parser.disallow_all = True
        elif 400 <= resp.status_code < 500:
            parser.allow_all = True
        elif resp.status_code >= 500:
            parser.disallow_all = True
        else:
            parser.parse(resp.text.splitlines())
            delay = parser.crawl_delay(self.robots_agent)
            if delay:
                self.limiter.set_interval(netloc.lower(), float(delay))
        return parser

    def can_fetch(self, url: str) -> bool:
        """True when robots.txt of the URL's site allows our user agent to fetch it."""
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return False
        key = f"{parts.scheme}://{parts.netloc}"
        with self._robots_lock:
            if key not in self._robots:
                self._robots[key] = self._load_robots(parts.scheme, parts.netloc)
            parser = self._robots[key]
        return parser is not None and parser.can_fetch(self.robots_agent, url)
