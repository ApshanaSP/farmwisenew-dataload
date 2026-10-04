"""Data Steward agent: source health, the data contract, drift, and crosswalk proposals.

Tools: read run logs, read file stamps, run contract checks, compare category mixes.
Writes: source_health, data_quality, quarantine, review items, data-quality alerts.
Never edits a source file or a curated record.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..refdata import Reference
from ..util import IST, Settings, mixed_to_ist
from . import AgentRun


def _mtime(p: Path) -> pd.Timestamp | None:
    return pd.Timestamp(p.stat().st_mtime, unit="s", tz="UTC").tz_convert(IST) if p.exists() else None


def _later(*ts: pd.Timestamp | None) -> pd.Timestamp | None:
    ts = [t for t in ts if t is not None and pd.notna(t)]
    return max(ts) if ts else None


def _refresh_runs(settings: Settings) -> dict[str, tuple[pd.Timestamp | None, pd.Timestamp | None]]:
    """run_pipeline.py's record of each feed's last try and last success. A generator that already has today's
    rows exits without rewriting its file (hospital: once a day), so the file time alone would read as stale."""
    try:
        st = json.loads((settings.out_dir / "state" / "refresh_state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    at = lambda s: pd.Timestamp(float(s), unit="s", tz="UTC").tz_convert(IST) if s else None  # noqa: E731
    return {k: (at(v.get("last_run")), at(v.get("last_ok"))) for k, v in st.items()
            if not k.startswith("_") and isinstance(v, dict)}


def source_health(settings: Settings, events: pd.DataFrame, obs: pd.DataFrame, docs_raw_rows: int, now: pd.Timestamp) -> pd.DataFrame:
    every = {r["name"]: r["every_minutes"] for r in settings.raw.get("refresh", [])}
    rows = []

    def add(name, kind, last_run, last_ok, newest, rows_n, detail, ok_parts=None):
        target = every.get(name, 1440)
        age_min = (now - last_ok).total_seconds() / 60 if last_ok is not None and pd.notna(last_ok) else None
        if last_ok is None or pd.isna(last_ok):
            status = "failed"
        # daily feeds are stale after a day and a half, so one missed 6:00 AM run shows; faster feeds after 3 intervals
        elif age_min > target * (1.5 if target >= 1440 else 3):
            status = "stale"
        elif ok_parts is not None and ok_parts[0] < ok_parts[1]:
            status = "degraded"
        else:
            status = "ok"
        rows.append({"source": name, "kind": kind, "last_run_at": last_run, "last_success_at": last_ok, "newest_record_at": newest,
                     "freshness_target_min": target, "minutes_since_success": round(age_min) if age_min is not None else None,
                     "status": status, "rows": rows_n, "endpoints_ok": ok_parts[0] if ok_parts else None,
                     "endpoints_total": ok_parts[1] if ok_parts else None, "detail": detail})

    # IMD
    p = settings.src("imd", "dir") / "imd_weather_run_log.csv"
    if p.exists():
        lg = pd.read_csv(p, encoding="utf-8-sig")
        lg["run_at"] = mixed_to_ist(lg["run_at"])
        last = lg[lg["run_at"] == lg["run_at"].max()]
        ok = last["fetch_status"].eq("ok")
        bad = "; ".join(f"{s}: {f}" for s, f in zip(last.loc[~ok, "source"], last.loc[~ok, "fetch_status"]))
        add("imd", "live collector", lg["run_at"].max(), lg.loc[lg["fetch_status"].eq("ok"), "run_at"].max(),
            obs.loc[(obs["source"] == "imd") & (obs["metric"] != "imd_warning_level"), "observed_at"].max(), int((obs["source"] == "imd").sum()),
            f"Unavailable endpoints (not bypassed): {bad}" if bad else "All endpoints ok", (int(ok.sum()), len(last)))
    # CPCB
    p = settings.src("cpcb", "dir") / "cpcb_air_quality_run_log.csv"
    if p.exists():
        lg = pd.read_csv(p, encoding="utf-8-sig")
        lg["run_started_at"] = mixed_to_ist(lg["run_started_at"])
        last = lg[lg["run_started_at"] == lg["run_started_at"].max()]
        ok = pd.to_numeric(last["rows_fetched"], errors="coerce").fillna(0) > 0
        add("cpcb", "live collector", lg["run_started_at"].max(), lg.loc[pd.to_numeric(lg["rows_fetched"], errors="coerce").fillna(0) > 0, "run_started_at"].max(),
            obs.loc[obs["source"] == "cpcb", "observed_at"].max(), int((obs["source"] == "cpcb").sum()),
            "; ".join(f"{s}: {h}" for s, h in zip(last["source"], last["http_status"])), (int(ok.sum()), len(last)))
    # CFM
    p = settings.src("cfm", "dir") / "cfm_dss_run_log.csv"
    if p.exists():
        lg = pd.read_csv(p, encoding="utf-8-sig")
        lg["run_started_at"] = mixed_to_ist(lg["run_started_at"])
        last = lg[lg["run_started_at"] == lg["run_started_at"].max()]
        ok = last["http_status"].astype(str).eq("200")
        suspect = int(((obs["source"] == "cfm") & (obs["quality"] == "suspect")).sum())
        add("cfm", "live collector", lg["run_started_at"].max(), lg.loc[lg["http_status"].astype(str).eq("200"), "run_started_at"].max(),
            obs.loc[obs["source"] == "cfm", "observed_at"].max(), int((obs["source"] == "cfm").sum()),
            f"{suspect} gauge readings marked suspect (stage inversion or datum); current gauge feed returned "
            f"{int(pd.to_numeric(last.loc[last['endpoint'].str.contains('Csection', na=False), 'rows_fetched'], errors='coerce').fillna(0).sum())} rows",
            (int(ok.sum()), len(last)))
    # news
    st = settings.path("chennai_news_pipeline/data/state/pipeline_state.json")
    state = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
    master = settings.src("news", "master")
    newest = events.loc[events["source"] == "news", "reported_at"].max()
    runs = _refresh_runs(settings)

    def ran(name, f):  # (last run, last success): the file time, or the refresh record when that is later
        last_try, last_ok = runs.get(name, (None, None))
        return _later(_mtime(f), last_try), _later(_mtime(f), last_ok)

    add("news", "live collector (RSS, sitemaps, Google News)", *ran("news", master), newest, docs_raw_rows,
        f"State: {', '.join(f'{k}={v}' for k, v in state.items() if not isinstance(v, (dict, list)))[:200]}")
    # generators and the portal export
    for name, path, kind in (("grievance", settings.src("grievances", "complaints"), "departmental app export (synthetic history + live filings)"),
                             ("police", settings.src("police", "reports"), "departmental dataset (synthetic generator)"),
                             ("pwd", settings.src("pwd", "dir") / "pwd_incidents.csv", "departmental dataset (synthetic generator)"),
                             ("hospital", settings.src("hospital", "csv"), "departmental dataset (synthetic generator)")):
        src_ev = events[(events["source"] == name) & (events["is_overlay"] == 0)]
        add(name, kind, *ran(name, path), src_ev["reported_at"].max(), int(len(src_ev)), "")
    return pd.DataFrame(rows)


def contract(events: pd.DataFrame, obs: pd.DataFrame, actions: pd.DataFrame, works: pd.DataFrame, ref: Reference,
             as_of: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (issues summary, quarantine rows). Hard failures are quarantined; soft ones are flagged."""
    issues, quarantine = [], []

    def check(table, name, mask, severity, action, df, id_col="event_id"):
        n = int(mask.sum())
        ex = "|".join(map(str, df.loc[mask, id_col].head(5))) if n else ""
        issues.append({"table": table, "check": name, "severity": severity, "failing": n, "total": int(len(df)), "examples": ex, "action": action})
        if n and severity == "hard":
            q = df.loc[mask, [id_col]].copy()
            q["table"], q["check"] = table, name
            quarantine.append(q.rename(columns={id_col: "record_id"}))

    e = events
    check("events", "event_id unique", e["event_id"].duplicated(keep=False), "hard", "keep first; fix loader", e)
    check("events", "reported_at present", e["reported_at"].isna(), "hard", "quarantine", e)
    check("events", "category in master", ~e["category_code"].isin(ref.cat.keys()), "hard", "propose crosswalk entry", e)
    check("events", "lead department in master", ~e["lead_dept"].isin(ref.departments["code"]), "soft", "propose department mapping", e)
    check("events", "reported not before occurred (15 min)", e["reported_at"] < e["occurred_at"] - pd.Timedelta(minutes=15), "soft", "flag", e)
    check("events", "closed not before reported", e["closed_at"] < e["reported_at"], "hard", "quarantine", e)
    check("events", "not in the future", e["reported_at"] > as_of + pd.Timedelta(minutes=5), "hard", "quarantine", e)
    check("events", "has a location", e["lat"].isna() & (e["geo_level"] != "district"), "soft", "geo review queue", e)
    check("events", "inside the district", e["in_district"] == 0, "soft", "flag out of district", e)
    check("events", "taluk resolved", e["taluk_code"].isna() & (e["geo_level"] != "district"), "soft", "flag", e)
    check("events", "geo confidence >= 0.7", e["geo_conf"].fillna(1) < 0.7, "soft", "geo review queue", e)
    check("events", "text usable", e["junk_flag"] == 1, "soft", "hide from briefings", e)
    check("events", "provenance snapshot present", e["snapshot_sha256"].isna() & (e["is_overlay"] == 0) & e["source"].isin(["grievance", "police", "pwd", "hospital", "news"]), "soft", "flag", e)
    check("events", "cross-reference resolves", e["ext_ref_status"].eq("external_system_unresolved"), "soft",
          "kept as external reference; links come from matching", e)
    check("events", "Closed status has a close time", e["status_std"].eq("Resolved") & e["closed_at"].isna() & e["source"].isin(["police", "pwd"]), "soft", "flag", e)
    o = obs.assign(event_id=obs["metric"] + ":" + obs["place_id"].astype(str) + ":" + obs["observed_at"].astype(str))
    check("observations", "plausible reading", o["quality"] == "suspect", "soft", "keep value, never alert on it", o)
    check("observations", "value present", o["value"].isna(), "hard", "quarantine", o)
    if len(actions):
        a = actions
        check("actions", "lifecycle in order", (a["completed_at"] < a["assigned_at"]) | (a["verified_at"] < a["completed_at"]), "hard", "quarantine", a, "action_id")
    if len(works):
        check("pwd_works", "spend within 1.25 x sanction", works["overrun_pct"] > 25, "soft", "flag cost overrun", works, "work_id")
        check("pwd_works", "completed at 100%", (works["status"] == "completed") & (works["physical_progress_pct"] < 100), "soft", "flag", works, "work_id")
    q = pd.concat(quarantine, ignore_index=True) if quarantine else pd.DataFrame(columns=["record_id", "table", "check"])
    return pd.DataFrame(issues), q


