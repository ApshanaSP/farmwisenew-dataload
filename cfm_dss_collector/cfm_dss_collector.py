#!/usr/bin/env python3
"""CFM-DSS flood-routing collector for the Chennai District Collector Intelligence Dashboard.

Collects river/canal gauge levels (with the authority's warning/danger stages), surplus-site
flows and official alerts/bulletins from the TN WRD Chennai Flood Monitoring DSS
(https://chennaifloodmonitor.tn.gov.in), keeping a rolling 90-day history.

Boundary: rainfall/weather belongs to imd_weather_collector.py and reservoir storage/level to
cmwssb_collector.py. Neither is requested nor written here.

Endpoints verified against live traffic on 2026-09-25 (all return double-encoded JSON):
  GET /Master/GetCsectionAndWaterLevel     25 points: id, displayname, waterlevel, warninglevel,
                                           dangerlevel, maxwaterlevel, maxwaterleveltimestamp.
                                           Current snapshot only.
  GET /Master/GetDangerMaxWaterlevel       5 "critical" points with warning/danger stages.
  GET /Master/GetCriticalWaterLevel?ids=   those points' latest waterlevel + FRDate/FRTime.
  GET /Master/GetAllSubBasinAndPointid     point id -> sub-basin (Adyar/Cooum/Kosasthalayar/Kovalam).
  GET /Master/GetAWLR_AWRSData             sensor list with district (used for relevance).
  GET /Master/GetTankDataByDate            latest daily 6:00 AM tank record; ignores date params.
  GET /Master/GetHistoricalDataForReservoir?reservoirid&startdate&enddate (yyyy-MM-dd)
                                           daily history, max 8 rows per call, no spillway field.
  GET /HomePage/GetPublishAlert            published alerts (sid, linktext, filename).
  GET /HomePage/GetBulletins               bulletins (sid, linktext, date dd/mm/yyyy, filename).
Units: the site's own pages label gauge levels/stages "m"; tank flows are served in cusec (the
dashboard shows the raw value as "cusec" and multiplies by 0.02832 for m3/s).

Known source limitations (reported every run, never papered over):
  * No gauge history endpoint works (GetHistoricalDataForRiver returns 404 for GET and POST);
    GetLast15daysdataforriver returns daily averages, which are not instantaneous readings.
  * No endpoint publishes station coordinates or surplus gates-open counts.
  * Alerts/bulletins publish no validity period.
  * GetRlsData (GCC subway/canal sensors) publishes no unit or datum, so it is not ingested.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlencode

import pandas as pd
import requests

IST = timezone(timedelta(hours=5, minutes=30))
BASE = "https://chennaifloodmonitor.tn.gov.in"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
WINDOW_DAYS = 90
RECHECK_DAYS = 3
MIN_INTERVAL_S = 1.5
TIMEOUT_S = 20
MAX_BYTES = 5_000_000
MAX_RETRIES = 4
HIST_PAGE_ROWS = 8          # GetHistoricalDataForReservoir caps each response at 8 daily rows
STALE_AFTER = timedelta(days=2)  # the site hides its own gauge tab when data is >2 days old

EP_CSECTION = "/Master/GetCsectionAndWaterLevel"
EP_DANGER = "/Master/GetDangerMaxWaterlevel"
EP_CRITICAL = "/Master/GetCriticalWaterLevel"
EP_SUBBASIN = "/Master/GetAllSubBasinAndPointid"
EP_SENSORS = "/Master/GetAWLR_AWRSData"
EP_TANK = "/Master/GetTankDataByDate"
EP_TANK_HIST = "/Master/GetHistoricalDataForReservoir"
EP_ALERT = "/HomePage/GetPublishAlert"
EP_BULLETIN = "/HomePage/GetBulletins"

# Requests the homepage's own JS would fire that fall outside this collector's boundary.
# Blocked in the Playwright context so this collector never calls them.
FORBIDDEN_PATH = re.compile(
    r"Rainfall|Forecast|AWS|ARG|SRG|Temperature|Humidity|WindSpeed|Storage|Visitors|AdminAccess",
    re.I)

GAUGE_COLS = ["station_id", "station_name", "waterbody_name", "basin", "relevance", "latitude",
              "longitude", "observed_at", "water_level", "water_level_unit", "warning_stage",
              "danger_stage", "stage_unit", "source_url", "fetch_method", "fetched_at"]
GATE_COLS = ["gate_site_id", "gate_site_name", "relevance", "latitude", "longitude", "observed_at",
             "gates_open_count", "discharge", "discharge_unit", "inflow", "inflow_unit",
             "source_url", "fetch_method", "fetched_at"]
ALERT_COLS = ["alert_id", "alert_type", "issued_at", "valid_from", "valid_to",
              "covered_location_id", "alert_text", "source_url", "fetch_method", "fetched_at"]
COVERAGE_COLS = ["station_id", "station_name", "station_type", "basin", "latitude", "longitude",
                 "relevance", "affected_taluks", "basis"]
RUNLOG_COLS = ["run_started_at", "endpoint", "http_status", "fetch_method",
               "latest_source_observed_at", "fetched_at", "rows_fetched", "inserted", "updated",
               "unchanged", "corrected", "dropped_out_of_scope", "archived", "total_rows_after",
               "window_days_with_data"]
PROVENANCE = {"source_url", "fetch_method", "fetched_at"}

# CFM-DSS gauge point id -> sensor id in GetAWLR_AWRSData (matched by station name; the two
# lists spell names differently). The district is read live from that sensor record.
POINT_TO_SENSOR = {
    100: "AWLR0044", 103: "AWLR0042", 104: "AWLR0043", 107: "AWLR0029", 111: "AWLR0032",
    112: "AWLR0034", 115: "AWLR0065", 117: "AWLR0001", 119: "AWLR0016", 120: "AWLR0023",
    121: "AWLR0026", 125: "AWLR0050", 126: "AWLR0049", 127: "AWLR0046", 130: "AWLR0013",
    131: "AWLR0021", 132: "AWLR0030", 133: "AWLR0031", 134: "AWLR0035", 135: "AWLR0039",
    136: "AWLR0047", 137: "AWLR0057", 138: "AWLR0061",
}
# Points absent from the sensor list, located from documented administrative geography.
MANUAL_DISTRICT = {
    118: ("Chennai", "Manali is in Chennai district (GCC Zone 2); not in CFM-DSS sensor list"),
    124: ("Chennai", "Sholinganallur taluk is in Chennai district; not in CFM-DSS sensor list"),
    98: ("Chengalpattu", "Perungalathur is in Chengalpattu district; not in CFM-DSS sensor list"),
}
# These rivers reach the sea inside Chennai, so any point on them outside the district is upstream.
CHENNAI_OUTFALL_BASINS = {"Adyar", "Cooum", "Kosasthalayar"}

GATE_SITES = {
    "TNCH-07-T0726": {"sensor": "AWLR0006", "basis": "Chembarambakkam lake surplus feeds the "
                      "Adyar through Chennai; CFM-DSS sensor list places the lake outside Chennai"},
    "RD001": {"sensor": "AWLR0005", "basis": "Red Hills (Puzhal) surplus channel flows into "
              "Chennai (see gauge 138, Burma Nagar); CFM-DSS sensor list places the lake outside "
              "Chennai"},
}

CANDIDATES_NOT_PUBLISHED = [
    "Adyar at Nandambakkam (sensor AWLR0041 listed, but no level published)",
    "Cooum at Chetpet (no such gauge published)",
    "Cooum at Choolaimedu (no such gauge published)",
]
LIMITATIONS = [
    "gauge history: no working historical endpoint; only current snapshots are collected",
    "coordinates: not published by any accessible endpoint (latitude/longitude left empty)",
    "gates_open_count: not published for any site",
    "gate discharge (spillway) is only in the live daily record; history has inflow only",
    "alert validity: no valid_from/valid_to published",
]

log = logging.getLogger("cfm_dss")


# ---------------------------------------------------------------- time helpers

def now_ist() -> datetime:
    return datetime.now(IST).replace(microsecond=0)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST).isoformat(timespec="seconds")


def parse_local(text: str | None, *fmts: str) -> datetime | None:
    text = (text or "").strip()
    for fmt in fmts:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def day_of(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def num(value) -> str:
    """Return the published number as text, or '' if it is not numeric."""
    if value is None or isinstance(value, bool):
        return ""
    try:
        float(value)
    except (TypeError, ValueError):
        return ""
    return str(value).strip()


@dataclass(frozen=True)
class Window:
    start: date
    end: date

    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]


# ---------------------------------------------------------------- fetching

@dataclass
class Fetched:
    url: str
    status: int | None
    method: str
    data: list | None = None
    error: str = ""


class Fetcher:
    """One request at a time, polite pacing; requests first, Playwright after a WAF 403."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA, "Referer": BASE + "/", "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01"})
        self.requests_blocked = False
        self._last = 0.0
        self._pw = self._browser = self._page = None
        self._robots = self._load_robots()
        self.method_counts = {"requests": 0, "playwright": 0}

    def _load_robots(self) -> robotparser.RobotFileParser | None:
        try:
            self._throttle()
            r = self.session.get(BASE + "/robots.txt", timeout=TIMEOUT_S)
        except requests.RequestException as exc:
            log.warning("robots.txt unreadable via requests (%s); proceeding with public XHR "
                        "endpoints", type(exc).__name__)
            return None
        if r.status_code != 200 or "<html" in r.text[:200].lower():
            log.info("robots.txt not published (HTTP %s); no crawl restrictions declared",
                     r.status_code)
            return None
        rp = robotparser.RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp

    def _throttle(self) -> None:
        wait = MIN_INTERVAL_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get(self, path: str, params: dict | None = None) -> Fetched:
        url = BASE + path + ("?" + urlencode(params) if params else "")
        if self._robots and not self._robots.can_fetch(UA, url):
            return Fetched(url, None, "", error="disallowed by robots.txt")
        if not self.requests_blocked:
            status, text, err = self._with_retries(self._via_requests, url)
            # The server omits its intermediate certificate: browsers recover via AIA,
            # requests cannot. Verification is never disabled; the browser path is used instead.
            if status == 403 or (status is None and "SSLError" in err):
                self.requests_blocked = True
                log.info("%s via requests on %s; switching to Playwright",
                         "HTTP 403" if status == 403 else "TLS chain unverifiable", path)
            else:
                return self._finish(url, status, text, err, "requests")
        status, text, err = self._with_retries(self._via_playwright, url)
        return self._finish(url, status, text, err, "playwright")

    def _with_retries(self, fn, url: str):
        for attempt in range(MAX_RETRIES + 1):
            self._throttle()
            status, text, err, retry_after = fn(url)
            if status not in (429, 500, 502, 503, 504) or attempt == MAX_RETRIES:
                return status, text, err
            delay = min(60.0, 2.0 * 2 ** attempt)
            if retry_after and retry_after.strip().isdigit():
                delay = max(delay, float(retry_after))
            log.info("HTTP %s on %s; retrying in %.0fs", status, url, delay)
            time.sleep(delay)
        return None, None, "retries exhausted"

    def _via_requests(self, url: str):
        try:
            with self.session.get(url, timeout=TIMEOUT_S, stream=True) as r:
                body = b""
                for chunk in r.iter_content(65536):
                    body += chunk
                    if len(body) > MAX_BYTES:
                        return r.status_code, None, "response exceeds size cap", None
                return (r.status_code, body.decode(r.encoding or "utf-8", "replace"), "",
                        r.headers.get("Retry-After"))
        except requests.RequestException as exc:
            return None, None, f"{type(exc).__name__}: {exc}", None

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        ctx = self._browser.new_context(user_agent=UA, locale="en-IN", timezone_id="Asia/Kolkata")
        ctx.route("**/*", _route_filter)
        self._page = ctx.new_page()
        self._page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60_000)
        self._page.wait_for_timeout(2000)  # let session cookies / anti-forgery token settle
        return self._page

    def _via_playwright(self, url: str):
        try:
            page = self._ensure_page()
            res = page.evaluate(_FETCH_JS, [url, MAX_BYTES, TIMEOUT_S * 1000])
        except Exception as exc:  # playwright raises several unrelated error types
            return None, None, f"playwright: {exc}".splitlines()[0], None
        if res.get("error"):
            return None, None, res["error"], None
        if res.get("text") is None:
            return res["status"], None, "response exceeds size cap", None
        return res["status"], res["text"], "", res.get("retry")

    def _finish(self, url, status, text, err, method) -> Fetched:
        self._cache(url, status, text, method)
        out = Fetched(url, status, method, error=err)
        if status != 200 or text is None:
            out.error = err or f"HTTP {status}"
            return out
        try:
            out.data = decode_payload(text)
            self.method_counts[method] += 1
        except ValueError as exc:
            out.error = str(exc)
        return out

    def _cache(self, url, status, text, method) -> None:
        """Raw responses are kept for audit/re-parsing only; they are never read back as live data."""
        stamp = now_ist()
        folder = self.cache_dir / stamp.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "_", url.removeprefix(BASE))[:120]
        record = {"url": url, "fetched_at": iso(stamp), "fetch_method": method,
                  "http_status": status, "body": text}
        path = folder / f"{stamp:%H%M%S}_{time.monotonic_ns() % 10**6:06d}_{slug}.json"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    def close(self) -> None:
        for closer in (self._browser, self._pw):
            try:
                if closer is not None:
                    closer.close() if closer is self._browser else closer.stop()
            except Exception:
                pass


