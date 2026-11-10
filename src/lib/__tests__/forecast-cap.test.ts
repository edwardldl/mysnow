import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { fetchBulkWeatherData, fetchElevationTriad, fetchWeatherData, MAX_FORECAST_DAYS } from '../api';
import { capForecastDays } from '../data';
import {
  BLEND_MODEL_ID,
  MAX_MODEL_LEAD_DAY,
  modelArmPreference,
  modelForecastFor,
  setModelForecastDataForTests,
  type ModelForecastData,
} from '../model-forecast';
import type { Location } from '../types';

/**
 * The 10-day forecast cap (user decision 2026-11-10: "only do 10 days
 * for all forecast across all surfaces"), pinned at every layer:
 *  - fetches never ask Open-Meteo for more than MAX_FORECAST_DAYS;
 *  - the display filter drops grouped days beyond today + 9;
 *  - ENS-EB resolution ignores serving records beyond lead 10 and
 *    never resolves the retired Open-Meteo arm -- even when a stale
 *    or hand-edited serving file still carries such records.
 */

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

describe('cap constants agree', () => {
  it('fetch, display and model caps are all 10 days', () => {
    expect(MAX_FORECAST_DAYS).toBe(10);
    expect(MAX_MODEL_LEAD_DAY).toBe(10);
  });
});

