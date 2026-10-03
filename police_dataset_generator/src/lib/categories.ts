/**
 * Category catalogue: base mix, hourly profiles, source mix, duplicate-report
 * propensity, and per-subtype attribute builders with headline/description
 * templates. All realism rules that depend on the incident category live here.
 */
import { Rng } from './rng';

export const CATEGORIES = [
  'ROAD_ACCIDENT', 'THEFT_BURGLARY', 'CHAIN_SNATCHING', 'TRAFFIC_OBSTRUCTION', 'PUBLIC_NUISANCE',
  'CYBER_FRAUD', 'ASSAULT', 'CRIMES_AGAINST_WOMEN', 'MISSING_PERSON', 'PROTEST_LAW_AND_ORDER',
  'DRUGS_ILLICIT_LIQUOR', 'WEATHER_EMERGENCY', 'MURDER', 'OTHER',
] as const;
export type Category = (typeof CATEGORIES)[number];

export const SOURCES = ['FIR', 'CONTROL_ROOM_112', 'PATROL', 'CITIZEN_GRIEVANCE', 'NEWS_REPORT'] as const;
export type Source = (typeof SOURCES)[number];

export const STATUSES = ['REPORTED', 'UNDER_INVESTIGATION', 'ACTION_TAKEN', 'CLOSED'] as const;
export type Status = (typeof STATUSES)[number];

/** Base category mix (percent of events). */
export const CATEGORY_MIX: Record<Category, number> = {
  ROAD_ACCIDENT: 18, THEFT_BURGLARY: 15, CYBER_FRAUD: 10, TRAFFIC_OBSTRUCTION: 10, PUBLIC_NUISANCE: 9,
  CHAIN_SNATCHING: 7, ASSAULT: 7, CRIMES_AGAINST_WOMEN: 5, MISSING_PERSON: 5, PROTEST_LAW_AND_ORDER: 4,
  DRUGS_ILLICIT_LIQUOR: 4, WEATHER_EMERGENCY: 3, MURDER: 1, OTHER: 2,
};

/** Nuisance and accidents rise on weekends. */
export const WEEKEND_MULTIPLIER: Partial<Record<Category, number>> = { ROAD_ACCIDENT: 1.2, PUBLIC_NUISANCE: 1.35 };

/** Categories that can carry a crowd_estimate (protests and crowd events). */
export const CROWD_CATEGORIES: Category[] = ['PROTEST_LAW_AND_ORDER', 'PUBLIC_NUISANCE', 'OTHER'];

/** Categories where response_minutes is not meaningful. */
export const NO_RESPONSE_CATEGORIES: Category[] = ['CYBER_FRAUD', 'MISSING_PERSON'];

// ---------------------------------------------------------------------------
// Hour-of-day profiles (relative weights for hours 0..23)
// ---------------------------------------------------------------------------
const H_DAY = [1, 0.8, 0.6, 0.5, 0.5, 0.8, 1.5, 2.5, 3.5, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 3.5, 3, 2, 1.5];
export const HOURLY: Record<Category, number[]> = {
  // Peaks at 8-10am, 6-9pm and late night.
  ROAD_ACCIDENT: [6, 5, 4, 3, 2, 2, 3, 5, 9, 9, 7, 5, 5, 5, 5, 5, 6, 7, 9, 9, 9, 7, 6, 6],
  // Mostly at night.
  THEFT_BURGLARY: [8, 9, 9, 8, 7, 4, 2, 1, 1, 1, 1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 2, 2, 3, 4, 6, 7],
  // Early morning walkers and evening commuters.
  CHAIN_SNATCHING: [0.3, 0.2, 0.2, 0.3, 2, 6, 7, 6, 3, 2, 2, 2, 2, 2, 2, 3, 4, 6, 7, 7, 5, 2, 1, 0.5],
  TRAFFIC_OBSTRUCTION: [1, 1, 1, 1, 1, 1, 2, 4, 7, 7, 5, 4, 3, 3, 3, 4, 5, 7, 8, 7, 5, 3, 2, 1],
  PUBLIC_NUISANCE: [4, 3, 2, 1, 0.5, 0.5, 0.5, 0.5, 1, 1, 1, 1, 1.5, 1.5, 1.5, 2, 2, 3, 4, 5, 6, 7, 7, 6],
  CYBER_FRAUD: [0.3, 0.2, 0.2, 0.2, 0.2, 0.3, 0.6, 1, 2, 3, 4, 4, 4, 4, 4, 4, 4, 3.5, 3, 3, 2.5, 2, 1, 0.6],
  ASSAULT: [3, 2.5, 2, 1, 0.5, 0.5, 0.8, 1, 1.5, 2, 2, 2, 2, 2, 2, 2.5, 3, 3.5, 4, 5, 5, 5, 4.5, 4],
  CRIMES_AGAINST_WOMEN: [1, 0.8, 0.5, 0.3, 0.3, 0.5, 1, 2, 3.5, 3.5, 3, 3, 3, 3, 3, 3, 3.5, 4, 4.5, 4.5, 4, 3, 2, 1.5],
  MISSING_PERSON: H_DAY,
  PROTEST_LAW_AND_ORDER: [0, 0, 0, 0, 0, 0, 0.2, 0.5, 2, 5, 7, 7, 6, 5, 5, 4, 3, 2, 1, 0.5, 0.2, 0, 0, 0],
  DRUGS_ILLICIT_LIQUOR: [3, 3, 2, 1, 0.5, 0.5, 0.5, 0.5, 1, 1, 1, 1, 1, 1, 1, 2, 3, 4, 5, 5, 5, 4, 4, 3],
  WEATHER_EMERGENCY: [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2.5, 3, 3.5, 4, 4, 4, 4, 3.5, 3, 3, 2.5, 2],
  MURDER: [5, 5, 4, 3, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 3, 4, 4, 5, 5],
  OTHER: H_DAY,
};

// ---------------------------------------------------------------------------
// Source mix and duplicate-report propensity
// ---------------------------------------------------------------------------
type SourceWeights = Record<Source, number>;
const DEFAULT_PRIMARY: SourceWeights = { CONTROL_ROOM_112: 0.4, PATROL: 0.2, FIR: 0.3, CITIZEN_GRIEVANCE: 0.05, NEWS_REPORT: 0.05 };
export const PRIMARY_SOURCE: Record<Category, SourceWeights> = {
  ROAD_ACCIDENT: { CONTROL_ROOM_112: 0.5, PATROL: 0.22, FIR: 0.18, CITIZEN_GRIEVANCE: 0.03, NEWS_REPORT: 0.07 },
  THEFT_BURGLARY: { CONTROL_ROOM_112: 0.3, PATROL: 0.05, FIR: 0.55, CITIZEN_GRIEVANCE: 0.07, NEWS_REPORT: 0.03 },
  CHAIN_SNATCHING: { CONTROL_ROOM_112: 0.5, PATROL: 0.1, FIR: 0.33, CITIZEN_GRIEVANCE: 0.03, NEWS_REPORT: 0.04 },
  TRAFFIC_OBSTRUCTION: { CONTROL_ROOM_112: 0.38, PATROL: 0.42, FIR: 0.02, CITIZEN_GRIEVANCE: 0.1, NEWS_REPORT: 0.08 },
  PUBLIC_NUISANCE: { CONTROL_ROOM_112: 0.48, PATROL: 0.32, FIR: 0.05, CITIZEN_GRIEVANCE: 0.12, NEWS_REPORT: 0.03 },
  CYBER_FRAUD: { CONTROL_ROOM_112: 0.22, PATROL: 0, FIR: 0.65, CITIZEN_GRIEVANCE: 0.1, NEWS_REPORT: 0.03 },
  ASSAULT: DEFAULT_PRIMARY,
  CRIMES_AGAINST_WOMEN: { CONTROL_ROOM_112: 0.4, PATROL: 0.1, FIR: 0.42, CITIZEN_GRIEVANCE: 0.05, NEWS_REPORT: 0.03 },
  MISSING_PERSON: { CONTROL_ROOM_112: 0.35, PATROL: 0, FIR: 0.55, CITIZEN_GRIEVANCE: 0.08, NEWS_REPORT: 0.02 },
  PROTEST_LAW_AND_ORDER: { CONTROL_ROOM_112: 0.3, PATROL: 0.4, FIR: 0.1, CITIZEN_GRIEVANCE: 0, NEWS_REPORT: 0.2 },
  DRUGS_ILLICIT_LIQUOR: { CONTROL_ROOM_112: 0.05, PATROL: 0.6, FIR: 0.3, CITIZEN_GRIEVANCE: 0.05, NEWS_REPORT: 0 },
  WEATHER_EMERGENCY: { CONTROL_ROOM_112: 0.52, PATROL: 0.26, FIR: 0.02, CITIZEN_GRIEVANCE: 0.1, NEWS_REPORT: 0.1 },
  MURDER: { CONTROL_ROOM_112: 0.6, PATROL: 0.1, FIR: 0.3, CITIZEN_GRIEVANCE: 0, NEWS_REPORT: 0 },
  OTHER: DEFAULT_PRIMARY,
};