_FETCH_JS = """async ([u, maxBytes, timeoutMs]) => {
  const c = new AbortController(); const t = setTimeout(() => c.abort(), timeoutMs);
  try {
    const r = await fetch(u, {credentials: 'same-origin', signal: c.signal, headers: {
      'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json, text/javascript, */*; q=0.01'}});
    const txt = await r.text();
    return {status: r.status, text: txt.length > maxBytes ? null : txt, retry: r.headers.get('retry-after')};
  } catch (e) { return {error: String(e)}; } finally { clearTimeout(t); }
}"""


def _route_filter(route) -> None:
    req = route.request
    if req.resource_type in ("image", "media", "font") or FORBIDDEN_PATH.search(req.url):
        route.abort()
    else:
        route.continue_()


def decode_payload(text: str) -> list:
    """CFM-DSS returns JSON that is usually a JSON string wrapping a JSON array."""
    if text.lstrip()[:1] == "<":
        raise ValueError("HTML returned instead of JSON")
    try:
        value = json.loads(text)
        if isinstance(value, str):
            value = json.loads(value) if value.strip() else []
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"unexpected payload type {type(value).__name__}")
    return value


# ---------------------------------------------------------------- storage

@dataclass(frozen=True)
class Dataset:
    name: str
    filename: str
    columns: list
    key: tuple
    time_col: str
    id_col: str


