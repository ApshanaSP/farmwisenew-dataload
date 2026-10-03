"""Grievance portal (GCC) -> events + timeline.

Personal data (name, mobile, email, address) is not copied. The mobile number is
reduced to a keyed hash so repeat complainants can be counted without identifying them.
"""
from __future__ import annotations

import os
import re

import numpy as np
import pandas as pd

from .. import severity as sev
from .. import textproc as tp
from ..refdata import Reference
from ..schema import EVENT_COLUMNS, TIMELINE_COLUMNS, conform
from ..util import Settings, clean_str, hmac_hash, sha256_file, to_ist

REJECT_REASONS = [
    ("duplicate", re.compile(r"duplicate of", re.I)),
    ("not_jurisdiction", re.compile(r"jurisdiction|tneb|tangedco|metrowater|highways", re.I)),
    ("insufficient_details", re.compile(r"insufficient|unable to identify|not clear", re.I)),
    ("private_property", re.compile(r"private property|private land", re.I)),
    ("photo_mismatch", re.compile(r"photo does not match|photo mismatch", re.I)),
    ("work_unsatisfactory", re.compile(r"not satisfactory|unsatisfactory|redo", re.I)),
]
ASSIGNED = re.compile(r"(?:assigned|forwarded) to (?:the )?([^.;]+?)(?:[.;]|$)", re.I)
DUP_OF = re.compile(r"Duplicate of (\d{4}-\d{3}[A-Z]{3})")


