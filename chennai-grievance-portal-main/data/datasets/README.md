# Grievance dataset: data dictionary

The dataset behind the Collector and Department Officer intelligence dashboards.
It holds every complaint and every status change: real ones filed through the portal,
plus 90 days of synthetic history so the dashboards have something to work with
from day one.

> **Personal data.** Real rows contain complainants' names, mobile numbers, email
> addresses and map pins. This folder is git-ignored (except this README). Treat
> exported copies accordingly.

## Files

| File | Rows | Written by |
|---|---|---|
| `grievances.csv` | one per complaint, oldest first | `dataset:export` (full rebuild) and live append after filing |
| `grievance_status_history.csv` | one per status change, oldest first | same |
| `_truth/duplicate_truth.csv` | one per complaint in a planted duplicate or hotspot group | `dataset:generate` only |
| `generation_meta.json` | seed, date range, snapshot time, rain days | `dataset:generate` |
| `GENERATION_REPORT.md` | counts, duplicate/hotspot stats, validation results, sample rows | `dataset:generate` |

**Format.** UTF-8 **with a byte-order mark** (so Excel shows Tamil correctly),
RFC-4180 quoting, CRLF line endings, header row always present. A blank field
means NULL. Timestamps are IST wall-clock `YYYY-MM-DD HH:mm` (MySQL runs on IST).

**Location.** `DATASET_DIR` (env) overrides the default `data/datasets`. A
relative path resolves against the project root.

## Commands

```bash
npm run dataset:generate                 # insert synthetic history, export, validate, report
npm run dataset:generate -- --reset-synthetic     # replace an earlier synthetic run
npm run dataset:generate -- --dry-run    # print stats only; writes nothing
npm run dataset:generate -- --csv-only   # write CSVs only; MySQL untouched
npm run dataset:export                   # rebuild both CSVs from MySQL (temp file + rename)
npm run dataset:validate                 # 13 checks, exit code 1 on failure
npm run dataset:unseed                   # delete is_synthetic = 1 rows only, then re-export
```

Generator options: `--days 90`, `--end YYYY-MM-DD` (default today IST),
`--per-day 90` (overall average, including rain surges, bursts and duplicates),
`--seed 42`, `--rain-days 2026-08-14,2026-09-02,...`, `--no-validate`.
The same seed, end date and reference data always produce the same output.

## How rows get here

1. **Filing.** After `POST /api/complaints` commits, `appendComplaintToDataset()`
   (`src/lib/dataset/append.ts`) appends the complaint to `grievances.csv` and its
   history to `grievance_status_history.csv`. It runs fire-and-forget: a dataset
   failure is logged with `console.error` and never fails or slows the filing.
   Writes go through a single in-process queue, and a code already in the file
   is skipped.
2. **Status changes.** `appendStatusChangeToDataset(code)` appends the newest
   history row.
   **TODO: call after every status update** in the upcoming Officer and Collector
   APIs. No route changes status yet.
3. **Rebuild.** `grievances.csv` records a complaint as it was **when filed**; later
   status changes only add history lines. `npm run dataset:export` rebuilds both
   files from MySQL and fixes that drift. Run it before analysis, or on a schedule.

All three paths use the same SELECT (`scripts/lib/dataset-sql.js`), which is also
what `npm run export:excel` uses, so the columns cannot drift apart.

## `grievances.csv`

Columns 1–31 are exactly the Complaints sheet of `npm run export:excel`, in the
same order and with the same formatting. Columns 32–38 are extensions for the
dashboards.