DATASETS = {
    "gauge": Dataset("gauge", "cfm_dss_gauge_observations_chennai.csv", GAUGE_COLS,
                     ("station_id", "observed_at"), "observed_at", "station_id"),
    "gate": Dataset("gate", "cfm_dss_gate_operations_chennai.csv", GATE_COLS,
                    ("gate_site_id", "observed_at"), "observed_at", "gate_site_id"),
    "alert": Dataset("alert", "cfm_dss_alerts_chennai.csv", ALERT_COLS,
                     ("alert_id", "issued_at"), "issued_at", "alert_id"),
}


def read_csv(path: Path, columns: list) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns)
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    return df[columns]


def write_csv_atomic(df: pd.DataFrame, path: Path) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.stem, suffix=".tmp")
    os.close(fd)
    try:
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


class Store:
    """Main + archive CSVs for one dataset, held in memory as key -> row."""

    def __init__(self, ds: Dataset, out: Path):
        self.ds = ds
        self.main_path = out / ds.filename
        self.archive_path = out / f"{Path(ds.filename).stem}_archive.csv"
        self.existed = self.main_path.exists() or self.archive_path.exists()
        archive = read_csv(self.archive_path, ds.columns).to_dict("records")
        main = read_csv(self.main_path, ds.columns).to_dict("records")
        self.initial_main = {self.key(r) for r in main}
        self.rows = {self.key(r): r for r in archive + main}

    def key(self, row: dict) -> tuple:
        return tuple(row.get(k, "") for k in self.ds.key)

    def upsert(self, rows: list[dict]) -> dict:
        stats = {"inserted": 0, "updated": 0, "unchanged": 0, "corrected": 0}
        for new in rows:
            new = {c: ("" if new.get(c) is None else str(new.get(c))) for c in self.ds.columns}
            k = self.key(new)
            old = self.rows.get(k)
            if old is None:
                self.rows[k] = new
                stats["inserted"] += 1
                continue
            merged, changed, corrected = dict(old), False, []
            for col in self.ds.columns:
                if col in PROVENANCE or new[col] == "" or new[col] == old[col]:
                    continue  # an empty re-fetch never erases a published value
                if old[col] != "":
                    corrected.append(f"{col}: {old[col]!r} -> {new[col]!r}")
                merged[col], changed = new[col], True
            if not changed:
                stats["unchanged"] += 1
                continue
            for col in PROVENANCE:
                merged[col] = new[col]
            self.rows[k] = merged
            stats["updated"] += 1
            if corrected:
                stats["corrected"] += 1
                log.info("correction %s %s: %s", self.ds.name, k, "; ".join(corrected))
        return stats

    def split(self, window: Window) -> tuple[list[dict], list[dict]]:
        main, archive = [], []
        for row in self.rows.values():
            d = day_of(row[self.ds.time_col])
            (archive if d is not None and d < window.start else main).append(row)
        return main, archive

    def days_with_data(self, window: Window) -> int:
        days = {day_of(r[self.ds.time_col]) for r in self.rows.values()}
        return sum(1 for d in window.days() if d in days)

    def dates_for(self, id_value: str) -> set[date]:
        return {day_of(r[self.ds.time_col]) for r in self.rows.values()
                if r[self.ds.id_col] == id_value}

    def write(self, window: Window) -> int:
        main, archive = self.split(window)
        archived = len(self.initial_main & {self.key(r) for r in archive})
        order = [self.ds.time_col, self.ds.id_col]
        for rows, path in ((main, self.main_path), (archive, self.archive_path)):
            if not rows and not path.exists() and path is self.archive_path:
                continue
            df = pd.DataFrame(rows, columns=self.ds.columns).sort_values(order, kind="stable")
            write_csv_atomic(df, path)
        return archived