def drift(events: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Population stability index of the category mix, last 7 days vs the 28 days before."""
    rows = []
    for s, e in events[events["is_overlay"] == 0].groupby("source"):
        cur = e[e["reported_at"] > as_of - pd.Timedelta(days=7)]["category_code"].value_counts(normalize=True)
        base = e[(e["reported_at"] <= as_of - pd.Timedelta(days=7)) & (e["reported_at"] > as_of - pd.Timedelta(days=35))]["category_code"].value_counts(normalize=True)
        if len(cur) < 2 or len(base) < 2:
            continue
        cats = cur.index.union(base.index)
        c = cur.reindex(cats).fillna(0) + 1e-4
        b = base.reindex(cats).fillna(0) + 1e-4
        psi = float(((c - b) * np.log(c / b)).sum())
        top = (c - b).abs().sort_values(ascending=False).head(3)
        rows.append({"source": s, "psi_7d_vs_28d": round(psi, 3), "drift": "high" if psi > 0.25 else ("moderate" if psi > 0.1 else "low"),
                     "biggest_shifts": "; ".join(f"{k} {b.get(k, 0):.1%}→{c.get(k, 0):.1%}" for k in top.index)})
    return pd.DataFrame(rows)


def crosswalk_proposals(events: pd.DataFrame, docs: pd.DataFrame, ref: Reference) -> pd.DataFrame:
    rows = []
    # only types whose explicit sub-types map to several categories are ambiguous at type level
    sub_map = ref.cmap["grievance"]["subtype"]
    by_type: dict[str, set] = {}
    ev_g = events[events["source"] == "grievance"]
    for t, s_ in zip(ev_g["category_src"], ev_g["subcategory_src"]):
        if s_ in sub_map:
            by_type.setdefault(t, set()).add(sub_map[s_])
    ambiguous = {t for t, codes in by_type.items() if len(codes) > 1}
    g = ev_g[ev_g["category_method"].eq("crosswalk:type") & ev_g["category_src"].isin(ambiguous)]
    for (t, s), n in g.groupby(["category_src", "subcategory_src"]).size().items():
        rows.append({"item_type": "crosswalk", "item_id": f"grievance:{s}", "suggestion": ref.cmap["grievance"]["type"].get(t),
                     "reason": f"Sub-type '{s}' ({n} complaints) mapped only by its type '{t}'", "evidence": ""})
    unseen = set(docs["department"].dropna()) - set(ref.cmap["news_department"])
    for d in sorted(unseen):
        rows.append({"item_type": "crosswalk", "item_id": f"news_department:{d}", "suggestion": None,
                     "reason": "News department name has no department code", "evidence": ""})
    return pd.DataFrame(rows)


def run(settings, ref, events, obs, actions, works, docs, raw_rows, as_of, llm) -> dict:
    run = AgentRun("data_steward", "after_load")
    now = pd.Timestamp.now(tz=IST)
    run.tool("read_run_logs")
    health = source_health(settings, events, obs, raw_rows, now)
    run.tool("run_contract")
    issues, quarantine = contract(events, obs, actions, works, ref, as_of)
    run.tool("category_drift")
    dr = drift(events, as_of)
    run.tool("crosswalk_proposals")
    props = crosswalk_proposals(events, docs, ref)
    alerts = []
    for r in health.itertuples():
        if r.status in ("stale", "failed", "degraded"):
            alerts.append({"type": "data_quality", "severity": "Medium" if r.status == "degraded" else "High",
                           "title": f"{r.source.upper()} feed {r.status}",
                           "message": f"Last success {r.last_success_at:%d %b %H:%M}" if pd.notna(r.last_success_at) else "No successful run found",
                           "explanation": r.detail or "", "place": "District"})
    for r in dr[dr["drift"] == "high"].itertuples() if len(dr) else []:
        alerts.append({"type": "data_quality", "severity": "Medium", "title": f"Category mix shifted in {r.source}",
                       "message": f"PSI {r.psi_7d_vs_28d}", "explanation": r.biggest_shifts, "place": "District"})
    run.outputs = {"sources": len(health), "not_ok": int((health["status"] != "ok").sum()), "checks": len(issues),
                   "failing_checks": int((issues["failing"] > 0).sum()), "quarantined": len(quarantine), "proposals": len(props)}
    return {"health": health, "issues": issues, "quarantine": quarantine, "drift": dr, "proposals": props,
            "alerts": alerts, "run": run}
