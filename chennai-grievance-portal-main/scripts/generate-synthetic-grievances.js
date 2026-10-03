/**
 * Pre-fills the grievance dataset with realistic synthetic complaints.
 *
 *   node scripts/generate-synthetic-grievances.js               # npm run dataset:generate
 *   node scripts/generate-synthetic-grievances.js --reset-synthetic
 *   node scripts/generate-synthetic-grievances.js --dry-run     # stats only, writes nothing
 *   node scripts/generate-synthetic-grievances.js --csv-only    # CSVs only, no DB writes
 *   node scripts/generate-synthetic-grievances.js --unseed      # npm run dataset:unseed
 *
 * Options
 *   --days 90            length of the history, ending on --end
 *   --end YYYY-MM-DD     last day (default: today, IST)
 *   --per-day 90         mean complaints per ordinary day
 *   --seed 42            PRNG seed; same seed + same reference data = same output
 *   --rain-days a,b,...  thunderstorm days (default: 5 picked in Aug-Sep)
 *   --no-validate        skip the validation pass after writing
 *
 * Default mode inserts complaints and their status history into MySQL with
 * is_synthetic = 1, user_id = NULL and changed_by = NULL, in transactions of
 * 500, then rebuilds the CSVs from MySQL with the shared SELECT, validates
 * them and writes GENERATION_REPORT.md.
 *
 * SAFETY: every DELETE is restricted to is_synthetic = 1. Real complaints
 * (is_synthetic = 0) are never modified or deleted.
 *
 * Everything is built from the reference data already in the database and the
 * ward polygons in data/boundaries/. No network calls; "Other" complaints are
 * routed with the keyword classifier only, never OpenAI.
 */
const fs = require("fs");
const path = require("path");
const mysql = require("mysql2/promise");
const { getDbConfig, describeDb } = require("./db-config");
const T = require("./lib/synthetic-text");
const geo = require("./lib/ward-geo");
const { classifyOther } = require("./lib/keyword-classifier");
const intel = require("./lib/intel");
const { COMPLAINT_COLUMNS } = require("./lib/dataset-sql");
const { exportDataset, fetchDataset } = require("./lib/dataset-export");
const files = require("./lib/dataset-files");
const csv = require("./lib/csv");

const ROOT = path.join(__dirname, "..");
const SEC = 1000;
const MIN = 60 * SEC;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

// ---------------------------------------------------------------- options --

function arg(flag, fallback) {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : fallback;
}
const has = (flag) => process.argv.includes(flag);

const OPTS = {
  days: Number(arg("--days", 90)),
  end: arg("--end", null),
  perDay: Number(arg("--per-day", 90)),
  seed: Number(arg("--seed", 42)),
  rainDays: arg("--rain-days", null),
  csvOnly: has("--csv-only"),
  dryRun: has("--dry-run"),
  reset: has("--reset-synthetic"),
  unseed: has("--unseed"),
  validate: !has("--no-validate")
};

// ------------------------------------------------------------ parameters --

/** Category shares (percent). The three MEGA STREETS categories share one 2% pool. */
const CATEGORY_SHARE = {
  Garbage: 22, "Road and Footpath": 14, "Street Light": 12, "Public Health": 12,
  "Water Stagnation": 9, "Storm Water Drains": 4, "Public Toilet": 4, "Park and Playground": 4,
  General: 3, "Tax and Licence": 3, "Building Plan Permission": 2, Flood: 1, "Air Quality": 1,
  "Voter ID": 1, MEGA: 2, Other: 4
};
const FREQUENT_BOOST = 5;
/** Extra within-category weight for specific sub types. */
const SUBTYPE_BOOST = {
  "Mosquito Menace": 4,
  "Public Health / Dengue / Malaria / Gastro Enteritis": 2,
  "Issues regarding Amma nutrition kit for pregnant woman": 0.3,
  "Issues regarding Muthulakshmi Reddy Maternity Benefit Scheme (MRMBS)": 0.3,
  "Issues regarding amma baby care kits (After Delivery)": 0.3,
  "Issues regarding janani suraksha yojana scheme (JSY)": 0.3,
  "Overflowing of Garbage Bin": 2,
  "Removal of Fallen Trees": 1.5
};

/**
 * Relative complaint volume per zone. The repo has no population data, so this
 * is a documented proxy: older, denser central and north Chennai zones score
 * higher than the newer, sparser southern and north-western fringe.
 */
const ZONE_WEIGHT = {
  Royapuram: 1.35, Tondiarpet: 1.3, "Thiru-Vi-Ka-Nagar": 1.3, Teynampet: 1.3,
  "Anna Nagar": 1.25, Kodambakkam: 1.25, Adyar: 1.05, Ambattur: 1.0, Thiruvottiyur: 0.95,
  Valasaravakkam: 0.95, Alandur: 0.9, Perungudi: 0.9, Madhavaram: 0.85, Sholinganallur: 0.85,
  Manali: 0.7
};
/** Where rain-driven complaints concentrate. */
const LOW_LYING = new Set([
  "Royapuram", "Tondiarpet", "Teynampet", "Kodambakkam", "Adyar", "Perungudi", "Sholinganallur", "Valasaravakkam"
]);
const LOW_LYING_RAIN_BOOST = 3;
const RAIN_WATER_CATEGORIES = new Set(["Water Stagnation", "Storm Water Drains", "Flood"]);
const RAIN_TREE_SUBTYPES = new Set(["Removal of Fallen Trees", "Obstruction of Trees", "Tree Pruning"]);

/** Filing volume by IST hour: peaks 08-11 and 18-22, trough 01-05. */
const HOUR_PROFILE = [1.2, 0.6, 0.4, 0.3, 0.3, 0.6, 1.5, 3, 5, 5.5, 5, 4.5, 3.5, 3, 3, 3.2, 3.5, 4, 5, 5.5, 5.5, 5, 3.5, 2];
const WEEKDAY_FACTOR = { 0: 0.75, 1: 1.15 }; // Sunday, Monday

/**
 * APPROXIMATE PIN codes per zone: plausible Chennai PINs for localities in
 * each zone, all within 600001-600119. Not an authoritative zone-to-PIN map;
 * PIN areas do not follow ward boundaries.
 */
const PIN_BY_ZONE = {
  1: ["600019", "600057"],
  2: ["600068", "600051"],
  3: ["600060", "600051", "600066", "600099"],
  4: ["600081", "600021", "600039"],
  5: ["600001", "600003", "600013", "600079", "600112"],
  6: ["600011", "600012", "600039", "600082"],
  7: ["600053", "600058", "600098", "600050"],
  8: ["600040", "600102", "600101", "600030", "600029", "600010"],
  9: ["600018", "600006", "600014", "600004", "600086", "600005", "600002"],
  10: ["600024", "600017", "600033", "600078", "600026", "600093"],
  11: ["600087", "600116", "600089", "600095"],
  12: ["600016", "600061", "600088", "600032"],
  13: ["600020", "600041", "600090", "600015", "600085"],
  14: ["600096", "600091", "600100", "600042"],
  15: ["600119", "600097", "600115", "600041"]
};

const RATES = {
  gccStreet: 0.88,
  mapPin: 0.85,
  locationPin: 0.6,
  landmark: 0.5,
  anonymous: 0.08,
  photo: 0.45,
  email: 0.45,
  lastName: 0.4,
  initials: 0.5,
  transgender: 0.003,
  female: 0.447,
  repeatComplainant: 0.1,
  junk: 0.015,
  labelTitle: 0.8,
  duplicate: 0.06,
  stall: 0.12,
  // Department-officer rejection: junk and duplicates mostly, plus a small
  // base rate, which together land near 6% of all complaints.
  junkRejectDO: 0.85,
  duplicateRejectDO: 0.6,
  baseRejectDO: 0.012,
  rejectCollector: 0.022
};
const LANGUAGE_MIX = { en: 0.6, ta: 0.25, tanglish: 0.15 };

const HOTSPOT_SUBTYPES = [
  "Removal of Garbage", "Overflowing of Garbage Bin", "Stagnation of Water", "Non burning of Street lights",
  "Street Dogs", "Pot hole fill up / Repairs to the damaged surface", "Mosquito Menace",
  "Removal of Fallen Trees", "Obstruction of Water Flow", "Water entering Home/Shop",
  "Burning of Garbage at Dumping Ground"
];
const RAIN_HOTSPOT_SUBTYPES = new Set([
  "Stagnation of Water", "Obstruction of Water Flow", "Water entering Home/Shop", "Removal of Fallen Trees"
]);

