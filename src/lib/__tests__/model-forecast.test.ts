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

  it('confines literal modes to their own source family', () => {
    // Literal HRRR: that arm only, no fallback of any kind.
    expect(modelArmPreference('hrrr', 3)).toEqual(['hrrr']);
    // Literal ECMWF: the GRIB arm, then the Open-Meteo tail arm (same
    // ECMWF family; it holds only beyond-ceiling windows) -- never HRRR.
    expect(modelArmPreference('ecmwf', 1)).toEqual(['ecmwf', 'ecmwf_openmeteo']);
    expect(modelArmPreference('ecmwf', 12)).toEqual(['ecmwf', 'ecmwf_openmeteo']);
    expect(modelArmPreference('gfs', 1)).toEqual([]);
    expect(modelArmPreference('best_match', 2)).toEqual([]);
  });

  it('mirrors the app blend: HRRR privileged inside 48 h, ECMWF beyond', () => {
    expect(modelArmPreference(BLEND_MODEL_ID, 1)).toEqual(['hrrr', 'ecmwf', 'ecmwf_openmeteo']);
    expect(modelArmPreference(BLEND_MODEL_ID, 2)).toEqual(['hrrr', 'ecmwf', 'ecmwf_openmeteo']);
    expect(modelArmPreference(BLEND_MODEL_ID, 3)).toEqual(['ecmwf', 'ecmwf_openmeteo', 'hrrr']);
    expect(modelArmPreference(BLEND_MODEL_ID, 4)).toEqual(['ecmwf', 'ecmwf_openmeteo', 'hrrr']);
    expect(modelArmPreference(BLEND_MODEL_ID, 9)).toEqual(['ecmwf', 'ecmwf_openmeteo', 'hrrr']);
    expect(modelArmPreference(BLEND_MODEL_ID, 12)).toEqual(['ecmwf', 'ecmwf_openmeteo', 'hrrr']);
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
    expect(modelForecastData.points.length).toBe(33);
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

describe('Open-Meteo long-range arm (third source, beyond the GRIB ceiling)', () => {
  const palisadesPoint = modelForecastData.points.find((p) => p.pointId === 'palisades-tahoe-base');
  const omRows = palisadesPoint?.forecasts.ecmwf_openmeteo ?? [];
  const day10Row = omRows.find((r) => r.leadDay === 10) as (typeof omRows)[number];
  const day12Row = omRows.find((r) => r.leadDay === 12) as (typeof omRows)[number];
  const day10Date = day10Row.windowEndLocalDate;
  const day12Date = day12Row.windowEndLocalDate;
  // Lead day 15 would end 2026-11-15 at Palisades; no arm stores it.
  const day15Date = '2026-11-15';

  it('stores only the tail windows: leads 10-14 at Palisades, 11-14 at Stowe', () => {
    expect(omRows.map((r) => r.leadDay)).toEqual([10, 11, 12, 13, 14]);
    expect(omRows.every((r) => r.reducedFeatureVariant === true)).toBe(true);
    const stoweOmRows =
      modelForecastData.points.find((p) => p.pointId === 'stowe-base')?.forecasts.ecmwf_openmeteo ?? [];
    expect(stoweOmRows.map((r) => r.leadDay)).toEqual([11, 12, 13, 14]);
    expect(modelForecastData.openMeteoArm?.displayName).toBe('ECMWF IFS via Open-Meteo');
    expect(modelForecastData.openMeteoArm?.reducedFeatureVariant).toBe(true);
    expect(modelForecastData.cycles.ecmwf_openmeteo).toBe(modelForecastData.cycles.ecmwf);
  });

  it('resolves the day-12 window to the Open-Meteo arm in literal ecmwf mode', () => {
    const result = modelForecastFor(palisades, 'ecmwf', 'min', day12Date);
    expect(result?.arm).toBe('ecmwf_openmeteo');
    expect(result?.blended).toBe(false);
    expect(result?.forecast.leadDay).toBe(12);
    expect(result?.forecast.modelSlr).toBe(day12Row?.modelSlr);
    expect(result?.forecast.modelSnowfallCm).toBe(day12Row?.modelSnowfallCm);
    expect(result?.forecast.reducedFeatureVariant).toBe(true);
    expect(result?.cycleUtc).toBe(modelForecastData.cycles.ecmwf_openmeteo);
    expect(result?.retrievedAtUtc).toBe(modelForecastData.openMeteoArm?.retrievedAtUtc);
  });

  it('resolves the day-12 window to the Open-Meteo arm in blend mode too', () => {
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', day12Date);
    expect(result?.blended).toBe(true);
    expect(result?.arm).toBe('ecmwf_openmeteo');
    expect(result?.forecast.modelSlr).toBe(day12Row?.modelSlr);
    // Same values the literal ECMWF mode shows for this window.
    expect(result?.forecast).toEqual(
      modelForecastFor(palisades, 'ecmwf', 'min', day12Date)?.forecast,
    );
  });

  it('serves the first tail window (day 10 at Palisades) from Open-Meteo, not GRIB', () => {
    expect(
      palisadesPoint?.forecasts.ecmwf?.find((r) => r.windowEndLocalDate === day10Date),
    ).toBeUndefined();
    const result = modelForecastFor(palisades, 'ecmwf', 'min', day10Date);
    expect(result?.arm).toBe('ecmwf_openmeteo');
    expect(result?.forecast.leadDay).toBe(10);
  });

  it('keeps Stowe day 10 on the GRIB arm and day 11 on Open-Meteo', () => {
    const stowePoint = modelForecastData.points.find((p) => p.pointId === 'stowe-base');
    const gribDay10 = stowePoint?.forecasts.ecmwf?.find((r) => r.leadDay === 10) as
      | (typeof omRows)[number]
      | undefined;
    const omDay11 = stowePoint?.forecasts.ecmwf_openmeteo?.find((r) => r.leadDay === 11) as
      | (typeof omRows)[number]
      | undefined;
    const day10 = modelForecastFor(stowe, 'ecmwf', 'min', gribDay10?.windowEndLocalDate ?? null);
    expect(day10?.arm).toBe('ecmwf');
    const day11 = modelForecastFor(stowe, BLEND_MODEL_ID, 'min', omDay11?.windowEndLocalDate ?? null);
    expect(day11?.arm).toBe('ecmwf_openmeteo');
    expect(day11?.forecast.modelSlr).toBe(omDay11?.modelSlr);
  });

  it('never resolves the Open-Meteo arm in literal hrrr mode', () => {
    expect(modelForecastFor(palisades, 'hrrr', 'min', day12Date)).toBeNull();
    expect(modelForecastFor(palisades, 'hrrr', 'min', day10Date)).toBeNull();
  });

  it('has no model record beyond day 14 in any mode', () => {
    expect(modelForecastFor(palisades, 'ecmwf', 'min', day15Date)).toBeNull();
    expect(modelForecastFor(palisades, BLEND_MODEL_ID, 'min', day15Date)).toBeNull();
    expect(modelForecastFor(palisades, 'hrrr', 'min', day15Date)).toBeNull();
  });

  it('labels the day-12 panel as ECMWF IFS via Open-Meteo with the reduced-feature note', () => {
    const html = renderToString(
      createElement(ModelForecastPanel, {
        result: modelForecastFor(palisades, 'ecmwf', 'min', day12Date),
      }),
    );
    expect(html).toContain('MODEL FORECAST');
    expect(html).toContain('ECMWF IFS via Open-Meteo');
    expect(html).toContain('anchored to the 2026-10-31 12Z cycle');
    expect(html).toContain('Reduced-feature variant');
    expect(html).toContain('not cycle-verified');
    // The OM arm's values are the stored record's.
    expect(html).toContain(day12Row?.modelSnowfallCm?.toFixed(1));
  });
});

describe('Mount Rose (catalogue addition, estimated geometry)', () => {
  const mrPoint = modelForecastData.points.find((p) => p.pointId === 'mount-rose-base');
  const mrEcmwf = mrPoint?.forecasts.ecmwf ?? [];
  const mrOm = mrPoint?.forecasts.ecmwf_openmeteo ?? [];
  const day2Row = mrEcmwf.find((r) => r.leadDay === 2) as (typeof mrEcmwf)[number];
  const day12Row = mrOm.find((r) => r.leadDay === 12) as (typeof mrOm)[number];

  it('matches the Mount Rose points by elevation mode', () => {
    expect(cataloguePointForLocation(mountRose, 'min')?.pointId).toBe('mount-rose-base');
    expect(cataloguePointForLocation(mountRose, 'avg')?.pointId).toBe('mount-rose-mid_mountain');
    expect(cataloguePointForLocation(mountRose, 'max')?.pointId).toBe('mount-rose-summit');
  });

  it('stores GRIB leads 1-9, the HRRR day-1 row, and Open-Meteo leads 10-14', () => {
    expect(mrEcmwf.map((r) => r.leadDay)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
    expect((mrPoint?.forecasts.hrrr ?? []).map((r) => r.leadDay)).toEqual([1]);
    expect(mrOm.map((r) => r.leadDay)).toEqual([10, 11, 12, 13, 14]);
    expect(mrOm.every((r) => r.reducedFeatureVariant === true)).toBe(true);
  });

  it('resolves the day-2 window to the GRIB ECMWF arm', () => {
    const result = modelForecastFor(mountRose, 'ecmwf', 'min', day2Row.windowEndLocalDate);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.forecast.modelSlr).toBe(day2Row.modelSlr);
    expect(result?.forecast.modelSnowfallCm).toBe(day2Row.modelSnowfallCm);
  });

  it('resolves the day-12 window to the Open-Meteo arm in ecmwf and blend modes, never hrrr', () => {
    const literal = modelForecastFor(mountRose, 'ecmwf', 'min', day12Row.windowEndLocalDate);
    expect(literal?.arm).toBe('ecmwf_openmeteo');
    expect(literal?.forecast.modelSlr).toBe(day12Row.modelSlr);
    const blend = modelForecastFor(mountRose, BLEND_MODEL_ID, 'min', day12Row.windowEndLocalDate);
    expect(blend?.arm).toBe('ecmwf_openmeteo');
    expect(modelForecastFor(mountRose, 'hrrr', 'min', day12Row.windowEndLocalDate)).toBeNull();
  });

  it('labels the day-2 panel ECMWF IFS and the day-12 panel as the Open-Meteo arm', () => {
    const grib = renderToString(
      createElement(ModelForecastPanel, {
        result: modelForecastFor(mountRose, 'ecmwf', 'min', day2Row.windowEndLocalDate),
      }),
    );
    expect(grib).toContain('MODEL FORECAST');
    expect(grib).toContain('ECMWF IFS');
    expect(grib).not.toContain('via Open-Meteo');
    const om = renderToString(
      createElement(ModelForecastPanel, {
        result: modelForecastFor(mountRose, 'ecmwf', 'min', day12Row.windowEndLocalDate),
      }),
    );
    expect(om).toContain('ECMWF IFS via Open-Meteo');
    expect(om).toContain('anchored to the 2026-10-31 12Z cycle');
    expect(om).toContain('Reduced-feature variant');
    expect(om).toContain(day12Row.modelSnowfallCm?.toFixed(1));
  });
});
