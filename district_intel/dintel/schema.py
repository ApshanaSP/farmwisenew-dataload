"""Canonical column lists. Every loader conforms its output to these."""
from __future__ import annotations

import pandas as pd

EVENT_COLUMNS = [
    # identity & provenance
    "event_id", "source", "source_record_id", "deep_link", "snapshot_sha256", "is_synthetic", "is_overlay",
    # time
    "occurred_at", "reported_at", "closed_at", "time_precision", "issue_age_claimed_days",
    # text
    "title", "text", "lang", "junk_flag", "simhash",
    # classification
    "category_src", "subcategory_src", "category_code", "category_family", "category_conf", "category_method",
    "dept_src", "lead_dept", "support_depts", "is_actionable",
    # location
    "lat", "lon", "loc_precision_m", "place_text", "ward_no", "zone_no", "taluk_code", "taluk_src",
    "geo_level", "geo_method", "geo_conf", "in_district",
    # impact
    "dead", "injured", "persons_affected", "affected_imputed", "vulnerable_flags", "hazard_flag",
    "access_blocked", "blockage_minutes", "service_disruption", "crowd_estimate", "weather_related",
    "claims_prior_complaint", "has_photo",
    # severity (the event itself; age and deadlines live in incident priority)
    "severity_score", "severity_level", "severity_reasons",
    # status
    "status_src", "status_std", "status_at", "first_action_at", "response_applicable", "response_minutes",
    "rejection_reason_code", "reopen_count", "assigned_office_id", "officer",
    # reporter & credibility
    "channel", "reporter_hash", "source_reliability",
    # linking
    "dup_group_id", "incident_id", "link_prob", "link_method",
    # cross references kept from the source
    "ext_ref", "ext_ref_status",
]

TIMELINE_COLUMNS = ["event_id", "at", "step", "status_std", "actor", "note", "source"]
ACTION_COLUMNS = ["action_id", "event_id", "incident_id", "dept_code", "office_id", "owner", "text", "sop_step",
                  "assigned_at", "due_at", "status", "created_by", "origin", "completed_at", "verified_at", "evidence"]
OBS_COLUMNS = ["metric", "value", "unit", "place_type", "place_id", "place_name", "lat", "lon", "ward_no", "zone_no",
               "taluk_code", "observed_at", "period", "source", "quality", "is_synthetic", "detail"]
DOC_COLUMNS = [
    "doc_id", "canonical_url", "publisher_url", "publisher", "publisher_domain", "publisher_tier", "reliability",
    "lang", "title", "summary", "body", "body_status", "published_at", "first_fetched_at", "feed_sightings",
    "sightings_count", "simhash", "story_id", "story_role", "outlet_count", "is_district", "statewide_dateline",
    "report_type", "is_incident", "incident_conf", "incident_method", "category_code", "category_conf", "category_method",
    "dept_src", "places", "place_text", "ward_no", "zone_no", "taluk_code", "lat", "lon", "geo_level", "geo_conf",
    "dead", "injured", "event_id", "linked_incident_id", "source_kind",
]


def conform(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for c in columns:
        if c not in df.columns:
            df[c] = None
    return df[columns].copy()
