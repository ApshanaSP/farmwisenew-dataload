"""Write the curated store: SQLite (one file), CSV copies, and JSON feeds for the dashboard."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from .util import IST, epoch_ms, iso, log

INDEXES = {
    "events": ["source", "incident_id", "category_code", "ward_no", "zone_no", "taluk_code", "reported_at"],
    "incidents": ["category_code", "zone_no", "ward_no", "lead_dept", "is_open", "first_reported_at", "priority_score"],
    "incident_members": ["incident_id", "event_id"], "incident_timeline": ["incident_id"], "actions": ["incident_id", "dept_code"],
    "observations": ["metric", "place_id", "observed_at"], "documents": ["story_id", "published_at", "is_incident"],
    "daily_counts": ["date", "category_code", "zone_no"], "alerts": ["type", "severity"],
}
VIEWS = {
    # open incidents re-evaluated against the wall clock (the stored priority is as of the build)
    "v_open_incidents_live": """
        SELECT i.*, ROUND((julianday('now') - julianday(first_reported_at)) * 24, 1) AS hours_open_now,
               CASE WHEN julianday('now') > julianday(sla_due_at) THEN 1 ELSE 0 END AS past_deadline_now
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


def _sqlable(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        s = out[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            out[c] = s.map(iso)
        elif s.dtype == object:
            out[c] = s.map(lambda v: json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (list, dict, set, tuple))
                           else iso(v) if isinstance(v, pd.Timestamp)
                           # numpy scalars in object columns: sqlite3 would store np.int64 as an 8-byte BLOB
                           else v.item() if isinstance(v, np.generic) else v)
        elif str(s.dtype) in ("Int64", "Float64", "boolean"):
            out[c] = s.astype(object).where(s.notna(), None)
    return out


def write_sqlite(path: Path, tables: dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.db")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    try:
        for name, df in tables.items():
            if df is None:
                continue
            _sqlable(df).to_sql(name, con, index=False, if_exists="replace", chunksize=2000)
            for col in INDEXES.get(name, []):
                if col in df.columns:
                    con.execute(f'CREATE INDEX IF NOT EXISTS ix_{name}_{col} ON "{name}"("{col}")')
        for v, sql in VIEWS.items():
            con.execute(f"DROP VIEW IF EXISTS {v}")
            con.execute(f"CREATE VIEW {v} AS {sql}")
        con.commit()
    finally:
        con.close()
    tmp.replace(path)
    log.info("sqlite: %s (%.1f MB, %d tables, %d views)", path, path.stat().st_size / 1e6, len(tables), len(VIEWS))


def write_csv(dir_: Path, tables: dict[str, pd.DataFrame]) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        if df is not None:
            _sqlable(df).to_csv(dir_ / f"{name}.csv", index=False, encoding="utf-8-sig")


def _j(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return epoch_ms(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else round(float(v), 6)
    if v is pd.NaT or (hasattr(pd, "isna") and not isinstance(v, (list, dict, str)) and pd.isna(v)):
        return None
    return v


def _records(df: pd.DataFrame, cols: dict[str, str]) -> list[dict]:
    sub = df[list(cols)].rename(columns=cols)
    return [{k: _j(v) for k, v in r.items()} for r in sub.to_dict("records")]


def write_dashboard(dir_: Path, t: dict[str, pd.DataFrame], extra: dict) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    inc = t["incidents"]

    def dump(name, obj):
        (dir_ / name).write_text(json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":")), encoding="utf-8")

    cols = {"incident_id": "id", "first_reported_at": "t", "category_code": "cat", "category_label": "type", "family": "family",
            "lead_dept": "dept", "depts_involved": "depts", "zone_no": "zone", "ward_no": "ward", "taluk_code": "taluk",
            "lat": "lat", "lon": "lon", "place_text": "loc", "title": "title", "severity_level": "severity", "severity_score": "sevScore",
            "priority_score": "priority", "status_std": "status", "is_open": "open", "verified": "verified",
            "awaiting_collector": "awaitingCollector", "attention_flag": "attention", "attention_reason": "attentionReason",
            "sources": "sources", "source_count": "sourceCount", "citizen_complaints": "complaints", "outlet_count": "outlets",
            "media_only": "mediaOnly", "sla_due_at": "slaDue", "sla_breached": "breached", "hours_open": "hoursOpen",
            "closed_at": "resolvedAt", "hotspot_id": "hotspot", "is_overlay_any": "overlay", "is_synthetic_any": "synthetic",
            "needs_coordination": "coordination", "weather_related": "weather"}
    idx = _records(inc, cols)
    for r in idx:
        r["sources"] = (r["sources"] or "").split("|")
        r["depts"] = (r["depts"] or "").split("|")
    dump("incidents.json", idx)

    top = inc[(inc["is_open"] == 1)].sort_values(["attention_flag", "priority_score"], ascending=False).head(400)["incident_id"]
    mem = t["incident_members"][t["incident_members"]["incident_id"].isin(top)]
    tl = t["incident_timeline"][t["incident_timeline"]["incident_id"].isin(top)]
    ac = t["actions"][t["actions"]["incident_id"].isin(top)] if len(t["actions"]) else t["actions"]
    det = {}
    for r in inc[inc["incident_id"].isin(top)].itertuples():
        det[r.incident_id] = {"summary": r.summary, "priorityReasons": r.priority_reasons, "severityReasons": r.severity_reasons,
                              "officer": r.officer, "confidence": _j(r.confidence), "sources": [], "timeline": [], "actions": []}
    for r in mem.itertuples():
        det[r.incident_id]["sources"].append({"source": r.source, "id": r.source_record_id, "channel": r.channel, "t": epoch_ms(r.reported_at),
                                              "title": r.title, "link": r.deep_link if isinstance(r.deep_link, str) else None,
                                              "role": r.role, "linkProb": _j(r.link_prob), "overlay": int(r.is_overlay)})
    for r in tl.itertuples():
        det[r.incident_id]["timeline"].append({"t": epoch_ms(r.at), "label": r.step, "note": r.note, "actor": r.actor, "source": r.source})
    for r in ac.itertuples() if len(ac) else []:
        det[r.incident_id]["actions"].append({"a": r.text, "owner": r.owner, "due": epoch_ms(r.due_at), "st": r.status, "by": r.created_by})
    dump("incident_details.json", det)

    for name in ("kpis", "alerts", "hotspots", "source_health", "gaps", "briefings", "anomalies", "ward_stats"):
        df = t.get(name)
        if df is not None and len(df):
            dump(f"{name}.json", [{k: _j(v) for k, v in r.items()} for r in df.to_dict("records")])
    dump("meta.json", extra["meta"])
    dump("environment.json", extra["environment"])
    dump("trends.json", extra["trends"])
    dump("wards.geojson", extra["wards_geojson"])
    dump("zone_outlines.geojson", extra["zone_outlines"])
    log.info("dashboard feeds: %s", ", ".join(sorted(p.name for p in dir_.iterdir())))
