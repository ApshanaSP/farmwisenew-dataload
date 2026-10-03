/**
 * Validates the grievance dataset CSVs against the database, the ward
 * polygons and the routing rules. Fails loudly: exit code 1 on any failure.
 *
 *   node scripts/validate-grievance-dataset.js      # npm run dataset:validate
 *
 * Also used in-process by generate-synthetic-grievances.js (runValidation).
 *
 * Checks 5 (street -> area -> ward) and the "Other" routing half of check 2
 * are enforced for synthetic rows only: a real complaint's ward comes from a
 * map pin, which legitimately can fall outside the approximate area_wards
 * list, and the text a real citizen typed into the "Other" box
 * (otherDescription) is used for routing but not stored.
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const mysql = require("mysql2/promise");
const { getDbConfig, describeDb } = require("./db-config");
const csv = require("./lib/csv");
const geo = require("./lib/ward-geo");
const intel = require("./lib/intel");
const files = require("./lib/dataset-files");
const { COMPLAINT_COLUMNS } = require("./lib/dataset-sql");
const { classifyOther } = require("./lib/keyword-classifier");

const ROOT = path.join(__dirname, "..");

const STATUSES = [
  "Complaint Filed", "Pending Approval", "Approved by Department Officer", "In Progress",
  "Completed - Pending Collector Verification", "Verified by Collector", "Rejected"
];
/** Allowed next statuses, and who may make each rejection. */
const NEXT = {
  "Complaint Filed": ["Pending Approval"],
  "Pending Approval": ["Approved by Department Officer", "Rejected"],
  "Approved by Department Officer": ["In Progress", "Rejected"],
  "In Progress": ["Completed - Pending Collector Verification"],
  "Completed - Pending Collector Verification": ["Verified by Collector", "Rejected"],
  "Verified by Collector": [],
  Rejected: []
};
const REJECTER = {
  "Pending Approval": "Department Officer",
  "Approved by Department Officer": "Department Officer",
  "Completed - Pending Collector Verification": "Collector"
};
const ROLES = new Set(["Citizen", "System", "Department Officer", "Collector"]);
const CODE_RE = /^(\d{4})-\d{3}[A-Z]{3}$/;
const TS_RE = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/;
const TAMIL = /[஀-௿]/;

