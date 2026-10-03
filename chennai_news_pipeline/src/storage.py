"""Output writers: raw/processed/rejected CSV + JSON, master CSV, optional MySQL.

Re-running on the same date overwrites that date's files and replaces that date's
rows in master_news.csv / MySQL. This is file handling only - rows inside a run
are never de-duplicated.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from src.fetchers.base import RAW_COLUMNS
from src.logging_setup import get_logger

log = get_logger("storage")

# Article bodies can exceed the csv module's default 128 KB field limit.
csv.field_size_limit(2**31 - 1)

REJECTED_COLUMNS: list[str] = [
    "article_id", "fetched_at_ist", "published_at_ist", "title", "url", "canonical_url",
    "source_name", "source_domain", "source_type", "query_used", "language",
    "rejection_reason", "excluded_phrases_found", "context_only_places_found",
]


class FileLockedError(OSError):
    """The target file is open in another program (on Windows, usually Excel)."""


def _atomic_replace(tmp: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(tmp, target)
    except PermissionError as exc:
        tmp.unlink(missing_ok=True)
        raise FileLockedError(
            f"{target} is open in another program (e.g. Excel). Close it and re-run "
            f"`python run_pipeline.py --reprocess <run date>`."
        ) from exc


def write_csv(path: str | Path, rows: Iterable[dict[str, Any]], columns: list[str]) -> Path:
    """Write rows as UTF-8-with-BOM CSV (Excel shows Tamil correctly), overwriting."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    _atomic_replace(tmp, target)
    return target


def write_json(path: str | Path, rows: list[dict[str, Any]]) -> Path:
    """Write rows as a pretty UTF-8 JSON array, overwriting.

    ``entry_json`` strings are expanded back into objects so the JSON file holds each
    feed entry exactly as received.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    out = []
    for row in rows:
        item = dict(row)
        if item.get("entry_json"):
            item["entry_json"] = json.loads(item["entry_json"])
        out.append(item)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    _atomic_replace(tmp, target)
    return target


def row_date(row: dict[str, Any]) -> date | None:
    """Article date used for retention: published_date, else the fetch date."""
    value = str(row.get("published_date") or row.get("fetched_at_ist") or "")[:10]
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def update_master(
    master_path: str | Path,
    rows: list[dict[str, Any]],
    columns: list[str],
    run_prefix: str,
    keep_since: date | None = None,
) -> tuple[int, int]:
    """Append this run's rows to master_news.csv, first removing rows of the same run date.

    Rows are identified by the article_id prefix (``YYYYMMDD-``) so a same-day re-run
    replaces its earlier output instead of appending it twice.

    Args:
        keep_since: Rolling window. When set, rows (old and new) whose article date is
            before this date are dropped. Rows with no usable date are kept.

    Returns:
        (rows in the master file after the update, rows dropped by the rolling window)
    """
    target = Path(master_path)
    kept: list[dict[str, Any]] = []
    if target.exists():
        with open(target, encoding="utf-8-sig", newline="") as fh:
            kept = [r for r in csv.DictReader(fh) if not str(r.get("article_id", "")).startswith(run_prefix)]
    combined = [*kept, *rows]
    dropped = 0
    if keep_since is not None:
        before = len(combined)
        combined = [r for r in combined if (d := row_date(r)) is None or d >= keep_since]
        dropped = before - len(combined)
    write_csv(target, combined, columns)
    return len(combined), dropped


# ----------------------------------------------------------------------------- MySQL
def _mysql_datetime(value: str) -> str | None:
    """ISO-8601 with offset -> 'YYYY-MM-DD HH:MM:SS' (IST wall-clock)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _nullable(value: Any) -> Any:
    return None if value == "" else value


class MySQLStore:
    """Writes news_raw and news_processed. Enabled only when DB_* variables are set."""

    def __init__(self, params: dict[str, Any], schema_path: Path) -> None:
        self.params = params
        self.schema_path = schema_path

    @classmethod
    def from_env(cls, env: dict[str, str], schema_path: Path) -> "MySQLStore | None":
        """Return a store when DB_HOST, DB_USER and DB_NAME are present, else None."""
        host, user, name = env.get("DB_HOST"), env.get("DB_USER"), env.get("DB_NAME")
        if not (host and user and name):
            return None
        return cls(
            {
                "host": host,
                "port": int(env.get("DB_PORT") or 3306),
                "user": user,
                "password": env.get("DB_PASSWORD") or "",
                "database": name,
                "charset": "utf8mb4",
            },
            schema_path,
        )

    def _connect(self) -> Any:
        import pymysql  # imported lazily: MySQL is optional

        return pymysql.connect(**self.params, autocommit=False)

    def _ensure_schema(self, cur: Any) -> None:
        """Run schema.sql (CREATE TABLE IF NOT EXISTS - idempotent)."""
        sql = self.schema_path.read_text(encoding="utf-8")
        sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
        for statement in (s.strip() for s in sql.split(";")):
            if statement:
                cur.execute(statement)

    def write_run(
        self,
        raw_rows: list[dict[str, Any]],
        processed_rows: list[dict[str, Any]],
        processed_columns: list[str],
        run_prefix: str,
    ) -> None:
        """Replace this run date's rows in both tables inside one transaction."""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                self._ensure_schema(cur)
                like = f"{run_prefix}%"
                cur.execute("DELETE FROM news_raw WHERE article_id LIKE %s", (like,))
                cur.execute("DELETE FROM news_processed WHERE article_id LIKE %s", (like,))

                raw_values = [
                    tuple(_mysql_datetime(r[c]) if c == "fetched_at" else (r.get(c) or None) for c in RAW_COLUMNS)
                    for r in raw_rows
                ]
                self._insert(cur, "news_raw", RAW_COLUMNS, raw_values)

                dt_cols = {"fetched_at_ist", "published_at_ist"}
                bool_cols = {"body_extracted", "has_tamil_text", "is_complaint"}
                proc_values = []
                for r in processed_rows:
                    values = []
                    for c in processed_columns:
                        v = r.get(c, "")
                        if c in dt_cols:
                            v = _mysql_datetime(v)
                        elif c in bool_cols:
                            v = 1 if v in (True, "True", "true", 1, "1") else 0
                        else:
                            v = _nullable(v)
                        values.append(v)
                    proc_values.append(tuple(values))
                self._insert(cur, "news_processed", processed_columns, proc_values)
            conn.commit()
            log.info("MySQL: wrote %d raw and %d processed rows", len(raw_values), len(proc_values))
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _insert(cur: Any, table: str, columns: list[str], values: list[tuple], batch: int = 500) -> None:
        if not values:
            return
        placeholders = ", ".join(["%s"] * len(columns))
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        for i in range(0, len(values), batch):
            cur.executemany(sql, values[i : i + batch])


def load_state(path: str | Path) -> dict[str, Any]:
    """Read the pipeline state file ({} when missing or unreadable)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    """Persist pipeline state (last run, whether the backfill has completed)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def dated_path(directory: str | Path, stem: str, run_date: date, suffix: str) -> Path:
    """``<directory>/<stem>_YYYY-MM-DD<suffix>``."""
    return Path(directory) / f"{stem}_{run_date:%Y-%m-%d}{suffix}"
