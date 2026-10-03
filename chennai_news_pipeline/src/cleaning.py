"""Text, date and URL cleaning. Pure functions - no network, no file I/O."""
from __future__ import annotations

import base64
import binascii
import html
import re
import time
import unicodedata
from datetime import datetime, timezone, tzinfo
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dateutil import parser as date_parser

_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_BLOCK_TAG_RE = re.compile(r"<\s*/?\s*(br|p|div|li|ul|ol|h[1-6]|tr|td|blockquote)\b[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_ZERO_WIDTH_RE = re.compile("[\u200b\u2060\ufeff\u200e\u200f\u00ad]")
_WS_RE = re.compile(r"\s+")
_GOOGLE_HOSTS = {"news.google.com"}
_GOOGLE_ARTICLE_RE = re.compile(r"/(?:rss/)?articles/([A-Za-z0-9_\-]+)")


# ----------------------------------------------------------------------------- text
def strip_html(text: str) -> str:
    """Remove script/style blocks and all tags; block-level tags become spaces."""
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _BLOCK_TAG_RE.sub(" ", text)
    return _TAG_RE.sub("", text)


def normalize_unicode(text: str) -> str:
    """NFKC-normalise and drop zero-width / soft-hyphen characters.

    NFKC folds compatibility characters (non-breaking spaces, full-width forms,
    ligatures) and composes Tamil vowel signs into their canonical form.
    """
    return _ZERO_WIDTH_RE.sub("", unicodedata.normalize("NFKC", text))


def normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs to a single space and trim."""
    return _WS_RE.sub(" ", text).strip()


def clean_text(value: object) -> str:
    """Full cleaning for any text field: entities, HTML, unicode, whitespace.

    Entities are unescaped before and after tag removal so that double-escaped
    feeds (``&amp;lt;b&amp;gt;``) and escaped markup are both handled.
    """
    if value is None:
        return ""
    text = str(value)
    for _ in range(2):
        if "&" not in text:
            break
        text = html.unescape(text)
    text = html.unescape(strip_html(text))
    return normalize_whitespace(normalize_unicode(text))


def normalize_title(text: str) -> str:
    """Lower-case, punctuation/symbol-free version of a title for later matching.

    Tamil vowel signs and virama are Unicode *marks*, not punctuation, so they
    are kept intact.
    """
    cleaned = clean_text(text).casefold()
    chars = (" " if unicodedata.category(ch)[0] in ("P", "S") else ch for ch in cleaned)
    return normalize_whitespace("".join(chars))


def strip_source_suffix(title: str, source_name: str) -> str:
    """Remove the " - Publisher" suffix that Google News appends to titles."""
    if source_name:
        for sep in (" - ", " – ", " | "):
            suffix = f"{sep}{source_name}"
            if title.endswith(suffix):
                return title[: -len(suffix)].rstrip()
    return title


# ----------------------------------------------------------------------------- dates
def parse_datetime(value: object, naive_tz: tzinfo) -> datetime | None:
    """Parse RSS/Atom/ISO/epoch-struct dates into an aware datetime.

    Args:
        value: RFC 822 string, ISO 8601 string, ``time.struct_time`` (UTC, as produced
            by feedparser) or ``datetime``.
        naive_tz: Zone assumed for timestamps that carry no offset.

    Returns:
        Timezone-aware datetime, or None if unparseable/empty.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, time.struct_time):
        dt = datetime(*value[:6], tzinfo=timezone.utc)
    else:
        text = str(value).strip()
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            try:
                dt = date_parser.parse(text)
            except (ValueError, OverflowError):
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=naive_tz)
    return dt


def to_timezone(dt: datetime | None, tz: tzinfo) -> datetime | None:
    """Convert an aware datetime to ``tz`` (seconds precision)."""
    return dt.astimezone(tz).replace(microsecond=0) if dt else None


# ----------------------------------------------------------------------------- urls
def decode_google_news_url(url: str) -> str | None:
    """Decode a Google News article link to the publisher URL *without any request*.

    Older Google News links embed the target URL inside a base64 protobuf. Newer
    links (tokens starting with ``AU_yqL``) only contain an opaque ID that Google
    resolves server-side; those return None.

    Args:
        url: A ``news.google.com/rss/articles/...`` link.

    Returns:
        The publisher URL, or None when it cannot be decoded offline.
    """
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in _GOOGLE_HOSTS:
        return None
    match = _GOOGLE_ARTICLE_RE.search(parts.path)
    if not match:
        return None
    token = match.group(1)
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (binascii.Error, ValueError):
        return None
    start = raw.find(b"http")
    if start == -1:
        return None
    end = start
    while end < len(raw) and 0x21 <= raw[end] <= 0x7E:
        end += 1
    candidate = raw[start:end].decode("ascii", errors="ignore")
    if not candidate.startswith(("http://", "https://")):
        return None
    host = urlsplit(candidate).hostname or ""
    return candidate if "." in host else None


def is_tracking_param(name: str, params: Iterable[str], prefixes: Iterable[str]) -> bool:
    """True when a query parameter is a known tracking parameter."""
    lowered = name.lower()
    return lowered in {p.lower() for p in params} or any(lowered.startswith(p.lower()) for p in prefixes)


def canonicalize_url(
    url: str,
    tracking_params: Iterable[str] = (),
    tracking_prefixes: Iterable[str] = (),
    resolve_google: bool = True,
) -> str:
    """Canonical form of a URL.

    Steps: decode Google News redirect (offline, where possible); lower-case scheme and
    host; drop default ports, fragments and tracking parameters; sort the remaining
    query parameters.
    """
    if not url:
        return ""
    url = url.strip()
    if resolve_google:
        url = decode_google_news_url(url) or url
    parts = urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host if port is None or (scheme, port) in (("http", 80), ("https", 443)) else f"{host}:{port}"
    params = list(tracking_params)
    prefixes = list(tracking_prefixes)
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not is_tracking_param(k, params, prefixes)
    )
    return urlunsplit((scheme, netloc, parts.path or "/", urlencode(query, doseq=True), ""))


def domain_of(url: str) -> str:
    """Host name of a URL without a leading ``www.``."""
    host = (urlsplit(url).hostname or "").lower() if url else ""
    return host[4:] if host.startswith("www.") else host


def is_google_news_url(url: str) -> bool:
    """True for links that still point at news.google.com."""
    return domain_of(url) in _GOOGLE_HOSTS
