/**
 * Generates one calendar day of real-world events and the reports they produce.
 *
 * Each day is generated from its own RNG stream seeded by (seed, date), so a
 * day always yields the same events no matter when the program runs. The
 * caller filters out reports that have not "happened yet" (reported after now),
 * which is what lets --refresh fill in only the missing tail of the window.
 */
import { Rng } from './rng';
import {
  CATEGORIES, CATEGORY_MIX, CROWD_CATEGORIES, DUPLICATE_SOURCE, HOURLY, MULTI_REPORT_PROB, NO_RESPONSE_CATEGORIES,
  PRIMARY_SOURCE, SOURCES, SUBTYPES, WEEKEND_MULTIPLIER, render,
  type Attrs, type BuildCtx, type Category, type Source, type Subtype,
} from './categories';
import type { Bounds, CalendarEvent, DistrictConfig, EventsConfig, Locality, Station, Taluk } from './config';
import { DAY, HOUR, MIN, compactDate, dayOfWeek, fmtDate, timePhrase } from './time';

/** A report before it gets its report_id and status. */
export interface DraftReport {
  eventId: string;
  incidentMs: number;
  reportedMs: number;
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
  response_minutes: number | null;
}

export interface GenEvent {
  eventId: string;
  /** True when this event was created as a same-day, same-place, same-category look-alike. */
  nearMiss: boolean;
  reports: DraftReport[];
}

interface DayCtx {
  rng: Rng;
  dayStartMs: number;
  rain: boolean;
  festival: boolean;
  responseMultiplier: number;
}

interface ProtoEvent {
  category: Category;
  taluk: Taluk;
  locality: Locality;
  hour: number;
  nearMiss: boolean;
  reports: Omit<DraftReport, 'eventId'>[];
}

// Geo helpers -----------------------------------------------------------------
const M_PER_DEG = 111_320;
const round6 = (n: number) => Math.round(n * 1e6) / 1e6;
function offsetMeters(lat: number, lng: number, north: number, east: number): [number, number] {
  return [lat + north / M_PER_DEG, lng + east / (M_PER_DEG * Math.cos((lat * Math.PI) / 180))];
}
function sqDist(aLat: number, aLng: number, bLat: number, bLng: number): number {
  const dy = aLat - bLat;
  const dx = (aLng - bLng) * Math.cos((aLat * Math.PI) / 180);
  return dx * dx + dy * dy;
}

/** Keep counts, crowd and blockage consistent with the category. */
function sanitize(cat: Category, a: Attrs): Attrs {
  const b = { ...a };
  if (cat === 'MURDER') b.fatalities = Math.max(1, b.fatalities);
  b.persons = Math.max(b.persons, b.injured + b.fatalities);
  if (cat === 'CYBER_FRAUD') { b.roadBlocked = false; b.blockage = null; }
  if (!b.roadBlocked) b.blockage = null;
  if (!CROWD_CATEGORIES.includes(cat)) b.crowd = null;
  // "Directly harmed": for casualty-type categories the victim must be hurt.
  const harmByCasualty: Category[] = ['ROAD_ACCIDENT', 'ASSAULT', 'WEATHER_EMERGENCY', 'OTHER', 'PROTEST_LAW_AND_ORDER', 'DRUGS_ILLICIT_LIQUOR', 'PUBLIC_NUISANCE', 'MURDER'];
  if (harmByCasualty.includes(cat) && b.injured + b.fatalities === 0) b.vulnerable = false;
  if (b.persons === 0) b.vulnerable = false;
  return b;
}

/** Slightly different counts, crowd and blockage as seen by a second source. */
function vary(r: Rng, cat: Category, a: Attrs): Attrs {
  const b = { ...a };
  if (b.injured + b.fatalities > 0 && r.chance(0.3)) b.injured = Math.max(cat === 'ASSAULT' ? 1 : 0, b.injured + (r.chance(0.5) ? 1 : -1));
  if (b.injured > 0 && (cat === 'ROAD_ACCIDENT' || cat === 'WEATHER_EMERGENCY') && r.chance(0.06)) { b.injured--; b.fatalities++; }
  if (b.persons > 0 && cat !== 'MISSING_PERSON' && r.chance(0.25)) b.persons = Math.max(1, b.persons + (r.chance(0.5) ? 1 : -1));
  if (b.crowd !== null) b.crowd = Math.max(10, Math.round((b.crowd * r.float(0.7, 1.4)) / 10) * 10);
  if (b.blockage !== null) b.blockage = Math.max(5, Math.round((b.blockage * r.float(0.75, 1.3)) / 5) * 5);
  return sanitize(cat, b);
}

