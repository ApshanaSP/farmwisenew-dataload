"""
What the collectors and the build keep between runs, packed into one file. GitHub gives every run a fresh machine,
so the workflow restores this at the start and saves it (as the `state` release's asset) at the end.

    python ci/state.py pack   state.tar.gz
    python ci/state.py unpack state.tar.gz

Left out: raw/debug copies (never read back), logs, and the grievance files, which every run rebuilds in full
(they include real citizens' complaints, and the release asset of a public repository is public).
"""
from __future__ import annotations

import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEEP = [
    "chennai_news_pipeline/data/processed/master_news.csv",   # every article so far (the build reads this)
    "chennai_news_pipeline/data/state",                       # backfill done / last run
    "imd_weather_collector/data",
    "cpcb_air_quality_collector/data",                        # CPCB history cannot be re-downloaded
    "cfm_dss_collector/data",
    "chennai_hospital_data/chennai_hospital_health_data.csv",
    "pwd_dataset_generator/data",
    "police_dataset_generator/output",
    "district_intel/output/state",                            # refresh bookkeeping, shared rain calendar
    "district_intel/output/cache",                            # news embeddings already computed
]
SKIP_DIRS = {"raw", "raw_cache", "__pycache__"}


def keep(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = Path(info.name).parts
    if SKIP_DIRS.intersection(parts) or info.name.endswith(".log"):
        return None
    return info


def pack(out: Path) -> None:
    with tarfile.open(out, "w:gz") as tar:
        for rel in KEEP:
            p = ROOT / rel
            if p.exists():
                tar.add(p, arcname=rel, filter=keep)
            else:
                print(f"  (not there yet: {rel})")
    print(f"state packed: {out} ({out.stat().st_size / 1e6:.1f} MB)")


def unpack(src: Path) -> None:
    with tarfile.open(src, "r:gz") as tar:
        tar.extractall(ROOT, filter="data")
        n = len(tar.getmembers())
    print(f"state restored: {n} entries from {src}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("pack", "unpack"):
        sys.exit(__doc__)
    (pack if sys.argv[1] == "pack" else unpack)(Path(sys.argv[2]))
