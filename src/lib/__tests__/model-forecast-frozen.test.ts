import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import frozenFixture from './fixtures/model-forecasts-frozen.json';
import {
  applyModelMethodToDays,
  modelForecastFor,
  modelWindowEndDateForHour,
  setModelForecastDataForTests,
} from '../model-forecast';
import type { ModelForecastData } from '../model-forecast';
import { possibleInstantsForLocalTime, providerLocalTimeToEpochMs } from '../time';
import type { BlendedHour, DayData, Location, WindowData } from '../types';

/**
 * Batch-independent serving fixtures (Codex fix round on the California
 * catalogue expansion). The stored-batch suites re-anchor whenever the
 * served batch refreshes, and two scenarios cannot be expressed against
 * the 2026-11-09 batch at all: a real DST fall-back day (that batch
 * spans Nov 10-23; the 2026 fold was Nov 1), and a blend sequence with
 * distinct POSITIVE HRRR and ECMWF records (the batch's Palisades
 * day-1/day-2 records are both exactly 0).
 *
 * fixtures/model-forecasts-frozen.json contains the REAL
 * palisades-tahoe-base point from the 2026-10-31 12Z batch (blend day-1
 * record 10.72 cm -- the value from the original day-sum bug report),
 * plus a synthetic point at (39.5, -120.5) whose HRRR window ending
 * 2026-11-01 holds 12.0 cm and whose ECMWF window ending 2026-11-02
 * holds 6.0 cm.
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

const synthetic: Location = {
  id: 'synthetic-fixture',
  name: 'Synthetic Fixture',
  latitude: 39.5,
  longitude: -120.5,
  elevationFt: 8200,
  elevationM: 2500,
  minElevationM: 2000,
  maxElevationM: 3000,
  referencePoints: [
    { id: 'synthetic-base', name: 'Synthetic base', role: 'base', latitude: 39.5, longitude: -120.5, elevationM: 2000, geometrySource: 'estimated' },
    { id: 'synthetic-mid', name: 'Synthetic mid', role: 'mid_mountain', latitude: 39.5, longitude: -120.5, elevationM: 2500, geometrySource: 'estimated' },
    { id: 'synthetic-summit', name: 'Synthetic summit', role: 'summit', latitude: 39.5, longitude: -120.5, elevationM: 3000, geometrySource: 'estimated' },
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

/** Expected applied window snowfall under the pure-SLR semantics: record SLR x the window's substrate frozen SWE (fixture hours carry frozen = snowfall / 2) / 10. */
function expectedWindowSnow(inputDays: DayData[], loc: Location, endDate: string): number {
  const slr = modelForecastFor(loc, 'hrrr_ecmwf', 'min', endDate)?.forecast.modelSlr as number;
  const frozen = windowHours(inputDays, endDate).reduce((s, h) => s + (h.frozenSweMm ?? 0), 0);
  return (frozen * slr) / 10;
}

describe('frozen serving fixture (2026-10-31 batch + synthetic point)', () => {
  beforeAll(() => {
    setModelForecastDataForTests(frozenFixture as unknown as ModelForecastData);
  });
  afterAll(() => {
    setModelForecastDataForTests(null);
  });

  it('is the frozen batch, with the reported Palisades day-1 record intact', () => {
    const rec = modelForecastFor(palisades, 'hrrr_ecmwf', 'min', '2026-11-01');
    expect(rec?.cycleUtc).toBe('2026-10-31T12:00:00Z');
    expect(rec?.arm).toBe('hrrr');
    expect(rec?.forecast.modelSnowfallCm).toBeCloseTo(10.72005826894333, 9);
  });

  it('blend mode: distinct HRRR day-1 and ECMWF day-2 records are selected and each converts its own window', () => {
    expect(modelForecastFor(synthetic, 'hrrr_ecmwf', 'min', '2026-11-01')?.arm).toBe('hrrr');
    expect(modelForecastFor(synthetic, 'hrrr_ecmwf', 'min', '2026-11-02')?.arm).toBe('ecmwf');
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
    const out = applyModelMethodToDays(days, synthetic, 'hrrr_ecmwf', 'min');
    // Pure-SLR semantics (2026-11-11): each window's hours are converted
    // by that window's record SLR (HRRR 10, ECMWF 8 in this fixture) on
    // the SAME substrate frozen SWE -- the records' stored snowfalls
    // (12.0 / 6.0 cm) are not used.
    expect(Math.abs(windowSum(out, '2026-11-01') - expectedWindowSnow(days, synthetic, '2026-11-01'))).toBeLessThan(1e-9);
    expect(Math.abs(windowSum(out, '2026-11-02') - expectedWindowSnow(days, synthetic, '2026-11-02'))).toBeLessThan(1e-9);
    // Nov 1's afternoon hours belong to the Nov 2 window: frozen 0.05 mm
    // each, converted at the ECMWF SLR 8 -> 0.04 cm each.
    for (const h of out[1].hourly.slice(8)) {
      expect(Math.abs(h.snowfall - 0.04)).toBeLessThan(1e-9);
    }
    expectDaySumInvariant(out);
  });

  it('fall-back day 2026-11-01: both real 01:00 instants survive, share one window, and the window converts by its record SLR', () => {
    const dupInstants = possibleInstantsForLocalTime('2026-11-01T01:00', TZ);
    expect(dupInstants).toHaveLength(2);
    expect(dupInstants[1] - dupInstants[0]).toBe(3_600_000);
    const ones = dupInstants.map(
      (instantMs) =>
        ({
          ...hour('2026-11-01T01:00', 0.25),
          instantMs,
          dateObj: new Date(instantMs),
        }) as BlendedHour,
    );
    const day1Hours: BlendedHour[] = [
      hour('2026-11-01T00:00', 0.25),
      ...ones,
      ...labels('2026-11-01', 2, 7).map((l) => hour(l, 0.25)),
      ...labels('2026-11-01', 8, 23).map((l) => hour(l, 0.1)),
    ];
    expect(day1Hours).toHaveLength(25);
    const days = [
      day('2026-10-31', [
        ...labels('2026-10-31', 0, 7).map((l) => hour(l, 9)),
        ...labels('2026-10-31', 8, 23).map((l) => hour(l, 0.5)),
      ]),
      day('2026-11-01', day1Hours),
      day('2026-11-02', [
        ...labels('2026-11-02', 0, 7).map((l) => hour(l, 0.1)),
        ...labels('2026-11-02', 8, 23).map((l) => hour(l, 9)),
      ]),
    ];
    const out = applyModelMethodToDays(days, palisades, 'hrrr_ecmwf', 'min');
    const outDay1 = out[1];
    expect(outDay1.hourly).toHaveLength(25);
    // 25 DISTINCT instants -- the property the constructed fixture lacked.
    expect(new Set(outDay1.hourly.map((h) => h.instantMs)).size).toBe(25);
    const onesOut = outDay1.hourly.filter((h) => h.time === '2026-11-01T01:00');
    expect(new Set(onesOut.map((h) => h.instantMs))).toEqual(new Set(dupInstants));
    expect(Math.abs(windowSum(out, '2026-11-01') - expectedWindowSnow(days, palisades, '2026-11-01'))).toBeLessThan(1e-9);
    expectDaySumInvariant(out);
  });
});
