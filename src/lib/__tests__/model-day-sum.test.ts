import { describe, expect, it } from 'vitest';
import {
  applyModelMethodToDays,
  modelForecastData,
  modelForecastFor,
  modelWindowEndDateForHour,
  setModelForecastDataForTests,
  type ModelForecastData,
} from '../model-forecast';
import { groupData } from '../data';
import { addCalendarDays, providerLocalTimeToEpochMs } from '../time';
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

// Batch-agnostic anchors: window dates are read from the installed
// file's own Palisades rows, so the suite survives batch rollovers.
const palisadesPoint = modelForecastData.points.find((p) => p.pointId === 'palisades-tahoe-base');
const ecmwfRows = palisadesPoint?.forecasts.ecmwf ?? [];
const hrrrRows = palisadesPoint?.forecasts.hrrr ?? [];
const ecmwfEnd = (i: number): string => ecmwfRows[i].windowEndLocalDate;

/**
 * True when the 24 h window ending on `endDate` (Palisades local)
 * spans a DST transition: its two calendar days are not both 24 h,
 * so label-built fixtures (00..23) would mis-count its hours. The
 * fixture scenarios below select DST-clean windows (Codex gate,
 * 2026-11-12); at most one transition can fall inside a batch's
 * 10-day horizon, so a clean selection always exists.
 */
function windowStraddlesDst(endDate: string): boolean {
  const dayLengthH = (d: string) =>
    (providerLocalTimeToEpochMs(`${addCalendarDays(d, 1)}T00:00`, TZ) -
      providerLocalTimeToEpochMs(`${d}T00:00`, TZ)) / 3_600_000;
  return dayLengthH(addCalendarDays(endDate, -1)) !== 24 || dayLengthH(endDate) !== 24;
}
const cleanEnd = (i: number): boolean => !windowStraddlesDst(ecmwfEnd(i));
/** First stored window index >= `from` whose window is DST-clean. */
function firstCleanIndex(from: number): number {
  for (let i = from; i < ecmwfRows.length; i++) if (cleanEnd(i)) return i;
  throw new Error('no DST-clean window in batch');
}

/**
 * Controlled HRRR fixture: the real Palisades point carrying a
 * single HRRR record on a fixed date (2026-11-15 — mid-November
 * dates never fall on a US DST transition), injected through the
 * test seam so the scenario is independent of the installed batch
 * and of DST. Integration coverage of the real file's own HRRR row
 * lives in the resolver tests and the blend test above.
 */
function injectSingleHrrrRecord(): void {
  const synthetic = JSON.parse(JSON.stringify(modelForecastData)) as ModelForecastData;
  const point = synthetic.points.find((p) => p.pointId === 'palisades-tahoe-base');
  if (!point) throw new Error('fixture point missing');
  point.forecasts = {
    hrrr: [
      {
        leadDay: 2,
        windowStartUtc: '2026-11-14T15:00:00Z',
        windowEndUtc: '2026-11-15T15:00:00Z',
        windowEndLocalDate: '2026-11-15',
        modelSlr: 13.25,
        modelSnowfallCm: 4.2,
        kucheraSlr: 10,
        kucheraSnowfallCm: 3,
        liquidMm: 6,
      },
    ],
  };
  setModelForecastDataForTests(synthetic);
}

