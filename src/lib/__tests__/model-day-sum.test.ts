import { describe, expect, it } from 'vitest';
import {
  applyModelMethodToDays,
  modelForecastFor,
  modelWindowEndDateForHour,
} from '../model-forecast';
import { groupData } from '../data';
import { providerLocalTimeToEpochMs } from '../time';
import type { BlendedHour, DayData, Location, WindowData } from '../types';

/**
 * Regression tests for the day-sum invariant (bug reported 2026-11-10:
 * "the hourly snow bar does not add up to the predicted snowfall number
 * for the day").
 *
 * The invariant a user sees: for the day as displayed, the hourly bars
 * sum to the displayed daily snowfall. groupData guarantees it for every
 * physics method by deriving totalSnowfall as the sum of the calendar
 * day's hourly snowfall. Under the ENS-EB method the day total is
 * derived the same way from the final series: each model window's hours
 * are converted in place (frozenSweMm x record SLR / 10, the 2026-11-11
 * pure-SLR semantics) and the day total is the sum of its own converted
 * hours -- morning hours under the window ending that morning, afternoon
 * hours under the next window. These tests pin the day invariant for
 * every serving mode, the window conversion it composes from, and the
 * physics path they must agree with. Fixture hours carry frozen SWE at
 * a uniform substrate SLR of 20 (frozen = snowfall / 2).
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
    frozenSweMm: snowfall / 2,
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

function windowHours(days: DayData[], endDate: string): BlendedHour[] {
  return days
    .flatMap((d) => d.hourly)
    .filter((h) => modelWindowEndDateForHour(h, TZ) === endDate);
}

function windowSum(days: DayData[], endDate: string): number {
  return windowHours(days, endDate).reduce((s, h) => s + h.snowfall, 0);
}

function recordSlr(mode: string, endDate: string): number {
  const result = modelForecastFor(palisades, mode, 'min', endDate);
  expect(result, `record for ${mode} window ${endDate}`).not.toBeNull();
  return result?.forecast.modelSlr as number;
}

/** Expected applied window snowfall: record SLR x the window's substrate frozen SWE / 10. */
function expectedWindowSnow(days: DayData[], mode: string, endDate: string): number {
  const frozen = windowHours(days, endDate).reduce((s, h) => s + (h.frozenSweMm ?? 0), 0);
  return (frozen * recordSlr(mode, endDate)) / 10;
}

