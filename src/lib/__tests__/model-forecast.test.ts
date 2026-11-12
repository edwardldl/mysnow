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
    // Palisades base's only HRRR window in the batch is the lead-2
    // window; the lead-3 window has no HRRR row: literal HRRR mode
    // shows nothing rather than borrowing the ECMWF record.
    const lead3Date = ecmwfRows[1]?.windowEndLocalDate;
    expect(modelForecastFor(palisades, 'hrrr', 'min', lead3Date)).toBeNull();
    expect(modelForecastFor(palisades, 'ecmwf', 'min', lead3Date)?.arm).toBe('ecmwf');
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
  // The batch starts at lead 2: ecmwfRows[0] is the lead-2 window,
  // the only window HRRR also holds.
  const lead2Date = ecmwfRows[0]?.windowEndLocalDate;
  const lead3Date = ecmwfRows[1]?.windowEndLocalDate;
  const lead4Date = ecmwfRows[2]?.windowEndLocalDate;

  it('serves the HRRR arm for the lead-2 window (inside the 48 h horizon)', () => {
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', lead2Date);
    expect(result?.blended).toBe(true);
    expect(result?.arm).toBe('hrrr');
    expect(result?.forecast.modelSlr).toBe(hrrrRows[0]?.modelSlr);
    expect(result?.forecast.modelSnowfallCm).toBe(hrrrRows[0]?.modelSnowfallCm);
    expect(result?.cycleUtc).toBe(modelForecastData.cycles.hrrr);
  });

  it('falls back to the ECMWF arm, labelled ECMWF, when HRRR has no lead-3 record', () => {
    // Fixture premise: Palisades base has no HRRR row for the lead-3
    // window in the serving data (its only HRRR window is lead 2).
    expect(hrrrRows.find((row) => row.windowEndLocalDate === lead3Date)).toBeUndefined();
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', lead3Date);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.forecast.modelSlr).toBe(ecmwfRows[1]?.modelSlr);
    expect(result?.cycleUtc).toBe(modelForecastData.cycles.ecmwf);
    // Same values the literal ECMWF mode shows for this window.
    expect(result?.forecast).toEqual(
      modelForecastFor(palisades, 'ecmwf', 'min', lead3Date)?.forecast,
    );
  });

  it('keeps the HRRR arm on the lead-2 window where HRRR has a record (Stowe)', () => {
    const result = modelForecastFor(stowe, BLEND_MODEL_ID, 'min', lead2Date);
    expect(result?.arm).toBe('hrrr');
    expect(result?.point.pointId).toBe('stowe-base');
    const stoweHrrrLead2 = modelForecastData.points
      .find((p) => p.pointId === 'stowe-base')
      ?.forecasts.hrrr?.find((row) => row.windowEndLocalDate === lead2Date);
    expect(result?.forecast.modelSlr).toBe(stoweHrrrLead2?.modelSlr);
  });

  it('serves the ECMWF arm for the lead-4 window (beyond the HRRR horizon)', () => {
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', lead4Date);
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
    expect(modelForecastFor(farAway, BLEND_MODEL_ID, 'avg', lead2Date)).toBeNull();
  });

  it('resolves no model forecast for a date with no stored window in blend mode', () => {
    expect(modelForecastFor(palisades, BLEND_MODEL_ID, 'min', '2030-01-01')).toBeNull();
  });
});


describe('10-day cap and the retired Open-Meteo arm', () => {
  const palisadesPoint = modelForecastData.points.find((p) => p.pointId === 'palisades-tahoe-base');
  const ecmwfRows = palisadesPoint?.forecasts.ecmwf ?? [];

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

  it('serves the full capped horizon at Palisades: ECMWF leads 2-10, HRRR lead 2', () => {
    expect((palisadesPoint?.forecasts.ecmwf ?? []).map((r) => r.leadDay)).toEqual(
      [2, 3, 4, 5, 6, 7, 8, 9, 10],
    );
    expect((palisadesPoint?.forecasts.hrrr ?? []).map((r) => r.leadDay)).toEqual([2]);
  });

  it('has no model record beyond the cap in any mode', () => {
    // Derived from the installed file itself (batch-agnostic): the
    // lead-11 date is one day past the file's last stored Palisades
    // window (lead 10); lead 13 — the old Open-Meteo tail's
    // territory — is three days past it. No arm stores them.
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

  it('serves ECMWF leads 2-10 at every point; HRRR lead 2 at exactly the 321 in-domain points', () => {
    let hrrrPoints = 0;
    for (const point of modelForecastData.points) {
      expect((point.forecasts.ecmwf ?? []).map((r) => r.leadDay), point.pointId).toEqual(
        [2, 3, 4, 5, 6, 7, 8, 9, 10],
      );
      const hrrrLeads = (point.forecasts.hrrr ?? []).map((r) => r.leadDay);
      if (hrrrLeads.length > 0) {
        expect(hrrrLeads, point.pointId).toEqual([2]);
        hrrrPoints += 1;
      }
    }
    // 66 points are HRRR-excluded: 63 Canadian (policy) + 3 Alyeska
    // (geometric); every US in-domain point serves lead 2.
    expect(hrrrPoints).toBe(321);
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

  it('stores GRIB leads 2-10 and the HRRR lead-2 row; no Open-Meteo records', () => {
    expect(mrEcmwf.map((r) => r.leadDay)).toEqual([2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect((mrPoint?.forecasts.hrrr ?? []).map((r) => r.leadDay)).toEqual([2]);
    expect(mrPoint?.forecasts.ecmwf_openmeteo ?? []).toEqual([]);
  });

  it('resolves the day-2 window to the GRIB ECMWF arm', () => {
    const result = modelForecastFor(mountRose, 'ecmwf', 'min', day2Row.windowEndLocalDate);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.forecast.modelSlr).toBe(day2Row.modelSlr);
    expect(result?.forecast.modelSnowfallCm).toBe(day2Row.modelSnowfallCm);
  });

  it('has no model record beyond the 10-day cap in any mode', () => {
    // One day past Mount Rose's last stored window (lead 10) is the
    // lead-11 date. The retired Open-Meteo tail used to serve it.
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

  it('stores GRIB leads 2-10 and the HRRR lead-2 row at every new base point; no Open-Meteo records', () => {
    for (const r of CA_NEW) {
      const point = modelForecastData.points.find((p) => p.pointId === `${r.slug}-base`);
      expect(point, r.slug).toBeDefined();
      expect((point?.forecasts.ecmwf ?? []).map((x) => x.leadDay)).toEqual([2, 3, 4, 5, 6, 7, 8, 9, 10]);
      expect((point?.forecasts.hrrr ?? []).map((x) => x.leadDay)).toEqual([2]);
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
    // One day past Kirkwood's last stored window is the lead-11
    // date; the retired Open-Meteo tail used to serve it.
    const kirkwoodRows = point?.forecasts.ecmwf ?? [];
    const beyondDate = addCalendarDays(kirkwoodRows[kirkwoodRows.length - 1]?.windowEndLocalDate as string, 1);
    expect(modelForecastFor(kirkwood, 'ecmwf', 'min', beyondDate)).toBeNull();
    expect(modelForecastFor(kirkwood, 'hrrr', 'min', beyondDate)).toBeNull();
  });
});
