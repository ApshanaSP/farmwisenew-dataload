"""Ward geometry: point-in-polygon, snapping, adjacency, zone outlines.

Uses only numpy, matplotlib.path and scipy (no GEOS dependency). With 200 ward
polygons and a bounding-box prefilter, locating 20k points takes about a second.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from matplotlib.path import Path as MplPath
from scipy.spatial import cKDTree

from .util import to_xy


@dataclass
class Ward:
    ward_no: int
    zone_no: int
    zone_name: str
    rings: list[np.ndarray]            # outer rings, lon/lat arrays (N, 2)
    paths: list[MplPath] = field(default_factory=list)
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)
    area_km2: float = 0.0
    centroid: tuple[float, float] = (0.0, 0.0)   # lat, lon


class WardIndex:
    def __init__(self, geojson_path: Path) -> None:
        gj = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
        self.wards: list[Ward] = []
        for f in gj["features"]:
            p = f["properties"]
            g = f["geometry"]
            polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            rings = [np.asarray(poly[0], dtype=float) for poly in polys]
            w = Ward(int(p["ward_no"]), int(p["zone_number"]), str(p["zone_name"]), rings)
            w.paths = [MplPath(r) for r in rings]
            allpts = np.vstack(rings)
            w.bbox = (allpts[:, 0].min(), allpts[:, 1].min(), allpts[:, 0].max(), allpts[:, 1].max())
            area = 0.0
            cx = cy = 0.0
            for r in rings:
                xy = to_xy(r[:, 1], r[:, 0])
                x, y = xy[:, 0], xy[:, 1]
                a = 0.5 * np.sum(x[:-1] * y[1:] - x[1:] * y[:-1])
                area += abs(a)
                c = np.mean(r, axis=0)
                cx += c[0] * abs(a)
                cy += c[1] * abs(a)
            w.area_km2 = area / 1e6
            w.centroid = (cy / area, cx / area) if area else (float(np.mean(allpts[:, 1])), float(np.mean(allpts[:, 0])))
            self.wards.append(w)
        self.by_no = {w.ward_no: w for w in self.wards}
        verts = []
        owner = []
        for w in self.wards:
            for r in w.rings:
                verts.append(r)
                owner.extend([w.ward_no] * len(r))
        self._verts = np.vstack(verts)
        self._owner = np.asarray(owner)
        self._tree = cKDTree(to_xy(self._verts[:, 1], self._verts[:, 0]))

    # ------------------------------------------------------------------ locate --
    def locate(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        """Ward number for each point, 0 when outside every ward, -1 when missing."""
        lat = np.asarray(lat, dtype=float)
        lon = np.asarray(lon, dtype=float)
        out = np.full(len(lat), -1, dtype=int)
        ok = ~(np.isnan(lat) | np.isnan(lon))
        out[ok] = 0
        pts = np.column_stack([lon, lat])
        for w in self.wards:
            x0, y0, x1, y1 = w.bbox
            cand = ok & (out == 0) & (lon >= x0) & (lon <= x1) & (lat >= y0) & (lat <= y1)
            if not cand.any():
                continue
            idx = np.where(cand)[0]
            inside = np.zeros(len(idx), dtype=bool)
            for p in w.paths:
                inside |= p.contains_points(pts[idx])
            out[idx[inside]] = w.ward_no
        return out

    def nearest(self, lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Nearest ward (by boundary vertex) and distance in metres."""
        d, i = self._tree.query(to_xy(lat, lon))
        return self._owner[i], d

    def locate_with_snap(self, lat, lon, snap_m: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Returns ward_no (0 = outside), method ('pip'|'snapped'|'outside'|'none'), distance."""
        ward = self.locate(lat, lon)
        method = np.where(ward > 0, "pip", np.where(ward == 0, "outside", "none")).astype(object)
        dist = np.zeros(len(ward))
        out_idx = np.where(ward == 0)[0]
        if len(out_idx):
            nw, nd = self.nearest(np.asarray(lat)[out_idx], np.asarray(lon)[out_idx])
            snap = nd <= snap_m
            ward[out_idx[snap]] = nw[snap]
            method[out_idx[snap]] = "snapped"
            dist[out_idx] = nd
        return ward, method, dist

    # --------------------------------------------------------------- topology --
    def adjacency(self, decimals: int = 4) -> dict[int, set[int]]:
        """Wards sharing at least two boundary vertices (rounded to ~11 m)."""
        owners: dict[tuple[float, float], set[int]] = defaultdict(set)
        for w in self.wards:
            for r in w.rings:
                for x, y in np.round(r, decimals):
                    owners[(x, y)].add(w.ward_no)
        pair_count: Counter = Counter()
        for s in owners.values():
            if len(s) > 1:
                s = sorted(s)
                for i in range(len(s)):
                    for j in range(i + 1, len(s)):
                        pair_count[(s[i], s[j])] += 1
        adj: dict[int, set[int]] = defaultdict(set)
        for (a, b), n in pair_count.items():
            if n >= 2:
                adj[a].add(b)
                adj[b].add(a)
        return adj

    def zone_outlines(self, decimals: int = 5) -> dict[int, list[list[list[float]]]]:
        """Line segments on zone borders: ward edges not shared inside the same zone."""
        edge_count: dict[int, Counter] = defaultdict(Counter)
        for w in self.wards:
            for r in w.rings:
                rr = np.round(r, decimals)
                for k in range(len(rr) - 1):
                    a, b = tuple(rr[k]), tuple(rr[k + 1])
                    if a == b:
                        continue
                    edge_count[w.zone_no][(a, b) if a < b else (b, a)] += 1
        out: dict[int, list[list[list[float]]]] = {}
        for z, cnt in edge_count.items():
            out[z] = [[list(a), list(b)] for (a, b), n in cnt.items() if n == 1]
        return out

    def ward_geojson(self, props: dict[int, dict]) -> dict:
        feats = []
        for w in self.wards:
            coords = [[r.tolist()] for r in w.rings]
            geom = {"type": "MultiPolygon", "coordinates": coords}
            feats.append({"type": "Feature", "geometry": geom,
                          "properties": {"ward_no": w.ward_no, "zone_no": w.zone_no, "zone_name": w.zone_name,
                                         **props.get(w.ward_no, {})}})
        return {"type": "FeatureCollection", "features": feats}
