import { describe, expect, it } from 'vitest';
import {
  BLEND_MODEL_ID,
  cataloguePointForLocation,
  modelArmForMode,
  modelArmPreference,
  modelForecastData,
  modelForecastFor,
} from '../model-forecast';
import { addCalendarDays } from '../time';
import {
  ECMWF_STEP_CEILING_HOURS,
  expectedMergedWindows,
  expectedServedWindows,
  HRRR_HORIZON_HOURS,
} from './model-geometry';
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
    // Literal ECMWF: the GRIB arm only -- never HRRR. (The Open-Meteo
    // tail arm is retired since the 2026-11-10 10-day cap and no
    // longer appears in any preference order.)
    expect(modelArmPreference('ecmwf', 1)).toEqual(['ecmwf']);
    expect(modelArmPreference('ecmwf', 12)).toEqual(['ecmwf']);
    expect(modelArmPreference('gfs', 1)).toEqual([]);
    expect(modelArmPreference('best_match', 2)).toEqual([]);
  });

  it('mirrors the app blend: HRRR privileged inside 48 h, ECMWF beyond', () => {
    expect(modelArmPreference(BLEND_MODEL_ID, 1)).toEqual(['hrrr', 'ecmwf']);
    expect(modelArmPreference(BLEND_MODEL_ID, 2)).toEqual(['hrrr', 'ecmwf']);
    expect(modelArmPreference(BLEND_MODEL_ID, 3)).toEqual(['ecmwf', 'hrrr']);
    expect(modelArmPreference(BLEND_MODEL_ID, 4)).toEqual(['ecmwf', 'hrrr']);
    expect(modelArmPreference(BLEND_MODEL_ID, 9)).toEqual(['ecmwf', 'hrrr']);
    expect(modelArmPreference(BLEND_MODEL_ID, 12)).toEqual(['ecmwf', 'hrrr']);
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
    expect(modelForecastData.points.length).toBe(387);
    expect(ecmwfRows.length).toBeGreaterThan(0);
  });

  it('resolves a forecast in ecmwf mode for a stored window date', () => {
    const result = modelForecastFor(palisades, 'ecmwf', 'min', firstDate);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.blended).toBe(false);
    expect(result?.forecast.modelSlr).not.toBeNull();
  });

  it('does not fall back across sources in a literal mode', () => {
    // The second stored window has no HRRR row at Palisades in either
    // cycle geometry (HRRR holds only the first stored window here):
    // literal HRRR mode shows nothing rather than borrowing the ECMWF
    // record.
    const secondDate = ecmwfRows[1]?.windowEndLocalDate;
    expect(modelForecastFor(palisades, 'hrrr', 'min', secondDate)).toBeNull();
    expect(modelForecastFor(palisades, 'ecmwf', 'min', secondDate)?.arm).toBe('ecmwf');
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
  // Anchors are positional, so they hold in either cycle geometry:
  // the first stored window is the only one HRRR also holds at
  // Palisades (lead 2 from a 00Z cycle, lead 1 from a 12Z cycle);
  // the second stored window is the first HRRR does not hold.
  const sharedDate = hrrrRows[0]?.windowEndLocalDate;
  const firstEcmwfOnlyDate = ecmwfRows.find(
    (r) => !hrrrRows.some((h) => h.windowEndLocalDate === r.windowEndLocalDate),
  )?.windowEndLocalDate;
  const thirdDate = ecmwfRows[2]?.windowEndLocalDate;

  it('serves the HRRR arm for the first stored window (inside the 48 h horizon)', () => {
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', sharedDate);
    expect(result?.blended).toBe(true);
    expect(result?.arm).toBe('hrrr');
    expect(result?.forecast.modelSlr).toBe(hrrrRows[0]?.modelSlr);
    expect(result?.forecast.modelSnowfallCm).toBe(hrrrRows[0]?.modelSnowfallCm);
    expect(result?.cycleUtc).toBe(modelForecastData.cycles.hrrr);
  });

  it('falls back to the ECMWF arm, labelled ECMWF, for the first window HRRR does not hold', () => {
    // Fixture premise: Palisades base has no HRRR row for the first
    // ECMWF-only window in the serving data (its only HRRR window is
    // the first stored window).
    expect(hrrrRows.find((row) => row.windowEndLocalDate === firstEcmwfOnlyDate)).toBeUndefined();
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', firstEcmwfOnlyDate ?? null);
    const ecmwfRow = ecmwfRows.find((r) => r.windowEndLocalDate === firstEcmwfOnlyDate);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.forecast.modelSlr).toBe(ecmwfRow?.modelSlr);
    expect(result?.cycleUtc).toBe(modelForecastData.cycles.ecmwf);
    // Same values the literal ECMWF mode shows for this window.
    expect(result?.forecast).toEqual(
      modelForecastFor(palisades, 'ecmwf', 'min', firstEcmwfOnlyDate ?? null)?.forecast,
    );
  });

  it('keeps the HRRR arm on the first stored window where HRRR has a record (Stowe)', () => {
    // Stowe's own first HRRR date, not Palisades' sharedDate: the two
    // coincide in standard time, but from a 12Z cycle in daylight time
    // Palisades' first window is lead 1 while Stowe's is lead 2 under
    // a strict start, so borrowing the date would resolve nothing
    // (Codex gate, 2026-11-12).
    const stowePoint = modelForecastData.points.find((p) => p.pointId === 'stowe-base');
    const stoweSharedDate = stowePoint?.forecasts.hrrr?.[0]?.windowEndLocalDate;
    const result = modelForecastFor(stowe, BLEND_MODEL_ID, 'min', stoweSharedDate ?? null);
    expect(result?.arm).toBe('hrrr');
    expect(result?.point.pointId).toBe('stowe-base');
    const stoweHrrrShared = stowePoint?.forecasts.hrrr?.find(
      (row) => row.windowEndLocalDate === stoweSharedDate,
    );
    expect(result?.forecast.modelSlr).toBe(stoweHrrrShared?.modelSlr);
  });

  it('serves the ECMWF arm for a later stored window (beyond the HRRR horizon)', () => {
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', thirdDate);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.forecast.modelSlr).toBe(ecmwfRows[2]?.modelSlr);
  });

  it('serves the ECMWF arm for the extended-horizon lead-5 window', () => {
    const lead5Row = ecmwfRows.find((r) => r.leadDay === 5);
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', lead5Row?.windowEndLocalDate ?? null);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.forecast.leadDay).toBe(5);
    expect(result?.forecast.modelSlr).toBe(lead5Row?.modelSlr);
    // Same values the literal ECMWF mode shows for this window.
    expect(result?.forecast).toEqual(
      modelForecastFor(palisades, 'ecmwf', 'min', lead5Row?.windowEndLocalDate ?? null)?.forecast,
    );
  });

  it('resolves no model forecast at a non-catalogue location in blend mode', () => {
    expect(modelForecastFor(farAway, BLEND_MODEL_ID, 'avg', sharedDate)).toBeNull();
  });

  it('resolves no model forecast for a date with no stored window in blend mode', () => {
    expect(modelForecastFor(palisades, BLEND_MODEL_ID, 'min', '2030-01-01')).toBeNull();
  });
});


