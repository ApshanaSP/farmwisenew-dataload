"""PWD -> events (incidents, orphan tasks), actions (tasks), observations (lake levels),
facilities (assets), offices, works register and announcements."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .. import severity as sev
from .. import textproc as tp
from ..refdata import Reference
from ..schema import ACTION_COLUMNS, EVENT_COLUMNS, OBS_COLUMNS, TIMELINE_COLUMNS, conform
from ..util import Settings, sha256_file, to_ist

TASK_CATEGORY = [
    (re.compile(r"stagnant water|pump out|waterlog|inundat", re.I), "FLOOD_WATERLOGGING", "waterlogging"),
    (re.compile(r"desilt|drain|culvert|silt", re.I), "DRAINAGE_SEWAGE", "blocked_drain"),
    (re.compile(r"roof|leak|crack|wall|building|plaster", re.I), "BUILDING_SAFETY", "building_crack"),
    (re.compile(r"encroach", re.I), "ENCROACHMENT", "encroachment"),
    (re.compile(r"bund|sluice|shutter|lake|surplus", re.I), "WATERBODY_INFRA", "sluice_failure"),
    (re.compile(r"bridge|culvert", re.I), "ROAD_DAMAGE", "bridge_damage"),
]


def load(settings: Settings, ref: Reference) -> dict[str, pd.DataFrame]:
    d = settings.src("pwd", "dir")
    inc = pd.read_csv(d / "pwd_incidents.csv", encoding="utf-8-sig")
    tasks = pd.read_csv(d / "pwd_tasks.csv", encoding="utf-8-sig")
    assets = pd.read_csv(d / "pwd_assets.csv", encoding="utf-8-sig")
    levels = pd.read_csv(d / "pwd_water_levels.csv", encoding="utf-8-sig")
    works = pd.read_csv(d / "pwd_works.csv", encoding="utf-8-sig")
    offices = pd.read_csv(d / "pwd_offices.csv", encoding="utf-8-sig")
    ann = pd.read_csv(d / "pwd_announcements.csv", encoding="utf-8-sig")
    snap = sha256_file(d / "pwd_incidents.csv")
    for c in ("assigned_at", "accepted_at", "completed_at", "verified_at"):
        tasks[c] = to_ist(tasks[c])
    tasks["due_at"] = to_ist(tasks["due_date"])
    office = offices.set_index("office_id")
    asset = assets.set_index("asset_id")

    # ---------------------------------------------------------------- events --
    inc["reported_at"] = to_ist(inc["reported_at"])
    inc["resolved_at"] = to_ist(inc["resolved_at"])
    med = inc.groupby("incident_type")["people_affected_est"].median()
    imputed = inc["people_affected_est"].isna()
    inc["people_affected"] = inc["people_affected_est"].fillna(inc["incident_type"].map(med)).fillna(0)

    by_inc = tasks[tasks["incident_id"].notna()].sort_values("assigned_at").groupby("incident_id")
    first_task = by_inc.first()
    last_task = by_inc.last()

    ev = pd.DataFrame()
    ev["source_record_id"] = inc["incident_id"]
    ev["event_id"] = inc["incident_id"]
    ev["source"] = "pwd"
    ev["snapshot_sha256"] = snap
    ev["is_synthetic"] = 1
    ev["is_overlay"] = 0
    ev["occurred_at"] = inc["reported_at"]
    ev["reported_at"] = inc["reported_at"]
    ev["closed_at"] = inc["resolved_at"]
    ev["time_precision"] = "exact"
    ev["text"] = inc["description"]
    ev["title"] = inc["incident_type"].str.replace("_", " ").str.capitalize() + ", " + inc["locality"]
    ev["lang"] = [tp.language_of(t) for t in inc["description"]]
    ev["junk_flag"] = 0
    ev["simhash"] = [tp.simhash64(t) for t in inc["description"]]
    ev["category_src"] = inc["incident_type"]
    ev["category_code"] = inc["incident_type"].map(ref.cmap["pwd"]).fillna("OTHER")
    ev["category_method"] = "crosswalk"
    ev["category_conf"] = 1.0
    atype = inc["asset_id"].map(assets.set_index("asset_id")["asset_type"])
    wing = inc["incident_id"].map(first_task["assigned_office_id"]).map(office["wing"])
    ev["lead_dept"] = np.where(wing.eq("Buildings") | atype.eq("govt_building"), "PWD-BLD", "PWD-WRD")
    ev["dept_src"] = np.where(ev["lead_dept"] == "PWD-BLD", "Buildings", "Water Resources")
    ev["is_actionable"] = 1
    ev["lat"], ev["lon"] = inc["latitude"], inc["longitude"]
    ev["loc_precision_m"] = 50.0
    ev["place_text"] = inc["locality"] + np.where(inc["asset_id"].notna(), " (" + inc["asset_id"].map(asset["asset_name"]).fillna("") + ")", "")
    ev["taluk_src"] = inc["taluk_code"]
    ev["taluk_code"] = inc["taluk_code"].map(ref.taluk_by_pwd)
    ev["geo_method"], ev["geo_level"], ev["geo_conf"] = "source_point", "point", 1.0
    ev["injured"] = inc["casualty_reported"]
    ev["dead"] = 0
    ev["persons_affected"] = inc["people_affected"]
    ev["affected_imputed"] = imputed.astype(int)
    ev["vulnerable_flags"] = ["|".join(tp.vulnerable_flags(t)) for t in inc["description"]]
    ev["hazard_flag"] = [int(tp.hazard(t)) for t in inc["description"]]
    ev["access_blocked"] = inc["access_blocked"]
    ev["service_disruption"] = inc["service_disruption"]
    ev["weather_related"] = inc["incident_type"].isin(["waterlogging", "canal_overflow", "lake_surplus", "bund_breach"]).astype(int)
    ev["claims_prior_complaint"] = [int(tp.repeat_claim(t)) for t in inc["description"]]
    ev["has_photo"] = 0
    ev["status_src"] = inc["status"]
    std = [ref.std_status("pwd_incident", s) for s in inc["status"]]
    lt = inc["incident_id"].map(last_task["status"])
    ev["status_std"] = np.where((lt == "completed_pending_verification") & (inc["status"] != "resolved"), "Awaiting verification", std)
    ev["status_at"] = inc["resolved_at"].fillna(inc["reported_at"])
    ev["first_action_at"] = inc["incident_id"].map(first_task["assigned_at"])
    ev["response_applicable"] = 1
    ev["response_minutes"] = ((ev["first_action_at"] - ev["reported_at"]).dt.total_seconds() / 60).round(1)
    ev["reopen_count"] = inc["incident_id"].map(tasks[tasks["status"] == "reopened"].groupby("incident_id").size()).fillna(0).astype(int)
    ev["assigned_office_id"] = inc["incident_id"].map(first_task["assigned_office_id"])
    ev["officer"] = ev["assigned_office_id"].map(office["designation"] + ", " + office["office_name"] + " (" + office["officer_name"] + ")")
    ev["channel"] = inc["report_source"]
    ev["source_reliability"] = 1.0
    ev = sev.apply(ev, sev.pwd)

    # tasks raised from a grievance with no PWD incident: keep them as events so no work is lost
    orphan = tasks[tasks["incident_id"].isna()].copy().reset_index(drop=True)
    oe = pd.DataFrame()
    if len(orphan):
        cats = []
        for t in orphan["task_title"]:
            hit = next(((c, s) for rx, c, s in TASK_CATEGORY if rx.search(t)), ("OTHER", "task"))
            cats.append(hit)
        oe["source_record_id"] = orphan["task_id"]
        oe["event_id"] = orphan["task_id"]
        oe["source"] = "pwd"
        oe["snapshot_sha256"] = snap
        oe["is_synthetic"] = 1
        oe["is_overlay"] = 0
        oe["occurred_at"] = orphan["assigned_at"]
        oe["reported_at"] = orphan["assigned_at"]
        oe["closed_at"] = orphan["verified_at"]
        oe["time_precision"] = "exact"
        oe["title"] = orphan["task_title"]
        oe["text"] = orphan["task_title"]
        oe["lang"] = "en"
        oe["junk_flag"] = 0
        oe["category_src"] = [s for _, s in cats]
        oe["category_code"] = [c for c, _ in cats]
        oe["category_method"] = "keyword:task_title"
        oe["category_conf"] = 0.8
        wing_o = orphan["assigned_office_id"].map(office["wing"])
        oe["lead_dept"] = np.where(wing_o == "Buildings", "PWD-BLD", "PWD-WRD")
        oe["is_actionable"] = 1
        oe["lat"], oe["lon"] = orphan["latitude"], orphan["longitude"]
        oe["loc_precision_m"] = 50.0
        oe["place_text"] = orphan["locality"]
        oe["taluk_src"] = orphan["taluk_code"]
        oe["taluk_code"] = orphan["taluk_code"].map(ref.taluk_by_pwd)
        oe["geo_method"], oe["geo_level"], oe["geo_conf"] = "source_point", "point", 1.0
        oe["status_src"] = orphan["status"]
        oe["status_std"] = [ref.std_status("pwd_task", s) for s in orphan["status"]]
        oe["status_at"] = orphan["verified_at"].fillna(orphan["assigned_at"])
        oe["first_action_at"] = orphan["assigned_at"]
        oe["response_applicable"] = 1
        oe["assigned_office_id"] = orphan["assigned_office_id"]
        oe["channel"] = "citizen_grievance"
        oe["source_reliability"] = 1.0
        oe["ext_ref"] = orphan["grievance_id"]
        oe["ext_ref_status"] = "external_system_unresolved"
        oe = sev.apply(oe, sev.pwd)
    events = pd.concat([conform(ev, EVENT_COLUMNS), conform(oe, EVENT_COLUMNS)], ignore_index=True)

    # --------------------------------------------------------------- actions --
    t_event = tasks["incident_id"].fillna(tasks["task_id"])
    act = pd.DataFrame({
        "action_id": tasks["task_id"], "event_id": t_event, "dept_code": np.where(tasks["assigned_office_id"].map(office["wing"]) == "Buildings", "PWD-BLD", "PWD-WRD"),
        "office_id": tasks["assigned_office_id"],
        "owner": tasks["assigned_office_id"].map(office["designation"] + " (" + office["officer_name"] + ")"),
        "text": tasks["task_title"], "sop_step": None, "assigned_at": tasks["assigned_at"], "due_at": tasks["due_at"],
        "status": tasks["status"].map(ref.status["action_status"]), "created_by": "department", "origin": "pwd_task",
        "completed_at": tasks["completed_at"], "verified_at": tasks["verified_at"], "evidence": tasks["completion_photo_path"]})

    # -------------------------------------------------------------- timeline --
    rows = []
    for r in inc.itertuples(index=False):
        rows.append((r.incident_id, r.reported_at, "First report", "Open", r.report_source, f"Reported by {r.report_source.replace('_', ' ')}"))
        if pd.notna(r.resolved_at):
            rows.append((r.incident_id, r.resolved_at, "Resolved", "Resolved", "PWD", "Incident closed"))
    for r in tasks.assign(ev=t_event).itertuples(index=False):
        rows.append((r.ev, r.assigned_at, "Crew assigned", "Assigned", r.assigned_office_id, r.task_title))
        if pd.notna(r.accepted_at):
            rows.append((r.ev, r.accepted_at, "Work accepted", "Assigned", r.assigned_office_id, "Task accepted by field office"))
        if pd.notna(r.completed_at):
            rows.append((r.ev, r.completed_at, "Awaiting verification", "Awaiting verification", r.assigned_office_id, str(r.completion_remarks)))
        if pd.notna(r.verified_at):
            rows.append((r.ev, r.verified_at, "Verified", "Resolved", "Verifier", str(r.verification_remarks)))
    tl = pd.DataFrame(rows, columns=["event_id", "at", "step", "status_std", "actor", "note"])
    tl["source"] = "pwd"

    # ----------------------------------------------------------- observations --
    levels["observed_at"] = to_ist(levels["reading_date"])
    levels = levels.merge(assets[["asset_id", "asset_name", "latitude", "longitude", "taluk_code", "capacity_mcft"]], on="asset_id", how="left")
    obs = []
    for metric, col, unit in (("lake_storage_mcft", "storage_mcft", "mcft"), ("lake_outflow_cusec", "outflow_cusecs", "cusec")):
        o = pd.DataFrame({"metric": metric, "value": levels[col], "unit": unit, "place_type": "facility",
                          "place_id": levels["asset_id"], "place_name": levels["asset_name"], "lat": levels["latitude"],
                          "lon": levels["longitude"], "taluk_code": levels["taluk_code"].map(ref.taluk_by_pwd),
                          "observed_at": levels["observed_at"], "period": "day", "source": "pwd", "quality": "ok", "is_synthetic": 1})
        obs.append(o)
    pct = pd.DataFrame({"metric": "lake_pct_full", "value": (levels["storage_mcft"] / levels["capacity_mcft"] * 100).round(1), "unit": "%",
                        "place_type": "facility", "place_id": levels["asset_id"], "place_name": levels["asset_name"],
                        "lat": levels["latitude"], "lon": levels["longitude"], "taluk_code": levels["taluk_code"].map(ref.taluk_by_pwd),
                        "observed_at": levels["observed_at"], "period": "day", "source": "pwd", "quality": "derived", "is_synthetic": 1})
    obs.append(pct)
    observations = pd.concat([conform(o, OBS_COLUMNS) for o in obs], ignore_index=True)

    # ------------------------------------------------------------ facilities --
    fac = pd.DataFrame({"facility_id": assets["asset_id"], "type": assets["asset_type"], "name": assets["asset_name"],
                        "lat": assets["latitude"], "lon": assets["longitude"], "taluk_code": assets["taluk_code"].map(ref.taluk_by_pwd),
                        "capacity": assets["capacity_mcft"], "precision_m": 100, "source": "pwd",
                        "relevance": np.where(assets["taluk_code"].isna(), "upstream", "inside")})
    down = ref.facilities_cfg.get("reservoir_downstream_taluks", {})
    fac["downstream_taluks"] = fac["name"].map(lambda n: "|".join(down.get(n, [])))

    # ---------------------------------------------------------------- works --
    works["start_date"] = pd.to_datetime(works["start_date"])
    works["target_date"] = pd.to_datetime(works["target_date"])
    works["completion_date"] = pd.to_datetime(works["completion_date"])
    w = works.merge(assets[["asset_id", "asset_name", "latitude", "longitude", "taluk_code"]], on="asset_id", how="left")
    w["spend_pct"] = (w["expenditure_lakh"] / w["sanctioned_amount_lakh"] * 100).round(1)
    w["overrun_pct"] = np.where(w["expenditure_lakh"] > w["sanctioned_amount_lakh"], (w["spend_pct"] - 100).round(1), 0.0)
    w["progress_gap_pct"] = (w["spend_pct"] - w["physical_progress_pct"]).round(1)
    w["taluk_code"] = w["taluk_code"].map(ref.taluk_by_pwd)
    w["office"] = w["office_id"].map(office["office_name"])

    # --------------------------------------------------------- announcements --
    ann["published_at"] = to_ist(ann["published_at"])
    ann_docs = pd.DataFrame({"doc_id": ann["announcement_id"], "title": ann["title"], "summary": ann["summary"], "body": ann["summary"],
                             "body_status": "full", "published_at": ann["published_at"], "publisher": "PWD Chennai",
                             "publisher_domain": "pwd", "publisher_tier": "official", "reliability": 1.0, "lang": "en",
                             "report_type": ann["category"], "places": ann["taluk_codes_affected"].map(
                                 lambda s: "|".join(ref.taluk_by_pwd.get(t, t) for t in str(s).split("|"))),
                             "source_kind": "pwd_announcement", "is_district": 1, "is_incident": 0, "sightings_count": 1})

    offices_ref = offices.assign(dept_code=np.where(offices["wing"] == "Buildings", "PWD-BLD", "PWD-WRD"),
                                 taluks=offices["taluk_codes_covered"].map(lambda s: "|".join(ref.taluk_by_pwd.get(t, t) for t in str(s).split("|"))))
    return {"events": events, "actions": conform(act, ACTION_COLUMNS), "timeline": conform(tl, TIMELINE_COLUMNS),
            "observations": observations, "facilities": fac, "works": w, "announcements": ann_docs, "offices": offices_ref}
