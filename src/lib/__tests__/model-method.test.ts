import { createElement } from 'react';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import ModelForecastPanel from '../../components/ModelForecastPanel';
import { ALGORITHMS } from '../constants';
import {
  applyModelMethodToDays,
  BLEND_MODEL_ID,
  ENS_EB_METHOD_ID,
  modelForecastData,
  modelForecastFor,
  modelMethodAvailability,
  modelWindowStatuses,
} from '../model-forecast';
import { toSlrMethod } from '../snow/slr';
import type { DayData, Location } from '../types';

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
  timezone: 'America/Los_Angeles',
  isCustom: false,
} as Location;

const farAway: Location = {
  id: 'nowhere',
  name: 'Nowhere',
  latitude: 30.0,
  longitude: -90.0,
  elevationFt: 300,
  elevationM: 100,
  minElevationM: 90,
  maxElevationM: 110,
  timezone: 'America/Chicago',
  isCustom: true,
} as Location;

const stowe: Location = {
  id: 'stowe',
  name: 'Stowe',
  latitude: 44.53036,
  longitude: -72.788162,
  elevationFt: 2467,
  elevationM: 753,
  minElevationM: 397,
  maxElevationM: 1109,
  referencePoints: [
    { id: 'stowe-base', name: 'Base estimate', role: 'base', latitude: 44.53036, longitude: -72.788162, elevationM: 397, geometrySource: 'estimated' },
    { id: 'stowe-mid', name: 'Mid-mountain estimate', role: 'mid_mountain', latitude: 44.53036, longitude: -72.788162, elevationM: 753, geometrySource: 'estimated' },
    { id: 'stowe-summit', name: 'Summit estimate', role: 'summit', latitude: 44.53036, longitude: -72.788162, elevationM: 1109, geometrySource: 'estimated' },
  ],
  timezone: 'America/New_York',
  isCustom: false,
} as Location;

const mountRose: Location = {
  id: 'mount-rose',
  name: 'Mount Rose',
  latitude: 39.320736,
  longitude: -119.884359,
  elevationFt: 8748,
  elevationM: 2666.5,
  minElevationM: 2421,
  maxElevationM: 2912,
  referencePoints: [
    { id: 'mount-rose-base', name: 'Base estimate', role: 'base', latitude: 39.320736, longitude: -119.884359, elevationM: 2421, geometrySource: 'estimated' },
    { id: 'mount-rose-mid', name: 'Mid-mountain estimate', role: 'mid_mountain', latitude: 39.320736, longitude: -119.884359, elevationM: 2666.5, geometrySource: 'estimated' },
    { id: 'mount-rose-summit', name: 'Summit estimate', role: 'summit', latitude: 39.320736, longitude: -119.884359, elevationM: 2912, geometrySource: 'estimated' },
  ],
  timezone: 'America/Los_Angeles',
  isCustom: false,
} as Location;

const palisadesPoint = modelForecastData.points.find((p) => p.pointId === 'palisades-tahoe-base');
const ecmwfRows = palisadesPoint?.forecasts.ecmwf ?? [];
const omRows = palisadesPoint?.forecasts.ecmwf_openmeteo ?? [];
const allStoredDates = [...ecmwfRows, ...omRows].map((r) => r.windowEndLocalDate);
const day1Date = ecmwfRows[0]?.windowEndLocalDate;
const day2Date = ecmwfRows[1]?.windowEndLocalDate;
const day3Date = ecmwfRows[2]?.windowEndLocalDate;
const day4Date = ecmwfRows[3]?.windowEndLocalDate;
const day5Date = ecmwfRows[4]?.windowEndLocalDate;
const day9Date = ecmwfRows[8]?.windowEndLocalDate;
const stowePoint = modelForecastData.points.find((p) => p.pointId === 'stowe-base');
const stoweEcmwfRows = stowePoint?.forecasts.ecmwf ?? [];

