# farmwisenew-dataload

The District IQ data job, moved off the PC. Every hour GitHub Actions collects seven of the eight Chennai sources, rebuilds
the `district_intel` store and uploads it to AWS. The website (running with `DATA_BACKEND=aws`) loads each new build
within 5 minutes, so the dashboard stays current with the PC switched off.

## Where the code comes from

This repository holds only the job: the workflow, `ci/`, `aws/` and the grievance generator's slim
`chennai-grievance-portal-main/package.json`. The pipeline, the collectors and the generators are taken from the main
repo ([ApshanaSP/farmwisenew](https://github.com/ApshanaSP/farmwisenew), branch `main`) at the start of every run (code
paths only, no data), so there is one copy of the code: a fix pushed to the main repo is live on the next hourly run.

## What one run does (`.github/workflows/dataload.yml`)

0. **Hour check**: if the build AWS serves was made in this clock hour (IST), the run stops here (a few seconds), so
   cron-job.org, GitHub's backup schedule and the console's Refresh never build twice in an hour. "Run workflow"
   with *force* ticked skips the check.
1. **Restore history**: downloads `state.tar.gz` from the `state` release (`ci/state.py`): the news archive, the
   IMD / CPCB / CFM records, hospital, PWD and police files, and the build's caches. On the very first run it uses the
   PC's copy on the `seed` branch instead. Then the CPCB files come from the `cpcb` branch, which the PC replaces
   every hour (`district_intel/scripts/push_cpcb.py` in the main repo).
2. **Temporary MySQL**: the grievance generator reads MySQL, so the job starts a throwaway MySQL and fills it from
   AWS (`ci/seed_mysql.py`): the reference tables (zones, wards, streets, complaint types) and the website's complaints,
   including ones citizens filed today. No users or passwords are loaded.
3. **Collect and build**: `district_intel/run_pipeline.py refresh --all`. The same collectors and generators the PC
   ran, with their own command lines, unchanged: news, IMD, CFM-DSS (headless Chromium), hospital, PWD, grievances,
   police; then the build (multilingual-e5-small on CPU). CPCB is skipped here: GitHub's machines cannot connect to
   airquality.cpcb.gov.in, so the PC collects it (Task Scheduler `DistrictIntel`, hourly) and pushes it to the
   `cpcb` branch. While the PC is off, CPCB shows as stale.
4. **Upload**: `aws/export_intel.py` (the store to S3, skipped when AWS already serves a newer build) and
   `aws/push.py police` (DynamoDB), through the team API with `REFRESH_API_KEY`. No AWS keys.
5. **Save history**: packs the updated state back into the `state` release for the next hour.

A source that fails (a site timing out) is logged as a warning and retried the next hour; the rest still upload.

## Setup

- Repository secret **`REFRESH_API_KEY`**: Settings > Secrets and variables > Actions.
- Optional secrets **`GROQ_API_KEY`** (AI news labelling) and **`GEMINI_API_KEY`** (AI page 2 briefing): same place.
  The job writes them into `district_intel/.env` for the run; without them the pipeline uses its rules.
- **Hourly start (cron-job.org)**: GitHub's own schedule skips many runs (3 of 12 hours ran on 3-4 Oct 2026), so a
  free cron-job.org job starts the workflow through the API every hour; runs started that way begin within seconds.
  1. GitHub > Settings > Developer settings > Fine-grained tokens > Generate: only this repository, permission
     **Actions: Read and write**, the longest expiry offered (renew it before it ends).
  2. cron-job.org > Create cronjob: URL
     `https://api.github.com/repos/ApshanaSP/farmwisenew-dataload/actions/workflows/dataload.yml/dispatches`,
     every hour at minute 5, Asia/Kolkata. Advanced: method **POST**; headers `Authorization: Bearer <token>`,
     `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`;
     body `{"ref":"main"}`. Test run: GitHub answers **204**.
  3. Optional: the same token as `GITHUB_DISPATCH_TOKEN` in the website's `.env` makes the Collector console's
     Refresh start a run now.
- The cron `17 * * * *` (UTC, so :47 IST) stays as a backup.
- The PC only collects CPCB (`run_intel.bat`); it no longer builds or uploads, so only GitHub publishes builds.

## Notes

- Public repository = unlimited free Actions minutes. One run takes about 25-35 minutes.
- GitHub pauses scheduled workflows in a repository with no commits for 60 days; it emails before that, and one
  click (or any commit) turns it back on. The cron-job.org start is not affected.
- The `state` release asset is public, so it never holds the grievance files (they include real citizens'
  complaints); every run rebuilds those in full from AWS.
- Until 4 Oct 2026 this repository kept its own copy of the code, which fell behind the main repo; since then the
  code is taken from the main repo every run (see "Where the code comes from").
