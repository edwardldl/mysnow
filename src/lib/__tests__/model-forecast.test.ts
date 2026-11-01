import { createElement } from 'react';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import ModelForecastPanel from '../../components/ModelForecastPanel';
import {
  BLEND_MODEL_ID,
  cataloguePointForLocation,
  modelArmForMode,
  modelArmPreference,
  modelForecastData,
  modelForecastFor,
} from '../model-forecast';
import type { Location } from '../types';

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

describe('model mode gate', () => {
  it('maps only the literal ecmwf and hrrr modes to a single arm', () => {
    expect(modelArmForMode('ecmwf')).toBe('ecmwf');
    expect(modelArmForMode('hrrr')).toBe('hrrr');
    expect(modelArmForMode('gfs')).toBeNull();
    expect(modelArmForMode(BLEND_MODEL_ID)).toBeNull();
    expect(modelArmForMode('best_match')).toBeNull();
    expect(modelArmForMode('icon_global')).toBeNull();
  });

  it('gives literal modes a single-arm preference with no fallback', () => {
    expect(modelArmPreference('ecmwf', 1)).toEqual(['ecmwf']);
    expect(modelArmPreference('hrrr', 3)).toEqual(['hrrr']);
    expect(modelArmPreference('gfs', 1)).toEqual([]);
    expect(modelArmPreference('best_match', 2)).toEqual([]);
  });

  it('mirrors the app blend: HRRR privileged inside 48 h, ECMWF beyond', () => {
    expect(modelArmPreference(BLEND_MODEL_ID, 1)).toEqual(['hrrr', 'ecmwf']);
    expect(modelArmPreference(BLEND_MODEL_ID, 2)).toEqual(['hrrr', 'ecmwf']);
    expect(modelArmPreference(BLEND_MODEL_ID, 3)).toEqual(['ecmwf', 'hrrr']);
    expect(modelArmPreference(BLEND_MODEL_ID, 4)).toEqual(['ecmwf', 'hrrr']);
    expect(modelArmPreference(BLEND_MODEL_ID, 9)).toEqual(['ecmwf', 'hrrr']);
  });
});

describe('catalogue matching', () => {
  it('matches the Palisades base point in min elevation mode', () => {
    const point = cataloguePointForLocation(palisades, 'min');
    expect(point?.pointId).toBe('palisades-tahoe-base');
  });

  it('matches the Palisades summit point in max elevation mode', () => {
    const point = cataloguePointForLocation(palisades, 'max');
    expect(point?.pointId).toBe('palisades-tahoe-summit');
  });

  it('returns null for a non-catalogue location', () => {
    expect(cataloguePointForLocation(farAway, 'avg')).toBeNull();
  });
});

describe('model forecast resolution', () => {
  const palisadesPoint = modelForecastData.points.find((p) => p.pointId === 'palisades-tahoe-base');
  const ecmwfRows = palisadesPoint?.forecasts.ecmwf ?? [];
  const firstDate = ecmwfRows[0]?.windowEndLocalDate;

  it('serving data covers the catalogue', () => {
    expect(modelForecastData.points.length).toBe(30);
    expect(ecmwfRows.length).toBeGreaterThan(0);
  });

  it('resolves a forecast in ecmwf mode for a stored window date', () => {
    const result = modelForecastFor(palisades, 'ecmwf', 'min', firstDate);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.blended).toBe(false);
    expect(result?.forecast.modelSlr).not.toBeNull();
  });

  it('does not fall back across sources in a literal mode', () => {
    // Palisades base has no HRRR row for the day-2 window (beyond the
    // 48 h horizon from the 12Z cycle): literal HRRR mode shows nothing
    // rather than borrowing the ECMWF record.
    const day2Date = ecmwfRows[1]?.windowEndLocalDate;
    expect(modelForecastFor(palisades, 'hrrr', 'min', day2Date)).toBeNull();
    expect(modelForecastFor(palisades, 'ecmwf', 'min', day2Date)?.arm).toBe('ecmwf');
  });

  it('shows no panel in a non-ecmwf/hrrr mode for the same point and date', () => {
    expect(modelForecastFor(palisades, 'gfs', 'min', firstDate)).toBeNull();
    expect(modelForecastFor(palisades, 'best_match', 'min', firstDate)).toBeNull();
    expect(modelForecastFor(palisades, 'icon_global', 'min', firstDate)).toBeNull();
  });

  it('shows no panel for a non-catalogue location in ecmwf mode', () => {
    expect(modelForecastFor(farAway, 'ecmwf', 'avg', firstDate)).toBeNull();
  });

  it('shows no panel for a date with no stored window', () => {
    expect(modelForecastFor(palisades, 'ecmwf', 'min', '2030-01-01')).toBeNull();
  });
});