describe('ENS-EB menu option', () => {
  it('is listed in the Physics menu (ALGORITHMS) with a model label', () => {
    const opt = ALGORITHMS.find((o) => o.id === ENS_EB_METHOD_ID);
    expect(opt).toBeDefined();
    expect(opt?.name).toContain('ENS-EB');
    expect(opt?.name).toContain('MySnow');
    expect(opt?.disabled).toBeUndefined();
  });

  it('uses the Kuchera hourly substrate (the model serves window aggregates only)', () => {
    expect(toSlrMethod(ENS_EB_METHOD_ID)).toBe('kuchera');
    expect(toSlrMethod('kuchera')).toBe('kuchera');
  });
});

describe('model method availability matrix', () => {
  it('is available at a catalogue point in ecmwf, hrrr and blend modes', () => {
    for (const mode of ['ecmwf', 'hrrr', BLEND_MODEL_ID]) {
      const availability = modelMethodAvailability(mode, palisades, 'min');
      expect(availability.available).toBe(true);
      expect(availability.reason).toBeNull();
      expect(availability.point?.pointId).toBe('palisades-tahoe-base');
    }
  });

  it('is available at Mount Rose (estimated-geometry catalogue point)', () => {
    for (const mode of ['ecmwf', 'hrrr', BLEND_MODEL_ID]) {
      const availability = modelMethodAvailability(mode, mountRose, 'min');
      expect(availability.available).toBe(true);
      expect(availability.reason).toBeNull();
      expect(availability.point?.pointId).toBe('mount-rose-base');
    }
  });

  it('is unavailable in other forecast-source modes, with the mode reason', () => {
    for (const mode of ['gfs', 'nam', 'nbm', 'icon_global', 'best_match', 'ecmwf_aifs', 'gem_regional', 'gem_hrdps_west']) {
      const availability = modelMethodAvailability(mode, palisades, 'min');
      expect(availability.available).toBe(false);
      expect(availability.reason).toBe('unsupported-mode');
    }
  });

  it('is unavailable at a non-catalogue location, with the location reason', () => {
    for (const mode of ['ecmwf', 'hrrr', BLEND_MODEL_ID]) {
      const availability = modelMethodAvailability(mode, farAway, 'avg');
      expect(availability.available).toBe(false);
      expect(availability.reason).toBe('non-catalogue-location');
    }
  });

  it('checks the source mode before the location', () => {
    expect(modelMethodAvailability('gfs', farAway, 'avg').reason).toBe('unsupported-mode');
  });

  it('is unavailable before locations load', () => {
    expect(modelMethodAvailability('ecmwf', undefined, 'min').available).toBe(false);
    expect(modelMethodAvailability('ecmwf', null, 'min').reason).toBe('non-catalogue-location');
  });
});

describe('model window statuses (shared menu/panel view)', () => {
  it('blend mode: every Palisades window resolves to the model with truthful arms', () => {
    const ctx = modelWindowStatuses(palisades, BLEND_MODEL_ID, 'min');
    expect(ctx?.point.pointId).toBe('palisades-tahoe-base');
    expect(ctx?.windows.map((w) => w.windowEndLocalDate)).toEqual(allStoredDates);
    expect(ctx?.windows.map((w) => [w.status, w.arm])).toEqual([
      ['model', 'hrrr'],
      ...ecmwfRows.slice(1).map(() => ['model', 'ecmwf']),
      ...omRows.map(() => ['model', 'ecmwf_openmeteo']),
    ]);
  });

  it('literal hrrr mode: windows past day 1 are marked fallback, never silently filled', () => {
    const ctx = modelWindowStatuses(palisades, 'hrrr', 'min');
    // The Open-Meteo tail windows are in the stored set, but literal
    // HRRR never resolves them -- they stay marked fallback.
    expect(ctx?.windows.map((w) => w.windowEndLocalDate)).toEqual(allStoredDates);
    expect(ctx?.windows.map((w) => w.status)).toEqual([
      'model',
      ...[...ecmwfRows.slice(1), ...omRows].map(() => 'fallback'),
    ]);
    expect(ctx?.windows[1].result).toBeNull();
    expect(ctx?.windows[2].result).toBeNull();
    expect(ctx?.windows[ctx.windows.length - 1].result).toBeNull();
  });

  it('literal ecmwf mode: every stored window resolves to the model', () => {
    const ctx = modelWindowStatuses(palisades, 'ecmwf', 'min');
    expect(ctx?.windows.map((w) => w.windowEndLocalDate)).toEqual(allStoredDates);
    expect(ctx?.windows.map((w) => w.status)).toEqual(allStoredDates.map(() => 'model'));
    expect(ctx?.windows.map((w) => w.arm)).toEqual([
      ...ecmwfRows.map(() => 'ecmwf'),
      ...omRows.map(() => 'ecmwf_openmeteo'),
    ]);
  });

  it('returns null where the method is unavailable', () => {
    expect(modelWindowStatuses(palisades, 'gfs', 'min')).toBeNull();
    expect(modelWindowStatuses(farAway, 'ecmwf', 'avg')).toBeNull();
    expect(modelWindowStatuses(undefined, 'ecmwf', 'min')).toBeNull();
  });
});

