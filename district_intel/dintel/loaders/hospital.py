"""Hospital daily data -> observations; consecutive alert days -> one alert event per episode."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ..refdata import Reference, severity_level
from ..schema import EVENT_COLUMNS, OBS_COLUMNS, TIMELINE_COLUMNS, conform
from ..util import Settings, sha256_file, to_ist

MED = {"Available": 2, "Limited": 1, "Critical": 0}
LEVEL = {"Normal": 0, "Watch": 1, "Warning": 2, "Critical": 3}


def slug(s: str) -> str:
    return "HSP-" + re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def load(settings: Settings, ref: Reference) -> dict[str, pd.DataFrame]:
    path = settings.src("hospital", "csv")
    h = pd.read_csv(path, encoding="utf-8-sig")
    snap = sha256_file(path)
    h["observed_at"] = to_ist(h["date"])
    h["hid"] = h["hospital_name"].map(slug)
    h["taluk_code"] = h["taluk"].str.lower().map(ref.taluk_by_name)

    specs = [("bed_occupancy_pct", "bed_occupancy_rate", "%"), ("occupied_beds", "occupied_beds", "beds"),
             ("total_beds", "total_beds", "beds"), ("opd_count", "outpatient_count", "patients"),
             ("emergency_cases", "emergency_cases", "cases"), ("vaccination_pct", "vaccination_coverage", "%"),
             ("doctors_on_roll", "doctor_count", "staff"), ("nurses_on_roll", "nurse_count", "staff")]
    obs = []
    base = dict(place_type="facility", period="day", source="hospital", quality="ok", is_synthetic=1)
    for metric, col, unit in specs:
        obs.append(pd.DataFrame({"metric": metric, "value": h[col], "unit": unit, "place_id": h["hid"], "place_name": h["hospital_name"],
                                 "lat": h["latitude"], "lon": h["longitude"], "taluk_code": h["taluk_code"], "observed_at": h["observed_at"], **base}))
    obs.append(pd.DataFrame({"metric": "disease_cases", "value": h["disease_cases"], "unit": "cases", "place_id": h["hid"],
                             "place_name": h["hospital_name"], "lat": h["latitude"], "lon": h["longitude"], "taluk_code": h["taluk_code"],
                             "observed_at": h["observed_at"], "detail": h["disease_type"], **base}))
    obs.append(pd.DataFrame({"metric": "medicine_status", "value": h["medicine_availability"].map(MED), "unit": "0=critical,2=available",
                             "place_id": h["hid"], "place_name": h["hospital_name"], "lat": h["latitude"], "lon": h["longitude"],
                             "taluk_code": h["taluk_code"], "observed_at": h["observed_at"], "detail": h["medicine_availability"], **base}))
    obs.append(pd.DataFrame({"metric": "ambulance_available", "value": (h["ambulance_available"] == "Yes").astype(int), "unit": "flag",
                             "place_id": h["hid"], "place_name": h["hospital_name"], "lat": h["latitude"], "lon": h["longitude"],
                             "taluk_code": h["taluk_code"], "observed_at": h["observed_at"], **base}))
    obs.append(pd.DataFrame({"metric": "health_alert_level", "value": h["alert_level"].map(LEVEL), "unit": "0-3",
                             "place_id": h["hid"], "place_name": h["hospital_name"], "lat": h["latitude"], "lon": h["longitude"],
                             "taluk_code": h["taluk_code"], "observed_at": h["observed_at"], "detail": h["health_alert"], **base}))
    observations = pd.concat([conform(o, OBS_COLUMNS) for o in obs], ignore_index=True)

    # ---- alert episodes: consecutive Warning/Critical days of one hospital with the same alert text
    a = h[h["alert_level"].isin(["Warning", "Critical"])].sort_values(["hid", "observed_at"]).copy()
    last_day = h["observed_at"].max()
    rows, tl = [], []
    if len(a):
        a["gap"] = a.groupby("hid")["observed_at"].diff().dt.days.fillna(99)
        a["new"] = (a["gap"] > 1) | (a["health_alert"] != a.groupby("hid")["health_alert"].shift())
        a["ep"] = a["new"].cumsum()
        for ep, grp in a.groupby("ep"):
            f, l = grp.iloc[0], grp.iloc[-1]
            worst = "Critical" if (grp["alert_level"] == "Critical").any() else "Warning"
            score = 75.0 if worst == "Critical" else 55.0
            ongoing = l["observed_at"] >= last_day
            eid = f"{f['hid']}-{f['observed_at']:%Y%m%d}"
            code = ref.hospital_category(str(f["health_alert"]))
            rows.append({
                "event_id": eid, "source": "hospital", "source_record_id": eid, "snapshot_sha256": snap, "is_synthetic": 1, "is_overlay": 0,
                "occurred_at": f["observed_at"], "reported_at": f["observed_at"],
                "closed_at": None if ongoing else l["observed_at"] + pd.Timedelta(days=1), "time_precision": "day",
                "title": f"{f['health_alert']} at {f['hospital_name']}",
                "text": f"{f['hospital_name']}: {f['health_alert']} for {len(grp)} day(s). Bed occupancy up to {grp['bed_occupancy_rate'].max():.0f}%, "
                        f"emergency cases up to {grp['emergency_cases'].max()}, medicine {l['medicine_availability'].lower()}, ambulance {'available' if l['ambulance_available']=='Yes' else 'unavailable'}.",
                "lang": "en", "junk_flag": 0, "category_src": f["health_alert"], "category_code": code, "category_method": "crosswalk:hospital_alert",
                "category_conf": 1.0, "dept_src": "Health Services", "lead_dept": "HLT-DMS", "is_actionable": 1,
                "lat": f["latitude"], "lon": f["longitude"], "loc_precision_m": 50.0, "place_text": f["hospital_name"],
                "taluk_src": f["taluk"], "taluk_code": f["taluk_code"], "geo_method": "facility", "geo_level": "point", "geo_conf": 1.0,
                "persons_affected": int(grp["emergency_cases"].max()), "severity_score": score, "severity_level": severity_level(score),
                "severity_reasons": f"Hospital alert level {worst}", "status_src": "active" if ongoing else "cleared",
                "status_std": "Open" if ongoing else "Resolved", "status_at": l["observed_at"], "response_applicable": 0,
                "channel": "hospital_mis", "source_reliability": 1.0, "dup_group_id": eid})
            tl.append((eid, f["observed_at"], "First report", "Open", "Hospital MIS", f"{f['health_alert']} ({worst})"))
            if not ongoing:
                tl.append((eid, l["observed_at"] + pd.Timedelta(days=1), "Resolved", "Resolved", "Hospital MIS", "Alert cleared"))
    events = conform(pd.DataFrame(rows), EVENT_COLUMNS)
    timeline = conform(pd.DataFrame(tl, columns=["event_id", "at", "step", "status_std", "actor", "note"]).assign(source="hospital"), TIMELINE_COLUMNS)

    fac = h.drop_duplicates("hid")[["hid", "hospital_name", "latitude", "longitude", "taluk_code", "total_beds", "specialties", "address"]]
    fac = fac.rename(columns={"hid": "facility_id", "hospital_name": "name", "latitude": "lat", "longitude": "lon", "total_beds": "capacity"})
    fac["type"] = "hospital"
    fac["precision_m"] = 50
    fac["source"] = "hospital"
    return {"events": events, "observations": observations, "timeline": timeline, "facilities": fac}
