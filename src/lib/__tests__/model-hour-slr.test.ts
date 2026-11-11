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
 * Regression (2026-11-10, Mammoth summit; semantics replaced 2026-11-11):
 * inside a model window the rescaled hours used to present a hybrid SLR
 * -- model snowfall divided by the substrate's frozen SWE -- which
 * collapsed towards the ratio of the two forecasts when the live cycle
 * and the serving cycle disagreed about an event's size (live 78.0 cm
 * at SLR ~20 vs record 6.39 cm at SLR 11.9 presented ~2:1).
 *
 * Under the pure-SLR semantics (user directive, 2026-11-11) the hybrid
 * cannot arise: each model-window hour's snowfall is the record SLR x
 * the hour's OWN frozen SWE (untouched), and the hour carries the
 * record SLR, so every ratio consumer (hourly labels/colours, rolling
 * 24/48 h statistics) presents the model's SLR and the identity
 * snowfall / SLR = precipitation holds per hour and in aggregate.
 * Fixtures use the Palisades base point and the stored 2026-11-10 00Z
 * serving records, read back through the production resolver.
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
const RECORD_SLR = record.forecast.modelSlr as number;

describe('model-window hour SLR (Mammoth 2:1 regression, pure-SLR semantics)', () => {
  it('model-window hours carry the record SLR and snowfall = frozen SWE x record SLR / 10, frozen SWE untouched', () => {
    const days = [
      day('2026-11-13', labels('2026-11-13', 0, 23).map((l) => hour(l, 3))),
      day('2026-11-14', labels('2026-11-14', 0, 23).map((l) => hour(l, 3))),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const hours = windowHours(out, END);
    expect(hours.length).toBe(24);
    // Substrate hours: snowfall 3 cm at SLR 20 -> frozen SWE 1.5 mm each.
    const applied = (1.5 * RECORD_SLR) / 10;
    expect(hours.reduce((s, h) => s + h.snowfall, 0)).toBeCloseTo(24 * applied, 6);
    for (const h of hours) {
      expect(h.slr).toBe(RECORD_SLR);
      expect(h.frozenSweMm).toBeCloseTo(1.5, 9);
      expect(h.snowfall).toBeCloseTo(applied, 9);
      // The identity, per hour: snowfall / SLR = frozen precipitation.
      expect(h.snowfall / RECORD_SLR).toBeCloseTo((h.frozenSweMm ?? 0) / 10, 9);
    }
    // The rolling statistics divide snowfall by frozen SWE: over exactly
    // this window they return the record SLR and the applied snowfall.
    const ref = providerLocalTimeToEpochMs('2026-11-14T07:00', TZ);
    const stats = calculateRollingStats(out.flatMap((d) => d.hourly), ref);
    expect(stats.snow24h).toBeCloseTo(24 * applied, 6);
    expect(stats.slr24h).not.toBeNull();
    expect(stats.slr24h as number).toBeCloseTo(RECORD_SLR, 4);
  });

  it('rolling SLR across a model window and a fallback window is the frozen-weighted mean of the two -- each hour keeps its own ratio', () => {
    // Literal hrrr: the window ending 2026-11-11 is a model window, the
    // window ending 2026-11-12 is Kuchera fallback (substrate SLR 20).
    const hrrrSlr = modelForecastFor(palisades, 'hrrr', 'min', '2026-11-11')?.forecast.modelSlr as number;
    const days = ['2026-11-10', '2026-11-11', '2026-11-12'].map((d) =>
      day(d, labels(d, 0, 23).map((l) => hour(l, 3))),
    );
    const out = applyModelMethodToDays(days, palisades, 'hrrr', 'min');
    const ref = providerLocalTimeToEpochMs('2026-11-12T07:00', TZ);
    const stats = calculateRollingStats(out.flatMap((d) => d.hourly), ref);
    // 48 h = both windows, 36 mm frozen SWE each: the aggregate SLR is
    // the frozen-weighted mean, and snowfall / SLR = precipitation holds
    // for the aggregate exactly.
    const expectedSnow = (36 * hrrrSlr) / 10 + (36 * 20) / 10;
    expect(stats.snow48h).toBeCloseTo(expectedSnow, 6);
    expect(stats.slr48h).not.toBeNull();
    expect(stats.slr48h as number).toBeCloseTo((hrrrSlr + 20) / 2, 4);
    expect(((stats.snow48h / (stats.slr48h as number)) * 10)).toBeCloseTo(72, 4);
  });

  it('an invalid record SLR no longer resolves: the window falls back to Kuchera, hours untouched', () => {
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY]) {
      const doctored = JSON.parse(JSON.stringify(modelForecastData)) as typeof modelForecastData;
      const point = doctored.points.find((p) => p.pointId === 'palisades-tahoe-base');
      const row = point?.forecasts.ecmwf?.find((r) => r.windowEndLocalDate === END);
      if (!row) throw new Error('fixture record missing');
      row.modelSlr = bad;
      setModelForecastDataForTests(doctored);
      try {
        expect(modelForecastFor(palisades, 'ecmwf', 'min', END)).toBeNull();
        const days = [
          day('2026-11-13', labels('2026-11-13', 0, 23).map((l) => hour(l, 3))),
          day('2026-11-14', labels('2026-11-14', 0, 23).map((l) => hour(l, 3))),
        ];
        const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
        expect(out[1].modelWindow?.kind).toBe('kuchera_fallback');
        const hours = windowHours(out, END);
        const originals = days.flatMap((d) => d.hourly);
        for (const h of hours) expect(originals).toContain(h);
      } finally {
        setModelForecastDataForTests(null);
      }
    }
  });

  it('serving-file invariant: every record carries a finite SLR in [3, 30]', () => {
    // Under the pure-SLR semantics the SLR is the only quantity consumed
    // from a record, so the invariant now covers every row, not only
    // positive-snowfall ones.
    for (const point of modelForecastData.points) {
      for (const rows of Object.values(point.forecasts)) {
        for (const row of rows ?? []) {
          expect(row.modelSlr).not.toBeNull();
          expect(Number.isFinite(row.modelSlr as number)).toBe(true);
          expect(row.modelSlr as number).toBeGreaterThanOrEqual(3);
          expect(row.modelSlr as number).toBeLessThanOrEqual(30);
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
