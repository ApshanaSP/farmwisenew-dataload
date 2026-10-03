/**
 * The police_incident_reports row shape, column order, and conversions to and
 * from CSV / MySQL values.
 */
import type { Category, Source, Status } from './categories';

export interface Report {
  report_id: string;
  incident_datetime: string;
  reported_datetime: string;
  station_code: string;
  taluk_code: string;
  locality: string;
  latitude: number;
  longitude: number;
  category: Category;
  title: string;
  description: string;
  persons_affected: number;
  injured_count: number;
  fatalities: number;
  vulnerable_victim: boolean;
  weapon_involved: boolean;
  road_blocked: boolean;
  blockage_minutes: number | null;
  crowd_estimate: number | null;
  is_weather_related: boolean;
  source: Source;
  linked_grievance_code: string | null;
  status: Status;
  response_minutes: number | null;
  closed_datetime: string | null;
}

export interface StationRow {
  station_code: string;
  station_name: string;
  taluk_code: string;
  latitude: number;
  longitude: number;
}

export interface TruthRow { report_id: string; true_event_id: string }

export const REPORT_COLUMNS: (keyof Report)[] = [
  'report_id', 'incident_datetime', 'reported_datetime', 'station_code', 'taluk_code', 'locality',
  'latitude', 'longitude', 'category', 'title', 'description', 'persons_affected', 'injured_count',
  'fatalities', 'vulnerable_victim', 'weapon_involved', 'road_blocked', 'blockage_minutes',
  'crowd_estimate', 'is_weather_related', 'source', 'linked_grievance_code', 'status',
  'response_minutes', 'closed_datetime',
];
export const STATION_COLUMNS: (keyof StationRow)[] = ['station_code', 'station_name', 'taluk_code', 'latitude', 'longitude'];

const BOOL_COLUMNS = new Set<keyof Report>(['vulnerable_victim', 'weapon_involved', 'road_blocked', 'is_weather_related']);
const INT_COLUMNS = new Set<keyof Report>(['persons_affected', 'injured_count', 'fatalities', 'blockage_minutes', 'crowd_estimate', 'response_minutes']);
const NULLABLE = new Set<keyof Report>(['blockage_minutes', 'crowd_estimate', 'linked_grievance_code', 'response_minutes', 'closed_datetime']);

/** Row values for MySQL / CSV: booleans as 1/0, coordinates fixed to 6 decimals. */
export function reportValues(r: Report): (string | number | null)[] {
  return REPORT_COLUMNS.map((c) => {
    const v = r[c];
    if (typeof v === 'boolean') return v ? 1 : 0;
    if (c === 'latitude' || c === 'longitude') return (v as number).toFixed(6);
    return v as string | number | null;
  });
}

/** Normalise a row read from CSV (all strings) or MySQL (mixed types) into a Report. */
export function normalizeReport(raw: Record<string, unknown>): Report {
  const out: Record<string, unknown> = {};
  for (const c of REPORT_COLUMNS) {
    const v = raw[c];
    const empty = v === null || v === undefined || v === '';
    if (empty && NULLABLE.has(c)) out[c] = null;
    else if (BOOL_COLUMNS.has(c)) out[c] = v === true || v === 1 || v === '1' || v === 'true';
    else if (INT_COLUMNS.has(c)) out[c] = Number(v);
    else if (c === 'latitude' || c === 'longitude') out[c] = Number(v);
    else out[c] = String(v);
  }
  return out as unknown as Report;
}
