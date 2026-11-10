import { describe, expect, it } from 'vitest';
import {
  applyModelMethodToDays,
  modelForecastFor,
  modelWindowEndDateForHour,
} from '../model-forecast';
import { addCalendarDays, possibleInstantsForLocalTime, providerLocalTimeToEpochMs } from '../time';
import type { BlendedHour, DayData, Location, WindowData } from '../types';

/**
 * Per-window hourly rescaling under the ENS-EB menu method (2026-11-01):
 * with the model method active, the UI hourly snowfall inside each model
 * window (07:00 -> 07:00 local) must sum EXACTLY to the serving record's
 * snowfall -- the value the day total shows. Fixtures are built against
 * the Palisades Tahoe base catalogue point and the stored serving records
 * of the 2026-11-10 00Z batch; expected totals are read back through the
 * production resolver rather than hard-coded.
 */

const TZ = 'America/Los_Angeles';

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

function hour(label: string, snowfall: number, liquidMM = 0, slr: number | null = null): BlendedHour {
  const instantMs = providerLocalTimeToEpochMs(label, TZ);
  return {
    time: label,
    instantMs,
    dateObj: new Date(instantMs),
    providerTimezone: TZ,
    model: 'ecmwf',
    precipitation: liquidMM,
    liquidMM,
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
    slr,
    snowfall,
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

function fullDay(dateStr: string, snowfall: number, liquidMM = 0): BlendedHour[] {
  return labels(dateStr, 0, 23).map((l) => hour(l, snowfall, liquidMM));
}

function day(dateStr: string, hourly: BlendedHour[]): DayData {
  const windows: WindowData[] = Array.from({ length: 8 }, (_, i) => {
    const bucket = hourly.filter((h) => Math.floor(Number(h.time.slice(11, 13)) / 3) === i);
    return {
      startHour: i * 3,
      label: `${i * 3}:00`,
      snowfall: bucket.reduce((s, h) => s + h.snowfall, 0),
      precip: 0,
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
    totalPrecipitation: 0,
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

/** The 24 labelled hours of the model window ending 07:00 local on endDate. */
function windowHours(days: DayData[], endDate: string): BlendedHour[] {
  return days
    .flatMap((d) => d.hourly)
    .filter((h) => modelWindowEndDateForHour(h, TZ) === endDate);
}

function recordSnowfall(mode: string, endDate: string): number {
  const result = modelForecastFor(palisades, mode, 'min', endDate);
  expect(result, `record for ${mode} window ${endDate}`).not.toBeNull();
  return result?.forecast.modelSnowfallCm as number;
}

const EPS = 1e-9;

describe('modelWindowEndDateForHour', () => {
  it('assigns end-of-accumulation labels to 07:00 -> 07:00 windows', () => {
    expect(modelWindowEndDateForHour(hour('2026-11-01T07:00', 0), TZ)).toBe('2026-11-01');
    expect(modelWindowEndDateForHour(hour('2026-11-01T08:00', 0), TZ)).toBe('2026-11-02');
    expect(modelWindowEndDateForHour(hour('2026-11-01T00:00', 0), TZ)).toBe('2026-11-01');
    expect(modelWindowEndDateForHour(hour('2026-10-31T23:00', 0), TZ)).toBe('2026-11-01');
    expect(modelWindowEndDateForHour(hour('2026-10-31T07:00', 0), TZ)).toBe('2026-10-31');
  });

  it('puts both fall-back 01:00 instants in the same window', () => {
    const instants = possibleInstantsForLocalTime('2026-11-01T01:00', TZ);
    expect(instants).toHaveLength(2);
    for (const instantMs of instants) {
      const h = { time: '2026-11-01T01:00', instantMs } as BlendedHour;
      expect(modelWindowEndDateForHour(h, TZ)).toBe('2026-11-01');
    }
  });
});

describe('applyModelMethodToDays hourly rescaling', () => {
  it('blend mode: window hours sum exactly to the record, straddling two calendar days', () => {
    // Anchored to the 2026-11-10 00Z batch: in blend mode the window
    // ending 2026-11-18 resolves to the GRIB ECMWF arm (lead 9,
    // 5.48 cm; HRRR holds only the lead-2 window ending 2026-11-11).
    const days = [
      day('2026-11-17', [
        ...labels('2026-11-17', 0, 7).map((l) => hour(l, 9)), // window ending 2026-11-17
        ...labels('2026-11-17', 8, 23).map((l) => hour(l, 0.5)), // window ending 2026-11-18
      ]),
      day('2026-11-18', [
        ...labels('2026-11-18', 0, 7).map((l) => hour(l, 0.25)), // window ending 2026-11-18
        ...labels('2026-11-18', 8, 23).map((l) => hour(l, 0.1)), // window ending 2026-11-19
      ]),
      day('2026-11-19', [
        ...labels('2026-11-19', 0, 7).map((l) => hour(l, 0.1)), // window ending 2026-11-19
        ...labels('2026-11-19', 8, 23).map((l) => hour(l, 9)), // window ending 2026-11-20
      ]),
    ];
    const before = days.map((d) => d.hourly.map((h) => h.snowfall));

    const out = applyModelMethodToDays(days, palisades, 'hrrr_ecmwf', 'min');

    const r1 = recordSnowfall('hrrr_ecmwf', '2026-11-18');
    const w1 = windowHours(out, '2026-11-18');
    expect(w1).toHaveLength(24);
    expect(Math.abs(w1.reduce((s, h) => s + h.snowfall, 0) - r1)).toBeLessThan(EPS);
    // The day total follows the calendar day: it is the sum of the day's
    // own rescaled hours (morning from this window, afternoon from the
    // next), NOT the window record -- see model-day-sum.test.ts.
    expect(Math.abs(out[1].totalSnowfall - out[1].hourly.reduce((sum, h) => sum + h.snowfall, 0))).toBeLessThan(EPS);

    // The 2026-11-18 calendar day straddles two windows: its 08:00-23:00
    // hours are scaled by the 2026-11-19 window's factor, not this one.
    const r2 = recordSnowfall('hrrr_ecmwf', '2026-11-19');
    const w2 = windowHours(out, '2026-11-19');
    expect(Math.abs(w2.reduce((s, h) => s + h.snowfall, 0) - r2)).toBeLessThan(EPS);
    const day18Afternoon = out[1].hourly.filter((h) => Number(h.time.slice(11, 13)) >= 8);
    expect(day18Afternoon.every((h) => Math.abs(h.snowfall - 0.1 * (r2 / 2.4)) < 1e-6)).toBe(true);

    // The first window is truncated by the array boundary (its
    // 2026-11-16 afternoon is absent): the 8 present hours absorb its
    // full record (see also the array-boundary pin in
    // model-day-sum.test.ts).
    const r0 = recordSnowfall('hrrr_ecmwf', '2026-11-17');
    const w0 = windowHours(out, '2026-11-17');
    expect(w0).toHaveLength(8);
    expect(Math.abs(w0.reduce((s, h) => s + h.snowfall, 0) - r0)).toBeLessThan(EPS);

    // Inputs were not mutated.
    days.forEach((d, di) => d.hourly.forEach((h, hi) => expect(h.snowfall).toBe(before[di][hi])));
  });

  it('literal ecmwf mode: window hours sum exactly to the GRIB record', () => {
    const days = [
      day('2026-11-13', fullDay('2026-11-13', 0.3)),
      day('2026-11-14', fullDay('2026-11-14', 0.3)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const r = recordSnowfall('ecmwf', '2026-11-14');
    const w = windowHours(out, '2026-11-14');
    expect(w).toHaveLength(24);
    expect(Math.abs(w.reduce((s, h) => s + h.snowfall, 0) - r)).toBeLessThan(EPS);
    // Day total = the calendar-day sum of the rescaled hours, not r.
    expect(Math.abs(out[1].totalSnowfall - out[1].hourly.reduce((sum, h) => sum + h.snowfall, 0))).toBeLessThan(EPS);
  });

  it('beyond the cap: the window ending 2026-11-20 has no record in any mode and is never rescaled', () => {
    // Lead 11 at Palisades. Before the 2026-11-10 cap this window was
    // served by the Open-Meteo tail arm; the arm is retired and the
    // window now resolves to nothing, so its hours stay Kuchera.
    for (const mode of ['ecmwf', 'hrrr_ecmwf', 'hrrr']) {
      expect(modelForecastFor(palisades, mode, 'min', '2026-11-20')).toBeNull();
    }
    const days = [
      day('2026-11-19', fullDay('2026-11-19', 1)),
      day('2026-11-20', fullDay('2026-11-20', 1)),
      day('2026-11-21', fullDay('2026-11-21', 1)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const w = windowHours(out, '2026-11-20');
    expect(w).toHaveLength(24);
    const originals = days.flatMap((d) => d.hourly);
    for (const h of w) expect(originals).toContain(h);
    expect(out[1].modelWindow?.kind).toBe('kuchera_fallback');
    expect(out[1].totalSnowfall).toBe(24);
  });

  it('a record of exactly 0 zeroes the window hours', () => {
    expect(recordSnowfall('ecmwf', '2026-11-11')).toBe(0);
    const days = [
      day('2026-11-10', fullDay('2026-11-10', 1)),
      day('2026-11-11', fullDay('2026-11-11', 1)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const w = windowHours(out, '2026-11-11');
    expect(w).toHaveLength(24);
    expect(w.every((h) => h.snowfall === 0)).toBe(true);
    // The window's own hours are zeroed, but the day total follows the
    // calendar day: the 08:00-23:00 hours belong to the NEXT window,
    // which has no record in this fixture, so they keep their 1 cm/h
    // Kuchera values and the calendar-day total is 16, not 0.
    expect(out[1].totalSnowfall).toBe(16);
    expect(Math.abs(out[1].totalSnowfall - out[1].hourly.reduce((sum, h) => sum + h.snowfall, 0))).toBeLessThan(EPS);
  });

  it('zero denominator: record snowfall is distributed in proportion to UI liquid', () => {
    const r = recordSnowfall('ecmwf', '2026-11-14');
    expect(r).toBeGreaterThan(0);
    const prev = addCalendarDays('2026-11-14', -1);
    const hoursPrev = labels('2026-11-13', 0, 23).map((l) => {
      if (l === `${prev}T08:00`) return hour(l, 0, 2);
      if (l === `${prev}T09:00`) return hour(l, 0, 1);
      if (l === `${prev}T10:00`) return hour(l, 0, 1);
      return hour(l, 0, 0);
    });
    const hoursDay = labels('2026-11-14', 0, 23).map((l) => hour(l, 0, 0));
    const out = applyModelMethodToDays(
      [day('2026-11-13', hoursPrev), day('2026-11-14', hoursDay)],
      palisades,
      'ecmwf',
      'min',
    );
    const w = windowHours(out, '2026-11-14');
    expect(Math.abs(w.reduce((s, h) => s + h.snowfall, 0) - r)).toBeLessThan(EPS);
    const at = (label: string) => w.find((h) => h.time === label)?.snowfall as number;
    expect(Math.abs(at(`${prev}T08:00`) - r * 0.5)).toBeLessThan(EPS);
    expect(Math.abs(at(`${prev}T09:00`) - r * 0.25)).toBeLessThan(EPS);
    expect(Math.abs(at(`${prev}T10:00`) - r * 0.25)).toBeLessThan(EPS);
    expect(at(`${prev}T11:00`)).toBe(0);
  });

  it('zero denominator with no liquid either: record snowfall is split evenly', () => {
    const r = recordSnowfall('ecmwf', '2026-11-14');
    const days = [
      day('2026-11-13', fullDay('2026-11-13', 0, 0)),
      day('2026-11-14', fullDay('2026-11-14', 0, 0)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const w = windowHours(out, '2026-11-14');
    expect(w).toHaveLength(24);
    expect(Math.abs(w.reduce((s, h) => s + h.snowfall, 0) - r)).toBeLessThan(EPS);
    for (const h of w) expect(Math.abs(h.snowfall - r / 24)).toBeLessThan(EPS);
  });

  it('fallback windows keep Kuchera hours, identical objects (literal hrrr, the window after the HRRR day)', () => {
    // The batch's only HRRR window ends 2026-11-11; the window ending
    // 2026-11-12 has no HRRR record, so the day stays Kuchera.
    expect(modelForecastFor(palisades, 'hrrr', 'min', '2026-11-12')).toBeNull();
    const days = [
      day('2026-11-11', fullDay('2026-11-11', 0.4)),
      day('2026-11-12', fullDay('2026-11-12', 0.4)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'hrrr', 'min');
    expect(out[0].modelWindow?.kind).toBe('model');
    expect(out[1].modelWindow?.kind).toBe('kuchera_fallback');
    const w = windowHours(out, '2026-11-12');
    expect(w).toHaveLength(24);
    for (const h of w) {
      const original = days.flatMap((d) => d.hourly).find((o) => o.time === h.time && modelWindowEndDateForHour(o, TZ) === '2026-11-12' && o.instantMs === h.instantMs);
      expect(h).toBe(original);
      expect(h.snowfall).toBe(0.4);
    }
  });

  it('method inactive: days returned unchanged', () => {
    const days = [day('2026-11-01', fullDay('2026-11-01', 0.5))];
    expect(applyModelMethodToDays(days, palisades, 'gfs', 'min')).toBe(days);
    expect(applyModelMethodToDays(days, null, 'ecmwf', 'min')).toBe(days);
  });
});
