import { describe, expect, it } from 'vitest';
import {
  applyModelMethodToDays,
  modelForecastData,
  modelForecastFor,
  modelWindowEndDateForHour,
} from '../model-forecast';
import { calculateRollingStats } from '../data';
import { addCalendarDays, providerLocalTimeToEpochMs } from '../time';
import type { BlendedHour, DayData, Location, WindowData } from '../types';

/**
 * Precipitation invariance under the ENS-EB method (user directive,
 * 2026-11-11: "the precipitation total should be the same regardless of
 * the SLR model", stated as the identity "snowfall divided by SLR ratio
 * should always equal the precipitation").
 *
 * ENS-EB is a pure SLR method: for a model window it replaces the ratio,
 * never the precipitation. Each model-window hour's snowfall is the
 * record SLR x the hour's OWN frozen SWE (the same liquid base the
 * Kuchera path converts: freshSnowCm = frozenSweMm x freshSlr / 10 in
 * snow/snowfall.ts), and the hour's frozen SWE, total liquid and
 * precipitation fields pass through byte-identical. Two statements,
 * kept distinct: TOTAL precipitation is invariant across methods, and
 * the identity snowfall / SLR = precipitation holds against the FROZEN
 * precipitation -- per hour, per window, and in the rolling 24/48 h
 * statistics. The mixed-phase test below pins the boundary: when part
 * of an hour's precipitation is rain, snowfall / SLR recovers the
 * frozen part only, under ENS-EB exactly as under Kuchera.
 *
 * Fixtures use the Palisades base point and the installed serving
 * batch; window dates are read from the file's own rows
 * (batch-agnostic) and SLRs come back through the production
 * resolver. Substrate hours carry a uniform Kuchera SLR of 20 so the
 * pre-method series is itself identity-consistent.
 */

const TZ = 'America/Los_Angeles';
const SUBSTRATE_SLR = 20;

const palisades: Location = {
  id: 'palisades',
  name: 'Palisades Tahoe',
  latitude: 39.193416,
  longitude: -120.245402,
  elevationFt: 8200,
  elevationM: 2499,
  minElevationM: 1889,
  maxElevationM: 2708,
  referencePoints: [
    { id: 'palisades-base', name: 'Village base', role: 'base', latitude: 39.196999, longitude: -120.24313, elevationM: 1889, geometrySource: 'curated' },
    { id: 'palisades-high-camp', name: 'High Camp', role: 'mid_mountain', latitude: 39.195899, longitude: -120.260845, elevationM: 2499, geometrySource: 'curated' },
    { id: 'palisades-upper-mountain', name: 'Upper mountain', role: 'summit', latitude: 39.181764, longitude: -120.266494, elevationM: 2708, geometrySource: 'curated' },
  ],
  timezone: TZ,
  isCustom: false,
} as Location;

/** A substrate hour whose snowfall is frozen SWE x SUBSTRATE_SLR / 10. */
function hour(label: string, frozenSweMm: number): BlendedHour {
  const instantMs = providerLocalTimeToEpochMs(label, TZ);
  return {
    time: label,
    instantMs,
    dateObj: new Date(instantMs),
    providerTimezone: TZ,
    model: 'ecmwf',
    precipitation: frozenSweMm,
    liquidMM: frozenSweMm,
    temperature: -2,
    dewPoint: -4,
    windSpeed: 3,
    windDir: 270,
    providerSnowDepthM: null,
    modeledSnowpackDepthM: 0,
    precipChance: null,
    feelsLike: -4,
    rh: 80,
    gusts: null,
    clouds: null,
    snowLevel: null,
    weatherCode: 71,
    slr: SUBSTRATE_SLR,
    snowfall: (frozenSweMm * SUBSTRATE_SLR) / 10,
    frozenSweMm,
    method: 'kuchera',
    slrCategory: null,
    uvIndex: null,
  } as BlendedHour;
}

function labels(dateStr: string, from: number, to: number): string[] {
  const out: string[] = [];
  for (let h = from; h <= to; h++) out.push(`${dateStr}T${String(h).padStart(2, '0')}:00`);
  return out;
}