// ------------------------------------------------------------------- rng --

function mulberry32(a) {
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function makeRng(seed) {
  const r = mulberry32(seed);
  const R = {
    next: r,
    float: (a, b) => a + (b - a) * r(),
    int: (a, b) => a + Math.floor(r() * (b - a + 1)),
    chance: (p) => r() < p,
    pick: (arr) => arr[Math.floor(r() * arr.length)],
    normal() {
      let u = 0;
      while (u === 0) u = r();
      return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * r());
    },
    lognormal: (median, sigma) => median * Math.exp(sigma * R.normal()),
    poisson(lambda) {
      if (lambda > 60) return Math.max(0, Math.round(lambda + Math.sqrt(lambda) * R.normal()));
      const L = Math.exp(-lambda);
      let k = 0;
      let p = 1;
      do {
        k++;
        p *= r();
      } while (p > L);
      return k - 1;
    },
    shuffle(a) {
      for (let i = a.length - 1; i > 0; i--) {
        const j = Math.floor(r() * (i + 1));
        [a[i], a[j]] = [a[j], a[i]];
      }
      return a;
    }
  };
  return R;
}

/** Weighted sampler over items (binary search on the cumulative weights). */
function sampler(R, items, weights) {
  const cum = [];
  let s = 0;
  for (const w of weights) cum.push((s += w));
  return () => {
    const x = R.next() * s;
    let lo = 0;
    let hi = cum.length - 1;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (cum[m] > x) hi = m;
      else lo = m + 1;
    }
    return items[lo];
  };
}

const clip = (x, lo, hi) => Math.min(Math.max(x, lo), hi);
const wholeSec = (ms) => Math.floor(ms / SEC) * SEC;

// ------------------------------------------------------------------ time --

const fmtSec = (ms) => new Date(ms).toISOString().slice(0, 19).replace("T", " ");
const fmtMin = (ms) => fmtSec(ms).slice(0, 16);
const dateOf = (ms) => fmtSec(ms).slice(0, 10);
const dayStart = (s) => Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10));

// -------------------------------------------------------- reference data --

async function loadReference(conn) {
  const q = async (sql) => (await conn.query(sql))[0];

  const subtypes = await q(
    `SELECT s.id, s.label, s.department_id, s.mapping_status, s.is_frequent, cc.name AS category
       FROM complaint_subtypes s JOIN complaint_categories cc ON cc.id = s.category_id
      WHERE s.is_active = 1 ORDER BY s.id`
  );
  const departments = await q("SELECT id, name FROM departments ORDER BY id");
  const wards = await q(
    `SELECT zw.ward_number, zw.zone_id, z.zone_number, z.zone_name
       FROM zone_wards zw JOIN zones z ON z.id = zw.zone_id ORDER BY zw.ward_number`
  );
  const areaWards = await q("SELECT area_id, ward_number, is_primary FROM area_wards");
  const areas = await q("SELECT id, name FROM gcc_areas");
  const localities = await q("SELECT id, name, area_id FROM gcc_localities");
  const streets = await q("SELECT id, name, locality_id FROM gcc_streets");
  const codes = await q("SELECT complaint_code FROM complaints");

  const deptName = new Map(departments.map((d) => [d.id, d.name]));
  const deptId = new Map(departments.map((d) => [d.name, d.id]));

  const streetsByLoc = new Map();
  for (const s of streets) {
    if (!streetsByLoc.has(s.locality_id)) streetsByLoc.set(s.locality_id, []);
    streetsByLoc.get(s.locality_id).push(s);
  }
  // Only localities (and so areas) that actually have streets can be picked.
  const locsByArea = new Map();
  for (const l of localities) {
    if (!streetsByLoc.has(l.id)) continue;
    if (!locsByArea.has(l.area_id)) locsByArea.set(l.area_id, []);
    locsByArea.get(l.area_id).push(l);
  }
  const areaWardCount = new Map();
  const areaPrimary = new Map();
  for (const aw of areaWards) {
    areaWardCount.set(aw.area_id, (areaWardCount.get(aw.area_id) || 0) + 1);
    if (aw.is_primary === 1) areaPrimary.set(aw.area_id, aw.ward_number);
  }
  const areasByWard = new Map();
  for (const aw of areaWards) {
    if (!locsByArea.has(aw.area_id)) continue;
    if (!areasByWard.has(aw.ward_number)) areasByWard.set(aw.ward_number, []);
    areasByWard.get(aw.ward_number).push({ areaId: aw.area_id, primary: aw.is_primary === 1 });
  }

  return {
    subtypes,
    subByLabel: new Map(subtypes.map((s) => [s.category + "|" + s.label, s])),
    departments,
    deptName,
    deptId,
    wards,
    wardInfo: new Map(wards.map((w) => [w.ward_number, w])),
    areaName: new Map(areas.map((a) => [a.id, a.name])),
    locsByArea,
    streetsByLoc,
    areasByWard,
    areaWardCount,
    areaPrimary,
    allStreets: streets,
    existingCodes: new Set(codes.map((c) => c.complaint_code))
  };
}

function findSubtype(ref, label) {
  const s = ref.subtypes.find((x) => x.label === label && !x.category.startsWith("MEGA"));
  if (!s) throw new Error("Sub type not found in complaint_subtypes: " + label);
  return s;
}

// ------------------------------------------------------------- generator --

