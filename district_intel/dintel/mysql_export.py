"""Copy the curated SQLite store into MySQL for the dashboard.

Two databases on one server:
  district_intel      owned by the pipeline, fully replaced on every export
  district_intel_ops  owned by the dashboard (Collector decisions, workspaces, audit), never overwritten

The export reads output/district_intel.db, so it always matches the last build.
Column types are inferred from the data: ISO timestamps become DATETIME in IST
(naive wall-clock, like the grievance portal), date-only text becomes DATE.
Tables load into `<name>__new` and are swapped in with one atomic RENAME TABLE,
so the dashboard never reads a half-loaded store.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .store import INDEXES
from .util import IST, REPO_DIR, log

# Tables left out of MySQL: tiny debugging tables (see the storage plan).
SKIP_TABLES = {"quarantine", "category_drift"}

PRIMARY_KEYS = {
    "incidents": ["incident_id"], "events": ["event_id"], "incident_members": ["incident_id", "event_id"],
    "actions": ["action_id"], "documents": ["doc_id"], "alerts": ["alert_id"], "briefings": ["briefing_id"],
    "hotspots": ["hotspot_id"], "pwd_works": ["work_id"], "world_calendar": ["date"],
    "observation_signals": ["metric", "place_id"], "review_queue": ["review_id"], "source_health": ["source"],
    "metrics": ["metric"], "ref_wards": ["ward_no"], "ref_zones": ["zone_no"], "ref_taluks": ["taluk_code"],
    "ref_departments": ["code"], "ref_categories": ["category_code"], "ref_facilities": ["facility_id"],
    "ref_offices": ["office_id"],
}
EXTRA_INDEXES = {
    "incident_timeline": ["event_id"], "gaps": ["incident_id"], "kpis": ["period"], "anomalies": ["date"],
    "forecasts": ["location_id"], "link_pairs": ["event_a", "event_b"], "review_queue": ["item_id"],
}

# `documents` keeps what the dashboard shows; the raw news-pipeline columns stay in the news master file.
# Output name -> SQLite source column(s); the first non-null source wins.
DOCUMENT_COLUMNS = {
    "doc_id": ["doc_id"], "article_id": ["article_id"], "story_id": ["story_id"], "story_role": ["story_role"],
    "outlet_count": ["outlet_count"], "title": ["title"], "summary": ["summary"], "body": ["body"],
    "url": ["canonical_url", "url"], "publisher": ["publisher"], "publisher_domain": ["source_domain", "publisher_domain"],
    "publisher_tier": ["publisher_tier"], "reliability": ["reliability"], "lang": ["lang"], "source_kind": ["source_kind"],
    "report_type": ["report_type"], "published_at": ["published_at"], "fetched_at": ["fetched_at"],
    "is_district": ["is_district"], "is_incident": ["is_incident"], "incident_conf": ["incident_conf"],
    "category_code": ["category_code"], "category_conf": ["category_conf"], "department": ["dept_src"],
    "place_text": ["place_text"], "lat": ["lat"], "lon": ["lon"], "geo_level": ["geo_level"],
    "dead": ["dead"], "injured": ["injured"], "event_id": ["event_id"], "linked_incident_id": ["linked_incident_id"],
}

DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OFFSET_RE = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")
VARCHAR_BUCKETS = (16, 32, 64, 128, 255)
ROW_BYTES_LIMIT = 60000            # InnoDB allows 65,535 bytes of VARCHAR per row; utf8mb4 = 4 bytes a character

VIEWS = {
    # MySQL versions of the SQLite views in store.py (the server runs on IST, like the stored times)
    "v_open_incidents_live": """
        SELECT i.*, ROUND(TIMESTAMPDIFF(SECOND, first_reported_at, NOW()) / 3600, 1) AS hours_open_now,
               CASE WHEN NOW() > sla_due_at THEN 1 ELSE 0 END AS past_deadline_now
        FROM incidents i WHERE is_open = 1""",
    "v_collector_queue": """
        SELECT incident_id, title, severity_level, zone_name, ward_no, status_std, priority_score, priority_reasons, attention_reason
        FROM incidents WHERE is_open = 1 AND (awaiting_collector = 1 OR attention_flag = 1) ORDER BY priority_score DESC""",
    "v_zone_summary": """
        SELECT zone_no, zone_name, COUNT(*) AS incidents, SUM(is_open) AS open_incidents,
               SUM(CASE WHEN severity_level='Severe' THEN 1 ELSE 0 END) AS severe,
               SUM(CASE WHEN is_open=1 AND sla_breached=1 THEN 1 ELSE 0 END) AS open_past_deadline
        FROM incidents WHERE zone_no IS NOT NULL GROUP BY zone_no, zone_name""",
    "v_taluk_unresolved": """
        SELECT i.taluk_code, t.name AS taluk, COUNT(*) AS unresolved
        FROM incidents i LEFT JOIN ref_taluks t ON t.taluk_code = i.taluk_code
        WHERE i.is_open = 1 GROUP BY i.taluk_code, t.name ORDER BY unresolved DESC""",
    "v_media_gaps": """
        SELECT incident_id, title, category_label, zone_name, outlet_count, first_reported_at, priority_score
        FROM incidents WHERE media_only = 1 ORDER BY outlet_count DESC, priority_score DESC""",
    "v_department_performance": """
        SELECT lead_dept, COUNT(*) AS incidents, SUM(is_open) AS open_incidents,
               SUM(CASE WHEN is_open=1 AND sla_breached=1 THEN 1 ELSE 0 END) AS open_past_deadline,
               ROUND(AVG(hours_to_first_action), 1) AS avg_hours_to_first_action
        FROM incidents GROUP BY lead_dept ORDER BY open_past_deadline DESC""",
}

# Written by the dashboard; the pipeline only creates the tables if missing and never drops or truncates them.
OPS_TABLES = {
    "collector_decisions": """
        decision_id   BIGINT AUTO_INCREMENT PRIMARY KEY,
        incident_id   VARCHAR(64) NOT NULL,
        decision      ENUM('verify','escalate','reject','resolve','reopen','note') NOT NULL,
        escalate_to   VARCHAR(64) NULL COMMENT 'department or office code when escalated',
        note          TEXT NULL,
        decided_by    VARCHAR(128) NOT NULL,
        decided_role  VARCHAR(64) NULL,
        decided_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        applied_at    DATETIME NULL COMMENT 'set by the pipeline when a build applied it',
        superseded_by BIGINT NULL,
        KEY ix_incident (incident_id, decided_at)""",
    "action_updates": """
        update_id    BIGINT AUTO_INCREMENT PRIMARY KEY,
        action_id    VARCHAR(64) NULL COMMENT 'NULL = a new action added by hand',
        incident_id  VARCHAR(64) NOT NULL,
        dept_code    VARCHAR(64) NULL,
        office_id    VARCHAR(64) NULL,
        owner        VARCHAR(128) NULL,
        text         TEXT NULL,
        status       VARCHAR(32) NULL,
        due_at       DATETIME NULL,
        note         TEXT NULL,
        updated_by   VARCHAR(128) NOT NULL,
        updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        applied_at   DATETIME NULL,
        KEY ix_action (action_id), KEY ix_incident (incident_id)""",
    "review_decisions": """
        decision_id  BIGINT AUTO_INCREMENT PRIMARY KEY,
        review_id    VARCHAR(64) NULL,
        item_type    VARCHAR(64) NOT NULL COMMENT 'link, location, mapping, category, ...',
        item_id      VARCHAR(128) NOT NULL,
        decision     ENUM('accept','reject','merge','split','relocate','approve_mapping','other') NOT NULL,
        payload      JSON NULL COMMENT 'e.g. corrected ward_no/lat/lon, the event ids to split off',
        note         TEXT NULL,
        decided_by   VARCHAR(128) NOT NULL,
        decided_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        applied_at   DATETIME NULL,
        KEY ix_item (item_type, item_id), KEY ix_review (review_id)""",
    "workspaces": """
        workspace_id BIGINT AUTO_INCREMENT PRIMARY KEY,
        owner        VARCHAR(128) NOT NULL,
        name         VARCHAR(128) NOT NULL,
        version      INT NOT NULL DEFAULT 1,
        is_current   TINYINT NOT NULL DEFAULT 1,
        layout       JSON NULL,
        filters      JSON NULL,
        created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY ux_version (owner, name, version)""",
    "briefing_archive": """
        archive_id    BIGINT AUTO_INCREMENT PRIMARY KEY,
        briefing_id   VARCHAR(64) NOT NULL,
        period        VARCHAR(16) NOT NULL,
        as_of         DATETIME NOT NULL,
        window_start  DATETIME NULL,
        window_end    DATETIME NULL,
        markdown      MEDIUMTEXT NULL,
        fact_pack     MEDIUMTEXT NULL,
        method        VARCHAR(64) NULL,
        issued_by     VARCHAR(128) NOT NULL DEFAULT 'pipeline',
        issued_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY ux_briefing (briefing_id, as_of, issued_by)""",
    "audit_log": """
        log_id       BIGINT AUTO_INCREMENT PRIMARY KEY,
        at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        actor        VARCHAR(128) NOT NULL,
        action       VARCHAR(64) NOT NULL,
        table_name   VARCHAR(64) NOT NULL,
        record_id    VARCHAR(128) NULL,
        before_value JSON NULL,
        after_value  JSON NULL,
        KEY ix_record (table_name, record_id), KEY ix_at (at)""",
}


# ------------------------------------------------------------------ connection --

def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def connection_params(cfg: dict[str, Any]) -> dict[str, Any]:
    """DI_MYSQL_* environment variables win; otherwise the portal's .env (same local server)."""
    env_file = _read_env_file((REPO_DIR / cfg["env_file"]).resolve()) if cfg.get("env_file") else {}

    def pick(di: str, portal: str, default: Any) -> Any:
        return os.environ.get(di) or env_file.get(portal) or default

    return {
        "host": pick("DI_MYSQL_HOST", "DB_HOST", cfg.get("host", "localhost")),
        "port": int(pick("DI_MYSQL_PORT", "DB_PORT", cfg.get("port", 3306))),
        "user": pick("DI_MYSQL_USER", "DB_USER", cfg.get("user", "root")),
        "password": pick("DI_MYSQL_PASSWORD", "DB_PASSWORD", ""),
    }