describe('blend mode (hrrr_ecmwf, the app default)', () => {
  const palisadesPoint = modelForecastData.points.find((p) => p.pointId === 'palisades-tahoe-base');
  const ecmwfRows = palisadesPoint?.forecasts.ecmwf ?? [];
  const hrrrRows = palisadesPoint?.forecasts.hrrr ?? [];
  const day1Date = ecmwfRows[0]?.windowEndLocalDate;
  const day2Date = ecmwfRows[1]?.windowEndLocalDate;
  const day3Date = ecmwfRows[2]?.windowEndLocalDate;

  it('serves the HRRR arm for the day-1 window (inside the 48 h horizon)', () => {
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', day1Date);
    expect(result?.blended).toBe(true);
    expect(result?.arm).toBe('hrrr');
    expect(result?.forecast.modelSlr).toBe(hrrrRows[0]?.modelSlr);
    expect(result?.forecast.modelSnowfallCm).toBe(hrrrRows[0]?.modelSnowfallCm);
    expect(result?.cycleUtc).toBe(modelForecastData.cycles.hrrr);
  });

  it('falls back to the ECMWF arm, labelled ECMWF, when HRRR has no day-2 record', () => {
    // Fixture premise: Palisades base has no HRRR row for day 2 in the
    // serving data (window end beyond the 48 h horizon from 12Z).
    expect(hrrrRows.find((row) => row.windowEndLocalDate === day2Date)).toBeUndefined();
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', day2Date);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.forecast.modelSlr).toBe(ecmwfRows[1]?.modelSlr);
    expect(result?.cycleUtc).toBe(modelForecastData.cycles.ecmwf);
    // Same values the literal ECMWF mode shows for this window.
    expect(result?.forecast).toEqual(
      modelForecastFor(palisades, 'ecmwf', 'min', day2Date)?.forecast,
    );
  });

  it('keeps the HRRR arm on day 2 where HRRR does have a record (Stowe)', () => {
    const result = modelForecastFor(stowe, BLEND_MODEL_ID, 'min', day2Date);
    expect(result?.arm).toBe('hrrr');
    expect(result?.point.pointId).toBe('stowe-base');
    const stoweHrrrDay2 = modelForecastData.points
      .find((p) => p.pointId === 'stowe-base')
      ?.forecasts.hrrr?.find((row) => row.windowEndLocalDate === day2Date);
    expect(result?.forecast.modelSlr).toBe(stoweHrrrDay2?.modelSlr);
  });

  it('serves the ECMWF arm for the day-3 window (beyond the HRRR horizon)', () => {
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', day3Date);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.forecast.modelSlr).toBe(ecmwfRows[2]?.modelSlr);
  });

  it('serves the ECMWF arm for the extended-horizon day-5 window', () => {
    const day5Date = ecmwfRows[4]?.windowEndLocalDate;
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', day5Date);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.forecast.leadDay).toBe(5);
    expect(result?.forecast.modelSlr).toBe(ecmwfRows[4]?.modelSlr);
    // Same values the literal ECMWF mode shows for this window.
    expect(result?.forecast).toEqual(
      modelForecastFor(palisades, 'ecmwf', 'min', day5Date)?.forecast,
    );
  });

  it('shows no panel at a non-catalogue location in blend mode', () => {
    expect(modelForecastFor(farAway, BLEND_MODEL_ID, 'avg', day1Date)).toBeNull();
  });

  it('shows no panel for a date with no stored window in blend mode', () => {
    expect(modelForecastFor(palisades, BLEND_MODEL_ID, 'min', '2030-01-01')).toBeNull();
  });
});

describe('panel rendering and per-window source labelling', () => {
  const palisadesPoint = modelForecastData.points.find((p) => p.pointId === 'palisades-tahoe-base');
  const ecmwfRows = palisadesPoint?.forecasts.ecmwf ?? [];
  const day1Date = ecmwfRows[0]?.windowEndLocalDate;
  const day2Date = ecmwfRows[1]?.windowEndLocalDate;

  const render = (result: ReturnType<typeof modelForecastFor>): string =>
    renderToString(createElement(ModelForecastPanel, { result }));

  it('labels the day-1 blend window as NOAA HRRR with the model label intact', () => {
    const html = render(modelForecastFor(palisades, BLEND_MODEL_ID, 'min', day1Date));
    expect(html).toContain('MODEL FORECAST');
    expect(html).toContain('Produced by the MySnow ENS-EB model');
    expect(html).toContain('NOAA HRRR');
    expect(html).toContain('Blended (HRRR+ECMWF) mode');
  });

  it('labels the day-2 fallback window as ECMWF IFS, not HRRR', () => {
    const html = render(modelForecastFor(palisades, BLEND_MODEL_ID, 'min', day2Date));
    expect(html).toContain('MODEL FORECAST');
    expect(html).toContain('ECMWF IFS');
    expect(html).not.toContain('served by the NOAA HRRR model arm');
  });

  it('labels the day-5 blend window as ECMWF IFS with the model label intact', () => {
    const day5Date = ecmwfRows[4]?.windowEndLocalDate;
    const html = render(modelForecastFor(palisades, BLEND_MODEL_ID, 'min', day5Date));
    expect(html).toContain('MODEL FORECAST');
    expect(html).toContain('Produced by the MySnow ENS-EB model');
    expect(html).toContain('ECMWF IFS');
    expect(html).not.toContain('NOAA HRRR');
  });

  it('renders nothing without a resolved result', () => {
    expect(render(null)).toBe('');
    expect(render(modelForecastFor(palisades, 'gfs', 'min', day1Date))).toBe('');
  });
});
