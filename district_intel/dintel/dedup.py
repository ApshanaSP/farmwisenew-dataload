"""Within-source duplicate groups (union-find), scored against the repo's ground truth.

Blocking keeps it linear in practice: rows are compared only inside the same
category key and a sliding time window, using a KD-tree for the distance test.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from .util import UnionFind, to_xy


def _window_pairs(df: pd.DataFrame, key: str, hours: float, radius_m: float, extra=None) -> list[tuple[str, str]]:
    """Pairs with the same key, within `hours` and `radius_m`. extra(i, j) may veto a pair."""
    pairs = []
    for _, grp in df.groupby(key):
        if len(grp) < 2:
            continue
        g = grp.sort_values("reported_at")
        t = g["reported_at"].astype("int64").to_numpy() / 3.6e12
        xy = to_xy(g["lat"].to_numpy(), g["lon"].to_numpy())
        ok = ~np.isnan(xy).any(axis=1)
        ids = g["event_id"].to_numpy()
        if ok.sum() >= 2:
            tree = cKDTree(xy[ok])
            idx = np.where(ok)[0]
            for a, b in tree.query_pairs(radius_m):
                i, j = idx[a], idx[b]
                if abs(t[i] - t[j]) <= hours and (extra is None or extra(g.iloc[i], g.iloc[j])):
                    pairs.append((ids[i], ids[j]))
    return pairs


def grievances(ev: pd.DataFrame) -> tuple[dict[str, str], dict]:
    g = ev[ev["source"] == "grievance"].copy()
    g["key"] = g["category_src"] + "|" + g["subcategory_src"] + "|" + g["dept_src"].fillna("")
    uf = UnionFind(g["event_id"])
    pinned = g[g["geo_method"] == "pin"]
    for a, b in _window_pairs(pinned, "key", 72, 150):
        uf.union(a, b)
    # rows without a pin: same street in the same ward within 72 h
    nopin = g[g["geo_method"] != "pin"]
    for _, grp in g.groupby(["key", "ward_no", "_street"]):
        if len(grp) < 2 or grp["_street"].iloc[0] == "" or not grp["event_id"].isin(nopin["event_id"]).any():
            continue
        grp = grp.sort_values("reported_at")
        t = grp["reported_at"].astype("int64").to_numpy() / 3.6e12
        ids = grp["event_id"].to_numpy()
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                if t[j] - t[i] > 72:
                    break
                uf.union(ids[i], ids[j])
    # officers' own decisions: "Rejected: Duplicate of 2026-XXXXXX"
    human = 0
    for eid, dup in zip(g["event_id"], g["_officer_dup_of"]):
        if isinstance(dup, str) and f"GRV-{dup}" in uf.parent:
            uf.union(eid, f"GRV-{dup}")
            human += 1
    groups = _name_groups(uf, g)
    return groups, {"officer_duplicate_decisions_used": human}


def police(ev: pd.DataFrame) -> dict[str, str]:
    p = ev[ev["source"] == "police"].copy()
    p["reported_at"] = p["occurred_at"]
    uf = UnionFind(p["event_id"])
    for a, b in _window_pairs(p, "category_src", 3, 600, extra=lambda x, y: x["channel"] != y["channel"]):
        uf.union(a, b)
    return _name_groups(uf, p)


def pwd(ev: pd.DataFrame) -> dict[str, str]:
    w = ev[ev["source"] == "pwd"].copy()
    uf = UnionFind(w["event_id"])
    for a, b in _window_pairs(w, "category_src", 48, 200):
        uf.union(a, b)
    return _name_groups(uf, w)


def _name_groups(uf: UnionFind, df: pd.DataFrame) -> dict[str, str]:
    t = dict(zip(df["event_id"], df["reported_at"]))
    out = {}
    for root, members in uf.groups().items():
        first = min(members, key=lambda m: (t.get(m), m))
        for m in members:
            out[m] = first
    return out


def bcubed(pred: dict, truth: dict) -> dict:
    items = list(truth)
    pc, tc = defaultdict(set), defaultdict(set)
    for i in items:
        pc[pred[i]].add(i)
        tc[truth[i]].add(i)
    P = sum(len(pc[pred[i]] & tc[truth[i]]) / len(pc[pred[i]]) for i in items) / len(items)
    R = sum(len(pc[pred[i]] & tc[truth[i]]) / len(tc[truth[i]]) for i in items) / len(items)
    return {"precision": round(P, 4), "recall": round(R, 4), "f1": round(2 * P * R / (P + R), 4), "n": len(items)}


def evaluate(ev: pd.DataFrame, g_groups: dict, p_groups: dict, settings) -> dict:
    out = {}
    tr = pd.read_csv(settings.src("grievances", "duplicate_truth"), encoding="utf-8-sig")
    tmap = {f"GRV-{c}": grp for c, grp in zip(tr["Complaint No"], tr["Truth Group"]) if grp.startswith("DUP")}
    base = ev[(ev["source"] == "grievance") & (ev["is_overlay"] == 0)]["event_id"]
    truth = {e: tmap.get(e, e) for e in base}
    out["grievance_dedup"] = bcubed({e: g_groups[e] for e in truth}, truth)
    gt = pd.read_csv(settings.src("police", "ground_truth"), encoding="utf-8-sig")
    pmap = {"POL-" + r.replace("POL-", ""): t for r, t in zip(gt["report_id"], gt["true_event_id"])}
    pbase = ev[(ev["source"] == "police") & (ev["is_overlay"] == 0)]["event_id"]
    ptruth = {e: pmap[e] for e in pbase if e in pmap}
    out["police_dedup"] = bcubed({e: p_groups[e] for e in ptruth}, ptruth)
    return out