/** Sources used for the 2nd..4th report of the same event. */
export const DUPLICATE_SOURCE: SourceWeights = { FIR: 4, NEWS_REPORT: 3, CITIZEN_GRIEVANCE: 1.2, CONTROL_ROOM_112: 2, PATROL: 1 };

/** Chance that an event produces more than one report (overall â‰ˆ 20%). */
export const MULTI_REPORT_PROB: Record<Category, number> = {
  ROAD_ACCIDENT: 0.22, THEFT_BURGLARY: 0.15, CHAIN_SNATCHING: 0.2, TRAFFIC_OBSTRUCTION: 0.15, PUBLIC_NUISANCE: 0.12,
  CYBER_FRAUD: 0.15, ASSAULT: 0.22, CRIMES_AGAINST_WOMEN: 0.2, MISSING_PERSON: 0.3, PROTEST_LAW_AND_ORDER: 0.35,
  DRUGS_ILLICIT_LIQUOR: 0.15, WEATHER_EMERGENCY: 0.35, MURDER: 0.75, OTHER: 0.18,
};

// ---------------------------------------------------------------------------
// Attributes and text
// ---------------------------------------------------------------------------
export interface Attrs {
  persons: number;
  injured: number;
  fatalities: number;
  vulnerable: boolean;
  weapon: boolean;
  roadBlocked: boolean;
  blockage: number | null;
  crowd: number | null;
  weather: boolean;
  /** Free-text slots used by the templates (vehicle type, amount, victim wording...). */
  x: Record<string, string>;
}

export interface BuildCtx {
  rng: Rng;
  rain: boolean;
  festival: boolean;
  heavyShare: number;
  crowdHotspot: boolean;
}

export interface TextVars { loc: string; lm: string; t: string }
type Tpl = (a: Attrs, v: TextVars) => string;

export interface Subtype {
  key: string;
  weight: (c: BuildCtx) => number;
  build: (c: BuildCtx) => Attrs;
  titles: Tpl[];
  descs: Tpl[];
}

const A = (p: Partial<Attrs>): Attrs => ({
  persons: 0, injured: 0, fatalities: 0, vulnerable: false, weapon: false,
  roadBlocked: false, blockage: null, crowd: null, weather: false, x: {}, ...p,
});
const round5 = (n: number) => Math.max(5, Math.round(n / 5) * 5);
const round10 = (n: number) => Math.max(10, Math.round(n / 10) * 10);
const block = (c: BuildCtx, p: number, min: number, max: number) =>
  c.rng.chance(p) ? { roadBlocked: true, blockage: round5(c.rng.int(min, max)) } : { roadBlocked: false, blockage: null };

// Text helpers ---------------------------------------------------------------
const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'];
const num = (n: number) => (n <= 10 ? WORDS[n] : String(n));
const people = (n: number) => `${num(n)} ${n === 1 ? 'person' : 'persons'}`;
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const rs = (n: number) => `Rs ${n.toLocaleString('en-IN')}`;
function cas(a: Attrs): string {
  if (a.fatalities > 0 && a.injured > 0) return `${people(a.fatalities)} died and ${num(a.injured)} ${a.injured === 1 ? 'was' : 'were'} injured`;
  if (a.fatalities > 0) return `${people(a.fatalities)} died`;
  if (a.injured > 0) return `${people(a.injured)} ${a.injured === 1 ? 'was' : 'were'} injured`;
  return 'no injuries were reported';
}
const traffic = (a: Attrs) => (a.roadBlocked ? `traffic was held up for about ${a.blockage} minutes` : 'traffic was not affected');
const wet = (a: Attrs) => (a.weather ? ' on the rain-soaked road' : '');

// ---------------------------------------------------------------------------
// Subtypes per category
// ---------------------------------------------------------------------------
const HEAVY_VEHICLES = ['container lorry', 'water tanker', 'sand-laden lorry', 'MTC bus', 'trailer truck', 'goods carrier'];

function accident(c: BuildCtx, kind: 'bike' | 'car' | 'heavy' | 'ped' | 'auto'): Attrs {
  const r = c.rng;
  const persons = { bike: r.int(1, 2), car: r.int(2, 3), heavy: r.int(1, 4), ped: 1, auto: r.int(2, 5) }[kind];
  const pFatal = kind === 'heavy' ? 0.14 : kind === 'ped' ? 0.1 : 0.04;
  const fatalities = r.chance(pFatal) ? (kind === 'heavy' && persons > 1 && r.chance(0.15) ? 2 : 1) : 0;
  const injured = kind === 'ped' ? 1 - fatalities : Math.max(0, persons - fatalities - r.int(0, 1));
  return A({
    persons, injured, fatalities,
    vulnerable: r.chance(kind === 'ped' ? 0.45 : 0.07),
    ...block(c, kind === 'heavy' ? 0.8 : 0.35, kind === 'heavy' ? 30 : 10, kind === 'heavy' ? 180 : 60),
    weather: c.rain && r.chance(0.35),
    x: { veh: kind === 'heavy' ? r.pick(HEAVY_VEHICLES) : r.pick(['car', 'cab', 'SUV']) },
  });
}

const ROAD_ACCIDENT: Subtype[] = [
  {
    key: 'bike', weight: (c) => (c.rain ? 4 : 3), build: (c) => accident(c, 'bike'),
    titles: [
      (a, v) => `Two-wheeler skids and falls near ${v.lm}, ${v.loc}`,
      (a, v) => `Motorcycle accident on ${v.lm} in ${v.loc}`,
      (a, v) => `Bike rider loses control near ${v.lm}`,
      (a, v) => `Two-wheeler mishap reported at ${v.loc}`,
    ],
    descs: [
      (a, v) => `A two-wheeler skidded near ${v.lm}, ${v.loc} ${v.t}${wet(a)}; ${cas(a)}.`,
      (a, v) => `Police were alerted that a motorcycle lost control on ${v.lm} in ${v.loc} ${v.t}. ${cap(cas(a))} and ${traffic(a)}.`,
      (a, v) => `Rider fell after the bike skidded near ${v.lm} (${v.loc}) ${v.t}; ${cas(a)}.`,
    ],
  },
  {
    key: 'car', weight: () => 2.5, build: (c) => accident(c, 'car'),
    titles: [
      (a, v) => `${cap(a.x.veh)} and motorcycle collide at ${v.lm}`,
      (a, v) => `Collision between ${a.x.veh} and two-wheeler in ${v.loc}`,
      (a, v) => `${cap(a.x.veh)} hits bike near ${v.lm}, ${v.loc}`,
      (a, v) => `Road accident involving ${a.x.veh} and bike at ${v.loc}`,
    ],
    descs: [
      (a, v) => `A ${a.x.veh} collided with a two-wheeler near ${v.lm}, ${v.loc} ${v.t}${wet(a)}; ${cas(a)}.`,
      (a, v) => `Caller reported a ${a.x.veh}-bike collision on ${v.lm} in ${v.loc} ${v.t}. ${cap(cas(a))}; ${traffic(a)}.`,
      (a, v) => `Two vehicles, a ${a.x.veh} and a motorcycle, collided at ${v.loc} ${v.t}. ${cap(cas(a))}.`,
    ],
  },
  {
    key: 'heavy', weight: (c) => 6 * c.heavyShare, build: (c) => accident(c, 'heavy'),
    titles: [
      (a, v) => `${cap(a.x.veh)} involved in accident on ${v.lm}`,
      (a, v) => `Accident involving ${a.x.veh} at ${v.loc}`,
      (a, v) => `${cap(a.x.veh)} rams vehicles near ${v.lm}`,
      (a, v) => `Heavy vehicle crash reported in ${v.loc}`,
    ],
    descs: [
      (a, v) => `A ${a.x.veh} hit vehicles near ${v.lm}, ${v.loc} ${v.t}; ${cas(a)} and ${traffic(a)}.`,
      (a, v) => `A ${a.x.veh} was involved in a crash on ${v.lm} in ${v.loc} ${v.t}${wet(a)}. ${cap(cas(a))}.`,
      (a, v) => `Accident involving a ${a.x.veh} reported at ${v.loc} ${v.t}; ${cas(a)}, ${traffic(a)}.`,
    ],
  },
  {
    key: 'ped', weight: () => 1.5, build: (c) => accident(c, 'ped'),
    titles: [
      (a, v) => `Pedestrian knocked down near ${v.lm}`,
      (a, v) => `Pedestrian hit by speeding vehicle in ${v.loc}`,
      (a, v) => `Vehicle hits pedestrian crossing road at ${v.lm}`,
      (a, v) => `Pedestrian accident reported at ${v.loc}`,
    ],
    descs: [
      (a, v) => `A pedestrian crossing near ${v.lm}, ${v.loc} was hit by a vehicle ${v.t}; ${cas(a)}.`,
      (a, v) => `A speeding vehicle knocked down a pedestrian on ${v.lm} in ${v.loc} ${v.t}. ${cap(cas(a))}.`,
      (a, v) => `Pedestrian was struck by a vehicle at ${v.loc} ${v.t}${wet(a)}; ${cas(a)}.`,
    ],
  },
  {
    key: 'auto', weight: () => 1, build: (c) => accident(c, 'auto'),
    titles: [
      (a, v) => `Share auto overturns near ${v.lm}`,
      (a, v) => `Autorickshaw overturns in ${v.loc}`,
      (a, v) => `Auto topples after swerving at ${v.lm}`,
      (a, v) => `Auto accident reported at ${v.loc}`,
    ],
    descs: [
      (a, v) => `A share auto carrying ${num(a.persons)} passengers overturned near ${v.lm}, ${v.loc} ${v.t}; ${cas(a)}.`,
      (a, v) => `An autorickshaw swerved and toppled on ${v.lm} in ${v.loc} ${v.t}${wet(a)}. ${cap(cas(a))}.`,
      (a, v) => `Auto overturned at ${v.loc} ${v.t} with ${num(a.persons)} on board; ${cas(a)}.`,
    ],
  },
];