# ---------------------------------------------------------------- run bookkeeping

@dataclass
class EndpointLog:
    endpoint: str
    dataset: str | None
    statuses: list = field(default_factory=list)
    methods: set = field(default_factory=set)
    latest: str = ""
    rows_fetched: int = 0
    dropped: int = 0
    stats: dict = field(default_factory=lambda: dict.fromkeys(
        ("inserted", "updated", "unchanged", "corrected"), 0))
    errors: list = field(default_factory=list)

    def record(self, res: Fetched) -> None:
        self.statuses.append(str(res.status) if res.status is not None else "error")
        if res.method:
            self.methods.add(res.method)
        if res.error:
            self.errors.append(res.error)
            log.warning("%s failed: %s", res.url, res.error)

    def add(self, stats: dict, rows: list[dict], time_col: str) -> None:
        for k, v in stats.items():
            self.stats[k] += v
        self.rows_fetched += len(rows)
        times = [r[time_col] for r in rows if r.get(time_col)]
        self.latest = max([self.latest, *times])


class Run:
    def __init__(self, out: Path, window: Window, backfill_days: int | None):
        self.out = out
        self.window = window
        self.backfill_days = backfill_days
        self.started = now_ist()
        self.fetcher = Fetcher(out / "raw_cache")
        self.stores = {name: Store(ds, out) for name, ds in DATASETS.items()}
        self.coverage = {r["station_id"]: r for r in
                         read_csv(out / "cfm_dss_station_coverage.csv", COVERAGE_COLS)
                         .to_dict("records")}
        self.logs: dict[str, EndpointLog] = {}
        self.history_gaps: list[str] = []
        self.sensors: dict[str, tuple[str, str]] | None = None

    def fetch(self, endpoint: str, dataset: str | None, params: dict | None = None) -> Fetched:
        entry = self.logs.setdefault(endpoint, EndpointLog(endpoint, dataset))
        res = self.fetcher.get(endpoint, params)
        entry.record(res)
        return res

    def ingest(self, endpoint: str, dataset: str, rows: list[dict], dropped: int = 0) -> None:
        store = self.stores[dataset]
        entry = self.logs[endpoint]
        entry.add(store.upsert(rows), rows, store.ds.time_col)
        entry.dropped += dropped
        if dropped:
            log.info("%s: dropped %d out-of-scope records", endpoint, dropped)