describe('fetch cap', () => {
  const requestedUrls: string[] = [];

  function forecastPayload() {
    return {
      latitude: 39.2,
      longitude: -120.3,
      elevation: 2499,
      timezone: 'America/Los_Angeles',
      timezone_abbreviation: 'GMT-7',
      hourly: {
        time: ['2026-01-15T12:00'],
        temperature_2m: [-5],
        precipitation: [1],
        snowfall: [1],
        relative_humidity_2m: [90],
        surface_pressure: [750],
      },
      daily: {
        time: ['2026-01-15'],
        sunrise: ['2026-01-15T07:15'],
        sunset: ['2026-01-15T17:00'],
      },
    };
  }

  function ensemblePayload() {
    const base = forecastPayload();
    return {
      ...base,
      hourly: {
        ...base.hourly,
        temperature_2m_member01: [-5],
        precipitation_member01: [1],
        snowfall_member01: [1],
      },
    };
  }

  beforeAll(() => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.spyOn(console, 'time').mockImplementation(() => undefined);
    vi.spyOn(console, 'timeEnd').mockImplementation(() => undefined);
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = typeof input === 'string' || input instanceof URL ? input.toString() : input.url;
      requestedUrls.push(url);
      const parsed = new URL(url);
      if (parsed.pathname.startsWith('/data/')) return new Response(null, { status: 404 });
      if (parsed.hostname === 'ensemble-api.open-meteo.com') return Response.json(ensemblePayload());
      return Response.json(forecastPayload());
    }));
  });

  afterAll(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('no fetch entry point or mode requests more than 10 forecast days', async () => {
    // Per-case assertions (Codex reviews 2026-11-10): each entry point /
    // mode combination must actually issue forecast requests, and every
    // one of them must respect the cap. Failures are NOT swallowed: the
    // stubs return valid payloads for every branch (ensemble payloads
    // carry member series), so a case that rejects or stops before a
    // fetch branch fails this test instead of passing silently.
    const cases: Array<{
      name: string;
      run: () => Promise<unknown>;
      // Extra branch-coverage check over this case's requested URLs.
      branchCheck?: (urls: string[]) => void;
    }> = [
      { name: 'fetchWeatherData best_match', run: () => fetchWeatherData('palisades', 'best_match', 'avg', true) },
      {
        name: 'fetchWeatherData hrrr_ecmwf',
        run: () => fetchWeatherData('palisades', 'hrrr_ecmwf', 'avg', true),
        branchCheck: (urls) => {
          // Both sub-fetches happen: HRRR and the ECMWF endpoint.
          expect(urls.some((u) => new URL(u).searchParams.get('models') === 'gfs_hrrr')).toBe(true);
          expect(urls.some((u) => new URL(u).pathname === '/v1/ecmwf')).toBe(true);
        },
      },
      { name: 'fetchWeatherData ecmwf', run: () => fetchWeatherData('palisades', 'ecmwf', 'avg', true) },
      { name: 'fetchWeatherData gfs', run: () => fetchWeatherData('palisades', 'gfs', 'avg', true) },
      {
        name: 'fetchWeatherData ensemble',
        run: () => fetchWeatherData('palisades', 'ecmwf_aifs_ensemble', 'avg', true),
        branchCheck: (urls) => {
          expect(urls.some((u) => new URL(u).hostname === 'ensemble-api.open-meteo.com')).toBe(true);
        },
      },
      { name: 'fetchBulkWeatherData best_match', run: () => fetchBulkWeatherData(['palisades'], 'best_match', 'avg') },
      { name: 'fetchBulkWeatherData hrrr_ecmwf', run: () => fetchBulkWeatherData(['palisades'], 'hrrr_ecmwf', 'avg') },
      { name: 'fetchBulkWeatherData ecmwf', run: () => fetchBulkWeatherData(['palisades'], 'ecmwf', 'avg') },
      { name: 'fetchBulkWeatherData gfs', run: () => fetchBulkWeatherData(['palisades'], 'gfs', 'avg') },
      { name: 'fetchBulkWeatherData ensemble', run: () => fetchBulkWeatherData(['palisades'], 'ecmwf_aifs_ensemble', 'avg') },
    ];
    // The elevation triad warms all three reference points (base /
    // mid / summit) in one combined multi-location request per branch,
    // so branch coverage means: some request in the case carries all
    // three latitudes.
    const triadBranchCheck = (urls: string[]) => {
      const covered = urls.some(
        (u) => (new URL(u).searchParams.get('latitude') ?? '').split(',').length >= 3,
      );
      expect(covered, 'triad case never requested all three elevation points').toBe(true);
    };
    for (const mode of ['best_match', 'hrrr_ecmwf', 'ecmwf', 'gfs', 'ecmwf_aifs_ensemble']) {
      cases.push({
        name: `fetchElevationTriad ${mode}`,
        run: () => fetchElevationTriad('palisades', mode),
        branchCheck: triadBranchCheck,
      });
    }
    let sawFullHorizon = false;
    for (const { name, run, branchCheck } of cases) {
      const before = requestedUrls.length;
      await run();
      const caseUrls = requestedUrls.slice(before);
      const caseDays = caseUrls
        .map((u) => new URL(u).searchParams.get('forecast_days'))
        .filter((v): v is string => v !== null)
        .map(Number);
      expect(caseDays.length, `${name} issued no forecast_days request`).toBeGreaterThan(0);
      for (const days of caseDays) {
        expect(days, `${name} requested ${days} forecast days`).toBeLessThanOrEqual(10);
      }
      branchCheck?.(caseUrls);
      if (caseDays.includes(MAX_FORECAST_DAYS)) sawFullHorizon = true;
    }
    // At least one surface asks for the full capped horizon (the cap is
    // a ceiling, not an accidental shorter default).
    expect(sawFullHorizon).toBe(true);
  });
});

describe('display cap (capForecastDays)', () => {
  const day = (dateStr: string) => ({ dateStr });

  it('keeps today through today + 9 and drops today + 10', () => {
    const days = ['2026-11-10', '2026-11-11', '2026-11-19', '2026-11-20', '2026-11-25'].map(day);
    expect(capForecastDays(days, '2026-11-10', 10).map((d) => d.dateStr)).toEqual([
      '2026-11-10',
      '2026-11-11',
      '2026-11-19',
    ]);
  });

  it('keeps past days (history is not forecast)', () => {
    const days = ['2026-11-03', '2026-11-09', '2026-11-10'].map(day);
    expect(capForecastDays(days, '2026-11-10', 10)).toHaveLength(3);
  });

  it('rolls over month boundaries by calendar days', () => {
    const days = ['2026-11-28', '2026-12-07', '2026-12-08'].map(day);
    expect(capForecastDays(days, '2026-11-28', 10).map((d) => d.dateStr)).toEqual([
      '2026-11-28',
      '2026-12-07',
    ]);
  });
});

