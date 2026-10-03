/**
 * Minimal CLI parser. Accepts both "--days 90" and "--days=90" forms.
 *   --days      window length in days (default 90; 180 for quarter-over-quarter)
 *   --per-day   mean events per day (default 50, varied ±25% per day)
 *   --seed      RNG seed for reproducible output (default 42)
 *   --mode      csv | mysql | both (default both)
 *   --refresh   roll the window forward instead of rebuilding it
 */
export type Mode = 'csv' | 'mysql' | 'both';

export interface CliArgs {
  days: number;
  perDay: number;
  seed: number;
  mode: Mode;
  refresh: boolean;
}

export function parseArgs(argv = process.argv.slice(2)): CliArgs {
  const raw = new Map<string, string>();
  const positional: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) { positional.push(a); continue; }
    const eq = a.indexOf('=');
    if (eq > 0) raw.set(a.slice(2, eq), a.slice(eq + 1));
    else if (argv[i + 1] !== undefined && !argv[i + 1].startsWith('--')) raw.set(a.slice(2), argv[++i]);
    else raw.set(a.slice(2), 'true');
  }

  // Windows PowerShell 5.1 drops the bare "--" in `npm run generate -- --days 90`,
  // so npm parses the flags itself: --mode/--refresh arrive as npm_config_*
  // variables (recover them), but numeric values get mangled (e.g. --days is read
  // as the shorthands -d -a -y -s and "90" is passed as a loose argument). Refuse
  // to run with silently-wrong settings and say how to call it instead.
  for (const key of ['mode', 'refresh']) {
    const env = process.env[`npm_config_${key}`];
    if (!raw.has(key) && env) raw.set(key, env);
  }
  const mangled = ['days', 'per_day', 'seed'].some((k) => process.env[`npm_config_${k}`] !== undefined);
  if (positional.length || mangled) {
    throw new Error(
      'Command-line options did not reach the script (Windows PowerShell drops the "--" separator for npm).\n' +
      '  In PowerShell use npm.cmd:  npm.cmd run generate -- --days 90 --seed 42\n' +
      "  or quote the separator:     npm run generate '--' --days 90 --seed 42");
  }

  const num = (key: string, def: number, min: number, max: number): number => {
    if (!raw.has(key)) return def;
    const n = Number(raw.get(key));
    if (!Number.isInteger(n) || n < min || n > max) throw new Error(`--${key} must be an integer between ${min} and ${max}`);
    return n;
  };

  const mode = (raw.get('mode') ?? 'both') as Mode;
  if (!['csv', 'mysql', 'both'].includes(mode)) throw new Error('--mode must be csv, mysql or both');

  return {
    days: num('days', 90, 7, 400),
    perDay: num('per-day', 50, 1, 2000),
    seed: num('seed', 42, 0, 2 ** 31 - 1),
    mode,
    refresh: raw.get('refresh') === 'true',
  };
}

export const usesCsv = (m: Mode) => m === 'csv' || m === 'both';
export const usesMysql = (m: Mode) => m === 'mysql' || m === 'both';
