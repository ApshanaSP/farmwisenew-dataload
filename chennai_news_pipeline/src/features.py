"""Neutral feature extraction: language, text, time, source, location.

Nothing here ranks, scores, classifies or de-duplicates. Every function turns
cleaned article data into descriptive columns.
"""
from __future__ import annotations

import json
import unicodedata
from datetime import datetime
from typing import Any

from src.relevance_filter import is_tamil

PROCESSED_COLUMNS: list[str] = [
    "article_id", "fetched_at_ist", "published_at_ist", "published_date", "published_hour",
    "day_of_week", "age_hours", "title", "title_clean", "title_normalized", "summary_clean",
    "body_clean", "full_text", "body_extracted", "word_count", "char_count", "language",
    "has_tamil_text", "url", "canonical_url", "source_name", "source_domain", "source_type",
    "query_used", "mentioned_taluks", "mentioned_localities", "first_mentioned_place",
    "latitude", "longitude",
    # Added by src/classification.py (department + complaint flag).
    "department", "department_method", "department_confidence", "department_evidence",
    "is_complaint", "complaint_score", "complaint_cues", "complaint_method",
]
LIST_COLUMNS = ("mentioned_taluks", "mentioned_localities")

try:  # langdetect is optional; a script-based fallback is used without it.
    from langdetect import DetectorFactory, detect_langs
    from langdetect.lang_detect_exception import LangDetectException

    DetectorFactory.seed = 0  # deterministic results
    _HAS_LANGDETECT = True
except ImportError:  # pragma: no cover - exercised only when the package is missing
    _HAS_LANGDETECT = False


def _is_tamil_char(ch: str) -> bool:
    return "\u0b80" <= ch <= "\u0bff"


def detect_language(
    text: str,
    tamil_ratio_threshold: float,
    other_latin_min_probability: float,
    other_latin_min_chars: int,
) -> str:
    """Return ``"ta"``, ``"en"`` or ``"other"``.

    Tamil is decided by script share (reliable even for one-line headlines).
    Latin-script text defaults to ``"en"``: langdetect is unreliable on short
    headlines ("Legend Saravana plans Chennai's biggest store" -> "it"), so another
    Latin language is only reported when the text is long enough and langdetect is
    confident. Other scripts (Devanagari, Telugu, ...) are ``"other"``.

    Args:
        text: Title + body sample.
        tamil_ratio_threshold: Minimum share of Tamil letters/marks to return "ta".
        other_latin_min_probability: langdetect probability needed to call Latin text non-English.
        other_latin_min_chars: Minimum text length before langdetect is consulted.
    """
    letters = [ch for ch in text if unicodedata.category(ch)[0] in ("L", "M")]
    if not letters:
        return "other"
    tamil_share = sum(_is_tamil_char(ch) for ch in letters) / len(letters)
    if tamil_share >= tamil_ratio_threshold:
        return "ta"
    latin_share = sum(ch.isascii() for ch in letters) / len(letters)
    if latin_share < 0.8:
        return "other"
    if not _HAS_LANGDETECT or len(text) < other_latin_min_chars:
        return "en"
    try:
        top = detect_langs(text)[0]
    except (LangDetectException, IndexError):
        return "en"
    return "other" if top.lang != "en" and top.prob >= other_latin_min_probability else "en"


def text_features(title_clean: str, body_clean: str) -> dict[str, Any]:
    """full_text, word_count, char_count, has_tamil_text.

    When the body is identical to the title (Google News summaries repeat the
    headline) it is not appended again, so counts are not inflated.
    """
    if body_clean and body_clean != title_clean:
        full_text = f"{title_clean}\n{body_clean}".strip()
    else:
        full_text = title_clean
    return {
        "full_text": full_text,
        "word_count": len(full_text.split()),
        "char_count": len(full_text),
        "has_tamil_text": is_tamil(full_text),
    }


def time_features(published_ist: datetime | None, fetched_ist: datetime) -> dict[str, Any]:
    """published_date, published_hour, day_of_week, age_hours (at fetch time)."""
    if published_ist is None:
        return {
            "published_at_ist": "", "published_date": "", "published_hour": "",
            "day_of_week": "", "age_hours": "",
        }
    return {
        "published_at_ist": published_ist.isoformat(),
        "published_date": published_ist.date().isoformat(),
        "published_hour": published_ist.hour,
        "day_of_week": published_ist.strftime("%A"),
        "age_hours": round((fetched_ist - published_ist).total_seconds() / 3600, 2),
    }


def to_json_list(values: list[str]) -> str:
    """Serialise a list column as a JSON string (Tamil kept readable)."""
    return json.dumps(values, ensure_ascii=False)


def assemble_row(values: dict[str, Any]) -> dict[str, Any]:
    """Order a feature dict by PROCESSED_COLUMNS, JSON-encoding list columns."""
    row = {col: values.get(col, "") for col in PROCESSED_COLUMNS}
    for col in LIST_COLUMNS:
        if isinstance(row[col], list):
            row[col] = to_json_list(row[col])
    for col in ("latitude", "longitude"):
        if row[col] is None:
            row[col] = ""
    return row

