/**
 * Rebuilds grievances.csv and grievance_status_history.csv completely from
 * MySQL, real and synthetic rows alike, using the shared SELECTs.
 *
 * Each file is written to a temp file and renamed into place. Because the live
 * appender only adds lines, a rebuild is also how status changes made after
 * filing reach the main file.
 */
const { complaintsSql, historySql } = require("./dataset-sql");
const { GRIEVANCE_COLUMNS, HISTORY_DATASET_COLUMNS, enrich, nowIst } = require("./intel");
const { datasetPaths, writeCsvAtomic, rainDays } = require("./dataset-files");

const IS_SYNTHETIC = "c.is_synthetic AS 'Is Synthetic'";

async function fetchDataset(conn) {
  const [complaints] = await conn.query(
    complaintsSql({ orderBy: "c.created_at, c.id", extra: [IS_SYNTHETIC] })
  );
  const [history] = await conn.query(
    historySql({ orderBy: "h.created_at, h.id", extra: [IS_SYNTHETIC] })
  );
  return { complaints, history };
}

/**
 * options: { root, now } - root is the project root; now (parseTs scale)
 * defaults to the current IST time and sets "days open" for severity.
 */
async function exportDataset(conn, options) {
  const paths = datasetPaths(options.root);
  const { complaints, history } = await fetchDataset(conn);
  enrich(complaints, { now: options.now || nowIst(), rainDays: rainDays(options.root) });
  writeCsvAtomic(paths.grievances, GRIEVANCE_COLUMNS, complaints);
  writeCsvAtomic(paths.history, HISTORY_DATASET_COLUMNS, history);
  return { paths, complaints, history };
}

module.exports = { exportDataset, fetchDataset };
