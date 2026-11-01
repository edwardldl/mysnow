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

const palisadesPoint = modelForecastData.points.find((p) => p.pointId === 'palisades-tahoe-base');
const ecmwfRows = palisadesPoint?.forecasts.ecmwf ?? [];
const day1Date = ecmwfRows[0]?.windowEndLocalDate;
const day2Date = ecmwfRows[1]?.windowEndLocalDate;
const day3Date = ecmwfRows[2]?.windowEndLocalDate;

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
  it('blend mode: all three Palisades windows resolve to the model with truthful arms', () => {
    const ctx = modelWindowStatuses(palisades, BLEND_MODEL_ID, 'min');
    expect(ctx?.point.pointId).toBe('palisades-tahoe-base');
    expect(ctx?.windows.map((w) => w.windowEndLocalDate)).toEqual([day1Date, day2Date, day3Date]);
    expect(ctx?.windows.map((w) => [w.status, w.arm])).toEqual([
      ['model', 'hrrr'],
      ['model', 'ecmwf'],
      ['model', 'ecmwf'],
    ]);
  });

  it('literal hrrr mode: day-2 and day-3 windows are marked fallback, never silently filled', () => {
    const ctx = modelWindowStatuses(palisades, 'hrrr', 'min');
    expect(ctx?.windows.map((w) => w.status)).toEqual(['model', 'fallback', 'fallback']);
    expect(ctx?.windows[1].result).toBeNull();
    expect(ctx?.windows[2].result).toBeNull();
  });

  it('literal ecmwf mode: every stored window resolves to the model', () => {
    const ctx = modelWindowStatuses(palisades, 'ecmwf', 'min');
    expect(ctx?.windows.map((w) => w.status)).toEqual(['model', 'model', 'model']);
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
    expect(html).not.toContain('Kuchera fallback');
  });

  it('still renders nothing with neither a result nor a method context', () => {
    expect(renderToString(createElement(ModelForecastPanel, { result: null }))).toBe('');
    expect(
      renderToString(createElement(ModelForecastPanel, { result: null, methodContext: null })),
    ).toBe('');
  });
});