# ---------------------------------------------------------------- station coverage

def build_gauge_coverage(run: Run, points: dict[int, str]) -> None:
    basins = {}
    res = run.fetch(EP_SUBBASIN, None)
    for rec in res.data or []:
        basins[int(rec["pointid"])] = (rec.get("subbasin_name") or "").strip()
    sensors = fetch_sensor_districts(run)
    for pid, name in points.items():
        sid = f"CFM-{pid}"
        prev = run.coverage.get(sid, {})
        basin = basins.get(pid) or prev.get("basin", "")
        district, basis = locate_point(pid, sensors)
        if district is None and prev:
            run.coverage[sid] = {**prev, "station_name": name}
            continue
        relevance, why = classify(district, basin)
        run.coverage[sid] = {
            "station_id": sid, "station_name": name, "station_type": "river_gauge",
            "basin": basin, "latitude": "", "longitude": "", "relevance": relevance,
            "affected_taluks": "", "basis": f"{basis}; {why}"}


def fetch_sensor_districts(run: Run) -> dict[str, tuple[str, str]]:
    if run.sensors is None:
        res = run.fetch(EP_SENSORS, None)
        run.sensors = {r["awlrid"]: ((r.get("district") or "").strip(),
                                      (r.get("stationname") or "").strip())
                        for r in res.data or [] if r.get("awlrid")}
    return run.sensors


def locate_point(pid: int, sensors: dict) -> tuple[str | None, str]:
    sensor = POINT_TO_SENSOR.get(pid)
    if sensor and sensor in sensors:
        district, name = sensors[sensor]
        return district, f"district '{district}' from CFM-DSS sensor {sensor} ({name})"
    if pid in MANUAL_DISTRICT:
        return MANUAL_DISTRICT[pid]
    return None, "district not published by CFM-DSS"


def classify(district: str | None, basin: str) -> tuple[str, str]:
    if district == "Chennai":
        return "inside_chennai", "inside Chennai district"
    if basin in CHENNAI_OUTFALL_BASINS:
        return "upstream", f"{basin} outfalls to the sea within Chennai"
    return "", f"{basin or 'unknown'} basin point outside Chennai does not drain through Chennai"


def build_gate_coverage(run: Run, names: dict[str, str]) -> None:
    sensors = fetch_sensor_districts(run)
    for site_id, name in names.items():
        meta = GATE_SITES[site_id]
        district, sname = sensors.get(meta["sensor"], ("", ""))
        where = f"district '{district}' from CFM-DSS sensor {meta['sensor']} ({sname}); " \
            if district else ""
        run.coverage[site_id] = {
            "station_id": site_id, "station_name": name, "station_type": "gate_site",
            "basin": "", "latitude": "", "longitude": "", "relevance": "upstream",
            "affected_taluks": "", "basis": where + meta["basis"]}


def write_coverage(run: Run) -> None:
    rows = sorted((r for r in run.coverage.values() if r["relevance"]), key=lambda r: (r["station_type"], r["station_id"]))
    write_csv_atomic(pd.DataFrame(rows, columns=COVERAGE_COLS),
                     run.out / "cfm_dss_station_coverage.csv")


# ---------------------------------------------------------------- gauges

WATERBODY = re.compile(r"(?:across|,)\s+(?:the\s+)?([A-Z][\w .'-]*?(?:River|Canal|Nullah|Channel|"
                       r"drain|Creek|Odai))\b", re.I)