def load(settings: Settings, ref: Reference) -> dict[str, pd.DataFrame]:
    path = settings.src("grievances", "complaints")
    g = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    h = pd.read_csv(settings.src("grievances", "history"), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    salt = os.environ.get("DINTEL_HASH_SALT", "chennai-district-intel")
    snap = sha256_file(path)

    h["at"] = to_ist(h["Changed On"])
    h = h.sort_values(["Complaint No", "at"]).reset_index(drop=True)

    ev = pd.DataFrame()
    ev["source_record_id"] = g["Complaint No"]
    ev["event_id"] = "GRV-" + g["Complaint No"]
    ev["source"] = "grievance"
    ev["deep_link"] = [settings.raw["sources"]["grievances"]["deep_link"].format(code=c) for c in g["Complaint No"]]
    ev["snapshot_sha256"] = snap
    ev["is_synthetic"] = pd.to_numeric(g["Is Synthetic"], errors="coerce").fillna(0).astype(int)
    ev["is_overlay"] = 0
    ev["reported_at"] = to_ist(g["Filed On"])
    ev["occurred_at"] = ev["reported_at"]
    ev["time_precision"] = "filed_time"
    ev["text"] = g["Details"]
    ev["issue_age_claimed_days"] = [tp.claimed_age_days(t) for t in g["Details"]]
    sub = g["Complaint Sub Type"]
    street = g["Street"].str.strip()
    ev["title"] = np.where(g["Title"].str.strip().str.lower() == sub.str.strip().str.lower(),
                           sub + ", " + street.str.title() + ", Ward " + g["Ward"], g["Title"])
    ev["lang"] = g["Language"].replace("", None).fillna(pd.Series([tp.language_of(t) for t in g["Details"]]))
    ev["junk_flag"] = [int(tp.is_junk(t)) for t in g["Details"]]
    ev["simhash"] = [tp.simhash64(t) for t in g["Details"]]
    ev["category_src"] = g["Complaint Type"]
    ev["subcategory_src"] = sub
    cm = [ref.grievance_category(t, s) for t, s in zip(g["Complaint Type"], sub)]
    ev["category_code"] = [c for c, _ in cm]
    ev["category_method"] = [m if c != "OTHER" else "pending_classifier" for c, m in cm]
    ev["category_conf"] = [1.0 if c != "OTHER" else None for c, _ in cm]
    ev["dept_src"] = g["Department"]
    ev["lead_dept"] = [ref.dept_code(d) or "GCC-GAD" for d in g["Department"]]
    ev["is_actionable"] = (g["Status"] != "Rejected").astype(int)
    ev["lat"] = pd.to_numeric(g["Latitude"], errors="coerce")
    ev["lon"] = pd.to_numeric(g["Longitude"], errors="coerce")
    ev["loc_precision_m"] = np.where(ev["lat"].notna(), 30.0, np.nan)
    ev["place_text"] = [", ".join(p for p in (clean_str(a).title(), clean_str(b).title(), clean_str(c).title()) if p)
                        for a, b, c in zip(g["Street"], g["Locality"], g["Area"])]
    ev["ward_no"] = pd.to_numeric(g["Ward"], errors="coerce")
    ev["zone_no"] = pd.to_numeric(g["Zone No"], errors="coerce")
    ev["geo_method"] = np.where(ev["lat"].notna(), "pin", "citizen_selected_ward")
    ev["geo_level"] = np.where(ev["lat"].notna(), "point", "ward")
    ev["geo_conf"] = np.where(ev["lat"].notna(), 1.0, 0.9)
    place = g["Details"] + " " + g["Landmark"]
    ev["vulnerable_flags"] = ["|".join(tp.vulnerable_flags(t)) for t in place]
    ev["hazard_flag"] = [int(tp.hazard(t)) for t in g["Details"]]
    ev["claims_prior_complaint"] = [int(tp.repeat_claim(t)) for t in g["Details"]]
    ev["has_photo"] = (g["Photo Attached"] == "Yes").astype(int)
    ev["weather_related"] = 0
    ev["status_src"] = g["Status"]
    ev["status_std"] = [ref.std_status("grievance", s) for s in g["Status"]]
    ev["status_at"] = to_ist(g["Last Updated"])
    ev["channel"] = "citizen_app"
    ev["reporter_hash"] = [None if a == "Yes" else hmac_hash(m, salt) for a, m in zip(g["Anonymous"], g["Mobile"])]
    ev["source_reliability"] = 1.0
    ev["ext_ref"] = g["Duplicate Group"].where(g["Duplicate Group"] != g["Complaint No"], None)
    ev["ext_ref_status"] = np.where(ev["ext_ref"].notna(), "source_duplicate_group", None)

    # severity uses the landmark as well (vulnerable places are often written there)
    tmp = ev.assign(_landmark=g["Landmark"])
    tmp = sev.apply(tmp, sev.grievance)
    ev["severity_score"], ev["severity_level"], ev["severity_reasons"] = tmp["severity_score"], tmp["severity_level"], tmp["severity_reasons"]

    # ---- from history: first action, close time, rejection reason, officer, officer duplicate decisions
    first_action = h[h["Status"].isin(["Approved by Department Officer", "Rejected"])].groupby("Complaint No")["at"].min()
    closed = h[h["Status"].isin(["Verified by Collector", "Rejected"])].groupby("Complaint No")["at"].max()
    rej = h[h["Status"] == "Rejected"].groupby("Complaint No")["Remarks"].last()
    appr = h[h["Status"] == "Approved by Department Officer"].groupby("Complaint No")["Remarks"].last()
    idx = g["Complaint No"]
    ev["first_action_at"] = first_action.reindex(idx).reset_index(drop=True)
    ev["closed_at"] = closed.reindex(idx).reset_index(drop=True)
    ev["response_applicable"] = 1
    ev["response_minutes"] = ((ev["first_action_at"] - ev["reported_at"]).dt.total_seconds() / 60).round(1)

    def reason(txt: str | float) -> str | None:
        if not isinstance(txt, str):
            return None
        for code, rx in REJECT_REASONS:
            if rx.search(txt):
                return code
        return "other"

    ev["rejection_reason_code"] = [reason(r) for r in rej.reindex(idx).values]
    officer = []
    for r in appr.reindex(idx).values:
        m = ASSIGNED.search(r) if isinstance(r, str) else None
        officer.append(m.group(1).strip() if m else None)
    ev["officer"] = officer
    dup_of = [DUP_OF.search(r).group(1) if isinstance(r, str) and DUP_OF.search(r) else None for r in rej.reindex(idx).values]
    ev["_officer_dup_of"] = dup_of
    ev["reopen_count"] = 0

    # ---- timeline
    tl = pd.DataFrame({
        "event_id": "GRV-" + h["Complaint No"], "at": h["at"],
        "step": h["Status"].map({"Complaint Filed": "First report", "Pending Approval": "Routed to department",
                                 "Approved by Department Officer": "Officer verified", "In Progress": "Work in progress",
                                 "Completed - Pending Collector Verification": "Awaiting verification",
                                 "Verified by Collector": "Resolved", "Rejected": "Rejected"}),
        "status_std": [ref.std_status("grievance", s) for s in h["Status"]],
        "actor": h["Changed By Role"], "note": h["Remarks"], "source": "grievance"})

    ev_c = conform(ev, EVENT_COLUMNS)
    ev_c["_officer_dup_of"] = ev["_officer_dup_of"].values
    ev_c["_landmark"] = g["Landmark"].values
    ev_c["_street"] = g["Street"].str.strip().str.upper().values
    ev_c["_locality"] = g["Locality"].str.strip().str.upper().values
    ev_c["_area"] = g["Area"].str.strip().str.upper().values
    return {"events": ev_c, "timeline": conform(tl, TIMELINE_COLUMNS)}
