"""Briefing agent: fact pack -> briefing text -> numeric verifier -> versioned save.

Every number in the text must appear in the fact pack. The deterministic template
passes by construction; an optional LLM rewrite is accepted only if it also passes.
"""
from __future__ import annotations

import json
import re

import pandas as pd

from ..refdata import Reference
from ..util import IST, jdump
from . import AgentRun

TITLE = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly", "quarterly": "Quarterly"}
NUM = re.compile(r"(?<![\w-])[-+]?\d+(?:\.\d+)?(?![\w])")


def _flat_numbers(obj) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for v in obj.values():
            out |= _flat_numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _flat_numbers(v)
    elif obj is not None:
        s = str(obj)
        for m in re.findall(r"\d+(?:\.\d+)?", s):
            out.add(m)
            try:
                f = float(m)
                out |= {str(int(f)), f"{f:.1f}", f"{f:.0f}"}
            except ValueError:
                pass
    return out


def verify(text: str, facts: dict) -> list[str]:
    allowed = _flat_numbers(facts)
    bad = []
    for m in NUM.findall(text):
        v = m.lstrip("+-")
        if v not in allowed and v not in {"0", "1", "2", "3"}:
            bad.append(m)
    return bad


def fact_pack(period: str, days: int, inc: pd.DataFrame, kpis: pd.DataFrame, alerts: pd.DataFrame, gaps: pd.DataFrame,
              actions: pd.DataFrame, health: pd.DataFrame, ref: Reference, as_of: pd.Timestamp) -> dict:
    start = as_of - pd.Timedelta(days=days)
    k = kpis[(kpis["period"] == period) & kpis["zone_no"].isna()].set_index("offset")
    cur, prev = k.loc[0], k.loc[1]
    win = inc[inc["first_reported_at"] > start]
    top = inc[(inc["is_open"] == 1) & ((inc["first_reported_at"] > start) | (inc["attention_flag"] == 1))]
    top = top.sort_values(["attention_flag", "priority_score"], ascending=False).head(5)
    heads = ref.departments.set_index("code")["name"].to_dict()
    acts = actions.sort_values("due_at") if len(actions) else actions
    items = []
    for r in top.itertuples():
        mine = acts[acts["incident_id"] == r.incident_id] if len(acts) else pd.DataFrame()
        nxt = mine[mine["status"] != "Done"].head(1) if len(mine) else mine
        fallback = "verify the completed work and close" if len(mine) else "assign an owner"
        items.append({"id": r.incident_id, "title": r.title, "severity": r.severity_level, "zone": r.zone_name or "-",
                      "ward": int(r.ward_no) if pd.notna(r.ward_no) else None, "status": r.status_std,
                      "why": r.attention_reason or "High priority", "reasons": r.priority_reasons, "reports": int(r.member_count),
                      "sources": r.sources.replace("|", ", "), "outlets": int(r.outlet_count), "lead": heads.get(r.lead_dept, r.lead_dept),
                      "next": nxt["text"].iloc[0] if len(nxt) else fallback, "owner": nxt["owner"].iloc[0] if len(nxt) else heads.get(r.lead_dept, r.lead_dept)})
    cat_now = win.groupby("category_label").size()
    pw = inc[(inc["first_reported_at"] > start - pd.Timedelta(days=days)) & (inc["first_reported_at"] <= start)]
    cat_prev = pw.groupby("category_label").size()
    trend = (pd.DataFrame({"now": cat_now, "prev": cat_prev}).fillna(0).astype(int)
             .assign(change=lambda d: d["now"] - d["prev"]).sort_values("now", ascending=False).head(8))
    dept = inc[inc["is_open"] == 1].groupby("lead_dept").agg(open=("incident_id", "size"), breached=("sla_breached", "sum"),
                                                             severe=("severity_level", lambda s: int((s == "Severe").sum())))
    dept = dept.sort_values(["breached", "open"], ascending=False).head(8)
    dtop = []
    for d, r in dept.iterrows():
        t = inc[(inc["is_open"] == 1) & (inc["lead_dept"] == d)].sort_values("priority_score", ascending=False).head(1)
        dtop.append({"dept": heads.get(d, d), "open": int(r["open"]), "breached": int(r["breached"]), "severe": int(r["severe"]),
                     "top": t["title"].iloc[0] if len(t) else "-"})
    order = {"weather_warning": 0, "lake_level": 1, "hospital": 2, "metric_anomaly": 3, "anomaly": 4}
    warn = alerts[alerts["type"].isin(order)].assign(_o=lambda d: d["type"].map(order)).sort_values("_o", kind="stable") if len(alerts) else alerts
    warnings = [{"type": r.type, "title": r.title, "message": r.message} for r in warn.head(8).itertuples()] if len(warn) else []
    g = gaps[gaps["first_reported_at"] > start] if len(gaps) else gaps
    gap_items = [{"id": r.incident_id, "title": r.title, "outlets": int(r.outlet_count), "dept": heads.get(r.lead_dept, r.lead_dept)}
                 for r in g.head(5).itertuples()] if len(g) else []
    ok = int((health["status"] == "ok").sum())
    return {"period": period, "window_start": f"{start.tz_convert(IST):%d %b %Y %H:%M}", "window_end": f"{as_of.tz_convert(IST):%d %b %Y %H:%M}",
            "kpi": {c: (None if pd.isna(cur[c]) else cur[c]) for c in ["incidents", "severe_incidents", "open_incidents", "resolved", "complaints_filed",
                                                                      "complaints_open", "sla_breached_open", "multi_source_incidents", "media_only",
                                                                      "median_hours_to_first_action"]},
            "kpi_prev": {c: (None if pd.isna(prev[c]) else prev[c]) for c in ["incidents", "severe_incidents", "resolved"]},
            "attention": items, "warnings": warnings, "gaps": gap_items,
            "trend": [{"category": i, "now": int(r["now"]), "prev": int(r["prev"]), "change": int(r["change"])} for i, r in trend.iterrows()],
            "departments": dtop, "feeds_ok": ok, "feeds_total": int(len(health)),
            "feeds_not_ok": [f"{r.source} ({r.status})" for r in health[health["status"] != "ok"].itertuples()]}


