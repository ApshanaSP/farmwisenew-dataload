# District Intelligence data layer

Turns the outputs of every collector and generator in this repository into one
curated, traceable, dashboard-ready store for the Chennai Collector's dashboard
(FarmwiseAI Task 6).

**It never edits a source.** The collectors keep their own fetching methods (RSS,
REST, Playwright, generators). This layer reads their output files, and
`run_pipeline.py refresh` calls their existing command lines on a schedule.

```bash
cd district_intel
pip install -r requirements.txt
python run_pipeline.py build              # about 3.5 minutes on a laptop CPU
python -m pytest -q tests                 # 24 fast tests
python run_pipeline.py mysql              # copy the last build into MySQL (about 1.5 minutes)
python run_pipeline.py refresh            # run the sources that are due; rebuild only if a file changed
python run_pipeline.py refresh --all --only news,imd --no-build   # force some sources, skip the build
python run_pipeline.py watch --every 15   # keep refreshing and rebuilding
```

`scripts/run_intel.bat` runs `refresh`. It is registered in Windows Task Scheduler
as **DistrictIntel**, every 30 minutes (`schtasks /Query /TN DistrictIntel`;
remove with `schtasks /Delete /TN DistrictIntel /F`). Each source runs on its own
interval from `config.yaml` (news, IMD, CPCB hourly; CFM every 3 hours; the synthetic
generators daily).

### One rain calendar for every generator

`dintel/sync.py` reads the police generator's heavy-rain days
(`police_dataset_generator/config/events.json`) plus IMD warning days and hands them
to the other generators through their **own options**: `--rain-days` for grievances,
`--rainfall-csv` for PWD. It runs before every refresh, so all synthetic sources keep
the same weather. All four generators produce 180 days (police and grievances via
`--days 180`; PWD and hospitals via their `WINDOW_DAYS` / `HISTORY_DAYS` constants,
changed from 90 to 180 on 27 Sep 2026).

## What it produces (`output/`, git-ignored)

| Path | What |
|---|---|
| `district_intel.db` | SQLite store: 32 tables and 6 views (below). The dashboard can read it directly, or read the MySQL copy (below). |
| `curated/*.csv` | The same tables as CSV (UTF-8 with BOM so Excel shows Tamil). |
| `dashboard/*.json` | Feeds shaped for the dashboard tiles: `incidents.json`, `incident_details.json`, `kpis.json`, `alerts.json`, `briefings.json`, `environment.json`, `hotspots.json`, `gaps.json`, `trends.json`, `source_health.json`, `wards.geojson`, `zone_outlines.geojson`, `meta.json`. |
| `reports/evaluation.md` | Measured accuracy of every AI step. |
| `reports/data_quality.md` | Source health, the data contract, drift. |
| `reports/briefing_{daily,weekly,monthly,quarterly}.md` | Collector briefings with verified numbers. |
| `truth/cross_source_truth.csv` | Ground truth written by the scenario overlay (never read by the linker). |

### Main tables

| Table | One row per | Notes |
|---|---|---|
| `events` | source record (complaint, police report, PWD incident, hospital alert episode, IMD warning, news incident) | 70 canonical columns: time, text, category, location keys, impact, severity, status, provenance |
| `incidents` | real-world incident | cross-source roll-up with priority (computed at `as_of`, with reasons), deadline, attention flag |
| `incident_members`, `incident_timeline` | member record / timeline step | the evidence panel and merged timeline |
| `actions` | task | PWD tasks (real) plus playbook drafts from the Action Planner agent |
| `documents` | news article, PWD announcement, CFM bulletin | story clusters, report type, incident gate |
| `observations`, `observation_signals` | metric reading / latest value per series | hospitals, lakes, reservoirs, IMD, CPCB, gauges (with quality flags) |
| `kpis`, `daily_counts`, `anomalies`, `hotspots` | KPI tile / count / spike / cluster | all numbers come from here, never from a model |
| `alerts`, `briefings`, `gaps`, `review_queue` | agent outputs | drafts and suggestions; people decide |
| `source_health`, `data_quality`, `quarantine`, `category_drift` | Data Steward outputs | |
| `ref_*` | reference masters | wards (with taluk, low-lying index, Gi*), taluks, departments, categories, facilities, offices |
| `world_calendar` | day | rain days from every source plus IMD, festivals, protests |

Views: `v_open_incidents_live` (deadline re-checked against the wall clock),
`v_collector_queue`, `v_zone_summary`, `v_taluk_unresolved`, `v_media_gaps`,
`v_department_performance`.

## MySQL copy for the dashboard

`python run_pipeline.py mysql` (or `build --mysql`, or `mysql.export_after_build: true`
in `config.yaml`) copies the SQLite store into two databases on the local MySQL server:

| Database | Owner | On each export |
|---|---|---|
| `district_intel` | pipeline | All 31 tables replaced (about 370,000 rows). They load into `*__new` tables and swap in with one atomic `RENAME TABLE`, so readers never see a half-loaded store. |
| `district_intel_ops` | dashboard | Only missing tables are created. Nothing is dropped or truncated. Every pipeline briefing is added to `briefing_archive`. |

- **Times** become `DATETIME` in IST wall-clock, like the grievance portal.
- **Map shapes:** ward polygons are in `ref_wards.geometry` and zone outlines in `ref_zones.outline`, both JSON.
- **`documents`** keeps 32 of its 78 columns.
- **Left out:** `quarantine` and `category_drift`.
- **Credentials** come from `DI_MYSQL_*` environment variables, otherwise from the portal's `.env`.
- **Checks:** each export compares row counts and per-column non-null counts with SQLite and writes `reports/mysql_export.json`. The command exits with code 1 on any mismatch.

