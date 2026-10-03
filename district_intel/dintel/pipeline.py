"""Build the curated store from the current source outputs. Source files are only read."""
from __future__ import annotations

import json
import re
import time
from collections import Counter

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from . import PIPELINE_VERSION, analytics, classify, dedup, geolocate, incidents, linking, store, world
from .agents import briefing, steward, workers
from .agents.llm import LLM
from .geo import WardIndex
from .loaders import environment, grievances, hospital, news, police, pwd
from .refdata import Reference
from .schema import ACTION_COLUMNS, EVENT_COLUMNS, conform
from .util import IST, Settings, jdump, log


def _as_of(settings: Settings, events: pd.DataFrame) -> pd.Timestamp:
    v = str(settings.pipe.get("as_of", "auto"))
    now = pd.Timestamp.now(tz=IST)
    if v == "now":
        return now
    if v != "auto":
        return pd.Timestamp(v).tz_convert(IST) if pd.Timestamp(v).tzinfo else pd.Timestamp(v).tz_localize(IST)
    t = events.loc[events["reported_at"] <= now, "reported_at"].max()
    return t.ceil("min")


def _refine_police_other(ev: pd.DataFrame, ref: Reference) -> None:
    """Police reports filed as OTHER: place them by keywords (e.g. 'wall collapse' -> Unsafe buildings)."""
    m = (ev["source"] == "police") & (ev["category_code"] == "POLICE_OTHER")
    kw = [(c["code"], [k.lower() for k in c.get("keywords_en") or []]) for c in ref.cat.values() if c["code"] not in ("POLICE_OTHER", "OTHER")]
    for i in ev.index[m]:
        t = f"{ev.at[i, 'title']} {ev.at[i, 'text']}".lower()
        best = max(((code, sum(k in t for k in ks)) for code, ks in kw), key=lambda x: x[1])
        if best[1] >= 1:
            ev.at[i, "category_code"] = best[0]
            ev.at[i, "category_method"] = "keyword:police_other"
            ev.at[i, "category_conf"] = 0.7


def _extra_places(settings: Settings, pwd_events: pd.DataFrame) -> pd.DataFrame:
    pol = json.loads(settings.src("police", "taluks_config").read_text(encoding="utf-8"))
    rows = [{"name": l["name"], "lat": l["lat"], "lon": l["lng"]} for t in pol["taluks"] for l in t["localities"]]
    p = pwd_events.groupby("place_text")[["lat", "lon"]].mean().reset_index()
    p["name"] = p["place_text"].str.replace(r"\s*\(.*\)$", "", regex=True)
    rows += p[["name", "lat", "lon"]].to_dict("records")
    return pd.DataFrame(rows).drop_duplicates("name")


def _flood_corr(events: pd.DataFrame) -> dict:
    """Daily Spearman (strict) and 3-day-episode Pearson (a rain day and the two after it: complaints lag the rain)."""
    e = events[(events["category_family"] == "FLOOD") & events["source"].isin(["grievance", "police", "pwd"])]
    c = e.groupby([e["reported_at"].dt.tz_convert(IST).dt.date, "source"]).size().unstack(fill_value=0)
    c.index = pd.to_datetime(c.index)
    c = c.asfreq("D", fill_value=0)
    ep = c.rolling(3, min_periods=1).sum()
    out = {"daily_spearman": {}, "episode_3day_pearson": {}}
    for a, b in (("grievance", "police"), ("grievance", "pwd"), ("police", "pwd")):
        if a in c and b in c:
            out["daily_spearman"][f"{a}~{b}"] = round(float(spearmanr(c[a], c[b]).statistic), 3)
            out["episode_3day_pearson"][f"{a}~{b}"] = round(float(ep[a].corr(ep[b])), 3)
    return out


