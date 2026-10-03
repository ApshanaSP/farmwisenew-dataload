/**
 * Exports the collected complaint dataset to JSON, ready for the spreadsheet
 * writer (scripts/export-to-xlsx.py).
 *
 *   node scripts/export-complaints.js                 # everything
 *   node scripts/export-complaints.js --from 2026-01-01 --to 2026-12-31
 *   node scripts/export-complaints.js --out other.json
 *
 * Usually run via `npm run export:excel`, which chains this with the writer.
 *
 * IDs are resolved to names here rather than in the spreadsheet, so the output
 * is readable on its own: department, zone, complaint category and sub type,
 * and the street — whether it was picked from the GCC list or typed in.
 *
 * CONTAINS PERSONAL DATA: complainant names, mobile numbers, email addresses
 * and map coordinates. Treat the file accordingly.
 */
const fs = require("fs");
const path = require("path");
const mysql = require("mysql2/promise");
const { getDbConfig, describeDb } = require("./db-config");
const { complaintsSql, historySql } = require("./lib/dataset-sql");

function arg(flag, fallback) {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : fallback;
}

const FROM = arg("--from", null);
const TO = arg("--to", null);
const OUT = arg("--out", path.join(__dirname, "..", "exports", "complaints.json"));

(async () => {
  console.log("Database: " + describeDb());
  const conn = await mysql.createConnection(getDbConfig());

  const where = [];
  const params = [];
  if (FROM) {
    where.push("c.created_at >= ?");
    params.push(FROM + " 00:00:00");
  }
  if (TO) {
    where.push("c.created_at <= ?");
    params.push(TO + " 23:59:59");
  }
  const filter = where.join(" AND ");

  // The column list lives in scripts/lib/dataset-sql.js, shared with the CSV
  // dataset (npm run dataset:export) and the live appender, so the workbook
  // and the dataset can never disagree on columns.
  const [complaints] = await conn.query(
    complaintsSql({ where: filter, orderBy: "c.created_at DESC" }),
    params
  );

  const [history] = await conn.query(
    historySql({ where: filter, orderBy: "c.complaint_code, h.created_at" }),
    params
  );

  // Aggregates the spreadsheet turns into its summary sheet.
  const [byDept] = await conn.query(
    `SELECT COALESCE(d.name,'(unassigned)') AS name, COUNT(*) AS n
       FROM complaints c LEFT JOIN departments d ON d.id = c.department_id
      GROUP BY d.name ORDER BY n DESC`
  );
  const [byStatus] = await conn.query(
    "SELECT status AS name, COUNT(*) AS n FROM complaints GROUP BY status ORDER BY n DESC"
  );
  const [byZone] = await conn.query(
    `SELECT COALESCE(z.zone_name,'(unknown)') AS name, COUNT(*) AS n
       FROM complaints c LEFT JOIN zones z ON z.id = c.zone_id
      GROUP BY z.zone_name ORDER BY n DESC`
  );
  const [byType] = await conn.query(
    `SELECT COALESCE(cc.name,'(legacy type)') AS name, COUNT(*) AS n
       FROM complaints c
       LEFT JOIN complaint_subtypes cs ON cs.id = c.complaint_subtype_id
       LEFT JOIN complaint_categories cc ON cc.id = cs.category_id
      GROUP BY cc.name ORDER BY n DESC`
  );

  const [[users]] = await conn.query("SELECT COUNT(*) n FROM users");
  const [sources] = await conn.query(
    `SELECT dataset, source_label, is_official, fetched_at, row_count
       FROM reference_data_sources ORDER BY dataset`
  );

  const bundle = {
    generatedAt: new Date().toISOString(),
    database: describeDb(),
    filter: { from: FROM, to: TO },
    counts: {
      complaints: complaints.length,
      statusChanges: history.length,
      registeredAccounts: users.n
    },
    complaints,
    history,
    summary: {
      byDepartment: byDept,
      byStatus: byStatus,
      byZone: byZone,
      byType: byType
    },
    referenceSources: sources.map((r) => ({
      dataset: r.dataset,
      source: r.source_label,
      official: r.is_official === 1 ? "Yes" : "No",
      fetchedAt: r.fetched_at ? new Date(r.fetched_at).toISOString().slice(0, 10) : null,
      rows: r.row_count
    }))
  };

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(bundle, null, 2), "utf8");

  console.log(
    "Exported " + complaints.length + " complaint(s) and " +
    history.length + " status change(s)"
  );
  console.log("  -> " + OUT);
  if (complaints.length === 0) {
    console.log("  (no complaints yet — the workbook will have headers only)");
  }

  await conn.end();
})().catch((e) => {
  console.error("Export failed:", e.message);
  process.exit(1);
});