const LOOT = ['gold jewellery', 'cash and jewellery', 'a laptop and cash', 'silver articles', 'cash'];
const THEFT_BURGLARY: Subtype[] = [
  {
    key: 'house', weight: () => 3,
    build: (c) => A({ persons: c.rng.int(1, 5), vulnerable: c.rng.chance(0.1), weapon: c.rng.chance(0.03), x: { loot: c.rng.pick(LOOT) } }),
    titles: [
      (a, v) => `House burgled in ${v.loc}`,
      (a, v) => `Locked house broken into near ${v.lm}`,
      (a, v) => `Burglary reported at residence in ${v.loc}`,
      (a, v) => `${cap(a.x.loot)} stolen from house in ${v.loc}`,
    ],
    descs: [
      (a, v) => `Unidentified persons broke into a locked house near ${v.lm}, ${v.loc} ${v.t} and took ${a.x.loot}.`,
      (a, v) => `Residents in ${v.loc} found the front door lock broken and ${a.x.loot} missing; entry is estimated ${v.t}.`,
      (a, v) => `Burglary at a house on ${v.lm}, ${v.loc}: ${a.x.loot} reported stolen, entry ${v.t}.`,
    ],
  },
  {
    key: 'shop', weight: (c) => (c.crowdHotspot ? 2.5 : 1.5),
    build: (c) => A({ persons: c.rng.int(1, 2), weapon: c.rng.chance(0.03), x: { shop: c.rng.pick(['mobile shop', 'textile shop', 'grocery store', 'jewellery shop', 'medical shop']) } }),
    titles: [
      (a, v) => `${cap(a.x.shop)} shutter broken, cash stolen in ${v.loc}`,
      (a, v) => `Theft at ${a.x.shop} near ${v.lm}`,
      (a, v) => `Shop burglary reported at ${v.loc}`,
      (a, v) => `${cap(a.x.shop)} looted overnight in ${v.loc}`,
    ],
    descs: [
      (a, v) => `The shutter of a ${a.x.shop} near ${v.lm}, ${v.loc} was pried open ${v.t} and cash was taken from the counter.`,
      (a, v) => `Owner of a ${a.x.shop} in ${v.loc} reported a break-in ${v.t}; cash and goods missing.`,
      (a, v) => `Theft reported at a ${a.x.shop} on ${v.lm}, ${v.loc} ${v.t}; CCTV footage is being checked.`,
    ],
  },
  {
    key: 'vehicle', weight: () => 2.5,
    build: (c) => A({ persons: 1, x: { veh: c.rng.pick(['motorcycle', 'scooter', 'two-wheeler']) } }),
    titles: [
      (a, v) => `${cap(a.x.veh)} stolen near ${v.lm}`,
      (a, v) => `Two-wheeler theft reported in ${v.loc}`,
      (a, v) => `Parked ${a.x.veh} stolen at ${v.loc}`,
      (a, v) => `Vehicle theft near ${v.lm}, ${v.loc}`,
    ],
    descs: [
      (a, v) => `A ${a.x.veh} parked near ${v.lm}, ${v.loc} was stolen ${v.t}.`,
      (a, v) => `Owner reported that a ${a.x.veh} left outside a house in ${v.loc} went missing ${v.t}.`,
      (a, v) => `Parked ${a.x.veh} stolen on ${v.lm} in ${v.loc} ${v.t}; nearby CCTV is being checked.`,
    ],
  },
  {
    key: 'phone', weight: (c) => (c.crowdHotspot || c.festival ? 3 : 1.5),
    build: (c) => A({ persons: 1, vulnerable: c.rng.chance(0.05) }),
    titles: [
      (a, v) => `Mobile phone stolen in crowd at ${v.lm}`,
      (a, v) => `Phone theft reported at ${v.loc}`,
      (a, v) => `Pickpocket steals phone near ${v.lm}`,
      (a, v) => `Mobile phone lifted from commuter in ${v.loc}`,
    ],
    descs: [
      (a, v) => `A mobile phone was stolen from a shopper in the crowd at ${v.lm}, ${v.loc} ${v.t}.`,
      (a, v) => `Commuter reported a phone lifted from a bag near ${v.lm} in ${v.loc} ${v.t}.`,
      (a, v) => `Phone theft reported at ${v.loc} ${v.t}; the victim noticed it missing after boarding a bus.`,
    ],
  },
];

const CHAIN_SNATCHING: Subtype[] = [
  {
    key: 'chain', weight: () => 1,
    build: (c) => {
      const vulnerable = c.rng.chance(0.85);
      return A({
        persons: 1, injured: c.rng.chance(0.25) ? 1 : 0, vulnerable, weapon: c.rng.chance(0.05),
        x: {
          victim: vulnerable ? c.rng.pick(['a woman', 'an elderly woman', 'a woman walking home', 'an elderly man']) : 'a man',
          sov: String(c.rng.pick([2, 3, 4, 5, 6, 8])),
        },
      });
    },
    titles: [
      (a, v) => `Chain snatched from pedestrian near ${v.lm}`,
      (a, v) => `Bike-borne duo snatch gold chain in ${v.loc}`,
      (a, v) => `Chain snatching reported at ${v.loc}`,
      (a, v) => `Gold chain snatched on ${v.lm}, ${v.loc}`,
    ],
    descs: [
      (a, v) => `Two men on a motorcycle snatched a ${a.x.sov}-sovereign gold chain from ${a.x.victim} near ${v.lm}, ${v.loc} ${v.t}; ${a.injured ? 'the victim suffered minor injuries' : 'the victim was not injured'}.`,
      (a, v) => `${cap(a.x.victim)} lost a ${a.x.sov}-sovereign chain to bike-borne snatchers in ${v.loc} ${v.t}.`,
      (a, v) => `Chain snatching near ${v.lm} (${v.loc}) ${v.t}: ${a.x.sov} sovereigns taken from ${a.x.victim}${a.injured ? ', who was hurt when pulled down' : ''}.`,
    ],
  },
];