function day(dateStr: string, hourly: BlendedHour[]): DayData {
  const windows: WindowData[] = Array.from({ length: 8 }, (_, i) => {
    const bucket = hourly.filter((h) => Math.floor(Number(h.time.slice(11, 13)) / 3) === i);
    return {
      startHour: i * 3,
      label: `${i * 3}:00`,
      snowfall: bucket.reduce((s, h) => s + h.snowfall, 0),
      precip: bucket.reduce((s, h) => s + h.precipitation, 0),
      temps: [],
      windSpeeds: [],
      windDirs: [],
      slrs: [],
      precipChances: [],
      feelsLikes: [],
      rhs: [],
      gusts: [],
      clouds: [],
      snowLevels: [],
      uvIndices: [],
      visibilities: [],
    } as WindowData;
  });
  return {
    dateStr,
    dateObj: new Date(`${dateStr}T12:00:00Z`),
    sunrise: null,
    sunset: null,
    totalSnowfall: hourly.reduce((s, h) => s + h.snowfall, 0),
    totalPrecipitation: hourly.reduce((s, h) => s + h.precipitation, 0),
    models: new Set<string>(['ecmwf']),
    hourly,
    windows,
    providerSnowDepthValuesM: [],
    modeledSnowpackDepthValuesM: [],
    snowLayersOnGround: [],
    maxHourlySnowfall: Math.max(0, ...hourly.map((h) => h.snowfall)),
    maxWindowSnowfall: Math.max(0, ...windows.map((w) => w.snowfall)),
    minTemp: -5,
    maxTemp: 0,
    weatherCode: 71,
  } as DayData;
}

function windowHours(days: DayData[], endDate: string): BlendedHour[] {
  return days
    .flatMap((d) => d.hourly)
    .filter((h) => modelWindowEndDateForHour(h, TZ) === endDate);
}

/** A mixed-phase substrate hour: total precipitation exceeds the frozen part. */
function mixedHour(label: string, liquidMM: number, frozenSweMm: number): BlendedHour {
  const base = hour(label, frozenSweMm);
  return {
    ...base,
    precipitation: liquidMM,
    liquidMM,
    rainMm: liquidMM - frozenSweMm,
  } as BlendedHour;
}

const palisadesPoint = modelForecastData.points.find((p) => p.pointId === 'palisades-tahoe-base');
const ecmwfRows = palisadesPoint?.forecasts.ecmwf ?? [];

/**
 * True when the 24 h window ending on `endDate` (Palisades local)
 * spans a DST transition: its two calendar days are not both 24 h,
 * so label-built fixtures (00..23) would mis-count its hours. The
 * fixture window is selected DST-clean (Codex gate, 2026-11-12);
 * at most one transition can fall inside a batch's 10-day horizon.
 */
function windowStraddlesDst(endDate: string): boolean {
  const dayLengthH = (d: string) =>
    (providerLocalTimeToEpochMs(`${addCalendarDays(d, 1)}T00:00`, TZ) -
      providerLocalTimeToEpochMs(`${d}T00:00`, TZ)) / 3_600_000;
  return dayLengthH(addCalendarDays(endDate, -1)) !== 24 || dayLengthH(endDate) !== 24;
}
const END = (() => {
  for (let i = 3; i < ecmwfRows.length; i++) {
    if (!windowStraddlesDst(ecmwfRows[i].windowEndLocalDate)) return ecmwfRows[i].windowEndLocalDate;
  }
  throw new Error('no DST-clean fixture window in batch');
})();
const PREV = addCalendarDays(END, -1);
const resolved = modelForecastFor(palisades, 'ecmwf', 'min', END);
if (!resolved) throw new Error('serving record missing for fixture window');
const RECORD_SLR = resolved.forecast.modelSlr as number;

/** Two full calendar days whose hours span the windows ending PREV, END and (afternoon) END + 1. */
function fixtureDays(): DayData[] {
  return [
    day(PREV, labels(PREV, 0, 23).map((l) => hour(l, 1.5))),
    day(END, labels(END, 0, 23).map((l) => hour(l, 1.5))),
  ];
}

