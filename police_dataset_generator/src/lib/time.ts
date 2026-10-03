/**
 * Wall-clock time helpers.
 *
 * Every timestamp in the generator is a "wall-clock millisecond" value: the
 * local Asia/Kolkata date-time encoded as if it were UTC. That keeps the
 * arithmetic simple (a day is always 86,400,000 ms) and makes the output
 * independent of the machine's own time zone. DATETIME columns and CSV values
 * are written as 'YYYY-MM-DD HH:MM:SS' in local (IST) time.
 */

export const MIN = 60_000;
export const HOUR = 60 * MIN;
export const DAY = 24 * HOUR;

/** Current local wall-clock time in the configured zone (default Asia/Kolkata). */
export function nowWall(tz = process.env.TZ || 'Asia/Kolkata'): number {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: tz,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date());
  const get = (type: string) => Number(parts.find((p) => p.type === type)!.value);
  return Date.UTC(get('year'), get('month') - 1, get('day'), get('hour'), get('minute'), get('second'));
}

/** 'YYYY-MM-DD HH:MM:SS' */
export const fmt = (ms: number): string => new Date(ms).toISOString().slice(0, 19).replace('T', ' ');
/** 'YYYY-MM-DD' */
export const fmtDate = (ms: number): string => new Date(ms).toISOString().slice(0, 10);
/** 'YYYYMMDD' */
export const compactDate = (ms: number): string => fmtDate(ms).replace(/-/g, '');
/** Parse 'YYYY-MM-DD HH:MM:SS' (or 'YYYY-MM-DD') back to wall-clock ms. */
export const parseWall = (s: string): number => Date.parse((s.length === 10 ? `${s} 00:00:00` : s).replace(' ', 'T') + 'Z');

export const startOfDay = (ms: number): number => Math.floor(ms / DAY) * DAY;
export const hourOf = (ms: number): number => new Date(ms).getUTCHours();
/** 0 = Sunday ... 6 = Saturday */
export const dayOfWeek = (ms: number): number => new Date(ms).getUTCDay();

/** Rolling window: start = today minus (days - 1) at 00:00, end = now. */
export function computeWindow(days: number, now: number): { startMs: number; endMs: number } {
  return { startMs: startOfDay(now) - (days - 1) * DAY, endMs: now };
}

/** Human phrase used in descriptions, e.g. "around 9.40 pm". */
export function timePhrase(ms: number): string {
  const d = new Date(ms);
  const h = d.getUTCHours();
  const m = Math.floor(d.getUTCMinutes() / 5) * 5;
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `around ${h12}.${String(m).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`;
}