const TRAFFIC_OBSTRUCTION: Subtype[] = [
  {
    key: 'breakdown', weight: (c) => 1.5 + 4 * c.heavyShare,
    build: (c) => A({ roadBlocked: true, blockage: round5(c.rng.int(20, 150)), x: { veh: c.rng.pick(HEAVY_VEHICLES) } }),
    titles: [
      (a, v) => `Broken-down ${a.x.veh} blocks ${v.lm}`,
      (a, v) => `${cap(a.x.veh)} breakdown causes congestion in ${v.loc}`,
      (a, v) => `Traffic snarl after ${a.x.veh} breaks down at ${v.loc}`,
      (a, v) => `Vehicle breakdown obstructs traffic near ${v.lm}`,
    ],
    descs: [
      (a, v) => `A ${a.x.veh} broke down on ${v.lm}, ${v.loc} ${v.t}; ${traffic(a)}.`,
      (a, v) => `Stalled ${a.x.veh} blocked one lane near ${v.lm} in ${v.loc} ${v.t}. Traffic was held up for about ${a.blockage} minutes.`,
      (a, v) => `Congestion reported at ${v.loc} ${v.t} after a ${a.x.veh} broke down; ${traffic(a)}.`,
    ],
  },
  {
    key: 'waterlogging', weight: (c) => (c.rain ? 5 : 0.3),
    build: (c) => A({ roadBlocked: true, blockage: round5(c.rain ? c.rng.int(60, 360) : c.rng.int(30, 120)), weather: c.rain, x: { cause: c.rain ? 'heavy rain' : 'a burst water pipeline' } }),
    titles: [
      (a, v) => `Waterlogging blocks ${v.lm}`,
      (a, v) => `Road inundated near ${v.lm}, ${v.loc}`,
      (a, v) => `Stagnant water halts traffic in ${v.loc}`,
      (a, v) => `Flooded stretch at ${v.loc} disrupts traffic`,
    ],
    descs: [
      (a, v) => `Knee-deep water caused by ${a.x.cause} stagnated on ${v.lm}, ${v.loc} from ${v.t}; ${traffic(a)}.`,
      (a, v) => `Road near ${v.lm} in ${v.loc} was inundated after ${a.x.cause}, reported ${v.t}. Vehicles were diverted for about ${a.blockage} minutes.`,
      (a, v) => `Waterlogging due to ${a.x.cause} at ${v.loc} ${v.t}; ${traffic(a)}.`,
    ],
  },
  {
    key: 'tree', weight: (c) => (c.rain ? 3 : 0.3),
    build: (c) => A({ roadBlocked: true, blockage: round5(c.rng.int(30, 240)), weather: true, x: { cause: c.rain ? 'heavy rain' : 'strong winds' } }),
    titles: [
      (a, v) => `Uprooted tree blocks ${v.lm}`,
      (a, v) => `Tree falls across road in ${v.loc}`,
      (a, v) => `Fallen tree disrupts traffic near ${v.lm}`,
      (a, v) => `Road blocked by fallen tree at ${v.loc}`,
    ],
    descs: [
      (a, v) => `A tree uprooted by ${a.x.cause} fell across ${v.lm}, ${v.loc} ${v.t}; ${traffic(a)}.`,
      (a, v) => `Tree fall reported on ${v.lm} in ${v.loc} ${v.t} after ${a.x.cause}. No one was hurt; road cleared after about ${a.blockage} minutes.`,
      (a, v) => `Fallen tree blocked the road at ${v.loc} ${v.t}; ${traffic(a)}.`,
    ],
  },
  {
    key: 'parking', weight: () => 2.5,
    build: (c) => A({ roadBlocked: true, blockage: round5(c.rng.int(15, 60)) }),
    titles: [
      (a, v) => `Illegal parking chokes ${v.lm}`,
      (a, v) => `Haphazardly parked vehicles block road in ${v.loc}`,
      (a, v) => `Traffic obstruction due to parking near ${v.lm}`,
      (a, v) => `Encroachment and parking cause jam at ${v.loc}`,
    ],
    descs: [
      (a, v) => `Vehicles parked on both sides of ${v.lm}, ${v.loc} blocked movement ${v.t}; ${traffic(a)}.`,
      (a, v) => `Complaint of illegal parking obstructing traffic near ${v.lm} in ${v.loc} ${v.t}. Vehicles were cleared after about ${a.blockage} minutes.`,
      (a, v) => `Road at ${v.loc} was choked by parked vehicles ${v.t}; ${traffic(a)}.`,
    ],
  },
  {
    key: 'signal', weight: () => 1.5,
    build: (c) => A({ roadBlocked: true, blockage: round5(c.rng.int(20, 120)) }),
    titles: [
      (a, v) => `Signal failure causes jam at ${v.lm}`,
      (a, v) => `Traffic signal not working in ${v.loc}`,
      (a, v) => `Snarl at junction near ${v.lm} after signal breakdown`,
      (a, v) => `Junction gridlock reported at ${v.loc}`,
    ],
    descs: [
      (a, v) => `Traffic signal near ${v.lm}, ${v.loc} stopped working ${v.t}; ${traffic(a)}.`,
      (a, v) => `Signal breakdown at the junction in ${v.loc} ${v.t} caused a jam; police regulated traffic for about ${a.blockage} minutes.`,
      (a, v) => `Gridlock at ${v.loc} ${v.t} due to a non-functional signal; ${traffic(a)}.`,
    ],
  },
];

const PUBLIC_NUISANCE: Subtype[] = [
  {
    key: 'music', weight: () => 2, build: () => A({ persons: 0 }),
    titles: [
      (a, v) => `Loud music complaint in ${v.loc}`,
      (a, v) => `Late-night noise disturbance near ${v.lm}`,
      (a, v) => `Residents complain of loudspeaker noise in ${v.loc}`,
      (a, v) => `Noise nuisance reported at ${v.loc}`,
    ],
    descs: [
      (a, v) => `Residents near ${v.lm}, ${v.loc} complained of loud music ${v.t}.`,
      (a, v) => `Loudspeakers played past permitted hours in ${v.loc} ${v.t}; patrol asked organisers to stop.`,
      (a, v) => `Noise complaint from ${v.loc} ${v.t} about a function using loudspeakers near ${v.lm}.`,
    ],
  },
  {
    key: 'brawl', weight: () => 2,
    build: (c) => { const injured = c.rng.chance(0.2) ? 1 : 0; return A({ persons: Math.max(injured, c.rng.int(2, 4)), injured, ...block(c, 0.03, 10, 30) }); },
    titles: [
      (a, v) => `Drunken brawl near ${v.lm}`,
      (a, v) => `Drunk men create ruckus in ${v.loc}`,
      (a, v) => `Scuffle outside liquor outlet in ${v.loc}`,
      (a, v) => `Public disturbance by drunk group at ${v.lm}`,
    ],
    descs: [
      (a, v) => `A group of drunk men quarrelled near ${v.lm}, ${v.loc} ${v.t}; ${cas(a)}.`,
      (a, v) => `Ruckus by intoxicated men outside a liquor outlet in ${v.loc} ${v.t}. ${cap(cas(a))}.`,
      (a, v) => `Scuffle among ${num(a.persons)} men near ${v.lm} (${v.loc}) ${v.t}; ${cas(a)}.`,
    ],
  },
  {
    key: 'drinking', weight: () => 1.5, build: () => A({ persons: 0 }),
    titles: [
      (a, v) => `Public drinking reported near ${v.lm}`,
      (a, v) => `Residents object to open drinking in ${v.loc}`,
      (a, v) => `Drinking in public place at ${v.loc}`,
      (a, v) => `Nuisance by group drinking on ${v.lm}`,
    ],
    descs: [
      (a, v) => `Men were found drinking in public near ${v.lm}, ${v.loc} ${v.t}.`,
      (a, v) => `Residents of ${v.loc} complained of open drinking on the street ${v.t}.`,
      (a, v) => `Group drinking in a public place near ${v.lm} (${v.loc}) reported ${v.t}.`,
    ],
  },
  {
    key: 'crowd', weight: (c) => (c.festival ? 3 : c.crowdHotspot ? 1.5 : 0.6),
    build: (c) => A({ crowd: round10(c.rng.int(30, c.festival ? 800 : 300)), ...block(c, 0.4, 15, 60) }),
    titles: [
      (a, v) => `Unruly crowd gathers near ${v.lm}`,
      (a, v) => `Crowd causes disturbance in ${v.loc}`,
      (a, v) => `Large gathering blocks footpath at ${v.lm}`,
      (a, v) => `Crowd control needed at ${v.loc}`,
    ],
    descs: [
      (a, v) => `About ${a.crowd} people gathered near ${v.lm}, ${v.loc} ${v.t}, causing disturbance; ${traffic(a)}.`,
      (a, v) => `An unruly crowd of roughly ${a.crowd} formed in ${v.loc} ${v.t}. ${cap(traffic(a))}.`,
      (a, v) => `Gathering of around ${a.crowd} people at ${v.lm} (${v.loc}) reported ${v.t}.`,
    ],
  },
];

