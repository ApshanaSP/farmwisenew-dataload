#!/usr/bin/env python3
"""
IMD weather collector - Chennai District Collector Intelligence Dashboard.

Owns atmospheric data only: rainfall (measured and forecast), temperature, humidity, wind,
weather forecasts and IMD's own warning colour/text. River gauges, gates, discharge and
flood alerts belong to cfm_dss_collector.py; reservoirs to cmwssb_collector.py. Nothing
here outputs water level, stage, gates, discharge, inflow, storage or AQI.

Sources, verified live on 2026-09-25 before the parsers were written (retrieval order):

 Observations + station forecasts
  IMD_CITY_WEATHER      POST https://city.imd.gov.in/citywx/responsive/api/fetchCity_static.php (ID=<station>)
                        JSON behind IMD's public Local Weather Report. Station list from
                        .../api/search.php?query=chennai -> 43278 Nungambakkam, 43279 Meenambakkam,
                        99947 Madhavaram, 99948 Ennore. (43279 is Meenambakkam in the live source, not
                        Nungambakkam.) Tambaram, Poonamallee, DGP Chennai, Kattivakkam: not listed.
                        Gives 24 h rainfall ending 0830 IST, min temp (0830 IST), max temp with its
                        "Recorded on" date, RH 0830/1730 IST, departures, ~7 days of max/min history
                        (no rainfall history, no wind) and a 7-day station forecast.
  IMD_CITY_TEST_PAGE    https://city.imd.gov.in/citywx/city_weather_test.php?id=43279 -> HTTP 403.
  IMD_RMC_CHENNAI_HOME  https://mausam.imd.gov.in/chennai/ shows current temp/wind/RH for Nungambakkam
                        but publishes no observation time, so it cannot be keyed; probed, not stored.
  IMD_AWS_ARG           http://aws.imd.gov.in:8091/ -> login + captcha. Probed only; never bypassed.

 Warnings
  IMD_WARNINGS_API      https://mausam.imd.gov.in/api/warnings_district_api.php?id=78 -> HTTP 401
                        (IP whitelist). Logged, then fall back.
  IMD_DIST_WARNINGS_GIS WFS behind https://mausam.imd.gov.in/responsive/districtWiseWarningGIS.php:
                        https://reactjs.imd.gov.in/geoserver/wfs typename imd:district_warnings_india.
                        Day_1..Day_5 warning codes, DayN_Color codes, updated_at. Code->hex comes from
                        the layer's own WMS style (GetLegendGraphic); code->wording from the page script.
  IMD_DIST_WARNINGS_RMC https://mausam.imd.gov.in/imd_latest/contents/districtwise-warning_mc.php?id=26&day=Day_N
                        Fallback when the GIS service fails (issue date only, no time).
  IMD_ALL_INDIA_BULLETIN https://mausam.imd.gov.in/imd_latest/contents/all_india_forcast_bulletin.php
                        200, but carries no Chennai/Tamil Nadu content; probed as last fallback only.
  IMD_DIST_NOWCAST_RMC  https://mausam.imd.gov.in/imd_latest/contents/districtwisewarnings_mc.php?id=26
                        Time of issue + "Valid upto" in IST, colour + legend.

 Fallback (clearly labelled, never an IMD observation or warning)
  OPEN_METEO_ARCHIVE    https://archive-api.open-meteo.com/v1/archive
  OPEN_METEO_FORECAST   https://api.open-meteo.com/v1/forecast (recent tail, and forecast only if IMD's fails)
                        Both hosts' robots.txt say "Disallow: /". Respected by default; enable with
                        --open-meteo-ignore-robots only if you accept Open-Meteo's API terms.

Requires Python 3.10+, requests, pandas, beautifulsoup4, lxml. IMD hosts send an incomplete TLS
chain; the optional 'truststore' package lets Windows/macOS complete it (otherwise TLS fails).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

try:
    import truststore

    truststore.inject_into_ssl()
    HAVE_TRUSTSTORE = True
except ImportError:  # pragma: no cover
    HAVE_TRUSTSTORE = False

import pandas as pd
import requests
from bs4 import BeautifulSoup

IST = ZoneInfo("Asia/Kolkata")
SCRIPT_DIR = Path(__file__).resolve().parent
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36 ChennaiDistrictDashboard-IMDCollector/2.0")
TIMEOUT_S = 20
HOST_GAP_S = 1.5
MAX_RETRIES = 3
MAX_BACKOFF_S = 60
MAX_BYTES = 8 * 1024 * 1024
RAW_KEEP_DAYS = 30
WINDOW_DAYS = 90
RECHECK_DAYS = 3
NEARBY_KM = 25.0

CITY_API = "https://city.imd.gov.in/citywx/responsive/api/fetchCity_static.php"
CITY_SEARCH = "https://city.imd.gov.in/citywx/responsive/api/search.php"
CITY_PAGE = "https://city.imd.gov.in/citywx/responsive/?id={sid}"
CITY_TEST_PAGE = "https://city.imd.gov.in/citywx/city_weather_test.php?id=43279"
RMC_HOME = "https://mausam.imd.gov.in/chennai/"
AWS_ARG = "http://aws.imd.gov.in:8091/state/TAMIL_NADU"
WARN_API = "https://mausam.imd.gov.in/api/warnings_district_api.php"
GIS_PAGE = "https://mausam.imd.gov.in/responsive/districtWiseWarningGIS.php"
GIS_WFS = "https://reactjs.imd.gov.in/geoserver/wfs"
GIS_WMS = "https://reactjs.imd.gov.in/geoserver/imd/wms"
GIS_LAYER = "imd:Warnings_StateDistrict_Merged"
RMC_WARN = "https://mausam.imd.gov.in/imd_latest/contents/districtwise-warning_mc.php?id=26&day=Day_{n}"
RMC_NOWCAST = "https://mausam.imd.gov.in/imd_latest/contents/districtwisewarnings_mc.php?id=26"
ALL_INDIA_BULLETIN = "https://mausam.imd.gov.in/imd_latest/contents/all_india_forcast_bulletin.php"
OM_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OM_FORECAST = "https://api.open-meteo.com/v1/forecast"

KNOWN_CITY_IDS = ["43278", "43279", "99947", "99948"]  # used only if the live station search fails
IMD_DISTRICT = "CHENNAI"
IMD_DISTRICT_ID = "78"  # Obj_id of CHENNAI in IMD's warning layers (verified 2026-09-25)
SENTINELS = {"", "NA", "N/A", "--", "-", "999", "999.0", "99.0", "-999", "NULL", "NONE", "NAN"}
OM_DAILY = ("precipitation_sum,temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean,"
            "wind_speed_10m_max,wind_direction_10m_dominant")

P_IMD = "IMD"
P_OM = "Open-Meteo"

# Colour names for the hex values IMD's own styles/legends use. A hex is stored only when the
# authority's legend or style defines it; unknown hex values are stored verbatim.
HEX_NAMES = {"#008000": "Green", "#078C03": "Green", "#FFFF00": "Yellow", "#F2E205": "Yellow",
             "#FFA500": "Orange", "#F28705": "Orange", "#FF0000": "Red", "#F20505": "Red"}

OBS_COLS = ["provider", "station_id", "station_name", "latitude", "longitude", "observed_at",
            "observation_period_start", "observation_period_end", "rainfall_mm", "temp_max_c",
            "temp_min_c", "temp_departure_c", "humidity_pct", "wind_speed_kmph", "wind_direction",
            "rainfall_class_published", "source_url", "fetched_at"]
OBS_KEY = ["provider", "station_id", "observed_at", "observation_period_start", "observation_period_end"]
FC_COLS = ["provider", "location_id", "issued_at", "valid_from", "valid_to", "forecast_rainfall_mm",
           "forecast_text", "source_url", "fetched_at"]
FC_KEY = ["provider", "location_id", "issued_at", "valid_from", "valid_to"]
WARN_COLS = ["warning_id", "issuing_authority", "location_id", "issued_at", "valid_from", "valid_to",
             "warning_colour", "warning_text", "source_url", "fetched_at"]
WARN_KEY = ["warning_id"]
LOG_COLS = ["run_at", "source", "retrieval_method", "fetch_status", "stale", "latest_source_observation_time",
            "latest_successful_observation_time", "fetched_at", "rows_fetched", "rows_inserted", "rows_updated",
            "rows_unchanged", "rows_archived", "corrections", "district_boundary_exclusions", "imd_rows",
            "fallback_rows", "coverage_90d_per_station", "total_rows_after", "notes"]


# ----------------------------------------------------------------------------- small helpers

def now_ist() -> datetime:
    return datetime.now(IST).replace(microsecond=0)


def iso(dt: datetime | None) -> str:
    return dt.astimezone(IST).isoformat(timespec="seconds") if dt else ""


def at_ist(d: date, hh: int = 0, mm: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=IST)


def parse_ts(s: str) -> datetime | None:
    """ISO timestamp (or date-only, read as IST midnight) -> aware datetime."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=IST)


