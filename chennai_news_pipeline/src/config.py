"""Configuration loading (config.yaml + .env)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load config.yaml and resolve every entry under ``paths`` to an absolute path.

    Args:
        config_path: Path to the YAML file. Defaults to ``<project>/config.yaml``.

    Returns:
        The parsed configuration dictionary.
    """
    path = Path(config_path) if config_path else PROJECT_ROOT / "config.yaml"
    with open(path, encoding="utf-8") as fh:
        cfg: dict[str, Any] = yaml.safe_load(fh)
    cfg["paths"] = {
        key: str((PROJECT_ROOT / value).resolve()) if not Path(value).is_absolute() else value
        for key, value in cfg.get("paths", {}).items()
    }
    return cfg


def load_env() -> dict[str, str]:
    """Load ``<project>/.env`` (if present) into the process environment.

    Returns:
        A copy of the resulting environment.
    """
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    return dict(os.environ)
