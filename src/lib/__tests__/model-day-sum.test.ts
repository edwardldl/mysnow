import { describe, expect, it } from 'vitest';
import {
  applyModelMethodToDays,
  modelForecastFor,
  modelWindowEndDateForHour,
} from '../model-forecast';
import { groupData } from '../data';
import { possibleInstantsForLocalTime, providerLocalTimeToEpochMs } from '../time';
import type { BlendedHour, DayData, Location, WindowData } from '../types';

/**
 * Regression tests for the day-sum invariant (bug reported 2026-11-10:
 * "the hourly snow bar does not add up to the predicted snowfall number
 * for the day").
 *
 * The invariant a user sees: for the day as displayed, the hourly bars
 * sum to the displayed daily snowfall. groupData guarantees it for every
 * physics method by deriving totalSnowfall as the sum of the calendar
 * day's hourly snowfall. The ENS-EB method path broke it: the day total
 * was the serving record of the 24 h window ending 07:00 local -- a span
 * straddling two calendar days -- while the bars are the calendar day's
 * hours. Measured before the fix (Palisades base, blend mode): day
 * 2026-11-01 displayed 10.72 cm while its bars summed to 2.44 cm.
 *
 * The fix keeps the per-window rescale (each window's hours still sum
 * exactly to its record -- model-rescale.test.ts) and re-derives the
 * day total from the final hourly series, the same derivation groupData
 * uses. These tests pin BOTH invariants, for every serving mode, plus
 * the physics path they must agree with.
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

function hour(label: string, snowfall: number, liquidMM = 0): BlendedHour {
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
    slr: null,
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

function fullDay(dateStr: string, snowfall: number): BlendedHour[] {
  return labels(dateStr, 0, 23).map((l) => hour(l, snowfall));
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

const TOL = 1e-6;

function expectDaySumInvariant(days: DayData[]) {
  for (const d of days) {
    const barSum = d.hourly.reduce((s, h) => s + h.snowfall, 0);
    expect(
      Math.abs(barSum - d.totalSnowfall),
      `${d.dateStr}: bars ${barSum} vs total ${d.totalSnowfall}`,
    ).toBeLessThan(TOL);
  }
}

function windowSum(days: DayData[], endDate: string): number {
  return days
    .flatMap((d) => d.hourly)
    .filter((h) => modelWindowEndDateForHour(h, TZ) === endDate)
    .reduce((s, h) => s + h.snowfall, 0);
}

function record(mode: string, endDate: string): number {
  const result = modelForecastFor(palisades, mode, 'min', endDate);
  expect(result, `record for ${mode} window ${endDate}`).not.toBeNull();
  return result?.forecast.modelSnowfallCm as number;
}

describe('day-sum invariant under the ENS-EB method', () => {
  it('blend mode: every day adds up, model and fallback days alike', () => {
    const days = [
      day('2026-10-31', [
        ...labels('2026-10-31', 0, 7).map((l) => hour(l, 9)),
        ...labels('2026-10-31', 8, 23).map((l) => hour(l, 0.5)),
      ]),
      day('2026-11-01', [
        ...labels('2026-11-01', 0, 7).map((l) => hour(l, 0.25)),
        ...labels('2026-11-01', 8, 23).map((l) => hour(l, 0.1)),
      ]),
      day('2026-11-02', [
        ...labels('2026-11-02', 0, 7).map((l) => hour(l, 0.1)),
        ...labels('2026-11-02', 8, 23).map((l) => hour(l, 9)),
      ]),
    ];
    const out = applyModelMethodToDays(days, palisades, 'hrrr_ecmwf', 'min');
    expectDaySumInvariant(out);

    // The window invariant from the hourly rescale fix still holds.
    const r1 = record('hrrr_ecmwf', '2026-11-01');
    const r2 = record('hrrr_ecmwf', '2026-11-02');
    expect(Math.abs(windowSum(out, '2026-11-01') - r1)).toBeLessThan(1e-9);
    expect(Math.abs(windowSum(out, '2026-11-02') - r2)).toBeLessThan(1e-9);

    // Closed form for the straddling day 2026-11-01: its 00:00-07:00
    // hours are 2/10 of window 2026-11-01's raw sum (scaled to r1) and
    // its 08:00-23:00 hours are 1.6/2.4 of window 2026-11-02's (to r2).
    const nov1 = out[1];
    expect(Math.abs(nov1.totalSnowfall - (0.2 * r1 + (2 / 3) * r2))).toBeLessThan(TOL);
    // And it is decisively NOT the window record the day used to show.
    expect(Math.abs(nov1.totalSnowfall - r1)).toBeGreaterThan(1);
  });

  it('literal ecmwf mode: every day adds up', () => {
    const days = [
      day('2026-11-01', fullDay('2026-11-01', 0.3)),
      day('2026-11-02', fullDay('2026-11-02', 0.3)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    expectDaySumInvariant(out);
    const r = record('ecmwf', '2026-11-02');
    expect(Math.abs(windowSum(out, '2026-11-02') - r)).toBeLessThan(1e-9);
  });

  it('literal hrrr mode: model day and fallback day both add up', () => {
    const days = [
      day('2026-11-01', fullDay('2026-11-01', 0.4)),
      day('2026-11-02', fullDay('2026-11-02', 0.4)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'hrrr', 'min');
    expect(out[1].modelWindow?.kind).toBe('kuchera_fallback');
    expectDaySumInvariant(out);
  });

  it('physics path (groupData) satisfies the same invariant', () => {
    const hourly = [...fullDay('2026-11-01', 0.3), ...fullDay('2026-11-02', 0.7)];
    const days = groupData({ hourly, daily: null as never, lastRunAvailabilityTime: undefined });
    expect(days).toHaveLength(2);
    expectDaySumInvariant(days);
  });
});

describe('day-sum invariant: DST duplicate hours and array boundaries', () => {
  it('fall-back day: both 01:00 instants survive, land in the same window, and the day adds up', () => {
    const dupInstants = possibleInstantsForLocalTime('2026-11-01T01:00', TZ);
    expect(dupInstants).toHaveLength(2);
    const first = hour('2026-11-01T01:00', 0.25);
    expect(first.instantMs).toBe(dupInstants[0]);
    const second = {
      ...hour('2026-11-01T01:00', 0.25),
      instantMs: dupInstants[1],
      dateObj: new Date(dupInstants[1]),
    } as BlendedHour;
    const nov1: BlendedHour[] = [
      hour('2026-11-01T00:00', 0.25),
      first,
      second,
      ...labels('2026-11-01', 2, 7).map((l) => hour(l, 0.25)),
      ...labels('2026-11-01', 8, 23).map((l) => hour(l, 0.1)),
    ];
    expect(nov1).toHaveLength(25);
    const days = [
      day('2026-10-31', [
        ...labels('2026-10-31', 0, 7).map((l) => hour(l, 9)),
        ...labels('2026-10-31', 8, 23).map((l) => hour(l, 0.5)),
      ]),
      day('2026-11-01', nov1),
      day('2026-11-02', [
        ...labels('2026-11-02', 0, 7).map((l) => hour(l, 0.1)),
        ...labels('2026-11-02', 8, 23).map((l) => hour(l, 9)),
      ]),
    ];
    const out = applyModelMethodToDays(days, palisades, 'hrrr_ecmwf', 'min');
    const outNov1 = out[1];
    expect(outNov1.hourly).toHaveLength(25);
    const ones = outNov1.hourly.filter((h) => h.time === '2026-11-01T01:00');
    expect(new Set(ones.map((h) => h.instantMs))).toEqual(new Set(dupInstants));
    expectDaySumInvariant(out);
    // The window ending 2026-11-01 now holds 25 hours and still sums
    // exactly to its record.
    const r1 = record('hrrr_ecmwf', '2026-11-01');
    expect(Math.abs(windowSum(out, '2026-11-01') - r1)).toBeLessThan(1e-9);
  });

  it('array boundaries: a truncated first window allocates its full record to the hours present; the last afternoon stays substrate', () => {
    const days = [
      day('2026-11-01', fullDay('2026-11-01', 0.3)),
      day('2026-11-02', fullDay('2026-11-02', 0.3)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    // The window ending 2026-11-01 starts before the array (its
    // 2026-10-31 afternoon is absent), so the 8 present morning hours
    // absorb the whole record -- the rescale's pinned behaviour.
    const r1101 = record('ecmwf', '2026-11-01');
    expect(Math.abs(windowSum(out, '2026-11-01') - r1101)).toBeLessThan(1e-9);
    for (const h of out[0].hourly.slice(0, 8)) {
      expect(Math.abs(h.snowfall - r1101 / 8)).toBeLessThan(1e-9);
    }
    // The last day's afternoon belongs to the window ending 2026-11-03,
    // which is not a day in the array, so no record touches it.
    for (const h of out[1].hourly.slice(8)) {
      expect(h.snowfall).toBe(0.3);
    }
    expectDaySumInvariant(out);
  });
});
