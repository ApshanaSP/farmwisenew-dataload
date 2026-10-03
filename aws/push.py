"""
Sends the CSVs a collector wrote on this PC to AWS (POST /ingest/<source>), for sources that cannot run inside
Lambda: CPCB reads its readings through a browser (Playwright), which Lambda cannot run.

Uses only the API's REFRESH_API_KEY from chennai-grievance-portal-main/.env, which does not expire; no AWS keys.

    python aws/push.py cpcb              after the PC's own CPCB run
    python aws/push.py cpcb police       several sources
    python aws/push.py police --full     write every row, not only changes (first load)
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import sys
import urllib.error
import urllib.request

from common import API_URL, COLLECTORS, ROOT
from dotenv import load_dotenv

sys.path.insert(0, str(ROOT / "aws" / "lambdas" / "collect"))
from handler import FILES  # noqa: E402  the file names each source's Lambda accepts


def push(source: str, key: str, full: bool = False) -> None:
    folder = COLLECTORS[source][1]
    allowed = set(FILES[source]) | {n.replace(".csv", "_archive.csv") for n in FILES[source]}
    files = {f.name: f.read_text(encoding="utf-8-sig") for f in folder.glob("*.csv") if f.name in allowed}
    if not files:
        print(f"{source}: no CSVs in {folder}")
        return
    body = json.dumps({"gz": base64.b64encode(gzip.compress(json.dumps(files).encode())).decode()}).encode()
    req = urllib.request.Request(f"{API_URL}/ingest/{source}" + ("?full=1" if full else ""), data=body, method="POST",
                                 headers={"x-refresh-key": key, "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            print(f"{source}: {r.status} {r.read().decode()}")
    except urllib.error.HTTPError as e:
        print(f"{source}: HTTP {e.code} {e.read().decode()[:300]}")


if __name__ == "__main__":
    load_dotenv(ROOT / "chennai-grievance-portal-main" / ".env", override=True)
    key = os.environ.get("REFRESH_API_KEY", "").strip()
    if not key:
        sys.exit("REFRESH_API_KEY is not in chennai-grievance-portal-main/.env (python aws/setup.py makes it).")
    args = [a for a in sys.argv[1:] if a != "--full"]
    for s in args or ["cpcb"]:
        if s not in COLLECTORS:
            sys.exit(f"unknown source {s!r}; one of {', '.join(COLLECTORS)}")
        push(s, key, full="--full" in sys.argv)
