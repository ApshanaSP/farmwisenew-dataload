#!/usr/bin/env bash
# Daily runner for cron. Uses .venv/bin/python when a virtual environment exists.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" run_pipeline.py "$@"