| # | Column | Type / format | Source and allowed values |
|---|---|---|---|
| 1 | Complaint No | `YYYY-NNNXXX` | `complaints.complaint_code`: 4-digit year of filing, 3 digits, 3 uppercase letters. Unique. |
| 2 | Filed On | `YYYY-MM-DD HH:mm` | `created_at` |
| 3 | Status | enum | `Complaint Filed`, `Pending Approval`, `Approved by Department Officer`, `In Progress`, `Completed - Pending Collector Verification`, `Verified by Collector`, `Rejected` |
| 4 | Rejected At | enum / blank | `rejected_stage`: `Department Officer`, `Collector`. Set only when Status = Rejected. |
| 5 | Complaint Type | string | `complaint_categories.name`: 17 GCC categories + local `Other` |
| 6 | Complaint Sub Type | string | `complaint_subtypes.label`: 327 GCC labels + `Other / not listed above` |
| 7 | Department | string | `departments.name` (the 16 in `src/lib/constants.ts`). For `Other`, chosen by the classifier. |
| 8 | Routing | enum | `Auto-routed` (`needs_manual_review = 0`) / `Needs officer review` |
| 9 | Routing Basis | enum | `complaint_subtypes.mapping_status`: `mapped`, `assumed`, `unmapped`. `assumed`/`unmapped` ⇔ Needs officer review. |
| 10 | Zone No | int 1–15 | `zones.zone_number`, always taken from `zone_wards` for the ward |
| 11 | Zone | string | `zones.zone_name` |
| 12 | Ward | int 1–200 | `ward_number` |
| 13 | Ward Determined By | enum | `map_boundary` (resolved from the map pin) / `user_selected` |
| 14 | Area | string / blank | `gcc_areas.name`; blank for a typed street |
| 15 | Locality | string / blank | `gcc_localities.name`; blank for a typed street |
| 16 | Street | string | GCC street name (UPPERCASE, as published), or the typed name + street type |
| 17 | Street Source | enum | `From GCC list` / `Typed by citizen` |
| 18 | Landmark | string ≤ 500 / blank | `specific_location` |
| 19 | Location PIN | 6 digits / blank | `location_pincode`: PIN of the problem's location, not the complainant's |
| 20 | Latitude | decimal, 7 dp / blank | `latitude`; blank when there is no map pin |
| 21 | Longitude | decimal, 7 dp / blank | `longitude` |
| 22 | Title | string ≤ 200 | `title`, usually the sub-type label |
| 23 | Details | string ≤ 400 | `description`: free text in English, Tamil or Tanglish |
| 24 | Anonymous | `Yes` / `No` | `is_anonymous` |
| 25 | Complainant | string / blank | `initials first_name last_name`, trimmed. Blank if anonymous. |
| 26 | Gender | `Male` / `Female` / `Transgender` / blank | blank if anonymous |
| 27 | Mobile | 10 digits / blank | blank if anonymous |
| 28 | Email | string / blank | optional; blank if anonymous |
| 29 | Complainant Address | string / blank | `street_address`; blank if anonymous |
| 30 | Photo Attached | `Yes` / `No` | `media_path IS NOT NULL` |
| 31 | Last Updated | `YYYY-MM-DD HH:mm` | `updated_at` = time of the latest status change |
| 32 | Is Synthetic | `0` / `1` | `complaints.is_synthetic` (migration 010). Real complaints are always 0. |
| 33 | Language | `en` / `ta` / `tanglish` | `languageOf(Details)`: Tamil script ⇒ `ta`; romanised-Tamil markers ⇒ `tanglish` |
| 34 | Severity Score | int 0–100 | `severity()`; see below |
| 35 | Priority | `Critical` / `High` / `Medium` / `Low` | score ≥ 70 / ≥ 50 / ≥ 30 / below 30 |
| 36 | Priority Reasons | string | `; `-separated reasons with their points, written for the Collector |
| 37 | Duplicate Group | Complaint No | earliest complaint this one re-reports (see below); its own code if none |
| 38 | Ward Cluster 72h | int ≥ 1 | complaints with the same sub type in the same ward in the 72 h up to and including this one |

### Severity (columns 34–36)

Rule-based, in `scripts/lib/intel.js` (typed wrapper: `src/lib/dataset/intel.ts`).
Points add up, capped at 100:

