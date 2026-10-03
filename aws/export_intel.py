"""
Copies the last district_intel build to S3 (the AWS replacement for `run_pipeline.py mysql`).

Reads district_intel/output/district_intel.db with the MySQL export's own functions, so every table holds exactly the
rows, columns and types the dashboard reads from MySQL today (datetimes as naive IST "YYYY-MM-DD HH:MM:SS").
Each table becomes one gzipped JSON object, named by its sha256; only tables that changed since the last export are
uploaded, then the new build is published in one step (POST /intel/publish).

Needs no AWS keys: it uses the API's REFRESH_API_KEY from chennai-grievance-portal-main/.env and presigned URLs.

    python aws/export_intel.py          after `python run_pipeline.py build` (or refresh)

Snapshot format: {"table", "columns": [[name, kind]], "rows": [[...], ...]}; kind is one of
text, int, bool, float, date, datetime (datetime and date values are strings).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTEL = ROOT / "district_intel"
sys.path.insert(0, str(INTEL))

try:
    import truststore

    truststore.inject_into_ssl()  # this PC's antivirus / proxy re-signs HTTPS
except ImportError:  # pragma: no cover
    pass

from dotenv import load_dotenv  # noqa: E402

from dintel.mysql_export import (  # noqa: E402
    EXTRA_INDEXES, PRIMARY_KEYS, SKIP_TABLES, VIEWS, convert, plan_table, read_store)
from dintel.store import INDEXES  # noqa: E402

API_URL = "https://i6q6oi20lb.execute-api.ap-south-1.amazonaws.com"


def api(path: str, body: dict, key: str) -> dict:
    req = urllib.request.Request(f"{API_URL}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"x-refresh-key": key, "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"{path}: HTTP {e.code} {e.read().decode()[:300]}")


def snapshot(name: str, df, declared: dict) -> bytes:
    plan = plan_table(df, name, declared)
    rows = [[v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else v for v in r] for r in convert(df, plan)]
    doc = {"table": name, "columns": [[c, kind] for c, kind, _ in plan], "rows": rows}
    # mtime=0 and fixed separators: the same table always gives the same bytes, so an unchanged table keeps its sha
    return gzip.compress(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0)


def main() -> int:
    load_dotenv(ROOT / "chennai-grievance-portal-main" / ".env", override=True)
    key = os.environ.get("REFRESH_API_KEY", "").strip()
    if not key:
        sys.exit("REFRESH_API_KEY is not in chennai-grievance-portal-main/.env (python aws/setup.py makes it).")
    db = INTEL / "output" / "district_intel.db"
    if not db.exists():
        sys.exit(f"{db} not found: run `python run_pipeline.py build` in district_intel first")
    t0 = time.time()
    tables, declared = read_store(db, INTEL / "output" / "dashboard")
    blobs = {n: snapshot(n, df, declared.get(n, {})) for n, df in tables.items() if n not in SKIP_TABLES}
    shas = {n: hashlib.sha256(b).hexdigest() for n, b in blobs.items()}

    urls = api("/intel/upload-urls", {"tables": shas}, key)
    sent = 0
    for n, url in urls["upload"].items():
        req = urllib.request.Request(url, data=blobs[n], method="PUT", headers={"content-type": "application/gzip"})
        with urllib.request.urlopen(req, timeout=300) as r:
            if r.status != 200:
                sys.exit(f"upload of {n} failed: HTTP {r.status}")
        sent += len(blobs[n])

    built = datetime.fromtimestamp(db.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    manifest = {"build_id": f"{built}|{hashlib.sha256(''.join(sorted(shas.values())).encode()).hexdigest()[:12]}",
                "built_at": built, "exported_at": now,
                "tables": {n: {"sha": shas[n], "rows": int(len(tables[n])), "bytes": len(blobs[n])} for n in blobs},
                # what the MySQL export adds around the tables, so the website can rebuild the same schema:
                # the _export_meta rows, the dashboard views (MySQL dialect) and the indexed columns
                "meta": {"exported_at": now, "sqlite": str(db), "sqlite_mtime": built,
                         "tables": str(len(blobs)), "rows": str(sum(len(tables[n]) for n in blobs))},
                "views": VIEWS,
                "keys": {n: PRIMARY_KEYS[n] for n in blobs if n in PRIMARY_KEYS},
                "indexes": {n: sorted(set(INDEXES.get(n, [])) | set(EXTRA_INDEXES.get(n, []))) for n in blobs
                            if INDEXES.get(n) or EXTRA_INDEXES.get(n)}}
    res = api("/intel/publish", manifest, key)
    print(f"published {res['published']}: {len(blobs)} tables, {len(urls['upload'])} uploaded "
          f"({sent / 1e6:.1f} MB), {len(urls['already_there'])} unchanged, {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