def _connect(params: dict[str, Any], database: str | None = None):
    import pymysql

    return pymysql.connect(**params, database=database, charset="utf8mb4", autocommit=True,
                           local_infile=False, connect_timeout=10, read_timeout=600, write_timeout=600)


def q(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


# ------------------------------------------------------------------- typing --

def _is_bool(s: pd.Series) -> bool:
    v = s.dropna()
    return len(v) > 0 and v.isin([0, 1]).all()


def infer_column(s: pd.Series, declared: str = "") -> tuple[str, str]:
    """(kind, MySQL type). kind is one of int, bool, float, datetime, date, text.
    `declared` is the SQLite column type: INTEGER columns with NULLs arrive as floats."""
    v = s.dropna()
    if pd.api.types.is_bool_dtype(s):
        return "bool", "TINYINT"
    whole = pd.api.types.is_float_dtype(s) and declared.upper() == "INTEGER" and bool((v == v.round()).all())
    if pd.api.types.is_integer_dtype(s) or whole:
        if _is_bool(s):
            return "bool", "TINYINT"
        big = len(v) and (v.abs().max() > 2_000_000_000)
        return "int", "BIGINT" if big else "INT"
    if pd.api.types.is_float_dtype(s):
        return "float", "DOUBLE"
    v = v.astype(str)
    v = v[v != ""]
    if len(v) == 0:
        return "text", "VARCHAR(64)"
    if v.str.match(DATE_RE).all():
        return "date", "DATE"
    if v.str.match(DATETIME_RE).all():
        return "datetime", "DATETIME"
    chars = int(v.str.len().max())
    if chars <= 255:
        size = next(b for b in VARCHAR_BUCKETS if b >= min(255, chars * 2))
        return "text", f"VARCHAR({size})"
    nbytes = int(v.map(lambda x: len(x.encode("utf-8"))).max())
    return "text", "TEXT" if nbytes * 2 < 65535 else "MEDIUMTEXT"


def to_ist_naive(s: pd.Series) -> pd.Series:
    """ISO strings (with an offset, or naive IST wall-clock) -> naive IST datetimes, whole seconds."""
    s = s.where(s.notna() & (s.astype(str) != ""), None)
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    has = s.notna()
    aware = has & s.astype(str).str.contains(OFFSET_RE)
    if aware.any():
        t = pd.to_datetime(s[aware], utc=True, format="ISO8601")
        out[aware] = t.dt.tz_convert(IST).dt.tz_localize(None)
    naive = has & ~aware
    if naive.any():
        out[naive] = pd.to_datetime(s[naive], format="mixed", errors="coerce")
    return out.dt.floor("s")


def plan_table(df: pd.DataFrame, name: str, declared: dict[str, str] | None = None) -> list[tuple[str, str, str]]:
    """[(column, kind, mysql type)]; key columns get NOT NULL, wide rows fall back to TEXT."""
    pk = set(PRIMARY_KEYS.get(name, []))
    idx = set(INDEXES.get(name, [])) | set(EXTRA_INDEXES.get(name, []))
    cols = []
    for c in df.columns:
        kind, typ = infer_column(df[c], (declared or {}).get(c, ""))
        if kind == "text" and typ.startswith("TEXT") and c in (pk | idx):
            typ = "VARCHAR(255)"
        cols.append([c, kind, typ])

    def row_bytes() -> int:
        return sum(int(t[8:-1]) * 4 for _, _, t in cols if t.startswith("VARCHAR"))

    # keep under the InnoDB row limit: widest non-key VARCHARs become TEXT
    for col in sorted((c for c in cols if c[2].startswith("VARCHAR") and c[0] not in pk | idx),
                      key=lambda c: -int(c[2][8:-1])):
        if row_bytes() <= ROW_BYTES_LIMIT:
            break
        col[2] = "TEXT"
    return [tuple(c) for c in cols]


def convert(df: pd.DataFrame, plan: list[tuple[str, str, str]]) -> list[tuple]:
    out = {}
    for c, kind, _ in plan:
        s = df[c]
        if kind == "datetime":
            t = to_ist_naive(s)
            out[c] = [None if pd.isna(x) else x.to_pydatetime() for x in t]
        elif kind == "date":
            out[c] = [None if (x is None or x != x or x == "") else str(x) for x in s]
        elif kind in ("int", "bool"):
            out[c] = [None if pd.isna(x) else int(x) for x in s]
        elif kind == "float":
            out[c] = [None if (x is None or (isinstance(x, float) and not math.isfinite(x))) else float(x) for x in s]
        else:
            out[c] = [None if (x is None or (isinstance(x, float) and math.isnan(x))) else str(x) for x in s]
    return list(zip(*[out[c] for c, _, _ in plan])) if plan else []


# ------------------------------------------------------------ source tables --

def trim_documents(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for new, srcs in DOCUMENT_COLUMNS.items():
        have = [s for s in srcs if s in df.columns]
        if not have:
            continue
        col = df[have[0]]
        for s in have[1:]:
            col = col.where(col.notna() & (col.astype(str) != ""), df[s])
        out[new] = col
    return out


def add_map_shapes(tables: dict[str, pd.DataFrame], dashboard_dir: Path) -> None:
    """Ward polygons into ref_wards.geometry; zone outlines as a small ref_zones table."""
    wards_path, zones_path = dashboard_dir / "wards.geojson", dashboard_dir / "zone_outlines.geojson"
    if wards_path.exists() and "ref_wards" in tables:
        feats = json.loads(wards_path.read_text(encoding="utf-8"))["features"]
        geom = {int(f["properties"]["ward_no"]): json.dumps(f["geometry"], separators=(",", ":")) for f in feats}
        tables["ref_wards"]["geometry"] = tables["ref_wards"]["ward_no"].map(lambda w: geom.get(int(w)))
    else:
        log.warning("mysql: %s not found; ref_wards has no geometry", wards_path)
    if zones_path.exists():
        feats = json.loads(zones_path.read_text(encoding="utf-8"))["features"]
        tables["ref_zones"] = pd.DataFrame([{"zone_no": int(f["properties"]["zone_no"]), "zone_name": f["properties"]["zone_name"],
                                             "outline": json.dumps(f["geometry"], separators=(",", ":"))} for f in feats])


def decode_int_blobs(s: pd.Series) -> pd.Series:
    """Older builds wrote np.int64 values in object columns as 8-byte little-endian BLOBs."""
    out = s.map(lambda v: int.from_bytes(v, "little", signed=True) if isinstance(v, bytes) and len(v) == 8 else v)
    return pd.to_numeric(out, errors="coerce") if out.map(lambda v: v is None or isinstance(v, (int, float))).all() else out


def read_store(sqlite_path: Path, dashboard_dir: Path) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, str]]]:
    """(tables, declared SQLite column types)."""
    con = sqlite3.connect(sqlite_path)
    try:
        names = [n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                 if n not in SKIP_TABLES]
        tables = {n: pd.read_sql_query(f'SELECT * FROM "{n}"', con) for n in names}
        declared = {n: {r[1]: r[2] for r in con.execute(f'PRAGMA table_info("{n}")')} for n in names}
        for n, df in tables.items():
            for c in df.columns:
                if df[c].dtype == object and df[c].map(lambda v: isinstance(v, bytes)).any():
                    df[c] = decode_int_blobs(df[c])
                    log.warning("mysql: %s.%s had numpy integers stored as BLOBs; decoded", n, c)
    finally:
        con.close()
    if "documents" in tables:
        tables["documents"] = trim_documents(tables["documents"])
    add_map_shapes(tables, dashboard_dir)
    return tables, declared


