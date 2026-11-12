import { describe, expect, it } from 'vitest';
import {
  applyModelMethodToDays,
  modelForecastData,
  modelForecastFor,
  modelWindowEndDateForHour,
  modelWindowTrendLabel,
  setModelForecastDataForTests,
  type ModelForecastData,
} from '../model-forecast';
import { addCalendarDays, possibleInstantsForLocalTime, providerLocalTimeToEpochMs } from '../time';
import type { BlendedHour, DayData, Location, WindowData } from '../types';

/**
 * Per-window SLR application under the ENS-EB menu method (2026-11-11
 * pure-SLR semantics, replacing the 2026-11-01 rescale-to-record-total
 * suite): inside a model window each hour's snowfall becomes
 * frozenSweMm x recordSlr / 10, the hour's frozen SWE and precipitation
 * pass through untouched, and the day total is the sum of the calendar
 * day's converted hours. Fixtures are built against the Palisades Tahoe
 * base catalogue point and the installed serving batch; every window
 * date is read from the file's own rows (batch-agnostic — the batch
 * rolls over twice a day) and record SLRs are read back through the
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

const palisadesPoint = modelForecastData.points.find((p) => p.pointId === 'palisades-tahoe-base');
const ecmwfRows = palisadesPoint?.forecasts.ecmwf ?? [];

/**
 * Controlled HRRR fixture: the real Palisades point carrying a
 * single HRRR record on a fixed date (2026-11-15 — mid-November
 * dates never fall on a US DST transition), injected through the
 * test seam so the scenario is independent of the installed batch
 * and of DST. Integration coverage of the real file's own HRRR row
 * lives in the resolver tests.
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
/** Window-end date of the n-th stored ECMWF row (0-based) at Palisades. */
const ecmwfEnd = (i: number): string => ecmwfRows[i].windowEndLocalDate;
/** The last stored ECMWF window-end date at Palisades (lead 10, the cap). */
const lastEcmwfEnd = (): string => ecmwfRows[ecmwfRows.length - 1].windowEndLocalDate;

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
    // Anchored on the file's last three stored ECMWF windows (e1, e2,
    // e3): the window after e3 holds no record (at or beyond the
    // cap). The triple is the last DST-clean one in the file.
    let e1 = '';
    let e2 = '';
    let e3 = '';
    for (let i = ecmwfRows.length - 1; i >= 2; i--) {
      if (cleanEnd(i) && cleanEnd(i - 1) && cleanEnd(i - 2)) {
        e3 = ecmwfEnd(i);
        e2 = ecmwfEnd(i - 1);
        e1 = ecmwfEnd(i - 2);
        break;
      }
    }
    expect(e3, 'a DST-clean trailing triple exists in the batch').not.toBe('');
    const days = [
      day(e1, [
        ...labels(e1, 0, 7).map((l) => hour(l, 4.5)), // window ending e1
        ...labels(e1, 8, 23).map((l) => hour(l, 0.25)), // window ending e2
      ]),
      day(e2, [
        ...labels(e2, 0, 7).map((l) => hour(l, 0.125)), // window ending e2
        ...labels(e2, 8, 23).map((l) => hour(l, 0.05)), // window ending e3
      ]),
      day(e3, [
        ...labels(e3, 0, 7).map((l) => hour(l, 0.05)), // window ending e3
        ...labels(e3, 8, 23).map((l) => hour(l, 4.5)), // window ending e3 + 1 (no record)
      ]),
    ];
    const before = days.map((d) => d.hourly.map((h) => [h.snowfall, h.frozenSweMm]));

    const out = applyModelMethodToDays(days, palisades, 'hrrr_ecmwf', 'min');

    // Window ending e2: frozen total = 16 x 0.25 + 8 x 0.125 = 5 mm.
    const slrE2 = recordSlr('hrrr_ecmwf', e2);
    const w1 = windowHours(out, e2);
    expect(w1).toHaveLength(24);
    expect(Math.abs(w1.reduce((s, h) => s + h.snowfall, 0) - (5 * slrE2) / 10)).toBeLessThan(EPS);
    for (const h of w1) {
      expect(h.slr).toBe(slrE2);
      expect(Math.abs(h.snowfall - ((h.frozenSweMm ?? 0) * slrE2) / 10)).toBeLessThan(EPS);
    }
    // The mark on the e2 day carries the applied window snowfall.
    expect(Math.abs((out[1].modelWindow?.snowfallCm as number) - (5 * slrE2) / 10)).toBeLessThan(EPS);

    // The day total follows the calendar day: the sum of the day's own
    // converted hours (morning from this window, afternoon from the next).
    expect(Math.abs(out[1].totalSnowfall - out[1].hourly.reduce((sum, h) => sum + h.snowfall, 0))).toBeLessThan(EPS);

    // The e2 calendar day straddles two windows: its 08:00-23:00
    // hours are converted by the e3 window's SLR, not this one.
    const slrE3 = recordSlr('hrrr_ecmwf', e3);
    const w2 = windowHours(out, e3);
    const frozenE3 = w2.reduce((s, h) => s + (h.frozenSweMm ?? 0), 0);
    expect(Math.abs(w2.reduce((s, h) => s + h.snowfall, 0) - (frozenE3 * slrE3) / 10)).toBeLessThan(EPS);
    const dayE2Afternoon = out[1].hourly.filter((h) => Number(h.time.slice(11, 13)) >= 8);
    expect(dayE2Afternoon.every((h) => Math.abs(h.snowfall - (0.05 * slrE3) / 10) < EPS)).toBe(true);

    // The first window is truncated by the array boundary (its
    // previous-day afternoon is absent): the 8 present hours are
    // converted by their own window's SLR — nothing is allocated or
    // rescaled.
    const slrE1 = recordSlr('hrrr_ecmwf', e1);
    const w0 = windowHours(out, e1);
    expect(w0).toHaveLength(8);
    expect(Math.abs(w0.reduce((s, h) => s + h.snowfall, 0) - (36 * slrE1) / 10)).toBeLessThan(EPS);

    // Inputs were not mutated.
    days.forEach((d, di) =>
      d.hourly.forEach((h, hi) => {
        expect(h.snowfall).toBe(before[di][hi][0]);
        expect(h.frozenSweMm).toBe(before[di][hi][1]);
      }),
    );
  });

  it('literal ecmwf mode: window hours convert by the GRIB record SLR', () => {
    const end = ecmwfEnd(firstCleanIndex(2));
    const prev = addCalendarDays(end, -1);
    const days = [
      day(prev, fullDay(prev, 0.15)),
      day(end, fullDay(end, 0.15)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const slr = recordSlr('ecmwf', end);
    const w = windowHours(out, end);
    expect(w).toHaveLength(24);
    expect(Math.abs(w.reduce((s, h) => s + h.snowfall, 0) - (3.6 * slr) / 10)).toBeLessThan(EPS);
    // Day total = the calendar-day sum of the converted hours, not a window value.
    expect(Math.abs(out[1].totalSnowfall - out[1].hourly.reduce((sum, h) => sum + h.snowfall, 0))).toBeLessThan(EPS);
  });

  it('beyond the cap: the window ending one day past the last stored window has no record in any mode and is never converted', () => {
    // The lead-11 date at Palisades, derived from the file: one day
    // past its last stored window (lead 10). Before the 2026-11-10
    // cap this window was served by the Open-Meteo tail arm; the arm
    // is retired and the window now resolves to nothing, so its
    // hours stay Kuchera.
    const lastDate = lastEcmwfEnd();
    const beyond = [1, 2, 3].map((k) => addCalendarDays(lastDate, k)).find((d) => !windowStraddlesDst(d));
    if (!beyond) throw new Error('no DST-clean beyond-cap window in batch');
    const before = addCalendarDays(beyond, -1);
    const after = addCalendarDays(beyond, 1);
    for (const mode of ['ecmwf', 'hrrr_ecmwf', 'hrrr']) {
      expect(modelForecastFor(palisades, mode, 'min', beyond)).toBeNull();
    }
    const days = [
      day(before, fullDay(before, 0.5)),
      day(beyond, fullDay(beyond, 0.5)),
      day(after, fullDay(after, 0.5)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const w = windowHours(out, beyond);
    expect(w).toHaveLength(24);
    const originals = days.flatMap((d) => d.hourly);
    for (const h of w) expect(originals).toContain(h);
    expect(out[1].modelWindow?.kind).toBe('kuchera_fallback');
    expect(out[1].totalSnowfall).toBe(24);
  });

  it('a dry window (no frozen SWE) converts to zero snowfall and keeps the record SLR on its hours', () => {
    // Anchored on the file's first DST-clean stored ECMWF window.
    const first = ecmwfEnd(firstCleanIndex(0));
    const prev = addCalendarDays(first, -1);
    const days = [
      day(prev, fullDay(prev, 0)),
      day(first, fullDay(first, 0)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    const w = windowHours(out, first);
    expect(w).toHaveLength(24);
    expect(w.every((h) => h.snowfall === 0)).toBe(true);
    const slr = recordSlr('ecmwf', first);
    expect(w.every((h) => h.slr === slr)).toBe(true);
    expect(out[1].modelWindow?.snowfallCm).toBe(0);
    // The day total follows the calendar day: the 08:00-23:00 hours
    // belong to the NEXT window; with zero frozen SWE everywhere
    // they contribute zero under any conversion.
    expect(out[1].totalSnowfall).toBe(0);
  });

  it('fallback windows keep Kuchera hours, identical objects (literal hrrr, the window after the HRRR day)', () => {
    // Controlled batch (injectSingleHrrrRecord): the only HRRR
    // window ends 2026-11-15; the window ending the next day has no
    // HRRR record, so the day stays Kuchera.
    injectSingleHrrrRecord();
    try {
      const hrrrEnd = '2026-11-15';
      const fallbackEnd = '2026-11-16';
      expect(modelForecastFor(palisades, 'hrrr', 'min', fallbackEnd)).toBeNull();
      const days = [
        day(hrrrEnd, fullDay(hrrrEnd, 0.2)),
        day(fallbackEnd, fullDay(fallbackEnd, 0.2)),
      ];
      const out = applyModelMethodToDays(days, palisades, 'hrrr', 'min');
      expect(out[0].modelWindow?.kind).toBe('model');
      expect(out[1].modelWindow?.kind).toBe('kuchera_fallback');
      const w = windowHours(out, fallbackEnd);
      expect(w).toHaveLength(24);
      for (const h of w) {
        const original = days.flatMap((d) => d.hourly).find((o) => o.time === h.time && modelWindowEndDateForHour(o, TZ) === fallbackEnd && o.instantMs === h.instantMs);
        expect(h).toBe(original);
        expect(h.snowfall).toBe(0.4);
      }
    } finally {
      setModelForecastDataForTests(null);
    }
  });

  it("partial first day: the not-applied disclaimer is scoped to its window, while the afternoon hours are converted by the next window", () => {
    // The array starts at W 08:00, where W is a stored ECMWF window
    // end read from the file: NO hour of the window ending W is in
    // view, so that window's mark keeps snowfallCm null — but the
    // day's afternoon hours belong to the window ending W + 1 and
    // ARE converted by its record SLR. The trend label must not
    // claim the day shows substrate values (Codex confirmation gate,
    // 2026-11-11).
    const wi = (() => {
      for (let i = 2; i + 1 < ecmwfRows.length; i++) if (cleanEnd(i) && cleanEnd(i + 1)) return i;
      throw new Error('no DST-clean window pair in batch');
    })();
    const w0 = ecmwfEnd(wi);
    const w1 = addCalendarDays(w0, 1);
    const days = [
      day(w0, labels(w0, 8, 23).map((l) => hour(l, 0.15))),
      day(w1, fullDay(w1, 0.15)),
    ];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    expect(out[0].modelWindow?.kind).toBe('model');
    expect(out[0].modelWindow?.snowfallCm).toBeNull();
    const slr14 = recordSlr('ecmwf', w1);
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