function generate(ref, opts) {
  const R = makeRng(opts.seed);
  const endDate = opts.end || dateOf(intel.nowIst());
  if (!/^\d{4}-\d{2}-\d{2}$/.test(endDate)) throw new Error("--end must be YYYY-MM-DD");
  const endMs = dayStart(endDate);
  const now = wholeSec(Math.min(intel.nowIst(), endMs + DAY - SEC));
  if (endMs > intel.nowIst()) throw new Error("--end cannot be in the future");
  const startMs = endMs - (opts.days - 1) * DAY;
  const dates = [];
  for (let i = 0; i < opts.days; i++) dates.push(dateOf(startMs + i * DAY));

  const warnings = [];

  // ---- self-check: every Other template must route where it claims -------
  for (const o of T.OTHER) {
    for (const title of ["Other / not listed above", o.title]) {
      const got = classifyOther(title + ". " + o.text);
      if (got !== o.dept) {
        throw new Error(`Other template routes to ${got}, expected ${o.dept}: "${title}. ${o.text}"`);
      }
    }
  }

  // ---- rain events --------------------------------------------------------
  let rainDays;
  if (opts.rainDays) {
    rainDays = opts.rainDays.split(",").map((s) => s.trim()).filter(Boolean);
    for (const d of rainDays) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) throw new Error("--rain-days: bad date " + d);
      if (d < dates[0] || d > endDate) warnings.push("rain day " + d + " is outside the generated range");
    }
  } else {
    // Chennai's pre-monsoon thunderstorms: August and September.
    const eligible = dates.filter((d) => d.slice(5, 7) === "08" || d.slice(5, 7) === "09");
    const pool = eligible.length >= 5 ? eligible : dates;
    rainDays = [];
    let guard = 0;
    while (rainDays.length < Math.min(5, pool.length) && guard++ < 1000) {
      const d = R.pick(pool);
      // Keep events at least 4 days apart so their 3-day windows do not merge.
      if (rainDays.every((x) => Math.abs(dayStart(x) - dayStart(d)) >= 4 * DAY)) rainDays.push(d);
    }
    rainDays.sort();
  }
  const rainMultByDate = new Map();
  const rainEventOf = new Map();
  for (const d of rainDays) {
    const m = R.float(3, 4);
    for (let k = 0; k < 3; k++) {
      const dd = dateOf(dayStart(d) + k * DAY);
      if (m > (rainMultByDate.get(dd) || 0)) {
        rainMultByDate.set(dd, m);
        rainEventOf.set(dd, d);
      }
    }
  }

  // ---- sub-type weights ---------------------------------------------------
  const catKey = (cat) => (cat.startsWith("MEGA STREETS") ? "MEGA" : cat);
  const byCat = new Map();
  for (const s of ref.subtypes) {
    const k = catKey(s.category);
    if (!byCat.has(k)) byCat.set(k, []);
    byCat.get(k).push(s);
  }
  const withinWeight = new Map();
  for (const [k, list] of byCat) {
    const raw = list.map((s) => (s.is_frequent ? FREQUENT_BOOST : R.float(0.6, 1.4)) * (SUBTYPE_BOOST[s.label] || 1));
    const sum = raw.reduce((a, b) => a + b, 0);
    list.forEach((s, i) => withinWeight.set(s.id, raw[i] / sum));
    if (CATEGORY_SHARE[k] === undefined) warnings.push("category without a share: " + k);
  }
  const baseWeight = ref.subtypes.map((s) => (CATEGORY_SHARE[catKey(s.category)] || 0) * withinWeight.get(s.id));
  const baseTotal = baseWeight.reduce((a, b) => a + b, 0);
  const rainFactorOf = (s, m) =>
    RAIN_WATER_CATEGORIES.has(s.category) ? m : RAIN_TREE_SUBTYPES.has(s.label) || s.category === "Street Light" ? 2 : 1;
  const isRainCategory = (s) => rainFactorOf(s, 3) > 1;

  const baseSubSampler = sampler(R, ref.subtypes, baseWeight);
  function daySubSampler(date) {
    const m = rainMultByDate.get(date);
    if (!m) return { sample: baseSubSampler, factor: 1 };
    const w = ref.subtypes.map((s, i) => baseWeight[i] * rainFactorOf(s, m));
    return { sample: sampler(R, ref.subtypes, w), factor: w.reduce((a, b) => a + b, 0) / baseTotal };
  }

  // ---- ward weights -------------------------------------------------------
  const wardWeight = ref.wards.map((w) => (ZONE_WEIGHT[w.zone_name] || 1) * R.float(0.7, 1.3));
  const wardNos = ref.wards.map((w) => w.ward_number);
  const wardSampler = sampler(R, wardNos, wardWeight);
  const rainWardSampler = sampler(
    R,
    wardNos,
    ref.wards.map((w, i) => wardWeight[i] * (LOW_LYING.has(w.zone_name) ? LOW_LYING_RAIN_BOOST : 1))
  );

  // ---- geometry -----------------------------------------------------------
  const polysByWard = new Map();
  for (const f of geo.loadWardFeatures()) {
    if (!polysByWard.has(f.wardNo)) polysByWard.set(f.wardNo, []);
    polysByWard.get(f.wardNo).push(f);
  }
  const wardCentre = new Map([...polysByWard.entries()].map(([w, ps]) => {
    const b = ps.map((p) => p.bbox);
    return [w, { lat: b.reduce((a, x) => a + (x[1] + x[3]) / 2, 0) / b.length, lng: b.reduce((a, x) => a + (x[0] + x[2]) / 2, 0) / b.length }];
  }));
  const wardKm = (a, b) => {
    const p = wardCentre.get(a), q = wardCentre.get(b);
    if (!p || !q) return Infinity;
    return Math.hypot((p.lat - q.lat) * 111.2, (p.lng - q.lng) * 111.2 * Math.cos((p.lat * Math.PI) / 180));
  };

  // GCC "areas" range from one ward to old divisions of 20-49 wards (EGMORE spans 26), and an
  // address ends with its area name. So a ward only uses an area that is plausibly its own: the
  // area's primary ward, a small area (3 wards or fewer), or an area whose primary ward is within
  // AREA_NEAR_KM. Otherwise the area whose primary ward is nearest. This keeps "Pantheon Road,
  // Egmore" near Egmore instead of anywhere in the 26 wards GCC lists for EGMORE; with no area
  // within 2.5 km the street is typed, with no area name.
  const AREA_NEAR_KM = 1.2;
  const areaSamplers = new Map();
  function areaFor(ward) {
    if (!areaSamplers.has(ward)) {
      const list = ref.areasByWard.get(ward) || [];
      const fits = (a) => a.primary || ref.areaWardCount.get(a.areaId) <= 3 || wardKm(ref.areaPrimary.get(a.areaId), ward) <= AREA_NEAR_KM;
      let ok = list.filter(fits);
      if (!ok.length && list.length) {
        const d = (a) => wardKm(ref.areaPrimary.get(a.areaId), ward);
        const near = list.reduce((m, a) => (d(a) < d(m) ? a : m));
        // no area is close enough to name: the resident types the street instead
        ok = d(near) <= 2.5 ? [near] : [];
      }
      areaSamplers.set(ward, ok.length ? sampler(R, ok.map((a) => a.areaId), ok.map((a) => (a.primary ? 3 : 1))) : null);
    }
    const s = areaSamplers.get(ward);
    return s ? s() : null;
  }
  const accepts = (lat, lng, ward) => {
    if (!geo.pointInWard(lat, lng, ward)) return false;
    const r = geo.resolveWard(lat, lng);
    return r.status === "resolved" && r.wardNo === ward && !r.ambiguous;
  };
  const round7 = (x) => Number(x.toFixed(7));
  function randomPointInWard(ward) {
    const polys = polysByWard.get(ward);
    if (!polys) throw new Error("No polygon for ward " + ward);
    const areaW = polys.map((p) => (p.bbox[2] - p.bbox[0]) * (p.bbox[3] - p.bbox[1]));
    const pickPoly = sampler(R, polys, areaW);
    for (let i = 0; i < 2000; i++) {
      const [minLng, minLat, maxLng, maxLat] = pickPoly().bbox;
      const lat = round7(R.float(minLat, maxLat));
      const lng = round7(R.float(minLng, maxLng));
      if (accepts(lat, lng, ward)) return { lat, lng };
    }
    throw new Error("Could not place a point inside ward " + ward);
  }
  function pointNear(lat, lng, ward, maxM, minM = 0) {
    for (let i = 0; i < 80; i++) {
      const d = R.float(minM, maxM);
      const th = R.float(0, 2 * Math.PI);
      const la = round7(lat + (d * Math.cos(th)) / 111320);
      const ln = round7(lng + (d * Math.sin(th)) / (111320 * Math.cos((lat * Math.PI) / 180)));
      if (accepts(la, ln, ward)) return { lat: la, lng: ln };
    }
    return null;
  }

  // ---- text ---------------------------------------------------------------
  const langSampler = sampler(R, Object.keys(LANGUAGE_MIX), Object.values(LANGUAGE_MIX));
  const otherSampler = sampler(R, T.OTHER, T.OTHER.map((o) => {
    const n = T.OTHER.filter((x) => x.dept === o.dept).length;
    return (T.OTHER_DEPT_WEIGHT[o.dept] || 1) / n;
  }));
  const titleCase = (s) => s.toLowerCase().replace(/\b([a-z])/g, (m) => m.toUpperCase());
  const streetDisplay = (c) => (c.streetName ? titleCase(c.streetName) : c.manualStreet + (c.streetType ? " " + c.streetType : ""));
  const fillDur = (s) => s.replace("{n}", String(R.int(2, 12)));

  function truncate400(s) {
    if (s.length <= 400) return s;
    const cut = s.slice(0, 400);
    const end = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("। "));
    return end > 200 ? cut.slice(0, end + 1) : cut.trimEnd();
  }

  function junkText() {
    if (R.chance(0.5)) return R.pick(T.JUNK_WORDS);
    let s = "";
    for (let i = R.int(5, 12); i > 0; i--) s += T.KEYBOARD[R.int(0, T.KEYBOARD.length - 1)];
    return s;
  }

  /**
   * Title and Details for c. lang is the language to write in (ignored for
   * "Other", whose templates carry their own); junk is "maybe" (the 1.5% noise
   * rate applies), "never" or "always".
   */
  function writeText(c, lang, junk = "never") {
    const s = c.sub;
    const cat = s.category;
    c.junk = false;

    // Slot filler for the resident's own words (street, locality, counts, times).
    const fill = (x) => x
      .replace(/\{street\}/g, streetDisplay(c))
      .replace(/\{locality\}/g, c.localityName ? titleCase(c.localityName) : { en: "our area", ta: "எங்கள் பகுதி", tanglish: "engal area" }[c.lang])
      .replace(/\{house\}/g, String(R.int(1, 180)) + (R.chance(0.2) ? "/" + R.int(1, 12) : ""))
      .replace(/\{n\}/g, String(R.int(4, 60)))
      .replace(/\{k\}/g, String(R.int(2, 6)))
      .replace(/\{m\}/g, String(R.int(3, 25)))
      .replace(/\{len\}/g, String(R.int(3, 30) * 10))
      .replace(/\{hour\}/g, String(R.int(6, 10)))
      .replace(/\{days\}/g, String(R.int(3, 7)))
      .replace(/\{day\}/g, R.pick(T.DAYS));

    if (cat === "Other") {
      const tpl = otherSampler();
      c.lang = tpl.lang;
      const parts = [tpl.text];
      if (R.chance(0.4)) parts.unshift(fill(R.pick(T.OPENER[tpl.lang])));
      if (R.chance(0.25)) parts.push(fill(R.pick(T.EXTENT[tpl.lang])));
      if (R.chance(0.1)) parts.push(R.pick(T.REPEAT[tpl.lang]));
      if (R.chance(0.6)) parts.push(R.pick(T.PLEA[tpl.lang]));
      c.details = truncate400(parts.join(" "));
      c.title = R.chance(0.5) ? s.label : tpl.title;
      // The API classifies [otherDescription, title, description].join(". ").
      c.deptName = classifyOther([c.title, c.details].join(". "));
      c.otherTarget = tpl.dept;
      return;
    }

    if (junk === "always" || (junk === "maybe" && R.chance(RATES.junk))) {
      c.junk = true;
      c.lang = "en";
      c.details = junkText();
      c.title = R.chance(RATES.labelTitle) ? s.label : junkText();
      return;
    }

    c.lang = lang;
    const key = catKey(cat);
    const specific = T.BY_SUBTYPE[s.label] && T.BY_SUBTYPE[s.label][lang];
    const general = (T.BY_CATEGORY[key] && T.BY_CATEGORY[key][lang]) || T.GENERIC[lang];
    const core = specific && R.chance(0.8) ? R.pick(specific) : R.pick(general);

    let place = "";
    if (lang === "en") {
      if (c.landmark && !/[஀-௿]/.test(c.landmark)) {
        place = c.landmark[0].toLowerCase() + c.landmark.slice(1);
        if (!/^(near|opp|behind|next)/i.test(place)) place = "near " + place;
      } else {
        place = R.pick(["in our street", "in our area", "on " + streetDisplay(c)]);
      }
    }
    let text = core
      .replace("{sub}", s.label)
      .replace("{subl}", s.label.toLowerCase())
      .replace("{place}", place)
      .replace("{dur}", fillDur(R.pick(T.DUR[lang])));
    text = text.replace(/\s+/g, " ").replace(/\s+([.,;:])/g, "$1").replace(/\bon on\b/g, "on").trim();
    text = text[0].toUpperCase() + text.slice(1);

    // Who is writing, what exactly they see, when and how many are affected: each optional,
    // so complaints about the same problem read like different residents wrote them.
    // a light that burns in the daytime is not "off"
    const details = ((T.DETAIL[key] && T.DETAIL[key][lang]) || []).filter((x) => !(/daytime/i.test(s.label) && /\boff\b|dark|eriyala|எரியவில்லை/i.test(x)));
    const parts = [];
    if (R.chance(0.35)) parts.push(fill(R.pick(T.OPENER[lang])));
    parts.push(text);
    if (details.length && R.chance(0.75)) {
      const first = R.pick(details);
      parts.push(fill(first));
      if (details.length > 2 && R.chance(0.25)) parts.push(fill(R.pick(details.filter((x) => x !== first))));
    }
    if (R.chance(0.3)) parts.push(fill(R.pick(T.WHEN[lang])));
    if (R.chance(0.3)) parts.push(fill(R.pick(T.EXTENT[lang])));
    if (R.chance(0.35)) parts.push(R.pick(T.IMPACT[lang]));
    if (R.chance(0.15)) parts.push(R.pick(T.REPEAT[lang]));
    if (R.chance(0.55)) parts.push(R.pick(T.PLEA[lang]));
    c.details = truncate400(parts.join(" "));

    const shorts =
      (T.SHORT_TITLES_BY_SUBTYPE[s.label] && T.SHORT_TITLES_BY_SUBTYPE[s.label][lang]) ||
      (T.SHORT_TITLES[key] && T.SHORT_TITLES[key][lang]);
    c.title = !shorts || R.chance(RATES.labelTitle) ? s.label : R.pick(shorts);
  }

  function landmarkFor(lang) {
    if (!R.chance(RATES.landmark)) return null;
    const tamil = lang === "ta" ? R.chance(0.6) : R.chance(0.15);
    return R.pick(tamil ? T.LANDMARKS.ta : T.LANDMARKS.en);
  }

  // ---- location -----------------------------------------------------------
  function typedStreet(c) {
    let name = R.pick(T.TYPED_STREET_BASES) + R.pick(T.TYPED_STREET_ORDINALS);
    let type = null;
    if (R.chance(0.45)) type = R.pick(T.STREET_TYPES);
    else name += " " + R.pick(T.TYPED_STREET_SUFFIXES);
    const style = R.next();
    if (style > 0.85) name = name.toUpperCase();
    else if (style > 0.55) name = titleCase(name);
    c.manualStreet = name;
    c.streetType = type;
  }

  function setWard(c, ward) {
    const w = ref.wardInfo.get(ward);
    c.ward = ward;
    c.zoneId = w.zone_id;
    c.zoneNumber = w.zone_number;
    c.zoneName = w.zone_name;
  }

  function placeStreet(c) {
    const areaId = R.chance(RATES.gccStreet) ? areaFor(c.ward) : null;
    if (areaId) {
      const loc = R.pick(ref.locsByArea.get(areaId));
      const st = R.pick(ref.streetsByLoc.get(loc.id));
      c.areaId = areaId;
      c.areaName = ref.areaName.get(areaId);
      c.localityId = loc.id;
      c.localityName = loc.name;
      c.streetId = st.id;
      c.streetName = st.name;
    } else {
      typedStreet(c);
    }
  }

  function newComplaint(t, sub, opt = {}) {
    const date = dateOf(t);
    const rain = rainMultByDate.has(date);
    const c = { t: wholeSec(t), sub, rain, rainEvent: rainEventOf.get(date) || null };
    const ward = opt.ward || (rain && isRainCategory(sub) ? rainWardSampler() : wardSampler());
    setWard(c, ward);
    placeStreet(c);
    if (R.chance(RATES.mapPin)) {
      Object.assign(c, randomPointInWard(ward));
      c.wardSource = "map_boundary";
    } else {
      c.wardSource = "user_selected";
    }
    c.pin = R.chance(RATES.locationPin) ? R.pick(PIN_BY_ZONE[c.zoneNumber]) : null;
    const lang = langSampler();
    c.landmark = landmarkFor(lang);
    writeText(c, lang, "maybe");
    finishRouting(c);
    return c;
  }

  function finishRouting(c) {
    const s = c.sub;
    if (s.category === "Other") {
      c.deptId = ref.deptId.get(c.deptName);
      c.needsReview = true;
    } else {
      c.deptId = s.department_id;
      c.deptName = ref.deptName.get(s.department_id);
      c.needsReview = s.mapping_status !== "mapped";
    }
  }

  // ---- 1. base volume -----------------------------------------------------
  // --per-day is the overall average, so the weekday pattern, rain boosts,
  // hotspot bursts and duplicates are carved out of it rather than added on:
  // the default 90/day over 90 days lands near 8,100 complaints in total.
  const hourSampler = sampler(R, HOUR_PROFILE.map((_, h) => h), HOUR_PROFILE);
  const dayPlan = dates.map((date) => {
    const d0 = dayStart(date);
    return { date, d0, weekday: WEEKDAY_FACTOR[new Date(d0).getUTCDay()] || 1, ...daySubSampler(date) };
  });
  const expectedHotspots = 12.5 * 12.5; // bursts x mean size
  const shape = dayPlan.reduce((a, p) => a + p.weekday * p.factor, 0);
  const baseScale = Math.max(opts.perDay * opts.days * (1 - RATES.duplicate) - expectedHotspots, 0) / shape;
  const base = [];
  for (const { d0, weekday, sample, factor } of dayPlan) {
    const n = R.poisson(baseScale * weekday * factor);
    for (let i = 0; i < n; i++) {
      const t = d0 + hourSampler() * HOUR + R.int(0, 59) * MIN + R.int(0, 59) * SEC;
      if (t > now) continue;
      base.push(newComplaint(t, sample()));
    }
  }

  // ---- 2. hotspot bursts --------------------------------------------------
  const hotspots = [];
  const hotComplaints = [];
  const nHot = R.int(10, 15);
  for (let h = 0; h < nHot; h++) {
    const label = R.pick(HOTSPOT_SUBTYPES);
    const sub = findSubtype(ref, label);
    const rainy = RAIN_HOTSPOT_SUBTYPES.has(label) && rainDays.length > 0;
    let start = rainy
      ? dayStart(R.pick(rainDays)) + R.int(6, 20) * HOUR
      : startMs + R.float(0, Math.max(now - 48 * HOUR - startMs, DAY));
    if (start > now - 2 * HOUR) start = now - 30 * HOUR;
    const ward = rainy ? rainWardSampler() : wardSampler();
    const center = randomPointInWard(ward);
    const shared = { ward };
    setWard(shared, ward);
    placeStreet(shared);
    const id = "HOT-" + String(h + 1).padStart(2, "0");
    const k = R.int(5, 20);
    let made = 0;
    const peak = Math.max(...HOUR_PROFILE);
    for (let j = 0; j < k; j++) {
      // Burst members still follow the hour-of-day filing profile.
      let t;
      do t = start + R.float(0, 48 * HOUR);
      while (!R.chance(HOUR_PROFILE[new Date(t).getUTCHours()] / peak));
      if (t > now) continue;
      const c = newComplaint(t, sub, { ward });
      if (R.chance(0.6)) {
        for (const f of ["areaId", "areaName", "localityId", "localityName", "streetId", "streetName", "manualStreet", "streetType"]) {
          c[f] = shared[f];
        }
      }
      const p = R.chance(RATES.mapPin) ? pointNear(center.lat, center.lng, ward, 250) : null;
      if (p) {
        c.lat = p.lat;
        c.lng = p.lng;
        c.wardSource = "map_boundary";
      } else {
        delete c.lat;
        delete c.lng;
        c.wardSource = "user_selected";
      }
      // Rewritten because the street (which English text may name) changed.
      writeText(c, c.lang, c.junk ? "always" : "never");
      c.truth = id;
      c.hotspot = true;
      hotComplaints.push(c);
      made++;
    }
    hotspots.push({ id, label, ward, zone: shared.zoneName, start: fmtMin(start), complaints: made, rain: rainy });
  }

  // ---- 3. near-duplicate re-reports --------------------------------------
  const candidates = base.filter((c) => c.lat !== undefined && c.sub.category !== "Other" && !c.junk && c.t <= now - 30 * MIN);
  const nDup = Math.round(((base.length + hotComplaints.length) * RATES.duplicate) / (1 - RATES.duplicate));
  const dups = [];
  let attempts = 0;
  while (dups.length < nDup && attempts++ < nDup * 20) {
    const orig = R.pick(candidates);
    if ((orig.dupCount || 0) >= 3) continue;
    const latest = Math.min(orig.t + 72 * HOUR, now);
    if (latest - orig.t < 20 * MIN) continue;
    const t = wholeSec(orig.t + R.float(20 * MIN, latest - orig.t));
    const d = {
      t,
      sub: orig.sub,
      rain: rainMultByDate.has(dateOf(t)),
      rainEvent: rainEventOf.get(dateOf(t)) || null
    };
    setWard(d, orig.ward);
    for (const f of ["areaId", "areaName", "localityId", "localityName", "streetId", "streetName", "manualStreet", "streetType"]) {
      if (orig[f] !== undefined) d[f] = orig[f];
    }
    const p = pointNear(orig.lat, orig.lng, orig.ward, 140, 5) || { lat: orig.lat, lng: orig.lng };
    d.lat = p.lat;
    d.lng = p.lng;
    d.wardSource = "map_boundary";
    d.pin = R.chance(0.5) ? orig.pin : R.chance(RATES.locationPin) ? R.pick(PIN_BY_ZONE[d.zoneNumber]) : null;
    // Reworded, and usually in a different language from the original.
    const langs = Object.keys(LANGUAGE_MIX).filter((l) => l !== orig.lang);
    const lang = R.chance(0.6) ? R.pick(langs) : orig.lang;
    d.landmark = R.chance(0.5) ? orig.landmark : landmarkFor(lang);
    writeText(d, lang, "never");
    finishRouting(d);
    d.dupOf = orig;
    orig.dupCount = (orig.dupCount || 0) + 1;
    dups.push(d);
  }
  if (dups.length < nDup) warnings.push(`only ${dups.length} of ${nDup} duplicates could be placed`);

  const all = [...base, ...hotComplaints, ...dups].sort((a, b) => a.t - b.t);

  // ---- 4. codes -----------------------------------------------------------
  const used = new Set(ref.existingCodes);
  const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  for (const c of all) {
    let code;
    do {
      let tail = "";
      for (let i = 0; i < 3; i++) tail += String(R.int(0, 9));
      for (let i = 0; i < 3; i++) tail += LETTERS[R.int(0, 25)];
      code = dateOf(c.t).slice(0, 4) + "-" + tail;
    } while (used.has(code));
    used.add(code);
    c.code = code;
  }
  for (const d of dups) d.truth = "DUP-" + d.dupOf.code;
  for (const c of all) if (c.dupCount) c.truth = "DUP-" + c.code;

  // ---- 5. unique Details per ward per day --------------------------------
  const seen = new Set();
  let rewrites = 0;
  for (const c of all) {
    let key = c.ward + "|" + dateOf(c.t) + "|" + c.details;
    for (let i = 0; i < 25 && seen.has(key); i++) {
      writeText(c, c.lang, c.junk ? "always" : "never");
      if (c.sub.category === "Other") finishRouting(c);
      key = c.ward + "|" + dateOf(c.t) + "|" + c.details;
      rewrites++;
    }
    if (seen.has(key)) {
      c.details = truncate400(c.details + " (" + streetDisplay(c) + ")");
      key = c.ward + "|" + dateOf(c.t) + "|" + c.details;
    }
    seen.add(key);
  }

  // ---- 6. complainants ----------------------------------------------------
  const people = [];
  const slots = [];
  while (slots.length < all.length) {
    const p = newPerson();
    people.push(p);
    const k = R.chance(RATES.repeatComplainant) ? R.int(2, 4) : 1;
    for (let i = 0; i < k; i++) slots.push(p);
  }
  R.shuffle(slots);
  all.forEach((c, i) => {
    c.person = slots[i];
    c.anonymous = R.chance(RATES.anonymous);
  });
  for (const d of dups) {
    if (d.person === d.dupOf.person) {
      d.person = newPerson();
      people.push(d.person);
    }
  }

  function newPerson() {
    const g = R.next();
    const gender = g < RATES.transgender ? "Transgender" : g < RATES.transgender + RATES.female ? "Female" : "Male";
    const first = R.pick(T.NAMES[gender]);
    let initials = null;
    if (R.chance(RATES.initials)) {
      const a = T.INITIAL_LETTERS[R.int(0, T.INITIAL_LETTERS.length - 1)];
      initials = R.chance(0.7) ? a + "." : a + ". " + T.INITIAL_LETTERS[R.int(0, T.INITIAL_LETTERS.length - 1)] + ".";
    }
    const last = R.chance(RATES.lastName) ? R.pick(T.LAST_NAMES) : null;
    const email = R.chance(RATES.email)
      ? (first + R.pick([".", "_", ""]) + (last || (initials ? initials[0] : ""))).toLowerCase() +
        R.int(1, 999) + "@" + R.pick(["example.com", "example.in"])
      : null;
    const homeWard = wardSampler();
    const homeArea = areaFor(homeWard);
    let street;
    if (homeArea) {
      const loc = R.pick(ref.locsByArea.get(homeArea));
      street = R.pick(ref.streetsByLoc.get(loc.id)).name;
    } else {
      street = R.pick(ref.allStreets).name;
    }
    const zoneNo = ref.wardInfo.get(homeWard).zone_number;
    return {
      gender,
      first,
      initials,
      last,
      // Fabricated: every synthetic mobile number is 90000 followed by five digits.
      mobile: "90000" + String(R.int(0, 99999)).padStart(5, "0"),
      email,
      address: "No. " + R.int(1, 250) + (R.chance(0.2) ? "/" + R.int(1, 9) : "") + ", " + titleCase(street).replace(/\s+/g, " "),
      pincode: R.chance(0.5) ? R.pick(PIN_BY_ZONE[zoneNo]) : null
    };
  }

  // ---- 7. media -----------------------------------------------------------
  for (const c of all) {
    if (!R.chance(RATES.photo)) continue;
    const epoch = c.t - 5.5 * HOUR - R.int(20, 600) * SEC;
    let rnd = "";
    for (let i = 0; i < 6; i++) rnd += "abcdefghijklmnopqrstuvwxyz0123456789"[R.int(0, 35)];
    const ext = R.chance(0.85) ? "jpg" : R.chance(0.8) ? "png" : "mp4";
    c.media = `/uploads/complaints/complaint-${epoch}-${rnd}.${ext}`;
  }

  // ---- 8. lifecycle -------------------------------------------------------
  const stallWeight = (c) =>
    c.deptName === "Engineering Department (Town Planning & Building Permissions)" ? 2.5
      : c.deptName === "Storm Water Drain Department" ? 2.0 : 0.6;
  const meanStall = all.reduce((a, c) => a + stallWeight(c), 0) / all.length;
  for (const c of all) {
    c.stalled = R.chance((RATES.stall * stallWeight(c)) / meanStall);
    simulate(c);
  }

  function simulate(c) {
    const ev = [];
    const push = (t, status, stage, remarks) => ev.push({ t: wholeSec(t), status, stage, remarks });
    const stallAt = c.stalled ? R.pick(["decision", "decision", "start", "complete"]) : null;
    const stall = (at) => (stallAt === at ? R.float(10, 35) * DAY : 0);
    const zoneTag = "Zone " + c.zoneNumber;

    let t = c.t;
    push(t, "Complaint Filed", "Citizen",
      c.needsReview ? "Complaint registered. Department routing pending officer review." : "Complaint registered by citizen.");

    t += R.float(61 * SEC, 30 * MIN);
    push(t, "Pending Approval", "System",
      c.needsReview ? "Routing to be confirmed by department officer." : "Forwarded to " + c.deptName + " for approval.");

    t += clip(R.lognormal(30 * HOUR, 0.8), 2 * HOUR, 72 * HOUR) + stall("decision");
    const pReject = c.junk ? RATES.junkRejectDO : c.dupOf ? RATES.duplicateRejectDO : RATES.baseRejectDO;
    if (R.chance(pReject)) {
      const reason = c.junk
        ? T.JUNK_REJECT_REASON
        : c.dupOf
          ? "Duplicate of " + c.dupOf.code + ". Action is being taken under that complaint."
          : R.pick(T.DO_REJECT_REASONS);
      push(t, "Rejected", "Department Officer", "Rejected: " + reason);
      return finish(c, ev, "Department Officer", "Rejected: " + reason);
    }
    push(t, "Approved by Department Officer", "Department Officer",
      (c.needsReview ? "Routing to " + c.deptName + " confirmed. " : "") +
        R.pick(["Approved. Assigned to AE, " + zoneTag + ".", "Approved and assigned to the Assistant Engineer, Ward " + c.ward + ".", "Approved. Forwarded to the Zonal Officer, " + c.zoneName + "."]));

    t += clip(R.lognormal(8 * HOUR, 0.9), 1 * HOUR, 72 * HOUR) + stall("start");
    const cat = c.sub.category;
    push(t, "In Progress", "Department Officer", R.pick(T.IN_PROGRESS_REMARKS[cat] || T.IN_PROGRESS_REMARKS._));

    const median = intel.workMedianDays(c.deptName, cat) * DAY;
    t += clip(R.lognormal(median, 0.6), 3 * HOUR, median * 5) + stall("complete");
    push(t, "Completed - Pending Collector Verification", "Department Officer",
      R.pick(T.COMPLETED_REMARKS[c.sub.label] || T.COMPLETED_REMARKS[cat] || T.COMPLETED_REMARKS._));

    t += clip(R.lognormal(20 * HOUR, 0.8), 6 * HOUR, 96 * HOUR);
    if (R.chance(RATES.rejectCollector)) {
      const reason = "Rejected at verification: " + R.pick(T.COLLECTOR_REJECT_REASONS);
      push(t, "Rejected", "Collector", reason);
      return finish(c, ev, "Collector", reason);
    }
    push(t, "Verified by Collector", "Collector", R.pick(T.VERIFIED_REMARKS));
    return finish(c, ev, null, null);
  }

  function finish(c, ev, rejectedStage, reason) {
    c.events = ev.filter((e) => e.t <= now);
    const last = c.events[c.events.length - 1];
    c.status = last.status;
    c.updatedT = last.t;
    const rejected = c.status === "Rejected";
    c.rejectedStage = rejected ? rejectedStage : null;
    c.remarks = rejected ? reason : null;
  }

  return {
    complaints: all,
    people,
    hotspots,
    rainDays,
    rainMultByDate,
    dates,
    startDate: dates[0],
    endDate,
    now,
    warnings,
    rewrites,
    counts: { base: base.length, hotspot: hotComplaints.length, duplicate: dups.length }
  };
}

