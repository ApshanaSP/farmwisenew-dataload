"""Chennai District news dataset pipeline - entry point.

Usage:
    python run_pipeline.py              # first run: 90-day backfill; later runs: last 48 h
    python run_pipeline.py --backfill   # force a 90-day backfill
    python run_pipeline.py --no-extract # skip article-page downloads (RSS summaries only)
    python run_pipeline.py --no-db      # do not write to MySQL even if configured
    python run_pipeline.py --reprocess  # rebuild today's processed/rejected files from today's
                                        # saved raw JSON (no re-fetch; e.g. after gazetteer edits)
    python run_pipeline.py --reprocess 2026-09-24   # same, for an earlier run date
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.classification import NewsClassifier
from src.config import PROJECT_ROOT, load_config, load_env
from src.features import PROCESSED_COLUMNS
from src.fetchers import fetch_all_sources
from src.fetchers.article_extractor import ArticleExtractor
from src.fetchers.base import RAW_COLUMNS, FetchReport, run_id_prefix
from src.http_client import HttpClient, enable_system_trust_store
from src.logging_setup import setup_logging
from src.processing import build_rows, clean_item
from src.relevance_filter import Gazetteer, RelevanceFilter
from src.storage import (
    REJECTED_COLUMNS,
    FileLockedError,
    MySQLStore,
    dated_path,
    load_state,
    save_state,
    update_master,
    write_csv,
    write_json,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Build the Chennai District news dataset.")
    parser.add_argument("--backfill", action="store_true", help="fetch the last N days (run.backfill_days)")
    parser.add_argument("--no-extract", action="store_true", help="skip full-text extraction")
    parser.add_argument("--no-db", action="store_true", help="skip MySQL even if configured in .env")
    parser.add_argument("--reprocess", nargs="?", const="today", default=None, metavar="YYYY-MM-DD",
                        help="re-run processing on a saved raw JSON (default: today's) instead of fetching")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    return parser.parse_args(argv)


def load_raw_report(raw_json: Path) -> FetchReport:
    """Rebuild a FetchReport from a saved raw JSON file (for --reprocess)."""
    rows = json.loads(raw_json.read_text(encoding="utf-8"))
    report = FetchReport(requests_ok=1)
    for row in rows:
        if isinstance(row.get("entry_json"), (dict, list)):
            row["entry_json"] = json.dumps(row["entry_json"], ensure_ascii=False)
        report.items.append(row)
        report.per_feed[row["source_name"]] += 1
    return report


def print_summary(
    mode: str,
    raw_rows: list[dict[str, Any]],
    per_source: dict[str, Counter],
    per_feed: Counter,
    n_relevant: int,
    n_rejected: int,
    failures: list[str],
    extraction: Counter,
    outputs: list[Path],
) -> None:
    """End-of-run console summary."""
    line = "=" * 78
    print(f"\n{line}\nCHENNAI NEWS PIPELINE - RUN SUMMARY ({mode})\n{line}")
    print(f"Total fetched: {len(raw_rows)}  ->  Chennai-relevant: {n_relevant}  |  rejected: {n_rejected}")
    if extraction:
        print(f"Body extraction: {dict(extraction)}")
    print("\nPer feed (items fetched):")
    for feed, count in per_feed.most_common():
        print(f"  {feed:<40} {count:>6}")
    print(f"\nPer source ({len(per_source)} publishers)   fetched  relevant  rejected")
    ranked = sorted(per_source.items(), key=lambda kv: -kv[1]["fetched"])
    for name, c in ranked:
        print(f"  {name[:38]:<38} {c['fetched']:>9} {c['relevant']:>9} {c['rejected']:>9}")
    if failures:
        print(f"\nSource failures ({len(failures)}), details in the log:")
        for failure in failures[:15]:
            print(f"  - {failure[:150]}")
        if len(failures) > 15:
            print(f"  ... and {len(failures) - 15} more")
    print("\nOutputs:")
    for path in outputs:
        print(f"  {path}")
    print(line)


def print_classification(counts: dict[str, Counter], total: int) -> None:
    """Department and complaint breakdown for the console summary."""
    if not counts or not total:
        return
    print("\nDepartments (processed rows):")
    for name, n in counts["department"].most_common():
        print(f"  {name[:58]:<58} {n:>6}  {100 * n / total:5.1f}%")
    print(f"Department method: {dict(counts['method'])}")
    print(f"Complaints: {counts['complaint']['complaint']} of {total} "
          f"({100 * counts['complaint']['complaint'] / total:.1f}%)")


def main(argv: list[str] | None = None) -> int:
    """Run the full pipeline once. Returns a process exit code."""
    args = parse_args(argv)
    cfg = load_config(args.config)
    env = load_env()
    tz = ZoneInfo(cfg["timezone"])
    naive_tz = ZoneInfo(cfg["run"]["naive_datetime_timezone"])
    started = datetime.now(tz).replace(microsecond=0)
    run_date = started.date()
    if args.reprocess and args.reprocess != "today":
        run_date = date.fromisoformat(args.reprocess)
    run_prefix = run_id_prefix(run_date)
    paths = cfg["paths"]
    log = setup_logging(paths["log_dir"], run_date)

    state = load_state(paths["state_file"])
    mode = "backfill" if args.backfill or not state.get("backfill_completed") else "daily"
    log.info("=== Run started %s | mode=%s ===", started.isoformat(), mode)

    enable_system_trust_store(bool(cfg["http"].get("use_system_trust_store", True)))
    client = HttpClient(cfg["http"])
    gazetteer = Gazetteer.from_config(cfg["gazetteer"])
    relevance = RelevanceFilter(gazetteer, cfg["relevance"].get("exclusion_phrases") or [])

    # 1. Fetch -----------------------------------------------------------------
    raw_csv = dated_path(paths["raw_dir"], "raw_news", run_date, ".csv")
    raw_json = dated_path(paths["raw_dir"], "raw_news", run_date, ".json")
    if args.reprocess:
        report = load_raw_report(raw_json)
        mode = "reprocess"
        log.info("Reprocessing %d raw items from %s", len(report.items), raw_json)
    else:
        report = fetch_all_sources(client, cfg, env, mode, run_date, tz)
        write_csv(raw_csv, report.items, RAW_COLUMNS)
        write_json(raw_json, report.items)
        log.info("Raw: %d items written to %s", len(report.items), raw_csv)
    raw_rows = report.items

    # 2. Clean + extract bodies -------------------------------------------------
    cleaned = []
    for raw in raw_rows:
        try:
            cleaned.append(clean_item(raw, cfg, tz, naive_tz))
        except Exception:
            log.exception("Cleaning failed for %s (%s)", raw.get("article_id"), raw.get("link"))
            cleaned.append(None)

    extractor = ArticleExtractor(client, cfg["extraction"])
    bodies: dict[str, str | None] = {}
    if cfg["extraction"].get("enabled", True) and not args.no_extract:
        bodies = extractor.extract_many(c.canonical_url for c in cleaned if c is not None)

    # 3. Filter + features ------------------------------------------------------
    processed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    per_source: dict[str, Counter] = defaultdict(Counter)
    for raw, item in zip(raw_rows, cleaned):
        if item is None:
            rejected.append({"article_id": raw["article_id"], "title": raw.get("title", ""), "url": raw.get("link", ""),
                             "source_type": raw["source_type"], "rejection_reason": "processing_error"})
            per_source[raw["source_name"]]["fetched"] += 1
            per_source[raw["source_name"]]["rejected"] += 1
            continue
        try:
            is_relevant, row, _ = build_rows(item, bodies.get(item.canonical_url), relevance, cfg)
        except Exception:
            log.exception("Processing failed for %s", raw["article_id"])
            is_relevant, row = False, {"article_id": raw["article_id"], "title": item.title_clean,
                                       "url": item.url, "rejection_reason": "processing_error"}
        counts = per_source[item.source_name]
        counts["fetched"] += 1
        if is_relevant:
            processed.append(row)
            counts["relevant"] += 1
        else:
            rejected.append(row)
            counts["rejected"] += 1

    # 4. Department + complaint classification ---------------------------------
    cls_cfg = cfg.get("classification") or {}
    classification_counts: dict[str, Counter] = {}
    if cls_cfg.get("enabled", True) and processed:
        classifier = NewsClassifier(cls_cfg, env, Path(paths["classification_cache"]))
        classifier.annotate(processed)
        classification_counts = {
            "department": Counter(r["department"] for r in processed),
            "method": Counter(r["department_method"] for r in processed),
            "complaint": Counter("complaint" if r["is_complaint"] else "not complaint" for r in processed),
        }
        log.info("Classification: %s | %s", dict(classification_counts["method"]), dict(classification_counts["complaint"]))

    # 5. Write -----------------------------------------------------------------
    proc_csv = write_csv(dated_path(paths["processed_dir"], "processed_news", run_date, ".csv"), processed, PROCESSED_COLUMNS)
    rej_csv = write_csv(dated_path(paths["processed_dir"], "rejected", run_date, ".csv"), rejected, REJECTED_COLUMNS)
    master = Path(paths["processed_dir"]) / "master_news.csv"
    try:
        retention = int(cfg["run"].get("master_retention_days") or 0)
        keep_since = run_date - timedelta(days=retention) if retention > 0 else None
        master_total, master_dropped = update_master(master, processed, PROCESSED_COLUMNS, run_prefix, keep_since)
        log.info("Processed: %d | Rejected: %d | master_news.csv now %d rows", len(processed), len(rejected), master_total)
        if keep_since:
            log.info("Rolling window: kept articles dated %s or later; dropped %d older rows", keep_since, master_dropped)
    except FileLockedError as exc:
        log.error("master_news.csv NOT updated: %s", exc)

    db_status = "not configured"
    store = None if args.no_db else MySQLStore.from_env(env, PROJECT_ROOT / "schema.sql")
    if store is not None:
        try:
            store.write_run(raw_rows, processed, PROCESSED_COLUMNS, run_prefix)
            db_status = "written"
        except Exception:
            log.exception("MySQL write failed; CSV outputs are complete")
            db_status = "FAILED (see log)"
    log.info("MySQL: %s", db_status)

    if not args.reprocess:
        state.update({"last_run_at": started.isoformat(), "last_mode": mode, "last_run_items": len(raw_rows)})
        if mode == "backfill" and report.requests_ok > 0:
            state["backfill_completed"] = True
        save_state(paths["state_file"], state)

    print_summary(mode, raw_rows, per_source, report.per_feed, len(processed), len(rejected),
                  report.failures, extractor.status_counts, [raw_csv, raw_json, proc_csv, rej_csv, master])
    print_classification(classification_counts, len(processed))
    print(f"MySQL: {db_status}")
    log.info("=== Run finished in %s ===", datetime.now(tz).replace(microsecond=0) - started)
    return 0 if report.requests_ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
