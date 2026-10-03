"""Police incident reports -> events + timeline; stations -> facilities."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import severity as sev
from .. import textproc as tp
from ..refdata import Reference
from ..schema import EVENT_COLUMNS, TIMELINE_COLUMNS, conform
from ..util import Settings, sha256_file, to_ist

CHANNEL = {"CONTROL_ROOM_112": "control_room_112", "FIR": "fir_walk_in", "PATROL": "patrol",
           "CITIZEN_GRIEVANCE": "citizen_grievance", "NEWS_REPORT": "media"}


def load(settings: Settings, ref: Reference) -> dict[str, pd.DataFrame]:
    path = settings.src("police", "reports")
    p = pd.read_csv(path, encoding="utf-8-sig")
    st = pd.read_csv(settings.src("police", "stations"), encoding="utf-8-sig")
    snap = sha256_file(path)
    ev = pd.DataFrame()
    ev["source_record_id"] = p["report_id"]
    ev["event_id"] = "POL-" + p["report_id"].str.replace("POL-", "", regex=False)
    ev["source"] = "police"
    ev["snapshot_sha256"] = snap
    ev["is_synthetic"] = 1
    ev["is_overlay"] = 0
    ev["occurred_at"] = to_ist(p["incident_datetime"])
    ev["reported_at"] = to_ist(p["reported_datetime"])
    ev["closed_at"] = to_ist(p["closed_datetime"])
    ev["time_precision"] = "exact"
    ev["title"] = p["title"]
    ev["text"] = p["description"]
    ev["lang"] = "en"
    ev["junk_flag"] = 0
    ev["simhash"] = [tp.simhash64(t) for t in p["description"]]
    ev["category_src"] = p["category"]
    ev["category_code"] = p["category"].map(ref.cmap["police"]).fillna("POLICE_OTHER")
    ev["category_method"] = "crosswalk"
    ev["category_conf"] = 1.0
    ev["dept_src"] = "Police"
    ev["lead_dept"] = "POL-GCP"
    ev["is_actionable"] = 1
    ev["lat"], ev["lon"] = p["latitude"], p["longitude"]
    ev["loc_precision_m"] = 50.0
    ev["place_text"] = p["locality"]
    ev["taluk_src"] = p["taluk_code"]
    ev["taluk_code"] = p["taluk_code"].map(ref.taluk_by_police)
    ev["geo_method"] = "source_point"
    ev["geo_level"] = "point"
    ev["geo_conf"] = 1.0
    ev["dead"] = p["fatalities"]
    ev["injured"] = p["injured_count"]
    ev["persons_affected"] = p["persons_affected"]
    ev["affected_imputed"] = 0
    ev["vulnerable_flags"] = np.where(p["vulnerable_victim"] == 1, "vulnerable_victim", "")
    ev["hazard_flag"] = p["weapon_involved"].fillna(0).astype(int)
    ev["access_blocked"] = p["road_blocked"]
    ev["blockage_minutes"] = p["blockage_minutes"]
    ev["crowd_estimate"] = p["crowd_estimate"]
    ev["weather_related"] = p["is_weather_related"]
    ev["claims_prior_complaint"] = 0
    ev["has_photo"] = 0
    ev["status_src"] = p["status"]
    ev["status_std"] = [ref.std_status("police", s) for s in p["status"]]
    ev["status_at"] = ev["closed_at"].fillna(ev["reported_at"])
    # Only 112 calls and patrols have a field response. FIRs are filed at the station, and reports that
    # reach police through a grievance or the news are logged, not attended, so no response time applies.
    ev["response_applicable"] = p["source"].isin(["CONTROL_ROOM_112", "PATROL"]).astype(int)
    ev["response_minutes"] = p["response_minutes"]
    ev["first_action_at"] = ev["reported_at"] + pd.to_timedelta(p["response_minutes"], unit="m")
    # registering the FIR, or logging a forwarded report, is the first action
    fir = ~p["source"].isin(["CONTROL_ROOM_112", "PATROL"])
    ev.loc[fir, "first_action_at"] = ev.loc[fir, "reported_at"]
    ev["rejection_reason_code"] = None
    ev["reopen_count"] = 0
    ev["assigned_office_id"] = p["station_code"]
    ev["officer"] = p["station_code"].map(dict(zip(st["station_code"], "SHO, " + st["station_name"])))
    ev["channel"] = p["source"].map(CHANNEL)
    ev["source_reliability"] = 1.0
    ev["ext_ref"] = p["linked_grievance_code"]
    ev["ext_ref_status"] = np.where(p["linked_grievance_code"].notna(), "external_system_unresolved", None)
    tmp = ev.assign(_vulnerable_victim=p["vulnerable_victim"], _weapon=p["weapon_involved"])
    tmp = sev.apply(tmp, sev.police)
    ev["severity_score"], ev["severity_level"], ev["severity_reasons"] = tmp["severity_score"], tmp["severity_level"], tmp["severity_reasons"]

    rows = []
    for r in ev[["event_id", "reported_at", "first_action_at", "closed_at", "status_std", "channel", "officer", "response_applicable"]].itertuples(index=False):
        rows.append((r.event_id, r.reported_at, "First report", "Open", r.channel, f"Reported via {r.channel.replace('_', ' ')}"))
        if pd.notna(r.first_action_at) and r.response_applicable:
            rows.append((r.event_id, r.first_action_at, "Crew assigned", "In progress", "Police", f"Patrol reached the spot ({r.officer})"))
        elif not r.response_applicable:
            what = "FIR registered" if r.channel == "fir_walk_in" else "Report logged"
            rows.append((r.event_id, r.reported_at, "Officer verified", "In progress", "Police", f"{what} ({r.officer})"))
        if pd.notna(r.closed_at):
            rows.append((r.event_id, r.closed_at, "Resolved", "Resolved", "Police", "Case closed"))
    tl = pd.DataFrame(rows, columns=["event_id", "at", "step", "status_std", "actor", "note"])
    tl["source"] = "police"

    fac = pd.DataFrame({"facility_id": st["station_code"], "type": "police_station", "name": st["station_name"],
                        "lat": st["latitude"], "lon": st["longitude"], "taluk_code": st["taluk_code"].map(ref.taluk_by_police),
                        "precision_m": 30, "source": "police"})
    return {"events": conform(ev, EVENT_COLUMNS), "timeline": conform(tl, TIMELINE_COLUMNS), "facilities": fac}
