/**
 * npm run validate -- [--days 90] [--mode=csv|mysql|both]
 *
 * Reads the dataset (MySQL when reachable and mode allows, otherwise the CSVs),
 * prints distributions, and runs integrity and realism checks. Exits with code 1
 * if any check fails.
 */
import type { Connection } from 'mysql2/promise';
import { CATEGORIES, CATEGORY_MIX, CROWD_CATEGORIES, NO_RESPONSE_CATEGORIES, SOURCES, STATUSES } from './lib/categories';
import { parseArgs, usesCsv, usesMysql } from './lib/cli';
import { inBounds, loadDistrict, loadEvents } from './lib/config';
import type { Report, StationRow } from './lib/report';
import * as store from './lib/store';
import { DAY, computeWindow, fmt, fmtDate, nowWall, parseWall } from './lib/time';

let failures = 0;
function check(name: string, ok: boolean, detail = '') {
  if (!ok) failures++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
}

function table(title: string, rows: [string, number][], total: number, extra?: (key: string) => string) {
  console.log(`\n${title}`);
  for (const [k, n] of rows) {
    const pct = ((100 * n) / total).toFixed(1).padStart(5);
    console.log(`  ${k.padEnd(30)} ${String(n).padStart(6)}  ${pct}%${extra ? '  ' + extra(k) : ''}`);
  }
}

function countBy<T>(items: T[], key: (t: T) => string, order?: readonly string[]): [string, number][] {
  const m = new Map<string, number>();
  for (const k of order ?? []) m.set(k, 0);
  for (const it of items) m.set(key(it), (m.get(key(it)) ?? 0) + 1);
  return [...m.entries()];
}

const quantile = (xs: number[], q: number) => {
  if (!xs.length) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(q * s.length))];
};

