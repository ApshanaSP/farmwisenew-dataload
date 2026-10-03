"""Aggregates, baselines, anomalies, hotspots, ward statistics and KPIs.

* Expected daily count per (category x zone): rolling 28-day mean with a district-wide
  weekday factor; anomaly when P(X >= x | expected) < 0.01 and x >= 3 (Poisson).
* Hotspots: DBSCAN with haversine distance per category; Getis-Ord Gi* on ward counts.
* Metric series: EWMA (span 7) and z-score of the latest value against 28 days.
Everything here is deterministic SQL-style arithmetic; no model writes a number.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm, poisson
from sklearn.cluster import DBSCAN

from .geo import WardIndex
from .refdata import Reference
from .util import IST, log

PERIODS = {"daily": 1, "weekly": 7, "monthly": 30, "quarterly": 90}


def daily_counts(events: pd.DataFrame) -> pd.DataFrame:
    e = events[(events["junk_flag"] != 1) & events["source"].isin(["grievance", "police", "pwd", "hospital", "news"])].copy()
    e["date"] = e["reported_at"].dt.tz_convert(IST).dt.date.astype(str)
    return e.groupby(["date", "zone_no", "category_code", "source"], dropna=False).size().rename("count").reset_index()


def anomalies(inc: pd.DataFrame, as_of: pd.Timestamp, days_back: int, p_thr: float, min_count: int, window: int) -> pd.DataFrame:
    """Incident counts per (category, zone, day) against the rolling baseline."""
    x = inc[inc["zone_no"].notna()].copy()
    x["date"] = x["first_reported_at"].dt.tz_convert(IST).dt.normalize()
    cnt = x.groupby(["category_code", "zone_no", "date"]).size().rename("n")
    days = pd.date_range(x["date"].min(), as_of.tz_convert(IST).normalize(), freq="D")
    wk = x.groupby(x["date"].dt.dayofweek).size()
    wk = (wk / wk.mean()).reindex(range(7)).fillna(1.0)
    out = []
    for (cat, zone), s in cnt.groupby(level=[0, 1]):
        s = s.droplevel([0, 1]).reindex(days, fill_value=0)
        base = s.shift(1).rolling(window, min_periods=14).mean()
        exp = base * s.index.dayofweek.map(wk).to_numpy()
        recent = s.index >= as_of.tz_convert(IST).normalize() - pd.Timedelta(days=days_back - 1)
        for d in s.index[recent]:
            n = int(s[d])
            lam = float(exp[d]) if pd.notna(exp[d]) else np.nan
            if np.isnan(lam) or n < min_count:
                continue
            p = float(poisson.sf(n - 1, max(lam, 0.05)))
            if p < p_thr:
                out.append({"date": str(d.date()), "category_code": cat, "zone_no": int(zone), "observed": n,
                            "expected": round(lam, 2), "p_value": float(f"{p:.2e}"), "ratio": round(n / max(lam, 0.05), 1)})
    a = pd.DataFrame(out)
    log.info("anomalies: %d (category x zone x day) spikes in the last %d days", len(a), days_back)
    return a


def hotspots(inc: pd.DataFrame, as_of: pd.Timestamp, eps_m: float, min_samples: int, days: int = 90) -> tuple[pd.DataFrame, pd.Series]:
    x = inc[(inc["first_reported_at"] >= as_of - pd.Timedelta(days=days)) & inc["lat"].notna() & (inc["has_official_record"] == 1)]
    rows, assign = [], pd.Series(index=inc["incident_id"], dtype=object)
    for cat, grp in x.groupby("category_code"):
        if len(grp) < min_samples:
            continue
        X = np.radians(grp[["lat", "lon"]].to_numpy(float))
        lab = DBSCAN(eps=eps_m / 6_371_000, min_samples=min_samples, metric="haversine", algorithm="ball_tree").fit_predict(X)
        for k in set(lab) - {-1}:
            m = grp[lab == k]
            hid = f"HOT-{cat}-{k:03d}"
            recent = m["first_reported_at"] >= as_of - pd.Timedelta(days=30)
            rows.append({"hotspot_id": hid, "category_code": cat, "incidents": len(m), "incidents_30d": int(recent.sum()),
                         "open": int(m["is_open"].sum()), "lat": round(m["lat"].mean(), 6), "lon": round(m["lon"].mean(), 6),
                         "wards": "|".join(str(int(w)) for w in sorted(m["ward_no"].dropna().unique())),
                         "first_seen": m["first_reported_at"].min(), "last_seen": m["first_reported_at"].max(),
                         "top_place": m["place_text"].mode().iloc[0] if m["place_text"].notna().any() else None})
            assign.loc[m["incident_id"]] = hid
    h = pd.DataFrame(rows)
    log.info("hotspots: %d clusters across %d categories", len(h), h["category_code"].nunique() if len(h) else 0)
    return h, assign


def gi_star(values: pd.Series, adj: dict[int, set[int]]) -> pd.Series:
    """Getis-Ord Gi* z-score per ward with binary contiguity (self included)."""
    wards = values.index.tolist()
    x = values.to_numpy(float)
    n = len(x)
    xbar, s = x.mean(), np.sqrt((x ** 2).mean() - x.mean() ** 2)
    pos = {w: i for i, w in enumerate(wards)}
    z = np.zeros(n)
    for i, w in enumerate(wards):
        nb = [pos[j] for j in adj.get(w, set()) if j in pos] + [i]
        wsum = len(nb)
        num = x[nb].sum() - xbar * wsum
        den = s * np.sqrt((n * wsum - wsum ** 2) / (n - 1)) if s > 0 else 1
        z[i] = num / den if den else 0
    return pd.Series(z.round(2), index=wards)


def ward_stats(wards: WardIndex, events: pd.DataFrame, inc: pd.DataFrame, ward_taluk: dict, adj: dict, as_of: pd.Timestamp) -> pd.DataFrame:
    rows = []
    fl = events[(events["category_family"] == "FLOOD") & events["ward_no"].notna()].groupby("ward_no").size()
    inc30 = inc[(inc["first_reported_at"] >= as_of - pd.Timedelta(days=30)) & inc["ward_no"].notna()]
    c30 = inc30.groupby("ward_no").size()
    opn = inc[(inc["is_open"] == 1) & inc["ward_no"].notna()].groupby("ward_no").size()
    brc = inc[(inc["is_open"] == 1) & (inc["sla_breached"] == 1) & inc["ward_no"].notna()].groupby("ward_no").size()
    for w in wards.wards:
        rows.append({"ward_no": w.ward_no, "zone_no": w.zone_no, "zone_name": w.zone_name,
                     "taluk_code": ward_taluk.get(w.ward_no, (None, 0))[0], "taluk_vote_share": ward_taluk.get(w.ward_no, (None, 0))[1],
                     "area_km2": round(w.area_km2, 3), "centroid_lat": round(w.centroid[0], 6), "centroid_lon": round(w.centroid[1], 6),
                     "adjacent_wards": "|".join(str(a) for a in sorted(adj.get(w.ward_no, set()))),
                     "flood_reports": int(fl.get(w.ward_no, 0)), "incidents_30d": int(c30.get(w.ward_no, 0)),
                     "open_incidents": int(opn.get(w.ward_no, 0)), "open_past_deadline": int(brc.get(w.ward_no, 0))})
    ws = pd.DataFrame(rows).set_index("ward_no")
    dens = ws["flood_reports"] / ws["area_km2"].clip(lower=0.3)
    ws["low_lying_index"] = (dens.rank(pct=True)).round(3)
    ws["gi_star_z_30d"] = gi_star(ws["incidents_30d"], adj)
    ws["hot_ward"] = (ws["gi_star_z_30d"] > norm.ppf(0.975)).astype(int)
    return ws.reset_index()


def observation_signals(obs: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Latest value per (metric, place) with EWMA, 28-day z-score and a flag."""
    o = obs[(obs["quality"] != "suspect") & obs["value"].notna() & (obs["observed_at"] <= as_of)].copy()
    rows = []
    for (m, pid), s in o.sort_values("observed_at").groupby(["metric", "place_id"]):
        v = s["value"].astype(float)
        last = s.iloc[-1]
        hist = v[s["observed_at"] >= last["observed_at"] - pd.Timedelta(days=28)]
        mu, sd = hist.iloc[:-1].mean() if len(hist) > 1 else np.nan, hist.iloc[:-1].std() if len(hist) > 2 else np.nan
        z = (v.iloc[-1] - mu) / sd if sd and sd > 0 else np.nan
        ew = v.ewm(span=7).mean().iloc[-1]
        slope = np.nan
        if len(v) >= 7:
            y = v.iloc[-7:].to_numpy()
            slope = float(np.polyfit(np.arange(len(y)), y, 1)[0])
        rows.append({"metric": m, "place_id": pid, "place_name": last["place_name"], "lat": last["lat"], "lon": last["lon"],
                     "taluk_code": last["taluk_code"], "observed_at": last["observed_at"], "value": float(v.iloc[-1]),
                     "unit": last["unit"], "ewma7": round(float(ew), 2), "mean28": round(float(mu), 2) if pd.notna(mu) else None,
                     "zscore": round(float(z), 2) if pd.notna(z) else None, "slope_per_day": round(slope, 3) if pd.notna(slope) else None,
                     "n_points": int(len(v)), "detail": last["detail"], "source": last["source"],
                     "anomaly": int(pd.notna(z) and abs(z) >= 3 and len(hist) >= 10)})
    sig = pd.DataFrame(rows)
    if len(sig):
        lake = sig["metric"] == "lake_pct_full"
        sig["days_to_full"] = np.where(lake & (sig["slope_per_day"] > 0.05), ((100 - sig["value"]) / sig["slope_per_day"]).round(0), np.nan)
    return sig


