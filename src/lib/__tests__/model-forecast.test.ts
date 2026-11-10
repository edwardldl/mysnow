import { describe, expect, it } from 'vitest';
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
    expect(modelForecastData.points.length).toBe(57);
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

  it('resolves no model forecast in a non-ecmwf/hrrr mode for the same point and date', () => {
    expect(modelForecastFor(palisades, 'gfs', 'min', firstDate)).toBeNull();
    expect(modelForecastFor(palisades, 'best_match', 'min', firstDate)).toBeNull();
    expect(modelForecastFor(palisades, 'icon_global', 'min', firstDate)).toBeNull();
  });

  it('resolves no model forecast for a non-catalogue location in ecmwf mode', () => {
    expect(modelForecastFor(farAway, 'ecmwf', 'avg', firstDate)).toBeNull();
  });

  it('resolves no model forecast for a date with no stored window', () => {
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

  it('resolves no model forecast at a non-catalogue location in blend mode', () => {
    expect(modelForecastFor(farAway, BLEND_MODEL_ID, 'avg', day1Date)).toBeNull();
  });

  it('resolves no model forecast for a date with no stored window in blend mode', () => {
    expect(modelForecastFor(palisades, BLEND_MODEL_ID, 'min', '2030-01-01')).toBeNull();
  });
});


describe('Open-Meteo long-range arm (third source, beyond the GRIB ceiling)', () => {
  const palisadesPoint = modelForecastData.points.find((p) => p.pointId === 'palisades-tahoe-base');
  const omRows = palisadesPoint?.forecasts.ecmwf_openmeteo ?? [];
  const day10Row = omRows.find((r) => r.leadDay === 10) as (typeof omRows)[number];
  const day12Row = omRows.find((r) => r.leadDay === 12) as (typeof omRows)[number];
  const day10Date = day10Row.windowEndLocalDate;
  const day12Date = day12Row.windowEndLocalDate;
  // Lead day 15 would end 2026-11-24 at Palisades; no arm stores it.
  const day15Date = '2026-11-24';

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

});

