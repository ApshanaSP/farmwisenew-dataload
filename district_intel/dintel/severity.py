"""Event severity: how serious the event itself is, from static facts only.

Age, deadlines, corroboration and growth are NOT here; they belong to incident
priority, which is computed when read so it never goes stale.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from . import textproc as tp
from .refdata import Reference, severity_level


def _finish(score: float, reasons: list[str], cap: float = 100) -> tuple[float, str, str]:
    score = float(min(score, cap))
    return score, severity_level(score), "; ".join(reasons)


def grievance(row: dict[str, Any]) -> tuple[float, str, str]:
    sub = row.get("subcategory_src") or ""
    cat = row.get("category_src") or ""
    text = f"{row.get('title') or ''} {row.get('text') or ''}"
    place = f"{text} {row.get('_landmark') or ''}"
    reasons: list[str] = []
    if cat == "Flood" or tp.HIGH_RISK_SUB.search(sub):
        score = 35; reasons.append(f"High-risk issue: {sub} (+35)")
    elif cat in ("Tax and Licence", "Voter ID") or tp.LOW_SUB.search(sub):
        score = 5; reasons.append(f"Administrative issue: {sub} (+5)")
    elif tp.ELEVATED_SUB.search(sub):
        score = 25; reasons.append(f"Public-safety or sanitation issue: {sub} (+25)")
    else:
        score = 15; reasons.append(f"Civic issue: {sub or cat} (+15)")
    if tp.hazard(text):
        score += 15; reasons.append("Describes an immediate hazard (+15)")
    vul = tp.vulnerable_flags(place)
    if vul:
        pts = min(8 * len(vul), 24); score += pts; reasons.append(f"Affects {', '.join(vul)} (+{pts})")
    if tp.repeat_claim(text):
        score += 10; reasons.append("Citizen reports complaining before (+10)")
    return _finish(score, reasons)


POLICE_BASE = {"MURDER": 70, "CRIMES_AGAINST_WOMEN": 55, "ASSAULT": 45, "WEATHER_EMERGENCY": 45, "MISSING_PERSON": 40,
               "ROAD_ACCIDENT": 35, "PROTEST_LAW_AND_ORDER": 35, "CHAIN_SNATCHING": 30, "DRUGS_ILLICIT_LIQUOR": 30,
               "THEFT_BURGLARY": 25, "CYBER_FRAUD": 20, "TRAFFIC_OBSTRUCTION": 20, "PUBLIC_NUISANCE": 15, "OTHER": 15}


def police(row: dict[str, Any]) -> tuple[float, str, str]:
    cat = row.get("category_src") or "OTHER"
    score = POLICE_BASE.get(cat, 15)
    reasons = [f"{cat.replace('_', ' ').title()} (+{score})"]
    dead = int(row.get("dead") or 0)
    inj = int(row.get("injured") or 0)
    if dead > 0:
        score = max(score + 30, 70); reasons.append(f"{dead} fatalit{'y' if dead == 1 else 'ies'} (Severe)")
    if inj > 0:
        pts = min(8 * inj, 24); score += pts; reasons.append(f"{inj} injured (+{pts})")
    if row.get("_vulnerable_victim"):
        score += 10; reasons.append("Vulnerable victim (+10)")
    if row.get("_weapon"):
        score += 10; reasons.append("Weapon involved (+10)")
    if row.get("access_blocked") and (row.get("blockage_minutes") or 0) > 60:
        score += 10; reasons.append(f"Road blocked {int(row['blockage_minutes'])} min (+10)")
    if (row.get("crowd_estimate") or 0) > 1000:
        score += 10; reasons.append(f"Crowd of about {int(row['crowd_estimate'])} (+10)")
    return _finish(score, reasons)


PWD_BASE = {"bund_breach": 60, "wall_collapse": 55, "canal_overflow": 45, "lake_surplus": 45, "bridge_damage": 40,
            "waterlogging": 35, "sluice_failure": 35, "building_crack": 30, "blocked_drain": 25, "encroachment": 15}


def pwd(row: dict[str, Any]) -> tuple[float, str, str]:
    t = row.get("category_src") or ""
    score = PWD_BASE.get(t, 20)
    reasons = [f"{t.replace('_', ' ')} (+{score})"]
    if row.get("dead") or row.get("injured"):
        score = max(score, 70); reasons.append("Casualty reported (Severe)")
    sd = row.get("service_disruption")
    if sd == "full":
        score += 15; reasons.append("Full service disruption (+15)")
    elif sd == "partial":
        score += 5; reasons.append("Partial service disruption (+5)")
    if row.get("access_blocked"):
        score += 10; reasons.append("Access blocked (+10)")
    pa = row.get("persons_affected") or 0
    if pa >= 500:
        score += 15; reasons.append(f"About {int(pa)} people affected (+15)")
    elif pa >= 100:
        score += 10; reasons.append(f"About {int(pa)} people affected (+10)")
    vul = tp.vulnerable_flags(row.get("text") or "")
    if vul:
        pts = min(8 * len(vul), 16); score += pts; reasons.append(f"Affects {', '.join(vul)} (+{pts})")
    return _finish(score, reasons)


def from_category(row: dict[str, Any], ref: Reference, cap_unconfirmed: bool) -> tuple[float, str, str]:
    code = row.get("category_code") or "OTHER"
    base = ref.cat.get(code, ref.cat["OTHER"])["base_severity"]
    score = float(base)
    reasons = [f"{ref.cat.get(code, ref.cat['OTHER'])['label']} (+{base})"]
    dead = int(row.get("dead") or 0)
    inj = int(row.get("injured") or 0)
    if dead:
        score = max(score + 30, 70); reasons.append(f"{dead} reported dead")
    if inj >= 2:
        score += 10; reasons.append(f"{inj} reported injured (+10)")
    if row.get("hazard_flag"):
        score += 10; reasons.append("Hazard described (+10)")
    vul = row.get("vulnerable_flags") or []
    if isinstance(vul, str):
        vul = [v for v in vul.split("|") if v]
    if vul:
        pts = min(8 * len(vul), 16); score += pts; reasons.append(f"Affects {', '.join(vul)} (+{pts})")
    cap = 69 if cap_unconfirmed else 100
    if cap_unconfirmed and score > cap:
        reasons.append("Capped at High until a department or officer confirms it")
    return _finish(score, reasons, cap)


def apply(df: pd.DataFrame, fn, *args) -> pd.DataFrame:
    res = [fn(r, *args) for r in df.to_dict("records")]
    df["severity_score"] = [r[0] for r in res]
    df["severity_level"] = [r[1] for r in res]
    df["severity_reasons"] = [r[2] for r in res]
    return df
