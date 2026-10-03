"""Common raw-item structure and fetch bookkeeping shared by all fetchers."""
from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

RAW_COLUMNS: list[str] = [
    "article_id", "fetched_at", "source_type", "source_name", "source_feed", "query_used",
    "edition", "title", "link", "published", "summary", "source_title", "source_url",
    "guid", "entry_json",
]

SOURCE_TYPE_GOOGLE = "google_news"
SOURCE_TYPE_RSS = "rss"
SOURCE_TYPE_SITEMAP = "sitemap"
SOURCE_TYPE_API = "api"


def run_id_prefix(run_date: date) -> str:
    """Prefix shared by every article_id of one run date (used to overwrite re-runs)."""
    return f"{run_date:%Y%m%d}-"


def new_article_id(run_date: date) -> str:
    """Unique ID per fetched row. NOT a content hash: identical articles get different IDs."""
    return f"{run_id_prefix(run_date)}{uuid.uuid4().hex[:16]}"


def _jsonable(value: Any) -> Any:
    """Fallback serialiser for feedparser objects (struct_time, bytes, ...)."""
    if hasattr(value, "tm_year"):
        return list(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def entry_to_json(entry: Any) -> str:
    """Serialise a feed entry / API record exactly as received."""
    return json.dumps(entry, ensure_ascii=False, default=_jsonable)


def make_raw_item(
    *,
    run_date: date,
    fetched_at: datetime,
    source_type: str,
    source_name: str,
    source_feed: str,
    query_used: str = "",
    edition: str = "",
    title: Any = "",
    link: Any = "",
    published: Any = "",
    summary: Any = "",
    source_title: Any = "",
    source_url: Any = "",
    guid: Any = "",
    entry: Any = None,
) -> dict[str, Any]:
    """Build one raw row. Field values are stored as received (no cleaning)."""
    return {
        "article_id": new_article_id(run_date),
        "fetched_at": fetched_at.isoformat(),
        "source_type": source_type,
        "source_name": source_name,
        "source_feed": source_feed,
        "query_used": query_used,
        "edition": edition,
        "title": "" if title is None else str(title),
        "link": "" if link is None else str(link),
        "published": "" if published is None else str(published),
        "summary": "" if summary is None else str(summary),
        "source_title": "" if source_title is None else str(source_title),
        "source_url": "" if source_url is None else str(source_url),
        "guid": "" if guid is None else str(guid),
        "entry_json": entry_to_json(entry) if entry is not None else "",
    }


@dataclass
class FetchReport:
    """Items plus per-feed bookkeeping for the end-of-run summary."""

    items: list[dict[str, Any]] = field(default_factory=list)
    per_feed: Counter = field(default_factory=Counter)
    requests_ok: int = 0
    failures: list[str] = field(default_factory=list)

    def extend(self, other: "FetchReport") -> None:
        """Merge another report into this one."""
        self.items.extend(other.items)
        self.per_feed.update(other.per_feed)
        self.requests_ok += other.requests_ok
        self.failures.extend(other.failures)
