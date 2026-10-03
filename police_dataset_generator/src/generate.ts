/**
 * npm run generate -- [--days 90] [--per-day 50] [--seed 42] [--mode=csv|mysql|both] [--refresh]
 *
 * Full build: generates every day of the rolling window and replaces all police rows.
 * --refresh:  deletes rows older than the window start, advances the status of
 *             open cases, and appends only reports that appeared since the last run.
 */
import type { Connection } from 'mysql2/promise';
import { parseArgs, usesCsv, usesMysql, type CliArgs } from './lib/cli';
import { loadDistrict, loadEvents } from './lib/config';
import { DayGenerator, type DraftReport } from './lib/generator';
import { lifecycle } from './lib/lifecycle';
import type { Report, StationRow, TruthRow } from './lib/report';
import * as store from './lib/store';
import { DAY, compactDate, computeWindow, fmt, nowWall, parseWall, startOfDay } from './lib/time';

interface Collected {
  drafts: DraftReport[];
  events: number;
  multiReportEvents: number;
  nearMissEvents: number;
}

/** Generate days [fromDay, toDay] and keep reports that exist by `now` (and after `afterMs`). */
function collect(gen: DayGenerator, fromDay: number, toDay: number, windowStart: number, now: number, afterMs = -Infinity): Collected {
  const out: Collected = { drafts: [], events: 0, multiReportEvents: 0, nearMissEvents: 0 };
  for (let day = fromDay; day <= toDay; day += DAY) {
    for (const ev of gen.generate(day)) {
      const kept = ev.reports
        .filter((r) => r.reportedMs <= now && r.reportedMs > afterMs)
        .map((r) => ({ ...r, incidentMs: Math.max(r.incidentMs, windowStart) }));
      if (!kept.length) continue;
      out.events++;
      if (ev.reports.filter((r) => r.reportedMs <= now).length > 1) out.multiReportEvents++;
      if (ev.nearMiss) out.nearMissEvents++;
      out.drafts.push(...kept);
    }
  }
  return out;
}

/** Assign POL-YYYYMMDD-NNNN ids (by reported date, continuing existing sequences) and statuses. */
function finalize(drafts: DraftReport[], lastSeq: Map<string, number>, now: number): { reports: Report[]; truth: TruthRow[] } {
  const sorted = [...drafts].sort((a, b) => a.reportedMs - b.reportedMs || a.eventId.localeCompare(b.eventId));
  const reports: Report[] = [];
  const truth: TruthRow[] = [];
  for (const d of sorted) {
    const date = compactDate(d.reportedMs);
    const seq = (lastSeq.get(date) ?? 0) + 1;
    if (seq > 9999) throw new Error(`More than 9999 reports on ${date}; lower --per-day`);
    lastSeq.set(date, seq);
    const report_id = `POL-${date}-${String(seq).padStart(4, '0')}`;
    const { eventId, incidentMs, reportedMs, ...fields } = d;
    reports.push({
      report_id,
      incident_datetime: fmt(incidentMs),
      reported_datetime: fmt(reportedMs),
      ...fields,
      ...lifecycle(report_id, d.category, reportedMs, now),
    } as Report);
    truth.push({ report_id, true_event_id: eventId });
  }
  return { reports, truth };
}

const byIncident = (a: Report, b: Report) => a.incident_datetime.localeCompare(b.incident_datetime) || a.report_id.localeCompare(b.report_id);

function printSample(reports: Report[]) {
  console.log('\nSample (first 10 rows of police_incident_reports.csv):');
  console.table(reports.slice(0, 10).map((r) => ({
    report_id: r.report_id,
    incident: r.incident_datetime,
    taluk: r.taluk_code,
    locality: r.locality,
    category: r.category,
    title: r.title.length > 48 ? r.title.slice(0, 47) + '…' : r.title,
    source: r.source,
    status: r.status,
  })));
}

async function fullBuild(args: CliArgs, gen: DayGenerator, stations: StationRow[], conn: Connection | null, startMs: number, now: number) {
  const c = collect(gen, startMs, startOfDay(now), startMs, now);
  const { reports, truth } = finalize(c.drafts, new Map(), now);
  reports.sort(byIncident);

  console.log(`Events: ${c.events} | Reports: ${reports.length} | Events with 2+ reports: ${c.multiReportEvents} (${((100 * c.multiReportEvents) / c.events).toFixed(1)}%) | Near-miss look-alike events: ${c.nearMissEvents}`);

  store.writeTruthCsv(truth);
  console.log(`Wrote ${store.FILES.truth} (${truth.length} rows, file only, never loaded into MySQL)`);
  if (usesCsv(args.mode)) {
    store.writeAllCsv(stations, reports);
    console.log(`Wrote ${store.FILES.stations} (${stations.length} rows)`);
    console.log(`Wrote ${store.FILES.reports} (${reports.length} rows)`);
  }
  if (conn) {
    await store.dbReplaceAll(conn, stations, reports);
    console.log(`MySQL: replaced police_stations (${stations.length}) and police_incident_reports (${reports.length}) in batches of 500`);
  }
  printSample(reports);
}

