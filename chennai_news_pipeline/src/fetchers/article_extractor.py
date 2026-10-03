"""Full-article text extraction with trafilatura (robots.txt-aware, rate-limited)."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable

import requests
import trafilatura

from src.cleaning import domain_of
from src.http_client import HttpClient
from src.logging_setup import get_logger

log = get_logger("extract")

STATUS_OK = "ok"
STATUS_ROBOTS = "robots_disallowed"
STATUS_HTTP_ERROR = "http_error"
STATUS_EMPTY = "no_text_extracted"
STATUS_SKIPPED = "skipped"


class ArticleExtractor:
    """Downloads article pages and extracts the main text.

    Each distinct URL is downloaded at most once per run (a cache, not a dataset
    de-duplication: every row that points at the URL receives the same text).
    """

    def __init__(self, client: HttpClient, ext_cfg: dict[str, Any]) -> None:
        self.client = client
        self.respect_robots: bool = bool(ext_cfg["respect_robots_txt"])
        self.max_workers: int = int(ext_cfg["max_workers"])
        self.max_articles: int = int(ext_cfg["max_articles_per_run"])
        self.min_chars: int = int(ext_cfg["min_body_chars"])
        self.skip_domains: set[str] = {d.lower() for d in ext_cfg.get("skip_domains") or []}
        self.status_counts: Counter = Counter()

    def is_eligible(self, url: str) -> bool:
        """True for http(s) URLs outside ``skip_domains``."""
        return url.startswith(("http://", "https://")) and domain_of(url) not in self.skip_domains

    def _extract_one(self, url: str) -> tuple[str, str | None, str]:
        if self.respect_robots and not self.client.can_fetch(url):
            return url, None, STATUS_ROBOTS
        try:
            resp = self.client.get(url)
        except requests.RequestException as exc:
            log.debug("Article download failed %s: %s", url, exc)
            return url, None, STATUS_HTTP_ERROR
        try:
            text = trafilatura.extract(
                resp.text,
                url=url,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
            )
        except Exception as exc:  # trafilatura can raise on malformed HTML
            log.debug("trafilatura failed on %s: %s", url, exc)
            text = None
        if not text or len(text.strip()) < self.min_chars:
            return url, None, STATUS_EMPTY
        return url, text, STATUS_OK

    def extract_many(self, urls: Iterable[str]) -> dict[str, str | None]:
        """Extract text for many URLs concurrently (rate limits are per domain).

        Returns:
            Mapping url -> extracted text, or None when extraction was not possible.
        """
        unique = [u for u in dict.fromkeys(urls) if self.is_eligible(u)]
        selected, overflow = unique[: self.max_articles], unique[self.max_articles :]
        results: dict[str, str | None] = {u: None for u in overflow}
        self.status_counts[STATUS_SKIPPED] += len(overflow)
        if overflow:
            log.warning("Extraction cap reached: %d URLs keep only their RSS summary", len(overflow))
        log.info("Extracting article text from %d URLs (%d workers)", len(selected), self.max_workers)
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for i, (url, text, status) in enumerate(pool.map(self._extract_one, selected), start=1):
                results[url] = text
                self.status_counts[status] += 1
                if i % 50 == 0:
                    log.info("Extraction progress: %d/%d", i, len(selected))
        log.info("Extraction results: %s", dict(self.status_counts))
        return results
