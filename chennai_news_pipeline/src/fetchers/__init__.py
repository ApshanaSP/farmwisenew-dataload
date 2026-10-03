"""Source fetchers. ``fetch_all_sources`` runs every enabled source and merges results."""
from __future__ import annotations

from datetime import date, tzinfo
from typing import Any

from src.fetchers.api_sources import fetch_api_sources
from src.fetchers.base import FetchReport
from src.fetchers.google_news import fetch_google_news
from src.fetchers.rss_feeds import fetch_rss_feeds, fetch_sitemaps
from src.http_client import HttpClient
from src.logging_setup import get_logger

log = get_logger("fetchers")


def fetch_all_sources(
    client: HttpClient,
    cfg: dict[str, Any],
    env: dict[str, str],
    mode: str,
    run_date: date,
    tz: tzinfo,
) -> FetchReport:
    """Fetch publisher feeds, sitemaps, API connectors and Google News.

    Each source is isolated: an unexpected exception in one is logged and the
    others still run.
    """
    report = FetchReport()
    steps = [
        ("RSS feeds", lambda: fetch_rss_feeds(client, cfg.get("rss_feeds") or [], run_date, tz)),
        ("Sitemaps", lambda: fetch_sitemaps(client, cfg.get("sitemaps") or [], run_date, tz)),
        ("API connectors", lambda: fetch_api_sources(client, cfg.get("api_sources") or {}, env, mode, cfg["run"], run_date, tz)),
        ("Google News", lambda: fetch_google_news(client, cfg, mode, run_date, tz)),
    ]
    for label, step in steps:
        try:
            report.extend(step())
        except Exception:  # keep the run alive; details go to the log
            log.exception("%s fetcher crashed; continuing with other sources", label)
            report.failures.append(f"{label}: crashed (see log)")
    return report