def num(v: Any, lo: float, hi: float, rejects: list[str], label: str) -> float | None:
    """Published number or None. Sentinels / out-of-range values stay missing, never zero."""
    if v is None:
        return None
    s = str(v).strip()
    if s.upper() in SENTINELS:
        return None
    try:
        x = float(s)
    except ValueError:
        rejects.append(f"{label}: non-numeric {s!r}")
        return None
    if math.isnan(x) or not lo <= x <= hi:
        rejects.append(f"{label}: out of range {s!r}")
        return None
    return x


def fmt(x: float | None, places: int = 2) -> str:
    return "" if x is None else str(round(x, places))


def html_text(fragment: str) -> str:
    fragment = re.sub(r"(?i)<\s*/?\s*br\s*/?\s*>", "; ", fragment)  # IMD writes </br>, which parsers drop
    soup = BeautifulSoup(fragment, "lxml")
    for p in soup.find_all("p"):
        p.insert_after("; ")
    t = re.sub(r"\s+", " ", soup.get_text(" "))
    return re.sub(r"(\s*;\s*)+", "; ", t).strip(" ;")


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def ranges(days: Iterable[date]) -> str:
    ds, out, i = sorted(set(days)), [], 0
    while i < len(ds):
        j = i
        while j + 1 < len(ds) and ds[j + 1] - ds[j] == timedelta(days=1):
            j += 1
        out.append(ds[i].isoformat() if i == j else f"{ds[i]}..{ds[j]}")
        i = j + 1
    return ", ".join(out)


def atomic_write(path: Path, text: str, encoding: str = "utf-8-sig") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ----------------------------------------------------------------------------- HTTP

@dataclass
class Fetch:
    url: str
    ok: bool
    status: int | None = None
    content: bytes = b""
    error: str = ""
    blocked: bool = False
    fetched_at: datetime = field(default_factory=now_ist)

    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace").lstrip("﻿")

    def json(self) -> Any:
        return json.loads(self.text())


class Http:
    """Live requests only: one host at a time, 1.5 s apart, backoff with Retry-After, robots.txt, size cap."""

    def __init__(self, raw_dir: Path):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9",
                               "Cache-Control": "no-cache", "Pragma": "no-cache"})
        self.raw_dir = raw_dir
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | str] = {}

    def _space(self, host: str) -> None:
        wait = HOST_GAP_S - (time.monotonic() - self._last.get(host, -1e9))
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def robots_allows(self, url: str) -> tuple[bool, str]:
        u = urlparse(url)
        origin = f"{u.scheme}://{u.netloc}"
        if origin not in self._robots:
            self._space(u.netloc)
            try:
                r = self.s.get(origin + "/robots.txt", timeout=TIMEOUT_S)
                if r.status_code == 200 and "text/plain" in r.headers.get("Content-Type", ""):
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse(r.text[:200_000].splitlines())
                    self._robots[origin] = rp
                elif 400 <= r.status_code < 500 or r.status_code == 200:
                    self._robots[origin] = "allow-all"  # no robots.txt published
                else:
                    self._robots[origin] = f"robots.txt unavailable (HTTP {r.status_code})"
            except requests.RequestException as e:
                self._robots[origin] = f"robots.txt unreachable ({type(e).__name__})"
        rp = self._robots[origin]
        if rp == "allow-all":
            return True, ""
        if isinstance(rp, str):
            return False, rp
        return (True, "") if rp.can_fetch(USER_AGENT, url) else (False, "disallowed by robots.txt")

    def get(self, url: str, tag: str, *, method: str = "GET", params: dict | None = None,
            data: dict | None = None, headers: dict | None = None, ignore_robots: bool = False) -> Fetch:
        full = url + ("?" + urlencode(params) if params else "")
        allowed, why = self.robots_allows(full)
        if not allowed and not ignore_robots:
            return Fetch(full, False, error=why, blocked=True)
        host = urlparse(url).netloc
        err, status = "", None
        for attempt in range(MAX_RETRIES + 1):
            self._space(host)
            fetched = now_ist()
            try:
                with self.s.request(method, url, params=params, data=data, headers=headers,
                                    timeout=TIMEOUT_S, stream=True) as r:
                    status = r.status_code
                    if status in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                        err = f"HTTP {status}"
                        time.sleep(self._backoff(r.headers.get("Retry-After"), attempt))
                        continue
                    body = self._read_capped(r)
                    if body is None:
                        return Fetch(full, False, status, error=f"response exceeds {MAX_BYTES} bytes", fetched_at=fetched)
                    self._archive(tag, body, r.headers.get("Content-Type", ""), fetched)
                    if status != 200:
                        snippet = body[:160].decode("utf-8", "replace").strip()
                        return Fetch(full, False, status, body, f"HTTP {status}: {snippet}", fetched_at=fetched)
                    return Fetch(full, True, status, body, fetched_at=fetched)
            except requests.exceptions.SSLError as e:
                hint = "" if HAVE_TRUSTSTORE else " - install 'truststore' (IMD sends an incomplete certificate chain)"
                return Fetch(full, False, error=f"TLS error{hint}: {str(e)[:160]}", fetched_at=fetched)
            except requests.RequestException as e:
                err = f"{type(e).__name__}: {str(e)[:160]}"
                if attempt < MAX_RETRIES:
                    time.sleep(self._backoff(None, attempt))
        return Fetch(full, False, status, error=err)

    @staticmethod
    def _backoff(retry_after: str | None, attempt: int) -> float:
        if retry_after:
            try:
                return min(MAX_BACKOFF_S, max(0.0, float(retry_after)))
            except ValueError:
                try:
                    return min(MAX_BACKOFF_S, max(0.0, (parsedate_to_datetime(retry_after) -
                                                        datetime.now(timezone.utc)).total_seconds()))
                except (TypeError, ValueError):
                    pass
        return min(MAX_BACKOFF_S, 2.0 * 2 ** attempt)

    @staticmethod
    def _read_capped(r: requests.Response) -> bytes | None:
        if int(r.headers.get("Content-Length") or 0) > MAX_BYTES:
            return None
        buf = bytearray()
        for chunk in r.iter_content(65536):
            buf.extend(chunk)
            if len(buf) > MAX_BYTES:
                return None
        return bytes(buf)

    def _archive(self, tag: str, body: bytes, ctype: str, at: datetime) -> None:
        """Debug copy only - never read back as live data."""
        try:
            ext = "json" if "json" in ctype else "html" if "html" in ctype else "txt"
            d = self.raw_dir / at.strftime("%Y-%m-%d")
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{at:%H%M%S}_{re.sub(r'[^A-Za-z0-9_.-]', '_', tag)}.{ext}").write_bytes(body)
        except OSError:
            pass

    def prune_raw(self, today: date) -> None:
        if not self.raw_dir.exists():
            return
        for d in self.raw_dir.iterdir():
            try:
                if d.is_dir() and date.fromisoformat(d.name) < today - timedelta(days=RAW_KEEP_DAYS):
                    for f in d.iterdir():
                        f.unlink()
                    d.rmdir()
            except (ValueError, OSError):
                continue