describe('precipitation invariance under ENS-EB (2026-11-11 directive)', () => {
  it('switching Kuchera -> ENS-EB leaves every precipitation figure byte-identical', () => {
    const days = fixtureDays();
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    expect(out).toHaveLength(days.length);
    out.forEach((outDay, di) => {
      expect(outDay.totalPrecipitation).toBe(days[di].totalPrecipitation);
      expect(outDay.hourly).toHaveLength(days[di].hourly.length);
      outDay.hourly.forEach((outHour, hi) => {
        const inHour = days[di].hourly[hi];
        expect(outHour.frozenSweMm).toBe(inHour.frozenSweMm);
        expect(outHour.liquidMM).toBe(inHour.liquidMM);
        expect(outHour.precipitation).toBe(inHour.precipitation);
      });
    });
  });

  it('snowfall scales by exactly the SLR change on the same liquid', () => {
    const days = fixtureDays();
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const w = windowHours(out, END);
    expect(w).toHaveLength(24);
    const frozenTotal = w.reduce((s, h) => s + (h.frozenSweMm ?? 0), 0);
    const ensSnow = w.reduce((s, h) => s + h.snowfall, 0);
    // The Kuchera total on the SAME hours, from the untouched input.
    const kucheraSnow = windowHours(days, END).reduce((s, h) => s + h.snowfall, 0);
    expect(ensSnow).toBeCloseTo((frozenTotal * RECORD_SLR) / 10, 9);
    expect(ensSnow).toBeCloseTo(kucheraSnow * (RECORD_SLR / SUBSTRATE_SLR), 9);
    expect(ensSnow).not.toBeCloseTo(kucheraSnow, 6);
  });

  it('identity: snowfall / SLR = precipitation, per hour and per window', () => {
    const days = fixtureDays();
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const w = windowHours(out, END);
    for (const h of w) {
      expect(h.slr).toBe(RECORD_SLR);
      // snowfall (cm) / SLR = frozen precipitation in cm of water.
      expect(h.snowfall / (h.slr as number)).toBeCloseTo((h.frozenSweMm ?? 0) / 10, 9);
    }
    const snowTotal = w.reduce((s, h) => s + h.snowfall, 0);
    const frozenTotal = w.reduce((s, h) => s + (h.frozenSweMm ?? 0), 0);
    expect(snowTotal / RECORD_SLR).toBeCloseTo(frozenTotal / 10, 9);
    // The day mark carries the same window accounting.
    const mark = out.find((d) => d.dateStr === END)?.modelWindow;
    expect(mark?.kind).toBe('model');
    expect(mark?.slr).toBe(RECORD_SLR);
    expect(mark?.snowfallCm as number).toBeCloseTo(snowTotal, 9);
  });

  it('mixed-phase and rain-only hours: total precipitation invariant, identity holds against the frozen part only', () => {
    // Every hour of the window ending END: 3.0 mm total
    // precipitation, of which 1.2 mm frozen (snow fraction 0.4); the
    // window ending PREV is rain-only (3.0 mm, none frozen).
    const days = [
      day(PREV, labels(PREV, 0, 23).map((l) =>
        Number(l.slice(11, 13)) >= 8 ? mixedHour(l, 3.0, 1.2) : mixedHour(l, 3.0, 0),
      )),
      day(END, labels(END, 0, 23).map((l) =>
        Number(l.slice(11, 13)) <= 7 ? mixedHour(l, 3.0, 1.2) : mixedHour(l, 3.0, 1.2),
      )),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const w = windowHours(out, END);
    expect(w).toHaveLength(24);
    for (const h of w) {
      // Total precipitation: untouched, byte-identical.
      expect(h.precipitation).toBe(3.0);
      expect(h.liquidMM).toBe(3.0);
      // Conversion acts on the frozen part only.
      expect(h.frozenSweMm).toBe(1.2);
      expect(h.snowfall).toBeCloseTo((1.2 * RECORD_SLR) / 10, 9);
      // Identity against the frozen precipitation...
      expect(h.snowfall / RECORD_SLR).toBeCloseTo(0.12, 9);
      // ...and explicitly NOT against the total precipitation.
      expect(h.snowfall / RECORD_SLR).not.toBeCloseTo(0.3, 6);
    }
    expect(out[1].totalPrecipitation).toBe(days[1].totalPrecipitation);
    // Rain-only hours (the morning side of the window ending PREV
    // sits in the PREV calendar day; the rest of that window is in
    // the day before the fixture): zero frozen -> zero snow under
    // both methods, precipitation still 3.0 mm.
    const rainHours = windowHours(out, PREV).filter((h) => (h.frozenSweMm ?? 0) === 0);
    expect(rainHours.length).toBeGreaterThan(0);
    for (const h of rainHours) {
      expect(h.snowfall).toBe(0);
      expect(h.precipitation).toBe(3.0);
    }
  });

  it('identity in the rolling 24 h statistics over a model window', () => {
    const days = fixtureDays();
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const ref = providerLocalTimeToEpochMs(`${END}T07:00`, TZ);
    const stats = calculateRollingStats(out.flatMap((d) => d.hourly), ref);
    const w = windowHours(out, END);
    const frozenTotal = w.reduce((s, h) => s + (h.frozenSweMm ?? 0), 0);
    expect(stats.snow24h).toBeCloseTo(w.reduce((s, h) => s + h.snowfall, 0), 9);
    expect(stats.slr24h).not.toBeNull();
    expect((stats.snow24h / (stats.slr24h as number)) * 10).toBeCloseTo(frozenTotal, 6);
    expect(stats.slr24h as number).toBeCloseTo(RECORD_SLR, 6);
  });
});
