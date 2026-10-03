"""Put every event on the same keys: ward_no, zone_no, taluk_code, in_district.

Also derives two reference assets from the data itself:
  * ward -> taluk, by majority vote of police and PWD points that carry a taluk code
  * street / locality / area -> (ward, lat, lon), from grievances that have a map pin
    (GCC's street list has no coordinates of its own)
and measures text-only geo-resolution on a held-out split of pinned grievances.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .geo import WardIndex
from .util import log


def ward_taluk_vote(events: pd.DataFrame, wards: WardIndex, taluks: pd.DataFrame) -> dict[int, tuple[str, float]]:
    pts = events[events["source"].isin(["police", "pwd"]) & events["taluk_code"].notna() & events["lat"].notna()]
    w = wards.locate(pts["lat"].to_numpy(), pts["lon"].to_numpy())
    df = pd.DataFrame({"ward": w, "taluk": pts["taluk_code"].to_numpy()})
    df = df[df["ward"] > 0]
    out: dict[int, tuple[str, float]] = {}
    for ward, grp in df.groupby("ward"):
        vc = grp["taluk"].value_counts()
        out[int(ward)] = (vc.index[0], round(vc.iloc[0] / vc.sum(), 2))
    # wards with no coded points: nearest taluk centroid
    t = taluks[taluks["in_district"] == 1].dropna(subset=["lat"])
    for wd in wards.wards:
        if wd.ward_no not in out:
            dist = (t["lat"] - wd.centroid[0]) ** 2 + (t["lon"] - wd.centroid[1]) ** 2
            out[wd.ward_no] = (t.iloc[int(np.argmin(dist.to_numpy()))]["taluk_code"], 0.0)
    return out


def place_tables(g: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Street, locality and area centroids and majority wards from pinned grievances."""
    pinned = g[g["lat"].notna() & (g["_street"] != "")]
    out = {}
    for level, col in (("street", "_street"), ("locality", "_locality"), ("area", "_area")):
        x = pinned[pinned[col] != ""]
        agg = x.groupby([col, "ward_no"]).agg(n=("lat", "size"), lat=("lat", "median"), lon=("lon", "median")).reset_index()
        agg = agg.sort_values("n", ascending=False).drop_duplicates(col)
        tot = x.groupby(col).size()
        agg["share"] = (agg["n"] / agg[col].map(tot)).round(2)
        out[level] = agg.rename(columns={col: "name"})
    return out


def geo_holdout(g: pd.DataFrame, seed: int = 11) -> dict:
    """Hide the pin of 20% of pinned grievances; resolve from street/locality/area text; compare wards."""
    pinned = g[g["lat"].notna()].copy()
    rng = np.random.default_rng(seed)
    test = rng.random(len(pinned)) < 0.2
    tables = place_tables(pinned[~test])
    te = pinned[test]
    pred = pd.Series(np.nan, index=te.index)
    plat = pd.Series(np.nan, index=te.index)
    plon = pd.Series(np.nan, index=te.index)
    lvl = pd.Series("", index=te.index)
    for level, col in (("street", "_street"), ("locality", "_locality"), ("area", "_area")):
        m = pred.isna()
        t = tables[level].set_index("name")
        pred.loc[m] = te.loc[m, col].map(t["ward_no"])
        plat.loc[m] = te.loc[m, col].map(t["lat"])
        plon.loc[m] = te.loc[m, col].map(t["lon"])
        lvl.loc[m & pred.notna()] = level
    from .util import haversine_m
    err = pd.Series(haversine_m(plat, plon, te["lat"], te["lon"]), index=te.index)
    zone_of = g.drop_duplicates("ward_no").set_index("ward_no")["zone_no"]
    out = {"n_test": int(len(te)), "resolved_share": round(float(pred.notna().mean()), 3),
           "note": "Grievances always carry the citizen's ward; text only places them inside it. These figures are the harder case of text alone."}
    for level in ("street", "locality", "area"):
        m = lvl == level
        if m.sum():
            out[level] = {"share": round(float(m.mean()), 3), "ward_acc": round(float((pred[m] == te.loc[m, "ward_no"]).mean()), 3),
                          "zone_acc": round(float((pred[m].map(zone_of) == te.loc[m, "ward_no"].map(zone_of)).mean()), 3),
                          "median_error_m": int(err[m].median())}
    r = pred.notna()
    out["all_resolved"] = {"ward_acc": round(float((pred[r] == te.loc[r, "ward_no"]).mean()), 3),
                           "zone_acc": round(float((pred[r].map(zone_of) == te.loc[r, "ward_no"].map(zone_of)).mean()), 3),
                           "median_error_m": int(err[r].median())}
    return out