function cyber(c: BuildCtx, min: number, max: number, pElderly: number): Attrs {
  const many = c.rng.chance(0.05);
  const amt = Math.round(c.rng.float(min, max) / 500) * 500;
  return A({ persons: many ? c.rng.int(2, 6) : 1, vulnerable: c.rng.chance(pElderly), x: { amt: rs(amt) } });
}
const CYBER_FRAUD: Subtype[] = [
  {
    key: 'otp', weight: () => 3, build: (c) => cyber(c, 5000, 150000, 0.35),
    titles: [
      (a, v) => `Resident of ${v.loc} loses ${a.x.amt} in OTP fraud`,
      (a, v) => `Fake bank call: ${a.x.amt} siphoned from ${v.loc} resident`,
      (a, v) => `OTP scam reported in ${v.loc}`,
      (a, v) => `Bank account fraud complaint from ${v.loc}`,
    ],
    descs: [
      (a, v) => `A resident of ${v.loc} lost ${a.x.amt} after sharing an OTP with a caller posing as a bank official ${v.t}.`,
      (a, v) => `Caller claiming to be from a bank obtained card details and an OTP from a ${v.loc} resident ${v.t}; ${a.x.amt} was debited.`,
      (a, v) => `OTP fraud: ${a.x.amt} withdrawn from the account of a person in ${v.loc}, call received ${v.t}.`,
    ],
  },
  {
    key: 'upi', weight: () => 2.5, build: (c) => cyber(c, 1000, 60000, 0.15),
    titles: [
      (a, v) => `UPI fraud: ${v.loc} trader cheated of ${a.x.amt}`,
      (a, v) => `Fake payment QR scam in ${v.loc}`,
      (a, v) => `UPI collect-request scam reported at ${v.loc}`,
      (a, v) => `Online payment fraud complaint from ${v.loc}`,
    ],
    descs: [
      (a, v) => `A shop owner in ${v.loc} lost ${a.x.amt} after approving a fake UPI collect request ${v.t}.`,
      (a, v) => `Fraudsters sent a fake QR code to a ${v.loc} resident selling goods online; ${a.x.amt} was debited ${v.t}.`,
      (a, v) => `UPI scam reported from ${v.loc}: ${a.x.amt} lost through a payment request, ${v.t}.`,
    ],
  },
  {
    key: 'job', weight: () => 2, build: (c) => cyber(c, 10000, 400000, 0.03),
    titles: [
      (a, v) => `Part-time job scam: ${v.loc} resident loses ${a.x.amt}`,
      (a, v) => `Online task fraud reported in ${v.loc}`,
      (a, v) => `Work-from-home offer turns into ${a.x.amt} fraud`,
      (a, v) => `Job fraud complaint from ${v.loc}`,
    ],
    descs: [
      (a, v) => `A ${v.loc} resident paid ${a.x.amt} in instalments to an online 'task' job scheme that stopped responding; first contact ${v.t}.`,
      (a, v) => `Messaging-app offer of part-time work led a person in ${v.loc} to transfer ${a.x.amt}, reported ${v.t}.`,
      (a, v) => `Job fraud: victim from ${v.loc} lost ${a.x.amt} after paying 'registration' and 'task' fees.`,
    ],
  },
  {
    key: 'digital_arrest', weight: () => 1, build: (c) => cyber(c, 100000, 2500000, 0.6),
    titles: [
      (a, v) => `'Digital arrest' scam: ${v.loc} resident loses ${a.x.amt}`,
      (a, v) => `Fake police video call cheats ${v.loc} resident`,
      (a, v) => `Impersonation fraud of ${a.x.amt} in ${v.loc}`,
      (a, v) => `Digital arrest fraud reported at ${v.loc}`,
    ],
    descs: [
      (a, v) => `Callers posing as investigators kept a ${v.loc} resident on a video call and extorted ${a.x.amt} ${v.t}.`,
      (a, v) => `A person in ${v.loc} transferred ${a.x.amt} after a fake 'digital arrest' call claiming a parcel had contraband.`,
      (a, v) => `Impersonation fraud from ${v.loc}: ${a.x.amt} moved to fraudsters' accounts after threats, call ${v.t}.`,
    ],
  },
  {
    key: 'investment', weight: () => 1.5, build: (c) => cyber(c, 50000, 1500000, 0.15),
    titles: [
      (a, v) => `Stock-trading app fraud: ${a.x.amt} lost in ${v.loc}`,
      (a, v) => `Investment scam reported in ${v.loc}`,
      (a, v) => `Fake trading group cheats ${v.loc} resident`,
      (a, v) => `Online investment fraud complaint from ${v.loc}`,
    ],
    descs: [
      (a, v) => `A resident of ${v.loc} invested ${a.x.amt} through a fake trading app promoted in a messaging group.`,
      (a, v) => `Investment fraud: ${a.x.amt} lost by a ${v.loc} resident lured with high returns; last transfer ${v.t}.`,
      (a, v) => `Victim from ${v.loc} reported ${a.x.amt} lost to a bogus stock-tips group.`,
    ],
  },
];

function assault(c: BuildCtx, pWeapon: number, min: number, max: number, pBlock: number): Attrs {
  const persons = c.rng.int(min, max);
  return A({
    persons, injured: c.rng.int(1, Math.max(1, persons - 1)), weapon: c.rng.chance(pWeapon),
    vulnerable: c.rng.chance(0.1), ...block(c, pBlock, 10, 40),
    x: { weapon: c.rng.pick(['a knife', 'a sickle', 'an iron rod', 'a wooden log', 'a broken bottle']) },
  });
}
const wpn = (a: Attrs) => (a.weapon ? ` with ${a.x.weapon}` : '');
const ASSAULT: Subtype[] = [
  {
    key: 'quarrel', weight: () => 3, build: (c) => assault(c, 0.35, 1, 3, 0.02),
    titles: [
      (a, v) => `Man attacked after quarrel in ${v.loc}`,
      (a, v) => `Assault reported near ${v.lm}`,
      (a, v) => `Quarrel turns violent at ${v.loc}`,
      (a, v) => `Youth assaulted near ${v.lm}, ${v.loc}`,
    ],
    descs: [
      (a, v) => `A quarrel near ${v.lm}, ${v.loc} turned violent ${v.t}; the victim was attacked${wpn(a)} and ${cas(a)}.`,
      (a, v) => `Assault reported in ${v.loc} ${v.t} following a verbal dispute. ${cap(cas(a))}.`,
      (a, v) => `Person attacked${wpn(a)} at ${v.lm} (${v.loc}) ${v.t}; ${cas(a)}.`,
    ],
  },
  {
    key: 'road_rage', weight: () => 1.5, build: (c) => assault(c, 0.2, 2, 3, 0.2),
    titles: [
      (a, v) => `Road rage: motorist assaulted on ${v.lm}`,
      (a, v) => `Road-rage attack reported in ${v.loc}`,
      (a, v) => `Drivers come to blows at ${v.lm}`,
      (a, v) => `Traffic dispute ends in assault at ${v.loc}`,
    ],
    descs: [
      (a, v) => `A minor brush between vehicles on ${v.lm}, ${v.loc} led to an assault ${v.t}; ${cas(a)}.`,
      (a, v) => `Road-rage incident in ${v.loc} ${v.t}: a motorist was attacked${wpn(a)}. ${cap(cas(a))}; ${traffic(a)}.`,
      (a, v) => `Two drivers fought after a traffic dispute at ${v.loc} ${v.t}; ${cas(a)}.`,
    ],
  },
  {
    key: 'neighbour', weight: () => 1.5, build: (c) => assault(c, 0.25, 2, 4, 0),
    titles: [
      (a, v) => `Neighbours clash over dispute in ${v.loc}`,
      (a, v) => `Family dispute turns violent in ${v.loc}`,
      (a, v) => `Assault in neighbourhood quarrel near ${v.lm}`,
      (a, v) => `Clash between neighbours reported at ${v.loc}`,
    ],
    descs: [
      (a, v) => `A dispute over a water connection between neighbours in ${v.loc} turned violent ${v.t}; ${cas(a)}.`,
      (a, v) => `Neighbours clashed near ${v.lm}, ${v.loc} ${v.t}; one side attacked${wpn(a)}. ${cap(cas(a))}.`,
      (a, v) => `Quarrel between two families in ${v.loc} led to an assault ${v.t}; ${cas(a)}.`,
    ],
  },
  {
    key: 'gang', weight: () => 0.8, build: (c) => assault(c, 0.8, 3, 6, 0.15),
    titles: [
      (a, v) => `Gang clash reported near ${v.lm}`,
      (a, v) => `Group attack in ${v.loc}`,
      (a, v) => `Rival groups clash at ${v.loc}`,
      (a, v) => `Armed group assaults youths in ${v.loc}`,
    ],
    descs: [
      (a, v) => `Two groups clashed near ${v.lm}, ${v.loc} ${v.t}${a.weapon ? ', some armed with ' + a.x.weapon : ''}; ${cas(a)}.`,
      (a, v) => `A group attacked youths in ${v.loc} ${v.t} over previous enmity. ${cap(cas(a))}; ${traffic(a)}.`,
      (a, v) => `Clash between rival groups at ${v.loc} ${v.t}; ${cas(a)}.`,
    ],
  },
];

