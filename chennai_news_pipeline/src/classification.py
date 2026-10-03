"""Department classification and complaint detection for processed news rows.

Three layers, strongest available wins:

1. **LLM (optional)** - OpenAI chat model (same provider as the dashboard's
   ``ai-classifier.ts``), used only when ``OPENAI_API_KEY`` is set. It must answer
   with a department from the configured list; anything else is discarded.
2. **Keyword model (always)** - weighted English + Tamil terms per department,
   title matches weighted higher. Deterministic and explainable.
3. **Embedding model (optional, offline)** - for English rows with no keyword hit,
   cosine similarity between the article and each department description using a
   locally cached sentence-transformers model. Never downloads anything.

Rows that none of the layers can place get ``fallback_department`` with
``department_method = "fallback"`` so they are easy to filter or review.

Complaint detection is a scored lexicon (grievance + problem cues minus
announcement / crime-report cues), or the LLM's judgement when the LLM is enabled.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import requests

from src.logging_setup import get_logger
from src.relevance_filter import alias_pattern, is_tamil

log = get_logger("classify")

_FLEX_SEP = r"[\s.\-]*"
# Tamil sandhi: a doubled hard consonant (க் ச் த் ப்) may join two words,
# e.g. காலை உணவு திட்டம் is often written காலை உணவுத் திட்டம்.
_PULLI = chr(0x0BCD)
_TAMIL_SANDHI = "(?:[" + "".join(chr(c) for c in (0x0B95, 0x0B9A, 0x0BA4, 0x0BAA)) + "]" + _PULLI + ")?"

METHOD_LLM = "llm"
METHOD_KEYWORD = "keyword"
METHOD_EMBEDDING = "embedding"
METHOD_FALLBACK = "fallback"
METHOD_RULES = "rules"

CLASSIFICATION_COLUMNS: list[str] = [
    "department", "department_method", "department_confidence", "department_evidence",
    "is_complaint", "complaint_score", "complaint_cues", "complaint_method",
]


def term_pattern(term: str) -> re.Pattern[str]:
    """Compile one lexicon term.

    English terms match whole words plus the plural/past suffixes s, es, ed, d
    (pothole -> potholes, flood -> flooded). "-ing" is not added automatically
    ("park" must not match "parking"); list -ing forms explicitly. Short all-caps
    acronyms (ROB, SWD, JCB) match exactly and case-sensitively ("ROB" is not "rob").
    Tamil terms reuse the gazetteer rules (word start, case suffixes and inflected
    pulli endings allowed).
    """
    if is_tamil(term):
        return re.compile(alias_pattern(term).replace(_FLEX_SEP, _TAMIL_SANDHI + _FLEX_SEP), re.IGNORECASE)
    if term.isupper() and len(term) <= 6:
        return re.compile(alias_pattern(term))
    base = alias_pattern(term)
    # alias_pattern ends with a negative lookahead for a word character; allow
    # common English suffixes before it.
    suffix_at = base.rfind("(?![A-Za-z0-9])")
    pattern = base[:suffix_at] + r"(?:s|es|ed|d)?" + base[suffix_at:]
    return re.compile(pattern, re.IGNORECASE)


@dataclass
class WeightedLexicon:
    """A list of (term, weight, compiled pattern)."""

    entries: list[tuple[str, float, re.Pattern[str]]] = field(default_factory=list)

    @classmethod
    def build(cls, weighted_terms: Iterable[tuple[str, float]]) -> "WeightedLexicon":
        seen: set[str] = set()
        entries = []
        for term, weight in weighted_terms:
            key = term.strip().lower()
            if key and key not in seen:
                seen.add(key)
                entries.append((term, weight, term_pattern(term)))
        return cls(entries)

    def matches(self, text: str) -> list[tuple[str, float]]:
        """Distinct terms found in ``text`` with their weights."""
        return [(term, weight) for term, weight, pattern in self.entries if pattern.search(text)]


def _weighted(cfg: dict[str, Any], strong_keys: tuple[str, ...], weak_keys: tuple[str, ...],
              strong: float, weak: float) -> list[tuple[str, float]]:
    terms: list[tuple[str, float]] = []
    for key in strong_keys:
        terms.extend((t, strong) for t in cfg.get(key) or [])
    for key in weak_keys:
        terms.extend((t, weak) for t in cfg.get(key) or [])
    return terms


# ----------------------------------------------------------------------------- departments
@dataclass
class DepartmentResult:
    department: str
    method: str
    confidence: float
    evidence: list[str]


class KeywordDepartmentModel:
    """Weighted bilingual keyword scoring, one lexicon per department."""

    def __init__(self, dept_cfgs: list[dict[str, Any]], title_weight: float, strong: float, weak: float,
                 min_weak_terms: int = 2) -> None:
        self.names = [d["name"] for d in dept_cfgs]
        self.title_weight = title_weight
        self.strong_weight = strong
        self.min_weak_terms = min_weak_terms
        self.lexicons = {
            d["name"]: WeightedLexicon.build(
                _weighted(d, ("en", "ta"), ("en_weak", "ta_weak"), strong, weak)
            )
            for d in dept_cfgs
        }

    def score(self, title: str, text: str, min_weak_terms: int | None = None) -> tuple[Counter, dict[str, list[str]]]:
        """Score every eligible department. Title hits count ``title_weight`` times.

        A department is eligible only with at least one strong term or
        ``min_weak_terms`` distinct weak terms, so one generic word ("road",
        "hospital") never decides the department on its own.
        """
        scores: Counter = Counter()
        evidence: dict[str, list[str]] = {}
        for name, lexicon in self.lexicons.items():
            body_hits = dict(lexicon.matches(text))
            if not body_hits:
                continue
            strong_hits = [t for t, w in body_hits.items() if w >= self.strong_weight]
            needed = self.min_weak_terms if min_weak_terms is None else min_weak_terms
            if not strong_hits and len(body_hits) < needed:
                continue
            title_hits = dict(lexicon.matches(title))
            scores[name] = sum(body_hits.values()) + sum(w * (self.title_weight - 1) for w in title_hits.values())
            evidence[name] = list(body_hits)
        return scores, evidence

    def classify(self, title: str, text: str, min_weak_terms: int | None = None) -> DepartmentResult | None:
        scores, evidence = self.score(title, text, min_weak_terms)
        if not scores:
            return None
        ranked = scores.most_common()
        best, best_score = ranked[0]
        # Ties go to the department listed first in config (stable, documented).
        tied = [n for n, s in ranked if s == best_score]
        best = min(tied, key=self.names.index)
        confidence = round(best_score / sum(scores.values()), 3)
        return DepartmentResult(best, METHOD_KEYWORD, confidence, evidence[best])


class EmbeddingDepartmentModel:
    """Zero-shot similarity against department descriptions with a cached local model."""

    def __init__(self, model_name: str, dept_cfgs: list[dict[str, Any]]) -> None:
        # Never reach out to the internet: only an already-cached model is used.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name, device="cpu", local_files_only=True)
        self.names = [d["name"] for d in dept_cfgs]
        descriptions = [f"{d['name']}: {d['description']}" for d in dept_cfgs]
        self.dept_vectors = self.model.encode(descriptions, normalize_embeddings=True, show_progress_bar=False)

    @classmethod
    def try_load(cls, model_name: str, dept_cfgs: list[dict[str, Any]]) -> "EmbeddingDepartmentModel | None":
        try:
            return cls(model_name, dept_cfgs)
        except Exception as exc:  # model not cached, library missing, ...
            log.warning("Embedding model %s unavailable (%s); keyword model only.", model_name, exc)
            return None

    def classify_many(self, texts: list[str], min_similarity: float) -> list[DepartmentResult | None]:
        if not texts:
            return []
        vectors = self.model.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False)
        sims = vectors @ self.dept_vectors.T
        results: list[DepartmentResult | None] = []
        for row in sims:
            idx = int(row.argmax())
            sim = float(row[idx])
            results.append(
                DepartmentResult(self.names[idx], METHOD_EMBEDDING, round(sim, 3), []) if sim >= min_similarity else None
            )
        return results


# ----------------------------------------------------------------------------- complaints
@dataclass
class ComplaintResult:
    is_complaint: bool
    score: float
    cues: list[str]
    method: str = METHOD_RULES


class ComplaintDetector:
    """Scored lexicon: grievance cues + problem cues - negative (announcement / crime) cues."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.threshold = float(cfg["threshold"])
        self.grievance = WeightedLexicon.build(_weighted(
            cfg, ("grievance_en", "grievance_ta"), ("grievance_weak_en", "grievance_weak_ta"),
            float(cfg["grievance_weight"]), float(cfg["grievance_weak_weight"]),
        ))
        self.problem = WeightedLexicon.build(_weighted(cfg, ("problem_en", "problem_ta"), (), float(cfg["problem_weight"]), 0))
        self.negative = WeightedLexicon.build(
            _weighted(cfg, ("negative_en", "negative_ta"), (), float(cfg["negative_weight"]), 0)
        )

    def detect(self, text: str) -> ComplaintResult:
        grievance = self.grievance.matches(text)
        problem = self.problem.matches(text)
        negative = self.negative.matches(text)
        score = sum(w for _, w in grievance) + sum(w for _, w in problem) - sum(w for _, w in negative)
        cues = [t for t, _ in grievance + problem] + [f"-{t}" for t, _ in negative]
        # A complaint needs at least one grievance or problem cue, not only a high score.
        is_complaint = score >= self.threshold and bool(grievance or problem)
        return ComplaintResult(is_complaint, round(score, 2), cues)


