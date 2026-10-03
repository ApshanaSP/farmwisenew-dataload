"""
Fills the run's temporary MySQL (a GitHub Actions service container) with what the grievance generator reads:
the reference tables (zones, wards, streets, complaint types ...) and the website's complaints with their status
history, all from AWS through the team API (POST /store/load). The generator then runs unchanged.

    python ci/seed_mysql.py            needs REFRESH_API_KEY and DB_HOST / DB_PORT / DB_USER / DB_PASSWORD

Users, passwords and the Collector's own work are never loaded: the generator does not read them.
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import sys
import urllib.request
from pathlib import Path

import pymysql

API_URL = os.environ.get("AWS_API_URL", "https://i6q6oi20lb.execute-api.ap-south-1.amazonaws.com").rstrip("/")
DATABASE = os.environ.get("DB_NAME", "district_collector_dashboard")
SCHEMA = Path(__file__).with_name("portal_schema.sql")
FROM_DURABLE = ("complaints", "complaint_status_history")  # the website's own rows; everything else is reference data


def api(route: str) -> dict:
    req = urllib.request.Request(f"{API_URL}{route}", data=b"{}", method="POST",
                                 headers={"x-refresh-key": os.environ["REFRESH_API_KEY"].strip(), "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def download(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=300) as r:
        return json.loads(gzip.decompress(r.read()))


def value(v):
    if isinstance(v, dict) and "$b64" in v:
        return base64.b64decode(v["$b64"])
    return v


def main() -> int:
    m = api("/store/load")
    durable, reference = download(m["rows"]), download(m["reference"])
    con = pymysql.connect(host=os.environ.get("DB_HOST", "127.0.0.1"), port=int(os.environ.get("DB_PORT") or 3306),
                          user=os.environ.get("DB_USER", "root"), password=os.environ.get("DB_PASSWORD", ""),
                          charset="utf8mb4", autocommit=False, client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS)
    cur = con.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DATABASE}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
    cur.execute(f"USE `{DATABASE}`")
    cur.execute(SCHEMA.read_text(encoding="utf-8"))
    while cur.nextset():
        pass
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    cur.execute("SHOW TABLES")
    tables = {r[0] for r in cur.fetchall()}
    rows = {t: r for t, r in reference["rows"].items() if t in tables}
    rows.update({t: durable["rows"].get(t, []) for t in FROM_DURABLE})
    for t, data in sorted(rows.items()):
        if not data:
            continue
        cols = list(data[0])
        sql = f"INSERT INTO `{t}` ({', '.join(f'`{c}`' for c in cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
        for i in range(0, len(data), 2000):
            cur.executemany(sql, [tuple(value(r.get(c)) for c in cols) for r in data[i:i + 2000]])
        print(f"  {t}: {len(data)} rows")
    con.commit()
    con.close()
    print(f"MySQL {DATABASE}: {len(rows)} tables loaded from AWS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