/** Minutes between the incident and this source registering it. */
function lagMinutes(r: Rng, source: Source, cat: Category): number {
  let m: number;
  switch (source) {
    case 'CONTROL_ROOM_112': m = 1 + r.expo(4); break;
    case 'PATROL': m = r.int(2, 30); break;
    case 'FIR': m = 60 + r.expo(300); break;
    case 'CITIZEN_GRIEVANCE': m = 120 + r.expo(900); break;
    case 'NEWS_REPORT': m = 180 + r.expo(420); break;
  }
  if (cat === 'CYBER_FRAUD' && source !== 'CONTROL_ROOM_112') m += r.expo(1440); // victims realise late
  if (cat === 'MISSING_PERSON') m += 240 + r.expo(600); // families search first
  return Math.min(Math.round(m), 7 * 24 * 60);
}

/** Log-normal response time (median ≈ 12 min) with a long tail; slower at peaks and in rain. */
function responseMinutes(r: Rng, cat: Category, hour: number, day: DayCtx): number | null {
  if (NO_RESPONSE_CATEGORIES.includes(cat)) return null;
  let m = r.logNormal(11, 0.5);
  if (r.chance(0.07)) m *= r.float(2, 5);
  if ((hour >= 8 && hour < 10) || (hour >= 17 && hour < 21)) m *= 1.35;
  if (day.rain) m *= day.responseMultiplier;
  return Math.min(600, Math.max(2, Math.round(m)));
}

function pickHour(r: Rng, profile: number[], avoidHour?: number): number {
  const hours = profile.map((_, h) => h);
  let w = profile.map((x, h) => (avoidHour !== undefined && Math.abs(h - avoidHour) < 3 ? 0 : x));
  if (w.every((x) => x === 0)) w = hours.map((h) => (avoidHour !== undefined && Math.abs(h - avoidHour) < 3 ? 0 : 1));
  return r.weighted(hours, w);
}

export class DayGenerator {
  private eventsByDate = new Map<string, CalendarEvent[]>();

  constructor(
    private seed: number,
    private perDay: number,
    private district: DistrictConfig,
    private calendar: EventsConfig,
  ) {
    for (const e of calendar.events) {
      const list = this.eventsByDate.get(e.date) ?? [];
      list.push(e);
      this.eventsByDate.set(e.date, list);
    }
  }

  calendarFor(dayStartMs: number): CalendarEvent[] {
    return this.eventsByDate.get(fmtDate(dayStartMs)) ?? [];
  }