def gauge_row(run: Run, pid: int, observed: datetime, level, warning, danger,
              url: str, method: str, fetched: str) -> dict | None:
    cov = run.coverage.get(f"CFM-{pid}")
    if not cov or not cov["relevance"] or num(level) == "":
        return None
    match = WATERBODY.search(cov["station_name"])
    return {
        "station_id": cov["station_id"], "station_name": cov["station_name"],
        "waterbody_name": match.group(1).strip() if match else "", "basin": cov["basin"],
        "relevance": cov["relevance"], "latitude": cov["latitude"], "longitude": cov["longitude"],
        "observed_at": iso(observed), "water_level": num(level), "water_level_unit": "m",
        "warning_stage": num(warning), "danger_stage": num(danger),
        "stage_unit": "m" if num(warning) or num(danger) else "",
        "source_url": url, "fetch_method": method, "fetched_at": fetched}


def collect_gauges(run: Run) -> None:
    csec = run.fetch(EP_CSECTION, "gauge")
    danger = run.fetch(EP_DANGER, "gauge")
    points = {int(r["id"]): (r.get("displayname") or r.get("name") or "").strip()
              for r in (csec.data or []) + (danger.data or []) if r.get("id") is not None}
    if points:
        build_gauge_coverage(run, points)
    if csec.data is not None:
        rows, dropped = csection_rows(run, csec)
        run.ingest(EP_CSECTION, "gauge", rows, dropped)
    if danger.data:
        crit = run.fetch(EP_CRITICAL, "gauge",
                         {"ids": ",".join(str(r["id"]) for r in danger.data)})
        if crit.data is not None:
            rows, dropped = critical_rows(run, crit, danger.data)
            run.ingest(EP_CRITICAL, "gauge", rows, dropped)


def csection_rows(run: Run, res: Fetched) -> tuple[list[dict], int]:
    fetched, rows, dropped, untimed = iso(now_ist()), [], 0, 0
    for rec in res.data:
        pid = int(rec["id"])
        observed = parse_local(rec.get("maxwaterleveltimestamp"), "%Y-%m-%d %H:%M:%S")
        # The feed only timestamps the max reading; accept waterlevel only when it IS that reading.
        same = num(rec.get("maxwaterlevel")) and num(rec.get("waterlevel")) and \
            abs(float(rec["maxwaterlevel"]) - float(rec["waterlevel"])) < 1e-6
        if observed is None or not same:
            untimed += 1
            continue
        row = gauge_row(run, pid, observed, rec.get("waterlevel"), rec.get("warninglevel"),
                        rec.get("dangerlevel"), res.url, res.method, fetched)
        if row is None:
            dropped += 1
        elif validate_time(observed):
            rows.append(row)
    if untimed:
        log.info("%s: skipped %d readings with no verifiable observation time", EP_CSECTION, untimed)
    return rows, dropped


def critical_rows(run: Run, res: Fetched, stages: list[dict]) -> tuple[list[dict], int]:
    fetched, rows, dropped = iso(now_ist()), [], 0
    by_id = {int(s["id"]): s for s in stages}
    for rec in res.data:
        pid = int(rec.get("pointid") or 0)
        observed = parse_local(f"{rec.get('FRDate', '')} {rec.get('FRTime', '')}",
                               "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")
        stage = by_id.get(pid)
        if observed is None or stage is None or not validate_time(observed):
            continue
        row = gauge_row(run, pid, observed, rec.get("waterlevel"), stage.get("warninglevel"),
                        stage.get("dangerlevel"), res.url, res.method, fetched)
        if row is None:
            dropped += 1
        else:
            rows.append(row)
    return rows, dropped


def validate_time(observed: datetime) -> bool:
    if observed.replace(tzinfo=IST) > now_ist() + timedelta(hours=1):
        log.warning("rejected future observation time %s", observed)
        return False
    return True


# ---------------------------------------------------------------- gate sites

def gate_row(run: Run, site_id: str, observed: datetime, inflow, discharge,
             url: str, method: str, fetched: str) -> dict:
    cov = run.coverage[site_id]
    return {
        "gate_site_id": site_id, "gate_site_name": cov["station_name"],
        "relevance": cov["relevance"], "latitude": cov["latitude"],
        "longitude": cov["longitude"], "observed_at": iso(observed), "gates_open_count": "",
        "discharge": num(discharge), "discharge_unit": "cusec" if num(discharge) else "",
        "inflow": num(inflow), "inflow_unit": "cusec" if num(inflow) else "",
        "source_url": url, "fetch_method": method, "fetched_at": fetched}


def tank_time(rec: dict) -> datetime | None:
    day = parse_local((rec.get("date") or "")[:10], "%Y-%m-%d")
    clock = parse_local(rec.get("time"), "%I:%M %p", "%H:%M", "%H:%M:%S")
    if day is None or clock is None:
        return None
    return datetime.combine(day.date(), clock.time())


def collect_gates(run: Run) -> None:
    res = run.fetch(EP_TANK, "gate")
    if res.data is None:
        return
    names = {r["tankid"]: r.get("tankname", "").strip() for r in res.data
             if r.get("tankid") in GATE_SITES}
    build_gate_coverage(run, names)
    fetched, rows = iso(now_ist()), []
    for rec in res.data:
        observed = tank_time(rec)
        if rec.get("tankid") not in GATE_SITES or observed is None or not validate_time(observed):
            continue
        # outflow_spillway is the surplus discharge; outflow_total also includes water supply.
        rows.append(gate_row(run, rec["tankid"], observed, rec.get("inflow_total"),
                             rec.get("outflow_spillway"), res.url, res.method, fetched))
    run.ingest(EP_TANK, "gate", rows, len(res.data) - len(rows))


