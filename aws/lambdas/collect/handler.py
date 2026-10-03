"""
fai-tce-team37-collect-<source>: runs one of the unchanged collectors inside Lambda. Every collector Lambda ships
this same package; the SOURCE environment variable picks the collector.

Started by a direct invoke, or over HTTPS through API Gateway (POST /refresh/<source>), which an outside cron calls
hourly because EventBridge schedules are denied to the team role.

1. Pulls the collector's CSVs from S3 (state/<source>/) into /tmp, so its upserts and rolling windows carry over.
2. Runs the collector exactly as it runs on the PC, with its output folder pointed at /tmp.
3. Pushes the CSVs back to S3, copies this run's raw responses to raw/<source>/, and writes to DynamoDB only the
   rows that are new or changed since the previous run (so an hourly run costs a few writes, not thousands).

Item layout in the intel table (all values kept as the collector's strings):
  pk      = <source>#<kind>[#<id>]   e.g. imd#observation#43279, cpcb#aqi#site_288, pwd#water_level#A012
  sk      = the row's time / identity columns joined by '#'
  kind_pk = <source>#<kind>           for a by-kind index on a future table (UpdateTable is denied, so the
  kind_sk = sk[#<id>]                 current table cannot get one)
"""
from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import hmac
import importlib
import io
import json
import logging
import os
import sys
import time
from pathlib import Path

try:
    import boto3  # always there in Lambda; aws/push.py imports this file only for FILES, on a PC that may lack it
except ImportError:  # pragma: no cover
    boto3 = None

SOURCE = os.environ.get("SOURCE", "")
BUCKET = os.environ.get("RAW_BUCKET", "")
TABLE = os.environ.get("INTEL_TABLE", "")
HERE = Path(__file__).resolve().parent
TALUKS = str(HERE / "taluks.json")

# kind, id column (or None), sort-key columns. A *_archive.csv file uses its base file's mapping.
FILES: dict[str, dict[str, tuple[str, str | None, list[str]]]] = {
    "imd": {
        "imd_weather_observations_chennai.csv": ("observation", "station_id", ["observed_at", "observation_period_start", "observation_period_end", "provider"]),
        "imd_weather_forecasts_chennai.csv": ("forecast", "location_id", ["issued_at", "valid_from", "valid_to", "provider"]),
        "imd_weather_warnings_chennai.csv": ("warning", None, ["issued_at", "warning_id"]),
    },
    "cpcb": {
        "cpcb_station_aqi_chennai.csv": ("aqi", "station_id", ["observation_datetime"]),
        "cpcb_pollutants_chennai.csv": ("pollutant", "station_id", ["pollutant", "observation_datetime", "averaging_period", "statistic"]),
        "cpcb_station_coverage.csv": ("station", None, ["station_id"]),
    },
    "cfm": {
        "cfm_dss_gauge_observations_chennai.csv": ("gauge", "station_id", ["observed_at"]),
        "cfm_dss_gate_operations_chennai.csv": ("gate", "gate_site_id", ["observed_at"]),
        "cfm_dss_alerts_chennai.csv": ("alert", None, ["issued_at", "alert_id"]),
        "cfm_dss_station_coverage.csv": ("station", None, ["station_type", "station_id"]),
    },
    "pwd": {
        "pwd_water_levels.csv": ("water_level", "asset_id", ["reading_date"]),
        "pwd_incidents.csv": ("incident", "taluk_code", ["reported_at", "incident_id"]),
        "pwd_tasks.csv": ("task", "assigned_office_id", ["assigned_at", "task_id"]),
        "pwd_announcements.csv": ("announcement", None, ["published_at", "announcement_id"]),
        "pwd_works.csv": ("work", None, ["work_id"]),
        "pwd_assets.csv": ("asset", None, ["asset_id"]),
        "pwd_offices.csv": ("office", None, ["office_id"]),
    },
    "hospital": {
        "chennai_hospital_health_data.csv": ("daily", "hospital_name", ["date"]),
    },
    "news": {
        "master_news.csv": ("article", "published_date", ["published_at_ist", "article_id"]),
    },
    "police": {  # Node.js generator: runs on the PC, arrives through /ingest/police (ground_truth_events.csv stays out)
        "police_incident_reports.csv": ("incident", "station_code", ["reported_datetime", "report_id"]),
        "police_stations.csv": ("station", None, ["station_code"]),
    },
}
PC_ONLY = {"police": "a Node.js generator", "cpcb": "a browser (Playwright)"}
# Columns left out of DynamoDB (the full CSV stays in S3): article bodies are large and the dashboard shows summaries.
DROP = {"news": {"full_text", "body_clean", "title_normalized"}}
# Sources whose run is longer than API Gateway's 30 s: /refresh starts them in the background and returns 202.
BACKGROUND = {"news"}


