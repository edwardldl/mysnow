import { describe, expect, it } from 'vitest';
import {
  applyModelMethodToDays,
  modelForecastData,
  modelForecastFor,
  modelWindowEndDateForHour,
  setModelForecastDataForTests,
} from '../model-forecast';
import { calculateRollingStats } from '../data';
import { providerLocalTimeToEpochMs } from '../time';
import type { BlendedHour, DayData, Location, WindowData } from '../types';

/**
 * Regression (2026-11-10, Mammoth summit): inside a model window the
 * rescaled hours used to keep a hybrid SLR -- the substrate's per-hour
 * SLR scaled by the model/substrate snowfall factor, equivalently model
 * snowfall divided by the substrate's frozen SWE. When the live cycle
 * and the serving cycle disagree about an event's size the hybrid
 * collapses towards the ratio of the two forecasts (live 78.0 cm at
 * SLR ~20 vs record 6.39 cm at SLR 11.9 presented ~2:1). Hours carrying
 * model snowfall must carry the record's own SLR and the record-implied
 * frozen SWE, so every ratio consumer (hourly labels/colours, rolling
 * 24/48 h statistics) presents the model's SLR. Fixtures use the
 * Palisades base point and the stored 2026-11-10 00Z serving records,
 * read back through the production resolver.
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

function hour(label: string, snowfall: number, opts: { liquidMM?: number; slr?: number | null; frozenSweMm?: number } = {}): BlendedHour {
  const instantMs = providerLocalTimeToEpochMs(label, TZ);
  const slr = opts.slr === undefined ? 20 : opts.slr;
  return {
    time: label,
    instantMs,
    dateObj: new Date(instantMs),
    providerTimezone: TZ,
    model: 'ecmwf',
    precipitation: opts.liquidMM ?? 0,
    liquidMM: opts.liquidMM ?? 0,
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
    frozenSweMm: opts.frozenSweMm ?? (slr ? (snowfall * 10) / slr : 0),
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
    maxWindowSnowfall: Math.max(0, ...hourly.map((h) => h.snowfall)),
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

const END = '2026-11-14';
const record = modelForecastFor(palisades, 'ecmwf', 'min', END);
if (!record) throw new Error('serving record missing for fixture window');
const R = record.forecast.modelSnowfallCm as number;
const RECORD_SLR = record.forecast.modelSlr as number;

describe('model-window hour SLR (Mammoth 2:1 regression)', () => {
  it('scaling branch: rescaled hours carry the record SLR, not the hybrid ratio', () => {
    const days = [
      day('2026-11-13', labels('2026-11-13', 0, 23).map((l) => hour(l, 3))),
      day('2026-11-14', labels('2026-11-14', 0, 23).map((l) => hour(l, 3))),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const hours = windowHours(out, END).filter((h) => h.snowfall > 0);
    expect(hours.length).toBe(24);
    expect(hours.reduce((s, h) => s + h.snowfall, 0)).toBeCloseTo(R, 6);
    for (const h of hours) {
      expect(h.slr).toBeCloseTo(RECORD_SLR, 6);
      expect(h.frozenSweMm ?? 0).toBeCloseTo((h.snowfall * 10) / RECORD_SLR, 6);
    }
    // The rolling statistics divide rescaled snowfall by frozen SWE:
    // over exactly this window they must return the record SLR.
    const ref = providerLocalTimeToEpochMs('2026-11-14T07:00', TZ);
    const stats = calculateRollingStats(out.flatMap((d) => d.hourly), ref);
    expect(stats.snow24h).toBeCloseTo(R, 6);
    expect(stats.slr24h).not.toBeNull();
    expect(stats.slr24h as number).toBeCloseTo(RECORD_SLR, 4);
  });

  it('distribution branch: distributed hours carry the record SLR', () => {
    const inWindow = new Set([...labels('2026-11-13', 8, 23), ...labels('2026-11-14', 0, 7)]);
    const mk = (dateStr: string) =>
      day(dateStr, labels(dateStr, 0, 23).map((l) =>
        inWindow.has(l)
          ? hour(l, 0, { liquidMM: 2, slr: null, frozenSweMm: 0 })
          : hour(l, 0, { liquidMM: 0, slr: null, frozenSweMm: 0 }),
      ));
    const out = applyModelMethodToDays([mk('2026-11-13'), mk('2026-11-14')], palisades, 'ecmwf', 'min');
    const hours = windowHours(out, END).filter((h) => h.snowfall > 0);
    expect(hours.length).toBe(24);
    expect(hours.reduce((s, h) => s + h.snowfall, 0)).toBeCloseTo(R, 6);
    for (const h of hours) {
      expect(h.slr).toBeCloseTo(RECORD_SLR, 6);
      expect(h.frozenSweMm ?? 0).toBeCloseTo((h.snowfall * 10) / RECORD_SLR, 6);
    }
    const ref = providerLocalTimeToEpochMs('2026-11-14T07:00', TZ);
    const stats = calculateRollingStats(out.flatMap((d) => d.hourly), ref);
    expect(stats.slr24h).not.toBeNull();
    expect(stats.slr24h as number).toBeCloseTo(RECORD_SLR, 4);
  });

  it('a zero-record window cannot dilute a neighbouring snowy window in rolling stats', () => {
    const rec12 = modelForecastFor(palisades, 'ecmwf', 'min', '2026-11-12');
    const rec13 = modelForecastFor(palisades, 'ecmwf', 'min', '2026-11-13');
    expect(rec12?.forecast.modelSnowfallCm).toBe(0);
    expect(rec13?.forecast.modelSnowfallCm as number).toBeGreaterThan(0);
    const slr13 = rec13?.forecast.modelSlr as number;
    const days = ['2026-11-11', '2026-11-12', '2026-11-13'].map((d) =>
      day(d, labels(d, 0, 23).map((l) => hour(l, 3))),
    );
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    for (const h of windowHours(out, '2026-11-12')) {
      expect(h.snowfall).toBe(0);
      expect(h.frozenSweMm ?? 0).toBe(0);
    }
    const ref = providerLocalTimeToEpochMs('2026-11-13T07:00', TZ);
    const stats = calculateRollingStats(out.flatMap((d) => d.hourly), ref);
    expect(stats.snow48h).toBeCloseTo(rec13?.forecast.modelSnowfallCm as number, 6);
    expect(stats.slr48h).not.toBeNull();
    expect(stats.slr48h as number).toBeCloseTo(slr13, 4);
  });

  it('an invalid record SLR presents no hour SLR and no frozen SWE, never the substrate hybrid', () => {
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY]) {
      const doctored = JSON.parse(JSON.stringify(modelForecastData)) as typeof modelForecastData;
      const point = doctored.points.find((p) => p.pointId === 'palisades-tahoe-base');
      const row = point?.forecasts.ecmwf?.find((r) => r.windowEndLocalDate === END);
      if (!row) throw new Error('fixture record missing');
      row.modelSlr = bad;
      setModelForecastDataForTests(doctored);
      try {
        const days = [
          day('2026-11-13', labels('2026-11-13', 0, 23).map((l) => hour(l, 3))),
          day('2026-11-14', labels('2026-11-14', 0, 23).map((l) => hour(l, 3))),
        ];
        const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
        const hours = windowHours(out, END).filter((h) => h.snowfall > 0);
        expect(hours.length).toBe(24);
        expect(hours.reduce((s, h) => s + h.snowfall, 0)).toBeCloseTo(R, 6);
        for (const h of hours) {
          expect(h.slr).toBeNull();
          expect(h.frozenSweMm ?? 0).toBe(0);
        }
      } finally {
        setModelForecastDataForTests(null);
      }
    }
  });

  it('rolling SLR is unavailable when an invalid-SLR window adjoins a valid one', () => {
    const doctored = JSON.parse(JSON.stringify(modelForecastData)) as typeof modelForecastData;
    const point = doctored.points.find((p) => p.pointId === 'palisades-tahoe-base');
    const row = point?.forecasts.ecmwf?.find((r) => r.windowEndLocalDate === END);
    if (!row) throw new Error('fixture record missing');
    row.modelSlr = Number.NaN;
    setModelForecastDataForTests(doctored);
    try {
      const rec13 = modelForecastFor(palisades, 'ecmwf', 'min', '2026-11-13');
      const snow13 = rec13?.forecast.modelSnowfallCm as number;
      const days = [
        day('2026-11-13', labels('2026-11-13', 0, 23).map((l) => hour(l, 3))),
        day('2026-11-14', labels('2026-11-14', 0, 23).map((l) => hour(l, 3))),
      ];
      const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
      const ref = providerLocalTimeToEpochMs('2026-11-14T07:00', TZ);
      const stats = calculateRollingStats(out.flatMap((d) => d.hourly), ref);
      // Snowfall totals are preserved; the SLR is unavailable rather than
      // borrowed from the valid window's SWE (which would inflate it).
      expect(stats.snow24h).toBeCloseTo(R, 6);
      expect(stats.slr24h).toBeNull();
      expect(stats.snow48h).toBeCloseTo(R + snow13, 6);
      expect(stats.slr48h).toBeNull();
    } finally {
      setModelForecastDataForTests(null);
    }
  });

  it('serving-file invariant: every positive-snowfall record carries a finite SLR in [3, 30]', () => {
    for (const point of modelForecastData.points) {
      for (const rows of Object.values(point.forecasts)) {
        for (const row of rows ?? []) {
          if ((row.modelSnowfallCm ?? 0) > 0) {
            expect(row.modelSlr).not.toBeNull();
            expect(Number.isFinite(row.modelSlr as number)).toBe(true);
            expect(row.modelSlr as number).toBeGreaterThanOrEqual(3);
            expect(row.modelSlr as number).toBeLessThanOrEqual(30);
          }
        }
      }
    }
  });

  it('non-model methods leave hour SLR and frozen SWE untouched', () => {
    const days = [day('2026-11-13', labels('2026-11-13', 0, 23).map((l) => hour(l, 3)))];
    const out = applyModelMethodToDays(days, palisades, 'best_match', 'min');
    for (const h of out[0].hourly) {
      expect(h.slr).toBe(20);
      expect(h.frozenSweMm).toBeCloseTo(1.5, 9);
    }
  });
});