def history_dates_needed(run: Run, site_id: str) -> list[date]:
    store, w = run.stores["gate"], run.window
    if run.backfill_days or not store.existed:
        span = min(run.backfill_days or WINDOW_DAYS, WINDOW_DAYS)
        return [w.end - timedelta(days=i) for i in range(span)][::-1]
    have = store.dates_for(site_id)
    recheck = {w.end - timedelta(days=i) for i in range(RECHECK_DAYS)}
    return sorted({d for d in w.days() if d not in have} | recheck)


def contiguous(days: list[date]) -> list[tuple[date, date]]:
    runs: list[tuple[date, date]] = []
    for d in days:
        if runs and d - runs[-1][1] == timedelta(days=1):
            runs[-1] = (runs[-1][0], d)
        else:
            runs.append((d, d))
    return runs


def collect_gate_history(run: Run) -> None:
    for site_id in GATE_SITES:
        if site_id not in run.coverage:
            continue
        for start, end in contiguous(history_dates_needed(run, site_id)):
            fetch_history_range(run, site_id, start, end)
        missing = [d for d in run.window.days() if d not in run.stores["gate"].dates_for(site_id)]
        if missing:
            run.history_gaps.append(f"gate {site_id}: {len(missing)} window days not published "
                                    f"(first {missing[0]}, last {missing[-1]})")


def fetch_history_range(run: Run, site_id: str, start: date, end: date) -> None:
    cursor = start
    while cursor <= end:
        res = run.fetch(EP_TANK_HIST, "gate", {"reservoirid": site_id,
                                               "startdate": cursor.isoformat(),
                                               "enddate": end.isoformat()})
        if res.data is None:
            return
        fetched, rows, days = iso(now_ist()), [], []
        for rec in res.data:
            observed = tank_time(rec)
            if rec.get("tankid") != site_id or observed is None or not validate_time(observed):
                continue
            days.append(observed.date())
            rows.append(gate_row(run, site_id, observed, rec.get("inflow_total"), None,
                                 res.url, res.method, fetched))
        run.ingest(EP_TANK_HIST, "gate", rows)
        if len(res.data) < HIST_PAGE_ROWS or not days or max(days) < cursor:
            return
        cursor = max(days) + timedelta(days=1)


# ---------------------------------------------------------------- alerts

def issued_value(rec: dict) -> str:
    for key in ("date", "publishdate", "publish_date", "alertdate", "createddate", "issued_on"):
        raw = (rec.get(key) or "").strip() if isinstance(rec.get(key), str) else ""
        if not raw:
            continue
        stamp = parse_local(raw, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S",
                            "%d-%m-%Y %H:%M:%S")
        if stamp is not None and stamp.time() != datetime.min.time():
            return iso(stamp)
        day = parse_local(raw[:10], "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")
        if day is not None:
            return day.date().isoformat()  # the source publishes a date only; no time is invented
    return ""


def collect_alerts(run: Run) -> None:
    for endpoint, kind in ((EP_ALERT, "alert"), (EP_BULLETIN, "bulletin")):
        res = run.fetch(endpoint, "alert")
        if res.data is None:
            continue
        fetched, rows = iso(now_ist()), []
        for rec in res.data:
            text = rec.get("linktext")
            if rec.get("sid") is None or not text:
                continue
            # CFM-DSS is Chennai's own flood system; every item it publishes covers Chennai.
            rows.append({
                "alert_id": f"{kind}-{rec['sid']}", "alert_type": kind,
                "issued_at": issued_value(rec), "valid_from": "", "valid_to": "",
                "covered_location_id": "", "alert_text": text, "source_url": res.url,
                "fetch_method": res.method, "fetched_at": fetched})
        run.ingest(endpoint, "alert", rows, len(res.data) - len(rows))


# ---------------------------------------------------------------- outputs

def write_run_log(run: Run, archived: dict[str, int]) -> None:
    path = run.out / "cfm_dss_run_log.csv"
    fetched = iso(now_ist())
    rows = []
    for entry in run.logs.values():
        ds = entry.dataset
        store = run.stores.get(ds) if ds else None
        latest = entry.latest or (latest_time(store) if store else "")
        rows.append({
            "run_started_at": iso(run.started), "endpoint": entry.endpoint,
            "http_status": ",".join(sorted(set(entry.statuses))),
            "fetch_method": ",".join(sorted(entry.methods)),
            "latest_source_observed_at": latest, "fetched_at": fetched,
            "rows_fetched": entry.rows_fetched, **entry.stats,
            "dropped_out_of_scope": entry.dropped,
            "archived": archived.get(ds, 0) if ds else "",
            "total_rows_after": len(store.split(run.window)[0]) if store else "",
            "window_days_with_data": store.days_with_data(run.window) if store else ""})
    df = pd.concat([read_csv(path, RUNLOG_COLS), pd.DataFrame(rows, columns=RUNLOG_COLS)])
    write_csv_atomic(df, path)