describe('applyModelMethodToDays', () => {
  const mkDay = (dateStr: string, totalSnowfall: number): DayData =>
    ({ dateStr, totalSnowfall }) as DayData;

  it('replaces day totals with the record snowfall in ecmwf mode and marks them model', () => {
    const days = [mkDay(day1Date, 999), mkDay(day2Date, 999), mkDay(day3Date, 999)];
    const out = applyModelMethodToDays(days, palisades, 'ecmwf', 'min');
    out.forEach((day, i) => {
      expect(day.totalSnowfall).toBe(ecmwfRows[i].modelSnowfallCm);
      expect(day.modelWindow?.kind).toBe('model');
      expect(day.modelWindow?.arm).toBe('ecmwf');
      expect(day.modelWindow?.slr).toBe(ecmwfRows[i].modelSlr);
    });
    // Input days are not mutated.
    expect(days[0].totalSnowfall).toBe(999);
    expect(days[0].modelWindow).toBeUndefined();
  });

  it('agrees exactly with the panel resolver in blend mode (same numbers, same arm)', () => {
    const out = applyModelMethodToDays([mkDay(day2Date, 0)], palisades, BLEND_MODEL_ID, 'min');
    const panel = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', day2Date);
    expect(panel).not.toBeNull();
    expect(out[0].totalSnowfall).toBe(panel?.forecast.modelSnowfallCm);
    expect(out[0].modelWindow?.arm).toBe('ecmwf');
    expect(out[0].modelWindow?.blended).toBe(true);
  });

  it('marks days without a record as Kuchera fallback and keeps their totals', () => {
    const out = applyModelMethodToDays([mkDay(day2Date, 7.5)], palisades, 'hrrr', 'min');
    expect(out[0].totalSnowfall).toBe(7.5);
    expect(out[0].modelWindow?.kind).toBe('kuchera_fallback');
    expect(out[0].modelWindow?.slr).toBeNull();

    const extra = applyModelMethodToDays([mkDay('2030-01-01', 3)], palisades, 'ecmwf', 'min');
    expect(extra[0].totalSnowfall).toBe(3);
    expect(extra[0].modelWindow?.kind).toBe('kuchera_fallback');
  });

  it('returns the days untouched when the method is unavailable', () => {
    const days = [mkDay(day1Date, 5)];
    expect(applyModelMethodToDays(days, palisades, 'gfs', 'min')).toBe(days);
    expect(applyModelMethodToDays(days, farAway, 'ecmwf', 'avg')).toBe(days);
    expect(applyModelMethodToDays(days, undefined, 'ecmwf', 'min')).toBe(days);
  });
});