describe('day-sum invariant under the ENS-EB method', () => {
  it('blend mode: every day adds up, model and fallback days alike', () => {
    const days = [
      day('2026-11-17', [
        ...labels('2026-11-17', 0, 7).map((l) => hour(l, 9)),
        ...labels('2026-11-17', 8, 23).map((l) => hour(l, 0.5)),
      ]),
      day('2026-11-18', [
        ...labels('2026-11-18', 0, 7).map((l) => hour(l, 0.25)),
        ...labels('2026-11-18', 8, 23).map((l) => hour(l, 0.1)),
      ]),
      day('2026-11-19', [
        ...labels('2026-11-19', 0, 7).map((l) => hour(l, 0.1)),
        ...labels('2026-11-19', 8, 23).map((l) => hour(l, 9)),
      ]),
      // Beyond the last stored window (lead 10 ends 2026-11-19): no arm
      // holds a record for this day's windows, so it stays Kuchera.
      day('2026-11-24', fullDay('2026-11-24', 0.2)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'hrrr_ecmwf', 'min');
    expectDaySumInvariant(out);

    // Each model window's hours sum to its SLR applied to the SAME
    // substrate frozen SWE (not to the record's stored snowfall).
    expect(Math.abs(windowSum(out, '2026-11-18') - expectedWindowSnow(days, 'hrrr_ecmwf', '2026-11-18'))).toBeLessThan(1e-9);
    expect(Math.abs(windowSum(out, '2026-11-19') - expectedWindowSnow(days, 'hrrr_ecmwf', '2026-11-19'))).toBeLessThan(1e-9);
    // Inside its horizon the blend still privileges the HRRR arm.
    expect(modelForecastFor(palisades, 'hrrr_ecmwf', 'min', '2026-11-11')?.arm).toBe('hrrr');

    // Closed form for the straddling day 2026-11-18: morning frozen
    // 8 x 0.125 = 1 mm under the 11-18 SLR, afternoon frozen
    // 16 x 0.05 = 0.8 mm under the 11-19 SLR.
    const day18 = out[1];
    const closed = (1 * recordSlr('hrrr_ecmwf', '2026-11-18') + 0.8 * recordSlr('hrrr_ecmwf', '2026-11-19')) / 10;
    expect(Math.abs(day18.totalSnowfall - closed)).toBeLessThan(TOL);
    expect(out[3].modelWindow?.kind).toBe('kuchera_fallback');
  });

  it('literal ecmwf mode: every day adds up', () => {
    const days = [
      day('2026-11-13', fullDay('2026-11-13', 0.3)),
      day('2026-11-14', fullDay('2026-11-14', 0.3)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    expectDaySumInvariant(out);
    expect(Math.abs(windowSum(out, '2026-11-14') - expectedWindowSnow(days, 'ecmwf', '2026-11-14'))).toBeLessThan(1e-9);
  });

  it('the shipped negative-snowfall record (window ending 2026-11-15) still serves its SLR', () => {
    // The 00Z batch ships a genuinely negative stored snowfall at
    // Palisades for the window ending 2026-11-15 (ECMWF lead 6,
    // -0.0266 cm) -- an accumulation-differencing artefact of the
    // batch. Under the pure-SLR semantics the stored snowfall is never
    // consumed: the record's (positive) SLR converts the substrate's
    // frozen SWE, the day stays a MODEL day, and the displayed total
    // remains exactly the sum of the day's bars.
    const result = modelForecastFor(palisades, 'ecmwf', 'min', '2026-11-15');
    expect(result?.forecast.modelSnowfallCm as number).toBeLessThan(0);
    expect(result?.forecast.modelSnowfallCm as number).toBeGreaterThan(-0.03);
    const days = [
      day('2026-11-14', fullDay('2026-11-14', 0.3)),
      day('2026-11-15', fullDay('2026-11-15', 0.3)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    expect(out[1].modelWindow?.kind).toBe('model');
    expect(Math.abs(windowSum(out, '2026-11-15') - expectedWindowSnow(days, 'ecmwf', '2026-11-15'))).toBeLessThan(1e-9);
    expect(windowSum(out, '2026-11-15')).toBeGreaterThan(0);
    expectDaySumInvariant(out);
  });

  it('literal hrrr mode: model day and fallback day both add up', () => {
    // The 00Z batch's only HRRR window ends 2026-11-11 (lead 2): that
    // calendar day is the model day, the next is Kuchera fallback.
    const days = [
      day('2026-11-11', fullDay('2026-11-11', 0.4)),
      day('2026-11-12', fullDay('2026-11-12', 0.4)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'hrrr', 'min');
    expect(out[0].modelWindow?.kind).toBe('model');
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

describe('day-sum invariant: array boundaries', () => {
  it('array boundaries: a truncated first window converts only the hours present; the last afternoon stays substrate', () => {
    const days = [
      day('2026-11-13', fullDay('2026-11-13', 0.3)),
      day('2026-11-14', fullDay('2026-11-14', 0.3)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    // The window ending 2026-11-13 starts before the array (its
    // 2026-11-12 afternoon is absent): the 8 present morning hours are
    // converted by that window's SLR (frozen 0.15 mm each), nothing more.
    const slr13 = recordSlr('ecmwf', '2026-11-13');
    for (const h of out[0].hourly.slice(0, 8)) {
      expect(Math.abs(h.snowfall - (0.15 * slr13) / 10)).toBeLessThan(1e-9);
    }
    // The last day's afternoon belongs to the window ending 2026-11-15,
    // which is not a day in the array, so no record touches it.
    for (const h of out[1].hourly.slice(8)) {
      expect(h.snowfall).toBe(0.3);
    }
    expectDaySumInvariant(out);
  });
});
