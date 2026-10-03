/**
 * Persistence: CSV files in output/ and batched MySQL writes.
 * ground_truth_events.csv is file-only and is never written to MySQL.
 */
import fs from 'fs';
import path from 'path';
import mysql from 'mysql2/promise';
import { ROOT, dbSettings, type DistrictConfig } from './config';
import {
  REPORT_COLUMNS, STATION_COLUMNS, normalizeReport, reportValues,
  type Report, type StationRow, type TruthRow,
} from './report';

export const OUTPUT_DIR = path.join(ROOT, 'output');
export const FILES = {
  stations: path.join(OUTPUT_DIR, 'police_stations.csv'),
  reports: path.join(OUTPUT_DIR, 'police_incident_reports.csv'),
  truth: path.join(OUTPUT_DIR, 'ground_truth_events.csv'),
};

// ---------------------------------------------------------------------------
// CSV
// ---------------------------------------------------------------------------
function csvCell(v: unknown): string {
  if (v === null || v === undefined) return '';
  const s = String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function writeCsv(file: string, columns: string[], rows: unknown[][]): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const lines = [columns.join(','), ...rows.map((r) => r.map(csvCell).join(','))];
  fs.writeFileSync(file, lines.join('\n') + '\n', 'utf8');
}

/** RFC-4180 style parser (quoted fields, doubled quotes, embedded commas/newlines). */
export function readCsv(file: string): Record<string, string>[] {
  const text = fs.readFileSync(file, 'utf8');
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') { row.push(cell); cell = ''; }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(cell); cell = '';
      if (row.length > 1 || row[0] !== '') rows.push(row);
      row = [];
    } else cell += ch;
  }
  if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
  const [header, ...body] = rows;
  return body.map((r) => Object.fromEntries(header.map((h, i) => [h, r[i] ?? ''])));
}

export function stationRows(district: DistrictConfig): StationRow[] {
  return district.taluks.flatMap((t) =>
    t.stations.map((s) => ({ station_code: s.code, station_name: s.name, taluk_code: t.code, latitude: s.lat, longitude: s.lng })),
  );
}

const stationValues = (s: StationRow) => [s.station_code, s.station_name, s.taluk_code, s.latitude.toFixed(6), s.longitude.toFixed(6)];

export function writeAllCsv(stations: StationRow[], reports: Report[]): void {
  writeCsv(FILES.stations, STATION_COLUMNS, stations.map(stationValues));
  writeCsv(FILES.reports, REPORT_COLUMNS, reports.map(reportValues));
}

export function writeTruthCsv(rows: TruthRow[]): void {
  writeCsv(FILES.truth, ['report_id', 'true_event_id'], rows.map((r) => [r.report_id, r.true_event_id]));
}

export function readReportsCsv(): Report[] | null {
  return fs.existsSync(FILES.reports) ? readCsv(FILES.reports).map(normalizeReport) : null;
}

export function readStationsCsv(): StationRow[] | null {
  if (!fs.existsSync(FILES.stations)) return null;
  return readCsv(FILES.stations).map((r) => ({
    station_code: r.station_code, station_name: r.station_name, taluk_code: r.taluk_code,
    latitude: Number(r.latitude), longitude: Number(r.longitude),
  }));
}

export function readTruthCsv(): TruthRow[] {
  return fs.existsSync(FILES.truth) ? (readCsv(FILES.truth) as unknown as TruthRow[]) : [];
}

// ---------------------------------------------------------------------------
// MySQL
// ---------------------------------------------------------------------------
export async function connect(opts: { withDatabase?: boolean; multipleStatements?: boolean } = {}) {
  const s = dbSettings();
  return mysql.createConnection({
    host: s.host, port: s.port, user: s.user, password: s.password,
    database: opts.withDatabase === false ? undefined : s.database,
    multipleStatements: !!opts.multipleStatements,
    dateStrings: true, // DATETIME comes back as the stored IST wall-clock string
  });
}

/** Connect, or return null (with the reason) when MySQL is unreachable. */
export async function tryConnect(): Promise<{ conn: mysql.Connection | null; error?: string }> {
  try {
    return { conn: await connect() };
  } catch (e) {
    return { conn: null, error: (e as Error).message };
  }
}

export async function tablesExist(conn: mysql.Connection): Promise<boolean> {
  const [rows] = await conn.query(
    `SELECT COUNT(*) AS n FROM information_schema.tables
     WHERE table_schema = DATABASE() AND table_name IN ('police_stations','police_incident_reports')`,
  );
  return Number((rows as { n: number }[])[0].n) === 2;
}

async function insertBatched(conn: mysql.Connection, table: string, columns: string[], rows: unknown[][], batchSize = 500) {
  for (let i = 0; i < rows.length; i += batchSize) {
    await conn.query(`INSERT INTO ${table} (${columns.join(',')}) VALUES ?`, [rows.slice(i, i + batchSize)]);
  }
}

async function upsertStations(conn: mysql.Connection, stations: StationRow[]) {
  await conn.query(
    `INSERT INTO police_stations (${STATION_COLUMNS.join(',')}) VALUES ?
     ON DUPLICATE KEY UPDATE station_name = VALUES(station_name), taluk_code = VALUES(taluk_code),
       latitude = VALUES(latitude), longitude = VALUES(longitude)`,
    [stations.map(stationValues)],
  );
}

/** Full rebuild: replace all police rows in one transaction. */
export async function dbReplaceAll(conn: mysql.Connection, stations: StationRow[], reports: Report[]) {
  await conn.beginTransaction();
  try {
    await conn.query('DELETE FROM police_incident_reports');
    await conn.query('DELETE FROM police_stations');
    await insertBatched(conn, 'police_stations', STATION_COLUMNS, stations.map(stationValues));
    await insertBatched(conn, 'police_incident_reports', REPORT_COLUMNS, reports.map(reportValues));
    await conn.commit();
  } catch (e) {
    await conn.rollback();
    throw e;
  }
}

/** Rolling refresh: drop rows before the window, advance statuses, add new rows. */
export async function dbRefresh(
  conn: mysql.Connection, stations: StationRow[], windowStart: string, statusChanges: Report[], inserts: Report[],
): Promise<number> {
  await conn.beginTransaction();
  try {
    const [del] = await conn.query('DELETE FROM police_incident_reports WHERE incident_datetime < ?', [windowStart]);
    await upsertStations(conn, stations);
    for (const r of statusChanges) {
      await conn.query('UPDATE police_incident_reports SET status = ?, closed_datetime = ? WHERE report_id = ?', [r.status, r.closed_datetime, r.report_id]);
    }
    await insertBatched(conn, 'police_incident_reports', REPORT_COLUMNS, inserts.map(reportValues));
    await conn.commit();
    return (del as mysql.ResultSetHeader).affectedRows;
  } catch (e) {
    await conn.rollback();
    throw e;
  }
}

export async function dbLoadReports(conn: mysql.Connection): Promise<Report[]> {
  const [rows] = await conn.query(`SELECT ${REPORT_COLUMNS.join(',')} FROM police_incident_reports`);
  return (rows as Record<string, unknown>[]).map(normalizeReport);
}

export async function dbLoadStations(conn: mysql.Connection): Promise<StationRow[]> {
  const [rows] = await conn.query(`SELECT ${STATION_COLUMNS.join(',')} FROM police_stations`);
  return (rows as Record<string, unknown>[]).map((r) => ({
    station_code: String(r.station_code), station_name: String(r.station_name), taluk_code: String(r.taluk_code),
    latitude: Number(r.latitude), longitude: Number(r.longitude),
  }));
}
