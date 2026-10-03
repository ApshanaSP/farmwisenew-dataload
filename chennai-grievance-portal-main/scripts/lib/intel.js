/**
 * Rule-based complaint intelligence: language, severity / priority, duplicate
 * grouping and ward clustering. Pure functions, no I/O.
 *
 * Used by the dataset exporter and generator (CommonJS) and by the live
 * appender through src/lib/dataset/intel.ts, so a row gets the same extension
 * columns whichever path wrote it.
 *
 * Rows are objects keyed by the dataset column names (see dataset-sql.js), as
 * returned by the shared SELECT or parsed from grievances.csv.
 */
const { COMPLAINT_COLUMNS, HISTORY_COLUMNS } = require("./dataset-sql");

/** Columns 32-38 of grievances.csv. */
const EXTENSION_COLUMNS = [
  "Is Synthetic",
  "Language",
  "Severity Score",
  "Priority",
  "Priority Reasons",
  "Duplicate Group",
  "Ward Cluster 72h"
];
const GRIEVANCE_COLUMNS = [...COMPLAINT_COLUMNS, ...EXTENSION_COLUMNS];
const HISTORY_DATASET_COLUMNS = [...HISTORY_COLUMNS, "Is Synthetic"];

const HOUR = 3600 * 1000;
const DAY = 24 * HOUR;
const DUPLICATE_WINDOW_MS = 72 * HOUR;
const DUPLICATE_RADIUS_M = 150;

/** Score -> priority. Inclusive lower bounds. */
const PRIORITY_BANDS = [
  { min: 70, priority: "Critical" },
  { min: 50, priority: "High" },
  { min: 30, priority: "Medium" },
  { min: 0, priority: "Low" }
];

/**
 * Median working days from approval to completion, by department. MEGA
 * STREETS categories are routed to Engineering but run on project timelines,
 * so they get their own figure.
 */
const WORK_MEDIAN_DAYS = {
  "Solid Waste Management Department": 1,
  "Electrical Department": 2,
  "Health Department": 3,
  "Storm Water Drain Department": 5,
  "Parks & Play Fields Department": 5,
  "Engineering Department (Town Planning & Building Permissions)": 7,
  "Revenue Department": 7,
  "General Administration": 7,
  "Bridges Department": 10,
  "Education Department": 5,
  "Family Welfare Department": 4,
  "Buildings Department": 7,
  "Mechanical Engineering Department": 5,
  "Land & Estate Department": 10,
  "Financial Management Unit": 7,
  "Council Department": 7
};
const MEGA_STREETS_WORK_DAYS = 14;

function isMegaStreets(category) {
  return typeof category === "string" && category.startsWith("MEGA STREETS");
}

function workMedianDays(department, category) {
  if (isMegaStreets(category)) return MEGA_STREETS_WORK_DAYS;
  return WORK_MEDIAN_DAYS[department] || 7;
}

/** Rain-sensitive: waterlogging categories, fallen / obstructing trees, street lights. */
const RAIN_CATEGORIES = new Set(["Water Stagnation", "Storm Water Drains", "Flood"]);
const RAIN_TREE_SUBTYPES = new Set(["Removal of Fallen Trees", "Obstruction of Trees", "Tree Pruning"]);
function isRainSensitive(category, subType) {
  return RAIN_CATEGORIES.has(category) || category === "Street Light" || RAIN_TREE_SUBTYPES.has(subType);
}

// ---------------------------------------------------------------- time --

/** "YYYY-MM-DD HH:mm[:ss]" (IST wall clock) -> ms on a UTC-based scale. */
function parseTs(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/.exec(s || "");
  if (!m) return NaN;
  return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
}

/** The current IST wall-clock time on the same scale as parseTs(). */
function nowIst() {
  return Date.now() + 5.5 * HOUR;
}