def build(settings: Settings, only_steps: set[str] | None = None) -> dict:
    t0 = time.time()
    ref = Reference(settings)
    wards = WardIndex(settings.src("wards_geojson"))
    zone_names = {w.zone_no: w.zone_name for w in wards.wards}
    llm = LLM(settings.raw.get("llm", {}))
    metrics: dict = {"pipeline_version": PIPELINE_VERSION, "llm_enabled": llm.enabled}
    agent_runs = []

    # ------------------------------------------------------------------ load --
    log.info("loading sources")
    G = grievances.load(settings, ref)
    P = police.load(settings, ref)
    W = pwd.load(settings, ref)
    H = hospital.load(settings, ref)
    E = environment.load(settings, ref)
    N = news.load(settings, ref, _extra_places(settings, W["events"]))
    docs = N["documents"]

    base = pd.concat([G["events"], P["events"], W["events"], H["events"], E["events"]], ignore_index=True)
    model = classify.daily_model(base)
    metrics["category_classifier"] = model.metrics
    classify.apply(base, docs, model)
    _refine_police_other(base, ref)
    NE = news.to_events(docs, ref, N["snapshot"])
    events = pd.concat([base, NE], ignore_index=True)
    events["category_family"] = events["category_code"].map(ref.family)
    events["support_depts"] = events["category_code"].map(lambda c: "|".join(ref.cat.get(c, ref.cat["OTHER"]).get("support") or []))
    metrics["news_gate"] = N.get("gate_eval", {})
    metrics["news_funnel"] = {"feed_rows": N["raw_rows"], "unique_urls": N["unique_urls"], "documents": len(docs),
                              "stories": int(docs["story_id"].nunique()), "incident_articles": int(docs["is_incident"].sum()),
                              "incident_events": int(len(NE)), "with_full_text": int((docs["body_status"] == "full").sum()),
                              "located_below_district": int((docs["geo_level"] != "district").sum())}

    # ------------------------------------------------------------------- geo --
    ward_taluk = geolocate.ward_taluk_vote(events, wards, ref.taluks)
    gmask = events["source"] == "grievance"
    tables = geolocate.place_tables(events[gmask])
    metrics["geo_holdout"] = geolocate.geo_holdout(events[gmask])
    events = geolocate.resolve(events, wards, ward_taluk, settings.pipe["snap_outside_points_m"], tables)
    as_of = _as_of(settings, events)
    metrics["as_of"] = str(as_of)
    log.info("as_of = %s", as_of)
    metrics["flood_day_correlation_before_overlay"] = _flood_corr(events)

    # --------------------------------------------------------------- overlay --
    timeline = pd.concat([G["timeline"], P["timeline"], W["timeline"], H["timeline"]], ignore_index=True)
    actions = W["actions"].copy()
    truth = None
    hosp_fac = H["facilities"].copy()
    hosp_fac["in_district"] = wards.locate(hosp_fac["lat"].to_numpy(), hosp_fac["lon"].to_numpy()) > 0
    if settings.raw["overlay"].get("enabled", True):
        O = world.build(settings, ref, wards, events, docs, W["offices"], hosp_fac[hosp_fac["in_district"]], as_of)
        oe = O["events"]
        oe["category_family"] = oe["category_code"].map(ref.family)
        oe["support_depts"] = oe["category_code"].map(lambda c: "|".join(ref.cat.get(c, ref.cat["OTHER"]).get("support") or []))
        oe = geolocate.resolve(oe, wards, ward_taluk, settings.pipe["snap_outside_points_m"], tables)
        events = pd.concat([events, oe], ignore_index=True)
        timeline = pd.concat([timeline, O["timeline"]], ignore_index=True)
        actions = pd.concat([actions, O["actions"]], ignore_index=True)
        truth, calendar = O["truth"], O["calendar"]
        metrics["overlay"] = {**O["info"], "planted_records": int(len(oe)), "by_source": oe["source"].value_counts().to_dict(),
                              "world_events": int(truth["world_event_id"].nunique()) if len(truth) else 0}
        metrics["flood_day_correlation_after_overlay"] = _flood_corr(events)
    else:
        calendar, info = world.build_calendar(world.Ctx(settings, ref, wards, np.random.default_rng(0), as_of, {}), events)

    # ----------------------------------------------------------------- dedup --
    gg, ginfo = dedup.grievances(events)
    pg = dedup.police(events)
    wg = dedup.pwd(events)
    grp = {**gg, **pg, **wg}
    events["dup_group_id"] = events["event_id"].map(grp).fillna(events["dup_group_id"]).fillna(events["event_id"])
    metrics["dedup"] = {**dedup.evaluate(events, gg, pg, settings), **ginfo}

    # --------------------------------------------------------------- linking --
    L = linking.link(events, ref, truth, settings.pipe["link_merge_threshold"], settings.pipe["link_review_threshold"])
    events, pairs = L["events"], L["pairs"]
    metrics["linking"] = L["metrics"]
    docs["event_id"] = np.where(docs["doc_id"].isin(events.loc[events["source"] == "news", "source_record_id"]), "NEWS-" + docs["doc_id"], None)
    docs["linked_incident_id"] = docs["event_id"].map(dict(zip(events["event_id"], events["incident_id"])))

    # ------------------------------------------------------------- incidents --
    I = incidents.build(events, timeline, conform(actions, ACTION_COLUMNS), ref, calendar, as_of, zone_names)
    inc = I["incidents"]

    # ------------------------------------------------------------- analytics --
    hs, assign = analytics.hotspots(inc, as_of, settings.pipe["hotspot_eps_m"], settings.pipe["hotspot_min_samples"])
    inc["hotspot_id"] = inc["incident_id"].map(assign)
    an = analytics.anomalies(inc, as_of, 7, settings.pipe["anomaly_p_value"], settings.pipe["anomaly_min_count"], settings.pipe["baseline_days"])
    adj = wards.adjacency()
    obs = pd.concat([W["observations"], H["observations"], E["observations"]], ignore_index=True)
    ow = wards.locate(obs["lat"].to_numpy(float), obs["lon"].to_numpy(float))
    obs["ward_no"] = pd.Series(ow).where(ow > 0).astype("Int64").values
    ward_zone = {x.ward_no: x.zone_no for x in wards.wards}
    obs["zone_no"] = pd.Series(obs["ward_no"]).map(lambda w: ward_zone.get(int(w)) if pd.notna(w) else None).astype("Int64").values
    ws = analytics.ward_stats(wards, events, inc, ward_taluk, adj, as_of)
    sig = analytics.observation_signals(obs, as_of)
    kp = analytics.kpis(inc, events, as_of)
    dc = analytics.daily_counts(events)

    # ---------------------------------------------------------------- agents --
    drafts, r1 = workers.action_planner(inc, I["actions"], events, W["offices"], ref, as_of)
    all_actions = pd.concat([I["actions"], drafts], ignore_index=True) if len(drafts) else I["actions"]
    ac = all_actions.groupby("incident_id").agg(action_total=("action_id", "size"),
                                                 action_done=("status", lambda s: int((s == "Done").sum())), next_action_due=("due_at", "min"))
    inc = inc.drop(columns=["action_total", "action_done", "next_action_due"]).merge(ac, left_on="incident_id", right_index=True, how="left")
    inc["action_total"] = inc["action_total"].fillna(0).astype(int)
    inc["action_done"] = inc["action_done"].fillna(0).astype(int)
    gaps, r2 = workers.gap_finder(inc, as_of)
    link_items, r3 = workers.linker(pairs, events, llm)
    warn_obs = obs[(obs["metric"] == "imd_warning_level") & (obs["value"] >= 1) & (obs["observed_at"] >= as_of.normalize())]
    alerts, r4 = workers.watchdog(inc, an, sig, warn_obs, ref, as_of, zone_names)
    S = steward.run(settings, ref, events, obs, all_actions, W["works"], docs, N["raw_rows"], as_of, llm)
    alerts += S["alerts"]
    alerts_df = pd.DataFrame(alerts)
    alerts_df.insert(0, "alert_id", [f"ALR-{i + 1:04d}" for i in range(len(alerts_df))])
    alerts_df["created_at"] = as_of
    alerts_df["status"] = "new"
    B, r5 = briefing.run(inc, kp, alerts_df, gaps, all_actions, S["health"], ref, as_of, llm)
    agent_runs = [r.row(as_of) for r in (r1, r2, r3, r4, S["run"], r5)]
    metrics["briefing_unverified_numbers"] = int(B["unverified_numbers"].sum())

    # ---------------------------------------------------------- review queue --
    geo_low = events[(events["geo_conf"].fillna(1) < settings.pipe["geo_review_threshold"]) & (events["source"] != "news")]
    geo_items = pd.DataFrame({"item_type": "geo", "item_id": geo_low["event_id"], "suggestion": geo_low["ward_no"].astype(str),
                              "reason": "Low location confidence (" + geo_low["geo_method"].astype(str) + ")", "evidence": geo_low["place_text"]})
    gap_items = pd.DataFrame({"item_type": "gap", "item_id": gaps["incident_id"], "suggestion": gaps["suggested_action"],
                              "reason": "In the news, no departmental record", "evidence": gaps["title"]})
    review = pd.concat([link_items, S["proposals"], geo_items, gap_items], ignore_index=True)
    review.insert(0, "review_id", [f"REV-{i + 1:05d}" for i in range(len(review))])
    review["status"] = "open"
    review["created_at"] = as_of

    # --------------------------------------------------------------- outputs --
    out = settings.out_dir
    ev_store = conform(events, EVENT_COLUMNS)
    docs_store = docs.drop(columns=[c for c in docs.columns if c.startswith("_")])
    docs_store = pd.concat([docs_store, W["announcements"], E["documents"]], ignore_index=True)
    ref_tables = {"ref_departments": ref.departments.assign(source_names=ref.departments["source_names"].map(lambda v: "|".join(v))),
                  "ref_categories": ref.categories, "ref_taluks": ref.taluks, "ref_wards": ws,
                  "ref_facilities": pd.concat([P["facilities"], W["facilities"], H["facilities"].assign(in_district=hosp_fac["in_district"].values),
                                               E["facilities"]], ignore_index=True),
                  "ref_offices": W["offices"]}
    metrics["runtime_s"] = round(time.time() - t0, 1)
    metrics_df = pd.DataFrame([{"metric": k, "value": jdump(v)} for k, v in metrics.items()])
    tables = {
        "events": ev_store, "incidents": inc, "incident_members": I["members"], "incident_timeline": I["timeline"],
        "actions": all_actions, "documents": docs_store, "observations": obs, "observation_signals": sig, "forecasts": E["forecasts"],
        "daily_counts": dc, "anomalies": an, "hotspots": hs, "kpis": kp, "alerts": alerts_df, "briefings": B, "gaps": gaps,
        "link_pairs": pairs, "review_queue": review, "source_health": S["health"], "data_quality": S["issues"], "quarantine": S["quarantine"],
        "category_drift": S["drift"], "world_calendar": calendar, "pwd_works": W["works"], "agent_runs": pd.DataFrame(agent_runs),
        "metrics": metrics_df, **ref_tables}
    ready = store.prepare(tables)
    store.write_sqlite(settings.path(settings.raw["output"]["sqlite"]), ready, prepared=True)
    store.write_csv(out / "curated", ready, prepared=True)
    if truth is not None:
        (out / "truth").mkdir(parents=True, exist_ok=True)
        truth.to_csv(out / "truth" / "cross_source_truth.csv", index=False, encoding="utf-8-sig")
    extra = _dashboard_extras(settings, wards, ws, inc, obs, sig, E, ref, zone_names, S["health"], as_of, metrics)
    store.write_dashboard(out / "dashboard", tables, extra)
    _reports(out / "reports", metrics, S, B, as_of)
    log.info("done in %.1f s", time.time() - t0)
    return metrics


