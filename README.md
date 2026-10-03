# farmwisenew-dataload

The District IQ data job, moved off the PC. Every hour GitHub Actions collects seven of the eight Chennai sources, rebuilds
the `district_intel` store and uploads it to AWS. The website (running with `DATA_BACKEND=aws`) loads each new build
within 5 minutes, so the dashboard stays current with the PC switched off.

## What one run does (`.github/workflows/dataload.yml`)

1. **Restore history**: downloads `state.tar.gz` from the `state` release (`ci/state.py`): the news archive, the
   IMD / CPCB / CFM records, hospital, PWD and police files, and the build's caches. On the very first run it uses the
   PC's copy on the `seed` branch instead.
2. **Temporary MySQL**: the grievance generator reads MySQL, so the job starts a throwaway MySQL and fills it from
   AWS (`ci/seed_mysql.py`): the reference tables (zones, wards, streets, complaint types) and the website's complaints,
   including ones citizens filed today. No users or passwords are loaded.
3. **Collect and build**: `district_intel/run_pipeline.py refresh --all`. The same collectors and generators the PC
   ran, with their own command lines, unchanged: news, IMD, CFM-DSS (headless Chromium), hospital, PWD, grievances,
   police; then the build (multilingual-e5-small on CPU). CPCB is skipped here: GitHub's machines cannot connect to
   airquality.cpcb.gov.in, so the PC collects it and sends it (`aws/push.py cpcb`).
4. **Upload**: `aws/export_intel.py` (the store to S3, skipped when AWS already serves a newer build) and
   `aws/push.py police` (DynamoDB), through the team API with `REFRESH_API_KEY`. No AWS keys.
5. **Save history**: packs the updated state back into the `state` release for the next hour.

A source that fails (a site timing out) is logged as a warning and retried the next hour; the rest still upload.

## Setup

- Repository secret **`REFRESH_API_KEY`**: Settings > Secrets and variables > Actions.
- Run it once by hand: Actions > Hourly data load > Run workflow. After that it runs every hour at :17.
- When it works, turn off the PC's upload (Task Scheduler job `DistrictIntel`), so only one place publishes builds.

## Notes

- Public repository = unlimited free Actions minutes. One run takes about 25-35 minutes.
- GitHub pauses scheduled workflows in a repository with no commits for 60 days; it emails before that, and one
  click (or any commit) turns it back on.
- The `state` release asset is public, so it never holds the grievance files (they include real citizens'
  complaints); every run rebuilds those in full from AWS.
- Code here is copied from the main project; the collectors' fetching methods are not changed. Only `ci/`, the
  workflow and `chennai-grievance-portal-main/package.json` are new.
