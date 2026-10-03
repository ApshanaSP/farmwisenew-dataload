"""Action Planner, Gap Finder, Linker and Watchdog agents."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..refdata import Reference
from ..util import IST, jdump, stable_id
from . import AgentRun


# ------------------------------------------------------------ Action Planner --
def action_planner(inc: pd.DataFrame, actions: pd.DataFrame, events: pd.DataFrame, offices: pd.DataFrame,
                   ref: Reference, as_of: pd.Timestamp) -> tuple[pd.DataFrame, AgentRun]:
    """Draft a department-wise playbook for open Severe/High or attention incidents that have no actions yet."""
    run = AgentRun("action_planner", "new_or_open_high_incidents")
    run.tool("list_incidents")
    target = inc[(inc["is_open"] == 1) & (inc["action_total"] == 0) &
                 (inc["severity_level"].isin(["Severe", "High"]) | (inc["attention_flag"] == 1))]
    heads = ref.departments.set_index("code")["head"].to_dict()
    run.tool("read_playbooks")
    rows = []
    for r in target.itertuples():
        steps = ref.cat.get(r.category_code, ref.cat["OTHER"]).get("playbook") or ["Assess", "Act", "Close"]
        due_total = r.sla_hours
        owner = _owner(r, offices, heads)
        for k, step in enumerate(steps):
            due = r.first_reported_at + pd.Timedelta(hours=due_total * (k + 1) / len(steps))
            rows.append({"action_id": stable_id("ACT", r.incident_id, k), "event_id": None, "incident_id": r.incident_id,
                         "dept_code": r.lead_dept, "office_id": None, "owner": owner if k < len(steps) - 1 else heads.get(r.lead_dept, owner),
                         "text": step, "sop_step": k + 1, "assigned_at": None, "due_at": due, "status": "Draft",
                         "created_by": "agent:action_planner", "origin": "playbook", "completed_at": None, "verified_at": None, "evidence": None})
        for d in str(r.support_depts).split("|"):
            if d and d != r.lead_dept:
                rows.append({"action_id": stable_id("ACT", r.incident_id, d), "event_id": None, "incident_id": r.incident_id,
                             "dept_code": d, "owner": heads.get(d, d), "text": f"Support {ref.cat.get(r.category_code, ref.cat['OTHER'])['label'].lower()} response",
                             "sop_step": None, "due_at": r.first_reported_at + pd.Timedelta(hours=due_total / 2), "status": "Draft",
                             "created_by": "agent:action_planner", "origin": "coordination"})
    run.outputs = {"incidents": len(target), "draft_actions": len(rows)}
    return pd.DataFrame(rows), run


def _owner(r, offices: pd.DataFrame, heads: dict) -> str:
    if r.lead_dept in ("PWD-WRD", "PWD-BLD") and isinstance(r.taluk_code, str):
        wing = "Buildings" if r.lead_dept == "PWD-BLD" else "Water Resources"
        o = offices[(offices["wing"] == wing) & offices["taluks"].str.contains(r.taluk_code, regex=False)]
        o = o.sort_values("designation", key=lambda s: s.map({"Assistant Executive Engineer": 0, "Executive Engineer": 1}).fillna(2))
        if len(o):
            x = o.iloc[0]
            return f"{x['designation']}, {x['office_name']} ({x['officer_name']})"
    if r.lead_dept == "POL-GCP" and isinstance(r.officer, str):
        return r.officer
    if str(r.lead_dept).startswith("GCC") and pd.notna(r.ward_no):
        return f"Assistant Engineer, Ward {int(r.ward_no)}" + (f" ({r.zone_name} zone)" if isinstance(r.zone_name, str) else "")
    return heads.get(r.lead_dept, str(r.lead_dept))


# --------------------------------------------------------------- Gap Finder --
def gap_finder(inc: pd.DataFrame, as_of: pd.Timestamp, days: int = 14) -> tuple[pd.DataFrame, AgentRun]:
    """Incidents in the news with no departmental record: the brief's 'missing from the app' query."""
    run = AgentRun("gap_finder", "daily_after_briefing")
    run.tool("list_incidents")
    # police-led stories (crime, accidents, protests) are police's own work; the gap is civic incidents no department logged
    g = inc[(inc["media_only"] == 1) & (inc["first_reported_at"] >= as_of - pd.Timedelta(days=days)) & (inc["lead_dept"] != "POL-GCP")].copy()
    g = g.sort_values(["outlet_count", "priority_score"], ascending=False)
    g["gap_strength"] = np.where(g["outlet_count"] >= 2, "reported by 2+ outlets", "single outlet")
    g["suggested_action"] = "Ask " + g["lead_dept"] + " to verify on the ground and log it in their system"
    out = g[["incident_id", "title", "category_code", "lead_dept", "zone_name", "ward_no", "outlet_count", "first_reported_at",
             "severity_level", "priority_score", "gap_strength", "suggested_action"]]
    run.outputs = {"media_only_incidents": len(out), "two_plus_outlets": int((out["outlet_count"] >= 2).sum())}
    return out, run