describe('10-day cap and the retired Open-Meteo arm', () => {
  const palisadesPoint = modelForecastData.points.find((p) => p.pointId === 'palisades-tahoe-base');
  const ecmwfRows = palisadesPoint?.forecasts.ecmwf ?? [];
  const hrrrRows = palisadesPoint?.forecasts.hrrr ?? [];

  it('serves no Open-Meteo records and no record beyond lead 10, anywhere', () => {
    expect(modelForecastData.openMeteoArm).toBeUndefined();
    expect(Object.keys(modelForecastData.cycles).sort()).toEqual(['ecmwf', 'hrrr']);
    for (const point of modelForecastData.points) {
      expect(point.forecasts.ecmwf_openmeteo ?? []).toEqual([]);
      for (const rows of Object.values(point.forecasts)) {
        for (const row of rows ?? []) {
          expect(row.leadDay).toBeLessThanOrEqual(10);
        }
      }
    }
  });

  it('serves the full capped horizon at Palisades: ECMWF to the step-240 ceiling, HRRR to the 48 h horizon', () => {
    // The expected lead sets are derived from the cycle + point (see
    // model-geometry.ts): at a 00Z cycle ECMWF serves leads 2-10 and
    // HRRR lead 2; at a 12Z cycle ECMWF serves leads 1-9 and HRRR
    // lead 1 at this America/Los_Angeles point.
    const cycle = modelForecastData.cycles;
    expect(
      ecmwfRows.map((r) => ({ leadDay: r.leadDay, windowEndLocalDate: r.windowEndLocalDate })),
      ).toEqual(expectedMergedWindows(ecmwfRows, cycle.ecmwf, palisadesPoint!, ECMWF_STEP_CEILING_HOURS));
    expect(
      hrrrRows.map((r) => ({ leadDay: r.leadDay, windowEndLocalDate: r.windowEndLocalDate })),
      ).toEqual(expectedMergedWindows(hrrrRows, cycle.hrrr, palisadesPoint!, HRRR_HORIZON_HOURS));
  });

  it('has no model record beyond the cap in any mode', () => {
    // Derived from the installed file itself (batch-agnostic): one day
    // past the file's last stored Palisades window (the cap); three
    // days past it is the old Open-Meteo tail's territory. No arm
    // stores them.
    const lastDate = ecmwfRows[ecmwfRows.length - 1]?.windowEndLocalDate as string;
    for (const date of [addCalendarDays(lastDate, 1), addCalendarDays(lastDate, 3)]) {
      expect(modelForecastFor(palisades, 'ecmwf', 'min', date)).toBeNull();
      expect(modelForecastFor(palisades, BLEND_MODEL_ID, 'min', date)).toBeNull();
      expect(modelForecastFor(palisades, 'hrrr', 'min', date)).toBeNull();
    }
  });
});