  /** All events (with every report they will ever produce) for one day. */
  generate(dayStartMs: number): GenEvent[] {
    const rng = new Rng(`${this.seed}|${fmtDate(dayStartMs)}`);
    const todays = this.calendarFor(dayStartMs);
    const rain = todays.some((e) => e.type === 'HEAVY_RAIN');
    const day: DayCtx = {
      rng, dayStartMs, rain,
      festival: todays.some((e) => e.type === 'FESTIVAL'),
      responseMultiplier: Math.max(1, ...todays.map((e) => e.response_multiplier ?? 1)),
    };
    const weekend = [0, 6].includes(dayOfWeek(dayStartMs));
    const volume = todays.reduce((m, e) => m * (e.volume_multiplier ?? 1), 1);
    const count = Math.max(1, Math.round(this.perDay * rng.float(0.75, 1.25) * (weekend ? 1.08 : 1) * volume));

    const catWeights = CATEGORIES.map((c) => {
      let w = CATEGORY_MIX[c];
      if (weekend) w *= WEEKEND_MULTIPLIER[c] ?? 1;
      for (const e of todays) w *= e.category_multipliers?.[c] ?? 1;
      if (c === 'WEATHER_EMERGENCY' && !rain) w *= this.calendar.non_rain_weather_multiplier;
      return w;
    });

    const events: ProtoEvent[] = [];
    for (let i = 0; i < count; i++) {
      const cat = rng.weighted(CATEGORIES, catWeights);
      events.push(this.makeEvent(day, cat, this.pickTaluk(rng, cat, todays)));
    }

    // Near-miss look-alikes: same category, same locality, same day, different event.
    for (const e of [...events]) {
      if (e.category !== 'MURDER' && rng.chance(0.03)) {
        events.push(this.makeEvent(day, e.category, e.taluk, { locality: e.locality, avoidHour: e.hour }));
      }
    }

    // Coverage guarantee: each taluk is forced to have an event on one day of
    // every 7 (by day number), so any 7 consecutive days include every taluk.
    const epochDay = Math.round(dayStartMs / DAY);
    const nonMurder = catWeights.map((w, i) => (CATEGORIES[i] === 'MURDER' ? 0 : w));
    this.district.taluks.forEach((t, idx) => {
      if ((epochDay + idx) % 7 === 0 && !events.some((e) => e.taluk === t)) {
        events.push(this.makeEvent(day, rng.weighted(CATEGORIES, nonMurder), t));
      }
    });

    const prefix = `EVT-${compactDate(dayStartMs)}-`;
    return events.map((e, k) => {
      const eventId = prefix + String(k + 1).padStart(4, '0');
      return { eventId, nearMiss: e.nearMiss, reports: e.reports.map((r) => ({ ...r, eventId })) };
    });
  }

  private pickTaluk(r: Rng, cat: Category, todays: CalendarEvent[]): Taluk {
    const weights = this.district.taluks.map((t) => {
      let w = (t.weight ?? 1) * (t.hotspots?.[cat] ?? 1);
      for (const e of todays) {
        if (Array.isArray(e.taluks) && e.taluks.includes(t.code) && (e.category_multipliers?.[cat] ?? 1) > 1) {
          w *= this.calendar.taluk_focus_multiplier;
        }
      }
      return w;
    });
    return r.weighted(this.district.taluks, weights);
  }

  private makeEvent(day: DayCtx, cat: Category, taluk: Taluk, opts?: { locality: Locality; avoidHour: number }): ProtoEvent {
    const r = day.rng;
    const hour = pickHour(r, HOURLY[cat], opts?.avoidHour);
    const incidentMs = day.dayStartMs + hour * HOUR + r.int(0, 59) * MIN;
    const locality = opts?.locality ?? r.pick(taluk.localities);

    // Event location: scattered around the locality centre (sigma 250 m, capped).
    const spread = cat === 'CYBER_FRAUD' ? 150 : 250;
    const clampN = (x: number) => Math.max(-2.8, Math.min(2.8, x));
    const [lat, lng] = this.clamp(offsetMeters(locality.lat, locality.lng, clampN(r.normal()) * spread, clampN(r.normal()) * spread));

    const ctx: BuildCtx = {
      rng: r, rain: day.rain, festival: day.festival,
      heavyShare: taluk.heavy_vehicle_share ?? 0.12,
      crowdHotspot: (taluk.hotspots?.OTHER ?? 1) > 1,
    };
    const subs = SUBTYPES[cat];
    const sub = r.weighted(subs, subs.map((s) => s.weight(ctx)));
    const attrs = sanitize(cat, sub.build(ctx));

    return {
      category: cat, taluk, locality, hour, nearMiss: !!opts,
      reports: this.makeReports(day, cat, taluk, locality, sub, attrs, incidentMs, lat, lng),
    };
  }