async function main() {
  const args = parseArgs();
  const now = nowWall();
  const nowStr = fmt(now);
  const { startMs } = computeWindow(args.days, now);
  const district = loadDistrict();
  const talukName = new Map(district.taluks.map((t) => [t.code, t.name]));

  // ---- Load --------------------------------------------------------------
  let conn: Connection | null = null;
  if (usesMysql(args.mode)) {
    const res = await store.tryConnect();
    if (res.conn && (await store.tablesExist(res.conn))) conn = res.conn;
    else if (args.mode === 'mysql') throw new Error(`MySQL not available: ${res.error ?? 'police tables missing'}`);
    else console.log(`(MySQL not available: ${res.error ?? 'police tables missing'}; validating CSV files)`);
  }
  let reports: Report[] | null;
  let stations: StationRow[] | null;
  let sourceLabel: string;
  if (conn) {
    reports = await store.dbLoadReports(conn);
    stations = await store.dbLoadStations(conn);
    sourceLabel = `MySQL ${process.env.DB_NAME}`;
  } else {
    reports = store.readReportsCsv();
    stations = store.readStationsCsv();
    sourceLabel = 'CSV files in output/';
  }
  if (!reports || !stations) throw new Error('No dataset found. Run "npm run generate" first.');
  const truth = store.readTruthCsv();

  console.log(`Validating: ${sourceLabel}`);
  console.log(`Window:     ${fmt(startMs)} -> ${nowStr} (${args.days} days)`);
  console.log(`\nTOTAL ROWS: ${reports.length} reports, ${stations.length} stations`);

  // ---- Distributions -------------------------------------------------------
  table('Rows per taluk', countBy(reports, (r) => r.taluk_code, district.taluks.map((t) => t.code)), reports.length, (k) => talukName.get(k) ?? '?');
  table('Rows per category', countBy(reports, (r) => r.category, CATEGORIES), reports.length, (k) => `(target ~${CATEGORY_MIX[k as keyof typeof CATEGORY_MIX]}%)`);
  table('Rows per source', countBy(reports, (r) => r.source, SOURCES), reports.length);
  table('Rows per status', countBy(reports, (r) => r.status, STATUSES), reports.length);

  // ---- Integrity checks ------------------------------------------------------
  console.log('\nIntegrity checks');
  const future = reports.filter((r) => r.incident_datetime > nowStr || r.reported_datetime > nowStr || (r.closed_datetime ?? '') > nowStr);
  check('No future timestamps', future.length === 0, `${future.length} offending rows`);

  const badOrder = reports.filter((r) => r.reported_datetime < r.incident_datetime);
  check('reported_datetime >= incident_datetime', badOrder.length === 0, `${badOrder.length} offending rows`);

  const badClosed = reports.filter((r) =>
    r.status === 'CLOSED' ? !r.closed_datetime || r.closed_datetime <= r.reported_datetime : r.closed_datetime !== null);
  check('closed_datetime after reported_datetime (and only when CLOSED)', badClosed.length === 0, `${badClosed.length} offending rows`);

  const beforeWindow = reports.filter((r) => parseWall(r.incident_datetime) < startMs);
  check('No rows older than window start', beforeWindow.length === 0, `${beforeWindow.length} offending rows`);

  const weeks = Math.ceil(args.days / 7);
  const missing: string[] = [];
  for (let w = 0; w < weeks; w++) {
    const s = startMs + w * 7 * DAY;
    const e = Math.min(s + 7 * DAY, now + 1);
    const present = new Set(reports.filter((r) => { const t = parseWall(r.incident_datetime); return t >= s && t < e; }).map((r) => r.taluk_code));
    for (const t of district.taluks) if (!present.has(t.code)) missing.push(`${t.code}@week${w + 1}(${fmtDate(s)})`);
  }
  check('Every taluk present in every week', missing.length === 0, missing.length ? missing.slice(0, 8).join(', ') : `${district.taluks.length} taluks x ${weeks} weeks`);

  const b = district.bounds;
  const outside = reports.filter((r) => !inBounds(b, r.latitude, r.longitude)).length + stations.filter((s) => !inBounds(b, s.latitude, s.longitude)).length;
  check('Every coordinate within Chennai bounds', outside === 0, `lat ${b.min_lat}-${b.max_lat}, lng ${b.min_lng}-${b.max_lng}; ${outside} outside`);

  const ids = new Set(reports.map((r) => r.report_id));
  check('report_id unique and formatted POL-YYYYMMDD-NNNN', ids.size === reports.length && reports.every((r) => /^POL-\d{8}-\d{4}$/.test(r.report_id)));

  const stationTaluk = new Map(stations.map((s) => [s.station_code, s.taluk_code]));
  check('station_code exists and matches taluk_code', reports.every((r) => stationTaluk.get(r.station_code) === r.taluk_code));
  check('2-4 stations per taluk', district.taluks.every((t) => { const n = stations!.filter((s) => s.taluk_code === t.code).length; return n >= 2 && n <= 4; }));

  const grievance = reports.filter((r) => r.source === 'CITIZEN_GRIEVANCE');
  check('linked_grievance_code only (and always) for CITIZEN_GRIEVANCE, format CMP-YYYYMMDD-XXXX',
    reports.every((r) => (r.source === 'CITIZEN_GRIEVANCE') === (r.linked_grievance_code !== null)) &&
    grievance.every((r) => /^CMP-\d{8}-\d{4}$/.test(r.linked_grievance_code!)));
  const gPct = (100 * grievance.length) / reports.length;
  check('CITIZEN_GRIEVANCE share about 8%', gPct >= 6 && gPct <= 10, `${gPct.toFixed(1)}%`);

  check('response_minutes NULL exactly for cyber and missing-person cases',
    reports.every((r) => NO_RESPONSE_CATEGORIES.includes(r.category) === (r.response_minutes === null)));
  check('blockage_minutes only when road_blocked', reports.every((r) => r.road_blocked ? r.blockage_minutes !== null : r.blockage_minutes === null));
  check('crowd_estimate only for protest / crowd categories', reports.every((r) => r.crowd_estimate === null || CROWD_CATEGORIES.includes(r.category)));
  check('MURDER always has at least 1 fatality', reports.every((r) => r.category !== 'MURDER' || r.fatalities >= 1));
  check('CYBER_FRAUD never blocks a road', reports.every((r) => r.category !== 'CYBER_FRAUD' || !r.road_blocked));
  check('persons_affected >= injured + fatalities', reports.every((r) => r.persons_affected >= r.injured_count + r.fatalities));
  check('title <= 120 and description <= 400 chars', reports.every((r) => r.title.length <= 120 && r.description.length <= 400));

  if (conn && usesCsv(args.mode)) {
    const csv = store.readReportsCsv();
    check('CSV and MySQL row counts match', csv?.length === reports.length, `csv=${csv?.length ?? 'missing'}, mysql=${reports.length}`);
  }
  if (conn) {
    const [rows] = await conn.query(`SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name LIKE '%ground%truth%'`);
    check('Ground truth is NOT loaded into MySQL', Number((rows as { n: number }[])[0].n) === 0);
  }

  // ---- Duplicate ground truth -------------------------------------------------
  console.log('\nDuplicate-report ground truth (output/ground_truth_events.csv)');
  const truthIds = new Set(truth.map((t) => t.report_id));
  check('Ground truth covers exactly the reports in the dataset', truth.length === reports.length && reports.every((r) => truthIds.has(r.report_id)), `${truth.length} truth rows`);
  const perEvent = new Map<string, number>();
  for (const t of truth) if (ids.has(t.report_id)) perEvent.set(t.true_event_id, (perEvent.get(t.true_event_id) ?? 0) + 1);
  const multi = [...perEvent.values()].filter((n) => n > 1).length;
  const multiPct = (100 * multi) / perEvent.size;
  console.log(`  Real-world events: ${perEvent.size} | with 2+ reports: ${multi} (${multiPct.toFixed(1)}%) | reports per event: ${(reports.length / perEvent.size).toFixed(2)}`);
  check('About 20% of events have multiple reports', multiPct >= 15 && multiPct <= 25, `${multiPct.toFixed(1)}%`);

  const eventOf = new Map(truth.map((t) => [t.report_id, t.true_event_id]));
  const groups = new Map<string, Set<string>>();
  for (const r of reports) {
    const key = `${r.category}|${r.locality}|${r.incident_datetime.slice(0, 10)}`;
    if (!groups.has(key)) groups.set(key, new Set());
    groups.get(key)!.add(eventOf.get(r.report_id) ?? r.report_id);
  }
  const nearMiss = [...groups.values()].filter((s) => s.size > 1).length;
  console.log(`  Near-miss groups (same category + locality + day, different true events): ${nearMiss}`);
  check('Near-miss non-duplicates present', nearMiss > 0);

  // ---- Realism summary ------------------------------------------------------------
  console.log('\nStatus by report age');
  const buckets: [string, (age: number) => boolean][] = [
    ['last 7 days', (a) => a < 7 * DAY],
    ['7-30 days', (a) => a >= 7 * DAY && a < 30 * DAY],
    ['older than 30 days', (a) => a >= 30 * DAY],
  ];
  for (const [label, test] of buckets) {
    const rs = reports.filter((r) => test(now - parseWall(r.reported_datetime)));
    const parts = STATUSES.map((s) => `${s} ${((100 * rs.filter((r) => r.status === s).length) / Math.max(1, rs.length)).toFixed(0)}%`);
    console.log(`  ${label.padEnd(20)} n=${String(rs.length).padStart(5)}  ${parts.join('  ')}`);
  }
  const recent = reports.filter((r) => now - parseWall(r.reported_datetime) < 7 * DAY);
  const recentOpen = recent.filter((r) => r.status === 'REPORTED' || r.status === 'UNDER_INVESTIGATION').length / Math.max(1, recent.length);
  const old = reports.filter((r) => now - parseWall(r.reported_datetime) >= 30 * DAY);
  const oldClosed = old.filter((r) => r.status === 'CLOSED').length / Math.max(1, old.length);
  check('Last-7-day rows mostly REPORTED / UNDER_INVESTIGATION', !recent.length || recentOpen > 0.5, recent.length ? `${(100 * recentOpen).toFixed(0)}%` : 'n/a');
  check('Rows older than 30 days mostly CLOSED', !old.length || oldClosed > 0.5, old.length ? `${(100 * oldClosed).toFixed(0)}%` : 'n/a: window is 30 days or shorter');

  const rainDays = new Set(loadEvents().events.filter((e) => e.type === 'HEAVY_RAIN').map((e) => e.date));
  const resp = reports.filter((r) => r.response_minutes !== null);
  const hour = (r: Report) => Number(r.incident_datetime.slice(11, 13));
  const isPeak = (r: Report) => (hour(r) >= 8 && hour(r) < 10) || (hour(r) >= 17 && hour(r) < 21);
  const isRain = (r: Report) => rainDays.has(r.incident_datetime.slice(0, 10));
  const med = (rs: Report[]) => quantile(rs.map((r) => r.response_minutes!), 0.5);
  console.log('\nResponse minutes');
  console.log(`  overall median ${med(resp)} | p90 ${quantile(resp.map((r) => r.response_minutes!), 0.9)} | max ${Math.max(...resp.map((r) => r.response_minutes!))}`);
  console.log(`  peak-hour median ${med(resp.filter(isPeak))} vs off-peak ${med(resp.filter((r) => !isPeak(r)))}`);
  console.log(`  rain-day median ${med(resp.filter(isRain))} vs other days ${med(resp.filter((r) => !isRain(r)))}`);
  const m = med(resp);
  check('Response median around 12 minutes', m >= 9 && m <= 15, `${m} min`);

  const rainRows = reports.filter(isRain);
  const dryRows = reports.filter((r) => !isRain(r));
  const wx = (rs: Report[]) => (100 * rs.filter((r) => r.is_weather_related).length) / Math.max(1, rs.length);
  console.log(`\nWeather-related rows: ${wx(rainRows).toFixed(1)}% on rain days (${rainRows.length} rows) vs ${wx(dryRows).toFixed(1)}% on other days`);
  check('Weather incidents cluster on rain days', rainRows.length === 0 || wx(rainRows) > 3 * wx(dryRows));

  console.log(failures ? `\n${failures} check(s) FAILED` : '\nAll checks passed.');
  await conn?.end();
  process.exit(failures ? 1 : 0);
}

main().catch((e) => {
  console.error(`Validate failed: ${e.message}`);
  process.exit(1);
});