def latest_time(store: Store) -> str:
    return max((r[store.ds.time_col] for r in store.rows.values()), default="")


def latest_per_id(store: Store) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for row in store.rows.values():
        cur = best.get(row[store.ds.id_col])
        if cur is None or row[store.ds.time_col] > cur[store.ds.time_col]:
            best[row[store.ds.id_col]] = row
    return best


def age_label(stamp: str) -> str:
    try:
        dt = datetime.fromisoformat(stamp)
    except ValueError:
        dt = datetime.combine(date.fromisoformat(stamp[:10]), datetime.min.time(), IST)
    days = (now_ist() - dt).days
    return f"{days} d old" if days else "today"


def print_summary(run: Run) -> None:
    w = run.window
    p = print
    p("\n=== CFM-DSS collector summary ===")
    p(f"output dir     : {run.out}")
    p(f"window         : {w.start} .. {w.end} ({WINDOW_DAYS} days, Asia/Kolkata)")
    for name, store in run.stores.items():
        main, archive = store.split(w)
        p(f"{name:<15}: {len(main)} rows in window, {len(archive)} archived, "
          f"{WINDOW_DAYS - store.days_with_data(w)} of {WINDOW_DAYS} days with no data")
    gauge_main = {r["station_id"] for r in run.stores["gauge"].split(w)[0]}
    p(f"stations       : {len(run.coverage)} in coverage file, {len(gauge_main)} gauges with "
      f"readings in window")
    p("latest readings (all history):")
    stale = []
    for store, fields in ((run.stores["gauge"], ("water_level", "water_level_unit")),
                          (run.stores["gate"], ("inflow", "inflow_unit"))):
        for sid, row in sorted(latest_per_id(store).items()):
            age = age_label(row["observed_at"])
            name = row.get("station_name") or row.get("gate_site_name")
            extra = (f" warn {row['warning_stage']} danger {row['danger_stage']}"
                     if row.get("warning_stage") else "")
            if row.get("discharge"):
                extra = f" discharge {row['discharge']} {row['discharge_unit']}"
            p(f"  {sid:<14} {name[:44]:<44} {row['observed_at']} ({age}) "
              f"{row[fields[0]]} {row[fields[1]]}{extra}")
            if now_ist() - datetime.fromisoformat(row["observed_at"]) > STALE_AFTER:
                stale.append(sid)
    alerts = latest_per_id(run.stores["alert"])
    newest = max(alerts.values(), key=lambda r: r["issued_at"], default=None)
    if newest:
        p(f"latest alert   : [{newest['alert_type']}] {newest['issued_at']} "
          f"\"{newest['alert_text']}\" -- validity not published, so not shown as active")
    else:
        p("latest alert   : none published")
    p(f"stale feeds    : {len(stale)} stations older than {STALE_AFTER.days} days"
      + (f" ({', '.join(stale)})" if stale else ""))
    for gap in run.history_gaps + [f"not published: {c}" for c in CANDIDATES_NOT_PUBLISHED]:
        p(f"history/gaps   : {gap}")
    for item in LIMITATIONS:
        p(f"limitation     : {item}")
    failed = [e.endpoint for e in run.logs.values() if e.errors]
    p(f"failed calls   : {', '.join(failed) if failed else 'none'}")
    mc = run.fetcher.method_counts
    p(f"fetch methods  : requests {mc['requests']}, playwright {mc['playwright']} successful calls")


# ---------------------------------------------------------------- entry point

def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backfill-days", type=int, choices=range(1, WINDOW_DAYS + 1),
                    metavar=f"1-{WINDOW_DAYS}", help="backfill this many days of the window")
    ap.add_argument("--date", type=date.fromisoformat, help="run as of YYYY-MM-DD (window end)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "data"),
                    help="output directory (default: ./data next to this script)")
    return ap.parse_args(argv)


def setup_logging(out: Path) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    log.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(), logging.FileHandler(out / "cfm_dss_collector.log",
                                                                 encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)


def main(argv=None) -> int:
    args = parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    setup_logging(out)
    end = args.date or now_ist().date()
    window = Window(end - timedelta(days=WINDOW_DAYS - 1), end)
    run = Run(out, window, args.backfill_days)
    log.info("run started; window %s..%s", window.start, window.end)
    try:
        collect_gauges(run)
        collect_gates(run)
        collect_gate_history(run)
        collect_alerts(run)
    except Exception:
        log.exception("run aborted; existing CSVs left untouched")
        return 1
    finally:
        run.fetcher.close()
    write_coverage(run)
    archived = {name: store.write(window) for name, store in run.stores.items()}
    write_run_log(run, archived)
    print_summary(run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