async function runValidation({ root = ROOT, conn = null, quiet = false } = {}) {
  const own = !conn;
  if (own) conn = await mysql.createConnection(getDbConfig());
  const paths = files.datasetPaths(root);
  const results = [];
  const check = (id, name, failures, okDetail) => {
    const pass = failures.length === 0;
    const detail = pass
      ? okDetail
      : failures.length + " problem(s); first: " + failures.slice(0, 3).join(" || ");
    results.push({ id, name, pass, detail });
    if (!quiet) console.log(`  [${pass ? "PASS" : "FAIL"}] ${id}. ${name}: ${detail}`);
  };

  try {
    for (const f of [paths.grievances, paths.history]) {
      if (!fs.existsSync(f)) throw new Error("Missing " + f + ". Run npm run dataset:export first.");
    }
    const gBuf = fs.readFileSync(paths.grievances);
    const hBuf = fs.readFileSync(paths.history);
    const gText = gBuf.toString("utf8");
    const hText = hBuf.toString("utf8");
    const G = csv.parseObjects(gText);
    const H = csv.parseObjects(hText);
    const rows = G.rows;
    const synthetic = rows.filter((r) => r["Is Synthetic"] === "1");
    const nowMin = new Date(intel.nowIst()).toISOString().slice(0, 16).replace("T", " ");

    // ---- reference data ---------------------------------------------------
    const q = async (sql) => (await conn.query(sql))[0];
    const taxonomy = await q(
      `SELECT cc.name AS cat, s.label, s.mapping_status, d.name AS dept
         FROM complaint_subtypes s JOIN complaint_categories cc ON cc.id = s.category_id
         LEFT JOIN departments d ON d.id = s.department_id`
    );
    const taxo = new Map();
    for (const t of taxonomy) {
      const k = t.cat + "|" + t.label;
      if (!taxo.has(k)) taxo.set(k, []);
      taxo.get(k).push(t);
    }
    const deptNames = (await q("SELECT name FROM departments")).map((d) => d.name);
    const zoneOfWard = new Map(
      (await q(`SELECT zw.ward_number, z.zone_number, z.zone_name FROM zone_wards zw JOIN zones z ON z.id = zw.zone_id`))
        .map((r) => [String(r.ward_number), r])
    );
    const areaWard = new Set(
      (await q(`SELECT a.name, aw.ward_number FROM area_wards aw JOIN gcc_areas a ON a.id = aw.area_id`))
        .map((r) => r.name + "|" + r.ward_number)
    );
    const streetChain = new Set(
      (await q(
        `SELECT a.name AS area, l.name AS loc, s.name AS street
           FROM gcc_streets s JOIN gcc_localities l ON l.id = s.locality_id JOIN gcc_areas a ON a.id = l.area_id`
      )).map((r) => r.area + "|" + r.loc + "|" + r.street)
    );
    const dbText = new Map(
      (await q("SELECT complaint_code, title, description, specific_location FROM complaints"))
        .map((r) => [r.complaint_code, r])
    );

    // ---- 1. headers -------------------------------------------------------
    {
      const f = [];
      const eq = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
      if (!eq(G.header, intel.GRIEVANCE_COLUMNS)) f.push("grievances.csv header: " + G.header.join(","));
      if (!eq(H.header, intel.HISTORY_DATASET_COLUMNS)) f.push("history header: " + H.header.join(","));
      for (const [name, buf] of [["grievances.csv", gBuf], ["history", hBuf]]) {
        if (!(buf[0] === 0xef && buf[1] === 0xbb && buf[2] === 0xbf)) f.push(name + " has no UTF-8 BOM");
      }
      if (fs.existsSync(paths.truth)) {
        const th = csv.parse(fs.readFileSync(paths.truth, "utf8"))[0] || [];
        if (th.join(",") !== "Complaint No,Truth Group") f.push("duplicate_truth.csv header: " + th.join(","));
      }
      check(1, "Headers exact, in order, with BOM", f, `${G.header.length} + ${H.header.length} columns`);
    }

    // ---- 2. taxonomy + department -----------------------------------------
    {
      const f = [];
      for (const r of rows) {
        const cands = taxo.get(r["Complaint Type"] + "|" + r["Complaint Sub Type"]);
        const tag = r["Complaint No"] + ": ";
        if (!cands) {
          f.push(tag + "unknown type/sub type " + r["Complaint Type"] + " / " + r["Complaint Sub Type"]);
          continue;
        }
        const t = cands.find((c) => c.mapping_status === r["Routing Basis"]);
        if (!t) {
          f.push(tag + "routing basis " + r["Routing Basis"] + " not in DB");
          continue;
        }
        if (t.dept) {
          if (t.dept !== r.Department) f.push(tag + "department " + r.Department + " != " + t.dept);
        } else if (!deptNames.includes(r.Department)) {
          f.push(tag + "unknown department " + r.Department);
        } else if (r["Is Synthetic"] === "1") {
          const expected = classifyOther([r.Title, r.Details].filter(Boolean).join(". "));
          if (expected !== r.Department) f.push(tag + "Other routed to " + r.Department + ", classifier says " + expected);
        }
      }
      check(2, "Type / sub type / department / routing basis match the DB", f, `${rows.length} rows`);
    }

    // ---- 3. routing flag ---------------------------------------------------
    {
      const f = rows
        .filter((r) => (r.Routing === "Needs officer review") !== ["assumed", "unmapped"].includes(r["Routing Basis"]))
        .map((r) => r["Complaint No"] + ": " + r.Routing + " / " + r["Routing Basis"]);
      check(3, "Needs officer review <=> assumed/unmapped", f, "consistent");
    }

    // ---- 4. geography -------------------------------------------------------
    {
      const f = [];
      let pinned = 0;
      for (const r of rows) {
        const z = zoneOfWard.get(r.Ward);
        if (!z) f.push(r["Complaint No"] + ": ward " + r.Ward + " not in zone_wards");
        else if (String(z.zone_number) !== r["Zone No"] || z.zone_name !== r.Zone) {
          f.push(r["Complaint No"] + ": ward " + r.Ward + " is in zone " + z.zone_number + ", row says " + r["Zone No"]);
        }
        if (r.Latitude !== "" || r.Longitude !== "") {
          pinned++;
          if (!geo.pointInWard(parseFloat(r.Latitude), parseFloat(r.Longitude), Number(r.Ward))) {
            f.push(r["Complaint No"] + ": " + r.Latitude + "," + r.Longitude + " outside ward " + r.Ward);
          }
        }
      }
      check(4, "Lat/lng inside the Ward polygon; Ward in its Zone", f, `${pinned} pinned rows inside their ward`);
    }

    // ---- 5. street -> area -> ward ----------------------------------------
    {
      const f = [];
      let checked = 0;
      let realOutside = 0;
      for (const r of rows) {
        if (r["Street Source"] !== "From GCC list" || r.Area === "") continue;
        const chainOk = streetChain.has(r.Area + "|" + r.Locality + "|" + r.Street);
        const wardOk = areaWard.has(r.Area + "|" + r.Ward);
        if (r["Is Synthetic"] !== "1") {
          if (!wardOk) realOutside++;
          if (!chainOk) f.push(r["Complaint No"] + ": street not under locality/area");
          continue;
        }
        checked++;
        if (!chainOk) f.push(r["Complaint No"] + ": " + r.Street + " not under " + r.Locality + " / " + r.Area);
        if (!wardOk) f.push(r["Complaint No"] + ": area " + r.Area + " not mapped to ward " + r.Ward);
      }
      check(5, "GCC-list street belongs to an area mapped to the ward", f,
        `${checked} synthetic rows` + (realOutside ? `; ${realOutside} real row(s) outside area_wards (informational)` : ""));
    }

    // ---- 6. typed street <=> blank area/locality ---------------------------
    {
      const f = rows
        .filter((r) => (r["Street Source"] === "Typed by citizen") !== (r.Area === "" && r.Locality === ""))
        .map((r) => r["Complaint No"] + ": " + r["Street Source"] + " area='" + r.Area + "' locality='" + r.Locality + "'");
      check(6, "Typed by citizen <=> Area and Locality blank", f, "consistent");
    }

    // ---- 7. anonymity -------------------------------------------------------
    {
      const personal = ["Complainant", "Gender", "Mobile", "Email", "Complainant Address"];
      const f = [];
      for (const r of rows) {
        const blank = personal.every((c) => r[c] === "");
        if (r.Anonymous === "Yes" && !blank) f.push(r["Complaint No"] + ": anonymous but personal data present");
        if (r.Anonymous === "No" && r.Complainant === "") f.push(r["Complaint No"] + ": not anonymous but no complainant");
        if (r.Anonymous !== "Yes" && r.Anonymous !== "No") f.push(r["Complaint No"] + ": Anonymous=" + r.Anonymous);
      }
      check(7, "Anonymous = Yes <=> complainant columns blank", f, `${rows.filter((r) => r.Anonymous === "Yes").length} anonymous rows`);
    }

    // ---- 8. complaint codes -------------------------------------------------
    {
      const f = [];
      const seen = new Set();
      for (const r of rows) {
        const code = r["Complaint No"];
        const m = CODE_RE.exec(code);
        if (!m) f.push(code + ": bad format");
        else if (m[1] !== r["Filed On"].slice(0, 4)) f.push(code + ": year differs from Filed On " + r["Filed On"]);
        if (seen.has(code)) f.push(code + ": duplicate");
        seen.add(code);
        if (!TS_RE.test(r["Filed On"]) || !TS_RE.test(r["Last Updated"])) f.push(code + ": bad timestamp format");
      }
      check(8, "Complaint No format, uniqueness, year = Filed On year", f, `${seen.size} unique codes`);
    }

    // ---- 9. history replay --------------------------------------------------
    {
      const f = [];
      const byCode = new Map();
      for (const h of H.rows) {
        if (!byCode.has(h["Complaint No"])) byCode.set(h["Complaint No"], []);
        byCode.get(h["Complaint No"]).push(h);
      }
      const main = new Map(rows.map((r) => [r["Complaint No"], r]));
      for (const code of byCode.keys()) if (!main.has(code)) f.push(code + ": history for unknown complaint");
      for (const r of rows) {
        const code = r["Complaint No"];
        const hs = byCode.get(code) || [];
        if (!hs.length) {
          f.push(code + ": no history");
          continue;
        }
        if (hs[0].Status !== "Complaint Filed" || hs[0]["Changed By Role"] !== "Citizen") f.push(code + ": first event is " + hs[0].Status);
        if (hs[0]["Changed On"] !== r["Filed On"]) f.push(code + ": first event " + hs[0]["Changed On"] + " != Filed On " + r["Filed On"]);
        for (let i = 0; i < hs.length; i++) {
          const h = hs[i];
          if (h["Is Synthetic"] !== r["Is Synthetic"]) f.push(code + ": history Is Synthetic differs");
          if (!ROLES.has(h["Changed By Role"])) f.push(code + ": role " + h["Changed By Role"]);
          if (!STATUSES.includes(h.Status)) f.push(code + ": status " + h.Status);
          if (h["Changed On"] > nowMin) f.push(code + ": event in the future " + h["Changed On"]);
          if (i === 0) continue;
          const prev = hs[i - 1];
          if (!(h["Changed On"] > prev["Changed On"])) f.push(code + ": Changed On not increasing at " + h.Status);
          if (!(NEXT[prev.Status] || []).includes(h.Status)) f.push(code + ": " + prev.Status + " -> " + h.Status + " not allowed");
          if (h.Status === "Rejected" && h["Changed By Role"] !== REJECTER[prev.Status]) {
            f.push(code + ": rejected by " + h["Changed By Role"] + " after " + prev.Status);
          }
        }
        const last = hs[hs.length - 1];
        if (last.Status !== r.Status) f.push(code + ": history ends at " + last.Status + ", Status is " + r.Status);
        if (last["Changed On"] !== r["Last Updated"]) f.push(code + ": Last Updated " + r["Last Updated"] + " != " + last["Changed On"]);
        const rejected = r.Status === "Rejected";
        if (rejected !== (r["Rejected At"] !== "")) f.push(code + ": Rejected At '" + r["Rejected At"] + "' with status " + r.Status);
        if (rejected && r["Rejected At"] !== last["Changed By Role"]) f.push(code + ": Rejected At differs from rejecting role");
      }
      check(9, "History replays to Status; timestamps increase; Last Updated; Rejected At", f,
        `${H.rows.length} history rows for ${byCode.size} complaints`);
    }

    // ---- 10. lengths + Tamil round-trip -----------------------------------
    {
      const f = [];
      for (const r of rows) {
        if (r.Title.length > 200) f.push(r["Complaint No"] + ": Title " + r.Title.length);
        if (r.Details.length > 400) f.push(r["Complaint No"] + ": Details " + r.Details.length);
        if (r.Landmark.length > 500) f.push(r["Complaint No"] + ": Landmark " + r.Landmark.length);
      }
      if (!Buffer.from(gText, "utf8").equals(gBuf)) f.push("grievances.csv is not valid UTF-8");
      if (!Buffer.from(hText, "utf8").equals(hBuf)) f.push("history file is not valid UTF-8");
      let tamil = 0;
      for (const r of rows) {
        const db = dbText.get(r["Complaint No"]);
        if (!db) continue;
        if (TAMIL.test(r.Details) || TAMIL.test(r.Landmark) || TAMIL.test(r.Title)) tamil++;
        if (db.description !== r.Details || db.title !== r.Title || (db.specific_location || "") !== r.Landmark) {
          f.push(r["Complaint No"] + ": text differs from the database");
        }
      }
      check(10, "Title/Details/Landmark lengths; Tamil round-trips", f,
        `${tamil} rows with Tamil text identical to MySQL after re-reading the file`);
    }

    // ---- 11. coverage -------------------------------------------------------
    {
      const f = [];
      const meta = files.readMeta(root);
      let detail = "";
      if (synthetic.length && meta) {
        const perDay = new Set(synthetic.map((r) => r["Filed On"].slice(0, 10)));
        let d = Date.UTC(+meta.startDate.slice(0, 4), +meta.startDate.slice(5, 7) - 1, +meta.startDate.slice(8, 10));
        let days = 0;
        for (;;) {
          const s = new Date(d).toISOString().slice(0, 10);
          if (s > meta.endDate) break;
          days++;
          if (!perDay.has(s)) f.push("no complaint on " + s);
          d += 86400000;
        }
        detail = `${days} days with complaints, `;
      } else if (!synthetic.length) {
        detail = "no synthetic rows (day coverage not applicable), ";
      }
      const statuses = new Set(rows.map((r) => r.Status));
      for (const s of STATUSES) if (!statuses.has(s)) f.push("status never appears: " + s);
      const depts = new Set(rows.map((r) => r.Department));
      for (const d of deptNames) if (!depts.has(d)) f.push("department never appears: " + d);
      check(11, "Every day has complaints; all 7 statuses and 16 departments appear", f,
        detail + `${statuses.size} statuses, ${depts.size} departments`);
    }

    // ---- 12. real rows identical to export:data ---------------------------
    {
      const f = [];
      const tmp = path.join(os.tmpdir(), "grievance-validate-" + process.pid + ".json");
      execFileSync(process.execPath, [path.join(__dirname, "export-complaints.js"), "--out", tmp], { stdio: "ignore" });
      const bundle = JSON.parse(fs.readFileSync(tmp, "utf8"));
      fs.rmSync(tmp, { force: true });
      const csvByCode = new Map(rows.map((r) => [r["Complaint No"], r]));
      let real = 0;
      let syn = 0;
      for (const j of bundle.complaints) {
        const r = csvByCode.get(j["Complaint No"]);
        // A CSV-only run replaces synthetic rows stored in MySQL by an earlier database run,
        // so only real rows must be present there.
        if (!r && String(j["Is Synthetic"]) !== "0") continue;
        if (!r) {
          f.push(j["Complaint No"] + ": in export:data but missing from grievances.csv");
          continue;
        }
        const a = csv.line(COMPLAINT_COLUMNS.map((c) => j[c]));
        const b = csv.line(COMPLAINT_COLUMNS.map((c) => r[c]));
        if (a !== b) f.push(j["Complaint No"] + ": columns 1-31 differ from export:data");
        if (r["Is Synthetic"] === "0") real++;
        else syn++;
      }
      const realCsv = rows.filter((r) => r["Is Synthetic"] === "0").length;
      if (real !== realCsv) f.push(`grievances.csv has ${realCsv} real rows, export:data has ${real}`);
      check(12, "Real rows byte-identical (cols 1-31) to npm run export:data", f,
        `${real} real row(s) identical` + (syn ? `; ${syn} synthetic rows also identical` : ""));
    }

    // ---- extra: ground-truth file ------------------------------------------
    if (fs.existsSync(paths.truth)) {
      const f = [];
      const synCodes = new Set(synthetic.map((r) => r["Complaint No"]));
      const truth = csv.parseObjects(fs.readFileSync(paths.truth, "utf8")).rows;
      for (const t of truth) if (!synCodes.has(t["Complaint No"])) f.push(t["Complaint No"] + ": not a synthetic complaint");
      check(13, "duplicate_truth.csv refers only to synthetic complaints", f, `${truth.length} rows`);
    }
  } finally {
    if (own) await conn.end();
  }

  const ok = results.every((r) => r.pass);
  return { ok, results };
}

if (require.main === module) {
  (async () => {
    console.log("Database: " + describeDb());
    console.log("Dataset:  " + files.datasetDir(ROOT) + "\n");
    const { ok, results } = await runValidation({ root: ROOT });
    const failed = results.filter((r) => !r.pass).length;
    console.log("\n" + (ok ? "ALL CHECKS PASSED" : failed + " CHECK(S) FAILED") + ` (${results.length} checks)`);
    process.exit(ok ? 0 : 1);
  })().catch((e) => {
    console.error("Validation error:", e.message);
    process.exit(1);
  });
}

module.exports = { runValidation };
