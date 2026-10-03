/**
 * Case status as a pure function of (report_id, category, reported time, now).
 *
 * Each report gets hidden, deterministic milestone durations derived from its
 * report_id: REPORTED -> UNDER_INVESTIGATION -> ACTION_TAKEN -> CLOSED. The
 * status at any moment is simply the last milestone reached. Because the
 * milestones never change, re-evaluating on a later --refresh run moves cases
 * forward naturally (never backwards), and closed_datetime is always after
 * reported_datetime and never in the future.
 */
import { Rng } from './rng';
import { DAY, HOUR, fmt } from './time';
import type { Category, Status } from './categories';

const QUICK: Category[] = ['TRAFFIC_OBSTRUCTION', 'PUBLIC_NUISANCE', 'WEATHER_EMERGENCY', 'OTHER'];
const SERIOUS: Category[] = ['MURDER', 'MISSING_PERSON', 'CYBER_FRAUD', 'CRIMES_AGAINST_WOMEN'];

export function lifecycle(reportId: string, category: Category, reportedMs: number, nowMs: number): { status: Status; closed_datetime: string | null } {
  const r = new Rng(`life|${reportId}`);
  const ack = (0.25 + r.expo(10)) * HOUR;

  let closeAfter: number; // ms after reported; Infinity = long-pending case
  if (QUICK.includes(category)) closeAfter = (1.5 + r.expo(5)) * DAY;
  else if (SERIOUS.includes(category)) closeAfter = r.chance(0.15) ? Infinity : (8 + r.expo(22)) * DAY;
  else closeAfter = (4 + r.expo(10)) * DAY;
  closeAfter = Math.max(closeAfter, ack + HOUR);

  const actionAfter = Number.isFinite(closeAfter)
    ? ack + (closeAfter - ack) * r.float(0.45, 0.85)
    : ack + r.float(10, 60) * DAY;

  const age = nowMs - reportedMs;
  if (age >= closeAfter) {
    const closedMs = Math.floor((reportedMs + closeAfter) / 1000) * 1000;
    return { status: 'CLOSED', closed_datetime: fmt(closedMs) };
  }
  if (age >= actionAfter) return { status: 'ACTION_TAKEN', closed_datetime: null };
  if (age >= ack) return { status: 'UNDER_INVESTIGATION', closed_datetime: null };
  return { status: 'REPORTED', closed_datetime: null };
}
