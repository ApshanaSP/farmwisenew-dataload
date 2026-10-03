# Police Incident Dataset Generator (Chennai District)

Generates a synthetic **Police Department** dataset for the FarmwiseAI Task 6 District Collector Intelligence Dashboard. The data covers 16 Chennai taluks, 60 police stations, and a rolling window of incident reports (90 days by default).

The dataset holds **raw, observable facts only**. It has no severity, priority, duplicate or cluster columns. The true duplicate grouping is written only to `output/ground_truth_events.csv`, for testing your own dedup algorithm. It is never loaded into MySQL.

## Requirements

- Node.js 18+
- MySQL 8 (only for `--mode=mysql|both`; CSV mode needs no database)

## Setup

Copy `.env.example` to `.env` and fill in your MySQL credentials:

```
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_mysql_password
DB_NAME=district_collector_dashboard
TZ=Asia/Kolkata
```

## Run commands

### Windows PowerShell

Windows PowerShell 5.1 strips the bare `--` before npm sees it, so options never reach the script. Use `npm.cmd` (or quote the separator as `'--'`). The script stops with an explanation if the options were lost.

```powershell
cd D:\farmwisenew\police_dataset_generator
Copy-Item .env.example .env        # then edit .env
npm install
npm.cmd run all                                          # setup + generate (90 days, seed 42) + validate
npm.cmd run generate -- --days 90 --seed 42              # full rebuild of the rolling window
npm.cmd run generate -- --days 180 --seed 42             # 180 days for quarter-over-quarter comparison
npm.cmd run generate -- --mode=csv                       # CSV only, no MySQL needed
npm.cmd run generate -- --refresh                        # roll the window forward (daily job)
npm.cmd run validate
npm.cmd run validate -- --days 180                       # validate a 180-day build
```

### Linux / macOS (bash, zsh)

```bash
cd police_dataset_generator
cp .env.example .env               # then edit .env
npm install
npm run all
npm run generate -- --days 90 --seed 42
npm run generate -- --days 180 --seed 42
npm run generate -- --mode=csv
npm run generate -- --refresh
npm run validate
npm run validate -- --days 180
```

(`cmd.exe` and Git Bash on Windows accept the Linux form too.)

### Scripts

| Script | What it does |
|---|---|
| `npm run setup` | Creates `DB_NAME` if missing and applies `sql/schema.sql` (idempotent) |
| `npm run generate` | Generates the dataset (options below) |
| `npm run validate` | Prints distributions and runs integrity/realism checks; exits 1 on failure |
| `npm run all` | `setup`, then `generate`, then `validate` |

### Generate options

| Option | Default | Meaning |
|---|---|---|
| `--days N` | 90 | Window length. The window is start = today − (N−1) at 00:00 IST, end = now |
| `--per-day N` | 50 | Mean real-world events per day; each day varies ±25% |
| `--seed N` | 42 | RNG seed. The same seed gives the same events for the same dates |
| `--mode=csv\|mysql\|both` | both | Where to write. `ground_truth_events.csv` is always written |
| `--refresh` | off | Deletes rows older than the window start, advances the status of open cases, and adds only reports that appeared since the last run |

Use the same `--days`, `--per-day` and `--seed` for `--refresh` as for the original build. That makes a refreshed dataset identical to a fresh full rebuild, apart from how report IDs are numbered.

## Outputs

- `output/police_stations.csv`: 60 stations (2–4 per taluk)
- `output/police_incident_reports.csv`: one row per report
- `output/ground_truth_events.csv`: `report_id,true_event_id` (file only)
- MySQL tables `police_stations` and `police_incident_reports`, loaded in batches of 500 rows
- The first 10 rows, printed to the console as a sample

## How the data is built

- **Configuration:** `config/taluks.json` holds taluks (TLK-XXX), centroids, localities with landmarks, stations and hotspot multipliers. Replace it with an export of your Location Master table later. `config/events.json` lists festival, protest and heavy-rain days with volume, category and response-time multipliers. Extend it each year.
- **Category mix and time patterns:** the mix follows the spec (accidents 18%, theft 15%, ...). Each category has an hour-of-day profile: accidents peak 8–10am, 6–9pm and late night; chain snatching in the early morning and evening; theft at night. Nuisance and accidents rise on weekends.
- **Hotspots:** Mambalam and Egmore get more theft and crowd incidents. Sholinganallur and Velachery get more accidents and cyber fraud. Tondiarpet and Thiruvottiyur get more heavy-vehicle accidents and traffic obstruction.
- **Duplicates:** about 20% of events produce 2–4 reports from different sources. The incident time shifts by up to ±90 minutes, GPS is jittered 50–400 m, the text is reworded, counts differ slightly, and the locality is sometimes named as a landmark. About 3% of events also get a near-miss twin: the same category, locality and day, but a separate event.
- **Status:** each report's status comes from fixed milestones derived from its report_id. Old rows are mostly CLOSED, and rows from the last 7 days are mostly REPORTED or UNDER_INVESTIGATION. A refresh moves cases forward and never backwards. `closed_datetime` is always after `reported_datetime` and never in the future.
- **Coverage:** every taluk is guaranteed at least one incident in any 7 consecutive days.

## Scheduling the daily refresh

### Windows Task Scheduler

From an elevated or normal Command Prompt:

```bat
schtasks /Create /TN "PoliceDatasetRefresh" /SC DAILY /ST 00:15 /TR "cmd /c cd /d D:\farmwisenew\police_dataset_generator && npm run generate -- --refresh >> output\refresh.log 2>&1"
```

To set it up in the GUI instead, open Task Scheduler → Create Basic Task → Daily at 00:15 → Start a program:
- Program: `cmd`
- Arguments: `/c cd /d D:\farmwisenew\police_dataset_generator && npm run generate -- --refresh >> output\refresh.log 2>&1`

The task runs through `cmd.exe`, so `--` is passed correctly. To test it now, run `schtasks /Run /TN "PoliceDatasetRefresh"`.

### cron (Linux / macOS)

Run `crontab -e` and add the line below. Cron has a minimal PATH, so give the full path to npm (find it with `which npm`):

```
15 0 * * * cd /path/to/police_dataset_generator && /usr/bin/npm run generate -- --refresh >> output/refresh.log 2>&1
```

The window is always computed in `TZ` from `.env` (Asia/Kolkata), whatever the server's time zone is.

## Project layout

```
config/taluks.json      taluk / locality / station master
config/events.json      festival, protest and rain calendar
sql/schema.sql          MySQL DDL
src/setup.ts            npm run setup
src/generate.ts         npm run generate
src/validate.ts         npm run validate
src/lib/                rng, time, config, categories (rules + text), generator, lifecycle, store (CSV/MySQL)
output/                 generated files
```
