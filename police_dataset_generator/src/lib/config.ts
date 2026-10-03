/**
 * Loads .env, config/taluks.json and config/events.json, and sanity-checks the
 * taluk master so a bad edit fails loudly instead of producing bad data.
 */
import fs from 'fs';
import path from 'path';
import dotenv from 'dotenv';
import type { Category } from './categories';

export const ROOT = path.resolve(__dirname, '..', '..');
dotenv.config({ path: path.join(ROOT, '.env'), quiet: true });

export interface Locality { name: string; lat: number; lng: number; landmarks?: string[] }
export interface Station { code: string; name: string; lat: number; lng: number }
export interface Taluk {
  code: string;
  name: string;
  centroid: { lat: number; lng: number };
  weight?: number;
  heavy_vehicle_share?: number;
  hotspots?: Partial<Record<Category, number>>;
  localities: Locality[];
  stations: Station[];
}
export interface Bounds { min_lat: number; max_lat: number; min_lng: number; max_lng: number }
export interface DistrictConfig { district: string; bounds: Bounds; taluks: Taluk[] }

export interface CalendarEvent {
  date: string;
  type: 'FESTIVAL' | 'PROTEST' | 'HEAVY_RAIN';
  name: string;
  volume_multiplier?: number;
  response_multiplier?: number;
  category_multipliers?: Partial<Record<Category, number>>;
  taluks?: string[] | 'ALL';
}
export interface EventsConfig {
  non_rain_weather_multiplier: number;
  taluk_focus_multiplier: number;
  events: CalendarEvent[];
}

// Strip a UTF-8 BOM in case the file was saved by a Windows editor.
const readJson = <T>(rel: string): T => JSON.parse(fs.readFileSync(path.join(ROOT, rel), 'utf8').replace(/^﻿/, '')) as T;

export const inBounds = (b: Bounds, lat: number, lng: number): boolean =>
  lat >= b.min_lat && lat <= b.max_lat && lng >= b.min_lng && lng <= b.max_lng;

export function loadDistrict(): DistrictConfig {
  const cfg = readJson<DistrictConfig>('config/taluks.json');
  const errors: string[] = [];
  const codes = new Set<string>();
  for (const t of cfg.taluks) {
    if (!/^TLK-[A-Z]{3}$/.test(t.code)) errors.push(`${t.code}: taluk code must look like TLK-XXX`);
    if (t.localities.length < 4 || t.localities.length > 8) errors.push(`${t.code}: needs 4-8 localities`);
    if (t.stations.length < 2 || t.stations.length > 4) errors.push(`${t.code}: needs 2-4 stations`);
    const points = [t.centroid, ...t.localities, ...t.stations];
    for (const p of points) {
      if (!inBounds(cfg.bounds, p.lat, p.lng)) errors.push(`${t.code}: point ${p.lat},${p.lng} is outside Chennai bounds`);
    }
    for (const s of t.stations) {
      if (codes.has(s.code)) errors.push(`duplicate station code ${s.code}`);
      codes.add(s.code);
    }
  }
  if (cfg.taluks.length !== 16) errors.push(`expected 16 taluks, found ${cfg.taluks.length}`);
  if (errors.length) throw new Error(`config/taluks.json is invalid:\n  ${errors.join('\n  ')}`);
  return cfg;
}

export function loadEvents(): EventsConfig {
  return readJson<EventsConfig>('config/events.json');
}

export function dbSettings() {
  return {
    host: process.env.DB_HOST || 'localhost',
    port: Number(process.env.DB_PORT || 3306),
    user: process.env.DB_USER || 'root',
    password: process.env.DB_PASSWORD || '',
    database: process.env.DB_NAME || 'district_collector_dashboard',
  };
}
