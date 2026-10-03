"""Scenario overlay: one shared world for the separately generated sources.

1. World calendar: rain days from every source (grievance generator rain days, police
   heavy-rain days, PWD flood spikes, IMD warnings), plus festivals and protests.
2. Footprint completion: on each world rain day, a source that shows no flood response
   gets the reports it would have produced. The size of the response is learned from
   that source's own rain days. Sources that already show the event have their existing
   records ADOPTED into the shared incident instead of duplicated.
3. Multi-source incidents (drain overflow, wall collapse, dengue cluster, electrical hazard,
   fallen tree) seen from 2-3 sources each.
4. News-anchored incidents: real news incidents that name a locality get departmental
   records a few hours before publication. The rest stay media-only.

Every planted row is is_synthetic = 1, is_overlay = 1. The ground truth
(world_event_id per member) goes to output/truth/ only; the linker never sees it.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import poisson

from . import severity as sev
from . import textproc as tp
from .geo import WardIndex
from .refdata import Reference
from .schema import ACTION_COLUMNS, EVENT_COLUMNS, TIMELINE_COLUMNS, conform
from .util import IST, Settings, haversine_m, log

LANDMARKS = ["the bus stop", "the government school", "the temple", "the ration shop", "the UPHC", "the market",
             "the church", "the park", "the water tank", "the mosque"]
LANDMARKS_TA = ["பேருந்து நிறுத்தம்", "அரசு பள்ளி", "கோயில்", "ரேஷன் கடை", "சுகாதார நிலையம்", "மார்க்கெட்", "தேவாலயம்", "பூங்கா"]

G_TEXT = {
    "Stagnation of Water": {
        "en": ["Rain water stagnating on {street} since {when}. Knee-deep water near {lm}, people cannot walk.",
               "Water logged on {street} after the rain. Vehicles stuck near {lm}. Please pump out the water."],
        "ta": ["{street} பகுதியில் மழைநீர் தேங்கி நிற்கிறது. {lmta} அருகில் நடக்க முடியவில்லை.",
               "மழைக்குப் பிறகு {street} சாலையில் முழங்கால் அளவு தண்ணீர் தேங்கியுள்ளது."],
        "tanglish": ["{street} la mazhai thanni thengi nikkudhu, {lm} pakkathula nadakka mudiyala. Seekiram edunga.",
                     "{street} romba thanni thengi irukku, vandi ellam stuck aagudhu."]},
    "Water entering Home/Shop": {
        "en": ["Rain water entering our houses on {street}. Elderly people and children stuck inside, near {lm}."],
        "ta": ["{street} வீடுகளுக்குள் மழைநீர் புகுந்துள்ளது. முதியோர் மற்றும் குழந்தைகள் சிக்கியுள்ளனர்."],
        "tanglish": ["{street} veetukulla thanni varudhu, periyavanga pasanga ulla maatikittanga."]},
    "Obstruction of Water Flow": {
        "en": ["Drain inlet blocked on {street}, water not flowing and road flooded near {lm}."],
        "ta": ["{street} வடிகால் அடைப்பால் தண்ணீர் வடியவில்லை."],
        "tanglish": ["{street} drain adaippu, thanni pogala."]},
    "Illegal Draining of Sewage to SWD / Open Site": {
        "en": ["Sewage overflowing from the drain on {street}, stench near {lm}.", "Drain overflowing on {street}, sewage on the road."],
        "ta": ["{street} கால்வாய் நிரம்பி கழிவுநீர் சாலையில் ஓடுகிறது."], "tanglish": ["{street} drain overflow aagi sewage road la odudhu."]},
    "Mosquito Menace": {
        "en": ["Mosquito menace very high on {street}; many fever cases in our street.", "Stagnant water and mosquitoes near {lm} on {street}; children have fever."],
        "ta": ["{street} பகுதியில் கொசு தொல்லை அதிகம், பலருக்கு காய்ச்சல்."], "tanglish": ["{street} la kosu thollai jaasthi, neraya peruku fever."]},
    "Public Health / Dengue / Malaria / Gastro Enteritis": {
        "en": ["Dengue cases reported in houses on {street}. Please do fogging and a fever survey."],
        "ta": ["{street} பகுதியில் டெங்கு காய்ச்சல் பரவுகிறது, கொசு மருந்து அடிக்கவும்."], "tanglish": ["{street} la dengue fever varudhu, fogging pannunga."]},
    "Electric shock due to street light": {
        "en": ["Live wire hanging from the street light pole on {street} near {lm}. Danger to children."],
        "ta": ["{street} மின் கம்பி அறுந்து தொங்குகிறது, {lmta} அருகில் ஆபத்து."], "tanglish": ["{street} la current wire thongudhu, bayama irukku."]},
    "Removal of Fallen Trees": {
        "en": ["Tree fell on {street} after the storm, blocking the road near {lm}."],
        "ta": ["{street} மரம் சாய்ந்து சாலை அடைப்பு."], "tanglish": ["{street} la maram vizhundhu road block aayiduchu."]},
    "Pot hole fill up / Repairs to the damaged surface": {
        "en": ["Big pothole on {street} near {lm}; two-wheelers are skidding."],
        "ta": ["{street} சாலையில் பெரிய பள்ளம், வாகனங்கள் சறுக்கி விழுகின்றன."], "tanglish": ["{street} road la periya pallam, bike ellam slip aagudhu."]},
    "Removal of Garbage": {
        "en": ["Garbage not cleared on {street} for days; stench near {lm}."],
        "ta": ["{street} குப்பை அகற்றப்படவில்லை, துர்நாற்றம் வீசுகிறது."], "tanglish": ["{street} la kuppai edukala, naatram thaanga mudiyala."]},
}
G_META = {  # sub type -> (complaint type, department)
    "Stagnation of Water": ("Water Stagnation", "Storm Water Drain Department"),
    "Water entering Home/Shop": ("Flood", "Storm Water Drain Department"),
    "Obstruction of Water Flow": ("Water Stagnation", "Storm Water Drain Department"),
    "Illegal Draining of Sewage to SWD / Open Site": ("Public Health", "Health Department"),
    "Mosquito Menace": ("Public Health", "Health Department"),
    "Public Health / Dengue / Malaria / Gastro Enteritis": ("Public Health", "Health Department"),
    "Electric shock due to street light": ("Street Light", "Electrical Department"),
    "Removal of Fallen Trees": ("Park and Playground", "Parks & Play Fields Department"),
    "Pot hole fill up / Repairs to the damaged surface": ("Road and Footpath", "Engineering Department (Town Planning & Building Permissions)"),
    "Removal of Garbage": ("Garbage", "Solid Waste Management Department"),
}
WORK_DAYS = {"Storm Water Drain Department": 5, "Health Department": 3, "Electrical Department": 2,
             "Parks & Play Fields Department": 5, "Solid Waste Management Department": 1,
             "Engineering Department (Town Planning & Building Permissions)": 7}
P_TEXT = {
    "WEATHER_EMERGENCY": "Caller reported waterlogging on {street}, {loc}; vehicles stranded and traffic affected.",
    "TRAFFIC_OBSTRUCTION": "Traffic held up on {street}, {loc} due to {cause}. Personnel deployed to divert vehicles.",
    "ROAD_ACCIDENT": "{veh} hit a two-wheeler on {street}, {loc}. {inj} injured and taken to hospital.",
    "OTHER": "{what} reported on {street}, {loc}; area cordoned off by police.",
}
W_TEXT = {
    "waterlogging": "Field staff report water stagnation on {street}, {loc}; pumping needed.",
    "canal_overflow": "Canal overflowing near {loc}; water entering nearby streets including {street}.",
    "blocked_drain": "Storm water drain blocked with silt near {street}, {loc}; overflow onto the road.",
    "wall_collapse": "Portion of a compound wall collapsed at {street}, {loc}. Area cordoned off.",
    "building_crack": "Cracks seen in an old building on {street}, {loc}; occupants asked to vacate.",
}


@dataclass
class Ctx:
    settings: Settings
    ref: Reference
    wards: WardIndex
    rng: np.random.Generator
    as_of: pd.Timestamp
    seq: dict


def _next(ctx: Ctx, key: str) -> int:
    ctx.seq[key] = ctx.seq.get(key, 0) + 1
    return ctx.seq[key]


def _jitter(ctx: Ctx, lat: float, lon: float, max_m: float) -> tuple[float, float]:
    r = max_m * math.sqrt(ctx.rng.random())
    a = ctx.rng.random() * 2 * math.pi
    return lat + r * math.cos(a) / 110_540, lon + r * math.sin(a) / (111_320 * math.cos(math.radians(lat)))


# ------------------------------------------------------------------ calendar --
def daily_flood_counts(events: pd.DataFrame) -> pd.DataFrame:
    fam = events["category_family"] == "FLOOD"
    e = events[fam & events["source"].isin(["grievance", "police", "pwd"]) & (events["is_overlay"] == 0)]
    c = e.groupby([e["reported_at"].dt.tz_convert(IST).dt.date, "source"]).size().unstack(fill_value=0)
    idx = pd.date_range(min(c.index), max(c.index), freq="D").date
    return c.reindex(idx, fill_value=0)


def build_calendar(ctx: Ctx, events: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    counts = daily_flood_counts(events)
    meta = json.loads(ctx.settings.src("grievances", "generation_meta").read_text(encoding="utf-8"))
    pol_ev = json.loads(ctx.settings.src("police", "events_config").read_text(encoding="utf-8"))["events"]
    declared = {"grievance": set(meta.get("rainDays", [])),
                "police": {e["date"] for e in pol_ev if e["type"] == "HEAVY_RAIN"}}
    base = {s: float(counts[s].median()) if s in counts else 0.0 for s in ("grievance", "police", "pwd")}
    reflect: dict[str, set] = {s: set() for s in base}
    for s in base:
        if s not in counts:
            continue
        lam = max(base[s], 0.5)
        for d, x in counts[s].items():
            if x >= 3 and poisson.sf(x - 1, lam) < 0.01:
                reflect[s].add(str(d))
        reflect[s] |= {d for d in declared.get(s, set()) if d in {str(i) for i in counts.index}}
    # IMD warnings (real) also mark rain days
    imd = events[(events["source"] == "imd")]
    imd_days = {str(d) for d in imd["occurred_at"].dt.tz_convert(IST).dt.date}
    # rain days are the ones a calendar declares; a spike in one source alone is a local event, not weather
    in_window = {str(i) for i in counts.index}
    world_days = sorted(((declared["grievance"] | declared["police"]) & in_window) | imd_days)
    # response size per source = mean excess on its own rain days
    uplift = {}
    for s in base:
        own = [counts.loc[pd.Timestamp(d).date(), s] - base[s] for d in reflect[s] if pd.Timestamp(d).date() in counts.index]
        uplift[s] = max(float(np.mean(own)) if own else 0.0, 2.0)
    rows = []
    fest = {e["date"]: e for e in pol_ev}
    for d in pd.date_range(counts.index.min(), counts.index.max(), freq="D"):
        ds = str(d.date())
        srcs = [s for s in base if ds in reflect[s]]
        ex = []
        for s in srcs:
            if s in counts and d.date() in counts.index:
                ex.append((counts.loc[d.date(), s] - base[s]) / uplift[s])
        intensity = float(np.clip(max(ex) if ex else 0.6, 0.5, 2.0)) if ds in world_days else 0.0
        e = fest.get(ds, {})
        rows.append({"date": ds, "rain_event": int(ds in world_days), "rain_intensity": round(intensity, 2),
                     "reflected_by": "|".join(srcs + (["imd"] if ds in imd_days else [])),
                     "declared_by": "|".join(s for s in declared if ds in declared[s]),
                     "festival": e.get("name") if e.get("type") == "FESTIVAL" else None,
                     "protest": e.get("name") if e.get("type") == "PROTEST" else None,
                     "monsoon_phase": "southwest" if d.month in (6, 7, 8, 9) else ("northeast" if d.month in (10, 11, 12) else "dry"),
                     "weekday": d.day_name()})
    cal = pd.DataFrame(rows)
    info = {"baseline_per_day": base, "uplift_per_rain_day": uplift, "world_rain_days": len(world_days),
            "reflected": {s: len(v) for s, v in reflect.items()}}
    return cal, info


# ------------------------------------------------------------------ builders --
def _lifecycle_grievance(ctx: Ctx, t0: pd.Timestamp, dept: str) -> list[tuple[pd.Timestamp, str, str, str]]:
    r = ctx.rng
    steps = [(t0, "Complaint Filed", "Citizen", "Complaint registered by citizen.")]
    t = t0 + pd.Timedelta(minutes=float(r.uniform(1, 30)))
    steps.append((t, "Pending Approval", "System", f"Forwarded to {dept} for approval."))
    t = t + pd.Timedelta(hours=float(np.clip(r.lognormal(math.log(20), 0.6), 2, 72)))
    steps.append((t, "Approved by Department Officer", "Department Officer", "Approved and assigned to the Assistant Engineer."))
    t = t + pd.Timedelta(hours=float(r.uniform(1, 24)))
    steps.append((t, "In Progress", "Department Officer", "Work order issued; team deployed."))
    t = t + pd.Timedelta(days=float(np.clip(r.lognormal(math.log(WORK_DAYS.get(dept, 4)), 0.5), 0.3, 20)))
    steps.append((t, "Completed - Pending Collector Verification", "Department Officer", "Work completed. Completion photo uploaded."))
    t = t + pd.Timedelta(hours=float(r.uniform(6, 96)))
    steps.append((t, "Verified by Collector", "Collector", "Verified with completion photo; closed."))
    return [s for s in steps if s[0] <= ctx.as_of]


def grievance(ctx: Ctx, t0, lat, lon, ward, street, area, sub, lang=None):
    if t0 > ctx.as_of:
        return None
    ctype, dept = G_META[sub]
    lang = lang or ctx.rng.choice(["en", "ta", "tanglish"], p=[0.6, 0.25, 0.15])
    tmpl = ctx.rng.choice(G_TEXT[sub][lang])
    text = tmpl.format(street=street.title(), when=ctx.rng.choice(["morning", "last night", "yesterday", "two days"]),
                       lm=ctx.rng.choice(LANDMARKS), lmta=ctx.rng.choice(LANDMARKS_TA))
    n = _next(ctx, "grv")
    code = f"OVL-{n:05d}"
    steps = _lifecycle_grievance(ctx, t0, dept)
    status = steps[-1][1]
    std = ctx.ref.std_status("grievance", status)
    cat, meth = ctx.ref.grievance_category(ctype, sub)
    row = {"event_id": f"GRV-{code}", "source": "grievance", "source_record_id": code, "is_synthetic": 1, "is_overlay": 1,
           "occurred_at": t0, "reported_at": t0, "time_precision": "filed_time", "title": f"{sub}, {street.title()}, Ward {ward}",
           "text": text, "lang": lang, "junk_flag": 0, "simhash": tp.simhash64(text), "category_src": ctype, "subcategory_src": sub,
           "category_code": cat, "category_method": meth, "category_conf": 1.0, "dept_src": dept, "lead_dept": ctx.ref.dept_code(dept),
           "is_actionable": 1, "lat": lat, "lon": lon, "loc_precision_m": 30.0, "place_text": f"{street.title()}, {area.title()}",
           "ward_no": ward, "geo_method": "pin", "geo_level": "point", "geo_conf": 1.0,
           "vulnerable_flags": "|".join(tp.vulnerable_flags(text)), "hazard_flag": int(tp.hazard(text)),
           "claims_prior_complaint": int(tp.repeat_claim(text)), "has_photo": int(ctx.rng.random() < 0.45),
           "status_src": status, "status_std": std, "status_at": steps[-1][0], "channel": "citizen_app", "source_reliability": 1.0,
           "first_action_at": steps[2][0] if len(steps) > 2 else None, "response_applicable": 1,
           "closed_at": steps[-1][0] if status == "Verified by Collector" else None, "reopen_count": 0,
           "officer": f"Assistant Engineer, Ward {ward}", "_street": street.upper(), "_area": area.upper(), "_locality": "", "_landmark": ""}
    row["response_minutes"] = round((row["first_action_at"] - t0).total_seconds() / 60, 1) if row["first_action_at"] is not None else None
    s = sev.grievance(row)
    row.update(severity_score=s[0], severity_level=s[1], severity_reasons=s[2])
    tl = [(row["event_id"], at, {"Complaint Filed": "First report", "Pending Approval": "Routed to department",
                                 "Approved by Department Officer": "Officer verified", "In Progress": "Work in progress",
                                 "Completed - Pending Collector Verification": "Awaiting verification",
                                 "Verified by Collector": "Resolved"}[st], ctx.ref.std_status("grievance", st), who, note, "grievance")
          for at, st, who, note in steps]
    return row, tl


def police(ctx: Ctx, t0, lat, lon, street, loc, cat, what=None):
    if t0 > ctx.as_of:
        return None
    n = _next(ctx, "pol")
    rid = f"OVL-{n:05d}"
    r = ctx.rng
    channel = r.choice(["control_room_112", "patrol"], p=[0.7, 0.3])
    resp = float(r.uniform(6, 35))
    closed = t0 + pd.Timedelta(hours=float(r.uniform(2, 72)))
    status = "CLOSED" if closed <= ctx.as_of else ("ACTION_TAKEN" if t0 + pd.Timedelta(minutes=resp) <= ctx.as_of else "REPORTED")
    injured = int(r.integers(1, 3)) if cat == "ROAD_ACCIDENT" else 0
    text = P_TEXT[cat].format(street=street.title(), loc=loc, cause=r.choice(["waterlogging", "a fallen tree", "a stalled bus"]),
                              veh=r.choice(["A car", "An MTC bus", "A lorry", "A cab"]), inj=injured, what=what or "Incident")
    code = {"WEATHER_EMERGENCY": "FLOOD_WATERLOGGING", "TRAFFIC_OBSTRUCTION": "TRAFFIC_OBSTRUCTION", "ROAD_ACCIDENT": "ROAD_ACCIDENT"}.get(cat)
    if cat == "OTHER":
        code = "BUILDING_SAFETY" if "collapse" in (what or "").lower() else ("STREETLIGHT_ELECTRICAL" if "shock" in (what or "").lower() else "POLICE_OTHER")
    row = {"event_id": f"POL-{rid}", "source": "police", "source_record_id": rid, "is_synthetic": 1, "is_overlay": 1,
           "occurred_at": t0 - pd.Timedelta(minutes=float(r.uniform(2, 20))), "reported_at": t0, "time_precision": "exact",
           "closed_at": closed if status == "CLOSED" else None, "title": text.split(";")[0].split(".")[0], "text": text, "lang": "en",
           "junk_flag": 0, "simhash": tp.simhash64(text), "category_src": cat, "category_code": code,
           "category_method": "crosswalk" if cat != "OTHER" else "keyword:overlay", "category_conf": 1.0, "dept_src": "Police",
           "lead_dept": "POL-GCP", "is_actionable": 1, "lat": lat, "lon": lon, "loc_precision_m": 50.0, "place_text": loc,
           "geo_method": "source_point", "geo_level": "point", "geo_conf": 1.0, "dead": 0, "injured": injured,
           "persons_affected": injured, "access_blocked": int(cat in ("WEATHER_EMERGENCY", "TRAFFIC_OBSTRUCTION")),
           "blockage_minutes": float(r.uniform(30, 180)) if cat in ("WEATHER_EMERGENCY", "TRAFFIC_OBSTRUCTION") else None,
           "weather_related": int(cat == "WEATHER_EMERGENCY"), "status_src": status, "status_std": ctx.ref.std_status("police", status),
           "status_at": closed if status == "CLOSED" else t0, "response_applicable": 1, "response_minutes": round(resp, 1),
           "first_action_at": t0 + pd.Timedelta(minutes=resp), "channel": channel, "source_reliability": 1.0, "officer": "SHO (nearest station)"}
    s = sev.police(row)
    row.update(severity_score=s[0], severity_level=s[1], severity_reasons=s[2])
    tl = [(row["event_id"], t0, "First report", "Open", channel, f"Reported via {channel.replace('_', ' ')}"),
          (row["event_id"], t0 + pd.Timedelta(minutes=resp), "Crew assigned", "In progress", "Police", "Patrol reached the spot")]
    if status == "CLOSED":
        tl.append((row["event_id"], closed, "Resolved", "Resolved", "Police", "Case closed"))
    return row, [t + ("police",) for t in tl if t[1] <= ctx.as_of]


def pwd(ctx: Ctx, t0, lat, lon, street, loc, itype, offices: pd.DataFrame, taluk: str | None):
    if t0 > ctx.as_of:
        return None
    n = _next(ctx, "pwd")
    iid = f"PWD-INC-OVL{n:04d}"
    r = ctx.rng
    text = W_TEXT[itype].format(street=street.title(), loc=loc)
    wing = "Buildings" if itype in ("wall_collapse", "building_crack") else "Water Resources"
    cand = offices[(offices["wing"] == wing) & offices["taluks"].str.contains(taluk or "~", regex=False)]
    cand = cand if len(cand) else offices[offices["wing"] == wing]
    off = cand.sample(1, random_state=int(r.integers(1e9))).iloc[0]
    assigned = t0 + pd.Timedelta(hours=float(r.uniform(1, 12)))
    accepted = assigned + pd.Timedelta(hours=float(r.uniform(1, 24)))
    completed = accepted + pd.Timedelta(days=float(r.uniform(1, 5)))
    verified = completed + pd.Timedelta(days=float(r.uniform(1, 3)))
    tstatus = ("verified" if verified <= ctx.as_of else "completed_pending_verification" if completed <= ctx.as_of
               else "in_progress" if accepted <= ctx.as_of else "assigned")
    istatus = "resolved" if tstatus == "verified" else ("in_progress" if tstatus != "assigned" else "open")
    std = "Awaiting verification" if tstatus == "completed_pending_verification" else ctx.ref.std_status("pwd_incident", istatus)
    affected = float(r.integers(40, 400))
    row = {"event_id": iid, "source": "pwd", "source_record_id": iid, "is_synthetic": 1, "is_overlay": 1, "occurred_at": t0,
           "reported_at": t0, "closed_at": verified if istatus == "resolved" else None, "time_precision": "exact",
           "title": f"{itype.replace('_', ' ').capitalize()}, {loc}", "text": text, "lang": "en", "junk_flag": 0,
           "simhash": tp.simhash64(text), "category_src": itype, "category_code": ctx.ref.cmap["pwd"][itype], "category_method": "crosswalk",
           "category_conf": 1.0, "dept_src": wing, "lead_dept": "PWD-BLD" if wing == "Buildings" else "PWD-WRD", "is_actionable": 1,
           "lat": lat, "lon": lon, "loc_precision_m": 50.0, "place_text": loc, "taluk_code": taluk, "geo_method": "source_point",
           "geo_level": "point", "geo_conf": 1.0, "dead": 0, "injured": 0, "persons_affected": affected, "affected_imputed": 0,
           "access_blocked": int(r.random() < 0.4), "service_disruption": r.choice(["none", "partial", "full"], p=[0.3, 0.5, 0.2]),
           "weather_related": int(itype in ("waterlogging", "canal_overflow")), "status_src": istatus, "status_std": std,
           "status_at": verified if istatus == "resolved" else t0, "first_action_at": assigned, "response_applicable": 1,
           "response_minutes": round((assigned - t0).total_seconds() / 60, 1), "assigned_office_id": off["office_id"],
           "officer": f"{off['designation']}, {off['office_name']} ({off['officer_name']})", "channel": r.choice(["field_staff", "control_room"]),
           "source_reliability": 1.0}
    s = sev.pwd(row)
    row.update(severity_score=s[0], severity_level=s[1], severity_reasons=s[2])
    act = {"action_id": f"PWD-TSK-OVL{n:04d}", "event_id": iid, "dept_code": row["lead_dept"], "office_id": off["office_id"],
           "owner": f"{off['designation']} ({off['officer_name']})", "text": f"Attend {itype.replace('_', ' ')} - {street.title()}, {loc}",
           "assigned_at": assigned, "due_at": assigned + pd.Timedelta(days=7), "status": ctx.ref.status["action_status"][tstatus],
           "created_by": "department", "origin": "pwd_task", "completed_at": completed if completed <= ctx.as_of else None,
           "verified_at": verified if verified <= ctx.as_of else None}
    tl = [(iid, t0, "First report", "Open", row["channel"], f"Reported by {row['channel'].replace('_', ' ')}"),
          (iid, assigned, "Crew assigned", "Assigned", off["office_id"], act["text"]),
          (iid, accepted, "Work accepted", "Assigned", off["office_id"], "Task accepted by field office"),
          (iid, completed, "Awaiting verification", "Awaiting verification", off["office_id"], "Work completed; photo uploaded"),
          (iid, verified, "Verified", "Resolved", "Verifier", "Verified; closed")]
    return row, [t + ("pwd",) for t in tl if t[1] <= ctx.as_of], (act if assigned <= ctx.as_of else None)


def hospital_alert(ctx: Ctx, t0, hosp: pd.Series, disease: str):
    if t0 > ctx.as_of:
        return None
    n = _next(ctx, "hsp")
    eid = f"{hosp['facility_id']}-OVL{n:03d}"
    days = int(ctx.rng.integers(3, 7))
    closed = t0 + pd.Timedelta(days=days)
    ongoing = closed > ctx.as_of
    row = {"event_id": eid, "source": "hospital", "source_record_id": eid, "is_synthetic": 1, "is_overlay": 1, "occurred_at": t0,
           "reported_at": t0, "closed_at": None if ongoing else closed, "time_precision": "day",
           "title": f"{disease} Surge at {hosp['name']}", "text": f"{hosp['name']}: {disease} surge; outpatient fever cases rising.",
           "lang": "en", "junk_flag": 0, "category_src": f"{disease} Surge", "category_code": "VECTOR_DISEASE",
           "category_method": "crosswalk:hospital_alert", "category_conf": 1.0, "dept_src": "Health Services", "lead_dept": "HLT-DMS",
           "is_actionable": 1, "lat": hosp["lat"], "lon": hosp["lon"], "loc_precision_m": 50.0, "place_text": hosp["name"],
           "taluk_code": hosp.get("taluk_code"), "geo_method": "facility", "geo_level": "point", "geo_conf": 1.0,
           "severity_score": 55.0, "severity_level": "High", "severity_reasons": "Hospital alert level Warning",
           "status_src": "active" if ongoing else "cleared", "status_std": "Open" if ongoing else "Resolved",
           "status_at": t0, "response_applicable": 0, "channel": "hospital_mis", "source_reliability": 1.0}
    tl = [(eid, t0, "First report", "Open", "Hospital MIS", f"{disease} surge (Warning)", "hospital")]
    if not ongoing:
        tl.append((eid, closed, "Resolved", "Resolved", "Hospital MIS", "Alert cleared", "hospital"))
    return row, tl


# -------------------------------------------------------------------- driver --
def build(settings: Settings, ref: Reference, wards: WardIndex, events: pd.DataFrame, docs: pd.DataFrame,
          offices: pd.DataFrame, hospitals: pd.DataFrame, as_of: pd.Timestamp) -> dict:
    cfg = settings.raw["overlay"]
    ctx = Ctx(settings, ref, wards, np.random.default_rng(cfg["seed"]), as_of, {})
    cal, info = build_calendar(ctx, events)
    base = events[events["is_overlay"] == 0]
    pinned = base[(base["source"] == "grievance") & (base["geo_method"] == "pin") & (base["_street"].fillna("") != "")]
    flood = base[base["category_family"] == "FLOOD"]
    # low-lying wards: flood reports per km2 across the history
    area = {w.ward_no: w.area_km2 for w in wards.wards}
    dens = flood.groupby("ward_no").size() / flood.groupby("ward_no").size().index.map(lambda w: max(area.get(int(w), 1.0), 0.3))
    ward_w = dens.reindex([w.ward_no for w in wards.wards]).fillna(0.1)
    ward_p = (ward_w / ward_w.sum()).to_numpy()
    ward_ids = np.array([w.ward_no for w in wards.wards])
    by_ward = {w: grp for w, grp in pinned.groupby("ward_no")}

    rows, tls, acts, truth = [], [], [], []

    def anchor_in_ward(w):
        grp = by_ward.get(w)
        if grp is None or not len(grp):
            c = wards.by_no[int(w)].centroid
            return c[0], c[1], "Main Road", "", int(w)
        a = grp.iloc[int(ctx.rng.integers(len(grp)))]
        return a["lat"], a["lon"], a["_street"], a["_area"] or a["_locality"], int(w)

    def near_street(lat, lon, max_m=600):
        """Street and area of the nearest pinned grievance (for realistic addresses)."""
        d = haversine_m(lat, lon, pinned["lat"].to_numpy(), pinned["lon"].to_numpy())
        i = int(np.argmin(d))
        if d[i] > max_m:
            return None
        p = pinned.iloc[i]
        return p["_street"], p["_area"] or p["_locality"], int(p["ward_no"])

    def add(kind, wid, member):
        if member is None:            # would fall after as_of
            return
        row, tl = member[0], member[1]
        rows.append(row)
        tls.extend(tl)
        if len(member) > 2 and member[2] is not None:
            acts.append(member[2])
        truth.append({"world_event_id": wid, "event_id": row["event_id"], "role": "planted", "kind": kind})

    def adopt(wid, kind, eids):
        for e in eids:
            truth.append({"world_event_id": wid, "event_id": e, "role": "adopted", "kind": kind})

    t_of = lambda d, lo, hi: pd.Timestamp(d, tz=IST) + pd.Timedelta(hours=float(ctx.rng.uniform(lo, hi)))
    lo_k, hi_k = cfg["rain_incidents_per_day"]

    # ---- 1. rain days
    for day in cal[cal["rain_event"] == 1].itertuples():
        ds = day.date
        if pd.Timestamp(ds, tz=IST) > as_of:
            continue
        reflected = set(day.reflected_by.split("|")) if day.reflected_by else set()
        k = int(np.clip(round((lo_k + (hi_k - lo_k) * min(max(day.rain_intensity, 0.5), 1.5) / 1.5)), lo_k, hi_k))
        # sources that did not respond get their usual rain-day volume; sources that did get one or two
        # records per shared incident, so every rain incident is seen from all three sources
        deficit = {s: (k * float(ctx.rng.uniform(1.0, 1.6)) if s in reflected else
                       info["uplift_per_rain_day"][s] * float(ctx.rng.uniform(0.6, 1.0)))
                   for s in ("grievance", "police", "pwd")}
        day_flood = flood[flood["reported_at"].dt.tz_convert(IST).dt.date.astype(str) == ds]
        existing = day_flood[day_flood["source"].isin(reflected) & day_flood["lat"].notna() & day_flood["ward_no"].notna()]
        centres: list[tuple[float, float]] = []
        adopted: set[str] = set()

        def far_enough(la, lo) -> bool:   # same-day incidents stay distinct (>= 2 km apart)
            return all(haversine_m(la, lo, c[0], c[1]) >= 2000 for c in centres)

        for i in range(k):
            wid = f"WEV-{ds.replace('-', '')}-R{i + 1:02d}"
            # epicentre: an existing flood report of a reflecting source that day, else a low-lying ward
            pick = None
            for _ in range(25):
                if len(existing) and ctx.rng.random() < 0.8:
                    a = existing.iloc[int(ctx.rng.integers(len(existing)))]
                    if a["event_id"] not in adopted and far_enough(a["lat"], a["lon"]):
                        pick = ("existing", a)
                        break
                else:
                    w = int(ctx.rng.choice(ward_ids, p=ward_p))
                    cand = anchor_in_ward(w)
                    if far_enough(cand[0], cand[1]):
                        pick = ("ward", cand)
                        break
            if pick is None:
                continue
            if pick[0] == "existing":
                a = pick[1]
                elat, elon, ew = a["lat"], a["lon"], int(a["ward_no"])
                ns = near_street(elat, elon)
                street, area_n = (ns[0], ns[1]) if ns else ("Main Road", "")
                near = existing[(haversine_m(elat, elon, existing["lat"].to_numpy(), existing["lon"].to_numpy()) <= 250)
                                & ~existing["event_id"].isin(adopted)]
                adopt(wid, "rain", near["event_id"].tolist())
                adopted |= set(near["event_id"])
            else:
                elat, elon, street, area_n, ew = pick[1]
            centres.append((elat, elon))
            t0 = t_of(ds, 4, 11) if ctx.rng.random() < 0.6 else t_of(ds, 15, 21)
            loc = area_n.title() or f"Ward {ew}"
            n_g =int(ctx.rng.poisson(deficit["grievance"] / k)) if deficit["grievance"] else 0
            for _ in range(n_g):
                la, lo = _jitter(ctx, elat, elon, 150)
                sub = ctx.rng.choice(["Stagnation of Water", "Water entering Home/Shop", "Obstruction of Water Flow"], p=[0.7, 0.2, 0.1])
                add("rain", wid, grievance(ctx, t0 + pd.Timedelta(hours=float(ctx.rng.uniform(0.3, 10))), la, lo, ew, street, area_n, sub))
            n_p = int(ctx.rng.poisson(deficit["police"] / k)) if deficit["police"] else 0
            for _ in range(n_p):
                la, lo = _jitter(ctx, elat, elon, 250)
                add("rain", wid, police(ctx, t0 + pd.Timedelta(hours=float(ctx.rng.uniform(0.2, 4))), la, lo, street, loc,
                                        ctx.rng.choice(["WEATHER_EMERGENCY", "TRAFFIC_OBSTRUCTION"], p=[0.65, 0.35])))
            n_w = int(ctx.rng.poisson(deficit["pwd"] / k)) if deficit["pwd"] else 0
            for _ in range(n_w):
                la, lo = _jitter(ctx, elat, elon, 300)
                tk = _taluk_of(ref, wards, la, lo)
                add("rain", wid, pwd(ctx, t0 + pd.Timedelta(hours=float(ctx.rng.uniform(1, 8))), la, lo, street, loc,
                                     ctx.rng.choice(["waterlogging", "canal_overflow"], p=[0.7, 0.3]), offices, tk))

    # ---- 2. multi-source incidents
    start = base["reported_at"].min()
    span_h = (as_of - start).total_seconds() / 3600
    kinds = ["drain_overflow", "wall_collapse", "dengue_cluster", "electrical_hazard", "fallen_tree"]
    hosp_in = hospitals[hospitals["lat"].notna()]
    for i in range(cfg["other_incidents"]):
        kind = kinds[i % len(kinds)]
        wid = f"WEV-M{i + 1:03d}"
        t0 = start + pd.Timedelta(hours=float(ctx.rng.uniform(24, span_h - 6)))
        if kind == "dengue_cluster" and len(hosp_in):
            h = hosp_in.iloc[int(ctx.rng.integers(len(hosp_in)))]
            elat, elon = _jitter(ctx, h["lat"], h["lon"], 900)
            ns = near_street(elat, elon, 1500)
            if not ns:
                continue
            street, area_n, ew = ns
        else:
            ew = int(ctx.rng.choice(ward_ids, p=ward_p))
            elat, elon, street, area_n, ew = anchor_in_ward(ew)
        loc = area_n.title() or f"Ward {ew}"
        tk = _taluk_of(ref, wards, elat, elon)
        if kind == "drain_overflow":
            for _ in range(int(ctx.rng.integers(2, 5))):
                la, lo = _jitter(ctx, elat, elon, 120)
                add("multi", wid, grievance(ctx, t0 + pd.Timedelta(hours=float(ctx.rng.uniform(0, 30))), la, lo, ew, street, area_n,
                                            "Illegal Draining of Sewage to SWD / Open Site"))
            la, lo = _jitter(ctx, elat, elon, 150)
            add("multi", wid, pwd(ctx, t0 + pd.Timedelta(hours=float(ctx.rng.uniform(2, 40))), la, lo, street, loc, "blocked_drain", offices, tk))
        elif kind == "wall_collapse":
            la, lo = _jitter(ctx, elat, elon, 40)
            add("multi", wid, pwd(ctx, t0 + pd.Timedelta(hours=float(ctx.rng.uniform(0.5, 6))), la, lo, street, loc, "wall_collapse", offices, tk))
            la, lo = _jitter(ctx, elat, elon, 60)
            add("multi", wid, police(ctx, t0 + pd.Timedelta(minutes=float(ctx.rng.uniform(5, 60))), la, lo, street, loc, "OTHER", "Wall collapse"))
        elif kind == "dengue_cluster":
            for _ in range(int(ctx.rng.integers(3, 7))):
                la, lo = _jitter(ctx, elat, elon, 600)
                add("multi", wid, grievance(ctx, t0 + pd.Timedelta(hours=float(ctx.rng.uniform(0, 160))), la, lo, ew, street, area_n,
                                            ctx.rng.choice(["Mosquito Menace", "Public Health / Dengue / Malaria / Gastro Enteritis"])))
            add("multi", wid, hospital_alert(ctx, (t0 + pd.Timedelta(days=float(ctx.rng.uniform(4, 10)))).normalize(), h,
                                             ctx.rng.choice(["Dengue", "Viral Fever"])))
        elif kind == "electrical_hazard":
            for _ in range(int(ctx.rng.integers(1, 4))):
                la, lo = _jitter(ctx, elat, elon, 80)
                add("multi", wid, grievance(ctx, t0 + pd.Timedelta(hours=float(ctx.rng.uniform(0, 12))), la, lo, ew, street, area_n,
                                            "Electric shock due to street light"))
            la, lo = _jitter(ctx, elat, elon, 80)
            add("multi", wid, police(ctx, t0 + pd.Timedelta(hours=float(ctx.rng.uniform(0.2, 8))), la, lo, street, loc, "OTHER",
                                     "Person received an electric shock from a snapped wire"))
        elif kind == "fallen_tree":
            for _ in range(int(ctx.rng.integers(1, 3))):
                la, lo = _jitter(ctx, elat, elon, 60)
                add("multi", wid, grievance(ctx, t0 + pd.Timedelta(hours=float(ctx.rng.uniform(0, 6))), la, lo, ew, street, area_n,
                                            "Removal of Fallen Trees"))
            la, lo = _jitter(ctx, elat, elon, 80)
            add("multi", wid, police(ctx, t0 + pd.Timedelta(hours=float(ctx.rng.uniform(0.1, 3))), la, lo, street, loc, "TRAFFIC_OBSTRUCTION"))

    # ---- 3. news-anchored incidents
    ok_cat = {"FLOOD_WATERLOGGING": ("g", "Stagnation of Water", "waterlogging"), "DRAINAGE_SEWAGE": ("g", "Illegal Draining of Sewage to SWD / Open Site", "blocked_drain"),
              "ROAD_DAMAGE": ("g", "Pot hole fill up / Repairs to the damaged surface", None), "SOLID_WASTE": ("g", "Removal of Garbage", None),
              "STREETLIGHT_ELECTRICAL": ("g", "Electric shock due to street light", None), "VECTOR_DISEASE": ("g", "Mosquito Menace", None),
              "TREES_PARKS": ("g", "Removal of Fallen Trees", None), "ROAD_ACCIDENT": ("p", "ROAD_ACCIDENT", None),
              "BUILDING_SAFETY": ("w", None, "wall_collapse")}
    cand = docs[(docs["is_incident"] == 1) & (docs["geo_level"] == "locality") & docs["category_code"].isin(ok_cat.keys())
                & (docs["published_at"] >= start + pd.Timedelta(days=1)) & (docs["story_role"] == "first_report")]
    cand = cand.sample(min(cfg["news_anchored"], len(cand)), random_state=cfg["seed"]) if len(cand) else cand
    for j, d in enumerate(cand.itertuples()):
        ns = near_street(d.lat, d.lon, 1200)
        if not ns:
            continue
        street, area_n, ew = ns
        wid = f"WEV-N{j + 1:03d}"
        truth.append({"world_event_id": wid, "event_id": f"NEWS-{d.doc_id}", "role": "adopted", "kind": "news"})
        kind, sub, wtype = ok_cat[d.category_code]
        loc = d.place_text
        tk = _taluk_of(ref, wards, d.lat, d.lon)
        pub = d.published_at
        for _ in range(int(ctx.rng.integers(2, 5)) if kind == "g" else 1):
            la, lo = _jitter(ctx, d.lat, d.lon, 700)
            t0 = pub - pd.Timedelta(hours=float(ctx.rng.uniform(2, 30)))
            if kind == "g":
                add("news", wid, grievance(ctx, t0, la, lo, ew, street, area_n, sub))
            elif kind == "p":
                add("news", wid, police(ctx, t0, la, lo, street, loc, sub))
        if wtype:
            la, lo = _jitter(ctx, d.lat, d.lon, 700)
            add("news", wid, pwd(ctx, pub - pd.Timedelta(hours=float(ctx.rng.uniform(1, 20))), la, lo, street, loc, wtype, offices, tk))

    ev = pd.DataFrame(rows)
    for c in ("occurred_at", "reported_at", "closed_at", "status_at", "first_action_at"):
        if c in ev:
            ev[c] = pd.to_datetime(ev[c], utc=True).dt.tz_convert(IST).dt.floor("s")
    extras = ev[[c for c in ("_street", "_area", "_locality", "_landmark") if c in ev.columns]]
    evc = conform(ev, EVENT_COLUMNS)
    for c in extras.columns:
        evc[c] = extras[c].values
    tl = pd.DataFrame(tls, columns=["event_id", "at", "step", "status_std", "actor", "note", "source"])
    tl["at"] = pd.to_datetime(tl["at"], utc=True).dt.tz_convert(IST).dt.floor("s")
    ac = conform(pd.DataFrame(acts), ACTION_COLUMNS)
    tr = pd.DataFrame(truth)
    log.info("overlay: %d world rain days, %d planted records (%s), %d adopted records, %d world events",
             info["world_rain_days"], len(evc), evc["source"].value_counts().to_dict(),
             int((tr["role"] == "adopted").sum()) if len(tr) else 0, tr["world_event_id"].nunique() if len(tr) else 0)
    return {"events": evc, "timeline": conform(tl, TIMELINE_COLUMNS), "actions": ac, "truth": tr, "calendar": cal, "info": info}


def _taluk_of(ref: Reference, wards: WardIndex, lat: float, lon: float) -> str | None:
    t = ref.taluks[ref.taluks["in_district"] == 1].dropna(subset=["lat"])
    d = (t["lat"] - lat) ** 2 + (t["lon"] - lon) ** 2
    return t.iloc[int(np.argmin(d.to_numpy()))]["taluk_code"]