def render(f: dict) -> str:
    k, p = f["kpi"], f["kpi_prev"]
    L = [f"# Collector's {TITLE[f['period']]} Briefing, Chennai District",
         f"_{f['window_start']} to {f['window_end']} · {f['feeds_ok']} of {f['feeds_total']} data feeds healthy_", "",
         "## At a glance",
         f"- {k['incidents']} incidents this period ({p['incidents']} in the previous one); {k['severe_incidents']} severe; {k['open_incidents']} still open; {k['resolved']} resolved.",
         f"- {k['complaints_filed']} citizen complaints filed; {k['complaints_open']} of them still open.",
         f"- {k['sla_breached_open']} open incidents from this period are past their deadline."
         + (f" Median time to first action on civic complaints: {k['median_hours_to_first_action']} hours." if k.get("median_hours_to_first_action") is not None else ""),
         f"- {k['multi_source_incidents']} incidents were reported by more than one source; {k['media_only']} appear only in the news.", "",
         "## Needs your attention"]
    if not f["attention"]:
        L.append("Nothing open needs your personal attention.")
    for i, a in enumerate(f["attention"], 1):
        L.append(f"{i}. **{a['title']}** ({a['severity']}, {a['zone']}{f', ward ' + str(a['ward']) if a['ward'] else ''}; {a['status'].lower()}). "
                 f"{a['why']}. Why it matters: {a['reasons']}. Evidence: {a['reports']} report{'s' if a['reports'] != 1 else ''} from {a['sources']}"
                 + (f", {a['outlets']} news outlets" if a["outlets"] else "") + f". Next: {a['lead']}, {a['next'].lower()}" + (f" ({a['owner']})" if a["owner"] not in ("-", a["lead"]) else "") + f". `{a['id']}`")
    L += ["", "## Early warnings"]
    L += [f"- {w['title']}: {w['message']}" for w in f["warnings"]] or ["- No active warnings."]
    L += ["", "## In the news, not in any department's records"]
    L += [f"- {g['title']} ({g['outlets']} outlet{'s' if g['outlets'] != 1 else ''}); ask {g['dept']} to verify. `{g['id']}`" for g in f["gaps"]] or ["- None this period."]
    L += ["", "## Department follow-up", "| Department | Open | Past deadline | Severe | Top item |", "|---|---|---|---|---|"]
    L += [f"| {d['dept']} | {d['open']} | {d['breached']} | {d['severe']} | {d['top']} |" for d in f["departments"]]
    L += ["", "## Categories against the previous period", "| Category | This period | Previous | Change |", "|---|---|---|---|"]
    L += [f"| {t['category']} | {t['now']} | {t['prev']} | {'+' if t['change'] > 0 else ''}{t['change']} |" for t in f["trend"]]
    if f["feeds_not_ok"]:
        L += ["", f"_Feeds needing attention: {', '.join(f['feeds_not_ok'])}._"]
    return "\n".join(L) + "\n"


def run(inc, kpis, alerts, gaps, actions, health, ref, as_of, llm, periods=("daily", "weekly", "monthly", "quarterly")) -> tuple[pd.DataFrame, AgentRun]:
    run = AgentRun("briefing", "scheduled 06:30 / on demand")
    rows = []
    days = {"daily": 1, "weekly": 7, "monthly": 30, "quarterly": 90}
    for p in periods:
        run.tool("query_metrics")
        f = fact_pack(p, days[p], inc, kpis, alerts, gaps, actions, health, ref, as_of)
        text = render(f)
        method = "template"
        if llm is not None and llm.available():
            run.llm_calls += 1
            out = llm.complete("You edit government briefings. Rewrite the markdown for a District Collector: short, plain, active voice. "
                               "Keep every heading, table, ID in backticks and every number exactly as given. Add no new numbers or facts.",
                               f"FACTS:\n{jdump(f)}\n\nDRAFT:\n{text}", max_tokens=1600)
            if out and not verify(out, f):
                text, method = out, "llm_rewrite_verified"
        bad = verify(text, f)
        rows.append({"briefing_id": f"BRF-{p}-{as_of.tz_convert(IST):%Y%m%d%H%M}", "period": p, "as_of": as_of, "window_start": f["window_start"],
                     "window_end": f["window_end"], "markdown": text, "fact_pack": jdump(f), "unverified_numbers": len(bad),
                     "unverified_list": "|".join(bad), "method": method, "attention_items": len(f["attention"])})
    run.outputs = {"briefings": len(rows), "unverified_numbers": sum(r["unverified_numbers"] for r in rows)}
    return pd.DataFrame(rows), run