function dateOf(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

// ------------------------------------------------------------ language --

const TAMIL_CHAR = /[஀-௿]/g;
const LATIN_CHAR = /[A-Za-z]/g;
// Romanised Tamil function words and endings that do not occur in English.
const TANGLISH_MARKERS =
  /\b(irukku|iruku|illa|illai|pannunga|pannanum|pannom|romba|rombo|jaasthi|mudiyala|mudiyadhu|eduklala|edukala|varudhu|varala|pogudhu|poga|paarunga|seekiram|konjam|thadava|nikkudhu|thengi|eriyala|pasanga|periyavanga|kashtapadranga|bayama|bayapadranga|nadakka|pakkathula|kitta|aagudhu|aayiduchu|yaarum|sollunga|edunga|panna|kosu|mazhai|thanni|thollai|vaaram|naala)\b/gi;

/** "en", "ta" or "tanglish" (Tamil written in Latin script). */
function languageOf(text) {
  const s = text || "";
  const tamil = (s.match(TAMIL_CHAR) || []).length;
  const latin = (s.match(LATIN_CHAR) || []).length;
  if (tamil > 0 && tamil >= latin * 0.5) return "ta";
  const markers = (s.match(TANGLISH_MARKERS) || []).length;
  if (markers >= 2 || (markers === 1 && latin < 60)) return "tanglish";
  return "en";
}

// ------------------------------------------------------------ severity --

const HIGH_RISK =
  /electric shock|eb wire|spark|manhole|sewage|fallen tree|dengue|malaria|mosquito|biomedical|trap at home|water entering|elderly medicine|pregnant women issues|damage to the electric pole/i;
const ELEVATED_RISK =
  /stagnation of water|pot ?hole|street dogs|non burning of street lights|overflowing|burning of garbage|dark spot|safety|barricading|obstruction of water flow|desilting|removal of garbage|open defecation|electrical wires|stray cattle/i;
const LOW_RISK =
  /certificate|registration|opening and closing hours|online payment|project information|consultation|information in advance|revision objection|name error/i;
const LOW_RISK_CATEGORIES = new Set(["Tax and Licence", "Voter ID"]);

// Hazards a citizen describes that the sub type alone does not reveal.
const TEXT_HAZARD =
  /live wire|electric shock|current shock|shock adikkudhu|open manhole|sewage overflow|மின்சாரம் தாக்|மின் கம்பி|திறந்த நிலையில் உள்ள/i;

const VULNERABLE_PLACES = [
  { re: /school|பள்ளி|anganwadi|அங்கன்வாடி/i, label: "a school or anganwadi" },
  { re: /hospital|மருத்துவமனை|health cent|\bphc\b|\buphc\b|சுகாதார நிலைய/i, label: "a hospital or health centre" },
  { re: /bus stop|bus stand|பேருந்து நிறுத்த/i, label: "a bus stop" },
  { re: /temple|kovil|koil|கோயில்|கோவில்|church|mosque|masjid|தேவாலய|பள்ளிவாசல்/i, label: "a place of worship" },
  { re: /children|kids|pasanga|kozhandhai|குழந்தை|students|மாணவ/i, label: "children" },
  { re: /elderly|old age|senior citizen|periyavanga|முதியோர்|முதியவர்|வயதான/i, label: "elderly people" },
  { re: /pregnant|கர்ப்பிணி/i, label: "pregnant women" }
];

const REPEAT_PHRASES =
  /already complained|already complaint|complained (twice|thrice|again|many times|\d+ times)|no action (taken|so far|till now)|still not (done|cleared|fixed|repaired|attended)|reminder|repeated complaint|thadava complaint|evlo thadava|பலமுறை|ஏற்கனவே[^.]*புகார்|மீண்டும் புகார்|நடவடிக்கை இல்லை/i;

function priorityOf(score) {
  return PRIORITY_BANDS.find((b) => score >= b.min).priority;
}

/**
 * Severity score (0-100), priority and the reasons behind it, worded for the
 * Collector.
 *
 * context:
 *   clusterCount  complaints of this sub type in this ward in the 72 h up to
 *                 and including this one
 *   rainDays      Set of "YYYY-MM-DD" rain-event days (the event day and the
 *                 two after it count as affected)
 *   now           ms on the parseTs() scale; days open is measured to here
 */
function severity(row, context = {}) {
  const reasons = [];
  let score = 0;
  const add = (points, reason) => {
    score += points;
    reasons.push(reason + " (+" + points + ")");
  };

  const sub = row["Complaint Sub Type"] || "";
  const cat = row["Complaint Type"] || "";
  const text = (row["Title"] || "") + " " + (row["Details"] || "");
  const place = text + " " + (row["Landmark"] || "");

  if (cat === "Flood" || HIGH_RISK.test(sub)) add(35, "High-risk issue: " + sub);
  else if (LOW_RISK_CATEGORIES.has(cat) || LOW_RISK.test(sub)) add(5, "Administrative issue: " + sub);
  else if (ELEVATED_RISK.test(sub)) add(25, "Public-safety or sanitation issue: " + sub);
  else add(15, "Civic issue: " + (sub || cat));

  if (TEXT_HAZARD.test(text)) add(15, "Complaint describes an immediate hazard");

  const places = VULNERABLE_PLACES.filter((p) => p.re.test(place)).map((p) => p.label);
  if (places.length) {
    add(Math.min(places.length * 8, 24), "Affects " + places.join(", "));
  }

  const others = Math.max((context.clusterCount || 1) - 1, 0);
  if (others > 0) {
    add(
      Math.min(others * 5, 20),
      others + " other '" + sub + "' complaint" + (others === 1 ? "" : "s") +
        " in Ward " + row["Ward"] + " in the last 72 h"
    );
  }

  const filed = parseTs(row["Filed On"]);
  if (context.rainDays && context.rainDays.size && isRainSensitive(cat, sub) && !isNaN(filed)) {
    for (let back = 0; back <= 2; back++) {
      const d = dateOf(filed - back * DAY);
      if (context.rainDays.has(d)) {
        add(10, "Filed during the rain event of " + d);
        break;
      }
    }
  }

  const open = row["Status"] !== "Verified by Collector" && row["Status"] !== "Rejected";
  if (open && context.now && !isNaN(filed)) {
    const days = (context.now - filed) / DAY;
    const target = workMedianDays(row["Department"], cat) + 2;
    if (days > 2 * target) {
      add(20, "Open " + Math.floor(days) + " days, over twice the " + target + "-day target");
    } else if (days > target) {
      add(10, "Open " + Math.floor(days) + " days, past the " + target + "-day target");
    }
  }

  if (REPEAT_PHRASES.test(text)) add(10, "Citizen reports complaining before");

  score = Math.min(score, 100);
  return { score, priority: priorityOf(score), reasons };
}

// ---------------------------------------------------------- duplicates --

function coords(row) {
  const lat = parseFloat(row["Latitude"]);
  const lng = parseFloat(row["Longitude"]);
  return isNaN(lat) || isNaN(lng) ? null : { lat, lng };
}

function haversineM(lat1, lng1, lat2, lng2) {
  const R = 6371000;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a =
    Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

const norm = (s) => String(s || "").trim().toLowerCase().replace(/\s+/g, " ");

function sameIssue(a, b) {
  return (
    a["Complaint Type"] === b["Complaint Type"] &&
    a["Complaint Sub Type"] === b["Complaint Sub Type"] &&
    a["Department"] === b["Department"]
  );
}

function near(a, b) {
  const ca = coords(a);
  const cb = coords(b);
  if (ca && cb) return haversineM(ca.lat, ca.lng, cb.lat, cb.lng) <= DUPLICATE_RADIUS_M;
  // Without a map pin on both, fall back to the same street in the same ward.
  return String(a["Ward"]) === String(b["Ward"]) && norm(a["Street"]) !== "" && norm(a["Street"]) === norm(b["Street"]);
}

/**
 * Duplicate group for `row`: the group of the earliest earlier complaint with
 * the same sub type, within 150 m (or the same street and ward when either has
 * no map pin) and filed within the previous 72 h. A complaint that matches
 * nothing starts its own group, named after its own Complaint No, so groups
 * are stable whether rows arrive one at a time or in a batch.
 *
 * recentRows must carry "Duplicate Group" already.
 */
function duplicateGroup(row, recentRows) {
  const t = parseTs(row["Filed On"]);
  let best = null;
  let bestT = Infinity;
  for (const r of recentRows) {
    if (r["Complaint No"] === row["Complaint No"]) continue;
    const rt = parseTs(r["Filed On"]);
    if (rt > t || t - rt > DUPLICATE_WINDOW_MS) continue;
    if (!sameIssue(row, r) || !near(row, r)) continue;
    if (rt < bestT) {
      best = r;
      bestT = rt;
    }
  }
  return best ? best["Duplicate Group"] || best["Complaint No"] : row["Complaint No"];
}

/**
 * Fills columns 33-38 on every row, in place. Rows must be sorted by Filed On
 * ascending. Returns the same array.
 *
 * options: { now, rainDays }
 */
function enrich(rows, options = {}) {
  const now = options.now || nowIst();
  const rainDays = options.rainDays || new Set();
  const recentBySubtype = new Map();

  for (const row of rows) {
    const key = row["Complaint Type"] + "|" + row["Complaint Sub Type"];
    const t = parseTs(row["Filed On"]);
    let recent = recentBySubtype.get(key);
    if (!recent) recentBySubtype.set(key, (recent = []));
    while (recent.length && t - parseTs(recent[0]["Filed On"]) > DUPLICATE_WINDOW_MS) recent.shift();

    const ward = String(row["Ward"]);
    const clusterCount = 1 + recent.filter((r) => String(r["Ward"]) === ward).length;

    row["Duplicate Group"] = duplicateGroup(row, recent);
    row["Ward Cluster 72h"] = clusterCount;
    row["Language"] = languageOf(row["Details"]);
    const sev = severity(row, { clusterCount, rainDays, now });
    row["Severity Score"] = sev.score;
    row["Priority"] = sev.priority;
    row["Priority Reasons"] = sev.reasons.join("; ");

    recent.push(row);
  }
  return rows;
}

module.exports = {
  EXTENSION_COLUMNS,
  GRIEVANCE_COLUMNS,
  HISTORY_DATASET_COLUMNS,
  PRIORITY_BANDS,
  WORK_MEDIAN_DAYS,
  MEGA_STREETS_WORK_DAYS,
  DUPLICATE_WINDOW_MS,
  DUPLICATE_RADIUS_M,
  workMedianDays,
  isRainSensitive,
  isMegaStreets,
  parseTs,
  nowIst,
  dateOf,
  languageOf,
  severity,
  priorityOf,
  duplicateGroup,
  enrich,
  haversineM
};
