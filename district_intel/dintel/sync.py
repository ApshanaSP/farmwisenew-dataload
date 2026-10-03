"""One shared rain calendar for the synthetic generators, passed through their OWN options.

The police generator already keeps a rain calendar (config/events.json, HEAVY_RAIN days).
That list, plus IMD warning days, becomes the world calendar, and is handed to:
  * the grievance generator  -> --rain-days a,b,c
  * the PWD generator        -> --rainfall-csv date,taluk_code,rainfall_mm
No generator code changes; this only writes their inputs.
"""
from __future__ import annotations

import csv
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from .util import IST, INTEL_DIR, Settings

STATE = INTEL_DIR / "output" / "state"
PWD_CODES = [f"TLK{i:02d}" for i in range(1, 17)]


def world_rain_days(settings: Settings, start: date, end: date) -> dict[str, str]:
    """{date: intensity} with intensity 'heavy' (police calendar) or 'moderate' (IMD warning only)."""
    ev = json.loads(settings.src("police", "events_config").read_text(encoding="utf-8"))["events"]
    days = {e["date"]: "heavy" for e in ev if e["type"] == "HEAVY_RAIN" and start <= date.fromisoformat(e["date"]) <= end}
    w = settings.src("imd", "dir") / "imd_weather_warnings_chennai.csv"
    if w.exists():
        df = pd.read_csv(w, encoding="utf-8-sig")
        for r in df.itertuples():
            d = str(r.valid_from)[:10]
            if r.warning_colour in ("Yellow", "Orange", "Red") and start <= date.fromisoformat(d) <= end:
                days.setdefault(d, "heavy" if r.warning_colour in ("Orange", "Red") else "moderate")
    return dict(sorted(days.items()))


def write_inputs(settings: Settings, window_days: int = 180, end: date | None = None) -> dict:
    end = end or datetime.now(tz=pd.Timestamp.now(tz=IST).tz).date()
    start = end - timedelta(days=window_days + 10)
    days = world_rain_days(settings, start, end)
    STATE.mkdir(parents=True, exist_ok=True)
    rows = []
    d = start
    while d <= end:
        ds = d.isoformat()
        rng = random.Random(f"world-rain-{ds}")
        kind = days.get(ds)
        centre = rng.sample(PWD_CODES, 5)
        for c in PWD_CODES:
            if kind == "heavy":
                mm = rng.uniform(90, 170) if c in centre else rng.uniform(40, 95)
            elif kind == "moderate":
                mm = rng.uniform(8, 30)
            else:
                mm = rng.uniform(0, 3) if rng.random() < 0.25 else 0.0
            rows.append((ds, c, round(mm, 1)))
        d += timedelta(days=1)
    pwd_csv = STATE / "pwd_rainfall.csv"
    with open(pwd_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "taluk_code", "rainfall_mm"])
        w.writerows(rows)
    heavy = [k for k, v in days.items() if v == "heavy"]
    (STATE / "grievance_rain_days.txt").write_text(",".join(heavy), encoding="utf-8")
    return {"rain_days": days, "pwd_rainfall_csv": str(pwd_csv), "grievance_rain_days": heavy}


if __name__ == "__main__":
    from .util import load_settings
    print(json.dumps(write_inputs(load_settings()), indent=2))
