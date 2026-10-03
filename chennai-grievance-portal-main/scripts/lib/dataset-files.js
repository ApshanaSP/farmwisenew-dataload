/**
 * Where the grievance dataset lives and how it is written.
 *
 * DATASET_DIR (env) overrides the default data/datasets; a relative value is
 * resolved against the project root. The directory is git-ignored because it
 * holds personal data from real complaints.
 */
const fs = require("fs");
const path = require("path");
const csv = require("./csv");

const GRIEVANCES_FILE = "grievances.csv";
const HISTORY_FILE = "grievance_status_history.csv";
const TRUTH_FILE = path.join("_truth", "duplicate_truth.csv");
const META_FILE = "generation_meta.json";

function datasetDir(root) {
  const configured = process.env.DATASET_DIR;
  if (configured) return path.resolve(root, configured);
  return path.join(root, "data", "datasets");
}

function datasetPaths(root) {
  const dir = datasetDir(root);
  return {
    dir,
    grievances: path.join(dir, GRIEVANCES_FILE),
    history: path.join(dir, HISTORY_FILE),
    truth: path.join(dir, TRUTH_FILE),
    meta: path.join(dir, META_FILE)
  };
}

/**
 * Writes a complete CSV (BOM, header, rows) to a temp file beside the target,
 * then renames it over the target, so readers never see a half-written file.
 */
function writeCsvAtomic(file, columns, rows) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const parts = [csv.BOM, csv.line(columns)];
  for (const r of rows) parts.push(csv.lineFromRow(r, columns));
  const tmp = file + "." + process.pid + ".tmp";
  fs.writeFileSync(tmp, parts.join(""), "utf8");
  try {
    fs.renameSync(tmp, file);
  } catch (err) {
    fs.rmSync(tmp, { force: true });
    if (err.code === "EPERM" || err.code === "EBUSY") {
      throw new Error(file + " is locked (open in Excel?). Close it and re-run.");
    }
    throw err;
  }
}

/** The generator's record of seed, date range and rain days, or null. */
function readMeta(root) {
  try {
    return JSON.parse(fs.readFileSync(datasetPaths(root).meta, "utf8"));
  } catch {
    return null;
  }
}

/**
 * Rain-event days used for severity context: RAIN_DAYS (comma-separated
 * YYYY-MM-DD) when set, otherwise the days the generator recorded.
 */
function rainDays(root) {
  const env = process.env.RAIN_DAYS;
  if (env) return new Set(env.split(",").map((s) => s.trim()).filter(Boolean));
  const meta = readMeta(root);
  return new Set((meta && meta.rainDays) || []);
}

module.exports = {
  GRIEVANCES_FILE,
  HISTORY_FILE,
  TRUTH_FILE,
  META_FILE,
  datasetDir,
  datasetPaths,
  writeCsvAtomic,
  readMeta,
  rainDays
};