// ------------------------------------------------------------ row shapes --

const DB_COLUMNS = [
  "complaint_code", "user_id", "initials", "first_name", "last_name", "gender", "street_address", "pincode",
  "mobile_number", "phone_number", "email", "zone_id", "ward_number", "ward_source", "gcc_area_id",
  "gcc_locality_id", "gcc_street_id", "manual_street_name", "street_type", "location_pincode",
  "specific_location", "latitude", "longitude", "department_id", "complaint_subtype_id", "title",
  "description", "media_path", "is_anonymous", "needs_manual_review", "status", "rejected_stage",
  "remarks", "created_at", "updated_at", "is_synthetic"
];

function dbRow(c) {
  const p = c.person;
  return [
    c.code, null, p.initials, p.first, p.last, p.gender, p.address, p.pincode,
    p.mobile, null, p.email, c.zoneId, c.ward, c.wardSource, c.areaId || null,
    c.localityId || null, c.streetId || null, c.streetId ? null : c.manualStreet, c.streetId ? null : c.streetType || null,
    c.pin, c.landmark, c.lat !== undefined ? c.lat.toFixed(7) : null, c.lng !== undefined ? c.lng.toFixed(7) : null,
    c.deptId, c.sub.id, c.title, c.details, c.media || null, c.anonymous ? 1 : 0, c.needsReview ? 1 : 0,
    c.status, c.rejectedStage, c.remarks, fmtSec(c.t), fmtSec(c.updatedT), 1
  ];
}