# ------------------------------------------------------------------- Linker --
def linker(pairs: pd.DataFrame, events: pd.DataFrame, llm) -> tuple[pd.DataFrame, AgentRun]:
    """Gray-band pairs become review items. With an LLM, it adds a recommendation; a person decides."""
    run = AgentRun("linker", "gray_band_pairs")
    run.tool("get_pair_evidence")
    rq = pairs[pairs["decision"] == "review"].copy()
    ev = events.set_index("event_id")
    items = []
    for r in rq.itertuples():
        a, b = ev.loc[r.event_a], ev.loc[r.event_b]
        rec = "person to decide"
        if llm is not None and llm.available():
            run.llm_calls += 1
            j = llm.complete_json("You decide if two reports describe the same real-world incident. Reply JSON {\"same\": true|false, \"reason\": \"...\"}.",
                                  jdump({"a": {"source": a["source"], "time": str(a["reported_at"]), "place": a["place_text"], "text": str(a["text"])[:400]},
                                         "b": {"source": b["source"], "time": str(b["reported_at"]), "place": b["place_text"], "text": str(b["text"])[:400]},
                                         "features": r.features}))
            if j and isinstance(j.get("same"), bool):
                rec = f"LLM suggests {'same' if j['same'] else 'different'}: {j.get('reason', '')[:200]}"
        items.append({"item_type": "link", "item_id": f"{r.event_a}~{r.event_b}", "suggestion": rec,
                      "reason": f"Link probability {r.prob:.2f} is in the review band",
                      "evidence": jdump({"a": f"{a['source']}: {a['title']}", "b": f"{b['source']}: {b['title']}", "features": r.features})})
    run.outputs = {"review_items": len(items)}
    return pd.DataFrame(items), run


# ----------------------------------------------------------------- Watchdog --
def watchdog(inc: pd.DataFrame, anomalies: pd.DataFrame, signals: pd.DataFrame, forecasts_warn: pd.DataFrame,
             ref: Reference, as_of: pd.Timestamp, zone_names: dict) -> tuple[list[dict], AgentRun]:
    """Turns statistics into explained alerts. The numbers come from the statistics; the agent only words them."""
    run = AgentRun("watchdog", "hourly")
    alerts = []
    run.tool("anomalies")
    for r in anomalies.itertuples() if len(anomalies) else []:
        lbl = ref.cat.get(r.category_code, ref.cat["OTHER"])["label"]
        alerts.append({"type": "anomaly", "severity": "High" if r.ratio >= 4 else "Medium",
                       "title": f"Spike in {lbl.lower()} in {zone_names.get(r.zone_no, f'zone {r.zone_no}')}",
                       "message": f"{r.observed} incidents on {r.date} against {r.expected} expected",
                       "explanation": f"Poisson probability {r.p_value} against the 28-day baseline with weekday adjustment",
                       "place": zone_names.get(r.zone_no), "zone_no": r.zone_no, "date": r.date, "category_code": r.category_code})
    run.tool("warnings")
    for r in forecasts_warn.itertuples() if len(forecasts_warn) else []:
        alerts.append({"type": "weather_warning", "severity": {1: "Medium", 2: "High", 3: "Severe"}.get(int(r.value), "Low"),
                       "title": f"IMD warning level {int(r.value)} for {r.observed_at:%d %b}", "message": str(r.detail),
                       "explanation": "IMD district-wise warning (GIS service)", "place": "Chennai district", "date": str(r.observed_at.date())})
    run.tool("metric_signals")
    if len(signals):
        rising = signals["slope_per_day"].fillna(0) > 0
        lakes = signals[(signals["metric"] == "lake_pct_full") &
                        ((signals["value"] >= 95) | ((signals["value"] >= 90) & rising) | (signals["days_to_full"] <= 14))]
        for r in lakes.itertuples():
            trend = (f"rising {r.slope_per_day:.2f} points a day" if r.slope_per_day > 0 else
                     f"falling {abs(r.slope_per_day):.2f} points a day" if r.slope_per_day < 0 else "steady")
            alerts.append({"type": "lake_level", "severity": "High" if r.value >= 95 and r.slope_per_day > 0 else "Medium",
                           "title": f"{r.place_name} at {r.value:.0f}% of capacity",
                           "message": trend.capitalize() + (f"; full in about {r.days_to_full:.0f} days" if pd.notna(r.days_to_full) else ""),
                           "explanation": "PWD daily storage against asset capacity", "place": r.place_name})
        hosp = signals[(signals["metric"] == "health_alert_level") & (signals["value"] >= 2)]
        for r in hosp.itertuples():
            alerts.append({"type": "hospital", "severity": "High" if r.value >= 3 else "Medium",
                           "title": f"{r.place_name}: {r.detail}", "message": f"Alert level {int(r.value)} on {r.observed_at:%d %b}",
                           "explanation": "Hospital MIS daily alert", "place": r.place_name})
        z = signals[(signals["anomaly"] == 1) & signals["metric"].isin(["bed_occupancy_pct", "emergency_cases", "aqi", "reservoir_inflow_cusec"])]
        for r in z.itertuples():
            alerts.append({"type": "metric_anomaly", "severity": "Medium", "title": f"Unusual {r.metric.replace('_', ' ')} at {r.place_name}",
                           "message": f"{r.value:g} {r.unit} (28-day mean {r.mean28}, z = {r.zscore})",
                           "explanation": "Latest reading more than 3 standard deviations from its 28-day mean", "place": r.place_name})
    run.tool("list_incidents")
    att = inc[(inc["attention_flag"] == 1)].sort_values("priority_score", ascending=False).head(25)
    for r in att.itertuples():
        alerts.append({"type": "incident_attention", "severity": r.severity_level, "title": r.title, "message": r.attention_reason,
                       "explanation": r.priority_reasons, "place": r.zone_name, "zone_no": r.zone_no, "incident_ids": r.incident_id})
    breached = inc[(inc["is_open"] == 1) & (inc["sla_breached"] == 1)].groupby("lead_dept").size()
    for d, n in breached[breached >= 10].items():
        alerts.append({"type": "sla", "severity": "Medium", "title": f"{n} open incidents past their deadline ({d})",
                       "message": f"{d} has {n} open incidents past target", "explanation": "Deadlines from the category SLA table",
                       "place": "District"})
    run.outputs = {"alerts": len(alerts)}
    return alerts, run