describe('model resolution cap under injected beyond-cap data', () => {
  const row = (leadDay: number, windowEndLocalDate: string, modelSnowfallCm: number) => ({
    leadDay,
    windowStartUtc: '2026-11-09T15:00:00Z',
    windowEndUtc: '2026-11-10T15:00:00Z',
    windowEndLocalDate,
    modelSlr: 12,
    modelSnowfallCm,
    kucheraSlr: 10,
    kucheraSnowfallCm: modelSnowfallCm,
    liquidMm: 5,
  });

  const injected = {
    generatedAtUtc: '2026-11-10T00:00:00Z',
    modelVersion: 'cap-test',
    modelLabel: 'ENS-EB (cap test)',
    featureBuilderVersion: 'cap-test',
    nanPolicy: 'cap-test',
    cycles: { ecmwf: '2026-11-10T00:00:00Z', hrrr: '2026-11-10T00:00:00Z' },
    points: [
      {
        pointId: 'palisades-tahoe-base',
        resortSlug: 'palisades-tahoe',
        resortName: 'Palisades Tahoe',
        pointName: 'Village base',
        role: 'base',
        latitude: 39.196999,
        longitude: -120.24313,
        elevationM: 1889,
        timezone: 'America/Los_Angeles',
        forecasts: {
          ecmwf: [row(2, '2026-11-11', 3.25), row(10, '2026-11-19', 4.5), row(12, '2026-11-21', 9.9)],
          ecmwf_openmeteo: [row(12, '2026-11-21', 9.9)],
        },
      },
    ],
  } as unknown as ModelForecastData;

  beforeAll(() => setModelForecastDataForTests(injected));
  afterAll(() => setModelForecastDataForTests(null));

  it('still resolves the lead-10 window (the cap is a ceiling, not a cut)', () => {
    const result = modelForecastFor(palisades, 'ecmwf', 'min', '2026-11-19');
    expect(result?.arm).toBe('ecmwf');
    expect(result?.forecast.leadDay).toBe(10);
    expect(result?.forecast.modelSnowfallCm).toBe(4.5);
  });

  it('blend falls back to ECMWF at lead 2 when the preferred HRRR arm has no record', () => {
    // The fixture point holds an ECMWF lead-2 row and NO HRRR rows at
    // all: lead 2 is inside HRRR's privileged horizon, so this is the
    // preferred-arm-missing fallback (the case the 12Z batch exercised
    // at Palisades; at the 00Z batch every in-domain point has HRRR).
    const result = modelForecastFor(palisades, BLEND_MODEL_ID, 'min', '2026-11-11');
    expect(result?.blended).toBe(true);
    expect(result?.arm).toBe('ecmwf');
    expect(result?.forecast.leadDay).toBe(2);
    expect(result?.forecast.modelSnowfallCm).toBe(3.25);
    expect(result?.cycleUtc).toBe('2026-11-10T00:00:00Z');
    // Literal HRRR mode still shows nothing rather than borrowing.
    expect(modelForecastFor(palisades, 'hrrr', 'min', '2026-11-11')).toBeNull();
  });

  it('ignores a stored lead-12 record in every mode', () => {
    expect(modelForecastFor(palisades, 'ecmwf', 'min', '2026-11-21')).toBeNull();
    expect(modelForecastFor(palisades, BLEND_MODEL_ID, 'min', '2026-11-21')).toBeNull();
    expect(modelForecastFor(palisades, 'hrrr', 'min', '2026-11-21')).toBeNull();
  });

  it('never lists the retired Open-Meteo arm in any preference order', () => {
    for (let lead = 1; lead <= 14; lead++) {
      expect(modelArmPreference('ecmwf', lead)).not.toContain('ecmwf_openmeteo');
      expect(modelArmPreference(BLEND_MODEL_ID, lead)).not.toContain('ecmwf_openmeteo');
      expect(modelArmPreference('hrrr', lead)).not.toContain('ecmwf_openmeteo');
    }
  });
});