const CRIMES_AGAINST_WOMEN: Subtype[] = [
  {
    key: 'harassment', weight: () => 2.5,
    build: (c) => A({ persons: 1, injured: c.rng.chance(0.1) ? 1 : 0, vulnerable: true, weapon: c.rng.chance(0.05) }),
    titles: [
      (a, v) => `Woman harassed near ${v.lm}`,
      (a, v) => `Harassment of woman reported in ${v.loc}`,
      (a, v) => `Man misbehaves with woman at ${v.loc}`,
      (a, v) => `Complaint of sexual harassment near ${v.lm}`,
    ],
    descs: [
      (a, v) => `A woman was harassed by a man near ${v.lm}, ${v.loc} ${v.t}; ${cas(a)}.`,
      (a, v) => `Woman complained that a man misbehaved with her while she walked in ${v.loc} ${v.t}.`,
      (a, v) => `Harassment of a woman reported at ${v.loc} ${v.t}; the accused fled before patrol arrived.`,
    ],
  },
  {
    key: 'stalking', weight: () => 1.5,
    build: () => A({ persons: 1, vulnerable: true }),
    titles: [
      (a, v) => `Woman stalked in ${v.loc}, complaint lodged`,
      (a, v) => `Stalking complaint from ${v.loc}`,
      (a, v) => `College student followed near ${v.lm}`,
      (a, v) => `Repeated stalking reported in ${v.loc}`,
    ],
    descs: [
      (a, v) => `A woman from ${v.loc} reported being followed repeatedly by a man near ${v.lm}; latest incident ${v.t}.`,
      (a, v) => `A college student complained of being stalked on her way home in ${v.loc} ${v.t}.`,
      (a, v) => `Stalking complaint: a woman in ${v.loc} was followed and threatened ${v.t}.`,
    ],
  },
  {
    key: 'domestic', weight: () => 2,
    build: (c) => A({ persons: 1, injured: c.rng.chance(0.6) ? 1 : 0, vulnerable: true, weapon: c.rng.chance(0.15) }),
    titles: [
      (a, v) => `Domestic violence complaint in ${v.loc}`,
      (a, v) => `Woman assaulted by husband in ${v.loc}`,
      (a, v) => `Dowry harassment complaint from ${v.loc}`,
      (a, v) => `Domestic abuse reported at ${v.loc}`,
    ],
    descs: [
      (a, v) => `A woman in ${v.loc} reported being assaulted by her husband ${v.t}; ${cas(a)}.`,
      (a, v) => `Domestic violence complaint from ${v.loc}: woman beaten at home ${v.t}. ${cap(cas(a))}.`,
      (a, v) => `Woman from ${v.loc} alleged harassment for dowry and assault ${v.t}; ${cas(a)}.`,
    ],
  },
  {
    key: 'bus', weight: () => 1,
    build: () => A({ persons: 1, vulnerable: true }),
    titles: [
      (a, v) => `Woman harassed on MTC bus near ${v.lm}`,
      (a, v) => `Harassment on bus reported at ${v.loc}`,
      (a, v) => `Commuter misbehaves with woman passenger in ${v.loc}`,
      (a, v) => `Bus harassment complaint near ${v.lm}`,
    ],
    descs: [
      (a, v) => `A woman passenger was harassed on an MTC bus near ${v.lm}, ${v.loc} ${v.t}; the man was handed over to police.`,
      (a, v) => `Woman complained of misbehaviour by a co-passenger on a bus in ${v.loc} ${v.t}.`,
      (a, v) => `Harassment on a crowded bus reported at ${v.loc} ${v.t}.`,
    ],
  },
];

function missing(who: string, vulnerable: boolean): (c: BuildCtx) => Attrs {
  return () => A({ persons: 1, vulnerable, x: { who } });
}
const missingTpl: Pick<Subtype, 'titles' | 'descs'> = {
  titles: [
    (a, v) => `${cap(a.x.who)} missing from ${v.loc}`,
    (a, v) => `Missing person complaint: ${a.x.who} from ${v.loc}`,
    (a, v) => `Search on for ${a.x.who} last seen near ${v.lm}`,
    (a, v) => `${cap(a.x.who)} reported missing in ${v.loc}`,
  ],
  descs: [
    (a, v) => `Family reported that ${a.x.who} left home in ${v.loc} ${v.t} and has not returned.`,
    (a, v) => `${cap(a.x.who)} was last seen near ${v.lm}, ${v.loc} ${v.t}; relatives lodged a complaint.`,
    (a, v) => `Missing person case: ${a.x.who} from ${v.loc}, last seen ${v.t} near ${v.lm}.`,
  ],
};
const MISSING_PERSON: Subtype[] = [
  { key: 'child', weight: () => 2, build: missing('a schoolboy', true), ...missingTpl },
  { key: 'girl', weight: () => 1.5, build: missing('a teenage girl', true), ...missingTpl },
  { key: 'elderly', weight: () => 2, build: missing('an elderly man with memory loss', true), ...missingTpl },
  { key: 'woman', weight: () => 1.5, build: missing('a young woman', true), ...missingTpl },
  { key: 'youth', weight: () => 1.5, build: missing('a college student', false), ...missingTpl },
];

const GROUPS = ['residents', 'traders', 'transport workers', 'fishermen', 'students', 'party cadres', 'contract workers', 'auto drivers'];
const CAUSES = ['irregular water supply', 'poor road conditions', 'demand for a subway', 'wage demands', 'eviction notices', 'garbage dumping', 'a power cut'];
function protest(c: BuildCtx, min: number, max: number, pBlock: number, clash: boolean): Attrs {
  const injured = clash ? c.rng.int(1, 6) : c.rng.chance(0.05) ? 1 : 0;
  return A({
    persons: injured, injured, weapon: clash && c.rng.chance(0.15), crowd: round10(c.rng.int(min, max)),
    ...block(c, pBlock, 20, 240), x: { group: c.rng.pick(GROUPS), cause: c.rng.pick(CAUSES) },
  });
}
const PROTEST_LAW_AND_ORDER: Subtype[] = [
  {
    key: 'road_roko', weight: () => 3, build: (c) => protest(c, 30, 600, 0.9, false),
    titles: [
      (a, v) => `Road roko by ${a.x.group} at ${v.lm}`,
      (a, v) => `${cap(a.x.group)} block road in ${v.loc} over ${a.x.cause}`,
      (a, v) => `Road blockade protest in ${v.loc}`,
      (a, v) => `Traffic halted as ${a.x.group} stage road roko`,
    ],
    descs: [
      (a, v) => `About ${a.crowd} ${a.x.group} blocked ${v.lm}, ${v.loc} ${v.t} protesting ${a.x.cause}; ${traffic(a)}.`,
      (a, v) => `Road roko in ${v.loc} ${v.t} by roughly ${a.crowd} ${a.x.group} over ${a.x.cause}. ${cap(traffic(a))}.`,
      (a, v) => `${cap(a.x.group)} (around ${a.crowd}) staged a road blockade near ${v.lm} ${v.t}; ${cas(a)}.`,
    ],
  },
  {
    key: 'demonstration', weight: () => 3, build: (c) => protest(c, 50, 1500, 0.3, false),
    titles: [
      (a, v) => `${cap(a.x.group)} stage demonstration near ${v.lm}`,
      (a, v) => `Protest over ${a.x.cause} in ${v.loc}`,
      (a, v) => `Demonstration held at ${v.loc}`,
      (a, v) => `${cap(a.x.group)} protest in ${v.loc}`,
    ],
    descs: [
      (a, v) => `Around ${a.crowd} ${a.x.group} held a demonstration near ${v.lm}, ${v.loc} ${v.t} over ${a.x.cause}; ${traffic(a)}.`,
      (a, v) => `Demonstration by ${a.x.group} in ${v.loc} ${v.t}; crowd estimated at ${a.crowd}. ${cap(cas(a))}.`,
      (a, v) => `${cap(a.x.group)} gathered at ${v.loc} ${v.t} raising slogans on ${a.x.cause}; about ${a.crowd} took part.`,
    ],
  },
  {
    key: 'clash', weight: () => 0.8, build: (c) => protest(c, 30, 400, 0.6, true),
    titles: [
      (a, v) => `Protest turns violent near ${v.lm}`,
      (a, v) => `Clash during agitation in ${v.loc}`,
      (a, v) => `Stones thrown during protest at ${v.loc}`,
      (a, v) => `Law-and-order situation at ${v.loc} after clash`,
    ],
    descs: [
      (a, v) => `A protest by ${a.x.group} near ${v.lm}, ${v.loc} turned violent ${v.t}; ${cas(a)} and ${traffic(a)}.`,
      (a, v) => `Clash broke out during an agitation over ${a.x.cause} in ${v.loc} ${v.t}; crowd of about ${a.crowd}. ${cap(cas(a))}.`,
      (a, v) => `Stone pelting reported at a protest in ${v.loc} ${v.t}; ${cas(a)}.`,
    ],
  },
  {
    key: 'rally', weight: () => 1.5, build: (c) => protest(c, 200, 3000, 0.7, false),
    titles: [
      (a, v) => `Rally by ${a.x.group} passes through ${v.loc}`,
      (a, v) => `Procession slows traffic near ${v.lm}`,
      (a, v) => `Political rally in ${v.loc}`,
      (a, v) => `Large procession reported at ${v.loc}`,
    ],
    descs: [
      (a, v) => `A rally of about ${a.crowd} ${a.x.group} moved through ${v.loc} ${v.t}; ${traffic(a)}.`,
      (a, v) => `Procession near ${v.lm}, ${v.loc} ${v.t} with roughly ${a.crowd} participants. ${cap(traffic(a))}.`,
      (a, v) => `Rally by ${a.x.group} reached ${v.loc} ${v.t}; crowd of around ${a.crowd}.`,
    ],
  },
];