describe('extended ECMWF horizon (step-240 open-data ceiling)', () => {
  const mkDay = (dateStr: string, totalSnowfall: number): DayData =>
    ({ dateStr, totalSnowfall }) as DayData;

  it('covers lead days 1-9 at Palisades (America/Los_Angeles) and 1-10 at Stowe (America/New_York)', () => {
    // The 07:00-local window end for lead k lands at step 24k+3 in
    // Los Angeles and 24k in New York: the step-240 ceiling admits
    // k <= 9 in the west and k <= 10 in the east (phase0_audit_extended).
    expect(ecmwfRows.map((r) => r.leadDay)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
    expect(stoweEcmwfRows.map((r) => r.leadDay)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  });

  it('resolves the day-5 window to the ECMWF arm in literal ecmwf mode', () => {
    const result = modelForecastFor(palisades, 'ecmwf', 'min', day5Date);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.blended).toBe(false);
    expect(result?.forecast.leadDay).toBe(5);
    expect(result?.forecast.modelSlr).toBe(ecmwfRows[4]?.modelSlr);
    expect(result?.forecast.modelSnowfallCm).toBe(ecmwfRows[4]?.modelSnowfallCm);
  });

  it('resolves blend day 4 and day 5 to the ECMWF arm (HRRR horizon is 48 h)', () => {
    const cases: Array<[string, (typeof ecmwfRows)[number] | undefined]> = [
      [day4Date, ecmwfRows[3]],
      [day5Date, ecmwfRows[4]],
    ];
    for (const [date, row] of cases) {
      const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', date);
      expect(result?.blended).toBe(true);
      expect(result?.arm).toBe('ecmwf');
      expect(result?.forecast.leadDay).toBe(row?.leadDay);
      expect(result?.forecast.modelSlr).toBe(row?.modelSlr);
      expect(result?.cycleUtc).toBe(modelForecastData.cycles.ecmwf);
    }
  });

  it('resolves the day-9 window (Palisades ceiling) and the day-10 window at Stowe', () => {
    const day9 = modelForecastFor(palisades, 'ecmwf', 'min', day9Date);
    expect(day9?.forecast.leadDay).toBe(9);
    expect(day9?.forecast.modelSlr).toBe(ecmwfRows[8]?.modelSlr);
    const stoweDay10 = stoweEcmwfRows[9];
    const day10 = modelForecastFor(stowe, 'ecmwf', 'min', stoweDay10?.windowEndLocalDate);
    expect(day10?.forecast.leadDay).toBe(10);
    expect(day10?.forecast.modelSlr).toBe(stoweDay10?.modelSlr);
  });

  it('has no Palisades GRIB record beyond the ceiling: the day-10 date is served by Open-Meteo', () => {
    // Stowe's day-10 window-end date is the same calendar date a
    // Palisades day-10 window would end on -- but that window ends at
    // step 243 > 240, so no Palisades GRIB record exists for it. Since
    // the Open-Meteo arm was added (2026-11-01) the date resolves to
    // that third arm instead of staying Kuchera.
    const beyondDate = stoweEcmwfRows[9]?.windowEndLocalDate;
    expect(ecmwfRows.find((r) => r.windowEndLocalDate === beyondDate)).toBeUndefined();
    const result = modelForecastFor(palisades, 'ecmwf', 'min', beyondDate);
    expect(result?.arm).toBe('ecmwf_openmeteo');
    expect(result?.forecast.leadDay).toBe(10);
    const omDay10 = omRows.find((r) => r.leadDay === 10);
    const out = applyModelMethodToDays([mkDay(beyondDate, 7.5)], palisades, 'ecmwf', 'min');
    expect(out[0].totalSnowfall).toBe(omDay10?.modelSnowfallCm);
    expect(out[0].modelWindow?.kind).toBe('model');
    expect(out[0].modelWindow?.arm).toBe('ecmwf_openmeteo');
  });

  it('stays Kuchera beyond day 14, where no arm holds a record', () => {
    const out = applyModelMethodToDays([mkDay('2026-11-15', 7.5)], palisades, 'ecmwf', 'min');
    expect(out[0].totalSnowfall).toBe(7.5);
    expect(out[0].modelWindow?.kind).toBe('kuchera_fallback');
    const outBlend = applyModelMethodToDays([mkDay('2026-11-15', 7.5)], palisades, BLEND_MODEL_ID, 'min');
    expect(outBlend[0].modelWindow?.kind).toBe('kuchera_fallback');
  });

  it('replaces the day-5 total with the record snowfall under the menu method', () => {
    const out = applyModelMethodToDays([mkDay(day5Date, 999)], palisades, BLEND_MODEL_ID, 'min');
    expect(out[0].totalSnowfall).toBe(ecmwfRows[4]?.modelSnowfallCm);
    expect(out[0].modelWindow?.kind).toBe('model');
    expect(out[0].modelWindow?.arm).toBe('ecmwf');
  });
});

describe('panel in ENS-EB menu-method mode', () => {
  it('renders the fallback notice and coverage strip when the selected window has no record', () => {
    const ctx = modelWindowStatuses(palisades, 'hrrr', 'min');
    const html = renderToString(
      createElement(ModelForecastPanel, { result: null, methodContext: ctx, selectedDate: day2Date }),
    );
    expect(html).toContain('MODEL FORECAST');
    expect(html).toContain('Produced by the MySnow ENS-EB model');
    expect(html).toContain('No ENS-EB record');
    expect(html).toContain('Kuchera values shown for this window');
    expect(html).toContain('Kuchera fallback');
    expect(html).toContain('Model · NOAA HRRR');
  });

  it('shows each blend window with the arm that actually served it', () => {
    const ctx = modelWindowStatuses(palisades, BLEND_MODEL_ID, 'min');
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', day1Date);
    const html = renderToString(
      createElement(ModelForecastPanel, { result, methodContext: ctx, selectedDate: day1Date }),
    );
    expect(html).toContain('Model · NOAA HRRR');
    expect(html).toContain('Model · ECMWF IFS');
    expect(html).toContain('Model · ECMWF (Open-Meteo)');
    expect(html).not.toContain('Kuchera fallback');
  });

  it('labels the Open-Meteo tail windows distinctly in the coverage strip', () => {
    const ctx = modelWindowStatuses(palisades, BLEND_MODEL_ID, 'min');
    const day12 = omRows.find((r) => r.leadDay === 12) as (typeof omRows)[number];
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', day12.windowEndLocalDate);
    const html = renderToString(
      createElement(ModelForecastPanel, {
        result,
        methodContext: ctx,
        selectedDate: day12.windowEndLocalDate,
      }),
    );
    expect(html).toContain('Model · ECMWF (Open-Meteo)');
    expect(html).toContain('ECMWF IFS via Open-Meteo');
    expect(html).toContain('Reduced-feature variant');
  });

  it('still renders nothing with neither a result nor a method context', () => {
    expect(renderToString(createElement(ModelForecastPanel, { result: null }))).toBe('');
    expect(
      renderToString(createElement(ModelForecastPanel, { result: null, methodContext: null })),
    ).toBe('');
  });
});

describe('Open-Meteo tail under the ENS-EB menu method', () => {
  const mkDay = (dateStr: string, totalSnowfall: number): DayData =>
    ({ dateStr, totalSnowfall }) as DayData;
  const day12 = omRows.find((r) => r.leadDay === 12) as (typeof omRows)[number];
  const day12Date = day12.windowEndLocalDate;

  it('replaces the day-12 total with the Open-Meteo record in ecmwf and blend modes', () => {
    for (const mode of ['ecmwf', BLEND_MODEL_ID]) {
      const out = applyModelMethodToDays([mkDay(day12Date, 999)], palisades, mode, 'min');
      expect(out[0].totalSnowfall).toBe(day12?.modelSnowfallCm);
      expect(out[0].modelWindow?.kind).toBe('model');
      expect(out[0].modelWindow?.arm).toBe('ecmwf_openmeteo');
      expect(out[0].modelWindow?.slr).toBe(day12?.modelSlr);
      expect(out[0].modelWindow?.cycleUtc).toBe(modelForecastData.cycles.ecmwf_openmeteo);
    }
  });

  it('keeps the day-12 total on Kuchera in literal hrrr mode', () => {
    const out = applyModelMethodToDays([mkDay(day12Date, 7.5)], palisades, 'hrrr', 'min');
    expect(out[0].totalSnowfall).toBe(7.5);
    expect(out[0].modelWindow?.kind).toBe('kuchera_fallback');
    expect(out[0].modelWindow?.arm).toBeNull();
  });
});