  /** One report per source; 2nd..4th reports differ in time, place, wording and counts. */
  private makeReports(
    day: DayCtx, cat: Category, taluk: Taluk, locality: Locality, sub: Subtype, attrs: Attrs,
    incidentMs: number, lat: number, lng: number,
  ): Omit<DraftReport, 'eventId'>[] {
    const r = day.rng;
    const n = r.chance(MULTI_REPORT_PROB[cat]) ? r.weighted([2, 3, 4], [0.6, 0.3, 0.1]) : 1;

    const primary = PRIMARY_SOURCE[cat];
    const sources: Source[] = [r.weighted(SOURCES, SOURCES.map((s) => primary[s]))];
    while (sources.length < n) {
      const pool = SOURCES.filter((s) => !sources.includes(s) && !(cat === 'CYBER_FRAUD' && s === 'PATROL'));
      if (!pool.length) break;
      sources.push(r.weighted(pool, pool.map((s) => DUPLICATE_SOURCE[s])));
    }

    const variantBase = r.int(0, 11);
    return sources.map((source, j) => {
      const dup = j > 0;
      const repIncidentMs = incidentMs + (dup ? r.int(-18, 18) * 5 * MIN : 0); // up to ±90 min
      const reportedMs = Math.max(incidentMs, repIncidentMs) + lagMinutes(r, source, cat) * MIN + r.int(0, 59) * 1000;
      const [rLat, rLng] = dup ? this.jitter(r, lat, lng, r.float(50, 400)) : [lat, lng];
      const locName = dup && r.chance(0.35) ? this.altLocalityName(r, locality, taluk) : locality.name;
      const a = dup ? vary(r, cat, attrs) : attrs;
      const text = render(sub, a, { loc: locName, lm: this.landmark(r, locality, locName), t: timePhrase(repIncidentMs) }, variantBase + j);
      return {
        incidentMs: repIncidentMs,
        reportedMs,
        station_code: this.nearestStation(taluk, rLat, rLng).code,
        taluk_code: taluk.code,
        locality: locName,
        latitude: rLat,
        longitude: rLng,
        category: cat,
        title: text.title,
        description: text.description,
        persons_affected: a.persons,
        injured_count: a.injured,
        fatalities: a.fatalities,
        vulnerable_victim: a.vulnerable,
        weapon_involved: a.weapon,
        road_blocked: a.roadBlocked,
        blockage_minutes: a.roadBlocked ? a.blockage : null,
        crowd_estimate: a.crowd,
        is_weather_related: a.weather,
        source,
        linked_grievance_code: source === 'CITIZEN_GRIEVANCE' ? `CMP-${compactDate(reportedMs)}-${String(r.int(0, 9999)).padStart(4, '0')}` : null,
        response_minutes: responseMinutes(r, cat, new Date(repIncidentMs).getUTCHours(), day),
      };
    });
  }

  /** A nearby name for the same place: one of its landmarks or the closest other locality. */
  private altLocalityName(r: Rng, loc: Locality, taluk: Taluk): string {
    if (loc.landmarks?.length && r.chance(0.6)) return r.pick(loc.landmarks);
    const others = taluk.localities.filter((l) => l !== loc);
    others.sort((a, b) => sqDist(loc.lat, loc.lng, a.lat, a.lng) - sqDist(loc.lat, loc.lng, b.lat, b.lng));
    return others[0].name;
  }

  private landmark(r: Rng, loc: Locality, locName: string): string {
    const options = (loc.landmarks ?? []).filter((l) => l !== locName);
    return options.length ? r.pick(options) : `${loc.name} Main Road`;
  }

  private nearestStation(taluk: Taluk, lat: number, lng: number): Station {
    return taluk.stations.reduce((best, s) => (sqDist(lat, lng, s.lat, s.lng) < sqDist(lat, lng, best.lat, best.lng) ? s : best));
  }

  private jitter(r: Rng, lat: number, lng: number, meters: number): [number, number] {
    const theta = r.float(0, 2 * Math.PI);
    return this.clamp(offsetMeters(lat, lng, meters * Math.cos(theta), meters * Math.sin(theta)));
  }

  private clamp([lat, lng]: [number, number]): [number, number] {
    const b: Bounds = this.district.bounds;
    const m = 0.001;
    return [round6(Math.min(b.max_lat - m, Math.max(b.min_lat + m, lat))), round6(Math.min(b.max_lng - m, Math.max(b.min_lng + m, lng)))];
  }
}