def run_collector(source: str, out: Path) -> int:
    """Calls the collector's own entry point with its output folder set to `out`. Returns its exit code."""
    def call(fn, argv=None):
        old = sys.argv
        sys.argv = [source] + (argv or [])
        try:
            r = fn()
            return r if isinstance(r, int) else 0
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        finally:
            sys.argv = old

    for name in ("cpcb_aq", "cfm_dss", ""):  # warm Lambdas keep log handlers; drop ones left by the last run
        for h in list(logging.getLogger(name).handlers):
            if isinstance(h, logging.FileHandler):
                logging.getLogger(name).removeHandler(h)
    if source == "imd":
        m = importlib.reload(importlib.import_module("imd_weather_collector"))
        return m.main(["--out", str(out), "--backfill-days", os.environ.get("BACKFILL_DAYS", "3"),
                       "--boundary", str(HERE / "gcc-wards.geojson"), "--location-master", TALUKS])
    if source == "cpcb":
        m = importlib.reload(importlib.import_module("cpcb_air_quality_collector"))
        return call(m.main, ["--out", str(out), "--location-master", TALUKS])
    if source == "cfm":
        m = importlib.reload(importlib.import_module("cfm_dss_collector"))
        return m.main(["--out", str(out)])
    if source == "pwd":
        m = importlib.reload(importlib.import_module("generate_pwd_data"))
        return call(m.main, ["--out", str(out)])
    if source == "hospital":
        m = importlib.reload(importlib.import_module("chennai_hospital_data_generator"))
        m.CSV_FILE = str(out / "chennai_hospital_health_data.csv")  # its default is beside the script (read-only here)
        return call(m.main)
    if source == "news":
        return run_news(out)
    if source == "police":
        raise ValueError("police is generated on the PC (Node.js); send it with: python aws/push.py police")
    raise ValueError(f"unknown source {source!r}")


def run_news(out: Path) -> int:
    """
    The news pipeline with its own config.yaml, every path moved under /tmp. Its state (master_news.csv,
    pipeline_state.json) sits at the top of `out` between runs, so it travels through S3 like the other sources;
    it is moved into the processed folder the pipeline expects for the run and back afterwards. With no
    sentence-transformers here the pipeline uses its keyword department model, as it does on a PC without one.
    """
    import yaml

    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
    cfg["paths"] = {"raw_dir": str(out / "raw"), "processed_dir": str(out / "processed"),
                    "state_file": str(out / "pipeline_state.json"),
                    "classification_cache": str(out / "classification_cache.json"), "log_dir": str(out / "logs")}
    (out / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (out / "processed").mkdir(exist_ok=True)
    master = out / "master_news.csv"
    if master.exists():
        master.replace(out / "processed" / master.name)
    try:
        m = importlib.reload(importlib.import_module("run_pipeline"))
        return m.main(["--config", str(out / "config.yaml"), "--no-db"])
    finally:
        if (out / "processed" / master.name).exists():
            (out / "processed" / master.name).replace(master)
        (out / "config.yaml").unlink(missing_ok=True)


def items(source: str, path: Path):
    spec = FILES[source].get(path.name.replace("_archive.csv", ".csv"))
    if not spec or not path.exists():
        return
    kind, id_col, sk_cols = spec
    drop = DROP.get(source, set())
    for r in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))):
        # DynamoDB keeps no empty attributes
        item = {k: v for k, v in r.items() if k and k not in drop and v not in ("", None)}
        sk = "#".join(r.get(c, "") for c in sk_cols)
        ident = r.get(id_col, "") if id_col else ""
        item.update(pk=f"{source}#{kind}" + (f"#{ident}" if id_col else ""), sk=sk,
                    kind_pk=f"{source}#{kind}", kind_sk=sk + (f"#{ident}" if id_col else ""), source=source, kind=kind)
        yield item


def fingerprints(source: str, folder: Path) -> dict[tuple[str, str], str]:
    return {(i["pk"], i["sk"]): hashlib.sha1(json.dumps(i, sort_keys=True).encode()).hexdigest()
            for f in folder.glob("*.csv") for i in items(source, f)}