The ops tables (`collector_decisions`, `action_updates`, `review_decisions`,
`workspaces`, `briefing_archive`, `audit_log`) are ready for the dashboard to write to.
The pipeline does not apply them to builds yet.

## Pipeline

```
load (8 sources) -> classify -> locate -> overlay -> dedup -> link -> incidents
      -> analytics -> agents (planner, gap finder, linker, watchdog, steward, briefing) -> store
```

| Stage | Method | Where |
|---|---|---|
| Load | One loader per source; personal data is not copied (mobile -> keyed hash) | `dintel/loaders/` |
| Categories | Crosswalk tables for structured sources; a char-n-gram classifier trained on labelled grievance, police and PWD text for "Other" complaints and news | `reference/category_map.yaml`, `dintel/classify.py` |
| News | One document per URL, Google copies merged into publisher copies, story clusters (character n-grams, then multilingual-e5 embeddings at cosine 0.95 within 24 h), report type (scheduled power-cut notices excluded), incident filter trained on weak labels plus 300 hand labels, the news pipeline's own Tamil-aware gazetteer | `dintel/loaders/news.py`, `dintel/embed.py` |
| Location | Point-in-polygon on the 200 GCC wards, snapping within 500 m (flagged), ward -> taluk by majority vote of coded points | `dintel/geo.py`, `dintel/geolocate.py` |
| Scenario overlay | One shared world for the separately generated sources (see below) | `dintel/world.py` |
| Duplicates | Union-find over blocked pairs; the portal's rule for grievances plus officers' own "Duplicate of" decisions; police and PWD rules | `dintel/dedup.py` |
| Cross-source linking | Blocking (category group, KD-tree, category time window) -> logistic scorer -> threshold learned on half the world events -> review band -> union-find | `dintel/linking.py` |
| Incidents | Lead record, status, deadline (response-based for police cases), priority with reasons, attention flags | `dintel/incidents.py` |
| Analytics | Poisson baselines with weekday factor, DBSCAN hotspots, Getis-Ord Gi*, EWMA and z-scores, KPIs by period and zone | `dintel/analytics.py` |
| Agents | Data Steward, Action Planner, Gap Finder, Linker, Watchdog, Briefing (with a numeric verifier) | `dintel/agents/` |

Tuning lives in `config.yaml` and `reference/*.yaml` (categories carry their
deadlines, link windows and radii, playbooks and news keywords).

## The scenario overlay

The generators were written separately, so they disagree about the world: each
invents its own rain days, and their cross-reference IDs point to systems that do
not exist. The overlay fixes this without touching the generators:

1. Rain days from every source (grievance generator, police events calendar, PWD
   flood spikes, IMD warnings) form one world calendar.
2. On each world rain day, a source that shows no flood response gets the reports
   it would have produced, sized from its own real rain days. Records a source
   already has are adopted into the shared incident instead of duplicated.
3. Multi-source incidents (drain overflow, wall collapse, dengue cluster,
   electrical hazard, fallen tree) and news-anchored incidents (real news items
   that get departmental records a few hours earlier).

Every planted row has `is_synthetic = 1` and `is_overlay = 1`; the truth file says
which world event each belongs to. Real sources (news, IMD, CPCB, CFM) are never
changed. Turn it off with `python run_pipeline.py build --no-overlay` or
`overlay.enabled: false`.

## Optional LLM steps

Off by default. Set `llm.enabled: true` in `config.yaml` and `OPENAI_API_KEY` in
the environment to let the Briefing agent rewrite its text (accepted only if the
numeric verifier passes) and the Linker agent suggest decisions on gray-band pairs.
Calls are capped by `llm.max_calls_per_run`. Everything works without it.

## Hand labels for the news filter

`reference/labels/news_incident_labels.csv` holds 300 news articles drawn by stratified
sampling (150 the old filter accepted, 75 just below its threshold, 75 far below),
labelled by Claude as a draft (`labeled_by = claude-draft`, 42 marked `low` confidence).
**Please spot-check them**: change `label_incident` where you disagree and put your
name in `reviewed_by`. The next build retrains on them. Labelling rules:

* 1 = a specific event or condition in Chennai that a department must act on or
  monitor: civic failures, accidents, crimes that happened here, collapses, fires,
  disease spread, protests and road blockades.
* 0 = scheduled notices (power-cut lists), announcements and inspections, politics,
  court proceedings, statistics and reports, arrests or seizures for crimes elsewhere
  and follow-ups on older cases, business, entertainment, forecasts.
* `label_in_district` records location separately (1, 0 or ?), so place errors and
  topic errors can be measured apart.

Scores are population-weighted (each label carries its stratum weight) and come from
5-fold cross-validation, so the model is never scored on labels it trained on.

## Known limits

* **News filter:** F1 0.63 (precision 0.69, recall 0.57) with the hand labels, up from
  about 0.41 for the first rule-trained version. About 4 in 10 incident articles are
  still missed; more labels are the fastest way to improve it.
* **English-Tamil story merging:** multilingual-e5-small cannot tell a same-event pair
  from two different accidents on the same day using headlines (same-event pairs score
  0.84-0.98; unrelated English-Tamil pairs reach 0.86). The strict rule therefore merges
  none; same-language merges (129) were checked by hand and are correct.
* Synthetic grievance street names are not tied to their map pins (median 2.3 km
  apart), so text-only geo-resolution cannot be validated on them.
* 98% of news rows are Google News headline snippets; only a few hundred articles
  have full text.
* The category classifier scores near 0.99 because complaint texts come from
  templates; expect lower on real text.
* Station coordinates for CPCB and CFM are approximate (`reference/facilities.yaml`).
* Rainfall and air-quality history only starts accumulating now that the collectors
  run on a schedule; `docs/data_request_farmwiseai.md` asks FarmwiseAI for history.
