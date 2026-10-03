/**
 * Minimal RFC-4180 CSV writer and reader, so the dataset needs no extra
 * dependency.
 *
 *   - fields containing a comma, double quote, CR or LF are quoted, and quotes
 *     inside them are doubled
 *   - NULL / undefined is written as an empty field
 *   - records end in CRLF
 *   - files start with a UTF-8 byte-order mark so Excel reads Tamil correctly
 */

const BOM = "﻿";
const EOL = "\r\n";

function field(value) {
  if (value === null || value === undefined) return "";
  const s = typeof value === "string" ? value : String(value);
  return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

/** One CSV record, including its line ending. */
function line(values) {
  return values.map(field).join(",") + EOL;
}

/** One record from an object, in the given column order. */
function lineFromRow(row, columns) {
  return line(columns.map((c) => row[c]));
}

/**
 * Parses CSV text (with or without a BOM) into an array of records, each an
 * array of strings. Empty fields come back as "".
 */
function parse(text) {
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  const records = [];
  let rec = [];
  let cur = "";
  let i = 0;
  let quoted = false;
  const n = text.length;
  while (i < n) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cur += '"';
          i += 2;
          continue;
        }
        quoted = false;
        i++;
        continue;
      }
      cur += ch;
      i++;
      continue;
    }
    if (ch === '"' && cur === "") {
      quoted = true;
      i++;
    } else if (ch === ",") {
      rec.push(cur);
      cur = "";
      i++;
    } else if (ch === "\r" || ch === "\n") {
      rec.push(cur);
      records.push(rec);
      rec = [];
      cur = "";
      i += ch === "\r" && text[i + 1] === "\n" ? 2 : 1;
    } else {
      cur += ch;
      i++;
    }
  }
  if (cur !== "" || rec.length) {
    rec.push(cur);
    records.push(rec);
  }
  return records;
}

/** Parses into { header, rows } where each row is an object keyed by header. */
function parseObjects(text) {
  const records = parse(text);
  const header = records.shift() || [];
  const rows = records.map((r) => {
    const o = {};
    header.forEach((h, i) => (o[h] = r[i] === undefined ? "" : r[i]));
    return o;
  });
  return { header, rows };
}

module.exports = { BOM, EOL, field, line, lineFromRow, parse, parseObjects };