/** The row exactly as the shared SELECT would return it (columns 1-31 + Is Synthetic). */
function exportRow(c) {
  const p = c.person;
  const anon = c.anonymous;
  const street = c.streetId ? c.streetName : ((c.manualStreet || "") + " " + (c.streetType || "")).replace(/^ +| +$/g, "") || null;
  return {
    "Complaint No": c.code,
    "Filed On": fmtMin(c.t),
    Status: c.status,
    "Rejected At": c.rejectedStage,
    "Complaint Type": c.sub.category,
    "Complaint Sub Type": c.sub.label,
    Department: c.deptName,
    Routing: c.needsReview ? "Needs officer review" : "Auto-routed",
    "Routing Basis": c.sub.mapping_status,
    "Zone No": c.zoneNumber,
    Zone: c.zoneName,
    Ward: c.ward,
    "Ward Determined By": c.wardSource,
    Area: c.areaName || null,
    Locality: c.localityName || null,
    Street: street,
    "Street Source": c.streetId ? "From GCC list" : "Typed by citizen",
    Landmark: c.landmark,
    "Location PIN": c.pin,
    Latitude: c.lat !== undefined ? c.lat.toFixed(7) : null,
    Longitude: c.lng !== undefined ? c.lng.toFixed(7) : null,
    Title: c.title,
    Details: c.details,
    Anonymous: anon ? "Yes" : "No",
    Complainant: anon ? null : ((p.initials || "") + " " + p.first + " " + (p.last || "")).replace(/^ +| +$/g, ""),
    Gender: anon ? null : p.gender,
    Mobile: anon ? null : p.mobile,
    Email: anon ? null : p.email,
    "Complainant Address": anon ? null : p.address,
    "Photo Attached": c.media ? "Yes" : "No",
    "Last Updated": fmtMin(c.updatedT),
    "Is Synthetic": 1
  };
}

