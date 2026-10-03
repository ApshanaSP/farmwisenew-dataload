"""Incident roll-up: one row per real-world incident, with merged timeline,
SLA, priority (computed at as_of, with reasons) and attention flags."""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from .refdata import SEV_RANK, Reference
from .util import IST, log, to_xy

OFFICIAL = {"grievance", "police", "pwd", "hospital"}
CHANNEL_LABEL = {"citizen_app": "Grievance portal", "control_room_112": "Police 112", "fir_walk_in": "Police FIR",
                 "patrol": "Police patrol", "citizen_grievance": "Citizen grievance", "media": "News", "field_staff": "PWD field staff",
                 "control_room": "PWD control room", "collector_office": "Collector's office", "hospital_mis": "Hospital MIS",
                 "official_notice": "Official notice", "sensor": "Sensor"}


def build(events: pd.DataFrame, timeline: pd.DataFrame, actions: pd.DataFrame, ref: Reference,
          calendar: pd.DataFrame, as_of: pd.Timestamp, zone_names: dict[int, str]) -> dict[str, pd.DataFrame]:
    e = events[events["incident_id"].notna()].copy()
    order = ref.stage_order
    e["_stage"] = e["status_std"].map(order).fillna(0)
    e["_sev"] = e["severity_level"].map(SEV_RANK).fillna(3)
    e["_official"] = e["source"].isin(OFFICIAL)
    # lead member: official record first, then most severe, then earliest
    e = e.sort_values(["incident_id", "_official", "_sev", "severity_score", "reported_at"], ascending=[True, False, True, False, True])
    lead = e.drop_duplicates("incident_id").set_index("incident_id")
    g = e.groupby("incident_id")
    inc = pd.DataFrame(index=lead.index)
    inc["category_code"] = lead["category_code"]
    inc["category_label"] = inc["category_code"].map(lambda c: ref.cat.get(c, ref.cat["OTHER"])["label"])
    inc["family"] = lead["category_family"]
    inc["lead_dept"] = lead["lead_dept"]
    # per-incident sets in one pass over the rows (a Python function per group was most of this step's time)
    ids = e["incident_id"].to_numpy()
    dept_sets = _sets(ids, e["lead_dept"].to_numpy())
    depts = [sorted(dept_sets.get(i, ())) for i in inc.index]
    support = inc["category_code"].map(lambda c: ref.cat.get(c, ref.cat["OTHER"]).get("support") or [])
    inc["depts_involved"] = [sorted(set(a) | {l}) for a, l in zip(depts, inc["lead_dept"])]
    inc["support_depts"] = ["|".join(s) for s in support]
    # coordination = records from two or more departments on the same incident
    inc["needs_coordination"] = [int(len(d) >= 2) for d in inc["depts_involved"]]
    inc["depts_involved"] = inc["depts_involved"].map("|".join)
    inc["first_reported_at"] = g["reported_at"].min()
    inc["occurred_at_est"] = g["occurred_at"].min()
    inc["last_update_at"] = g["status_at"].max().fillna(g["reported_at"].max())
    inc["ward_no"] = lead["ward_no"]
    inc["zone_no"] = lead["zone_no"]
    inc["zone_name"] = inc["zone_no"].map(lambda z: zone_names.get(int(z)) if pd.notna(z) else None)
    inc["taluk_code"] = lead["taluk_code"]
    best = e.sort_values(["incident_id", "loc_precision_m"]).drop_duplicates("incident_id").set_index("incident_id")
    inc["lat"], inc["lon"] = best["lat"], best["lon"]
    inc["place_text"] = lead["place_text"]
    xy = to_xy(e["lat"], e["lon"])
    e["_x"], e["_y"] = xy[:, 0], xy[:, 1]
    inc["spread_m"] = (np.hypot(g["_x"].max() - g["_x"].min(), g["_y"].max() - g["_y"].min())).round(0)
    inc["member_count"] = g.size()
    src_sets = _sets(ids, e["source"].to_numpy(), keep=lambda v: True)
    inc["sources"] = ["|".join(sorted(src_sets.get(i, ()))) for i in inc.index]
    inc["source_count"] = g["source"].nunique()
    chan_sets = _sets(ids, e["channel"].to_numpy())
    inc["channels"] = ["|".join(sorted(chan_sets.get(i, ()))) for i in inc.index]
    inc["citizen_complaints"] = e["source"].eq("grievance").groupby(e["incident_id"]).sum().astype(int)
    inc["police_reports"] = e["source"].eq("police").groupby(e["incident_id"]).sum().astype(int)
    inc["outlet_count"] = e[e["source"] == "news"].groupby("incident_id")["source_record_id"].nunique().reindex(inc.index).fillna(0).astype(int)
    inc["has_official_record"] = g["_official"].any().astype(int)
    inc["media_only"] = ((inc["has_official_record"] == 0) & (inc["outlet_count"] > 0)).astype(int)
    inc["is_synthetic_any"] = g["is_synthetic"].max()
    inc["is_overlay_any"] = g["is_overlay"].max()
    # status: most advanced official status, ignoring rejected duplicates when others are active
    off = e[e["_official"]]
    active = off[off["status_std"] != "Rejected"]
    st_active = active.groupby("incident_id")["_stage"].max()
    st_all = off.groupby("incident_id")["_stage"].max()
    stage = st_active.reindex(inc.index).fillna(st_all.reindex(inc.index)).fillna(0)
    inv = {v: k for k, v in order.items()}
    inc["status_std"] = stage.map(inv)
    # an incident is resolved only when every active official member is resolved
    unresolved = active[~active["status_std"].isin(["Resolved"])].groupby("incident_id").size()
    inc.loc[inc.index.isin(unresolved.index) & (inc["status_std"] == "Resolved"), "status_std"] = "In progress"
    inc["verified"] = off["first_action_at"].notna().groupby(off["incident_id"]).any().astype(int).reindex(inc.index).fillna(0).astype(int)
    inc["verified_at"] = off.groupby("incident_id")["first_action_at"].min().reindex(inc.index)
    inc["closed_at"] = np.where(inc["status_std"].isin(["Resolved", "Rejected"]), g["closed_at"].max().reindex(inc.index), pd.NaT)
    inc["closed_at"] = pd.to_datetime(inc["closed_at"], utc=True).dt.tz_convert(IST)
    # a news-only incident with no new coverage for 7 days lapses (no department ever recorded it)
    news_only = ~g["_official"].any()
    lapsed = news_only.reindex(inc.index) & ((as_of - g["reported_at"].max().reindex(inc.index)) > pd.Timedelta(days=7))
    inc.loc[lapsed, "status_std"] = "Lapsed"
    inc["is_open"] = (~inc["status_std"].isin(["Resolved", "Rejected", "Lapsed"])).astype(int)
    inc["severity_score"] = g["severity_score"].max()
    sev_best = e.drop_duplicates("incident_id").set_index("incident_id")
    inc["severity_level"] = g["_sev"].min().map({v: k for k, v in SEV_RANK.items()})
    inc["severity_reasons"] = sev_best["severity_reasons"]
    inc["dead"] = g["dead"].max().fillna(0)
    inc["injured"] = g["injured"].max().fillna(0)
    inc["persons_affected"] = g["persons_affected"].max()
    vul_sets: dict = defaultdict(set)
    for i, x in zip(ids, e["vulnerable_flags"].to_numpy()):
        if isinstance(x, str):
            vul_sets[i].update(v for v in x.split("|") if v)
    inc["vulnerable"] = ["|".join(sorted(vul_sets.get(i, ()))) for i in inc.index]
    inc["weather_related"] = g["weather_related"].max().fillna(0).astype(int)
    inc["officer"] = lead["officer"]
    inc["reliability"] = g["source_reliability"].max()
    inc["confidence"] = g["link_prob"].min().fillna(1.0).round(3)
    geo_conf = g["geo_conf"].min()
    inc["confidence"] = np.minimum(inc["confidence"], geo_conf.fillna(1.0)).round(3)
    inc["needs_review"] = ((inc["confidence"] < 0.7) | (inc["spread_m"] > 3000) | (inc["member_count"] > 80)).astype(int)
    inc["review_reason"] = np.select([inc["spread_m"] > 3000, inc["member_count"] > 80, inc["confidence"] < 0.7],
                                     ["reports spread over more than 3 km", "unusually many reports", "low location or link confidence"], "")

    # ---- SLA and timing (as_of)
    sla_h = [ref.sla_hours(c, s) for c, s in zip(inc["category_code"], inc["severity_level"])]
    inc["sla_hours"] = sla_h
    inc["sla_due_at"] = inc["first_reported_at"] + pd.to_timedelta(inc["sla_hours"], unit="h")
    end = inc["closed_at"].where(inc["closed_at"].notna(), as_of)
    inc["hours_open"] = ((end - inc["first_reported_at"]).dt.total_seconds() / 3600).round(1)
    inc["hours_to_first_action"] = ((inc["verified_at"] - inc["first_reported_at"]).dt.total_seconds() / 3600).round(1)
    inc["sla_basis"] = inc["category_code"].map(lambda c: ref.cat.get(c, ref.cat["OTHER"]).get("sla_basis", "resolution"))
    resp = inc["sla_basis"] == "response"
    # hours measured against the deadline: time to first response, or time open / to closure
    waited = np.where(resp, inc["hours_to_first_action"].fillna(inc["hours_open"]), inc["hours_open"])
    inc["sla_ratio"] = (waited / inc["sla_hours"]).round(2)
    inc["sla_breached"] = (inc["sla_ratio"] > 1).astype(int)
    recent = e[e["reported_at"] >= as_of - pd.Timedelta(hours=24)]
    inc["growth_24h"] = recent.groupby("incident_id").size().reindex(inc.index).fillna(0).astype(int)
    inc["recurrence_90d"] = _recurrence(inc, ref)
    rain_days = set(calendar.loc[calendar["rain_event"] == 1, "date"]) if calendar is not None and len(calendar) else set()
    near_rain = [any(str((t - pd.Timedelta(days=k)).date()) in rain_days for k in range(0, 3)) for t in inc["first_reported_at"]]
    rs = inc["category_code"].map(lambda c: bool(ref.cat.get(c, ref.cat["OTHER"]).get("rain_sensitive")))
    inc["rain_coupled"] = (np.array(near_rain) & rs.to_numpy()).astype(int)

    # ---- priority with reasons
    pr, reasons = [], []
    for r in inc.itertuples():
        s = float(r.severity_score)
        why = [f"{r.severity_level} {r.category_label.lower()} ({r.severity_score:.0f})"]
        if r.source_count > 1:
            s += 5 * np.log2(r.source_count); why.append(f"reported by {r.source_count} sources")
        if r.citizen_complaints > 1:
            s += 3 * np.log2(1 + r.citizen_complaints); why.append(f"{r.citizen_complaints} citizen complaints")
        if r.is_open:
            over = r.sla_ratio if pd.notna(r.sla_ratio) else 0
            what = "response" if r.sla_basis == "response" else "resolution"
            if over > 2:
                s += 20; why.append(f"{what} over twice the {r.sla_hours:.0f} h target")
            elif over > 1:
                s += 10; why.append(f"{what} past the {r.sla_hours:.0f} h target")
            if not r.verified and r.severity_level in ("Severe", "High"):
                s += 8; why.append("not yet verified by an officer")
            if r.growth_24h >= 3:
                s += 8; why.append(f"{r.growth_24h} new reports in 24 h")
        if r.vulnerable:
            s += 5; why.append(f"affects {r.vulnerable.replace('|', ', ').replace('_', ' ')}")
        if r.outlet_count >= 2:
            s += 5; why.append(f"covered by {r.outlet_count} news outlets")
        if r.media_only:
            s += 5; why.append("in the news but not in any department's records")
        if r.rain_coupled:
            s += 5; why.append("linked to a rain event")
        if r.recurrence_90d >= 3:
            s += 5; why.append(f"{r.recurrence_90d} similar incidents here in 90 days")
        pr.append(round(s, 1))
        reasons.append("; ".join(why))
    inc["priority_score"] = pr
    inc["priority_reasons"] = reasons
    inc["awaiting_collector"] = (inc["status_std"] == "Awaiting verification").astype(int)
    age_d = (as_of - inc["first_reported_at"]).dt.total_seconds() / 86400
    hi = inc["severity_level"].isin(["Severe", "High"])
    opn = inc["is_open"] == 1
    conds = [
        opn & (inc["severity_level"] == "Severe") & (inc["verified"] == 0) & (age_d <= 30),
        opn & hi & (inc["sla_ratio"] > 2) & (age_d <= 30) & (inc["media_only"] == 0),
        opn & hi & (inc["needs_coordination"] == 1) & (age_d <= 14),
        opn & hi & (inc["media_only"] == 1) & (age_d <= 7),
        opn & (inc["growth_24h"] >= 3),
    ]
    labels = ["Severe and not verified", "Well past its deadline", "Needs several departments",
              "Only in the news", "Spreading: several new reports in 24 h"]
    inc["attention_reason"] = [", ".join(l for l, c in zip(labels, cs) if c) for cs in zip(*[c.to_numpy() for c in conds])]
    inc["attention_flag"] = (inc["attention_reason"] != "").astype(int)

    # ---- titles and summaries (deterministic; the Briefing agent may rewrite for display)
    inc["place_text"] = inc["place_text"].astype("string").str.replace(r"\s+", " ", regex=True).str.strip()
    news_led = lead["source"].reindex(inc.index) == "news"
    inc["title"] = [str(h)[:120] if nl else (f"{c} – {p}" if isinstance(p, str) and p else c)
                    for nl, h, c, p in zip(news_led, lead["title"].reindex(inc.index), inc["category_label"], inc["place_text"])]
    srcs_lbl = pd.Series([", ".join(sorted({CHANNEL_LABEL.get(c, str(c)) for c in chan_sets.get(i, ())})) for i in inc.index], index=inc.index)
    inc["summary"] = [
        f"{int(n)} report{'s' if n > 1 else ''} ({sl}) about {lbl.lower()} at {p or 'an unresolved location'}"
        f"{f', Ward {int(w)}' if pd.notna(w) else ''}{f' ({zn})' if isinstance(zn, str) else ''}. "
        f"First reported {fr:%d %b %H:%M}. Status: {st.lower()}."
        for n, sl, lbl, p, w, zn, fr, st in zip(inc["member_count"], srcs_lbl.reindex(inc.index).fillna(""), inc["category_label"],
                                               inc["place_text"], inc["ward_no"], inc["zone_name"], inc["first_reported_at"], inc["status_std"])]
    inc = inc.reset_index().rename(columns={"index": "incident_id"})

    # ---- members, merged timeline, actions
    first_src = e.sort_values("reported_at").drop_duplicates(["incident_id", "source"])
    members = e[["incident_id", "event_id", "source", "source_record_id", "channel", "reported_at", "link_prob", "link_method",
                 "severity_level", "status_std", "deep_link", "title", "is_overlay"]].copy()
    first_ev = e.sort_values("reported_at").drop_duplicates("incident_id")["event_id"]
    members["role"] = np.select([members["event_id"].isin(first_ev), members["source"] == "news", members["source"].isin(OFFICIAL)],
                                ["first_report", "media_coverage", "corroboration"], "corroboration")
    tl = timeline.merge(e[["event_id", "incident_id"]], on="event_id", how="inner")
    extra = first_src[~first_src["event_id"].isin(first_ev)]
    corro = pd.DataFrame({"event_id": extra["event_id"], "at": extra["reported_at"], "step": np.where(extra["source"] == "news", "News coverage", "Corroborated"),
                          "status_std": None, "actor": extra["channel"].map(lambda c: CHANNEL_LABEL.get(c, c)),
                          "note": "Also reported via " + extra["channel"].map(lambda c: CHANNEL_LABEL.get(c, c)).fillna(extra["source"]),
                          "source": extra["source"], "incident_id": extra["incident_id"]})
    news_first = e[e["source"] == "news"].sort_values("reported_at").drop_duplicates("incident_id")
    news_first = news_first[news_first["event_id"].isin(first_ev)]
    nf = pd.DataFrame({"event_id": news_first["event_id"], "at": news_first["reported_at"], "step": "First report", "status_std": "Open",
                       "actor": "News", "note": news_first["title"], "source": "news", "incident_id": news_first["incident_id"]})
    timeline_inc = pd.concat([tl, corro, nf], ignore_index=True).sort_values(["incident_id", "at"])
    act = actions.merge(e[["event_id", "incident_id"]], on="event_id", how="left", suffixes=("_x", ""))
    act["incident_id"] = act["incident_id"].fillna(act.get("incident_id_x"))
    act = act.drop(columns=[c for c in act.columns if c.endswith("_x")])
    ac = act.groupby("incident_id").agg(action_total=("action_id", "size"), action_done=("status", lambda s: int((s == "Done").sum())),
                                        next_action_due=("due_at", "min"))
    inc = inc.merge(ac, left_on="incident_id", right_index=True, how="left")
    inc["action_total"] = inc["action_total"].fillna(0).astype(int)
    inc["action_done"] = inc["action_done"].fillna(0).astype(int)
    log.info("incidents: %d total, %d open, %d multi-source, %d need attention, %d awaiting Collector verification",
             len(inc), int(inc["is_open"].sum()), int((inc["source_count"] > 1).sum()), int(inc["attention_flag"].sum()),
             int(inc["awaiting_collector"].sum()))
    return {"incidents": inc, "members": members, "timeline": timeline_inc, "actions": act}


def _sets(ids: np.ndarray, values: np.ndarray, keep=lambda v: isinstance(v, str)) -> dict:
    """incident_id -> set of the values its rows carry (by default only text values, skipping blanks/NaN)."""
    out: dict = defaultdict(set)
    for i, v in zip(ids, values):
        if keep(v):
            out[i].add(v)
    return out


def _recurrence(inc: pd.DataFrame, ref: Reference) -> pd.Series:
    out = pd.Series(0, index=inc.index)
    t = inc["first_reported_at"].astype("int64").to_numpy() / 8.64e13
    for cat, grp in inc[inc["lat"].notna()].groupby("category_code"):
        if len(grp) < 2:
            continue
        r = ref.cat.get(cat, ref.cat["OTHER"])["link_radius_m"]
        xy = to_xy(grp["lat"], grp["lon"])
        tree = cKDTree(xy)
        tt = grp["first_reported_at"].astype("int64").to_numpy() / 8.64e13
        cnt = []
        for i, nb in enumerate(tree.query_ball_point(xy, r)):
            cnt.append(sum(1 for j in nb if j != i and 0 < tt[i] - tt[j] <= 90))
        out.loc[grp.index] = cnt
    return out
