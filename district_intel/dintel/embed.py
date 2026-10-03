"""Multilingual sentence embeddings (intfloat/multilingual-e5-small), cached on disk.

The model reads Tamil and English into one space, so a Tamil headline and an English
headline about the same event land close together. Vectors are cached by text hash,
so only new articles are encoded on each build. If the model is not available the
pipeline carries on without it (character n-grams only).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np

from .util import INTEL_DIR, log

MODEL = "intfloat/multilingual-e5-small"
CACHE = INTEL_DIR / "output" / "cache" / "e5_small.npz"
_model = None


def _load():
    global _model
    if _model is not None:
        return _model
    try:
        try:  # antivirus / proxy TLS inspection: verify against the OS certificate store
            import truststore
            truststore.inject_into_ssl()
        except ImportError:
            pass
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        from sentence_transformers import SentenceTransformer
        try:
            _model = SentenceTransformer(MODEL, local_files_only=True)
        except Exception:
            _model = SentenceTransformer(MODEL)
    except Exception as exc:
        log.warning("embeddings unavailable (%s); continuing without them", exc)
        _model = False
    return _model


def encode(texts: list[str], prefix: str = "query: ") -> np.ndarray | None:
    """L2-normalised vectors, one row per text; None when the model is unavailable."""
    keys = [hashlib.sha1((prefix + t).encode("utf-8")).hexdigest() for t in texts]
    cache: dict[str, np.ndarray] = {}
    if CACHE.exists():
        z = np.load(CACHE, allow_pickle=False)
        cache = dict(zip(z["keys"].tolist(), z["vecs"]))
    todo = [i for i, k in enumerate(keys) if k not in cache]
    if todo:
        m = _load()
        if not m:
            return None
        vecs = m.encode([prefix + texts[i] for i in todo], batch_size=64, normalize_embeddings=True, show_progress_bar=False)
        for i, v in zip(todo, vecs):
            cache[keys[i]] = v.astype(np.float32)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        np.savez(CACHE, keys=np.array(list(cache.keys())), vecs=np.stack(list(cache.values())))
        log.info("embeddings: encoded %d new texts (%d cached)", len(todo), len(keys) - len(todo))
    return np.stack([cache[k] for k in keys])