def _dashboard_extras(settings, wards, ws, inc, obs, sig, E, ref, zone_names, health, as_of, metrics) -> dict:
    props = {int(r.ward_no): {"taluk": r.taluk_code, "lowLying": r.low_lying_index, "open": r.open_incidents,
                              "incidents30d": r.incidents_30d, "giZ": r.gi_star_z_30d, "hot": r.hot_ward} for r in ws.itertuples()}
    outlines = wards.zone_outlines()
    zo = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"zone_no": z, "zone_name": zone_names.get(z)},
                                                     "geometry": {"type": "MultiLineString", "coordinates": segs}} for z, segs in outlines.items()]}
    s = sig.copy()
    env = {
        "warnings": [{"date": str(r.observed_at.date()), "level": int(r.value), "text": r.detail}
                     for r in obs[obs["metric"] == "imd_warning_level"].sort_values("observed_at").itertuples()],
        "forecasts": [{"station": r.location_id.split(":")[-1], "date": str(r.valid_from.date()), "text": r.forecast_text}
                      for r in E["forecasts"].sort_values("valid_from").itertuples()] if len(E["forecasts"]) else [],
        "aqi": s[s["metric"] == "aqi"][["place_name", "value", "detail", "observed_at", "lat", "lon"]].to_dict("records"),
        "lakes": s[s["metric"] == "lake_pct_full"][["place_id", "place_name", "value", "slope_per_day", "days_to_full", "lat", "lon"]]
        .sort_values("value", ascending=False).to_dict("records"),
        "reservoir_inflow": s[s["metric"] == "reservoir_inflow_cusec"][["place_name", "value", "ewma7", "observed_at"]].to_dict("records"),
        "hospitals": s[s["metric"].isin(["bed_occupancy_pct", "health_alert_level", "emergency_cases"])]
        .pivot_table(index="place_name", columns="metric", values="value", aggfunc="last").reset_index().to_dict("records"),
        "temperature": s[s["metric"].isin(["temp_max_c", "temp_min_c", "humidity_pct"])][["place_name", "metric", "value", "observed_at"]].to_dict("records"),
        "rainfall_note": "IMD rainfall is published for 24 h ending 08:30 IST; history accumulates as the collector runs.",
    }
    x = inc.copy()
    x["date"] = x["first_reported_at"].dt.tz_convert(IST).dt.date.astype(str)
    trends = {"by_family": x.groupby(["date", "family"]).size().rename("n").reset_index().to_dict("records"),
              "by_category_week": x.assign(week=x["first_reported_at"].dt.tz_convert(IST).dt.to_period("W").astype(str))
              .groupby(["week", "category_code"]).size().rename("n").reset_index().to_dict("records")}
    meta = {"as_of": str(as_of), "generated_at": str(pd.Timestamp.now(tz=IST)), "pipeline_version": PIPELINE_VERSION,
            "counts": {"events": int(metrics.get("linking", {}).get("linkable_events", 0)), "incidents": int(len(inc)),
                       "open_incidents": int(inc["is_open"].sum())},
            "departments": ref.departments[["code", "name", "org", "head", "route", "action_owner"]].to_dict("records"),
            "categories": ref.categories[["category_code", "label", "family", "lead_dept", "base_severity"]].to_dict("records"),
            "zones": [{"zone_no": z, "zone_name": n} for z, n in sorted(zone_names.items())],
            "taluks": ref.taluks[["taluk_code", "name", "name_ta", "in_district", "lat", "lon"]].to_dict("records"),
            "stages": ref.status["stages"], "severities": ["Severe", "High", "Medium", "Low"],
            "overlay": metrics.get("overlay"), "sources": health[["source", "status", "last_success_at", "detail"]].to_dict("records")}
    return {"meta": meta, "environment": env, "trends": trends, "wards_geojson": wards.ward_geojson(props), "zone_outlines": zo}