describe('North America catalogue (129 resorts, 387 points)', () => {
  const EXPECTED_SLUGS = [
    'afton-alps', 'alpental', 'alpine-valley-oh', 'alta', 'alyeska', 'arapahoe-basin',
    'aspen', 'aspen-highlands', 'attitash', 'banff-sunshine', 'bear-mountain', 'beaver-creek',
    'big-boulder', 'big-sky', 'blue-mountain-on', 'blue-mountain-pa', 'boston-mills-brandywine',
    'boyne-mountain', 'breckenridge', 'bridger-bowl', 'brighton', 'buck-hill', 'buttermilk',
    'camelback', 'cannon-mountain', 'copper-mountain', 'cranmore', 'crested-butte',
    'crotched-mountain', 'crystal-mountain', 'cypress-mountain', 'deer-valley', 'devils-head',
    'eldora', 'fernie', 'grand-targhee', 'granite-peak', 'grouse-mountain', 'heavenly',
    'hidden-valley-mo', 'hidden-valley-pa', 'hunter-mountain', 'jack-frost', 'jackson-hole',
    'jay-peak', 'jiminy-peak', 'june-mountain', 'keystone', 'kicking-horse', 'killington',
    'kimberley', 'kirkwood', 'lake-louise', 'laurel-mountain', 'le-massif', 'liberty-mountain',
    'loon-mountain', 'loveland', 'lutsen-mountains', 'mad-river-glen', 'mad-river-mountain',
    'mammoth-mountain', 'marmot-basin', 'monarch', 'mont-sainte-anne', 'mount-rose',
    'mount-snow', 'mount-sunapee', 'mt-bachelor', 'mt-baker', 'mt-brighton', 'mt-hood-meadows',
    'mt-norquay', 'nakiska', 'northstar', 'okemo', 'palisades-tahoe', 'panorama', 'paoli-peaks',
    'park-city', 'pico-mountain', 'powder-mountain', 'purgatory', 'red-mountain', 'revelstoke',
    'roundtop', 'saddleback', 'schweitzer', 'seven-springs', 'sierra-at-tahoe', 'silverstar',
    'ski-butternut', 'snow-creek', 'snow-summit', 'snow-valley', 'snowbasin', 'snowbird',
    'snowmass', 'snowriver', 'snowshoe', 'solitude', 'steamboat', 'stevens-pass', 'stoneham',
    'stowe', 'stratton', 'sugar-bowl', 'sugarbush', 'sugarloaf', 'sun-peaks', 'sun-valley',
    'sunday-river', 'tamarack', 'taos', 'telluride', 'the-highlands', 'the-summit-at-snoqualmie',
    'timberline', 'tremblant', 'vail', 'whistler-blackcomb', 'whiteface', 'whitefish',
    'whitetail', 'wild-mountain', 'wildcat', 'wilmot-mountain', 'winter-park', 'wolf-creek',
  ];

  it('registers exactly the 129-resort roster, three points per resort', () => {
    const slugs = [...new Set(modelForecastData.points.map((p) => p.resortSlug))].sort();
    expect(slugs).toEqual(EXPECTED_SLUGS);
    expect(modelForecastData.points.length).toBe(387);
    for (const slug of EXPECTED_SLUGS) {
      const roles = modelForecastData.points
        .filter((p) => p.resortSlug === slug)
        .map((p) => p.role)
        .sort();
      expect(roles, slug).toEqual(['base', 'mid_mountain', 'summit']);
    }
  });

  it('serves the full capped horizon at every point; HRRR at exactly the 321 in-domain points', () => {
    const cycle = modelForecastData.cycles;
    let hrrrPoints = 0;
    const excludedPointIds = new Set<string>();
    for (const point of modelForecastData.points) {
      expect(
        (point.forecasts.ecmwf ?? []).map((r) => ({ leadDay: r.leadDay, windowEndLocalDate: r.windowEndLocalDate })),
        point.pointId,
      ).toEqual(expectedMergedWindows((point.forecasts.ecmwf ?? []), cycle.ecmwf, point, ECMWF_STEP_CEILING_HOURS));
      const hrrrRows = point.forecasts.hrrr ?? [];
      if (hrrrRows.length > 0) {
        expect(
          hrrrRows.map((r) => ({ leadDay: r.leadDay, windowEndLocalDate: r.windowEndLocalDate })),
          point.pointId,
      ).toEqual(expectedMergedWindows(hrrrRows, cycle.hrrr, point, HRRR_HORIZON_HOURS));
        hrrrPoints += 1;
      } else {
        // The 48 h rule alone would serve this point, so its empty
        // HRRR arm is the deliberate domain guard (Canadian policy /
        // Alyeska geometry), not a geometry miss.
        expect(expectedServedWindows(cycle.hrrr, point, HRRR_HORIZON_HOURS).length, point.pointId).toBeGreaterThan(0);
        excludedPointIds.add(point.pointId);
      }
    }
    // 66 points are HRRR-excluded: 63 Canadian (policy) + 3 Alyeska
    // (geometric); every US in-domain point serves the 48 h horizon.
    expect(hrrrPoints).toBe(321);
    // Identity pin for the domain guard (Codex gate, 2026-11-12): the
    // excluded points must be exactly the three points of each of the
    // 21 Canadian resorts in the roster plus Alyeska. The per-point
    // geometry checks and the 321 count alone cannot catch a swapped
    // HRRR arm between two same-timezone US points. Canadian list
    // determined 2026-11-12 from src/data/ski-areas.json (country =
    // Canada) cross-checked against the roster; a roster change must
    // update this list deliberately.
    const CANADIAN_SLUGS = [
      'banff-sunshine', 'blue-mountain-on', 'cypress-mountain', 'fernie',
      'grouse-mountain', 'kicking-horse', 'kimberley', 'lake-louise',
      'le-massif', 'marmot-basin', 'mont-sainte-anne', 'mt-norquay',
      'nakiska', 'panorama', 'red-mountain', 'revelstoke', 'silverstar',
      'stoneham', 'sun-peaks', 'tremblant', 'whistler-blackcomb',
    ];
    const expectedExcluded = new Set(
      [...CANADIAN_SLUGS, 'alyeska'].flatMap((slug) =>
        ['base', 'mid_mountain', 'summit'].map((role) => `${slug}-${role}`),
      ),
    );
    expect(excludedPointIds).toEqual(expectedExcluded);
  });
});