def resolve(events: pd.DataFrame, wards: WardIndex, ward_taluk: dict[int, tuple[str, float]], snap_m: float,
            tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    e = events
    zone_of = {w.ward_no: w.zone_no for w in wards.wards}
    cen = {w.ward_no: w.centroid for w in wards.wards}

    # 1. grievances without a pin: street -> locality -> area -> ward centroid
    g = (e["source"] == "grievance") & e["lat"].isna()
    if g.any():
        for level, col, prec in (("street", "_street", 300.0), ("locality", "_locality", 800.0), ("area", "_area", 1500.0)):
            m = g & e["lat"].isna()
            t = tables[level].set_index("name")
            hit = m & e[col].isin(t.index) & (e[col].map(t["ward_no"]) == e["ward_no"])
            e.loc[hit, "lat"] = e.loc[hit, col].map(t["lat"])
            e.loc[hit, "lon"] = e.loc[hit, col].map(t["lon"])
            e.loc[hit, "loc_precision_m"] = prec
            e.loc[hit, "geo_method"] = f"citizen_ward+{level}_centroid"
        m = g & e["lat"].isna() & e["ward_no"].notna()
        e.loc[m, "lat"] = e.loc[m, "ward_no"].map(lambda w: cen.get(int(w), (np.nan, np.nan))[0])
        e.loc[m, "lon"] = e.loc[m, "ward_no"].map(lambda w: cen.get(int(w), (np.nan, np.nan))[1])
        e.loc[m, "loc_precision_m"] = 1500.0
        e.loc[m, "geo_method"] = "citizen_ward+ward_centroid"

    # 2. point-in-polygon for every point; district-wide rows keep ward empty
    has = e["lat"].notna() & (e["geo_level"] != "district")
    ward, method, dist = wards.locate_with_snap(e.loc[has, "lat"].to_numpy(), e.loc[has, "lon"].to_numpy(), snap_m)
    pip = pd.Series(ward, index=e.index[has])
    meth = pd.Series(method, index=e.index[has])
    stated = e["ward_no"].copy()
    e.loc[has, "ward_no"] = np.where(e.loc[has, "source"] == "grievance", stated[has].fillna(pip), pip.replace(0, np.nan))
    mism = has & (e["source"] == "grievance") & e["geo_method"].eq("pin") & (pip.reindex(e.index) != stated)
    e.loc[mism, "geo_conf"] = 0.8
    e.loc[has & meth.reindex(e.index).eq("snapped"), "geo_method"] = e.loc[has & meth.reindex(e.index).eq("snapped"), "geo_method"].astype(str) + "+snapped"
    e.loc[has & meth.reindex(e.index).eq("snapped"), "geo_conf"] = 0.8
    outside = has & meth.reindex(e.index).eq("outside")
    e["in_district"] = 1
    e.loc[outside, "in_district"] = 0
    e.loc[outside & (e["source"] != "grievance"), "ward_no"] = np.nan

    # 3. zone and taluk
    e["zone_no"] = e["ward_no"].map(lambda w: zone_of.get(int(w)) if pd.notna(w) and int(w) in zone_of else np.nan)
    # The ward decides the revenue taluk, so the map, the taluk filter and every list agree.
    # A source's own taluk label (police station or PWD division) stays in taluk_src.
    has_w = e["ward_no"].notna()
    e.loc[has_w, "taluk_code"] = e.loc[has_w, "ward_no"].map(lambda w: ward_taluk.get(int(w), (None, 0))[0])
    e["ward_no"] = e["ward_no"].astype("Int64")
    e["zone_no"] = e["zone_no"].astype("Int64")
    log.info("geo: %d events located to a ward, %d snapped, %d outside the district, %d district-wide",
             int(e["ward_no"].notna().sum()), int(meth.eq("snapped").sum()), int(outside.sum()), int((e["geo_level"] == "district").sum()))
    return e
