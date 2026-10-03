"""Chennai-district relevance filter and gazetteer place matching.

This is the ONLY filtering step in the pipeline. It does not score or rank; it
answers one yes/no question: does the text mention Chennai or a Chennai-district
place from the gazetteer in config.yaml?
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

TAMIL_BLOCK = "\u0b80-\u0bff"
_TAMIL_CHAR_RE = re.compile(f"[{TAMIL_BLOCK}]")
_PULLI = "\u0bcd"
# Tamil vowel signs + pulli: what may follow a consonant stem in an inflected form.
_TAMIL_MARKS = "\u0bbe-\u0bcd"
_FLEX_SEP = r"[\s.\-]*"

CATEGORY_DISTRICT = "district"
CATEGORY_TALUK = "taluk"
CATEGORY_ZONE = "zone"
CATEGORY_LOCALITY = "locality"


@dataclass(frozen=True)
class Place:
    """One gazetteer entry."""

    name: str
    category: str
    latitude: float | None
    longitude: float | None
    requires_context: bool = False
    gcc_zone: int | None = None


@dataclass(frozen=True)
class Mention:
    """A place name found in a text."""

    place: Place
    start: int
    end: int
    matched_text: str


@dataclass
class RelevanceResult:
    """Outcome of the Chennai filter for one article."""

    is_relevant: bool
    reason: str
    mentions: list[Mention] = field(default_factory=list)
    excluded_phrases: list[str] = field(default_factory=list)

    @property
    def context_only_places(self) -> list[str]:
        """Names of context-dependent places found (useful for rejected-item review)."""
        return _unique(m.place.name for m in self.mentions if m.place.requires_context)


def _unique(values: Iterable[str]) -> list[str]:
    """De-duplicate while keeping first-seen order."""
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def is_tamil(text: str) -> bool:
    """True if the text contains at least one Tamil-script character."""
    return bool(_TAMIL_CHAR_RE.search(text))


def alias_pattern(alias: str, block_suffixes: Iterable[str] = ()) -> str:
    """Regex for one alias.

    English: case-insensitive whole-word match; spaces, dots and hyphens between
    tokens are optional/interchangeable.

    Tamil: must begin a word but may be followed by case suffixes (சென்னையில்).
    Aliases ending in pulli (e.g. மயிலாப்பூர்) are matched on the consonant stem
    followed by any vowel sign or pulli, so inflected forms (மயிலாப்பூரில்) match too.

    Args:
        alias: The spelling from config.
        block_suffixes: Tamil continuations that turn the match into another word.
    """
    tokens = [t for t in re.split(r"[\s.\-]+", alias.strip()) if t]
    if not tokens:
        raise ValueError(f"Empty alias: {alias!r}")
    if is_tamil(alias):
        lookahead = ""
        if tokens[-1].endswith(_PULLI) and len(tokens[-1]) > 1:
            tokens[-1] = tokens[-1][:-1]
            lookahead = f"(?=[{_TAMIL_MARKS}])"
        blocks = "".join(f"(?!{re.escape(s)})" for s in block_suffixes)
        body = _FLEX_SEP.join(re.escape(t) for t in tokens)
        return f"(?<![{TAMIL_BLOCK}]){body}{lookahead}{blocks}"
    body = _FLEX_SEP.join(re.escape(t) for t in tokens)
    return rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])"


class Gazetteer:
    """Compiled place-name matcher built from the ``gazetteer`` config section."""

    def __init__(self, entries: list[tuple[Place, list[str], list[str]]]) -> None:
        """
        Args:
            entries: (place, aliases, tamil_block_suffixes) triples.
        """
        self.places: dict[str, Place] = {}
        self._patterns: list[tuple[Place, re.Pattern[str]]] = []
        for place, aliases, block_suffixes in entries:
            self.places[place.name] = place
            for alias in aliases:
                if alias:
                    pattern = re.compile(alias_pattern(alias, block_suffixes), re.IGNORECASE)
                    self._patterns.append((place, pattern))
        self.district = next(p for p in self.places.values() if p.category == CATEGORY_DISTRICT)

    @classmethod
    def from_config(cls, gaz_cfg: dict[str, Any]) -> "Gazetteer":
        """Build from the ``gazetteer`` section of config.yaml."""
        entries: list[tuple[Place, list[str], list[str]]] = []

        def add(item: dict[str, Any], category: str) -> None:
            place = Place(
                name=item["name"],
                category=category,
                latitude=item.get("latitude"),
                longitude=item.get("longitude"),
                requires_context=bool(item.get("requires_context", False)),
                gcc_zone=item.get("gcc_zone"),
            )
            aliases = list(item.get("aliases_en") or []) + list(item.get("aliases_ta") or [])
            entries.append((place, aliases, list(item.get("ta_block_suffixes") or [])))

        add(gaz_cfg["district"], CATEGORY_DISTRICT)
        for key, category in (("taluks", CATEGORY_TALUK), ("zones", CATEGORY_ZONE), ("localities", CATEGORY_LOCALITY)):
            for item in gaz_cfg.get(key) or []:
                add(item, category)
        return cls(entries)

    def names(self, category: str) -> list[str]:
        """All place names of one category, in config order."""
        return [p.name for p in self.places.values() if p.category == category]

    def find_mentions(self, text: str) -> list[Mention]:
        """Every gazetteer mention in ``text``, ordered by position.

        Overlapping matches (e.g. "Chennai" inside "Greater Chennai Corporation")
        are reduced to the longest span so a single phrase is not counted twice.
        """
        if not text:
            return []
        raw: list[Mention] = []
        for place, pattern in self._patterns:
            for m in pattern.finditer(text):
                raw.append(Mention(place, m.start(), m.end(), m.group(0)))
        raw.sort(key=lambda m: (m.start, -(m.end - m.start)))
        result: list[Mention] = []
        last_end = -1
        for mention in raw:
            if mention.start >= last_end:
                result.append(mention)
                last_end = mention.end
        return result


class RelevanceFilter:
    """Keeps articles that mention Chennai / a Chennai-district place."""

    def __init__(self, gazetteer: Gazetteer, exclusion_phrases: Iterable[str]) -> None:
        self.gazetteer = gazetteer
        self._exclusions: list[tuple[str, re.Pattern[str]]] = [
            (phrase, re.compile(alias_pattern(phrase), re.IGNORECASE)) for phrase in exclusion_phrases if phrase
        ]

    def mask_exclusions(self, text: str) -> tuple[str, list[str]]:
        """Blank out exclusion phrases (keeping character positions stable).

        Returns:
            (masked_text, exclusion phrases that were found).
        """
        found: list[str] = []
        for phrase, pattern in self._exclusions:
            if pattern.search(text):
                found.append(phrase)
                text = pattern.sub(lambda m: " " * len(m.group(0)), text)
        return text, found

    def evaluate(self, text: str) -> RelevanceResult:
        """Decide whether ``text`` is Chennai-district relevant.

        Rejection reasons:
            * ``only_context_dependent_places`` - only names shared with other regions
              (e.g. "Manali", "Anna Nagar") were found.
            * ``only_excluded_phrases`` - the only Chennai mention was inside an excluded
              phrase (e.g. "Chennai Super Kings").
            * ``no_gazetteer_mention`` - nothing from the gazetteer.
        """
        masked, excluded = self.mask_exclusions(text or "")
        mentions = self.gazetteer.find_mentions(masked)
        if any(not m.place.requires_context for m in mentions):
            return RelevanceResult(True, "matched", mentions, excluded)
        if mentions:
            return RelevanceResult(False, "only_context_dependent_places", mentions, excluded)
        if excluded:
            return RelevanceResult(False, "only_excluded_phrases", mentions, excluded)
        return RelevanceResult(False, "no_gazetteer_mention", mentions, excluded)


def location_features(mentions: list[Mention], gazetteer: Gazetteer, include_district: bool) -> dict[str, Any]:
    """Neutral location features: every place mentioned, nothing chosen or scored.

    Args:
        mentions: Output of ``Gazetteer.find_mentions`` (position order).
        gazetteer: Used for centroid lookup.
        include_district: Whether the district name itself may be ``first_mentioned_place``.

    Returns:
        mentioned_taluks, mentioned_localities (GCC zones + localities),
        first_mentioned_place, latitude, longitude.
    """
    taluks = _unique(m.place.name for m in mentions if m.place.category == CATEGORY_TALUK)
    localities = _unique(
        m.place.name for m in mentions if m.place.category in (CATEGORY_ZONE, CATEGORY_LOCALITY)
    )
    first = next(
        (m.place for m in mentions if include_district or m.place.category != CATEGORY_DISTRICT),
        None,
    )
    return {
        "mentioned_taluks": taluks,
        "mentioned_localities": localities,
        "first_mentioned_place": first.name if first else "",
        "latitude": first.latitude if first else None,
        "longitude": first.longitude if first else None,
    }