async function refresh(args: CliArgs, gen: DayGenerator, stations: StationRow[], conn: Connection | null, existing: Report[], startMs: number, now: number) {
  // 1. Drop rows that fell out of the window.
  const kept = existing.filter((r) => parseWall(r.incident_datetime) >= startMs);
  const removed = existing.length - kept.length;

  // 2. Advance the status of open cases (milestones are deterministic per report_id).
  const statusChanges: Report[] = [];
  for (const r of kept) {
    if (r.status === 'CLOSED') continue;
    const lc = lifecycle(r.report_id, r.category, parseWall(r.reported_datetime), now);
    if (lc.status !== r.status) {
      r.status = lc.status;
      r.closed_datetime = lc.closed_datetime;
      statusChanges.push(r);
    }
  }

  // 3. Generate only reports that appeared after the newest existing report.
  const lastReported = kept.reduce((m, r) => Math.max(m, parseWall(r.reported_datetime)), startMs - 1);
  const fromDay = Math.max(startMs, startOfDay(lastReported) - 8 * DAY); // reports can lag an incident by up to 7 days
  const c = collect(gen, fromDay, startOfDay(now), startMs, now, lastReported);

  const lastSeq = new Map<string, number>();
  for (const r of kept) {
    const [, date, seq] = r.report_id.split('-');
    lastSeq.set(date, Math.max(lastSeq.get(date) ?? 0, Number(seq)));
  }
  const fresh = finalize(c.drafts, lastSeq, now);

  const keptIds = new Set(kept.map((r) => r.report_id));
  const truth = [...store.readTruthCsv().filter((t) => keptIds.has(t.report_id)), ...fresh.truth];
  const merged = [...kept, ...fresh.reports].sort(byIncident);

  console.log(`Refresh: removed ${removed} rows older than ${fmt(startMs)}, advanced ${statusChanges.length} statuses, added ${fresh.reports.length} new reports (since ${fmt(lastReported)})`);

  store.writeTruthCsv(truth);
  if (usesCsv(args.mode)) {
    store.writeAllCsv(stations, merged);
    console.log(`Wrote ${store.FILES.reports} (${merged.length} rows)`);
  }
  if (conn) {
    const deleted = await store.dbRefresh(conn, stations, fmt(startMs), statusChanges, fresh.reports);
    console.log(`MySQL: deleted ${deleted}, updated ${statusChanges.length}, inserted ${fresh.reports.length}`);
  }
  printSample(merged);
}

async function main() {
  const args = parseArgs();
  const now = nowWall();
  const { startMs } = computeWindow(args.days, now);
  const district = loadDistrict();
  const gen = new DayGenerator(args.seed, args.perDay, district, loadEvents());
  const stations = store.stationRows(district);

  console.log(`Window: ${fmt(startMs)} -> ${fmt(now)} (${args.days} days, ${process.env.TZ || 'Asia/Kolkata'})`);
  console.log(`Seed: ${args.seed} | Events/day: ${args.perDay} ±25% | Mode: ${args.mode}${args.refresh ? ' | refresh' : ''}`);

  let conn: Connection | null = null;
  if (usesMysql(args.mode)) {
    const res = await store.tryConnect();
    if (!res.conn) throw new Error(`Cannot connect to MySQL (${res.error}). Check .env, or run with --mode=csv.`);
    conn = res.conn;
    if (!(await store.tablesExist(conn))) throw new Error('Police tables are missing. Run "npm run setup" first.');
  }

  try {
    if (args.refresh) {
      const existing = (usesCsv(args.mode) ? store.readReportsCsv() : null) ?? (conn ? await store.dbLoadReports(conn) : null);
      if (existing?.length) {
        await refresh(args, gen, stations, conn, existing, startMs, now);
        return;
      }
      console.log('Refresh: no existing data found, doing a full build instead.');
    }
    await fullBuild(args, gen, stations, conn, startMs, now);
  } finally {
    await conn?.end();
  }
}

main().catch((e) => {
  console.error(`Generate failed: ${e.message}`);
  process.exit(1);
});
