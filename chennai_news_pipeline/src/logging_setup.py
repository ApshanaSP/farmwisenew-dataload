"""Logging configuration: one UTF-8 log file per run date plus console output."""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

LOGGER_NAME = "chennai_news"


def setup_logging(log_dir: str | Path, run_date: date, level: int = logging.INFO) -> logging.Logger:
    """Configure the pipeline logger.

    Logs go to ``<log_dir>/pipeline_YYYY-MM-DD.log`` (appended, so several runs on the
    same day remain visible) and to the console.

    Args:
        log_dir: Directory for log files (created if missing).
        run_date: Date used in the log file name.
        level: Logging level.

    Returns:
        The configured logger.
    """
    # Windows consoles default to a legacy code page; Tamil text would crash print().
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    file_handler = logging.FileHandler(log_path / f"pipeline_{run_date:%Y-%m-%d}.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S"))
    console.setLevel(level)
    logger.addHandler(file_handler)
    logger.addHandler(console)

    # Silence chatty third-party loggers; failures surface through our own messages.
    for noisy in ("trafilatura", "urllib3", "charset_normalizer", "htmldate", "courlan"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    return logger


def get_logger(child: str | None = None) -> logging.Logger:
    """Return the pipeline logger or one of its children."""
    return logging.getLogger(f"{LOGGER_NAME}.{child}" if child else LOGGER_NAME)