def kpis(inc: pd.DataFrame, events: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """KPI tiles per period and zone (plus district), with the previous period for deltas."""
    rows = []
    g = events[events["source"] == "grievance"]
    for pname, days in PERIODS.items():
        for offset in (0, 1):
            end = as_of - pd.Timedelta(days=days * offset)
            start = end - pd.Timedelta(days=days)
            win = inc[(inc["first_reported_at"] > start) & (inc["first_reported_at"] <= end)]
            closed = inc[(inc["closed_at"] > start) & (inc["closed_at"] <= end)]
            gw = g[(g["reported_at"] > start) & (g["reported_at"] <= end)]
            for zone, part in [(None, None)] + [(z, None) for z in sorted(inc["zone_no"].dropna().unique())]:
                w = win if zone is None else win[win["zone_no"] == zone]
                c = closed if zone is None else closed[closed["zone_no"] == zone]
                gg = gw if zone is None else gw[gw["zone_no"] == zone]
                rows.append({"period": pname, "offset": offset, "zone_no": zone, "window_start": start, "window_end": end,
                             "incidents": len(w), "severe_incidents": int((w["severity_level"] == "Severe").sum()),
                             "high_incidents": int((w["severity_level"] == "High").sum()),
                             "open_incidents": int(w["is_open"].sum()), "resolved": len(c),
                             "complaints_filed": len(gg), "complaints_open": int((~gg["status_std"].isin(["Resolved", "Rejected", "Lapsed"])).sum()),
                             "sla_breached_open": int(((w["is_open"] == 1) & (w["sla_breached"] == 1)).sum()),
                             "median_hours_to_first_action": (lambda h: round(float(h.median()), 1) if h.notna().any() else None)(
                                 w.loc[w["sla_basis"] == "resolution", "hours_to_first_action"]),
                             "multi_source_incidents": int((w["source_count"] > 1).sum()), "media_only": int(w["media_only"].sum())})
    return pd.DataFrame(rows)
