#!/usr/bin/env python3
"""CPCB air-quality collector for the Chennai District Collector Intelligence Dashboard.

Owns ONLY air quality: pollutant concentrations, the published Indian-scale AQI, its
published category and prominent pollutant, per CAAQMS station in Chennai District.
Weather, rivers and reservoirs belong to the sibling collectors (IMD, CFM-DSS, CMWSSB).

Sources, as verified on 2026-09-25 (tried in this order):
  1. data.gov.in real-time resource 3b01bcb8-... (requests, key from DATA_GOV_IN_API_KEY).
     Current snapshot only; one row per station x pollutant with min/max/avg and station
     coordinates. The host frequently read-times-out or returns 504.
  2. CPCB CCR portal https://airquality.cpcb.gov.in/ccr/ (playwright). The app's own API
     traffic is AES-encrypted, and every historical view (Advance Search, AQI Data
     Repository, All India AQI Dashboard) is behind a CAPTCHA. We do not defeat either.
     What IS public: the landing-page city map, whose station markers show a hover popup
     "Station: <name> - <agency> / AQI: <n>" and a city panel "Last Updated: <time>".
     That is read from the rendered page only.
  3. Official CSV/Excel download: only exists behind the same CAPTCHA, so history cannot be
     backfilled automatically; missing dates are reported, never invented.
  app.cpcbccr.com/AQI_India now serves a US-AQI page with ads and weather; it is not the
  CPCB Indian-scale feed and is rejected.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import requests

try:  # data.gov.in serves an incomplete chain; the OS trust store completes it (AIA).
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

IST = ZoneInfo("Asia/Kolkata")
WINDOW_DAYS = 90
RECHECK_DAYS = 3
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 20
MIN_GAP_S = 1.5
MAX_BYTES = 5_000_000
MAX_ATTEMPTS = 4

DATAGOV_URL = "https://api.data.gov.in/resource/3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"
DATAGOV_NAME = "data.gov.in CPCB real-time air quality (3b01bcb8)"
CCR_URL = "https://airquality.cpcb.gov.in/ccr/"
CCR_NAME = "CPCB CCR live city map"
CCR_HISTORY_URLS = ["https://airquality.cpcb.gov.in/ccr/#/caaqm-dashboard-all/advance-search",
                    "https://airquality.cpcb.gov.in/ccr/#/repository/aqi"]
CCR_HISTORY_NAME = "CPCB CCR historical (advance search / repository)"
AQI_INDIA_URL = "https://app.cpcbccr.com/AQI_India/"

AQI_FILE = "cpcb_station_aqi_chennai.csv"
POLL_FILE = "cpcb_pollutants_chennai.csv"
COVERAGE_FILE = "cpcb_station_coverage.csv"
RUN_LOG_FILE = "cpcb_air_quality_run_log.csv"

AQI_COLS = ["observation_date", "observation_datetime", "station_id", "station_name", "agency",
            "latitude", "longitude", "aqi", "aqi_category", "prominent_pollutant",
            "averaging_period", "source_name", "source_url", "fetch_method", "fetched_at"]
AQI_KEY = ["station_id", "observation_datetime"]
POLL_COLS = ["observation_date", "observation_datetime", "station_id", "pollutant",
             "concentration", "concentration_unit", "averaging_period", "statistic",
             "source_name", "source_url", "fetch_method", "fetched_at"]
POLL_KEY = ["station_id", "pollutant", "observation_datetime", "averaging_period", "statistic"]
COVERAGE_COLS = ["station_id", "station_name", "agency", "latitude", "longitude", "taluk", "basis"]
RUN_LOG_COLS = ["run_started_at", "source", "http_status", "fetch_method",
                "latest_source_observed_at", "stations_returning_data", "stations_silent",
                "rows_fetched", "inserted", "updated", "unchanged", "corrected",
                "dropped_out_of_scope", "archived", "unit_conversions", "total_rows_after",
                "window_days_with_data"]
# Columns that do not describe the observation itself; a change in them alone is not an update.
BOOKKEEPING = {"fetched_at", "fetch_method", "source_url", "source_name"}

POLLUTANT_ALIASES = {"PM2.5": "PM2.5", "PM25": "PM2.5", "PM10": "PM10", "NO2": "NO2",
                     "SO2": "SO2", "CO": "CO", "OZONE": "O3", "O3": "O3", "NH3": "NH3"}
UNIT = {p: "ug/m3" for p in ("PM2.5", "PM10", "NO2", "SO2", "O3", "NH3")} | {"CO": "mg/m3"}

# From CPCB's official CAAQM station list PDF (caaqms-common/api/reports/caaqm-stations/pdf),
# city = Chennai. The PDF gives no coordinates.
OFFICIAL_CHENNAI_STATIONS = [
    "Alandur Bus Depot, Chennai - CPCB", "Arumbakkam, Chennai - TNPCB",
    "Gandhi Nagar_Ennore, Chennai - TNPCB", "Kodungaiyur, Chennai - TNPCB",
    "Manali, Chennai - CPCB", "Manali Village, Chennai - TNPCB", "Perungudi, Chennai - TNPCB",
    "Royapuram, Chennai - TNPCB", "Velachery Res. Area, Chennai - CPCB"]
TALUK_MATCH_KM = 6.0

log = logging.getLogger("cpcb_aq")


# ---------------------------------------------------------------- small helpers
def now_ist() -> datetime:
    return datetime.now(IST).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(IST).isoformat()


def station_id_for(name: str) -> str:
    """Stable id from the published name, so both sources join on the same station."""
    return "CPCB-" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def split_agency(name: str) -> str:
    m = re.search(r"-\s*([A-Za-z ]+?)\s*$", name)
    return m.group(1).strip() if m else ""


def haversine_km(a_lat, a_lng, b_lat, b_lng) -> float:
    r = math.radians
    d = (math.sin(r(b_lat - a_lat) / 2) ** 2
         + math.cos(r(a_lat)) * math.cos(r(b_lat)) * math.sin(r(b_lng - a_lng) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(d))


def to_float(v):
    try:
        f = float(str(v).strip())
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    os.close(fd)
    try:
        df.to_csv(tmp, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_csv(path: Path, cols: list[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols]


def cache_raw(out: Path, source: str, run_at: datetime, suffix: str, payload: str | bytes) -> Path:
    d = out / "raw" / source / run_at.strftime("%Y%m%d")
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{run_at.strftime('%H%M%S')}_{suffix}"
    p.write_bytes(payload if isinstance(payload, bytes) else payload.encode("utf-8"))
    return p


# ---------------------------------------------------------------- polite HTTP
class PoliteHttp:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/html,*/*"})
        self.last: dict[str, float] = {}
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        host = urlparse(url).netloc
        if host not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.s.get(f"https://{host}/robots.txt", timeout=TIMEOUT)
                ctype = r.headers.get("content-type", "")
                rp.parse(r.text.splitlines() if r.ok and "text/plain" in ctype else [])
            except requests.RequestException:
                rp.parse([])
            self.robots[host] = rp
        return self.robots[host].can_fetch(USER_AGENT, url)

    def _wait_turn(self, host: str) -> None:
        gap = time.monotonic() - self.last.get(host, 0)
        if gap < MIN_GAP_S:
            time.sleep(MIN_GAP_S - gap)
        self.last[host] = time.monotonic()

    def get(self, url: str, params: dict | None = None) -> tuple[str, bytes | None]:
        """Returns (status, body). status is the HTTP code or a timeout/error label."""
        host, status = urlparse(url).netloc, "error"
        for attempt in range(MAX_ATTEMPTS):
            self._wait_turn(host)
            delay = 2 * 2 ** attempt
            try:
                with self.s.get(url, params=params, timeout=TIMEOUT, stream=True) as r:
                    status = str(r.status_code)
                    if r.status_code == 429 or r.status_code >= 500:
                        ra = r.headers.get("Retry-After", "")
                        delay = int(ra) if ra.isdigit() else delay
                        log.warning("%s -> HTTP %s, retry in %ss", host, status, delay)
                    else:
                        body = b""
                        for chunk in r.iter_content(65536):
                            body += chunk
                            if len(body) > MAX_BYTES:
                                return "oversize", None
                        return status, body
            except requests.exceptions.ReadTimeout:
                status = "read-timeout"
                log.warning("%s read timeout (attempt %d)", host, attempt + 1)
            except requests.exceptions.ConnectTimeout:
                status = "connect-timeout"
                log.warning("%s connect timeout (attempt %d)", host, attempt + 1)
            except requests.RequestException as e:
                status = type(e).__name__
                log.warning("%s %s (attempt %d)", host, status, attempt + 1)
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(delay)
        return status, None


# ---------------------------------------------------------------- run bookkeeping
@dataclass
class SourceResult:
    source: str
    fetch_method: str
    http_status: str = ""
    ok: bool = False
    aqi_rows: list[dict] = field(default_factory=list)
    poll_rows: list[dict] = field(default_factory=list)
    stations: dict[str, dict] = field(default_factory=dict)  # station_id -> coverage info
    dropped_out_of_scope: int = 0
    unit_conversions: int = 0
    note: str = ""


# ---------------------------------------------------------------- geography
class LocationMaster:
    def __init__(self, path: Path):
        self.ok = path.exists()
        self.bounds, self.points = None, []
        if not self.ok:
            log.warning("location master %s not found; taluks will be blank", path)
            return
        d = json.loads(path.read_text(encoding="utf-8"))
        self.bounds = d.get("bounds")
        for t in d.get("taluks", []):
            self.points.append((t["name"], t["centroid"]["lat"], t["centroid"]["lng"]))
            for loc in t.get("localities", []):
                self.points.append((t["name"], loc["lat"], loc["lng"]))

    def classify(self, lat, lng) -> tuple[bool | None, str, str]:
        """(in_district, taluk, basis). in_district None = cannot verify."""
        if lat is None or lng is None:
            return None, "", "no published coordinates yet; district membership unverified"
        if not self.bounds:
            return None, "", "no location master; district membership unverified"
        b = self.bounds
        if not (b["min_lat"] <= lat <= b["max_lat"] and b["min_lng"] <= lng <= b["max_lng"]):
            return False, "", "published coordinates outside Chennai District bounds"
        taluk, lat_c, lng_c = min(self.points, key=lambda p: haversine_km(lat, lng, p[1], p[2]))
        km = haversine_km(lat, lng, lat_c, lng_c)
        if km > TALUK_MATCH_KM:
            return True, "", f"inside district bounds; nearest master point {km:.1f} km away, taluk not assigned"
        return True, taluk, f"published coordinates; nearest location-master point in {taluk} ({km:.1f} km)"


# ---------------------------------------------------------------- source 1: data.gov.in
def normalise_co(value: float, pollutant: str, conv: list[str], station: str) -> tuple[float | None, str]:
    """CO published as mg/m3 (<=10 plausible) or ug/m3 (>=200). Between is ambiguous."""
    if pollutant != "CO":
        return value, UNIT[pollutant]
    if value <= 10:
        return value, "mg/m3"
    if value >= 200:
        conv.append(f"{station} CO {value} ug/m3 -> {value / 1000:.3f} mg/m3")
        return round(value / 1000, 4), "mg/m3"
    return None, ""


def fetch_datagov(http: PoliteHttp, out: Path, run_at: datetime, geo: LocationMaster) -> SourceResult:
    res = SourceResult(DATAGOV_NAME, "requests")
    key = os.environ.get("DATA_GOV_IN_API_KEY", "").strip()
    if not key:
        res.http_status = "no-api-key"
        log.error("data.gov.in skipped: DATA_GOV_IN_API_KEY is not set")
        return res
    if not http.allowed(DATAGOV_URL):
        res.http_status = "robots-disallow"
        return res
    records, offset, total, limit = [], 0, None, 10
    while total is None or offset < total:
        params = {"api-key": key, "format": "json", "limit": limit, "offset": offset,
                  "filters[city]": "Chennai"}
        status, body = http.get(DATAGOV_URL, params)
        res.http_status = status
        if body is None:
            log.error("data.gov.in failed at offset %d: %s", offset, status)
            return res  # partial pages are discarded: a half snapshot would look like silent stations
        cache_raw(out, "datagov", run_at, f"offset{offset}.json", body)
        try:
            d = json.loads(body)
        except ValueError:
            res.http_status = f"{status} non-json"
            return res
        page = d.get("records") or []
        records += page
        total = int(d.get("total") or 0)
        limit = int(d.get("limit") or limit)  # the sample key silently caps limit
        if not page:
            break
        offset += len(page)
    res.ok = True
    res.poll_rows, res.stations = parse_datagov(records, run_at, geo, res)
    return res


def parse_datagov(records, run_at, geo, res: SourceResult):
    rows, stations, conversions, dropped = [], {}, [], set()
    for r in records:
        name = (r.get("station") or "").strip()
        pol = POLLUTANT_ALIASES.get((r.get("pollutant_id") or "").strip().upper())
        try:
            obs = datetime.strptime(r.get("last_update", ""), "%d-%m-%Y %H:%M:%S").replace(tzinfo=IST)
        except ValueError:
            log.warning("data.gov.in: bad last_update %r for %s", r.get("last_update"), name)
            continue
        if not name or not pol:
            continue
        lat, lng = to_float(r.get("latitude")), to_float(r.get("longitude"))
        sid = station_id_for(name)
        inside, taluk, basis = geo.classify(lat, lng)
        stations[sid] = {"station_id": sid, "station_name": name, "agency": split_agency(name),
                         "latitude": lat, "longitude": lng, "taluk": taluk,
                         "basis": f"{basis} (data.gov.in)"}
        if inside is False:
            dropped.add(sid)
            continue
        for stat, keys in (("avg", ("avg_value", "pollutant_avg")), ("min", ("min_value", "pollutant_min")),
                           ("max", ("max_value", "pollutant_max"))):
            raw = next((r[k] for k in keys if k in r), None)
            v = to_float(raw)
            if v is None:
                continue
            v, unit = normalise_co(v, pol, conversions, name)
            if v is None:
                log.warning("data.gov.in: %s CO %s %s is neither plausible mg/m3 nor ug/m3; skipped", name, stat, raw)
                continue
            rows.append({"observation_date": obs.date().isoformat(), "observation_datetime": iso(obs),
                         "station_id": sid, "pollutant": pol, "concentration": f"{v:g}",
                         "concentration_unit": unit, "averaging_period": "24h", "statistic": stat,
                         "source_name": DATAGOV_NAME, "source_url": DATAGOV_URL,
                         "fetch_method": "requests", "fetched_at": iso(run_at)})
    for c in conversions:
        log.info("unit conversion: %s", c)
    res.unit_conversions = len(conversions)
    res.dropped_out_of_scope = len(dropped)
    if dropped:
        log.info("data.gov.in: dropped %d out-of-district station(s): %s", len(dropped), sorted(dropped))
    return rows, stations


# ---------------------------------------------------------------- source 2: CCR live map
def open_ccr_chennai(page) -> datetime | None:
    page.goto(CCR_URL, timeout=60_000, wait_until="networkidle")
    page.locator("ng-select").first.click()
    page.keyboard.type("Chennai")
    page.wait_for_timeout(800)
    page.keyboard.press("Enter")
    page.get_by_text("Tamil Nadu,Chennai").first.wait_for(timeout=20_000)
    page.wait_for_timeout(3000)
    txt = page.get_by_text(re.compile(r"Last Updated")).first.inner_text()
    m = re.search(r"(\d{1,2} \w{3} \d{4}),\s*(\d{1,2}:\d{2}\s*[AP]M)", txt)
    if not m:
        return None
    return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d %b %Y %I:%M %p").replace(tzinfo=IST)


def hover_sweep(page, box, step: int) -> dict[str, tuple[float, float]]:
    """Station markers are drawn on the map canvas, so the only way to read them is the
    popup the app shows on hover."""
    found, popup = {}, page.locator(".maplibregl-popup-content p")
    x0, y0, x1, y1 = box
    y = y0
    while y < y1:
        x = x0
        while x < x1:
            page.mouse.move(x, y)
            if popup.count():
                try:
                    t = popup.first.inner_text(timeout=500)
                except Exception:
                    t = ""
                if t and t not in found:
                    found[t] = (x, y)
            x += step
        y += step
    return found


def fetch_ccr_live(out: Path, run_at: datetime, known: set[str]) -> SourceResult:
    res = SourceResult(CCR_NAME, "playwright")
    try:
        from playwright.sync_api import sync_playwright, Error as PwError
    except ImportError:
        res.http_status = "playwright-missing"
        return res
    popups: dict[str, tuple[float, float]] = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT, viewport={"width": 1400, "height": 900})
            obs = open_ccr_chennai(page)
            res.http_status = "200"
            canvas = page.locator(".maplibregl-canvas").first
            canvas.scroll_into_view_if_needed()
            bb = canvas.bounding_box()
            full = (bb["x"] + 4, bb["y"] + 4, bb["x"] + bb["width"] - 4, bb["y"] + bb["height"] - 4)
            popups = hover_sweep(page, full, 10)
            cache_raw(out, "ccr", run_at, "landing.html", page.content())
            # Nearby markers overlap at city zoom; zoom in on each one to expose hidden neighbours.
            for (x, y) in list(popups.values()):
                if len({station_id_for(parse_popup(t)[0] or "") for t in popups} & known) >= len(known):
                    break
                open_ccr_chennai(page)
                page.mouse.dblclick(x, y)
                page.wait_for_timeout(1200)
                page.mouse.dblclick(x, y)
                page.wait_for_timeout(2000)
                bb = canvas.bounding_box()
                box = (max(bb["x"] + 4, x - 110), max(bb["y"] + 4, y - 110),
                       min(bb["x"] + bb["width"] - 4, x + 110), min(bb["y"] + bb["height"] - 4, y + 110))
                for t, pt in hover_sweep(page, box, 7).items():
                    popups.setdefault(t, pt)
            browser.close()
    except PwError as e:
        res.http_status = "playwright-error"
        log.error("CCR live map failed: %s", str(e).splitlines()[0])
        return res
    cache_raw(out, "ccr", run_at, "popups.json",
              json.dumps({"observed": obs and iso(obs), "popups": list(popups)}, indent=1))
    if obs is None:
        res.http_status = "200 no-timestamp"
        log.error("CCR live map: 'Last Updated' time not found; rows not written")
        return res
    res.ok = True
    for text in popups:
        name, aqi = parse_popup(text)
        if not name or "Chennai" not in name:
            continue
        sid = station_id_for(name)
        res.stations[sid] = {"station_id": sid, "station_name": name, "agency": split_agency(name)}
        if aqi is None:
            continue
        res.aqi_rows.append({
            "observation_date": obs.date().isoformat(), "observation_datetime": iso(obs),
            "station_id": sid, "station_name": name, "agency": split_agency(name),
            "latitude": "", "longitude": "", "aqi": str(aqi), "aqi_category": "",
            "prominent_pollutant": "", "averaging_period": "24h", "source_name": CCR_NAME,
            "source_url": CCR_URL, "fetch_method": "playwright", "fetched_at": iso(run_at)})
    return res


def parse_popup(text: str) -> tuple[str | None, int | None]:
    m = re.search(r"Station:\s*(.+?)\s*(?:\n|AQI:)", text)
    a = re.search(r"AQI:\s*(\d+)", text)
    aqi = int(a.group(1)) if a and 0 <= int(a.group(1)) <= 500 else None
    return (m.group(1).strip() if m else None), aqi


# ---------------------------------------------------------------- source 3: CCR history
def probe_ccr_history(out: Path, run_at: datetime) -> SourceResult:
    """History is only offered behind a CAPTCHA. Detect that and stop; never solve it."""
    res = SourceResult(CCR_HISTORY_NAME, "playwright")
    try:
        from playwright.sync_api import sync_playwright, Error as PwError
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            gated = []
            for url in CCR_HISTORY_URLS:
                page.goto(url, timeout=60_000, wait_until="networkidle")
                page.wait_for_timeout(2500)
                if page.get_by_text(re.compile("captcha", re.I)).count():
                    gated.append(url)
            browser.close()
    except (ImportError, PwError) as e:
        res.http_status = "playwright-error"
        log.error("CCR history probe failed: %s", str(e).splitlines()[0])
        return res
    if len(gated) == len(CCR_HISTORY_URLS):
        res.http_status = "captcha-gated"
        log.warning("CCR history is CAPTCHA-gated; backfill/gap-fill not possible automatically")
    else:
        res.http_status = "200 ungated"
        log.warning("CCR history page(s) no longer show a CAPTCHA: %s. Parser not implemented; "
                    "inspect before relying on it.", set(CCR_HISTORY_URLS) - set(gated))
    return res


def check_aqi_india(http: PoliteHttp) -> SourceResult:
    res = SourceResult("app.cpcbccr.com AQI_India", "requests")
    status, body = http.get(AQI_INDIA_URL)
    res.http_status = status
    if body and b"US AQI" in body:
        res.http_status = f"{status} rejected: US-AQI scale"
        log.info("AQI_India page reports US AQI, not the CPCB Indian scale; not used")
    return res


# ---------------------------------------------------------------- merge / window
def upsert(existing: pd.DataFrame, new_rows: list[dict], cols, key, recheck_from: date):
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "corrected": 0}
    if not new_rows:
        return existing, counts
    new = pd.DataFrame(new_rows, columns=cols).fillna("").astype(str)
    new = new.drop_duplicates(subset=key, keep="last")
    idx = {tuple(r): i for i, r in zip(existing.index, existing[key].itertuples(index=False))}
    compare = [c for c in cols if c not in BOOKKEEPING]
    existing = existing.copy()
    additions = []
    for _, row in new.iterrows():
        k = tuple(row[key])
        if k not in idx:
            additions.append(row)
            counts["inserted"] += 1
            continue
        old = existing.loc[idx[k]]
        diff = {c: (old[c], row[c]) for c in compare if str(old[c]) != str(row[c]) and row[c] != ""}
        if not diff:
            counts["unchanged"] += 1
            continue
        for c in cols:
            if row[c] != "":  # never blank out a value the source published earlier
                existing.at[idx[k], c] = row[c]
        if date.fromisoformat(row["observation_date"]) >= recheck_from:
            counts["corrected"] += 1
            log.info("correction %s: %s", k, diff)
        else:
            counts["updated"] += 1
    if additions:
        existing = pd.concat([existing, pd.DataFrame(additions, columns=cols)], ignore_index=True)
    return existing, counts


def split_window(df: pd.DataFrame, start: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = pd.to_datetime(df["observation_date"], errors="coerce").dt.date
    old = d.notna() & (d < start)
    return df[~old], df[old]


def sort_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values(["observation_datetime", "station_id"], kind="stable").reset_index(drop=True)


def archive(out: Path, name: str, rows: pd.DataFrame, cols, key) -> int:
    if rows.empty:
        return 0
    path = out / name.replace(".csv", "_archive.csv")
    merged = pd.concat([read_csv(path, cols), rows], ignore_index=True)
    merged = merged.drop_duplicates(subset=key, keep="last")
    atomic_write_csv(sort_rows(merged), path)
    return len(rows)


def validate(df: pd.DataFrame, key, name: str) -> pd.DataFrame:
    ts = pd.to_datetime(df["observation_datetime"], errors="coerce", utc=True)
    bad = ts.isna() | ~df["station_id"].str.startswith("CPCB-") | (df["source_name"] == "")
    if bad.any():
        log.warning("%s: %d invalid row(s) removed (timestamp/station/source)", name, int(bad.sum()))
    df = df[~bad]
    dup = df.duplicated(subset=key, keep="last")
    if dup.any():
        log.warning("%s: %d duplicate key(s) collapsed", name, int(dup.sum()))
    return df[~dup]


def missing_dates(df: pd.DataFrame, stations: list[str], start: date, end: date) -> dict[str, list[date]]:
    have = {(r.station_id, r.observation_date) for r in df[["station_id", "observation_date"]].itertuples()}
    days = [start + timedelta(n) for n in range((end - start).days + 1)]
    return {s: [d for d in days if (s, d.isoformat()) not in have] for s in stations}


def compress(days: list[date]) -> str:
    if not days:
        return "none"
    spans, a, b = [], days[0], days[0]
    for d in days[1:]:
        if d == b + timedelta(1):
            b = d
        else:
            spans.append((a, b))
            a = b = d
    spans.append((a, b))
    return ", ".join(a.isoformat() if a == b else f"{a.isoformat()}..{b.isoformat()}" for a, b in spans)


def days_with_data(df: pd.DataFrame, start: date, end: date) -> int:
    d = pd.to_datetime(df["observation_date"], errors="coerce").dt.date
    return int(d[(d >= start) & (d <= end)].nunique())


# ---------------------------------------------------------------- coverage
def update_coverage(out: Path, results: list[SourceResult], geo: LocationMaster) -> pd.DataFrame:
    path = out / COVERAGE_FILE
    cov = read_csv(path, COVERAGE_COLS).set_index("station_id", drop=False)
    for name in OFFICIAL_CHENNAI_STATIONS:
        sid = station_id_for(name)
        if sid not in cov.index:
            cov.loc[sid] = [sid, name, split_agency(name), "", "", "",
                            "CPCB CAAQM station list (city Chennai); no published coordinates yet; district membership unverified"]
    for res in results:
        for sid, info in res.stations.items():
            if sid not in cov.index:
                cov.loc[sid] = [sid, info["station_name"], info["agency"], "", "", "",
                                "seen on " + res.source + "; no published coordinates yet"]
            if info.get("latitude") is not None and "latitude" in info:
                for c in ("latitude", "longitude", "taluk", "basis"):
                    cov.at[sid, c] = "" if info[c] is None else str(info[c])
    cov = cov.reset_index(drop=True).sort_values("station_id")
    atomic_write_csv(cov, path)
    return cov


# ---------------------------------------------------------------- main run
def run(args) -> int:
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(out / "cpcb_air_quality_collector.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    run_at = now_ist()
    end = date.fromisoformat(args.date) if args.date else run_at.date()
    start = end - timedelta(WINDOW_DAYS - 1)
    recheck_from = end - timedelta(RECHECK_DAYS - 1)
    geo = LocationMaster(Path(args.location_master))
    log.info("run %s window %s..%s", iso(run_at), start, end)

    aqi = read_csv(out / AQI_FILE, AQI_COLS)
    poll = read_csv(out / POLL_FILE, POLL_COLS)
    first_run = aqi.empty and poll.empty
    known = {station_id_for(n) for n in OFFICIAL_CHENNAI_STATIONS}

    http = PoliteHttp()
    results = [fetch_datagov(http, out, run_at, geo), fetch_ccr_live(out, run_at, known)]
    backfill = first_run or args.backfill_days
    results.append(probe_ccr_history(out, run_at))  # 3-day recheck and gap fill both need history
    results.append(check_aqi_india(http))
    if backfill:
        log.info("backfill requested (%s days); history source status: %s",
                 args.backfill_days or WINDOW_DAYS, results[2].http_status)

    # Coordinates arrive only from data.gov.in; copy them onto AQI rows that lack them.
    cov = update_coverage(out, results, geo)
    coords = {r.station_id: (r.latitude, r.longitude) for r in cov.itertuples()}
    out_of_scope = {r.station_id for r in cov.itertuples() if r.basis.startswith("published coordinates outside")}

    log_rows = []
    for res in results:
        c_tot = {"inserted": 0, "updated": 0, "unchanged": 0, "corrected": 0}
        in_scope = [r for r in res.aqi_rows if r["station_id"] not in out_of_scope]
        if len(in_scope) < len(res.aqi_rows):
            res.dropped_out_of_scope += len(res.aqi_rows) - len(in_scope)
            log.info("%s: dropped %d out-of-district AQI row(s)", res.source, len(res.aqi_rows) - len(in_scope))
        aqi_rows = [r for r in in_scope if r["observation_date"] <= end.isoformat()]
        poll_rows = [r for r in res.poll_rows if r["observation_date"] <= end.isoformat()]
        future = len(in_scope) + len(res.poll_rows) - len(aqi_rows) - len(poll_rows)
        if future:
            log.info("%s: %d row(s) dated after --date %s not written", res.source, future, end)
        for r in aqi_rows:
            r["latitude"], r["longitude"] = coords.get(r["station_id"], ("", ""))
        aqi, c1 = upsert(aqi, aqi_rows, AQI_COLS, AQI_KEY, recheck_from)
        poll, c2 = upsert(poll, poll_rows, POLL_COLS, POLL_KEY, recheck_from)
        for k in c_tot:
            c_tot[k] = c1[k] + c2[k]
        stations = {r["station_id"] for r in aqi_rows + poll_rows}
        latest = max((r["observation_datetime"] for r in aqi_rows + poll_rows), default="")
        if not res.ok:
            prev = [df.loc[df["source_name"] == res.source, "observation_datetime"].max()
                    for df in (aqi, poll) if (df["source_name"] == res.source).any()]
            latest = max(prev) if prev else ""
        log_rows.append({"run_started_at": iso(run_at), "source": res.source,
                         "http_status": res.http_status + ("" if res.ok else " (stale)" if latest else ""),
                         "fetch_method": res.fetch_method, "latest_source_observed_at": latest,
                         "stations_returning_data": len(stations),
                         "stations_silent": len(known - stations) if res.ok else "",
                         "rows_fetched": len(res.aqi_rows) + len(res.poll_rows), **c_tot,
                         "dropped_out_of_scope": res.dropped_out_of_scope, "archived": 0,
                         "unit_conversions": res.unit_conversions})

    aqi, poll = validate(aqi, AQI_KEY, AQI_FILE), validate(poll, POLL_KEY, POLL_FILE)
    aqi, aqi_old = split_window(aqi, start)
    poll, poll_old = split_window(poll, start)
    archived = archive(out, AQI_FILE, aqi_old, AQI_COLS, AQI_KEY) + archive(out, POLL_FILE, poll_old, POLL_COLS, POLL_KEY)
    atomic_write_csv(sort_rows(aqi), out / AQI_FILE)
    atomic_write_csv(sort_rows(poll), out / POLL_FILE)

    totals = f"aqi={len(aqi)}; pollutants={len(poll)}"
    window_days = (f"aqi={days_with_data(aqi, start, end)}/{WINDOW_DAYS}; "
                   f"pollutants={days_with_data(poll, start, end)}/{WINDOW_DAYS}")
    for i, row in enumerate(log_rows):
        row.update(total_rows_after=totals, window_days_with_data=window_days,
                   archived=archived if i == 0 else 0)
    run_log = pd.concat([read_csv(out / RUN_LOG_FILE, RUN_LOG_COLS),
                         pd.DataFrame(log_rows, columns=RUN_LOG_COLS).astype(str)], ignore_index=True)
    atomic_write_csv(run_log, out / RUN_LOG_FILE)

    summarise(aqi, poll, cov, results, log_rows, start, end, known)
    return 0 if any(r.ok for r in results) else 2


def summarise(aqi, poll, cov, results, log_rows, start, end, known) -> None:
    both = pd.concat([aqi[["station_id", "observation_date"]], poll[["station_id", "observation_date"]]])
    names = dict(zip(cov["station_id"], cov["station_name"]))
    print("\n=== CPCB air quality: Chennai District ===")
    print(f"window {start}..{end} | {AQI_FILE}: {len(aqi)} rows | {POLL_FILE}: {len(poll)} rows")
    print(f"stations present: {both['station_id'].nunique()} of {len(known)} in the official Chennai list")
    if not aqi.empty:
        newest = aqi.sort_values("observation_datetime").groupby("station_id").tail(1)
        for r in newest.itertuples():
            print(f"  {r.station_name:<40} AQI {r.aqi:>4}  at {r.observation_datetime}")
    miss = missing_dates(both, sorted(known), start, end)
    print("missing dates per station:")
    for sid, days in miss.items():
        print(f"  {names.get(sid, sid):<40} {len(days):>2}/{WINDOW_DAYS} missing: {compress(days)}")
    stale = [f"{r['source']} [{r['http_status']}]" for r, res in zip(log_rows, results) if not res.ok]
    print("stale / failed sources: " + ("; ".join(stale) if stale else "none"))
    split = pd.concat([aqi["fetch_method"], poll["fetch_method"]]).value_counts().to_dict()
    print("rows by fetch_method: " + ", ".join(f"{k}={split.get(k, 0)}" for k in ("requests", "playwright", "download")))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backfill-days", type=int, default=0, help="attempt to backfill the window (max 90)")
    ap.add_argument("--date", help="run as of YYYY-MM-DD (window ends there)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "data"), help="output directory")
    ap.add_argument("--location-master", default=str(
        Path(__file__).resolve().parent.parent / "police_dataset_generator" / "config" / "taluks.json"),
        help="dashboard location master (taluks.json)")
    args = ap.parse_args()
    if args.date:
        date.fromisoformat(args.date)
    args.backfill_days = min(max(args.backfill_days, 0), WINDOW_DAYS)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
