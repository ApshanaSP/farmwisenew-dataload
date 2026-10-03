"""Google News RSS search fetcher (English + Tamil editions)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, tzinfo
from typing import Any
from urllib.parse import urlencode

import feedparser
import requests

from src.fetchers.base import SOURCE_TYPE_GOOGLE, FetchReport, make_raw_item
from src.http_client import HttpClient
from src.logging_setup import get_logger

log = get_logger("google_news")


def build_queries(gn_cfg: dict[str, Any], gaz_cfg: dict[str, Any]) -> list[tuple[str, str]]:
    """All (edition, query) pairs: core, topic and one-per-place queries.

    Args:
        gn_cfg: ``google_news`` config section.
        gaz_cfg: ``gazetteer`` config section (for place queries).
    """
    queries: list[tuple[str, str]] = []
    for group in ("core", "topics"):
        for edition, items in (gn_cfg.get("queries", {}).get(group) or {}).items():
            queries.extend((edition, q) for q in items or [])

    place_cfg = gn_cfg.get("place_queries") or {}
    if place_cfg.get("enabled"):
        for group, languages in (place_cfg.get("languages") or {}).items():
            for place in gaz_cfg.get(group) or []:
                for edition in languages or []:
                    if edition == "en":
                        queries.append(("en", place_cfg["template_en"].format(name=place["name"])))
                    elif edition == "ta" and place.get("aliases_ta"):
                        queries.append(("ta", place_cfg["template_ta"].format(name=place["aliases_ta"][0])))

    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for pair in queries:  # the same query string twice would just repeat a request
        if pair not in seen:
            seen.add(pair)
            unique.append(pair)
    return unique


def time_windows(mode: str, today: date, run_cfg: dict[str, Any]) -> list[str]:
    """Google search operators restricting the time range.

    daily    -> ["when:2d"]
    backfill -> ["after:YYYY-MM-DD before:YYYY-MM-DD", ...] covering ``backfill_days``.
    """
    if mode != "backfill":
        return [f"when:{run_cfg['daily_lookback']}"]
    days = int(run_cfg["backfill_days"])
    step = max(1, int(run_cfg["backfill_window_days"]))
    windows: list[str] = []
    start = today - timedelta(days=days)
    while start <= today:
        end = min(start + timedelta(days=step), today + timedelta(days=1))
        windows.append(f"after:{start:%Y-%m-%d} before:{end:%Y-%m-%d}")
        start = end
    return windows


def build_url(base_url: str, edition_params: dict[str, str], query: str) -> str:
    """Google News RSS search URL for one query + edition."""
    return f"{base_url}?{urlencode({'q': query, **edition_params})}"


def fetch_google_news(
    client: HttpClient,
    cfg: dict[str, Any],
    mode: str,
    run_date: date,
    tz: tzinfo,
) -> FetchReport:
    """Run every configured query for every time window.

    A failing query is logged and skipped; the rest continue.
    """
    report = FetchReport()
    gn_cfg = cfg["google_news"]
    if not gn_cfg.get("enabled", True):
        return report
    queries = build_queries(gn_cfg, cfg["gazetteer"])
    windows = time_windows(mode, run_date, cfg["run"])
    total = len(queries) * len(windows)
    log.info("Google News: %d queries x %d time windows = %d requests", len(queries), len(windows), total)

    done = 0
    for edition, query in queries:
        params = gn_cfg["editions"].get(edition)
        if not params:
            log.warning("No Google News edition '%s' configured; skipping query %r", edition, query)
            continue
        feed_label = f"Google News [{edition}]"
        for window in windows:
            done += 1
            url = build_url(gn_cfg["base_url"], params, f"{query} {window}")
            try:
                resp = client.get(url)
            except requests.RequestException as exc:
                log.warning("Google News query failed (%s | %s): %s", query, window, exc)
                report.failures.append(f"{feed_label}: {query} ({window}) -> {exc}")
                continue
            fetched_at = datetime.now(tz).replace(microsecond=0)
            parsed = feedparser.parse(resp.content)
            report.requests_ok += 1
            for entry in parsed.entries:
                source = entry.get("source") or {}
                report.items.append(
                    make_raw_item(
                        run_date=run_date,
                        fetched_at=fetched_at,
                        source_type=SOURCE_TYPE_GOOGLE,
                        source_name=feed_label,
                        source_feed=url,
                        query_used=query,
                        edition=edition,
                        title=entry.get("title"),
                        link=entry.get("link"),
                        published=entry.get("published") or entry.get("updated"),
                        summary=entry.get("summary"),
                        source_title=source.get("title"),
                        source_url=source.get("href"),
                        guid=entry.get("id"),
                        entry=dict(entry),
                    )
                )
                report.per_feed[feed_label] += 1
            if done % 25 == 0:
                log.info("Google News progress: %d/%d requests, %d items", done, total, len(report.items))
    return report