# ----------------------------------------------------------------------------- geography

class DistrictBoundary:
    """Chennai District = union of GCC ward polygons (district limits match GCC limits since 2018)."""

    def __init__(self, path: Path):
        gj = json.loads(path.read_text(encoding="utf-8"))
        self.polys: list[tuple[tuple[float, float, float, float], list[list[tuple[float, float]]]]] = []
        for feat in gj.get("features", []):
            g = feat.get("geometry") or {}
            parts = [g.get("coordinates")] if g.get("type") == "Polygon" else \
                g.get("coordinates", []) if g.get("type") == "MultiPolygon" else []
            for poly in parts:
                rings = [[(float(p[0]), float(p[1])) for p in ring] for ring in poly]
                if rings and len(rings[0]) >= 4:
                    xs, ys = [p[0] for p in rings[0]], [p[1] for p in rings[0]]
                    self.polys.append(((min(xs), min(ys), max(xs), max(ys)), rings))
        if not self.polys:
            raise ValueError(f"no polygons in boundary file {path}")
        self.label = f"{path.name} ({len(self.polys)} polygons)"
        meta = path.with_name(path.stem + ".meta.json")
        if meta.exists():
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
                self.label += f"; {m.get('sourceLabel') or m.get('source')}; official={m.get('official')}"
            except (ValueError, OSError):
                pass

    @staticmethod
    def _in_ring(x: float, y: float, ring: list[tuple[float, float]]) -> bool:
        inside, j = False, len(ring) - 1
        for i, (xi, yi) in enumerate(ring):
            xj, yj = ring[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside

    def contains(self, lat: float, lon: float) -> bool:
        return any(x0 <= lon <= x1 and y0 <= lat <= y1 and self._in_ring(lon, lat, rings[0])
                   and not any(self._in_ring(lon, lat, h) for h in rings[1:])
                   for (x0, y0, x1, y1), rings in self.polys)

    def distance_km(self, lat: float, lon: float) -> float:
        if self.contains(lat, lon):
            return 0.0
        return min(km(lat, lon, y, x) for _, rings in self.polys for x, y in rings[0])

    def centroid(self) -> tuple[float, float]:
        ax = ay = a = 0.0
        for _, rings in self.polys:
            for (x1, y1), (x2, y2) in zip(rings[0], rings[0][1:]):
                c = x1 * y2 - x2 * y1
                a, ax, ay = a + c, ax + (x1 + x2) * c, ay + (y1 + y2) * c
        return ay / (3 * a), ax / (3 * a)


class LocationMaster:
    """Dashboard taluk master (taluk centroids + locality points; it has no taluk polygons)."""

    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.name = path.name
        self.points = [(t["centroid"]["lat"], t["centroid"]["lng"], t["code"], t["name"])
                       for t in data.get("taluks", []) if t.get("centroid")]
        self.points += [(loc["lat"], loc["lng"], t["code"], t["name"])
                        for t in data.get("taluks", []) for loc in t.get("localities", [])]
        if not self.points:
            raise ValueError(f"location master {path} has no taluk points")

    def taluk(self, lat: float, lon: float) -> tuple[str, str, float]:
        p = min(self.points, key=lambda q: km(lat, lon, q[0], q[1]))
        return p[2], p[3], km(lat, lon, p[0], p[1])


# ----------------------------------------------------------------------------- CSV store

class Store:
    """Active rolling-window CSV + archive CSV, upserted on a key, written atomically (UTF-8 BOM)."""

    def __init__(self, path: Path, cols: list[str], key: list[str], time_col: str, sort: list[str]):
        self.path, self.cols, self.key, self.time_col, self.sort = path, cols, key, time_col, sort
        self.archive_path = path.with_name(path.stem + "_archive.csv")
        self.values = [c for c in cols if c not in key and c not in ("source_url", "fetched_at")]
        self.rows, self.dupes = self._load(path)
        self.archive: dict[tuple, dict[str, str]] | None = None

    def _load(self, path: Path) -> tuple[dict[tuple, dict[str, str]], int]:
        if not path.exists():
            return {}, 0
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        missing = [c for c in self.cols if c not in df.columns]
        if missing:
            raise ValueError(f"{path.name} lacks columns {missing}; refusing to overwrite it")
        rows: dict[tuple, dict[str, str]] = {}
        dupes = 0
        for r in df[self.cols].to_dict("records"):
            k = tuple(r[c] for c in self.key)
            dupes += k in rows
            rows[k] = r
        return rows, dupes

    def upsert(self, row: dict[str, str]) -> tuple[str, dict[str, tuple[str, str]]]:
        row = {c: row.get(c, "") for c in self.cols}
        if not row["source_url"] or not row["fetched_at"]:
            raise ValueError("row without source attribution")
        k = tuple(row[c] for c in self.key)
        old = self.rows.get(k)
        if old is None:
            self.rows[k] = row
            return "inserted", {}
        changes = {c: (old[c], row[c]) for c in self.values if row[c] != "" and row[c] != old[c]}
        if not changes:
            return "unchanged", {}
        old.update({c: n for c, (_, n) in changes.items()}, source_url=row["source_url"], fetched_at=row["fetched_at"])
        return "updated", changes

    def archive_before(self, cutoff: datetime) -> int:
        old = [k for k, r in self.rows.items() if (t := parse_ts(r[self.time_col])) and t < cutoff]
        if not old:
            return 0
        if self.archive is None:
            self.archive, _ = self._load(self.archive_path)
        for k in old:
            self.archive[k] = self.rows.pop(k)
        return len(old)

    def _csv(self, rows: Iterable[dict[str, str]]) -> str:
        df = pd.DataFrame(list(rows), columns=self.cols)
        df = df.sort_values(self.sort, kind="stable") if len(df) else df
        buf = io.StringIO()
        df.to_csv(buf, index=False, lineterminator="\n")
        return buf.getvalue()

    def save(self) -> None:
        atomic_write(self.path, self._csv(self.rows.values()))
        if self.archive is not None:
            atomic_write(self.archive_path, self._csv(self.archive.values()))

    def date_range(self) -> str:
        ts = [t for r in self.rows.values() if (t := parse_ts(r[self.time_col]))]
        return f"{iso(min(ts))} .. {iso(max(ts))}" if ts else "-"


# ----------------------------------------------------------------------------- per-source report

@dataclass
class Report:
    source: str
    method: str
    status: str = "not_run"
    latest_obs: str = ""
    fetched_at: str = ""
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    corrections: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def tally(self, outcome: str, changes: dict, key: tuple) -> None:
        self.fetched += 1
        setattr(self, outcome, getattr(self, outcome) + 1)
        fixed = {c: v for c, v in changes.items() if v[0] != ""}  # filling a blank is not a correction
        if fixed:
            self.corrections.append(f"{'/'.join(key)}: " + ", ".join(f"{c} {o}->{n}" for c, (o, n) in fixed.items()))

    def fail(self, status: str, why: str) -> None:
        self.status = status
        self.notes.append(why)


# ----------------------------------------------------------------------------- collector

class Collector:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.out = Path(a.out).resolve()
        self.run_at = now_ist()
        self.today = self.run_at.date()
        self.ref = a.date or self.today
        self.window = [self.ref - timedelta(days=i) for i in range(WINDOW_DAYS - 1, -1, -1)]
        self.backfill_from = self.ref - timedelta(days=min(a.backfill_days, WINDOW_DAYS) - 1)
        self.http = Http(self.out / "raw")
        self.boundary = DistrictBoundary(Path(a.boundary))
        self.master = LocationMaster(Path(a.location_master))
        self.obs = Store(self.out / "imd_weather_observations_chennai.csv", OBS_COLS, OBS_KEY,
                         "observed_at", ["observed_at", "station_id", "provider"])
        self.fc = Store(self.out / "imd_weather_forecasts_chennai.csv", FC_COLS, FC_KEY,
                        "issued_at", ["issued_at", "location_id", "valid_from"])
        self.warn = Store(self.out / "imd_weather_warnings_chennai.csv", WARN_COLS, WARN_KEY,
                          "issued_at", ["issued_at", "location_id", "valid_from", "warning_id"])
        self.log_path = self.out / "imd_weather_run_log.csv"
        self.reports: list[Report] = []
        self.stations: dict[str, dict[str, Any]] = {}  # IMD station_id -> membership info
        self.live_warnings: list[dict[str, str]] = []
        self.live_forecasts: list[dict[str, str]] = []

    def report(self, source: str, method: str) -> Report:
        r = Report(source, method, fetched_at=iso(now_ist()))
        self.reports.append(r)
        return r

    # ---------------------------------------------------------------- station geography
    def classify(self, sid: str, name: str, lat: float, lon: float, rep: Report) -> None:
        inside = self.boundary.contains(lat, lon)
        dist = self.boundary.distance_km(lat, lon)
        info: dict[str, Any] = {"name": name, "lat": lat, "lon": lon, "inside": inside, "dist": dist,
                                "membership": "inside" if inside else "nearby context" if dist <= NEARBY_KM else "outside"}
        if inside:
            info["taluk_code"], info["taluk"], info["taluk_km"] = self.master.taluk(lat, lon)
        else:
            rep.exclusions.append(f"{sid} {name} ({info['membership']}, {dist:.2f} km outside)")
        self.stations[sid] = info

    # ---------------------------------------------------------------- IMD city weather
    def collect_city(self) -> None:
        rep = self.report("IMD_CITY_WEATHER", "POST JSON city.imd.gov.in/citywx/responsive/api/fetchCity_static.php")
        hdr = {"Referer": "https://city.imd.gov.in/citywx/responsive/"}
        ids = self._city_ids(rep, hdr)
        good, latest = 0, None
        for sid in ids:
            f = self.http.get(CITY_API, f"city_{sid}", method="POST", data={"ID": sid},
                              headers={"Referer": CITY_PAGE.format(sid=sid)})
            if not f.ok:
                rep.notes.append(f"{sid}: {f.error}")
                continue
            try:
                payload = f.json()
                if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
                    raise ValueError(f"unexpected payload {str(payload)[:120]}")
                if str(payload[0].get("station_id")) != sid:
                    raise ValueError(f"response is for station {payload[0].get('station_id')!r}")
                hist = payload[1] if len(payload) > 1 and isinstance(payload[1], dict) else {}
                t = self._city_record(payload[0], hist, f, rep)
            except (ValueError, KeyError, TypeError) as e:
                rep.notes.append(f"{sid}: parse error - {e}")
                continue
            good += 1
            latest = max(filter(None, [latest, t]), default=None)
        rep.latest_obs = iso(latest)
        rep.status = "ok" if good == len(ids) else "partial" if good else "failed"
        rep.notes.append("rainfall = 24 h ending 0830 IST as labelled on IMD Local Weather Report; min temp 0830 IST; "
                         "max temp keyed to 1730 IST of its 'Recorded on' date; no rainfall history or wind published")
        rep.notes.append("forecast issued_at = record 'updat' read as UTC (a record holding 0830 IST readings "
                         "carried updat 04:31, impossible in IST)")

    def _city_ids(self, rep: Report, hdr: dict) -> list[str]:
        f = self.http.get(CITY_SEARCH, "city_search", params={"query": "chennai"}, headers=hdr)
        try:
            ids = [str(d["station_id"]) for d in f.json().get("data", []) if str(d.get("station_id", "")).isdigit()] \
                if f.ok else []
        except (ValueError, AttributeError, KeyError):
            ids = []
        if not ids:
            rep.notes.append(f"station search failed ({f.error or 'empty'}); using IDs verified 2026-09-25")
            return KNOWN_CITY_IDS
        return ids

    def _city_record(self, rec: dict, hist: dict, f: Fetch, rep: Report) -> datetime | None:
        rj: list[str] = []
        sid = str(rec["station_id"])
        name = re.sub(r"\s*-\s*", "-", str(rec.get("station", "")).strip())
        lat, lon = num(rec.get("lat"), 5, 40, rj, "lat"), num(rec.get("lon"), 65, 100, rj, "lon")
        if lat is None or lon is None:
            raise ValueError("station coordinates missing")
        self.classify(sid, name, lat, lon, rep)
        dat = date.fromisoformat(str(rec["dat"]))
        if dat > self.today:
            raise ValueError(f"observation date {dat} is in the future")
        md = str(rec.get("max_date") or "").strip()
        max_date = date.fromisoformat(md) if md else dat
        src = f"{CITY_API} (POST ID={sid})"
        base = {"provider": P_IMD, "station_id": sid, "station_name": name, "latitude": fmt(lat, 6),
                "longitude": fmt(lon, 6), "source_url": src, "fetched_at": iso(f.fetched_at)}
        rows = self._city_obs_rows(rec, hist, dat, max_date, base, rj)
        latest = self._upsert_obs(rows, f.fetched_at, rep)
        self._city_forecast(rec, dat, sid, name, src, f.fetched_at, rep, rj)
        rep.notes.extend(f"{sid} reject {m}" for m in rj[:10])
        return latest

    @staticmethod
    def _city_obs_rows(rec: dict, hist: dict, dat: date, max_date: date, base: dict, rj: list[str]) -> list[dict]:
        def r08(d: date, **v: str) -> dict:
            return {**base, "observed_at": iso(at_ist(d, 8, 30)), "observation_period_start":
                    iso(at_ist(d - timedelta(days=1), 8, 30)), "observation_period_end": iso(at_ist(d, 8, 30)), **v}

        def r17(d: date, **v: str) -> dict:
            return {**base, "observed_at": iso(at_ist(d, 17, 30)), **v}

        rows: list[dict] = []
        for ds, h in sorted(hist.items()):  # ~7-day history; departures are not published per day
            try:
                d = date.fromisoformat(ds)
            except ValueError:
                continue
            if d > dat or not isinstance(h, dict):
                continue  # later dates hold forecasts, not observations
            if (lo := num(h.get("MIN"), -10, 45, rj, f"{ds} MIN")) is not None:
                rows.append(r08(d, temp_min_c=fmt(lo)))
            if (hi := num(h.get("MAX"), -10, 55, rj, f"{ds} MAX")) is not None:
                rows.append(r17(d, temp_max_c=fmt(hi)))
        if max_date == dat:  # prevday_* is only unambiguous when today's max belongs to `dat`
            pmax = num(rec.get("prevday_max"), -10, 55, rj, "prevday_max")
            pdep = num(rec.get("prevday_maxdep"), -25, 25, rj, "prevday_maxdep")
            prh = num(rec.get("prevday_rh1730"), 0, 100, rj, "prevday_rh1730")
            if pmax is not None or prh is not None:
                rows.append(r17(dat - timedelta(days=1), temp_max_c=fmt(pmax), humidity_pct=fmt(prh),
                                temp_departure_c=fmt(pdep) if pmax is not None else ""))
        rain = num(rec.get("rainfall"), 0, 1500, rj, "rainfall")
        tmin = num(rec.get("min"), -10, 45, rj, "min")
        mdep = num(rec.get("mindep"), -25, 25, rj, "mindep")
        rh08 = num(rec.get("rh0830"), 0, 100, rj, "rh0830")
        if any(v is not None for v in (rain, tmin, rh08)):
            rows.append(r08(dat, rainfall_mm=fmt(rain), temp_min_c=fmt(tmin), humidity_pct=fmt(rh08),
                            temp_departure_c=fmt(mdep) if tmin is not None else ""))
        tmax = num(rec.get("max"), -10, 55, rj, "max")
        xdep = num(rec.get("maxdep"), -25, 25, rj, "maxdep")
        rh17 = num(rec.get("rh1730"), 0, 100, rj, "rh1730")
        if max_date == dat:
            if tmax is not None or rh17 is not None:
                rows.append(r17(dat, temp_max_c=fmt(tmax), humidity_pct=fmt(rh17),
                                temp_departure_c=fmt(xdep) if tmax is not None else ""))
        else:
            if tmax is not None:
                rows.append(r17(max_date, temp_max_c=fmt(tmax), temp_departure_c=fmt(xdep)))
            if rh17 is not None:
                rows.append(r17(dat, humidity_pct=fmt(rh17)))
        return rows

    def _upsert_obs(self, rows: list[dict], fetched: datetime, rep: Report) -> datetime | None:
        merged: dict[tuple, dict[str, str]] = {}
        for r in rows:  # history and current parts of one observation share a key: merge first
            r = {c: r.get(c, "") for c in OBS_COLS}
            k = tuple(r[c] for c in OBS_KEY)
            merged.setdefault(k, r).update({c: v for c, v in r.items() if v})
        latest = None
        for k, r in merged.items():
            t = parse_ts(r["observed_at"])
            if t is None or t > fetched + timedelta(minutes=5):
                rep.notes.append(f"skipped {r['station_id']} {r['observed_at']}: time invalid or after fetch")
                continue
            outcome, changes = self.obs.upsert(r)
            rep.tally(outcome, changes, k[1:3])
            latest = max(filter(None, [latest, t]), default=None)
        return latest

    def _city_forecast(self, rec: dict, dat: date, sid: str, name: str, src: str, fetched: datetime,
                       rep: Report, rj: list[str]) -> None:
        upd = str(rec.get("updat") or "").strip()
        try:
            issued = datetime.fromisoformat(upd).replace(tzinfo=timezone.utc)
        except ValueError:
            rj.append(f"forecast skipped: unparseable updat {upd!r}")
            return
        ffc, fmax, fmin = rec.get("ffc") or [], rec.get("fmax") or [], rec.get("fmin") or []
        for i, text in enumerate(ffc):
            vd = dat + timedelta(days=i)
            hi = num(fmax[i] if i < len(fmax) else None, -10, 55, rj, f"fmax{i}")
            lo = num(fmin[i] if i < len(fmin) else None, -10, 45, rj, f"fmin{i}")
            parts = [p for p in (str(text).strip(), hi is not None and f"Max {fmt(hi)} °C",
                                 lo is not None and f"Min {fmt(lo)} °C") if p]
            if parts:
                self._upsert_fc({"provider": P_IMD, "location_id": f"IMD_CITY_STATION:{sid}:{name}",
                                 "issued_at": iso(issued), "valid_from": iso(at_ist(vd)),
                                 "valid_to": iso(at_ist(vd + timedelta(days=1))), "forecast_rainfall_mm": "",
                                 "forecast_text": "; ".join(parts), "source_url": src, "fetched_at": iso(fetched)}, rep)

    def _upsert_fc(self, row: dict[str, str], rep: Report) -> None:
        self.live_forecasts.append(row)
        k = tuple(row[c] for c in FC_KEY)
        if k not in self.fc.rows:
            # The same forecast re-served under a newer record timestamp is not a new issuance.
            same = [r for r in self.fc.rows.values() if (r["provider"], r["location_id"], r["valid_from"],
                    r["valid_to"]) == (row["provider"], row["location_id"], row["valid_from"], row["valid_to"])]
            last = max(same, key=lambda r: r["issued_at"], default=None)
            if last and (last["forecast_text"], last["forecast_rainfall_mm"]) == \
                    (row["forecast_text"], row["forecast_rainfall_mm"]):
                rep.tally("unchanged", {}, k[1:3])
                return
        outcome, changes = self.fc.upsert(row)
        rep.tally(outcome, changes, k[1:3])

    # ---------------------------------------------------------------- probes that never produce rows
    def probe(self, source: str, url: str, method: str, check) -> Report:
        rep = self.report(source, method)
        f = self.http.get(url, source.lower())
        rep.fetched_at = iso(f.fetched_at)
        if f.blocked:
            rep.fail("blocked_by_robots", f.error)
        elif not f.ok:
            rep.fail(f"http_{f.status}" if f.status else "network_error", f.error)
        else:
            rep.status, note = check(f.text())
            rep.notes.append(note)
        return rep

    # ---------------------------------------------------------------- warnings
    def collect_warnings(self) -> None:
        api = self.report("IMD_WARNINGS_API", "GET mausam.imd.gov.in/api/warnings_district_api.php")
        f = self.http.get(WARN_API, "warnings_api", params={"id": IMD_DISTRICT_ID})
        api.fetched_at = iso(f.fetched_at)
        if f.ok:
            api.fail("ok_unparsed", "API answered 200; response format not verified, so not parsed")
        else:
            api.fail(f"http_{f.status}" if f.status else "network_error", f.error + " - falling back")
        if self.collect_gis_warnings():
            return
        if self.collect_rmc_warnings():
            return
        self.probe("IMD_ALL_INDIA_BULLETIN", ALL_INDIA_BULLETIN, "GET all_india_forcast_bulletin.php",
                   lambda t: ("no_district_content", "no Chennai district content; nothing stored")
                   if IMD_DISTRICT not in t.upper() else ("unparsed", "mentions Chennai; format not verified"))

    def collect_gis_warnings(self) -> bool:
        rep = self.report("IMD_DIST_WARNINGS_GIS", "GET WFS imd:district_warnings_india + WMS style + GIS page")
        page = self.http.get(GIS_PAGE, "gis_page")
        legend = self.http.get(GIS_WMS, "gis_legend", params={
            "service": "WMS", "version": "1.1.1", "request": "GetLegendGraphic", "layer": GIS_LAYER,
            "format": "application/json", "env": "day:Day1_Color"})
        wfs = self.http.get(GIS_WFS, "gis_wfs", params={
            "service": "WFS", "version": "1.1.0", "request": "GetFeature", "typename": "imd:district_warnings_india",
            "outputFormat": "application/json", "CQL_FILTER": f"District='{IMD_DISTRICT}'"})
        rep.fetched_at = iso(wfs.fetched_at)
        bad = [f for f in (page, legend, wfs) if not f.ok]
        if bad:
            rep.fail("blocked_by_robots" if bad[0].blocked else "failed", "; ".join(f"{f.url[:80]}: {f.error}" for f in bad))
            return False
        try:
            categories = dict(re.findall(r'"(\d+)"\s*:\s*"([^"]+)"',
                                         re.search(r"var category\s*=\s*\{(.*?)\}", page.text(), re.S).group(1)))
            labels = {int(n): datetime.strptime(s, "%b %d, %Y").date()
                      for n, s in re.findall(r'for="rb(\d)">\s*([A-Z][a-z]{2} \d{1,2}, \d{4})\s*<', page.text())}
            codes = dict(re.findall(r"'(\d+)','(#[0-9A-Fa-f]{6})'", legend.text()))
            feats = [x["properties"] for x in wfs.json()["features"]
                     if str(x["properties"].get("District", "")).upper() == IMD_DISTRICT]
            if not (categories and codes and feats):
                raise ValueError(f"missing parts: categories={len(categories)} colours={len(codes)} features={len(feats)}")
        except (AttributeError, ValueError, KeyError, TypeError) as e:
            rep.fail("parse_error", str(e))
            return False
        p = feats[0]
        issued = parse_ts(str(p.get("updated_at", "")))
        base = date.fromisoformat(str(p["Date"]))
        for n in range(1, 6):
            vd = labels.get(n, base + timedelta(days=n - 1))
            if labels.get(n) and labels[n] != base + timedelta(days=n - 1):
                rep.notes.append(f"Day_{n}: page label {labels[n]} differs from Date+{n - 1}; label used")
            names = [categories.get(c.strip(), f"code {c.strip()}") for c in str(p.get(f"Day_{n}", "")).split(",") if c.strip()]
            extra = str(p.get(f"Day{n}_text") or "").strip()
            hexcol = codes.get(str(p.get(f"Day{n}_Color", "")), "").upper()
            self._add_warning({
                "issuing_authority": "IMD - district-wise warning (GIS service)",
                "location_id": f"IMD_DISTRICT:{p.get('Obj_id')}:CHENNAI (district-wide; all covered taluks)",
                "issued_at": iso(issued) if issued else base.isoformat(),
                "valid_from": iso(at_ist(vd)), "valid_to": iso(at_ist(vd + timedelta(days=1))),
                "warning_colour": "" if hexcol in ("", "#FFFFFF") else HEX_NAMES.get(hexcol, hexcol),
                "warning_text": "; ".join(filter(None, [", ".join(names), extra])),
                "source_url": f"{GIS_WFS}?typename=imd:district_warnings_india&CQL_FILTER=District='{IMD_DISTRICT}'",
                "fetched_at": iso(wfs.fetched_at)}, rep)
        rep.status = "ok"
        rep.latest_obs = iso(issued)
        rep.notes.append("issued_at from updated_at as published (Z = UTC); colour codes from the layer's WMS style "
                         f"{codes}; validity = published day, IST calendar date")
        return True

    def collect_rmc_warnings(self) -> bool:
        rep = self.report("IMD_DIST_WARNINGS_RMC", "GET RMC Chennai districtwise-warning_mc.php Day_1..5")
        ok = 0
        for n in range(1, 6):
            url = RMC_WARN.format(n=n)
            f = self.http.get(url, f"rmc_warning_day{n}")
            if not f.ok:
                rep.notes.append(f"Day_{n}: {f.error}")
                continue
            try:
                row = self._rmc_warning_row(f.text(), n, url, f.fetched_at)
            except (ValueError, KeyError) as e:
                rep.notes.append(f"Day_{n}: parse error - {e}")
                continue
            self._add_warning(row, rep)
            rep.latest_obs = max(rep.latest_obs, row["issued_at"])
            ok += 1
        rep.status = "ok" if ok == 5 else "partial" if ok else "failed"
        rep.notes.append("source publishes an issue date only ('Updated on'); no time is invented")
        return ok > 0

    def _rmc_warning_row(self, html: str, n: int, url: str, fetched: datetime) -> dict[str, str]:
        labels = {int(k): datetime.strptime(v.strip(), "%B %d, %Y").date() for k, v in
                  re.findall(r'<input[^>]*value="Day_(\d)"[^>]*>\s*([A-Za-z]+ \d{1,2}, \d{4})', html)}
        area = self._rmc_area(html)
        balloon = str(area.get("balloonText", ""))
        m = re.search(r"Updated on\s*:\s*(\d{4}-\d{2}-\d{2})", balloon)
        if n not in labels or not m:
            raise ValueError("date label or 'Updated on' missing")
        text = html_text(re.sub(r"<p>\s*Updated on.*?</p>", "", balloon, flags=re.S))
        text = re.sub(rf"^{IMD_DISTRICT}\s*:?\s*;?\s*", "", text, flags=re.I)
        return {"issuing_authority": "IMD RMC Chennai - district-wise warning",
                "location_id": f"IMD_DISTRICT:{area.get('id')}:CHENNAI (district-wide; all covered taluks)",
                "issued_at": m.group(1), "valid_from": iso(at_ist(labels[n])),
                "valid_to": iso(at_ist(labels[n] + timedelta(days=1))),
                "warning_colour": self._legend_colour(html, area.get("color", "")),
                "warning_text": text, "source_url": url, "fetched_at": iso(fetched)}

    @staticmethod
    def _rmc_area(html: str) -> dict:
        i = html.find('"areas": [')
        if i < 0:
            raise ValueError("map areas not found")
        areas, _ = json.JSONDecoder().raw_decode(html[i + len('"areas": '):])
        area = next((a for a in areas if str(a.get("title", "")).strip().upper() == IMD_DISTRICT), None)
        if area is None:
            raise ValueError("CHENNAI not in map")
        return area

    @staticmethod
    def _legend_colour(html: str, hexcol: str) -> str:
        m = re.search(r"legend\s*:\s*\{", html)
        block = html[m.start():m.start() + 2000] if m else ""
        legend = {c.upper() for c in re.findall(r'"color"\s*:\s*"(#[0-9A-Fa-f]{6})"', block)}
        h = (hexcol or "").upper()
        return HEX_NAMES.get(h, h) if h in legend else ""

    def collect_nowcast(self) -> None:
        rep = self.report("IMD_DIST_NOWCAST_RMC", "GET RMC Chennai districtwisewarnings_mc.php")
        f = self.http.get(RMC_NOWCAST, "rmc_nowcast")
        rep.fetched_at = iso(f.fetched_at)
        if not f.ok:
            rep.fail("blocked_by_robots" if f.blocked else "failed", f.error)
            return
        try:
            area = self._rmc_area(f.text())
        except ValueError as e:
            rep.fail("ok" if "CHENNAI not in map" in str(e) else "parse_error", str(e))
            return
        rep.status = "ok"
        info = re.sub(rf"^{IMD_DISTRICT}\s*;?\s*", "", html_text(str(area.get("info") or area.get("balloonText") or "")))
        mi = re.search(r"Time of issue\s*:\s*(\d{4}-\d{2}-\d{2})\s*;?\s*(\d{3,4})\s*Hrs", info, re.I)
        mv = re.search(r"Valid up\s*to\s*:\s*(\d{3,4})\s*Hrs", info, re.I)
        if not (mi and mv):
            rep.notes.append(f"no issue/validity time for CHENNAI ({info[:80]!r}); nothing stored")
            return
        ti, tv = mi.group(2).zfill(4), mv.group(1).zfill(4)
        issued = at_ist(date.fromisoformat(mi.group(1)), int(ti[:2]), int(ti[2:]))
        valid_to = at_ist(issued.date(), int(tv[:2]) % 24, int(tv[2:]))
        valid_to += timedelta(days=1) if valid_to <= issued else timedelta(0)
        self._add_warning({"issuing_authority": "IMD RMC Chennai - district-wise nowcast",
                           "location_id": f"IMD_DISTRICT:{area.get('id')}:CHENNAI (district-wide; all covered taluks)",
                           "issued_at": iso(issued), "valid_from": iso(issued), "valid_to": iso(valid_to),
                           "warning_colour": self._legend_colour(f.text(), area.get("color", "")),
                           "warning_text": info[:mi.start()].strip(" ;"), "source_url": RMC_NOWCAST,
                           "fetched_at": iso(f.fetched_at)}, rep)
        rep.latest_obs = iso(issued)

    def _add_warning(self, row: dict[str, str], rep: Report) -> None:
        basis = "|".join(row[c] for c in WARN_COLS[1:8])
        row["warning_id"] = "IMDW-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
        self.live_warnings.append(row)
        outcome, changes = self.warn.upsert(row)
        rep.tally(outcome, changes, (row["warning_id"],))

    # ---------------------------------------------------------------- Open-Meteo fallback
    def collect_open_meteo(self) -> None:
        need = self._fallback_days()
        imd_fc_failed = any(r.source == "IMD_CITY_WEATHER" and r.status == "failed" for r in self.reports)
        if not need and not imd_fc_failed:
            self.report("OPEN_METEO_ARCHIVE", "GET archive-api.open-meteo.com/v1/archive").fail(
                "not_needed", "every past window day has an IMD inside-district observation")
            return
        lat, lon = self.boundary.centroid()
        sid = f"OPENMETEO_CENTROID_{lat:.3f}_{lon:.3f}"
        missing_after_archive = set(need)
        if need:
            got = self._om_call("OPEN_METEO_ARCHIVE", OM_ARCHIVE, sid, lat, lon, need,
                                {"start_date": min(need).isoformat(), "end_date": max(need).isoformat()},
                                "archive (ERA5-family reanalysis)")
            missing_after_archive -= got
        tail = sorted(d for d in missing_after_archive if d >= self.today - timedelta(days=92))
        if tail or imd_fc_failed:
            past = (self.today - min(tail)).days if tail else 0
            self._om_call("OPEN_METEO_FORECAST", OM_FORECAST, sid, lat, lon, tail,
                          {"past_days": past, "forecast_days": 7 if imd_fc_failed else 1},
                          "forecast API (model)", with_forecast=imd_fc_failed)

    def _fallback_days(self) -> list[date]:
        """Complete past days in the backfill range with no inside-district IMD observation.

        Already-filled fallback days are requested again too (one call covers the range), so the
        last RECHECK_DAYS days and any gaps are re-checked for late or corrected values every run.
        """
        imd_days = {parse_ts(r["observed_at"]).date() for r in self.obs.rows.values()
                    if r["provider"] == P_IMD and self.stations.get(r["station_id"], {}).get("inside")}
        return sorted(d for d in self.window if self.backfill_from <= d < self.today and d not in imd_days)

    def _om_call(self, source: str, url: str, sid: str, lat: float, lon: float, days: list[date],
                 extra: dict, kind: str, with_forecast: bool = False) -> set[date]:
        rep = self.report(source, f"GET {urlparse(url).netloc}{urlparse(url).path}")
        params = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "daily": OM_DAILY,
                  "timezone": "Asia/Kolkata", "wind_speed_unit": "kmh", **extra}
        f = self.http.get(url, source.lower(), params=params, ignore_robots=self.a.open_meteo_ignore_robots)
        rep.fetched_at = iso(f.fetched_at)
        if not f.ok:
            rep.fail("blocked_by_robots" if f.blocked else "failed",
                     f.error + (" (use --open-meteo-ignore-robots to accept Open-Meteo's API terms)" if f.blocked else ""))
            return set()
        if self.a.open_meteo_ignore_robots:
            rep.notes.append("robots.txt override requested by operator (--open-meteo-ignore-robots)")
        try:
            j = f.json()
            u = j["daily_units"]
            if (u.get("precipitation_sum"), u.get("temperature_2m_max"), u.get("wind_speed_10m_max"),
                    j.get("utc_offset_seconds")) != ("mm", "°C", "km/h", 19800):
                raise ValueError(f"unexpected units/offset: {u}, {j.get('utc_offset_seconds')}")
            daily, glat, glon = j["daily"], float(j["latitude"]), float(j["longitude"])
        except (ValueError, KeyError, TypeError) as e:
            rep.fail("parse_error", str(e))
            return set()
        name = f"Open-Meteo {kind}, grid cell near Chennai District centroid; calendar day 00-24 IST; NOT an IMD observation"
        got: set[date] = set()
        want = set(days)
        for i, ds in enumerate(daily.get("time", [])):
            d = date.fromisoformat(ds)
            vals = self._om_values(daily, i, ds, rep)
            if d in want and any(vals.values()):
                row = {"provider": P_OM, "station_id": sid, "station_name": name, "latitude": fmt(glat, 6),
                       "longitude": fmt(glon, 6), "observed_at": iso(at_ist(d + timedelta(days=1))),
                       "observation_period_start": iso(at_ist(d)), "observation_period_end": iso(at_ist(d + timedelta(days=1))),
                       **vals, "source_url": f.url, "fetched_at": iso(f.fetched_at)}
                outcome, changes = self.obs.upsert(row)
                rep.tally(outcome, changes, (sid, ds))
                got.add(d)
            elif with_forecast and d >= self.today and vals["rainfall_mm"] + vals["temp_max_c"]:
                self._upsert_fc({"provider": P_OM, "location_id": sid, "issued_at": iso(f.fetched_at),
                                 "valid_from": iso(at_ist(d)), "valid_to": iso(at_ist(d + timedelta(days=1))),
                                 "forecast_rainfall_mm": vals["rainfall_mm"],
                                 "forecast_text": f"Open-Meteo model; Max {vals['temp_max_c']} °C; Min {vals['temp_min_c']} °C",
                                 "source_url": f.url, "fetched_at": iso(f.fetched_at)}, rep)
        rep.status = "ok"
        rep.latest_obs = iso(at_ist(max(got) + timedelta(days=1))) if got else ""
        rep.notes.append("fallback context only; never IMD 0830-0830, never inside-district station, never a warning")
        return got

    @staticmethod
    def _om_values(daily: dict, i: int, ds: str, rep: Report) -> dict[str, str]:
        rj: list[str] = []

        def g(k: str, lo: float, hi: float) -> float | None:
            v = daily.get(k) or []
            return num(v[i] if i < len(v) else None, lo, hi, rj, f"{ds} {k}")

        wd = g("wind_direction_10m_dominant", 0, 360)
        out = {"rainfall_mm": fmt(g("precipitation_sum", 0, 1500)), "temp_max_c": fmt(g("temperature_2m_max", -10, 55)),
               "temp_min_c": fmt(g("temperature_2m_min", -10, 45)), "humidity_pct": fmt(g("relative_humidity_2m_mean", 0, 100)),
               "wind_speed_kmph": fmt(g("wind_speed_10m_max", 0, 400)),
               "wind_direction": f"{wd:.0f} deg (dominant)" if wd is not None else ""}
        rep.notes.extend(rj[:5])
        return out

    # ---------------------------------------------------------------- coverage, archive, log
    def coverage(self) -> dict[str, dict[str, Any]]:
        wset = set(self.window)
        cov: dict[str, dict[str, Any]] = {}
        for r in self.obs.rows.values():
            t = parse_ts(r["observation_period_start"] if r["provider"] == P_OM else r["observed_at"])
            if not t:
                continue
            c = cov.setdefault(r["station_id"], {"name": r["station_name"], "provider": r["provider"], "days": set()})
            if any(r[x] for x in ("rainfall_mm", "temp_max_c", "temp_min_c", "humidity_pct", "wind_speed_kmph")):
                c["days"].add(t.astimezone(IST).date())
        for sid in self.stations:
            cov.setdefault(sid, {"name": self.stations[sid]["name"], "provider": P_IMD, "days": set()})
        for c in cov.values():
            c["days"] &= wset
            c["missing"] = ranges(wset - c["days"])
        return cov

    def run(self) -> int:
        self.collect_city()
        self.probe("IMD_CITY_TEST_PAGE", CITY_TEST_PAGE, "GET city_weather_test.php?id=43279",
                   lambda t: ("unparsed", "reachable; format not verified, not used"))
        self.probe("IMD_RMC_CHENNAI_HOME", RMC_HOME, "GET mausam.imd.gov.in/chennai/",
                   lambda t: ("no_observation_time", "current-weather block has no observation time; not stored"))
        self.probe("IMD_AWS_ARG", AWS_ARG, "GET aws.imd.gov.in:8091/state/TAMIL_NADU",
                   lambda t: ("access_restricted", "redirects to login + captcha; not bypassed")
                   if re.search(r"LOGIN|url=\.\./\.\./internal|url=internal", t, re.I) else ("unparsed", "format not verified"))
        self.collect_warnings()
        self.collect_nowcast()
        self.collect_open_meteo()
        cutoff = at_ist(self.window[0])
        archived = {s.path.stem: s.archive_before(cutoff) for s in (self.obs, self.fc, self.warn)}
        for s in (self.obs, self.fc, self.warn):
            s.save()
        cov = self.coverage()
        self.write_log(archived, cov)
        self.http.prune_raw(self.today)
        self.summary(cov, archived)
        return 0 if any(r.status in ("ok", "partial") for r in self.reports if r.source.startswith("IMD_")) else 2

    def _previous_success(self) -> dict[str, str]:
        if not self.log_path.exists():
            return {}
        df = pd.read_csv(self.log_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        if "latest_successful_observation_time" not in df:
            return {}
        return df.groupby("source")["latest_successful_observation_time"].max().to_dict()

    def write_log(self, archived: dict[str, int], cov: dict) -> None:
        prev = self._previous_success()
        imd_n = sum(r["provider"] == P_IMD for r in self.obs.rows.values())
        om_n = sum(r["provider"] == P_OM for r in self.obs.rows.values())
        cov_s = "; ".join(f"{sid} {c['name'][:24]}: {len(c['days'])}/{WINDOW_DAYS}" for sid, c in sorted(cov.items()))
        totals = f"obs={len(self.obs.rows)}; forecasts={len(self.fc.rows)}; warnings={len(self.warn.rows)}"
        rows = []
        for r in self.reports:
            ok = r.status in ("ok", "partial")
            last_ok = max(filter(None, [prev.get(r.source, ""), r.latest_obs if ok else ""]), default="")
            rows.append({"run_at": iso(self.run_at), "source": r.source, "retrieval_method": r.method,
                         "fetch_status": r.status, "stale": str(r.status in ("failed", "partial", "network_error",
                                                                             "parse_error") or r.status.startswith("http_5")),
                         "latest_source_observation_time": r.latest_obs, "latest_successful_observation_time": last_ok,
                         "fetched_at": r.fetched_at, "rows_fetched": r.fetched, "rows_inserted": r.inserted,
                         "rows_updated": r.updated, "rows_unchanged": r.unchanged, "rows_archived": "",
                         "corrections": f"{len(r.corrections)}" + (": " + " | ".join(r.corrections[:10]) if r.corrections else ""),
                         "district_boundary_exclusions": f"{len(r.exclusions)}" + (": " + "; ".join(r.exclusions) if r.exclusions else ""),
                         "imd_rows": imd_n, "fallback_rows": om_n, "coverage_90d_per_station": cov_s,
                         "total_rows_after": totals, "notes": " | ".join(r.notes)[:3000]})
        rows.append({"run_at": iso(self.run_at), "source": "ROLLING_WINDOW_ARCHIVE",
                     "retrieval_method": f"rows older than {self.window[0]} moved to *_archive.csv", "fetch_status": "ok",
                     "stale": "False", "fetched_at": iso(now_ist()), "rows_archived": sum(archived.values()),
                     "imd_rows": imd_n, "fallback_rows": om_n, "coverage_90d_per_station": cov_s,
                     "total_rows_after": totals, "notes": "; ".join(f"{k}: {v}" for k, v in archived.items())})
        old = pd.read_csv(self.log_path, dtype=str, keep_default_na=False, encoding="utf-8-sig") \
            if self.log_path.exists() else pd.DataFrame(columns=LOG_COLS)
        df = pd.concat([old.reindex(columns=LOG_COLS), pd.DataFrame(rows, columns=LOG_COLS).astype(str)], ignore_index=True)
        buf = io.StringIO()
        df.to_csv(buf, index=False, lineterminator="\n")
        atomic_write(self.log_path, buf.getvalue())
        self._stale = {r["source"]: r["latest_successful_observation_time"] for r in rows if r["stale"] == "True"}

    def summary(self, cov: dict, archived: dict[str, int]) -> None:
        p = print
        now = now_ist()
        p("\n" + "=" * 80)
        p(f"IMD weather collector - Chennai District | run {iso(self.run_at)} | window {self.window[0]}..{self.window[-1]}")
        p("=" * 80)
        p("Sources:")
        for r in self.reports:
            p(f"  {r.source:<24} {r.status:<20} latest={r.latest_obs or '-':<26} "
              f"ins={r.inserted} upd={r.updated} same={r.unchanged} corr={len(r.corrections)}")
        p("Stale sources: " + (", ".join(f"{s} (last good observation {t or 'never'})" for s, t in self._stale.items())
                               if self._stale else "none"))
        p("\nIMD stations (district boundary check):")
        for sid, s in sorted(self.stations.items()):
            where = f"INSIDE, taluk {s['taluk']} (nearest master point {s['taluk_km']:.2f} km)" if s["inside"] else \
                f"{s['membership'].upper()} {s['dist']:.2f} km outside - context only"
            p(f"  {sid} {s['name']:<22} ({s['lat']}, {s['lon']}) {where}")
        excl = sum(not s["inside"] for s in self.stations.values())
        p(f"  inside district: {len(self.stations) - excl}; excluded as context: {excl}")
        p("\nNewest observation per station:")
        for sid, c in sorted(cov.items()):
            rows = [r for r in self.obs.rows.values() if r["station_id"] == sid]
            if rows:
                r = max(rows, key=lambda x: x["observed_at"])
                vals = ", ".join(f"{k}={r[k]}" for k in ("rainfall_mm", "temp_max_c", "temp_min_c", "humidity_pct",
                                                          "wind_speed_kmph") if r[k])
                p(f"  [{r['provider']}] {c['name'][:40]:<40} {r['observed_at']}  {vals}")
        p("\nCurrent forecasts (live this run):")
        cur_fc = [f for f in self.live_forecasts if parse_ts(f["valid_from"]) <= now < parse_ts(f["valid_to"])]
        for f in cur_fc:
            p(f"  [{f['provider']}] {f['location_id']}: {f['forecast_text']} (valid {f['valid_from']} -> {f['valid_to']})")
        if not cur_fc:
            p("  none fetched live this run")
        p("\nCurrent official warnings, Chennai District (live this run; apply district-wide):")
        cur_w = [w for w in self.live_warnings if parse_ts(w["valid_from"]) <= now < parse_ts(w["valid_to"])]
        for w in cur_w:
            p(f"  [{w['warning_colour'] or 'no colour published'}] {w['warning_text']} "
              f"(valid {w['valid_from']} -> {w['valid_to']}; {w['issuing_authority']})")
        if not cur_w:
            p("  none confirmed current" + (" - WARNING SOURCES STALE" if any("WARN" in s for s in self._stale) else ""))
        p(f"\n{WINDOW_DAYS}-day coverage (days with any value) and missing days:")
        for sid, c in sorted(cov.items()):
            p(f"  [{c['provider']}] {sid} {c['name'][:30]:<30} {len(c['days'])}/{WINDOW_DAYS}  missing: {c['missing'] or 'none'}")
        imd_n = sum(r["provider"] == P_IMD for r in self.obs.rows.values())
        p(f"\nObservation rows: IMD {imd_n} | fallback (Open-Meteo) {len(self.obs.rows) - imd_n}")
        p("IMD's public sources give no rainfall history and ~7 days of temperature history; IMD coverage grows by daily runs.")
        p("\nFiles:")
        for s in (self.obs, self.fc, self.warn):
            p(f"  {s.path}  rows={len(s.rows)}  range={s.date_range()}  archived this run={archived[s.path.stem]}")
        p(f"  {self.log_path}")


# ----------------------------------------------------------------------------- CLI

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="IMD weather observations, forecasts and warnings for Chennai District.")
    ap.add_argument("--backfill-days", type=int, default=WINDOW_DAYS, help="days of the window to (re)attempt (1-90)")
    ap.add_argument("--date", type=date.fromisoformat, help="YYYY-MM-DD: end of the 90-day window (default today IST)")
    ap.add_argument("--out", default=str(SCRIPT_DIR / "data"), help="output folder (default ./data beside the script)")
    ap.add_argument("--boundary", default=str(SCRIPT_DIR.parent / "chennai-grievance-portal-main" / "data" /
                                              "boundaries" / "gcc-wards.geojson"), help="Chennai District boundary GeoJSON")
    ap.add_argument("--location-master", default=str(SCRIPT_DIR.parent / "police_dataset_generator" / "config" /
                                                     "taluks.json"), help="dashboard location master (taluks)")
    ap.add_argument("--open-meteo-ignore-robots", action="store_true",
                    help="call Open-Meteo's documented API although its robots.txt disallows automated access")
    a = ap.parse_args(argv)
    if not 1 <= a.backfill_days <= WINDOW_DAYS:
        ap.error(f"--backfill-days must be 1..{WINDOW_DAYS}")
    if a.date and a.date > now_ist().date():
        ap.error("--date cannot be in the future")
    return a


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    args = parse_args(argv)
    try:
        return Collector(args).run()
    except PermissionError as e:
        print(f"ERROR: cannot write output - is a CSV open in Excel? {e}", file=sys.stderr)
        return 3
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
