"""Publisher RSS feeds and news sitemaps."""
from __future__ import annotations

import re
from datetime import date, datetime, tzinfo
from typing import Any

import feedparser
import requests
from lxml import etree

from src.fetchers.base import SOURCE_TYPE_RSS, SOURCE_TYPE_SITEMAP, FetchReport, make_raw_item
from src.http_client import HttpClient
from src.logging_setup import get_logger

log = get_logger("rss")

_SITEMAP_NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
    "image": "http://www.google.com/schemas/sitemap-image/1.1",
}


def fetch_rss_feeds(client: HttpClient, feeds: list[dict[str, Any]], run_date: date, tz: tzinfo) -> FetchReport:
    """Fetch every configured publisher RSS feed. Failures are logged and skipped."""
    report = FetchReport()
    for feed in feeds or []:
        name, url = feed["name"], feed["url"]
        try:
            resp = client.get(url)
        except requests.RequestException as exc:
            log.warning("RSS feed failed (%s): %s", name, exc)
            report.failures.append(f"{name}: {exc}")
            continue
        fetched_at = datetime.now(tz).replace(microsecond=0)
        parsed = feedparser.parse(resp.content)
        if not parsed.entries:
            log.warning("RSS feed %s returned no entries (bozo=%s)", name, getattr(parsed, "bozo_exception", ""))
        report.requests_ok += 1
        for entry in parsed.entries:
            report.items.append(
                make_raw_item(
                    run_date=run_date,
                    fetched_at=fetched_at,
                    source_type=SOURCE_TYPE_RSS,
                    source_name=name,
                    source_feed=url,
                    edition=feed.get("language", ""),
                    title=entry.get("title"),
                    link=entry.get("link"),
                    published=entry.get("published") or entry.get("updated"),
                    summary=entry.get("summary") or entry.get("description"),
                    source_title=name,
                    source_url=parsed.feed.get("link", ""),
                    guid=entry.get("id"),
                    entry=dict(entry),
                )
            )
            report.per_feed[name] += 1
        log.info("RSS %-24s %4d items", name, len(parsed.entries))
    return report


def _text(node: etree._Element, path: str) -> str:
    found = node.find(path, _SITEMAP_NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def fetch_sitemaps(client: HttpClient, sitemaps: list[dict[str, Any]], run_date: date, tz: tzinfo) -> FetchReport:
    """Fetch news sitemaps (for publishers without RSS).

    Only ``<url>`` entries whose location matches ``article_url_pattern`` are
    articles; section pages and the home page listed in the same sitemap are not
    news items and are not emitted.
    """
    report = FetchReport()
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True, huge_tree=True)
    for sm in sitemaps or []:
        name, url = sm["name"], sm["url"]
        pattern = re.compile(sm.get("article_url_pattern") or ".")
        try:
            resp = client.get(url)
            root = etree.fromstring(resp.content, parser)
        except (requests.RequestException, etree.XMLSyntaxError) as exc:
            log.warning("Sitemap failed (%s): %s", name, exc)
            report.failures.append(f"{name}: {exc}")
            continue
        fetched_at = datetime.now(tz).replace(microsecond=0)
        report.requests_ok += 1
        count = 0
        for node in root.findall("sm:url", _SITEMAP_NS) if root is not None else []:
            loc = _text(node, "sm:loc")
            if not loc or not pattern.search(loc):
                continue
            title = _text(node, "news:news/news:title") or _text(node, "image:image/image:title")
            published = _text(node, "news:news/news:publication_date") or _text(node, "sm:lastmod")
            entry = {
                "loc": loc,
                "lastmod": _text(node, "sm:lastmod"),
                "news_title": _text(node, "news:news/news:title"),
                "news_publication_date": _text(node, "news:news/news:publication_date"),
                "image_title": _text(node, "image:image/image:title"),
                "image_loc": _text(node, "image:image/image:loc"),
            }
            report.items.append(
                make_raw_item(
                    run_date=run_date,
                    fetched_at=fetched_at,
                    source_type=SOURCE_TYPE_SITEMAP,
                    source_name=name,
                    source_feed=url,
                    edition=sm.get("language", ""),
                    title=title,
                    link=loc,
                    published=published,
                    summary="",
                    source_title=name,
                    source_url=url,
                    guid=loc,
                    entry=entry,
                )
            )
            report.per_feed[name] += 1
            count += 1
        log.info("Sitemap %-20s %4d items", name, count)
    return report
