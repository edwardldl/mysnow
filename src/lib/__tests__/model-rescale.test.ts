import { describe, expect, it } from 'vitest';
import {
  applyModelMethodToDays,
  modelForecastFor,
  modelWindowEndDateForHour,
  modelWindowTrendLabel,
} from '../model-forecast';
import { possibleInstantsForLocalTime, providerLocalTimeToEpochMs } from '../time';
import type { BlendedHour, DayData, Location, WindowData } from '../types';

/**
 * Per-window SLR application under the ENS-EB menu method (2026-11-11
 * pure-SLR semantics, replacing the 2026-11-01 rescale-to-record-total
 * suite): inside a model window each hour's snowfall becomes
 * frozenSweMm x recordSlr / 10, the hour's frozen SWE and precipitation
 * pass through untouched, and the day total is the sum of the calendar
 * day's converted hours. Fixtures are built against the Palisades Tahoe
 * base catalogue point and the stored serving records of the
 * 2026-11-10 00Z batch; record SLRs are read back through the
 * production resolver rather than hard-coded. Substrate hours carry a
 * uniform Kuchera SLR of 20.
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

/** A substrate hour: snowfall = frozenSweMm x SUBSTRATE_SLR / 10. */
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

function fullDay(dateStr: string, frozenSweMm: number): BlendedHour[] {
  return labels(dateStr, 0, 23).map((l) => hour(l, frozenSweMm));
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

/** The 24 labelled hours of the model window ending 07:00 local on endDate. */
function windowHours(days: DayData[], endDate: string): BlendedHour[] {
  return days
    .flatMap((d) => d.hourly)
    .filter((h) => modelWindowEndDateForHour(h, TZ) === endDate);
}

function recordSlr(mode: string, endDate: string): number {
  const result = modelForecastFor(palisades, mode, 'min', endDate);
  expect(result, `record for ${mode} window ${endDate}`).not.toBeNull();
  return result?.forecast.modelSlr as number;
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

describe('applyModelMethodToDays SLR application', () => {
  it('blend mode: each window converts its own hours by its own record SLR, straddling two calendar days', () => {
    const days = [
      day('2026-11-17', [
        ...labels('2026-11-17', 0, 7).map((l) => hour(l, 4.5)), // window ending 2026-11-17
        ...labels('2026-11-17', 8, 23).map((l) => hour(l, 0.25)), // window ending 2026-11-18
      ]),
      day('2026-11-18', [
        ...labels('2026-11-18', 0, 7).map((l) => hour(l, 0.125)), // window ending 2026-11-18
        ...labels('2026-11-18', 8, 23).map((l) => hour(l, 0.05)), // window ending 2026-11-19
      ]),
      day('2026-11-19', [
        ...labels('2026-11-19', 0, 7).map((l) => hour(l, 0.05)), // window ending 2026-11-19
        ...labels('2026-11-19', 8, 23).map((l) => hour(l, 4.5)), // window ending 2026-11-20 (no record)
      ]),
    ];
    const before = days.map((d) => d.hourly.map((h) => [h.snowfall, h.frozenSweMm]));

    const out = applyModelMethodToDays(days, palisades, 'hrrr_ecmwf', 'min');

    // Window ending 2026-11-18: frozen total = 16 x 0.25 + 8 x 0.125 = 5 mm.
    const slr18 = recordSlr('hrrr_ecmwf', '2026-11-18');
    const w1 = windowHours(out, '2026-11-18');
    expect(w1).toHaveLength(24);
    expect(Math.abs(w1.reduce((s, h) => s + h.snowfall, 0) - (5 * slr18) / 10)).toBeLessThan(EPS);
    for (const h of w1) {
      expect(h.slr).toBe(slr18);
      expect(Math.abs(h.snowfall - ((h.frozenSweMm ?? 0) * slr18) / 10)).toBeLessThan(EPS);
    }
    // The mark on the 2026-11-18 day carries the applied window snowfall.
    expect(Math.abs((out[1].modelWindow?.snowfallCm as number) - (5 * slr18) / 10)).toBeLessThan(EPS);

    // The day total follows the calendar day: the sum of the day's own
    // converted hours (morning from this window, afternoon from the next).
    expect(Math.abs(out[1].totalSnowfall - out[1].hourly.reduce((sum, h) => sum + h.snowfall, 0))).toBeLessThan(EPS);

    // The 2026-11-18 calendar day straddles two windows: its 08:00-23:00
    // hours are converted by the 2026-11-19 window's SLR, not this one.
    const slr19 = recordSlr('hrrr_ecmwf', '2026-11-19');
    const w2 = windowHours(out, '2026-11-19');
    const frozen19 = w2.reduce((s, h) => s + (h.frozenSweMm ?? 0), 0);
    expect(Math.abs(w2.reduce((s, h) => s + h.snowfall, 0) - (frozen19 * slr19) / 10)).toBeLessThan(EPS);
    const day18Afternoon = out[1].hourly.filter((h) => Number(h.time.slice(11, 13)) >= 8);
    expect(day18Afternoon.every((h) => Math.abs(h.snowfall - (0.05 * slr19) / 10) < EPS)).toBe(true);

    // The first window is truncated by the array boundary (its
    // 2026-11-16 afternoon is absent): the 8 present hours are converted
    // by their own window's SLR -- nothing is allocated or rescaled.
    const slr17 = recordSlr('hrrr_ecmwf', '2026-11-17');
    const w0 = windowHours(out, '2026-11-17');
    expect(w0).toHaveLength(8);
    expect(Math.abs(w0.reduce((s, h) => s + h.snowfall, 0) - (36 * slr17) / 10)).toBeLessThan(EPS);

    // Inputs were not mutated.
    days.forEach((d, di) =>
      d.hourly.forEach((h, hi) => {
        expect(h.snowfall).toBe(before[di][hi][0]);
        expect(h.frozenSweMm).toBe(before[di][hi][1]);
      }),
    );
  });

  it('literal ecmwf mode: window hours convert by the GRIB record SLR', () => {
    const days = [
      day('2026-11-13', fullDay('2026-11-13', 0.15)),
      day('2026-11-14', fullDay('2026-11-14', 0.15)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const slr = recordSlr('ecmwf', '2026-11-14');
    const w = windowHours(out, '2026-11-14');
    expect(w).toHaveLength(24);
    expect(Math.abs(w.reduce((s, h) => s + h.snowfall, 0) - (3.6 * slr) / 10)).toBeLessThan(EPS);
    // Day total = the calendar-day sum of the converted hours, not a window value.
    expect(Math.abs(out[1].totalSnowfall - out[1].hourly.reduce((sum, h) => sum + h.snowfall, 0))).toBeLessThan(EPS);
  });

  it('beyond the cap: the window ending 2026-11-20 has no record in any mode and is never converted', () => {
    // Lead 11 at Palisades. Before the 2026-11-10 cap this window was
    // served by the Open-Meteo tail arm; the arm is retired and the
    // window now resolves to nothing, so its hours stay Kuchera.
    for (const mode of ['ecmwf', 'hrrr_ecmwf', 'hrrr']) {
      expect(modelForecastFor(palisades, mode, 'min', '2026-11-20')).toBeNull();
    }
    const days = [
      day('2026-11-19', fullDay('2026-11-19', 0.5)),
      day('2026-11-20', fullDay('2026-11-20', 0.5)),
      day('2026-11-21', fullDay('2026-11-21', 0.5)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const w = windowHours(out, '2026-11-20');
    expect(w).toHaveLength(24);
    const originals = days.flatMap((d) => d.hourly);
    for (const h of w) expect(originals).toContain(h);
    expect(out[1].modelWindow?.kind).toBe('kuchera_fallback');
    expect(out[1].totalSnowfall).toBe(24);
  });

  it('a dry window (no frozen SWE) converts to zero snowfall and keeps the record SLR on its hours', () => {
    const days = [
      day('2026-11-10', fullDay('2026-11-10', 0)),
      day('2026-11-11', fullDay('2026-11-11', 0)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const w = windowHours(out, '2026-11-11');
    expect(w).toHaveLength(24);
    expect(w.every((h) => h.snowfall === 0)).toBe(true);
    const slr = recordSlr('ecmwf', '2026-11-11');
    expect(w.every((h) => h.slr === slr)).toBe(true);
    expect(out[1].modelWindow?.snowfallCm).toBe(0);
    // The day total follows the calendar day: the 08:00-23:00 hours
    // belong to the NEXT window, which has no day in this fixture, so
    // they keep their (zero) substrate values.
    expect(out[1].totalSnowfall).toBe(0);
  });

  it('fallback windows keep Kuchera hours, identical objects (literal hrrr, the window after the HRRR day)', () => {
    // The batch's only HRRR window ends 2026-11-11; the window ending
    // 2026-11-12 has no HRRR record, so the day stays Kuchera.
    expect(modelForecastFor(palisades, 'hrrr', 'min', '2026-11-12')).toBeNull();
    const days = [
      day('2026-11-11', fullDay('2026-11-11', 0.2)),
      day('2026-11-12', fullDay('2026-11-12', 0.2)),
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

  it("partial first day: the not-applied disclaimer is scoped to its window, while the afternoon hours are converted by the next window", () => {
    // The array starts at 2026-11-13 08:00: NO hour of the window
    // ending 2026-11-13 is in view, so that window's mark keeps
    // snowfallCm null -- but the day's afternoon hours belong to the
    // window ending 2026-11-14 and ARE converted by its record SLR.
    // The trend label must not claim the day shows substrate values
    // (Codex confirmation gate, 2026-11-11).
    const days = [
      day('2026-11-13', labels('2026-11-13', 8, 23).map((l) => hour(l, 0.15))),
      day('2026-11-14', fullDay('2026-11-14', 0.15)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    expect(out[0].modelWindow?.kind).toBe('model');
    expect(out[0].modelWindow?.snowfallCm).toBeNull();
    const slr14 = recordSlr('ecmwf', '2026-11-14');
    for (const h of out[0].hourly) {
      expect(h.slr).toBe(slr14);
      expect(Math.abs(h.snowfall - (0.15 * slr14) / 10)).toBeLessThan(EPS);
    }
    const label = modelWindowTrendLabel(out[0].modelWindow!);
    expect(label).toContain("no hours from this window are in view -- this window's SLR was not applied");
    expect(label).not.toContain('substrate values');
  });

  it('method inactive: days returned unchanged', () => {
    const days = [day('2026-11-01', fullDay('2026-11-01', 0.25))];
    expect(applyModelMethodToDays(days, palisades, 'gfs', 'min')).toBe(days);
    expect(applyModelMethodToDays(days, null, 'ecmwf', 'min')).toBe(days);
  });
});