describe('Mount Rose (catalogue addition, estimated geometry)', () => {
  const mrPoint = modelForecastData.points.find((p) => p.pointId === 'mount-rose-base');
  const mrEcmwf = mrPoint?.forecasts.ecmwf ?? [];
  const day2Row = mrEcmwf.find((r) => r.leadDay === 2) as (typeof mrEcmwf)[number];

  it('matches the Mount Rose points by elevation mode', () => {
    expect(cataloguePointForLocation(mountRose, 'min')?.pointId).toBe('mount-rose-base');
    expect(cataloguePointForLocation(mountRose, 'avg')?.pointId).toBe('mount-rose-mid_mountain');
    expect(cataloguePointForLocation(mountRose, 'max')?.pointId).toBe('mount-rose-summit');
  });

  it('stores the full capped horizon at Mount Rose: ECMWF to the step-240 ceiling, HRRR to the 48 h horizon; no Open-Meteo records', () => {
    const cycle = modelForecastData.cycles;
    expect(
      mrEcmwf.map((r) => ({ leadDay: r.leadDay, windowEndLocalDate: r.windowEndLocalDate })),
      ).toEqual(expectedMergedWindows(mrEcmwf, cycle.ecmwf, mrPoint!, ECMWF_STEP_CEILING_HOURS));
    expect(
      (mrPoint?.forecasts.hrrr ?? []).map((r) => ({ leadDay: r.leadDay, windowEndLocalDate: r.windowEndLocalDate })),
      ).toEqual(expectedMergedWindows((mrPoint?.forecasts.hrrr ?? []), cycle.hrrr, mrPoint!, HRRR_HORIZON_HOURS));
    expect(mrPoint?.forecasts.ecmwf_openmeteo ?? []).toEqual([]);
  });

  it('resolves the day-2 window to the GRIB ECMWF arm', () => {
    const result = modelForecastFor(mountRose, 'ecmwf', 'min', day2Row.windowEndLocalDate);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.forecast.modelSlr).toBe(day2Row.modelSlr);
    expect(result?.forecast.modelSnowfallCm).toBe(day2Row.modelSnowfallCm);
  });

  it('has no model record beyond the 10-day cap in any mode', () => {
    // One day past Mount Rose's last stored window (the cap). The
    // retired Open-Meteo tail used to serve it.
    const beyondDate = addCalendarDays(mrEcmwf[mrEcmwf.length - 1]?.windowEndLocalDate as string, 1);
    for (const mode of ['ecmwf', BLEND_MODEL_ID, 'hrrr']) {
      expect(modelForecastFor(mountRose, mode, 'min', beyondDate)).toBeNull();
    }
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

  it('stores the full capped horizon at every new base point; no Open-Meteo records', () => {
    const cycle = modelForecastData.cycles;
    for (const r of CA_NEW) {
      const point = modelForecastData.points.find((p) => p.pointId === `${r.slug}-base`);
      expect(point, r.slug).toBeDefined();
      expect(
        (point?.forecasts.ecmwf ?? []).map((r) => ({ leadDay: r.leadDay, windowEndLocalDate: r.windowEndLocalDate })),
      ).toEqual(expectedMergedWindows((point?.forecasts.ecmwf ?? []), cycle.ecmwf, point!, ECMWF_STEP_CEILING_HOURS));
      expect(
        (point?.forecasts.hrrr ?? []).map((r) => ({ leadDay: r.leadDay, windowEndLocalDate: r.windowEndLocalDate })),
      ).toEqual(expectedMergedWindows((point?.forecasts.hrrr ?? []), cycle.hrrr, point!, HRRR_HORIZON_HOURS));
      expect(point?.forecasts.ecmwf_openmeteo ?? []).toEqual([]);
    }
  });

  it('resolves a day-2 GRIB window at a new resort; nothing beyond the cap', () => {
    const kirkwood = locationFor(CA_NEW[7]);
    const point = modelForecastData.points.find((p) => p.pointId === 'kirkwood-base');
    const day2 = (point?.forecasts.ecmwf ?? []).find((x) => x.leadDay === 2);
    const literal = modelForecastFor(kirkwood, 'ecmwf', 'min', day2!.windowEndLocalDate);
    expect(literal?.arm).toBe('ecmwf');
    expect(literal?.forecast.modelSlr).toBe(day2!.modelSlr);
    // One day past Kirkwood's last stored window (the cap); the
    // retired Open-Meteo tail used to serve it.
    const kirkwoodRows = point?.forecasts.ecmwf ?? [];
    const beyondDate = addCalendarDays(kirkwoodRows[kirkwoodRows.length - 1]?.windowEndLocalDate as string, 1);
    expect(modelForecastFor(kirkwood, 'ecmwf', 'min', beyondDate)).toBeNull();
    expect(modelForecastFor(kirkwood, 'hrrr', 'min', beyondDate)).toBeNull();
  });
});