describe('California Ikon/Epic expansion (catalogue addition, estimated geometry)', () => {
  // Geometry from the app catalogue entries in src/data/ski-areas.json,
  // following the estimated convention (base = min, mid = (min+max)/2,
  // summit = max, all at the resort coordinates). Ikon California per
  // ikonpass.com destinations (2026-27): June Mountain, Bear Mountain +
  // Snow Summit (listed separately under Big Bear Mountain Resort),
  // Snow Valley, Sierra-at-Tahoe. Epic California per epicpass.com:
  // Heavenly, Northstar, Kirkwood.
  const CA_NEW: Array<{
    slug: string; name: string; latitude: number; longitude: number;
    minElevationM: number; maxElevationM: number;
  }> = [
    { slug: 'june-mountain', name: 'June Mountain', latitude: 37.753661, longitude: -119.075663, minElevationM: 2314, maxElevationM: 3090 },
    { slug: 'bear-mountain', name: 'Bear Mountain', latitude: 34.219620, longitude: -116.860485, minElevationM: 2182, maxElevationM: 2678 },
    { slug: 'snow-summit', name: 'Snow Summit', latitude: 34.227750, longitude: -116.890393, minElevationM: 2128, maxElevationM: 2498 },
    { slug: 'snow-valley', name: 'Snow Valley', latitude: 34.214481, longitude: -117.036296, minElevationM: 2052, maxElevationM: 2388 },
    { slug: 'sierra-at-tahoe', name: 'Sierra-at-Tahoe', latitude: 38.797438, longitude: -120.076847, minElevationM: 2040, maxElevationM: 2702 },
    { slug: 'heavenly', name: 'Heavenly', latitude: 38.937462, longitude: -119.910798, minElevationM: 2027, maxElevationM: 3037 },
    { slug: 'northstar', name: 'Northstar California', latitude: 39.263035, longitude: -120.133240, minElevationM: 1927, maxElevationM: 2630 },
    { slug: 'kirkwood', name: 'Kirkwood', latitude: 38.678978, longitude: -120.067434, minElevationM: 2388, maxElevationM: 2892 },
  ];

  const locationFor = (r: (typeof CA_NEW)[number]): Location => {
    const mid = (r.minElevationM + r.maxElevationM) / 2;
    return {
      id: r.slug,
      name: r.name,
      latitude: r.latitude,
      longitude: r.longitude,
      elevationFt: Math.round(mid * 3.28084),
      elevationM: mid,
      minElevationM: r.minElevationM,
      maxElevationM: r.maxElevationM,
      referencePoints: [
        { id: `${r.slug}-base`, name: 'Base estimate', role: 'base', latitude: r.latitude, longitude: r.longitude, elevationM: r.minElevationM, geometrySource: 'estimated' },
        { id: `${r.slug}-mid`, name: 'Mid-mountain estimate', role: 'mid_mountain', latitude: r.latitude, longitude: r.longitude, elevationM: mid, geometrySource: 'estimated' },
        { id: `${r.slug}-summit`, name: 'Summit estimate', role: 'summit', latitude: r.latitude, longitude: r.longitude, elevationM: r.maxElevationM, geometrySource: 'estimated' },
      ],
      timezone: 'America/Los_Angeles',
      isCustom: false,
    } as Location;
  };

  it('matches every new resort point by elevation mode', () => {
    for (const r of CA_NEW) {
      const loc = locationFor(r);
      expect(cataloguePointForLocation(loc, 'min')?.pointId).toBe(`${r.slug}-base`);
      expect(cataloguePointForLocation(loc, 'avg')?.pointId).toBe(`${r.slug}-mid_mountain`);
      expect(cataloguePointForLocation(loc, 'max')?.pointId).toBe(`${r.slug}-summit`);
    }
  });

  it('disambiguates Bear Mountain and Snow Summit (0.031 deg apart)', () => {
    const bear = locationFor(CA_NEW[1]);
    const summit = locationFor(CA_NEW[2]);
    for (const mode of ['min', 'avg', 'max']) {
      expect(cataloguePointForLocation(bear, mode)?.resortSlug).toBe('bear-mountain');
      expect(cataloguePointForLocation(summit, mode)?.resortSlug).toBe('snow-summit');
    }
  });

  it('stores GRIB leads 1-9, the HRRR day-1 row, and Open-Meteo leads 10-14 at every new base point', () => {
    for (const r of CA_NEW) {
      const point = modelForecastData.points.find((p) => p.pointId === `${r.slug}-base`);
      expect(point, r.slug).toBeDefined();
      expect((point?.forecasts.ecmwf ?? []).map((x) => x.leadDay)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
      expect((point?.forecasts.hrrr ?? []).map((x) => x.leadDay)).toEqual([1]);
      const om = point?.forecasts.ecmwf_openmeteo ?? [];
      expect(om.map((x) => x.leadDay)).toEqual([10, 11, 12, 13, 14]);
      expect(om.every((x) => x.reducedFeatureVariant === true)).toBe(true);
    }
  });

  it('resolves a day-2 GRIB window and a day-12 Open-Meteo window at a new resort', () => {
    const kirkwood = locationFor(CA_NEW[7]);
    const point = modelForecastData.points.find((p) => p.pointId === 'kirkwood-base');
    const day2 = (point?.forecasts.ecmwf ?? []).find((x) => x.leadDay === 2);
    const day12 = (point?.forecasts.ecmwf_openmeteo ?? []).find((x) => x.leadDay === 12);
    const literal = modelForecastFor(kirkwood, 'ecmwf', 'min', day2!.windowEndLocalDate);
    expect(literal?.arm).toBe('ecmwf');
    expect(literal?.forecast.modelSlr).toBe(day2!.modelSlr);
    const omResult = modelForecastFor(kirkwood, 'ecmwf', 'min', day12!.windowEndLocalDate);
    expect(omResult?.arm).toBe('ecmwf_openmeteo');
    expect(modelForecastFor(kirkwood, 'hrrr', 'min', day12!.windowEndLocalDate)).toBeNull();
  });
});