# ----------------------------------------------------------------------------- LLM
class LLMClassifier:
    """OpenAI chat-completions classifier (strict JSON, validated, cached)."""

    PROMPT_VERSION = "v1"

    def __init__(self, llm_cfg: dict[str, Any], api_key: str, dept_cfgs: list[dict[str, Any]], cache_path: Path) -> None:
        self.cfg = llm_cfg
        self.api_key = api_key
        self.names = [d["name"] for d in dept_cfgs]
        self.cache_path = cache_path
        self.cache: dict[str, dict[str, Any]] = {}
        if cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except ValueError:
                log.warning("Classification cache unreadable; starting empty.")
        dept_lines = "\n".join(f"- {d['name']}: {d['description']}" for d in dept_cfgs)
        self.system_prompt = (
            "You classify Chennai (Tamil Nadu) news articles for the Greater Chennai Corporation / "
            "District Collectorate. Articles may be in English or Tamil.\n\n"
            "For each article choose EXACTLY ONE department from this list, copying the name verbatim:\n"
            f"{dept_lines}\n\n"
            f"If no department fits, use \"{llm_cfg['fallback_department']}\".\n\n"
            "Also decide is_complaint: true when the article reports a civic problem or grievance "
            "affecting residents or the public (e.g. uncleared garbage, waterlogging, damaged roads, "
            "power cuts, poor service, residents complaining/protesting about a civic issue). "
            "false for announcements, inaugurations, policy, budgets, events, politics and crime reports "
            "that do not describe a civic grievance.\n\n"
            'Reply with ONLY a JSON object: {"results": [{"id": "<id>", "department": "<name>", '
            '"is_complaint": true|false}, ...]} with one entry per input article.'
        )

    def _key(self, text: str) -> str:
        return hashlib.sha1(f"{self.cfg['model']}|{self.PROMPT_VERSION}|{text}".encode("utf-8")).hexdigest()

    def save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")

    def _request(self, batch: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
        payload = [{"id": item_id, "text": text} for item_id, text in batch]
        resp = requests.post(
            self.cfg["endpoint"],
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.cfg["model"],
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            },
            timeout=float(self.cfg["timeout_seconds"]),
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        out: dict[str, dict[str, Any]] = {}
        for entry in json.loads(content).get("results", []):
            dept = entry.get("department")
            if entry.get("id") is not None and dept in self.names and isinstance(entry.get("is_complaint"), bool):
                out[str(entry["id"])] = {"department": dept, "is_complaint": entry["is_complaint"]}
        return out

    def classify(self, items: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
        """Classify (id, text) pairs. Returns only valid answers; failures are left out."""
        results: dict[str, dict[str, Any]] = {}
        todo: list[tuple[str, str]] = []
        for item_id, text in items:
            cached = self.cache.get(self._key(text))
            if cached:
                results[item_id] = cached
            else:
                todo.append((item_id, text))
        todo = todo[: int(self.cfg["max_items_per_run"])]
        size = int(self.cfg["batch_size"])
        log.info("LLM: %d cached, %d to classify with %s", len(results), len(todo), self.cfg["model"])
        texts = dict(todo)
        for start in range(0, len(todo), size):
            batch = todo[start : start + size]
            try:
                answers = self._request(batch)
            except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
                log.warning("LLM batch %d failed: %s", start // size, str(exc).replace(self.api_key, "***"))
                continue
            for item_id, answer in answers.items():
                if item_id in texts:
                    results[item_id] = answer
                    self.cache[self._key(texts[item_id])] = answer
            if (start // size) % 20 == 0:
                self.save_cache()
        self.save_cache()
        return results


# ----------------------------------------------------------------------------- orchestration
class NewsClassifier:
    """Adds department and complaint columns to processed rows."""

    def __init__(self, cls_cfg: dict[str, Any], env: dict[str, str], cache_path: Path) -> None:
        self.cfg = cls_cfg
        self.dept_cfgs: list[dict[str, Any]] = cls_cfg["departments"]
        self.department_names = [d["name"] for d in self.dept_cfgs]
        self.fallback = cls_cfg["fallback_department"]
        if self.fallback not in self.department_names:
            raise ValueError(f"fallback_department {self.fallback!r} is not in the department list")
        kw = cls_cfg["keyword_model"]
        self.keyword_model = KeywordDepartmentModel(
            self.dept_cfgs, float(kw["title_weight"]), float(kw["strong_weight"]), float(kw["weak_weight"]),
            int(kw["min_weak_terms"]),
        )
        self.min_weak_terms_for_complaints = int(kw.get("min_weak_terms_for_complaints", kw["min_weak_terms"]))
        # Phrases blanked out before department matching (place / company names that
        # contain department words, e.g. பள்ளிக்கரணை starts with பள்ளி "school").
        self.ignore_patterns = [term_pattern(p) for p in cls_cfg.get("ignore_phrases") or []]
        self.complaints = ComplaintDetector(cls_cfg["complaint"])
        self.text_max_chars = int(cls_cfg["text_max_chars"])

        emb = cls_cfg.get("embedding_model") or {}
        self.embedding_cfg = emb
        self.embedding: EmbeddingDepartmentModel | None = None
        if emb.get("enabled"):
            self.embedding = EmbeddingDepartmentModel.try_load(emb["model"], self.dept_cfgs)

        llm_cfg = dict(cls_cfg.get("llm") or {})
        api_key = (env.get(llm_cfg.get("env_key", "")) or "").strip()
        self.llm: LLMClassifier | None = None
        if llm_cfg.get("enabled", True) and api_key:
            llm_cfg["fallback_department"] = self.fallback
            self.llm = LLMClassifier(llm_cfg, api_key, self.dept_cfgs, cache_path)
            log.info("LLM classification enabled (%s)", llm_cfg["model"])
        else:
            log.info("LLM classification disabled (no %s in .env); using keyword/embedding models", llm_cfg.get("env_key"))

    def _text(self, row: dict[str, Any]) -> str:
        parts = [row.get("title_clean", ""), row.get("summary_clean", ""), row.get("body_clean", "")]
        text = "\n".join(dict.fromkeys(p for p in parts if p))
        return text[: self.text_max_chars]

    def _mask(self, text: str) -> str:
        for pattern in self.ignore_patterns:
            text = pattern.sub(lambda m: " " * len(m.group(0)), text)
        return text

    def annotate(self, rows: list[dict[str, Any]]) -> None:
        """Add CLASSIFICATION_COLUMNS to every row in place."""
        texts = [self._text(r) for r in rows]
        llm_answers = self.llm.classify([(r["article_id"], t) for r, t in zip(rows, texts)]) if self.llm else {}

        complaints: list[ComplaintResult] = []
        for row, text in zip(rows, texts):
            answer = llm_answers.get(row["article_id"])
            if answer:
                complaints.append(ComplaintResult(answer["is_complaint"], 0.0, [], METHOD_LLM))
            else:
                complaints.append(self.complaints.detect(text))

        dept_results: list[DepartmentResult | None] = []
        need_embedding: list[int] = []
        for i, (row, text, complaint) in enumerate(zip(rows, texts, complaints)):
            answer = llm_answers.get(row["article_id"])
            if answer:
                dept_results.append(DepartmentResult(answer["department"], METHOD_LLM, 1.0, []))
                continue
            min_weak = self.min_weak_terms_for_complaints if complaint.is_complaint else None
            result = self.keyword_model.classify(self._mask(row.get("title_clean", "")), self._mask(text), min_weak)
            dept_results.append(result)
            if result is None and row.get("language") in (self.embedding_cfg.get("languages") or []):
                need_embedding.append(i)

        if self.embedding is not None and need_embedding:
            log.info("Embedding model classifying %d rows without keyword hits", len(need_embedding))
            emb_results = self.embedding.classify_many(
                [texts[i] for i in need_embedding], float(self.embedding_cfg["min_similarity"])
            )
            for i, result in zip(need_embedding, emb_results):
                dept_results[i] = result

        for row, dept, complaint in zip(rows, dept_results, complaints):
            dept = dept or DepartmentResult(self.fallback, METHOD_FALLBACK, 0.0, [])
            row.update(
                {
                    "department": dept.department,
                    "department_method": dept.method,
                    "department_confidence": dept.confidence,
                    "department_evidence": json.dumps(dept.evidence, ensure_ascii=False),
                    "is_complaint": complaint.is_complaint,
                    "complaint_score": complaint.score,
                    "complaint_cues": json.dumps(complaint.cues, ensure_ascii=False),
                    "complaint_method": complaint.method,
                }
            )