function historyRows(c) {
  return c.events.map((e) => ({
    "Complaint No": c.code,
    Status: e.status,
    "Changed By Role": e.stage,
    Remarks: e.remarks,
    "Changed On": fmtMin(e.t),
    "Is Synthetic": 1,
    _t: e.t
  }));
}

// -------------------------------------------------------------- database --

async function deleteSynthetic(conn) {
  let total = 0;
  for (;;) {
    await conn.beginTransaction();
    try {
      const [res] = await conn.query("DELETE FROM complaints WHERE is_synthetic = 1 LIMIT 2000");
      await conn.commit();
      total += res.affectedRows;
      if (res.affectedRows === 0) break;
    } catch (e) {
      await conn.rollback();
      throw e;
    }
  }
  return total;
}

async function insertAll(conn, complaints) {
  const BATCH = 500;
  for (let i = 0; i < complaints.length; i += BATCH) {
    const chunk = complaints.slice(i, i + BATCH);
    await conn.beginTransaction();
    try {
      await conn.query(`INSERT INTO complaints (${DB_COLUMNS.join(", ")}) VALUES ?`, [chunk.map(dbRow)]);
      const [ids] = await conn.query(
        "SELECT id, complaint_code FROM complaints WHERE is_synthetic = 1 AND complaint_code IN (?)",
        [chunk.map((c) => c.code)]
      );
      const idByCode = new Map(ids.map((r) => [r.complaint_code, r.id]));
      const hist = [];
      for (const c of chunk) {
        for (const e of c.events) hist.push([idByCode.get(c.code), e.status, e.stage, e.remarks, null, fmtSec(e.t)]);
      }
      await conn.query(
        "INSERT INTO complaint_status_history (complaint_id, status, stage, remarks, changed_by, created_at) VALUES ?",
        [hist]
      );
      await conn.commit();
    } catch (e) {
      await conn.rollback();
      throw e;
    }
    process.stdout.write(`\r  inserted ${Math.min(i + BATCH, complaints.length)}/${complaints.length}`);
  }
  process.stdout.write("\n");
}

