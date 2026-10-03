"""Shared helpers: settings, time, IDs, geometry maths, union-find."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import yaml

IST = "Asia/Kolkata"
PKG_DIR = Path(__file__).resolve().parent
INTEL_DIR = PKG_DIR.parent
REPO_DIR = INTEL_DIR.parent
REF_DIR = INTEL_DIR / "reference"

log = logging.getLogger("dintel")


# ------------------------------------------------------------------ settings --

@dataclass
class Settings:
    raw: dict[str, Any]

    def path(self, rel: str) -> Path:
        return (REPO_DIR / rel).resolve()

    def src(self, *keys: str) -> Path:
        node: Any = self.raw["sources"]
        for k in keys:
            node = node[k]
        return self.path(node)

    @property
    def out_dir(self) -> Path:
        return self.path(self.raw["output"]["dir"])

    @property
    def pipe(self) -> dict[str, Any]:
        return self.raw["pipeline"]


def load_settings(path: str | Path | None = None) -> Settings:
    p = Path(path) if path else INTEL_DIR / "config.yaml"
    with open(p, encoding="utf-8") as fh:
        return Settings(yaml.safe_load(fh))


def load_ref(name: str) -> Any:
    with open(REF_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------- time --

def to_ist(values: Any, naive_is_ist: bool = True) -> pd.Series:
    """Parse to tz-aware IST. Naive values are taken as IST wall-clock time."""
    s = pd.Series(values) if not isinstance(values, pd.Series) else values
    s = s.replace({"": None})
    out = pd.to_datetime(s, errors="coerce", utc=False, format="mixed")
    if getattr(out.dt, "tz", None) is None:
        if naive_is_ist:
            return out.dt.tz_localize(IST, ambiguous="NaT", nonexistent="NaT")
        return out.dt.tz_localize("UTC").dt.tz_convert(IST)
    return out.dt.tz_convert(IST)


def mixed_to_ist(values: pd.Series) -> pd.Series:
    """Values that mix offset-aware and naive strings."""
    def one(v: Any) -> pd.Timestamp:
        if v is None or (isinstance(v, float) and math.isnan(v)) or v == "":
            return pd.NaT
        t = pd.Timestamp(v)
        if t.tzinfo is None:
            return t.tz_localize(IST)
        return t.tz_convert(IST)
    return pd.Series([one(v) for v in values], index=values.index, dtype=f"datetime64[ns, {IST}]")


def iso(ts: Any) -> str | None:
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).isoformat()


def epoch_ms(ts: Any) -> int | None:
    if ts is None or pd.isna(ts):
        return None
    return int(pd.Timestamp(ts).value // 1_000_000)


# ------------------------------------------------------------------------ ids --

def stable_id(prefix: str, *parts: Any, n: int = 12) -> str:
    h = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:n]
    return f"{prefix}-{h}"


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hmac_hash(value: Any, salt: str) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)) or str(value).strip() == "":
        return None
    digits = re.sub(r"\D", "", str(value).split(".")[0])
    if not digits:
        return None
    return hashlib.sha256((salt + digits).encode()).hexdigest()[:16]


# ------------------------------------------------------------------- geometry --

LAT0 = 13.05
M_PER_DEG_LAT = 110_540.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))


def to_xy(lat: Any, lon: Any) -> np.ndarray:
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    return np.column_stack([(lon - 80.25) * M_PER_DEG_LON, (lat - LAT0) * M_PER_DEG_LAT])


def haversine_m(lat1: Any, lon1: Any, lat2: Any, lon2: Any) -> np.ndarray:
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(v, dtype=float)) for v in (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


# ---------------------------------------------------------------- union-find --

class UnionFind:
    """Disjoint sets with path halving and union by size. Near-constant time per op."""

    def __init__(self, items: Iterable[Any] = ()) -> None:
        self.parent: dict[Any, Any] = {}
        self.size: dict[Any, int] = {}
        for it in items:
            self.add(it)

    def add(self, x: Any) -> None:
        if x not in self.parent:
            self.parent[x] = x
            self.size[x] = 1

    def find(self, x: Any) -> Any:
        self.add(x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: Any, b: Any) -> Any:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]
        return ra

    def groups(self) -> dict[Any, list[Any]]:
        out: dict[Any, list[Any]] = {}
        for x in self.parent:
            out.setdefault(self.find(x), []).append(x)
        return out


# ---------------------------------------------------------------------- misc --

def jdump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def clean_str(v: Any) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip()


def setup_logging(verbose: bool = False) -> None:
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
