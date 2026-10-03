"""Turn one raw row into a processed row (or a rejected row).

Steps per row: clean -> attach extracted body -> Chennai filter -> neutral features.
No row is ever merged with, compared to or ranked against another row.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any

from src.cleaning import (
    canonicalize_url,
    clean_text,
    domain_of,
    is_google_news_url,
    normalize_title,
    parse_datetime,
    strip_source_suffix,
    to_timezone,
)
from src.features import assemble_row, detect_language, text_features, time_features
from src.fetchers.base import SOURCE_TYPE_GOOGLE
from src.relevance_filter import RelevanceFilter, RelevanceResult, location_features


@dataclass
class CleanedItem:
    """Cleaned view of one raw row, before the body is attached."""

    raw: dict[str, Any]
    title_clean: str
    summary_clean: str
    url: str
    canonical_url: str
    source_name: str
    source_domain: str
    published_ist: datetime | None
    fetched_ist: datetime


def clean_item(raw: dict[str, Any], cfg: dict[str, Any], tz: tzinfo, naive_tz: tzinfo) -> CleanedItem:
    """Clean text, dates, URL and source fields of a raw row.

    For Google News rows the publisher comes from the ``<source>`` element and the
    " - Publisher" title suffix is removed; ``source_domain`` is the publisher's
    domain even when the article link itself could not be resolved.
    """
    url_cfg = cfg["url_cleaning"]
    is_google = raw["source_type"] == SOURCE_TYPE_GOOGLE
    source_name = clean_text(raw.get("source_title")) or raw["source_name"]
    title_clean = clean_text(raw.get("title"))
    summary_clean = clean_text(raw.get("summary"))
    if is_google:
        title_clean = strip_source_suffix(title_clean, source_name)
        # Google's summary is "<a>title</a>&nbsp;<font>publisher</font>": drop the publisher tail.
        if source_name and summary_clean.endswith(source_name):
            summary_clean = summary_clean[: -len(source_name)].rstrip()
    canonical = canonicalize_url(
        raw.get("link", ""),
        url_cfg["tracking_params"],
        url_cfg["tracking_param_prefixes"],
        resolve_google=url_cfg["resolve_google_news_offline"],
    )
    if is_google and is_google_news_url(canonical):
        source_domain = domain_of(raw.get("source_url", "")) or domain_of(canonical)
    else:
        source_domain = domain_of(canonical)
    return CleanedItem(
        raw=raw,
        title_clean=title_clean,
        summary_clean=summary_clean,
        url=raw.get("link", ""),
        canonical_url=canonical,
        source_name=source_name,
        source_domain=source_domain,
        published_ist=to_timezone(parse_datetime(raw.get("published"), naive_tz), tz),
        fetched_ist=datetime.fromisoformat(raw["fetched_at"]).astimezone(tz),
    )


def match_text(title: str, summary: str, body: str) -> str:
    """Text searched for gazetteer places: title, summary and body (no repeats)."""
    parts: list[str] = []
    for part in (title, summary, body):
        if part and part not in parts:
            parts.append(part)
    return "\n".join(parts)


def build_rows(
    item: CleanedItem,
    body_text: str | None,
    relevance: RelevanceFilter,
    cfg: dict[str, Any],
) -> tuple[bool, dict[str, Any], RelevanceResult]:
    """Build the processed row (if relevant) or the rejected row.

    Returns:
        (is_relevant, row, relevance_result)
    """
    body_extracted = bool(body_text)
    body_clean = clean_text(body_text) if body_extracted else item.summary_clean
    lang_cfg = cfg["language"]
    language = detect_language(
        f"{item.title_clean} {body_clean}"[: int(lang_cfg["sample_chars"])],
        float(lang_cfg["tamil_ratio_threshold"]),
        float(lang_cfg["other_latin_min_probability"]),
        int(lang_cfg["other_latin_min_chars"]),
    )
    result = relevance.evaluate(match_text(item.title_clean, item.summary_clean, body_clean))
    times = time_features(item.published_ist, item.fetched_ist)
    base = {
        "article_id": item.raw["article_id"],
        "fetched_at_ist": item.fetched_ist.isoformat(),
        "title": item.raw.get("title", ""),
        "url": item.url,
        "canonical_url": item.canonical_url,
        "source_name": item.source_name,
        "source_domain": item.source_domain,
        "source_type": item.raw["source_type"],
        "query_used": item.raw.get("query_used", ""),
        "language": language,
        **times,
    }
    if not result.is_relevant:
        rejected = {
            **base,
            "title": item.title_clean,
            "rejection_reason": result.reason,
            "excluded_phrases_found": json.dumps(result.excluded_phrases, ensure_ascii=False),
            "context_only_places_found": json.dumps(result.context_only_places, ensure_ascii=False),
        }
        return False, rejected, result

    processed = assemble_row(
        {
            **base,
            "title_clean": item.title_clean,
            "title_normalized": normalize_title(item.title_clean),
            "summary_clean": item.summary_clean,
            "body_clean": body_clean,
            "body_extracted": body_extracted,
            **text_features(item.title_clean, body_clean),
            **location_features(
                result.mentions,
                relevance.gazetteer,
                bool(cfg["relevance"].get("first_place_includes_district", True)),
            ),
        }
    )
    return True, processed, result