| Factor | Points |
|---|---|
| Sub-type risk: high (electric shock, open manhole, sewage, fallen tree, dengue / mosquito, flood, EB wire, trapped / pregnant / elderly in floods) | 35 |
| Sub-type risk: public safety or sanitation (stagnation, potholes, street dogs, lights out, overflowing bins, dark spots, barricading…) | 25 |
| Sub-type risk: other civic issue | 15 |
| Sub-type risk: administrative (tax, voter ID, certificates, project information) | 5 |
| Details describe an immediate hazard (live wire, open manhole…) | +15 |
| Vulnerable place or people in Details / Landmark, in English, Tamil or Tanglish (school / பள்ளி, hospital / மருத்துவமனை, bus stop, temple / church / mosque, children / குழந்தைகள், elderly / முதியோர், pregnant) | +8 each, max 24 |
| Other same-sub-type complaints in the ward in the last 72 h | +5 each, max 20 |
| Rain-sensitive complaint filed on a rain-event day or within the 2 days after | +10 |
| Still open past the department's target (median work days + 2) / past twice the target | +10 / +20 |
| Citizen says they complained before ("already complained", பலமுறை, "evlo thadava") | +10 |

Work-day medians: Solid Waste Mgmt 1, Electrical 2, Health 3, Storm Water Drain 5,
Parks 5, Engineering / Revenue / General Admin 7, MEGA STREETS 14 (others 4–10).

**Severity is a snapshot.** "Days open" is measured when the row is written: at
filing for a live append, at export time for a rebuild.

### Duplicate Group (column 37)

Same Complaint Type, Sub Type and Department; filed ≤ 72 h after the other one;
and either ≤ 150 m apart (both have a map pin) or the same street in the same
ward (otherwise). The group is named after the earliest complaint in it. A live
append looks back 144 h so that earlier rows' own groups resolve too.

## `grievance_status_history.csv`

Columns 1–5 are exactly the Status History sheet of `npm run export:excel`.

| Column | Format | Source |
|---|---|---|
| Complaint No | string | `complaints.complaint_code` |
| Status | the 7 statuses | `complaint_status_history.status` |
| Changed By Role | `Citizen` / `System` / `Department Officer` / `Collector` | `stage` |
| Remarks | string ≤ 1000 | `remarks` |
| Changed On | `YYYY-MM-DD HH:mm` | `created_at` |
| Is Synthetic | `0` / `1` | copied from the parent complaint |

## `_truth/duplicate_truth.csv`

`Complaint No, Truth Group`. Ground truth for evaluating deduplication and
clustering, kept out of the main file so an algorithm can't see it.
`DUP-<code>` = a planted re-report group, named after the original complaint.
`HOT-nn` = a hotspot burst.

## Assumed lifecycle

> The Officer and Collector pages are still placeholders, and **no route changes
> status after filing yet**. The lifecycle below is the workflow the upcoming
> dashboards are **assumed** to implement. The synthetic history follows it, and
> the validator enforces it.

```
Complaint Filed ─(System, 1–30 min)─▶ Pending Approval ─(Dept Officer)─▶ Approved by Department Officer
      │                                   │                                   │
      │                                   └─▶ Rejected (Department Officer)    ├─▶ Rejected (Department Officer)
      │                                                                        ▼
      │                         In Progress ─(Dept Officer)─▶ Completed - Pending Collector Verification
      │                                                                        │
      │                                                  Verified by Collector ◀┤
      │                                                                        └─▶ Rejected (Collector)
```