def handler(event, context):
    """
    Direct invoke: {} or {"full": true}. API Gateway, with header x-refresh-key:
      POST /refresh/<source>  run the collector here (news takes ~10 min: started in the background, 202)
      POST /ingest/<source>   body {"gz": base64(gzip(json {file name: CSV text}))}: CSVs from a run on the PC
                              (aws/push.py), for sources that cannot run in Lambda (CPCB needs a browser)
    """
    if "requestContext" in event:
        given = (event.get("headers") or {}).get("x-refresh-key", "")
        if not given or not hmac.compare_digest(given.encode(), os.environ.get("REFRESH_KEY", "").encode()):
            return {"statusCode": 401, "body": json.dumps({"error": "missing or wrong x-refresh-key"})}
        files = None
        if event.get("rawPath", "").startswith("/ingest/"):
            body = event.get("body") or ""
            body = base64.b64decode(body).decode() if event.get("isBase64Encoded") else body
            try:
                files = json.loads(gzip.decompress(base64.b64decode(json.loads(body)["gz"])))
            except (ValueError, KeyError, OSError) as e:
                return {"statusCode": 400, "body": json.dumps({"error": f"bad ingest body: {e}"})}
            allowed = set(FILES[SOURCE]) | {n.replace(".csv", "_archive.csv") for n in FILES[SOURCE]}
            if not files or not set(files) <= allowed:
                return {"statusCode": 400, "body": json.dumps({"error": f"only these files: {sorted(FILES[SOURCE])}"})}
        elif SOURCE == "police":
            return {"statusCode": 409, "body": json.dumps({"error": f"{SOURCE} needs {PC_ONLY[SOURCE]}: "
                                                                    f"run it on the PC, then python aws/push.py {SOURCE}"})}
        elif SOURCE in BACKGROUND:
            boto3.client("lambda").invoke(FunctionName=context.function_name, InvocationType="Event", Payload=b"{}")
            return {"statusCode": 202, "body": json.dumps({"source": SOURCE, "started": True,
                                                           "note": "runs in the background; see CloudWatch Logs"})}
        full = (event.get("queryStringParameters") or {}).get("full") == "1"  # ?full=1: write every row
        return {"statusCode": 200, "headers": {"content-type": "application/json"},
                "body": json.dumps(run(SOURCE, full=full, ingest=files))}
    return run(event.get("source") or SOURCE, full=bool(event.get("full")))


def run(source: str, full: bool = False, ingest: dict[str, str] | None = None) -> dict:
    """
    full=True writes every row (first load into DynamoDB); otherwise only rows changed since the last run.
    ingest = CSVs sent from the PC: they replace the collector run.
    """
    start = time.time()
    s3 = boto3.client("s3")
    table = boto3.resource("dynamodb").Table(TABLE)
    out = Path("/tmp") / source
    state = f"state/{source}/"

    out.mkdir(parents=True, exist_ok=True)
    for f in out.iterdir():
        if f.is_file():
            f.unlink()  # a warm Lambda's leftovers; S3 holds the truth
    for obj in s3.list_objects_v2(Bucket=BUCKET, Prefix=state).get("Contents", []):
        name = obj["Key"][len(state):]
        if name and "/" not in name:
            s3.download_file(BUCKET, obj["Key"], str(out / name))
    before = {} if full else fingerprints(source, out)

    if ingest is None:
        code = run_collector(source, out)
    else:
        for name, text in ingest.items():
            (out / name).write_text(text, encoding="utf-8-sig")
        code = "ingested"

    for f in out.iterdir():
        if f.is_file() and f.suffix in (".csv", ".json"):
            s3.upload_file(str(f), BUCKET, state + f.name)
    raw = 0
    for f in out.rglob("*"):
        if f.is_file() and f.parent != out and f.stat().st_mtime >= start:
            s3.upload_file(str(f), BUCKET, f"raw/{source}/" + f.relative_to(out).as_posix())
            raw += 1

    written, total = {}, 0
    with table.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as w:
        for f in out.glob("*.csv"):
            for i in items(source, f):
                total += 1
                if before.get((i["pk"], i["sk"])) != hashlib.sha1(json.dumps(i, sort_keys=True).encode()).hexdigest():
                    w.put_item(Item=i)
                    written[i["kind"]] = written.get(i["kind"], 0) + 1
    result = {"source": source, "collector_exit": code, "rows_total": total, "rows_written": written,
              "raw_files_saved": raw, "seconds": round(time.time() - start, 1)}
    print(json.dumps(result))
    return result