def _reports(dir_, metrics, S, B, as_of) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    L = [f"# Evaluation report", f"_As of {as_of:%d %b %Y %H:%M} IST · {metrics['pipeline_version']}_", ""]
    d = metrics.get("dedup", {})
    L += ["## Within-source duplicates (B-cubed)", "| Source | Precision | Recall | F1 | Items |", "|---|---|---|---|---|"]
    for k in ("grievance_dedup", "police_dedup"):
        if k in d:
            v = d[k]
            L.append(f"| {k.split('_')[0]} | {v['precision']} | {v['recall']} | {v['f1']} | {v['n']} |")
    L += ["", f"Officer duplicate decisions used: {d.get('officer_duplicate_decisions_used')}", ""]
    lk = metrics.get("linking", {})
    L += ["## Cross-source linking (held-out half of the world events)", f"- Scorer: {lk.get('scorer')}",
          f"- Pairwise: {json.dumps(lk.get('eval_cross_source_pairs'))}", f"- World events fully joined: {lk.get('eval_world_events_fully_joined')}",
          f"- Linked pairs {lk.get('linked_pairs')}, review band {lk.get('review_pairs')}, multi-source incidents {lk.get('multi_source_incidents')}",
          f"- Coefficients: {json.dumps(lk.get('scorer_coefficients'))}", ""]
    L += ["## Geo resolution from text (20% of pinned grievances held out)", f"- {json.dumps(metrics.get('geo_holdout'))}", ""]
    L += ["## Category classifier (multilingual, held-out 20%)", f"- {json.dumps(metrics.get('category_classifier'))}", ""]
    L += ["## One shared world", f"- Flood-day correlation before overlay: {json.dumps(metrics.get('flood_day_correlation_before_overlay'))}",
          f"- After overlay: {json.dumps(metrics.get('flood_day_correlation_after_overlay'))}", f"- Overlay: {json.dumps(metrics.get('overlay'), default=str)}", ""]
    ng = metrics.get("news_gate", {})
    L += ["## News incident filter (300 hand labels, stratified; population-weighted 5-fold cross-validation)",
          f"- Weak labels only: {json.dumps(ng.get('weak_labels_only'))}",
          f"- Weak + hand labels: {json.dumps(ng.get('weak_plus_hand_labels_cv'))} at threshold {ng.get('threshold')}",
          f"- Features: {ng.get('features')}; story merges from embeddings: {json.dumps(ng.get('story_merges'))}", ""]
    L += ["## News funnel", f"- {json.dumps(metrics.get('news_funnel'))}", "",
          f"## Briefings", f"- Unverified numbers across all briefings: {metrics.get('briefing_unverified_numbers')}", ""]
    (dir_ / "evaluation.md").write_text("\n".join(L), encoding="utf-8")

    h = S["health"]
    Q = [f"# Data quality report", f"_As of {as_of:%d %b %Y %H:%M} IST_", "", "## Source health",
         "| Source | Status | Last success | Newest record | Rows | Detail |", "|---|---|---|---|---|---|"]
    for r in h.itertuples():
        Q.append(f"| {r.source} | {r.status} | {r.last_success_at} | {r.newest_record_at} | {r.rows} | {str(r.detail)[:160]} |")
    Q += ["", "## Data contract", "| Table | Check | Severity | Failing | Total | Action |", "|---|---|---|---|---|---|"]
    for r in S["issues"].itertuples():
        Q.append(f"| {r.table} | {r.check} | {r.severity} | {r.failing} | {r.total} | {r.action} |")
    Q += ["", f"Quarantined rows: {len(S['quarantine'])}", "", "## Category drift (7 days vs previous 28)"]
    Q += [f"- {r.source}: PSI {r.psi_7d_vs_28d} ({r.drift}); {r.biggest_shifts}" for r in S["drift"].itertuples()] if len(S["drift"]) else ["- Not enough data"]
    Q += ["", f"Crosswalk proposals awaiting review: {len(S['proposals'])}"]
    (dir_ / "data_quality.md").write_text("\n".join(Q), encoding="utf-8")
    for r in B.itertuples():
        (dir_ / f"briefing_{r.period}.md").write_text(r.markdown, encoding="utf-8")
