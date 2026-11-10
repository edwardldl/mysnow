import { describe, expect, it } from 'vitest';
import { ALGORITHMS } from '../constants';
import {
  applyModelMethodToDays,
  BLEND_MODEL_ID,
  ENS_EB_METHOD_ID,
  modelForecastData,
  modelForecastFor,
  modelMethodAvailability,
  modelWindowTrendLabel,
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
// The 00Z batch: ECMWF leads 2-10 at every point and no other arm's
// dates exist (the Open-Meteo tail is retired), so the stored dates
// are exactly the ECMWF window dates, 2026-11-11 .. 2026-11-19.
// day1Date is the lead-2 window -- the only window HRRR also holds;
// day2Date the lead-3 window; day9Date the lead-10 window (the cap).
const allStoredDates = ecmwfRows.map((r) => r.windowEndLocalDate);
const day1Date = ecmwfRows[0]?.windowEndLocalDate;
const day2Date = ecmwfRows[1]?.windowEndLocalDate;
const day3Date = ecmwfRows[2]?.windowEndLocalDate;
const day9Date = ecmwfRows[8]?.windowEndLocalDate;
const rowForLead = (lead: number) => ecmwfRows.find((r) => r.leadDay === lead);
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

  it('is available at every California Ikon/Epic expansion resort', () => {
    // Estimated-geometry locations (no explicit referencePoints: the
    // geometry layer derives base/mid/summit from min/max elevation).
    const caNew: Array<{ slug: string; latitude: number; longitude: number; minElevationM: number; maxElevationM: number }> = [
      { slug: 'june-mountain', latitude: 37.753661, longitude: -119.075663, minElevationM: 2314, maxElevationM: 3090 },
      { slug: 'bear-mountain', latitude: 34.219620, longitude: -116.860485, minElevationM: 2182, maxElevationM: 2678 },
      { slug: 'snow-summit', latitude: 34.227750, longitude: -116.890393, minElevationM: 2128, maxElevationM: 2498 },
      { slug: 'snow-valley', latitude: 34.214481, longitude: -117.036296, minElevationM: 2052, maxElevationM: 2388 },
      { slug: 'sierra-at-tahoe', latitude: 38.797438, longitude: -120.076847, minElevationM: 2040, maxElevationM: 2702 },
      { slug: 'heavenly', latitude: 38.937462, longitude: -119.910798, minElevationM: 2027, maxElevationM: 3037 },
      { slug: 'northstar', latitude: 39.263035, longitude: -120.133240, minElevationM: 1927, maxElevationM: 2630 },
      { slug: 'kirkwood', latitude: 38.678978, longitude: -120.067434, minElevationM: 2388, maxElevationM: 2892 },
    ];
    for (const r of caNew) {
      const loc = {
        id: r.slug,
        name: r.slug,
        latitude: r.latitude,
        longitude: r.longitude,
        elevationFt: Math.round(((r.minElevationM + r.maxElevationM) / 2) * 3.28084),
        elevationM: (r.minElevationM + r.maxElevationM) / 2,
        minElevationM: r.minElevationM,
        maxElevationM: r.maxElevationM,
        timezone: 'America/Los_Angeles',
        isCustom: false,
      } as Location;
      for (const mode of ['ecmwf', 'hrrr', BLEND_MODEL_ID]) {
        const availability = modelMethodAvailability(mode, loc, 'min');
        expect(availability.available, `${r.slug} ${mode}`).toBe(true);
        expect(availability.point?.pointId).toBe(`${r.slug}-base`);
      }
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
      expect(day.modelWindow?.reducedFeatureVariant).toBe(false);
    });
    // Input days are not mutated.
    expect(days[0].totalSnowfall).toBe(999);
    expect(days[0].modelWindow).toBeUndefined();
  });

  it('agrees exactly with modelForecastFor in blend mode (same numbers, same arm)', () => {
    const out = applyModelMethodToDays([mkDay(day2Date, 0)], palisades, BLEND_MODEL_ID, 'min');
    const resolved = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', day2Date);
    expect(resolved).not.toBeNull();
    expect(out[0].totalSnowfall).toBe(resolved?.forecast.modelSnowfallCm);
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

describe('ECMWF horizon under the 10-day cap', () => {
  const mkDay = (dateStr: string, totalSnowfall: number): DayData =>
    ({ dateStr, totalSnowfall }) as DayData;

  it('covers lead days 2-10 at both Palisades (America/Los_Angeles) and Stowe (America/New_York)', () => {
    // At the 00Z cycle the first complete 07:00-local window is lead 2
    // in both timezones; the GRIB step-240 ceiling and the 10-day cap
    // (2026-11-10) coincide at lead 10.
    expect(ecmwfRows.map((r) => r.leadDay)).toEqual([2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(stoweEcmwfRows.map((r) => r.leadDay)).toEqual([2, 3, 4, 5, 6, 7, 8, 9, 10]);
  });

  it('resolves the lead-5 window to the ECMWF arm in literal ecmwf mode', () => {
    const lead5 = rowForLead(5);
    const result = modelForecastFor(palisades, 'ecmwf', 'min', lead5?.windowEndLocalDate ?? null);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.blended).toBe(false);
    expect(result?.forecast.leadDay).toBe(5);
    expect(result?.forecast.modelSlr).toBe(lead5?.modelSlr);
    expect(result?.forecast.modelSnowfallCm).toBe(lead5?.modelSnowfallCm);
  });

  it('resolves blend lead 4 and lead 5 to the ECMWF arm (HRRR horizon is 48 h)', () => {
    for (const lead of [4, 5]) {
      const row = rowForLead(lead);
      const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', row?.windowEndLocalDate ?? null);
      expect(result?.blended).toBe(true);
      expect(result?.arm).toBe('ecmwf');
      expect(result?.forecast.leadDay).toBe(lead);
      expect(result?.forecast.modelSlr).toBe(row?.modelSlr);
      expect(result?.cycleUtc).toBe(modelForecastData.cycles.ecmwf);
    }
  });

  it('resolves the lead-10 window (the cap) at Palisades and at Stowe', () => {
    const day10 = modelForecastFor(palisades, 'ecmwf', 'min', day9Date);
    expect(day10?.forecast.leadDay).toBe(10);
    expect(day10?.forecast.modelSlr).toBe(ecmwfRows[8]?.modelSlr);
    const stoweDay10 = stoweEcmwfRows.find((r) => r.leadDay === 10);
    const stoweResult = modelForecastFor(stowe, 'ecmwf', 'min', stoweDay10?.windowEndLocalDate ?? null);
    expect(stoweResult?.forecast.leadDay).toBe(10);
    expect(stoweResult?.forecast.modelSlr).toBe(stoweDay10?.modelSlr);
  });

  it('has no Palisades record beyond the cap: the lead-11 date stays Kuchera', () => {
    // Lead 11 would end 2026-11-20 at Palisades. Before the 10-day cap
    // the Open-Meteo tail arm served this date; it is retired, so the
    // day now falls back to Kuchera in every mode.
    const beyondDate = '2026-11-20';
    expect(ecmwfRows.find((r) => r.windowEndLocalDate === beyondDate)).toBeUndefined();
    expect(modelForecastFor(palisades, 'ecmwf', 'min', beyondDate)).toBeNull();
    const out = applyModelMethodToDays([mkDay(beyondDate, 7.5)], palisades, 'ecmwf', 'min');
    expect(out[0].totalSnowfall).toBe(7.5);
    expect(out[0].modelWindow?.kind).toBe('kuchera_fallback');
    expect(out[0].modelWindow?.arm).toBeNull();
  });

  it('stays Kuchera well beyond the cap, where no arm ever held a record', () => {
    const out = applyModelMethodToDays([mkDay('2026-11-24', 7.5)], palisades, 'ecmwf', 'min');
    expect(out[0].totalSnowfall).toBe(7.5);
    expect(out[0].modelWindow?.kind).toBe('kuchera_fallback');
    const outBlend = applyModelMethodToDays([mkDay('2026-11-24', 7.5)], palisades, BLEND_MODEL_ID, 'min');
    expect(outBlend[0].modelWindow?.kind).toBe('kuchera_fallback');
  });

  it('replaces the lead-5 total with the record snowfall under the menu method', () => {
    const lead5 = rowForLead(5);
    const out = applyModelMethodToDays([mkDay(lead5!.windowEndLocalDate, 999)], palisades, BLEND_MODEL_ID, 'min');
    expect(out[0].totalSnowfall).toBe(lead5?.modelSnowfallCm);
    expect(out[0].modelWindow?.kind).toBe('model');
    expect(out[0].modelWindow?.arm).toBe('ecmwf');
  });
});


describe('retired Open-Meteo tail under the ENS-EB menu method', () => {
  const mkDay = (dateStr: string, totalSnowfall: number): DayData =>
    ({ dateStr, totalSnowfall }) as DayData;
  // Lead 12 would end 2026-11-22 at Palisades; the retired tail arm
  // used to serve it.
  const beyondCapDate = '2026-11-22';

  it('keeps beyond-cap days on Kuchera in ecmwf and blend modes (no tail records)', () => {
    for (const mode of ['ecmwf', BLEND_MODEL_ID]) {
      const out = applyModelMethodToDays([mkDay(beyondCapDate, 7.5)], palisades, mode, 'min');
      expect(out[0].totalSnowfall).toBe(7.5);
      expect(out[0].modelWindow?.kind).toBe('kuchera_fallback');
      expect(out[0].modelWindow?.arm).toBeNull();
      expect(out[0].modelWindow?.reducedFeatureVariant).toBe(false);
    }
  });

  it('keeps the beyond-cap day on Kuchera in literal hrrr mode too', () => {
    const out = applyModelMethodToDays([mkDay(beyondCapDate, 7.5)], palisades, 'hrrr', 'min');
    expect(out[0].totalSnowfall).toBe(7.5);
    expect(out[0].modelWindow?.kind).toBe('kuchera_fallback');
    expect(out[0].modelWindow?.arm).toBeNull();
    expect(out[0].modelWindow?.reducedFeatureVariant).toBe(false);
  });
});

describe('per-window marks through the standard day path', () => {
  const marksFor = (dates: string[], mode: string) =>
    applyModelMethodToDays(
      dates.map((dateStr) => ({ dateStr, totalSnowfall: 999 }) as DayData),
      palisades,
      mode,
      'min',
    ).map((day) => day.modelWindow);

  it('blend mode: every Palisades window is a model day with truthful arms', () => {
    const marks = marksFor(allStoredDates, BLEND_MODEL_ID);
    expect(marks.map((m) => [m?.kind, m?.arm])).toEqual([
      ['model', 'hrrr'],
      ...ecmwfRows.slice(1).map(() => ['model', 'ecmwf'] as const),
    ]);
  });

  it('literal hrrr mode: windows past the lead-2 window are marked Kuchera fallback, never silently filled', () => {
    const marks = marksFor(allStoredDates, 'hrrr');
    expect(marks.map((m) => m?.kind)).toEqual([
      'model',
      ...ecmwfRows.slice(1).map(() => 'kuchera_fallback' as const),
    ]);
    expect(marks[1]?.arm).toBeNull();
    expect(marks[marks.length - 1]?.arm).toBeNull();
  });

  it('literal ecmwf mode: every stored window is a model day', () => {
    const marks = marksFor(allStoredDates, 'ecmwf');
    expect(marks.map((m) => m?.kind)).toEqual(allStoredDates.map(() => 'model'));
    expect(marks.map((m) => m?.arm)).toEqual(ecmwfRows.map(() => 'ecmwf'));
  });
});

describe('inline labelling under the ENS-EB menu method', () => {
  const labelFor = (dateStr: string, mode: string): string => {
    const out = applyModelMethodToDays(
      [{ dateStr, totalSnowfall: 999 } as DayData],
      palisades,
      mode,
      'min',
    );
    return modelWindowTrendLabel(out[0].modelWindow!);
  };

  it('names the Kuchera fallback for a window with no model record', () => {
    expect(labelFor(day2Date, 'hrrr')).toBe(
      'Calendar-day snowfall · ENS-EB windows where available, Kuchera elsewhere · the 24 h window ending 07:00 local fell back to Kuchera (no ENS-EB record)',
    );
  });

  it('labels a blend HRRR window with the model label, arm and cycle', () => {
    const label = labelFor(day1Date, BLEND_MODEL_ID);
    expect(label).toContain('ENS-EB model');
    expect(label).toContain('24 h window ending 07:00 local');
    expect(label).toContain('NOAA HRRR arm');
    expect(label).toContain('Z cycle');
    expect(label).not.toContain('reduced-feature');
  });

  it('labels a GRIB ECMWF window as ECMWF IFS, never HRRR or Open-Meteo', () => {
    const label = labelFor(day2Date, BLEND_MODEL_ID);
    expect(label).toContain('ECMWF IFS arm');
    expect(label).not.toContain('NOAA HRRR');
    expect(label).not.toContain('Open-Meteo');
    expect(label).not.toContain('anchored to');
  });

  it('labels a beyond-cap day with the Kuchera fallback caption', () => {
    expect(labelFor('2026-11-22', 'ecmwf')).toBe(
      'Calendar-day snowfall · ENS-EB windows where available, Kuchera elsewhere · the 24 h window ending 07:00 local fell back to Kuchera (no ENS-EB record)',
    );
  });

  it('never labels any stored window as Open-Meteo (the arm is retired)', () => {
    for (const date of allStoredDates) {
      expect(labelFor(date, BLEND_MODEL_ID)).not.toContain('Open-Meteo');
      expect(labelFor(date, 'ecmwf')).not.toContain('Open-Meteo');
    }
  });
});