// ----------------------------------------------------------------- stats --

function tally(rows, key) {
  const m = new Map();
  for (const r of rows) {
    const k = typeof key === "function" ? key(r) : r[key];
    m.set(k, (m.get(k) || 0) + 1);
  }
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function printStats(gen) {
  const cs = gen.complaints;
  console.log(`\nGenerated ${cs.length} synthetic complaints, ${gen.startDate} .. ${gen.endDate} (now = ${fmtMin(gen.now)} IST)`);
  console.log(`  base ${gen.counts.base}, hotspot ${gen.counts.hotspot} (${gen.hotspots.length} bursts), near-duplicates ${gen.counts.duplicate}`);
  console.log("  rain days: " + gen.rainDays.join(", "));
  console.log("  status history rows: " + cs.reduce((a, c) => a + c.events.length, 0));
  for (const [label, key] of [["Status", "status"], ["Department", "deptName"], ["Language", "lang"]]) {
    console.log("  " + label + ": " + tally(cs, key).map(([k, n]) => `${k} ${n}`).join(" | "));
  }
  if (gen.warnings.length) console.log("  warnings:\n    - " + gen.warnings.join("\n    - "));
}

// ---------------------------------------------------------------- report --

function mdTable(header, rows) {
  return (
    "| " + header.join(" | ") + " |\n|" + header.map(() => "---").join("|") + "|\n" +
    rows.map((r) => "| " + r.join(" | ") + " |").join("\n") + "\n"
  );
}

function tallyTable(rows, key, label, total) {
  return mdTable([label, "Complaints", "Share"], tally(rows, key).map(([k, n]) => [k || "(blank)", n, ((100 * n) / total).toFixed(1) + "%"]));
}

function writeReport(gen, paths, validation) {
  const all = csv.parseObjects(fs.readFileSync(paths.grievances, "utf8")).rows;
  const hist = csv.parseObjects(fs.readFileSync(paths.history, "utf8")).rows;
  const syn = all.filter((r) => r["Is Synthetic"] === "1");
  const n = syn.length;
  const rainSet = new Set(gen.rainDays);
  const byCode = new Map(gen.complaints.map((c) => [c.code, c]));

  // Calendar: one row per week, Monday first; * marks a rain-event day.
  const perDay = new Map(tally(syn, (r) => r["Filed On"].slice(0, 10)));
  const weeks = [];
  let week = null;
  for (const d of gen.dates) {
    const dow = (new Date(dayStart(d)).getUTCDay() + 6) % 7;
    if (!week || dow === 0) weeks.push((week = { start: d, cells: Array(7).fill("") }));
    week.cells[dow] = (perDay.get(d) || 0) + (rainSet.has(d) ? "*" : "");
  }

  const dupTruth = gen.complaints.filter((c) => c.truth && c.truth.startsWith("DUP-"));
  const dupGroups = new Set(dupTruth.map((c) => c.truth));
  // How well the rule-based Duplicate Group column recovers the planted re-reports.
  const csvGroup = new Map(syn.map((r) => [r["Complaint No"], r["Duplicate Group"]]));
  const planted = gen.complaints.filter((c) => c.dupOf);
  const recovered = planted.filter((c) => csvGroup.get(c.code) === csvGroup.get(c.dupOf.code)).length;
  const flagged = syn.filter((r) => r["Duplicate Group"] !== r["Complaint No"]).length;

  const langMatch = syn.filter((r) => {
    const c = byCode.get(r["Complaint No"]);
    return c && !c.junk && c.lang === r["Language"];
  }).length;
  const nonJunk = gen.complaints.filter((c) => !c.junk).length;

  const samples = [];
  for (const s of ["Complaint Filed", "Pending Approval", "Approved by Department Officer", "In Progress",
    "Completed - Pending Collector Verification", "Verified by Collector", "Rejected"]) {
    const r = syn.find((x) => x.Status === s) || all.find((x) => x.Status === s);
    if (r) samples.push(r);
  }

  const out = [];
  out.push("# Grievance dataset: generation report\n");
  out.push(`Generated ${fmtMin(intel.nowIst())} IST with seed \`${OPTS.seed}\`, ${OPTS.days} days ending ${gen.endDate}, mean ${OPTS.perDay}/day.`);
  out.push(`Mode: ${OPTS.csvOnly ? "CSV only (nothing written to MySQL)" : "inserted into MySQL (is_synthetic = 1), CSVs rebuilt from MySQL"}.\n`);
  out.push("## Totals\n");
  out.push(mdTable(["", "Rows"], [
    ["Synthetic complaints", n],
    ["Real complaints (is_synthetic = 0)", all.length - n],
    ["Status-history rows", hist.length],
    ["of which base volume", gen.counts.base],
    ["of which hotspot bursts", gen.counts.hotspot],
    ["of which planted near-duplicates", gen.counts.duplicate],
    ["Details rewritten to keep ward/day text unique", gen.rewrites]
  ]));
  out.push("\n## Rain events\n");
  out.push("Thunderstorm days (the day and the two after it are boosted): **" + gen.rainDays.join(", ") + "**. " +
    "Override with `--rain-days` to align with IMD rainfall.\n");
  out.push(mdTable(["Rain day", "Water multiplier", "Complaints that day", "Water Stagnation + SWD + Flood that day"],
    gen.rainDays.map((d) => [d, (gen.rainMultByDate.get(d) || 0).toFixed(2), perDay.get(d) || 0,
      syn.filter((r) => r["Filed On"].startsWith(d) && ["Water Stagnation", "Storm Water Drains", "Flood"].includes(r["Complaint Type"])).length])));
  out.push("\n## Complaints per day\n");
  out.push("One row per week (Monday first). `*` = rain-event day.\n");
  out.push(mdTable(["Week of", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], weeks.map((w) => [w.start, ...w.cells])));
  out.push("\n## By category\n");
  out.push(tallyTable(syn, "Complaint Type", "Category", n));
  out.push("\n## By department\n");
  out.push(tallyTable(syn, "Department", "Department", n));
  out.push("\n## By zone\n");
  out.push(tallyTable(syn, (r) => r["Zone No"] + " " + r.Zone, "Zone", n));
  out.push("\n## By status\n");
  out.push(tallyTable(syn, "Status", "Status", n));
  out.push("\nRejected at: " + tally(syn.filter((r) => r.Status === "Rejected"), "Rejected At").map(([k, v]) => `${k} ${v}`).join(", ") + ".\n");
  const recent = syn.filter((r) => intel.parseTs(r["Filed On"]) > gen.now - 3 * DAY);
  out.push(`\nLast 3 days (${recent.length} complaints): ` + tally(recent, "Status").map(([k, v]) => `${k} ${v}`).join(", ") + ".\n");
  const stalled = gen.complaints.filter((c) => c.stalled);
  out.push(`\nStalled (a gap of 10+ days before the next step): ${stalled.length} (${((100 * stalled.length) / n).toFixed(1)}%), ` +
    tally(stalled, "deptName").slice(0, 4).map(([k, v]) => `${k} ${v}`).join(", ") + ".\n");
  out.push("\n## By priority\n");
  out.push(`Snapshot as of ${fmtMin(gen.now)} (days open is part of the score).\n`);
  out.push(tallyTable(syn, "Priority", "Priority", n));
  out.push("\n## By language\n");
  out.push(tallyTable(syn, "Language", "Detected language", n));
  out.push(`\nThe \`languageOf()\` detector agrees with the language each non-junk text was written in for ${langMatch} of ${nonJunk} (${((100 * langMatch) / nonJunk).toFixed(1)}%).\n`);
  out.push("\n## Duplicates and hotspots\n");
  out.push(`- Planted near-duplicates: **${planted.length}** re-reports in **${dupGroups.size}** groups ` +
    `(same sub type, 5-140 m, within 72 h, different complainant, reworded).`);
  out.push(`- The rule-based \`Duplicate Group\` column puts **${recovered} of ${planted.length}** ` +
    `(${((100 * recovered) / Math.max(planted.length, 1)).toFixed(1)}%) planted re-reports in the same group as their original, ` +
    `and flags ${flagged} complaints in total as belonging to an earlier complaint's group.`);
  out.push("- Ground truth is kept out of the main file, in `_truth/duplicate_truth.csv`.\n");
  out.push(mdTable(["Hotspot", "Sub type", "Ward", "Zone", "Starts", "Complaints", "Rain-driven"],
    gen.hotspots.map((h) => [h.id, h.label, h.ward, h.zone, h.start, h.complaints, h.rain ? "yes" : "no"])));
  out.push("\n## Validation\n");
  if (validation) {
    out.push(mdTable(["#", "Check", "Result", "Detail"], validation.results.map((r) => [r.id, r.name, r.pass ? "PASS" : "**FAIL**", r.detail.replace(/\|/g, "/")])));
  } else {
    out.push("Skipped (`--no-validate`). Run `npm run dataset:validate`.\n");
  }
  out.push("\n## One sample row per status\n");
  for (const r of samples) {
    out.push(`### ${r.Status}${r["Is Synthetic"] === "0" ? " (real complaint)" : ""}\n`);
    out.push("```\n" + csv.line(Object.values(r)).trimEnd() + "\n```\n");
  }
  fs.writeFileSync(path.join(paths.dir, "GENERATION_REPORT.md"), out.join("\n"), "utf8");
}

// ------------------------------------------------------------------ main --

async function unseed(conn) {
  const removed = await deleteSynthetic(conn);
  console.log(`Deleted ${removed} synthetic complaint(s) (history removed by cascade). Real complaints untouched.`);
  const paths = files.datasetPaths(ROOT);
  fs.rmSync(paths.truth, { force: true });
  fs.rmSync(paths.meta, { force: true });
  const { complaints, history } = await exportDataset(conn, { root: ROOT });
  console.log(`Re-exported ${complaints.length} complaint(s) and ${history.length} status change(s) -> ${paths.dir}`);
}

async function main() {
  console.log("Database: " + describeDb());
  const conn = await mysql.createConnection(getDbConfig());
  try {
    if (OPTS.unseed) return await unseed(conn);

    const [[{ n: existing }]] = await conn.query("SELECT COUNT(*) AS n FROM complaints WHERE is_synthetic = 1");
    if (existing > 0 && !OPTS.reset && !OPTS.csvOnly && !OPTS.dryRun) {
      throw new Error(
        existing + " synthetic complaints already exist. Re-run with --reset-synthetic to replace them, " +
        "or `npm run dataset:unseed` to remove them."
      );
    }

    const ref = await loadReference(conn);
    const wardsWithoutAreas = ref.wards.filter((w) => !ref.areasByWard.has(w.ward_number)).length;
    if (ref.areasByWard.size === 0) {
      throw new Error("area_wards is empty. Run `npm run derive:area-wards` first.");
    }
    if (wardsWithoutAreas) console.log(`  note: ${wardsWithoutAreas} ward(s) have no area in area_wards; their streets are always typed`);

    const gen = generate(ref, OPTS);
    printStats(gen);
    if (OPTS.dryRun) {
      console.log("\nDry run: nothing written.");
      return;
    }

    const paths = files.datasetPaths(ROOT);
    fs.mkdirSync(paths.dir, { recursive: true });
    // Rain days first: the exporter reads them for the severity context.
    fs.writeFileSync(paths.meta, JSON.stringify({
      generatedAt: new Date().toISOString(),
      seed: OPTS.seed,
      days: OPTS.days,
      perDay: OPTS.perDay,
      startDate: gen.startDate,
      endDate: gen.endDate,
      snapshotIst: fmtMin(gen.now),
      rainDays: gen.rainDays,
      mode: OPTS.csvOnly ? "csv-only" : "database",
      syntheticComplaints: gen.complaints.length
    }, null, 2) + "\n", "utf8");

    if (OPTS.csvOnly) {
      // Real rows are read (never written) so the files still hold everything. Synthetic rows
      // stored by an earlier database-mode run are left out: this run's set replaces them, and
      // exporting both put two overlapping synthetic histories into the files.
      const all = await fetchDataset(conn);
      const isReal = (r) => Number(r["Is Synthetic"]) === 0;
      const real = { complaints: all.complaints.filter(isReal), history: all.history.filter(isReal) };
      const rows = [...real.complaints, ...gen.complaints.map(exportRow)]
        .sort((a, b) => (a["Filed On"] < b["Filed On"] ? -1 : a["Filed On"] > b["Filed On"] ? 1 : 0));
      intel.enrich(rows, { now: gen.now, rainDays: new Set(gen.rainDays) });
      const hist = [
        ...real.history.map((h) => ({ ...h, _t: intel.parseTs(h["Changed On"]) })),
        ...gen.complaints.flatMap(historyRows)
      ].sort((a, b) => a._t - b._t);
      files.writeCsvAtomic(paths.grievances, intel.GRIEVANCE_COLUMNS, rows);
      files.writeCsvAtomic(paths.history, intel.HISTORY_DATASET_COLUMNS, hist);
      console.log("\nCSV-only: wrote " + rows.length + " complaints (MySQL untouched)");
    } else {
      if (OPTS.reset) {
        const removed = await deleteSynthetic(conn);
        console.log(`\nReset: deleted ${removed} earlier synthetic complaint(s); real complaints untouched`);
      }
      console.log("\nInserting into MySQL (batches of 500, one transaction each)...");
      await insertAll(conn, gen.complaints);
      const { complaints } = await exportDataset(conn, { root: ROOT, now: gen.now });

      // The rows MySQL returns must be exactly the rows the generator built.
      const expected = new Map(gen.complaints.map((c) => [c.code, exportRow(c)]));
      let mismatches = 0;
      for (const r of complaints) {
        const e = expected.get(r["Complaint No"]);
        if (!e) continue;
        for (const col of COMPLAINT_COLUMNS) {
          const a = csv.field(r[col]);
          const b = csv.field(e[col]);
          if (a !== b) {
            if (mismatches++ < 5) console.log(`  mismatch ${r["Complaint No"]} ${col}: db=${a} gen=${b}`);
          }
        }
      }
      console.log(`Round-trip check (MySQL export vs generator): ${mismatches === 0 ? "identical" : mismatches + " field mismatches"}`);
      if (mismatches) process.exitCode = 1;
    }

    // Ground truth for duplicate / hotspot evaluation, outside the main file.
    const truth = gen.complaints.filter((c) => c.truth).map((c) => ({ "Complaint No": c.code, "Truth Group": c.truth, _t: c.t }));
    files.writeCsvAtomic(paths.truth, ["Complaint No", "Truth Group"], truth);

    let validation = null;
    if (OPTS.validate) {
      const { runValidation } = require("./validate-grievance-dataset");
      validation = await runValidation({ root: ROOT, conn, quiet: false });
      if (!validation.ok) process.exitCode = 1;
    }
    writeReport(gen, paths, validation);
    console.log("\nWrote:");
    for (const f of [paths.grievances, paths.history, paths.truth, path.join(paths.dir, "GENERATION_REPORT.md"), paths.meta]) {
      console.log("  " + f);
    }
  } finally {
    await conn.end();
  }
}

if (require.main === module) {
  main().catch((err) => {
    console.error("\nGeneration failed:", err.message);
    process.exit(1);
  });
}

module.exports = { generate, loadReference, OPTS };