const DRUGS_ILLICIT_LIQUOR: Subtype[] = [
  {
    key: 'ganja', weight: () => 3,
    build: (c) => A({ weapon: c.rng.chance(0.03), x: { qty: `${c.rng.pick([1, 2, 3, 5, 8, 12])} kg` } }),
    titles: [
      (a, v) => `${a.x.qty} ganja seized in ${v.loc}`,
      (a, v) => `Ganja peddling busted near ${v.lm}`,
      (a, v) => `Narcotics seizure in ${v.loc}`,
      (a, v) => `Two held with ganja in ${v.loc}`,
    ],
    descs: [
      (a, v) => `Police seized ${a.x.qty} of ganja from two men near ${v.lm}, ${v.loc} ${v.t}.`,
      (a, v) => `Ganja weighing ${a.x.qty} recovered during a vehicle check in ${v.loc} ${v.t}.`,
      (a, v) => `Narcotics raid at ${v.loc} ${v.t}: ${a.x.qty} ganja seized, suspects held.`,
    ],
  },
  {
    key: 'liquor', weight: () => 2,
    build: (c) => A({ x: { qty: `${c.rng.pick([20, 40, 60, 100, 150])} litres` } }),
    titles: [
      (a, v) => `Illicit liquor seized in ${v.loc}`,
      (a, v) => `Illegal liquor sale busted near ${v.lm}`,
      (a, v) => `${a.x.qty} of illicit arrack seized at ${v.loc}`,
      (a, v) => `Raid on illicit liquor outlet in ${v.loc}`,
    ],
    descs: [
      (a, v) => `Police seized ${a.x.qty} of illicit liquor stored in a shed near ${v.lm}, ${v.loc} ${v.t}.`,
      (a, v) => `Illegal sale of liquor outside permitted hours raided in ${v.loc} ${v.t}; ${a.x.qty} seized.`,
      (a, v) => `Raid at ${v.loc} ${v.t} recovered ${a.x.qty} of illicit arrack.`,
    ],
  },
  {
    key: 'tablets', weight: () => 1,
    build: (c) => A({ x: { qty: `${c.rng.pick([50, 100, 200, 500])} tablets` } }),
    titles: [
      (a, v) => `Pain-killer tablets seized from youths in ${v.loc}`,
      (a, v) => `Drug tablets racket busted near ${v.lm}`,
      (a, v) => `Narcotic tablets seized at ${v.loc}`,
      (a, v) => `Youths held with drug tablets in ${v.loc}`,
    ],
    descs: [
      (a, v) => `Police seized ${a.x.qty} used as intoxicants from youths near ${v.lm}, ${v.loc} ${v.t}.`,
      (a, v) => `Sale of narcotic tablets busted in ${v.loc} ${v.t}; ${a.x.qty} recovered.`,
      (a, v) => `${cap(a.x.qty)} seized during a check at ${v.loc} ${v.t}.`,
    ],
  },
  {
    key: 'spurious', weight: () => 0.1,
    build: (c) => { const injured = c.rng.int(2, 8); const fatalities = c.rng.chance(0.2) ? 1 : 0; return A({ persons: injured + fatalities, injured, fatalities }); },
    titles: [
      (a, v) => `Several fall ill after consuming spurious liquor in ${v.loc}`,
      (a, v) => `Spurious liquor suspected in ${v.loc}`,
      (a, v) => `Hospitalisations after illicit liquor in ${v.loc}`,
      (a, v) => `Suspected hooch illness at ${v.loc}`,
    ],
    descs: [
      (a, v) => `${cap(people(a.persons))} fell ill after consuming illicit liquor in ${v.loc} ${v.t}; ${cas(a)}.`,
      (a, v) => `Suspected spurious liquor case in ${v.loc}: ${cas(a)}; samples sent for testing.`,
      (a, v) => `Illness after drinking illicit arrack reported near ${v.lm}, ${v.loc} ${v.t}; ${cas(a)}.`,
    ],
  },
];

const WEATHER_EMERGENCY: Subtype[] = [
  {
    key: 'tree', weight: (c) => (c.rain ? 3 : 4),
    build: (c) => { const injured = c.rng.chance(0.2) ? c.rng.int(1, 2) : 0; return A({ persons: injured, injured, weather: true, ...block(c, 0.8, 30, 240), x: { cause: c.rain ? 'heavy rain' : 'gusty winds' } }); },
    titles: [
      (a, v) => `Tree falls on vehicles near ${v.lm}`,
      (a, v) => `Uprooted tree damages vehicles in ${v.loc}`,
      (a, v) => `Tree collapse during ${a.x.cause} at ${v.loc}`,
      (a, v) => `Tree fall emergency reported in ${v.loc}`,
    ],
    descs: [
      (a, v) => `A large tree uprooted by ${a.x.cause} fell on parked vehicles near ${v.lm}, ${v.loc} ${v.t}; ${cas(a)}, ${traffic(a)}.`,
      (a, v) => `Tree collapse in ${v.loc} ${v.t} due to ${a.x.cause}. ${cap(cas(a))}; fire service cleared the branches.`,
      (a, v) => `Tree fell across ${v.lm} (${v.loc}) ${v.t}; ${cas(a)}.`,
    ],
  },
  {
    key: 'stranded', weight: (c) => (c.rain ? 4 : 0.2),
    build: (c) => A({ persons: c.rng.int(5, 60), vulnerable: c.rng.chance(0.3), weather: true, ...block(c, 0.9, 60, 600) }),
    titles: [
      (a, v) => `Residents stranded as water enters homes in ${v.loc}`,
      (a, v) => `Flooding traps residents near ${v.lm}`,
      (a, v) => `Rescue under way in waterlogged ${v.loc}`,
      (a, v) => `Inundation strands families at ${v.loc}`,
    ],
    descs: [
      (a, v) => `Rainwater entered houses in ${v.loc} ${v.t}; about ${a.persons} residents were stranded and ${traffic(a)}.`,
      (a, v) => `Around ${a.persons} people trapped by waist-deep water near ${v.lm}, ${v.loc} ${v.t}; rescue teams deployed.`,
      (a, v) => `Flooding at ${v.loc} ${v.t} left roughly ${a.persons} residents stranded.`,
    ],
  },
  {
    key: 'wall', weight: (c) => (c.rain ? 1 : 0.2),
    build: (c) => { const persons = c.rng.int(1, 4); const fatalities = c.rng.chance(0.12) ? 1 : 0; return A({ persons, fatalities, injured: persons - fatalities, vulnerable: c.rng.chance(0.25), weather: true, ...block(c, 0.3, 30, 120) }); },
    titles: [
      (a, v) => `Compound wall collapses in rain at ${v.loc}`,
      (a, v) => `Wall collapse near ${v.lm}`,
      (a, v) => `Old building wall caves in at ${v.loc}`,
      (a, v) => `Structure collapse reported in ${v.loc}`,
    ],
    descs: [
      (a, v) => `A rain-soaked compound wall collapsed in ${v.loc} ${v.t}; ${cas(a)}.`,
      (a, v) => `Part of an old building's wall caved in near ${v.lm}, ${v.loc} ${v.t}. ${cap(cas(a))}.`,
      (a, v) => `Wall collapse at ${v.loc} ${v.t} after continuous rain; ${cas(a)}.`,
    ],
  },
  {
    key: 'electrocution', weight: (c) => (c.rain ? 0.8 : 0.3),
    build: (c) => { const fatalities = c.rng.chance(0.5) ? 1 : 0; return A({ persons: 1, fatalities, injured: 1 - fatalities, vulnerable: c.rng.chance(0.2), weather: true }); },
    titles: [
      (a, v) => `Electrocution from snapped wire in ${v.loc}`,
      (a, v) => `Live wire in waterlogged street at ${v.lm}`,
      (a, v) => `Person electrocuted in ${v.loc} during rain`,
      (a, v) => `Electric shock incident reported at ${v.loc}`,
    ],
    descs: [
      (a, v) => `A person stepped on a snapped live wire in a waterlogged street in ${v.loc} ${v.t}; ${cas(a)}.`,
      (a, v) => `Electrocution near ${v.lm}, ${v.loc} ${v.t} from an overhead wire that fell in the wind. ${cap(cas(a))}.`,
      (a, v) => `Electric shock incident at ${v.loc} ${v.t}; ${cas(a)}.`,
    ],
  },
];

