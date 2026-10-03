"""Real collectors (IMD, CPCB, CFM-DSS) -> observations, forecasts, warning events, bulletins.

Values are never changed. Readings that fail plausibility checks keep their value
and get quality = "suspect" so they cannot raise alerts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..refdata import Reference, severity_level
from ..schema import EVENT_COLUMNS, OBS_COLUMNS, conform
from ..util import Settings, mixed_to_ist, sha256_file

WARN_LEVEL = {"Green": 0, "Yellow": 1, "Orange": 2, "Red": 3}
WARN_SCORE = {1: 35.0, 2: 60.0, 3: 80.0}
DISTRICT_CENTROID = (13.0827, 80.2707)


def aqi_band(v: float) -> str:
    if pd.isna(v):
        return ""
    for lim, name in ((50, "Good"), (100, "Satisfactory"), (200, "Moderate"), (300, "Poor"), (400, "Very Poor")):
        if v <= lim:
            return name
    return "Severe"


def _read(p) -> pd.DataFrame:
    return pd.read_csv(p, encoding="utf-8-sig") if p.exists() else pd.DataFrame()


def valid_reading(v: pd.Series, col: str) -> pd.Series:
    """IMD fills a missing reading with 999 / 9999 (or -99). Chennai's heaviest recorded day is about
    500 mm, so a value that large or negative is "no reading", not rain; dropping it keeps one station
    from turning the district average into very heavy rain."""
    v = pd.to_numeric(v, errors="coerce")
    return v.notna() & (v >= -50) & (v < (900 if col == "rainfall_mm" else 500))


def load(settings: Settings, ref: Reference) -> dict[str, pd.DataFrame]:
    obs, events, docs, forecasts = [], [], [], pd.DataFrame()
    fac_rows = []

    # ------------------------------------------------------------------- IMD --
    d = settings.src("imd", "dir")
    o = _read(d / "imd_weather_observations_chennai.csv")
    if len(o):
        o["observed_at"] = mixed_to_ist(o["observed_at"])
        for metric, col, unit in (("rainfall_24h_mm", "rainfall_mm", "mm"), ("temp_max_c", "temp_max_c", "°C"),
                                  ("temp_min_c", "temp_min_c", "°C"), ("temp_departure_c", "temp_departure_c", "°C"),
                                  ("humidity_pct", "humidity_pct", "%")):
            s = o[valid_reading(o[col], col)]
            if len(s):
                obs.append(pd.DataFrame({"metric": metric, "value": s[col], "unit": unit, "place_type": "facility",
                                         "place_id": "IMD-" + s["station_id"].astype(str), "place_name": s["station_name"],
                                         "lat": s["latitude"], "lon": s["longitude"], "observed_at": s["observed_at"],
                                         "period": "24h" if metric == "rainfall_24h_mm" else "instant", "source": "imd",
                                         "quality": "ok", "is_synthetic": 0}))
        for st in o.drop_duplicates("station_id").itertuples():
            fac_rows.append({"facility_id": f"IMD-{st.station_id}", "type": "weather_station", "name": st.station_name,
                             "lat": st.latitude, "lon": st.longitude, "precision_m": 100, "source": "imd"})
    f = _read(d / "imd_weather_forecasts_chennai.csv")
    if len(f):
        f["issued_at"] = mixed_to_ist(f["issued_at"])
        f["valid_from"] = mixed_to_ist(f["valid_from"])
        f = f.sort_values("issued_at").drop_duplicates(["location_id", "valid_from"], keep="last")
        forecasts = f[["location_id", "issued_at", "valid_from", "forecast_text", "source_url"]].copy()
    w = _read(d / "imd_weather_warnings_chennai.csv")
    if len(w):
        w["issued_at"] = mixed_to_ist(w["issued_at"])
        w["valid_from"] = mixed_to_ist(w["valid_from"])
        w["level"] = w["warning_colour"].map(WARN_LEVEL).fillna(0).astype(int)
        w = w.sort_values("issued_at").drop_duplicates(["valid_from"], keep="last")
        obs.append(pd.DataFrame({"metric": "imd_warning_level", "value": w["level"], "unit": "0=green..3=red", "place_type": "district",
                                 "place_id": "CHENNAI", "place_name": "Chennai district", "lat": DISTRICT_CENTROID[0],
                                 "lon": DISTRICT_CENTROID[1], "observed_at": w["valid_from"], "period": "day", "source": "imd",
                                 "quality": "ok", "is_synthetic": 0, "detail": w["warning_text"]}))
        for r in w[w["level"] > 0].itertuples():
            score = WARN_SCORE[r.level]
            eid = f"IMDW-{r.valid_from:%Y%m%d%H%M}"
            events.append({"event_id": eid, "source": "imd", "source_record_id": r.warning_id, "deep_link": r.source_url,
                           "is_synthetic": 0, "is_overlay": 0, "occurred_at": r.valid_from, "reported_at": r.issued_at,
                           "time_precision": "day", "title": f"IMD {r.warning_colour} warning for Chennai",
                           "text": r.warning_text, "lang": "en", "junk_flag": 0, "category_src": f"imd_{r.warning_colour.lower()}",
                           "category_code": "FLOOD_RISK_SIGNAL", "category_method": "crosswalk", "category_conf": 1.0,
                           "dept_src": "IMD", "lead_dept": "DIST-DM", "is_actionable": 1, "lat": DISTRICT_CENTROID[0],
                           "lon": DISTRICT_CENTROID[1], "loc_precision_m": 15000.0, "place_text": "Chennai district",
                           "geo_level": "district", "geo_method": "district_wide", "geo_conf": 1.0, "weather_related": 1,
                           "severity_score": score, "severity_level": severity_level(score),
                           "severity_reasons": f"IMD {r.warning_colour} warning (+{score:.0f})", "status_src": "issued",
                           "status_std": "Open", "status_at": r.issued_at, "response_applicable": 0, "channel": "official_notice",
                           "source_reliability": 1.0, "dup_group_id": eid})

    # ------------------------------------------------------------------ CPCB --
    d = settings.src("cpcb", "dir")
    a = _read(d / "cpcb_station_aqi_chennai.csv")
    cov = _read(d / "cpcb_station_coverage.csv")
    coords = ref.facilities_cfg.get("cpcb", {})
    names = dict(zip(cov.get("station_id", []), cov.get("station_name", [])))
    for sid, c in coords.items():
        fac_rows.append({"facility_id": sid, "type": "air_quality_station", "name": names.get(sid, sid), "lat": c["lat"], "lon": c["lon"],
                         "precision_m": c.get("precision_m"), "source": "cpcb"})
    if len(a):
        a["observed_at"] = mixed_to_ist(a["observation_datetime"])
        a["lat"] = a["station_id"].map(lambda s: coords.get(s, {}).get("lat"))
        a["lon"] = a["station_id"].map(lambda s: coords.get(s, {}).get("lon"))
        obs.append(pd.DataFrame({"metric": "aqi", "value": a["aqi"], "unit": "AQI (India)", "place_type": "facility",
                                 "place_id": a["station_id"], "place_name": a["station_name"], "lat": a["lat"], "lon": a["lon"],
                                 "observed_at": a["observed_at"], "period": a["averaging_period"], "source": "cpcb", "quality": "ok",
                                 "is_synthetic": 0, "detail": a["aqi"].map(aqi_band)}))
        for r in a[a["aqi"] > 200].itertuples():
            score = 50.0 if r.aqi <= 300 else 65.0
            eid = f"AQI-{r.station_id}-{r.observed_at:%Y%m%d%H}"
            events.append({"event_id": eid, "source": "cpcb", "source_record_id": eid, "is_synthetic": 0, "is_overlay": 0,
                           "occurred_at": r.observed_at, "reported_at": r.observed_at, "time_precision": "hour",
                           "title": f"AQI {r.aqi} ({aqi_band(r.aqi)}) at {r.station_name}", "text": f"AQI {r.aqi}",
                           "category_src": "aqi", "category_code": "AIR_POLLUTION", "category_method": "threshold", "category_conf": 1.0,
                           "lead_dept": "TNPCB", "is_actionable": 1, "lat": r.lat, "lon": r.lon, "loc_precision_m": 1000.0,
                           "place_text": r.station_name, "geo_level": "point", "geo_method": "facility", "geo_conf": 0.8,
                           "severity_score": score, "severity_level": severity_level(score), "severity_reasons": f"AQI {r.aqi}",
                           "status_std": "Open", "channel": "sensor", "source_reliability": 1.0, "dup_group_id": eid})

    # ------------------------------------------------------------------- CFM --
    d = settings.src("cfm", "dir")
    cc = ref.facilities_cfg.get("cfm", {})
    cov = _read(d / "cfm_dss_station_coverage.csv")
    for r in cov.itertuples():
        c = cc.get(r.station_id, {})
        down = ref.facilities_cfg.get("basin_downstream_taluks", {}).get(r.basin if isinstance(r.basin, str) else "", [])
        fac_rows.append({"facility_id": r.station_id, "type": r.station_type, "name": r.station_name, "lat": c.get("lat"),
                         "lon": c.get("lon"), "precision_m": c.get("precision_m"), "source": "cfm", "relevance": r.relevance,
                         "basin": r.basin, "same_as": c.get("same_as"), "downstream_taluks": "|".join(down)})
    g = _read(d / "cfm_dss_gate_operations_chennai.csv")
    if len(g):
        g["observed_at"] = mixed_to_ist(g["observed_at"])
        obs.append(pd.DataFrame({"metric": "reservoir_inflow_cusec", "value": g["inflow"], "unit": "cusec", "place_type": "facility",
                                 "place_id": g["gate_site_id"], "place_name": g["gate_site_name"],
                                 "lat": g["gate_site_id"].map(lambda s: cc.get(s, {}).get("lat")),
                                 "lon": g["gate_site_id"].map(lambda s: cc.get(s, {}).get("lon")),
                                 "observed_at": g["observed_at"], "period": "day", "source": "cfm", "quality": "ok", "is_synthetic": 0}))
    gauges = pd.concat([_read(d / "cfm_dss_gauge_observations_chennai.csv").assign(archived=0),
                        _read(d / "cfm_dss_gauge_observations_chennai_archive.csv").assign(archived=1)], ignore_index=True)
    if len(gauges):
        gauges["observed_at"] = mixed_to_ist(gauges["observed_at"])
        inverted = gauges["danger_stage"] < gauges["warning_stage"]
        datum = gauges["water_level"] > 5 * gauges[["warning_stage", "danger_stage"]].max(axis=1).clip(lower=0.5)
        q = np.where(inverted | datum, "suspect", "ok")
        why = np.where(inverted, "danger stage below warning stage", np.where(datum, "level far above stages (datum?)", ""))
        obs.append(pd.DataFrame({"metric": "gauge_level_m", "value": gauges["water_level"], "unit": "m", "place_type": "facility",
                                 "place_id": gauges["station_id"], "place_name": gauges["station_name"],
                                 "lat": gauges["station_id"].map(lambda s: cc.get(s, {}).get("lat")),
                                 "lon": gauges["station_id"].map(lambda s: cc.get(s, {}).get("lon")),
                                 "observed_at": gauges["observed_at"], "period": "instant", "source": "cfm", "quality": q,
                                 "is_synthetic": 0, "detail": [f"warning {w}, danger {x}; {y}".strip("; ") for w, x, y in
                                                               zip(gauges["warning_stage"], gauges["danger_stage"], why)]}))
    bul = pd.concat([_read(d / "cfm_dss_alerts_chennai.csv"), _read(d / "cfm_dss_alerts_chennai_archive.csv")], ignore_index=True)
    if len(bul):
        # the collector can re-fetch a bulletin with a corrected date; keep its latest fetch only
        bul = bul.sort_values("fetched_at").drop_duplicates("alert_id", keep="last")
        bul["published_at"] = mixed_to_ist(bul["issued_at"])
        docs.append(pd.DataFrame({"doc_id": "CFM-" + bul["alert_id"].astype(str), "title": bul["alert_text"], "summary": bul["alert_text"],
                                  "body": bul["alert_text"], "body_status": "title_only", "published_at": bul["published_at"],
                                  "publisher": "Chennai Flood Monitoring (WRD)", "publisher_domain": "chennaifloodmonitor.tn.gov.in",
                                  "publisher_tier": "official", "reliability": 1.0, "lang": "en", "report_type": bul["alert_type"],
                                  "canonical_url": bul["source_url"], "source_kind": "cfm_bulletin", "is_district": 1, "is_incident": 0,
                                  "sightings_count": 1}))

    observations = pd.concat([conform(x, OBS_COLUMNS) for x in obs], ignore_index=True) if obs else conform(pd.DataFrame(), OBS_COLUMNS)
    return {"observations": observations, "events": conform(pd.DataFrame(events), EVENT_COLUMNS),
            "documents": pd.concat(docs, ignore_index=True) if docs else pd.DataFrame(),
            "facilities": pd.DataFrame(fac_rows), "forecasts": forecasts}
