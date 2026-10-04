#!/usr/bin/env python3
"""District Intelligence data layer.

  python run_pipeline.py build            build the curated store from current source outputs
  python run_pipeline.py refresh          run the collectors/generators not yet collected today (from 6:00 AM), then build
  python run_pipeline.py refresh --all    run all of them regardless of schedule
  python run_pipeline.py watch --every 15 loop: refresh what is due every N minutes, rebuild when inputs change
  python run_pipeline.py mysql            copy the last build into MySQL (databases district_intel and district_intel_ops)

The existing collectors keep their own fetching methods; this only calls their
command lines (see `refresh:` in config.yaml) and reads their output files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from dintel.util import REPO_DIR, load_settings, log, setup_logging  # noqa: E402

STATE = HERE / "output" / "state" / "refresh_state.json"
ATTEMPTS = 3            # tries per source in one run
RETRY_WAIT_S = 60       # pause between tries
JOB_TIMEOUT_S = 1800    # per try; a collector stuck longer than this is stopped and retried


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2), encoding="utf-8")


def day_start(now: float, hour: int) -> float:
    """The most recent `hour`:00 local time at or before `now`: where the collection day begins."""
    t = time.localtime(now)
    start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, 0, 0, 0, 0, -1))
    return start if start <= now else start - 86400


def is_due(job: dict, st: dict, now: float, hour: int) -> bool:
    """Daily jobs are due until they have succeeded once since today's collection hour, so a run
    missed while the laptop slept or was off happens as soon as it is back. A failed attempt is
    retried after an hour. Faster jobs are due once their interval, less up to 30 minutes, has passed
    since the last run started: the hourly task fires on the hour, so measuring a full hour from when
    the last run finished skipped every other hour, and a run at 11:16 after sign-in must not push
    the 12:00 run to 13:00."""
    rec = st.get(job["name"], {})
    last_ok = rec.get("last_ok", rec.get("last_run", 0) if rec.get("ok") else 0)
    last_try = rec.get("last_run", 0)
    if job["every_minutes"] >= 1440:
        return last_ok < day_start(now, hour) and now - last_try >= 3300
    every = job["every_minutes"] * 60
    return now - rec.get("started", last_try) >= every - min(every / 2, 1800)


def refresh(settings, force: bool = False, only: set[str] | None = None) -> list[str]:
    st = _state()
    now = time.time()
    hour = int(settings.raw.get("refresh_hour", 6))
    jobs = [j for j in settings.raw.get("refresh", []) if (not only or j["name"] in only) and (force or is_due(j, st, now, hour))]
    if not jobs:
        log.info("refresh: every source already collected today (since %s)", time.strftime("%d %b %H:%M", time.localtime(day_start(now, hour))))
        return []
    # the log lines carry only the time, so each run starts with its date
    log.info("=== daily collection %s: %s ===", time.strftime("%a %d %b %Y %H:%M"), ", ".join(j["name"] for j in jobs))
    from dintel import sync
    cal = sync.write_inputs(settings)    # shared rain calendar for the generators' own options
    fill = {"grievance_rain_days": ",".join(cal["grievance_rain_days"])}
    ran = []
    for job in jobs:
        cwd = REPO_DIR / job["cwd"]
        cmd = [c.format(**fill) if "{" in c else c for c in job["cmd"]]
        # A feed that fails once (a site timing out, the laptop sleeping mid-run, MySQL still
        # starting after boot) usually works a minute later, so try up to three times now
        # instead of leaving the day's data missing until the next hourly check.
        started = time.time()
        for attempt in range(1, ATTEMPTS + 1):
            log.info("refresh %s: %s (in %s)%s", job["name"], " ".join(cmd), cwd, f" [attempt {attempt}]" if attempt > 1 else "")
            try:
                p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=JOB_TIMEOUT_S,
                                   encoding="utf-8", errors="replace")
                ok = p.returncode == 0
                tail = (p.stdout or "")[-400:] + (p.stderr or "")[-400:]
            except (OSError, subprocess.TimeoutExpired) as exc:
                ok, tail = False, str(exc)
            if ok or attempt == ATTEMPTS:
                break
            log.warning("refresh %s: failed (%s); retrying in %d s", job["name"], tail.strip().splitlines()[-1][:160] if tail.strip() else "no output", RETRY_WAIT_S)
            time.sleep(RETRY_WAIT_S)
        prev_ok = st.get(job["name"], {}).get("last_ok", 0)
        st[job["name"]] = {"started": started, "last_run": time.time(), "ok": ok, "last_ok": time.time() if ok else prev_ok, "attempts": attempt, "tail": tail[-800:]}
        _save_state(st)  # after every job, so an interrupted run keeps what already succeeded
        log.info("refresh %s: %s", job["name"], "ok" if ok else f"FAILED after {attempt} attempts (see output/state/refresh_state.json)")
        ran.append(job["name"])
    failed = [j for j in ran if not st[j]["ok"]]
    st["_last_collection"] = {"at": now, "ran": ran, "failed": failed}
    _save_state(st)
    log.info("=== collection done: %d ok%s ===", len(ran) - len(failed), f", failed: {', '.join(failed)} (retried at the next hourly check)" if failed else "")
    return ran


def _inputs_fingerprint(settings) -> str:
    h = hashlib.sha1()
    for p in sorted(REPO_DIR.glob("*/**/*.csv")):
        if "district_intel" in p.parts or "node_modules" in p.parts:
            continue
        s = p.stat()
        h.update(f"{p}|{s.st_size}|{s.st_mtime_ns}".encode())
    return h.hexdigest()


def _alive(lock: Path) -> bool:
    """Is the process that wrote the lock still running? A refresh that was stopped part-way
    leaves its lock behind; that lock must not block the next hourly check."""
    try:
        pid = int(lock.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _refresh_and_build(settings, args, build, sync_mysql) -> None:
    only = {x.strip() for x in args.only.split(",") if x.strip()} or None
    refresh(settings, force=args.all, only=only)
    st = _state()
    fp = _inputs_fingerprint(settings)
    if args.no_build:
        return
    if fp != st.get("_last_build_fingerprint"):
        build(settings)
        st = _state()  # re-read: the MySQL step records its own progress
        st["_last_build_fingerprint"] = fp
        _save_state(st)
    else:
        log.info("no source file changed since the last build; skipping the rebuild")
        sync_mysql(settings)


def _mysql(settings) -> dict:
    from dintel.mysql_export import export

    return export(settings)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["build", "refresh", "watch", "mysql"])
    ap.add_argument("--all", action="store_true", help="refresh every source regardless of schedule")
    ap.add_argument("--every", type=int, default=15, help="watch interval in minutes")
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-overlay", action="store_true", help="skip the scenario overlay")
    ap.add_argument("--only", default="", help="refresh only these sources, comma-separated (e.g. news,imd)")
    ap.add_argument("--no-build", action="store_true", help="refresh without rebuilding")
    ap.add_argument("--mysql", action="store_true", help="export to MySQL after each build (or set mysql.export_after_build)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    setup_logging(args.verbose)
    settings = load_settings(args.config)
    if args.no_overlay:
        settings.raw["overlay"]["enabled"] = False
    from dintel.pipeline import build as _build

    export_mysql = args.mysql or settings.raw.get("mysql", {}).get("export_after_build", False)

    def sync_mysql(s) -> None:
        """Export when the SQLite store is newer than the last export. Failures are logged and
        retried on the next run, so a stopped MySQL server never blocks the build."""
        if not export_mysql:
            return
        db = s.path(s.raw["output"]["sqlite"])
        st = _state()
        if not db.exists() or st.get("_mysql_exported_mtime") == db.stat().st_mtime:
            return
        try:
            rep = _mysql(s)
        except Exception as exc:  # noqa: BLE001
            log.error("mysql export failed (will retry on the next run): %s", exc)
            return
        if not rep["mismatches"]:
            st = _state()
            st["_mysql_exported_mtime"] = db.stat().st_mtime
            _save_state(st)

    def build(s):
        m = _build(s)
        sync_mysql(s)
        return m

    if args.command == "mysql":
        rep = _mysql(settings)
        print(json.dumps({k: rep[k] for k in ("database", "ops_database", "tables", "rows", "documents_columns",
                                              "ops_tables_created", "briefings_archived", "mismatches", "runtime_s")}, indent=2))
        sys.exit(1 if rep["mismatches"] else 0)
    elif args.command == "build":
        m = build(settings)
        st = _state()
        st["_last_build_fingerprint"] = _inputs_fingerprint(settings)
        _save_state(st)
        print(json.dumps({k: m[k] for k in ("as_of", "runtime_s", "dedup", "geo_holdout", "flood_day_correlation_before_overlay",
                                             "flood_day_correlation_after_overlay") if k in m}, indent=2, default=str))
    elif args.command == "refresh":
        # one refresh at a time: the hourly schedule, a sign-in trigger and the dashboard's Refresh can overlap
        lock = HERE / "output" / ".refresh.lock"
        if lock.exists() and time.time() - lock.stat().st_mtime < 2 * 3600 and _alive(lock):
            log.info("refresh: another refresh is running (started %s); skipping", time.strftime("%H:%M", time.localtime(lock.stat().st_mtime)))
            return
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(os.getpid()), encoding="utf-8")
        try:
            _refresh_and_build(settings, args, build, sync_mysql)
        finally:
            lock.unlink(missing_ok=True)
    else:
        last = None
        while True:
            refresh(settings)
            fp = _inputs_fingerprint(settings)
            if fp != last:
                build(settings)
                last = fp
            else:
                log.info("no input changed; next check in %d min", args.every)
            time.sleep(args.every * 60)


if __name__ == "__main__":
    main()