# --------------------------------------------------------------------- load --

JSON_COLUMNS = {("ref_wards", "geometry"), ("ref_zones", "outline")}


def create_sql(name: str, plan: list[tuple[str, str, str]]) -> str:
    pk = [c for c in PRIMARY_KEYS.get(name, []) if c in {p[0] for p in plan}]
    lines = []
    for c, _, typ in plan:
        if (name, c) in JSON_COLUMNS:
            typ = "JSON"
        lines.append(f"{q(c)} {typ}{' NOT NULL' if c in pk else ''}")
    if pk:
        lines.append(f"PRIMARY KEY ({', '.join(q(c) for c in pk)})")
    cols = {p[0] for p in plan}
    for c in dict.fromkeys(INDEXES.get(name, []) + EXTRA_INDEXES.get(name, [])):
        if c in cols and pk[:1] != [c]:          # the primary key already indexes its first column
            lines.append(f"KEY {q('ix_' + c)} ({q(c)})")
    return (f"CREATE TABLE {q(name + '__new')} (\n  " + ",\n  ".join(lines) +
            "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci")


def _insert(cur, name: str, plan, rows: list[tuple], max_bytes: int = 8_000_000) -> None:
    sql = f"INSERT INTO {q(name + '__new')} ({', '.join(q(c) for c, _, _ in plan)}) VALUES ({', '.join(['%s'] * len(plan))})"
    batch, size = [], 0
    for r in rows:
        batch.append(r)
        size += sum(len(x) for x in r if isinstance(x, str)) * 3 + 16 * len(r)
        if len(batch) >= 5000 or size >= max_bytes:
            cur.executemany(sql, batch)
            batch, size = [], 0
    if batch:
        cur.executemany(sql, batch)