describe('day-sum invariant under the ENS-EB method', () => {
  it('blend mode: every day adds up, model and fallback days alike', () => {
    // Anchored on three consecutive DST-clean stored ECMWF windows
    // read from the file (e1, e2, e3), plus a fallback day well past
    // the last stored window.
    let triple: [string, string, string] | null = null;
    for (let i = 4; i + 2 < ecmwfRows.length; i++) {
      if (cleanEnd(i) && cleanEnd(i + 1) && cleanEnd(i + 2)) {
        triple = [ecmwfEnd(i), ecmwfEnd(i + 1), ecmwfEnd(i + 2)];
        break;
      }
    }
    if (!triple) throw new Error('no DST-clean window triple in batch');
    const [e1, e2, e3] = triple;
    const fallbackDate = addCalendarDays(ecmwfRows[ecmwfRows.length - 1].windowEndLocalDate, 5);
    const days = [
      day(e1, [
        ...labels(e1, 0, 7).map((l) => hour(l, 9)),
        ...labels(e1, 8, 23).map((l) => hour(l, 0.5)),
      ]),
      day(e2, [
        ...labels(e2, 0, 7).map((l) => hour(l, 0.25)),
        ...labels(e2, 8, 23).map((l) => hour(l, 0.1)),
      ]),
      day(e3, [
        ...labels(e3, 0, 7).map((l) => hour(l, 0.1)),
        ...labels(e3, 8, 23).map((l) => hour(l, 9)),
      ]),
      // Beyond the last stored window (the file's lead-10 date): no
      // arm holds a record for this day's windows, so it stays Kuchera.
      day(fallbackDate, fullDay(fallbackDate, 0.2)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'hrrr_ecmwf', 'min');
    expectDaySumInvariant(out);

    // Each model window's hours sum to its SLR applied to the SAME
    // substrate frozen SWE (not to the record's stored snowfall).
    expect(Math.abs(windowSum(out, e2) - expectedWindowSnow(days, 'hrrr_ecmwf', e2))).toBeLessThan(1e-9);
    expect(Math.abs(windowSum(out, e3) - expectedWindowSnow(days, 'hrrr_ecmwf', e3))).toBeLessThan(1e-9);
    // Inside its horizon the blend still privileges the HRRR arm:
    // the file's HRRR window date resolves to the HRRR arm.
    const hrrrDate = hrrrRows[0].windowEndLocalDate;
    expect(modelForecastFor(palisades, 'hrrr_ecmwf', 'min', hrrrDate)?.arm).toBe('hrrr');

    // Closed form for the straddling day e2: morning frozen
    // 8 x 0.125 = 1 mm under the e2 SLR, afternoon frozen
    // 16 x 0.05 = 0.8 mm under the e3 SLR.
    const dayE2 = out[1];
    const closed = (1 * recordSlr('hrrr_ecmwf', e2) + 0.8 * recordSlr('hrrr_ecmwf', e3)) / 10;
    expect(Math.abs(dayE2.totalSnowfall - closed)).toBeLessThan(TOL);
    expect(out[3].modelWindow?.kind).toBe('kuchera_fallback');
  });

  it('literal ecmwf mode: every day adds up', () => {
    const end = ecmwfEnd(firstCleanIndex(2));
    const prev = addCalendarDays(end, -1);
    const days = [
      day(prev, fullDay(prev, 0.3)),
      day(end, fullDay(end, 0.3)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    expectDaySumInvariant(out);
    expect(Math.abs(windowSum(out, end) - expectedWindowSnow(days, 'ecmwf', end))).toBeLessThan(1e-9);
  });

  it('a negative-snowfall record still serves its SLR (synthetic fixture)', () => {
    // Serving batches ship genuinely negative stored snowfalls
    // (accumulation-differencing artefacts; the 2026-11-10 batch's
    // Palisades record was -0.0266 cm). Which batch carries one at
    // which point varies per cycle, so the scenario is synthetic:
    // the real Palisades base point with its forecasts replaced by a
    // single record carrying a negative stored snowfall and a
    // positive SLR, injected through the test seam. Under the
    // pure-SLR semantics the stored snowfall is never consumed: the
    // record's SLR converts the substrate's frozen SWE, the day
    // stays a MODEL day, and the displayed total remains exactly the
    // sum of the day's bars.
    const D = '2026-11-15';
    const synthetic = JSON.parse(JSON.stringify(modelForecastData)) as ModelForecastData;
    const point = synthetic.points.find((p) => p.pointId === 'palisades-tahoe-base');
    if (!point) throw new Error('fixture point missing');
    point.forecasts = {
      ecmwf: [
        {
          leadDay: 6,
          windowStartUtc: '2026-11-14T15:00:00Z',
          windowEndUtc: '2026-11-15T15:00:00Z',
          windowEndLocalDate: D,
          modelSlr: 11.5,
          modelSnowfallCm: -0.0266,
          kucheraSlr: 10,
          kucheraSnowfallCm: 2.5,
          liquidMm: 5,
        },
      ],
    };
    setModelForecastDataForTests(synthetic);
    try {
      const result = modelForecastFor(palisades, 'ecmwf', 'min', D);
      expect(result?.forecast.modelSnowfallCm as number).toBeLessThan(0);
      expect(result?.forecast.modelSnowfallCm as number).toBeGreaterThan(-0.03);
      const prev = addCalendarDays(D, -1);
      const days = [
        day(prev, fullDay(prev, 0.3)),
        day(D, fullDay(D, 0.3)),
      ];
      const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
      expect(out[1].modelWindow?.kind).toBe('model');
      expect(Math.abs(windowSum(out, D) - expectedWindowSnow(days, 'ecmwf', D))).toBeLessThan(1e-9);
      expect(windowSum(out, D)).toBeGreaterThan(0);
      expectDaySumInvariant(out);
    } finally {
      setModelForecastDataForTests(null);
    }
  });

  it('installed batch: any record with negative stored snowfall still carries a consumable SLR', () => {
    // The file-level counterpart of the synthetic scenario: wherever
    // the CURRENT batch carries a differencing-artefact record, its
    // SLR must remain finite and in range (the only field the
    // pure-SLR path consumes). Passes trivially on a batch with no
    // negative records.
    for (const point of modelForecastData.points) {
      for (const rows of Object.values(point.forecasts)) {
        for (const row of rows ?? []) {
          if ((row.modelSnowfallCm ?? 0) < 0) {
            expect(Number.isFinite(row.modelSlr as number), point.pointId).toBe(true);
            expect(row.modelSlr as number).toBeGreaterThanOrEqual(3);
            expect(row.modelSlr as number).toBeLessThanOrEqual(30);
          }
        }
      }
    }
  });

  it('literal hrrr mode: model day and fallback day both add up', () => {
    // Controlled batch (injectSingleHrrrRecord): the only HRRR
    // window ends 2026-11-15 (lead 2): that calendar day is the
    // model day, the next is Kuchera fallback.
    injectSingleHrrrRecord();
    try {
      const hrrrEnd = '2026-11-15';
      const fallbackEnd = '2026-11-16';
      const days = [
        day(hrrrEnd, fullDay(hrrrEnd, 0.4)),
        day(fallbackEnd, fullDay(fallbackEnd, 0.4)),
      ];
      const out = applyModelMethodToDays(days, palisades, 'hrrr', 'min');
      expect(out[0].modelWindow?.kind).toBe('model');
      expect(out[1].modelWindow?.kind).toBe('kuchera_fallback');
      expectDaySumInvariant(out);
    } finally {
      setModelForecastDataForTests(null);
    }
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
    // Two consecutive DST-clean stored windows read from the file.
    const wi = (() => {
      for (let i = 2; i + 1 < ecmwfRows.length; i++) if (cleanEnd(i) && cleanEnd(i + 1)) return i;
      throw new Error('no DST-clean window pair in batch');
    })();
    const w1 = ecmwfEnd(wi);
    const w2 = ecmwfEnd(wi + 1);
    const days = [
      day(w1, fullDay(w1, 0.3)),
      day(w2, fullDay(w2, 0.3)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    // The window ending w1 starts before the array (its previous-day
    // afternoon is absent): the 8 present morning hours are
    // converted by that window's SLR (frozen 0.15 mm each), nothing more.
    const slrW1 = recordSlr('ecmwf', w1);
    for (const h of out[0].hourly.slice(0, 8)) {
      expect(Math.abs(h.snowfall - (0.15 * slrW1) / 10)).toBeLessThan(1e-9);
    }
    // The last day's afternoon belongs to the window ending w2 + 1,
    // which is not a day in the array, so no record touches it.
    for (const h of out[1].hourly.slice(8)) {
      expect(h.snowfall).toBe(0.3);
    }
    expectDaySumInvariant(out);
  });
});