| Step | Changed By Role | Delay (synthetic) | Example remark |
|---|---|---|---|
| Complaint Filed | Citizen | 0 | `Complaint registered by citizen.` / `Complaint registered. Department routing pending officer review.` (the app's own) |
| Pending Approval | System | 1–30 min | `Forwarded to <Department> for approval.` / `Routing to be confirmed by department officer.` |
| Approved by Department Officer | Department Officer | 2 h – 3 days, lognormal, median 30 h | `Approved. Assigned to AE, Zone 8.` |
| In Progress | Department Officer | 1 h – 3 days, median 8 h | `Work order issued; team deployed.` |
| Completed - Pending Collector Verification | Department Officer | lognormal around the department's work median | `Pothole patched with cold mix. Photo uploaded.` |
| Verified by Collector | Collector | 6 h – 4 days | `Verified with completion photo; closed.` |

Rejections put the reason in the Rejected history row **and** in
`complaints.remarks`. Department Officer reasons: duplicate (`Duplicate of
<code>`), not GCC jurisdiction (TNEB / Metrowater / Highways), insufficient
details, private property. Collector reasons: completion photo does not match,
work not satisfactory.

## Synthetic data rules

Synthetic rows have `is_synthetic = 1`, `user_id = NULL` and history
`changed_by = NULL`. They are built only from reference data in this repo. No
network calls are made, and OpenAI is never used.

- **Volume.** 90 days ending on the run date; Poisson daily counts averaging `--per-day`;
  Sunday ×0.75, Monday ×1.15; filings peak 08–11 and 18–22 IST, trough 01–05.
- **Rain events (this run): 2026-08-10, 2026-08-25, 2026-08-29, 2026-09-03, 2026-09-16.**
  On each day and the two after it: Water Stagnation, Storm Water Drains and
  Flood ×3–4; tree sub types and Street Light ×2. The extra complaints go to
  low-lying zones (Royapuram, Tondiarpet, Teynampet, Kodambakkam, Adyar,
  Perungudi, Sholinganallur, Valasaravakkam). These are **chosen, not observed**
  days. Align them with IMD rainfall using `--rain-days`.
- **Type mix.** Category shares as specified (Garbage 22%, Road 14%, Street Light
  12%, Public Health 12%, …, MEGA STREETS 2% combined, Other 4%). Within a
  category the 5 GCC "frequent" sub types get ×5 weight, and Public Health is
  weighted towards dogs and mosquitoes.
- **Zone weights** are a documented proxy (no population data in the repo): denser
  central and north zones are weighted up to 1.35, the southern and north-western
  fringe down to 0.7.
- **Location.** Ward first; then an area from `area_wards` (primary ×3), a locality
  and a GCC street (88%) or a typed street (12%, Area / Locality blank). 85% have a
  map pin sampled **inside the ward polygon** and confirmed by the same ward
  resolver the API uses. Streets have no coordinates of their own, so a pin is
  inside the right ward but not necessarily on the named street.
- **Location PIN (60%) is APPROXIMATE**: picked from a small hand-made list of
  plausible Chennai PINs per zone (600001–600119). PIN areas don't follow ward
  boundaries.
- **Text.** Titles are the sub-type label 80% of the time. Details come from
  per-category and per-sub-type templates in English (60%), Tamil (25%) and
  Tanglish (15%), with slots for place, duration, impact and "already complained".
  1.5% are junk text (mostly rejected). No two Details repeat in the same ward on
  the same day. `Other` complaints are written so `KEYWORD_MAP` in
  `src/lib/ai-classifier.ts` routes them to departments the categories never
  reach, so all 16 departments appear.
- **Duplicates and hotspots.** 6% re-report an earlier complaint (same sub type,
  5–140 m, within 72 h, different complainant, reworded or in another language);
  about 60% of those are rejected as duplicates. 10–15 hotspot bursts put 5–20
  same-sub-type complaints in one ward within 48 h.
- **Lifecycle.** Simulated forward and cut at run time: old complaints are mostly
  Verified or Rejected, and the last 3 days are mostly Pending. About 12% stall
  (10+ days with no event), concentrated in Engineering and Storm Water Drain.

### Fake personal data

Every synthetic complainant is invented:

- names are common Tamil given names from a fixed list, not real people;
- **every synthetic mobile number is `90000` followed by 5 digits.** This range
  was chosen for synthetic data and isn't checked against real subscribers, so
  never call or message these numbers;
- emails use only the reserved `example.com` / `example.in` domains;
- addresses are a random door number plus a GCC street name.