def ensure_ops_schema(params: dict[str, Any], ops_db: str) -> list[str]:
    con = _connect(params)
    created = []
    try:
        cur = con.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {q(ops_db)} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        cur.execute(f"USE {q(ops_db)}")
        cur.execute("SHOW TABLES")
        have = {r[0] for r in cur.fetchall()}
        for name, body in OPS_TABLES.items():
            if name not in have:
                cur.execute(f"CREATE TABLE {q(name)} ({body}\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci")
                created.append(name)
    finally:
        con.close()
    return created


def archive_briefings(params: dict[str, Any], ops_db: str, briefings: pd.DataFrame | None) -> int:
    """Keep every pipeline briefing: the store only holds the latest one per period."""
    if briefings is None or briefings.empty:
        return 0
    b = briefings.copy()
    for c in ("as_of", "window_start", "window_end"):
        b[c] = to_ist_naive(b[c])
    rows = [(r.briefing_id, r.period, r.as_of.to_pydatetime(),
             None if pd.isna(r.window_start) else r.window_start.to_pydatetime(),
             None if pd.isna(r.window_end) else r.window_end.to_pydatetime(),
             r.markdown, r.fact_pack, r.method) for r in b.itertuples() if not pd.isna(r.as_of)]
    con = _connect(params, ops_db)
    try:
        cur = con.cursor()
        cur.executemany("INSERT IGNORE INTO briefing_archive (briefing_id, period, as_of, window_start, window_end, markdown, fact_pack, method) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", rows)
        return cur.rowcount
    finally:
        con.close()


