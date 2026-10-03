/**
 * The keyword department classifier, read straight out of
 * src/lib/ai-classifier.ts rather than copied, so the generator and the
 * validator route "Other" complaints exactly as the complaints API does when
 * OpenAI is not configured. Nothing here ever calls OpenAI.
 */
const fs = require("fs");
const path = require("path");

const SRC = path.join(__dirname, "..", "..", "src", "lib");

let cached = null;

function load() {
  if (cached) return cached;
  const classifier = fs.readFileSync(path.join(SRC, "ai-classifier.ts"), "utf8");
  const m = classifier.match(/const KEYWORD_MAP[^=]*=\s*(\[[\s\S]*?\n\]);/);
  if (!m) throw new Error("Could not find KEYWORD_MAP in src/lib/ai-classifier.ts");
  // The literal is plain JSON-compatible JS (strings, arrays, objects).
  const keywordMap = new Function("return " + m[1])();

  const constants = fs.readFileSync(path.join(SRC, "constants.ts"), "utf8");
  const f = constants.match(/DEFAULT_FALLBACK_DEPARTMENT\s*=\s*"([^"]+)"/);
  if (!f) throw new Error("Could not find DEFAULT_FALLBACK_DEPARTMENT in src/lib/constants.ts");

  cached = { keywordMap, fallback: f[1] };
  return cached;
}

/** keywordClassify() from ai-classifier.ts: the first entry with a matching keyword. */
function keywordClassify(text) {
  const lower = text.toLowerCase();
  for (const entry of load().keywordMap) {
    if (entry.keywords.some((kw) => lower.includes(kw))) return entry.department;
  }
  return null;
}

/**
 * classifyComplaint() without the OpenAI step. The API classifies
 * [otherDescription, title, description].filter(Boolean).join(". ").
 */
function classifyOther(text) {
  return keywordClassify(text) || load().fallback;
}

module.exports = { keywordClassify, classifyOther, fallbackDepartment: () => load().fallback };