function murder(c: BuildCtx, pWeapon: number, gang: boolean): Attrs {
  const fatalities = gang && c.rng.chance(0.1) ? 2 : 1;
  const injured = gang ? c.rng.int(0, 2) : c.rng.chance(0.1) ? 1 : 0;
  const vulnerable = c.rng.chance(0.15);
  return A({
    persons: fatalities + injured, fatalities, injured, weapon: c.rng.chance(pWeapon), vulnerable,
    x: { weapon: c.rng.pick(['a knife', 'a sickle', 'an iron rod', 'a machete']), victim: vulnerable ? c.rng.pick(['a woman', 'an elderly man', 'an elderly woman']) : 'a man' },
  });
}
const MURDER: Subtype[] = [
  {
    key: 'quarrel', weight: () => 3, build: (c) => murder(c, 0.9, false),
    titles: [
      (a, v) => `Murder after quarrel in ${v.loc}`,
      (a, v) => `Murder reported near ${v.lm}`,
      (a, v) => `Quarrel ends in murder at ${v.loc}`,
      (a, v) => `Homicide case registered in ${v.loc}`,
    ],
    descs: [
      (a, v) => `${cap(a.x.victim)} was killed${wpn(a)} following a quarrel near ${v.lm}, ${v.loc} ${v.t}; ${cas(a)}.`,
      (a, v) => `Murder in ${v.loc} ${v.t}: the victim was attacked after a dispute and died. ${cap(cas(a))}.`,
      (a, v) => `Police registered a murder case after a fatal attack at ${v.loc} ${v.t}; ${cas(a)}.`,
    ],
  },
  {
    key: 'body', weight: () => 1, build: (c) => murder(c, 0.6, false),
    titles: [
      (a, v) => `Body with injuries found near ${v.lm}`,
      (a, v) => `Suspected murder: body found in ${v.loc}`,
      (a, v) => `Person found dead with wounds at ${v.loc}`,
      (a, v) => `Murder probe after body found in ${v.loc}`,
    ],
    descs: [
      (a, v) => `The body of ${a.x.victim} with injury marks was found near ${v.lm}, ${v.loc}; death is estimated ${v.t}. ${cap(cas(a))}.`,
      (a, v) => `Residents of ${v.loc} alerted police to a body with wounds; murder case registered, time of death ${v.t}.`,
      (a, v) => `Suspected murder at ${v.loc}: ${a.x.victim} found dead with injuries, estimated ${v.t}.`,
    ],
  },
  {
    key: 'gang', weight: () => 1, build: (c) => murder(c, 1, true),
    titles: [
      (a, v) => `Gang attack turns fatal in ${v.loc}`,
      (a, v) => `Murder by armed gang near ${v.lm}`,
      (a, v) => `Revenge killing reported in ${v.loc}`,
      (a, v) => `Armed attack turns fatal at ${v.loc}`,
    ],
    descs: [
      (a, v) => `An armed gang attacked ${a.x.victim} near ${v.lm}, ${v.loc} ${v.t} over previous enmity; ${cas(a)}.`,
      (a, v) => `Murder in ${v.loc} ${v.t}: a group attacked with ${a.x.weapon} and fled. ${cap(cas(a))}.`,
      (a, v) => `Gang attack at ${v.loc} ${v.t} suspected to be a revenge killing; ${cas(a)}.`,
    ],
  },
];

const OTHER: Subtype[] = [
  {
    key: 'fire', weight: () => 2,
    build: (c) => { const injured = c.rng.chance(0.25) ? c.rng.int(1, 2) : 0; const fatalities = c.rng.chance(0.03) ? 1 : 0; return A({ persons: injured + fatalities + c.rng.int(0, 4), injured, fatalities, ...block(c, 0.2, 20, 90), x: { place: c.rng.pick(['a shop', 'a godown', 'a hut', 'a parked car', 'a transformer']) } }); },
    titles: [
      (a, v) => `Fire breaks out at ${a.x.place} in ${v.loc}`,
      (a, v) => `Fire accident near ${v.lm}`,
      (a, v) => `Blaze reported at ${v.loc}`,
      (a, v) => `Fire in ${a.x.place} near ${v.lm}, ${v.loc}`,
    ],
    descs: [
      (a, v) => `Fire broke out in ${a.x.place} near ${v.lm}, ${v.loc} ${v.t}; ${cas(a)}.`,
      (a, v) => `Fire service and police responded to a blaze in ${a.x.place} in ${v.loc} ${v.t}. ${cap(cas(a))}.`,
      (a, v) => `Blaze at ${v.loc} ${v.t} in ${a.x.place}; ${cas(a)} and ${traffic(a)}.`,
    ],
  },
  {
    key: 'bag', weight: () => 1,
    build: (c) => A({ ...block(c, 0.3, 15, 60) }),
    titles: [
      (a, v) => `Unattended bag triggers alert at ${v.lm}`,
      (a, v) => `Suspicious object found in ${v.loc}`,
      (a, v) => `Bomb squad checks abandoned bag at ${v.loc}`,
      (a, v) => `Security alert over unattended baggage near ${v.lm}`,
    ],
    descs: [
      (a, v) => `An unattended bag near ${v.lm}, ${v.loc} was checked by the bomb squad ${v.t}; nothing suspicious was found.`,
      (a, v) => `Suspicious object reported in ${v.loc} ${v.t}; area cordoned off and ${traffic(a)}.`,
      (a, v) => `Abandoned bag at ${v.loc} ${v.t} examined by police; it contained personal belongings.`,
    ],
  },
  {
    key: 'crowd_event', weight: (c) => (c.festival ? 5 : c.crowdHotspot ? 1.5 : 0.5),
    build: (c) => A({ crowd: round10(c.rng.int(200, c.festival ? 5000 : 1500)), ...block(c, 0.6, 30, 180), x: { what: c.festival ? 'festival' : c.rng.pick(['temple festival', 'shopping rush', 'public event', 'concert']) } }),
    titles: [
      (a, v) => `Heavy ${a.x.what} crowd at ${v.lm}`,
      (a, v) => `Crowd management deployed in ${v.loc}`,
      (a, v) => `Surge of visitors at ${v.lm}, ${v.loc}`,
      (a, v) => `Large crowd reported at ${v.loc}`,
    ],
    descs: [
      (a, v) => `An estimated ${a.crowd} people gathered at ${v.lm}, ${v.loc} for a ${a.x.what} ${v.t}; ${traffic(a)}.`,
      (a, v) => `Police deployed for crowd control at ${v.loc} ${v.t}; about ${a.crowd} people present for a ${a.x.what}.`,
      (a, v) => `${a.x.what === 'festival' ? 'Festival' : cap(a.x.what)} crowd of roughly ${a.crowd} at ${v.loc} ${v.t}. ${cap(traffic(a))}.`,
    ],
  },
  {
    key: 'cattle', weight: () => 1,
    build: (c) => A({ ...block(c, 0.5, 10, 40) }),
    titles: [
      (a, v) => `Stray cattle obstruct ${v.lm}`,
      (a, v) => `Cattle menace on road in ${v.loc}`,
      (a, v) => `Stray animals cause hazard at ${v.loc}`,
      (a, v) => `Complaint of cattle on carriageway near ${v.lm}`,
    ],
    descs: [
      (a, v) => `Stray cattle were squatting on ${v.lm}, ${v.loc} ${v.t}; ${traffic(a)}.`,
      (a, v) => `Cattle roaming on the main road in ${v.loc} ${v.t} posed a hazard to motorists.`,
      (a, v) => `Complaint of stray cattle on the carriageway at ${v.loc} ${v.t}; ${traffic(a)}.`,
    ],
  },
];

export const SUBTYPES: Record<Category, Subtype[]> = {
  ROAD_ACCIDENT, THEFT_BURGLARY, CHAIN_SNATCHING, TRAFFIC_OBSTRUCTION, PUBLIC_NUISANCE, CYBER_FRAUD, ASSAULT,
  CRIMES_AGAINST_WOMEN, MISSING_PERSON, PROTEST_LAW_AND_ORDER, DRUGS_ILLICIT_LIQUOR, WEATHER_EMERGENCY, MURDER, OTHER,
};

/** Render a title/description pair; different `variant` values give reworded text. */
export function render(sub: Subtype, a: Attrs, v: TextVars, variant: number): { title: string; description: string } {
  const title = sub.titles[variant % sub.titles.length](a, v);
  const description = sub.descs[variant % sub.descs.length](a, v);
  return { title: title.slice(0, 120), description: description.slice(0, 400) };
}