def verify(params: dict[str, Any], db: str, tables: dict[str, pd.DataFrame], plans: dict) -> list[dict]:
    """Row counts and per-column non-null counts must match the source frames."""
    con = _connect(params, db)
    problems = []
    try:
        cur = con.cursor()
        for name, df in tables.items():
            plan = plans[name]
            exprs = ", ".join(f"COUNT({q(c)})" for c, _, _ in plan)
            cur.execute(f"SELECT COUNT(*){', ' + exprs if exprs else ''} FROM {q(name)}")
            got = cur.fetchone()
            if got[0] != len(df):
                problems.append({"table": name, "column": "*", "sqlite": len(df), "mysql": got[0]})
            for (c, kind, _), n in zip(plan, got[1:]):
                s = df[c]
                want = int((s.notna() & (s.astype(str) != "")).sum()) if kind in ("datetime", "date") else int(s.notna().sum())
                if kind == "float":
                    want = int(s.map(lambda x: x is not None and isinstance(x, (int, float)) and math.isfinite(x)).sum())
                if n != want:
                    problems.append({"table": name, "column": c, "sqlite": want, "mysql": n})
    finally:
        con.close()
    return problems


def export(settings, sqlite_path: Path | None = None) -> dict[str, Any]:
    cfg = settings.raw.get("mysql", {})
    db, ops_db = cfg.get("database", "district_intel"), cfg.get("ops_database", "district_intel_ops")
    params = connection_params(cfg)
    sqlite_path = sqlite_path or settings.path(settings.raw["output"]["sqlite"])
    t0 = time.time()

    tables, declared = read_store(sqlite_path, settings.out_dir / "dashboard")
    plans = {n: plan_table(df, n, declared.get(n)) for n, df in tables.items()}

    con = _connect(params)
    try:
        cur = con.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {q(db)} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        cur.execute(f"USE {q(db)}")
        cur.execute("SET SESSION sql_mode = 'STRICT_ALL_TABLES,NO_ENGINE_SUBSTITUTION'")
        cur.execute("SET SESSION unique_checks = 0")
        for name, df in tables.items():
            t = time.time()
            cur.execute(f"DROP TABLE IF EXISTS {q(name + '__new')}")
            cur.execute(create_sql(name, plans[name]))
            _insert(cur, name, plans[name], convert(df, plans[name]))
            log.info("mysql: %-20s %8d rows  %.1fs", name, len(df), time.time() - t)

        # one atomic swap: the dashboard sees the old store or the new one, never a mix
        cur.execute("SHOW TABLES")
        existing = {r[0] for r in cur.fetchall()}
        for name in tables:
            if name + "__old" in existing:
                cur.execute(f"DROP TABLE {q(name + '__old')}")
        renames = []
        for name in tables:
            if name in existing:
                renames.append(f"{q(name)} TO {q(name + '__old')}")
            renames.append(f"{q(name + '__new')} TO {q(name)}")
        cur.execute("RENAME TABLE " + ", ".join(renames))
        for name in tables:
            if name in existing:
                cur.execute(f"DROP TABLE {q(name + '__old')}")
        for v, sql in VIEWS.items():
            cur.execute(f"CREATE OR REPLACE VIEW {q(v)} AS {sql}")
        cur.execute("CREATE TABLE IF NOT EXISTS `_export_meta` (k VARCHAR(64) PRIMARY KEY, v TEXT)")
        meta = {"exported_at": pd.Timestamp.now(tz=IST).strftime("%Y-%m-%d %H:%M:%S"), "sqlite": str(sqlite_path),
                "sqlite_mtime": pd.Timestamp(sqlite_path.stat().st_mtime, unit="s", tz="UTC").tz_convert(IST).strftime("%Y-%m-%d %H:%M:%S"),
                "tables": str(len(tables)), "rows": str(sum(len(d) for d in tables.values()))}
        cur.executemany("REPLACE INTO `_export_meta` (k, v) VALUES (%s, %s)", list(meta.items()))
    finally:
        con.close()

    created = ensure_ops_schema(params, ops_db)
    archived = archive_briefings(params, ops_db, tables.get("briefings"))
    problems = verify(params, db, tables, plans)

    report = {
        "database": db, "ops_database": ops_db, "host": f"{params['host']}:{params['port']}",
        "tables": len(tables), "rows": sum(len(d) for d in tables.values()),
        "row_counts": {n: len(d) for n, d in tables.items()},
        "documents_columns": len(tables.get("documents", pd.DataFrame()).columns),
        "ops_tables_created": created, "briefings_archived": archived,
        "mismatches": problems, "runtime_s": round(time.time() - t0, 1),
        "column_types": {n: {c: t for c, _, t in p} for n, p in plans.items()},
    }
    out = settings.out_dir / "reports" / "mysql_export.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if problems:
        log.warning("mysql: %d mismatches between SQLite and MySQL, see %s", len(problems), out)
    log.info("mysql: %d tables, %d rows into %s in %.1fs (ops tables created: %s, briefings archived: %d)",
             report["tables"], report["rows"], db, report["runtime_s"], ", ".join(created) or "none", archived)
    return report
