import { afterEach, describe, expect, it, vi } from 'vitest';
import { CACHE_EXPIRATION_MS, fetchWeatherData, STALE_IF_ERROR_MAX_AGE_MS } from '../api';
import { blendForecasts } from '../data';

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

describe('forecast model pool', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('continues with ECMWF when HRRR returns an API error', async () => {
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.spyOn(console, 'time').mockImplementation(() => undefined);
    vi.spyOn(console, 'timeEnd').mockImplementation(() => undefined);

    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = new URL(typeof input === 'string' || input instanceof URL ? input.toString() : input.url);
      if (url.pathname.startsWith('/data/')) return new Response(null, { status: 404 });
      if (url.searchParams.get('models') === 'gfs_hrrr') {
        return Response.json({ reason: 'Location outside model geographic coverage' }, { status: 400 });
      }
      if (url.pathname === '/v1/ecmwf') return Response.json(forecastPayload());
      throw new Error(`Unexpected request: ${url}`);
    }));

    const result = await fetchWeatherData('palisades', 'hrrr_ecmwf', 'avg', true);

    expect(result.hrrrData).toBeNull();
    expect(result.ecmwfData.modelIdentity).toBe('ecmwf');
    expect(result.ecmwfData.requestMetadata?.rawProfileAttached).toBe(true);
    expect(warning).toHaveBeenCalledWith(expect.stringContaining('Continuing with ECMWF'));

    const forecast = blendForecasts(
      result.hrrrData,
      result.ecmwfData,
      result.location,
      'fixed_10',
      result.mode,
    );
    expect(forecast.hourly[0].model).toBe('ECMWF');
  });

  it('uses stale cache only inside the bounded stale-on-error window', async () => {
    const storage = new Map<string, string>();
    vi.stubGlobal('window', {});
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
      key: (index: number) => [...storage.keys()][index] ?? null,
      get length() { return storage.size; },
      clear: () => storage.clear(),
    });
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.spyOn(console, 'time').mockImplementation(() => undefined);
    vi.spyOn(console, 'timeEnd').mockImplementation(() => undefined);
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-15T12:00:00Z'));

    let failForecast = false;
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = new URL(typeof input === 'string' || input instanceof URL ? input.toString() : input.url);
      if (url.pathname.startsWith('/data/')) return new Response(null, { status: 404 });
      if (failForecast) throw new Error('provider unavailable');
      return Response.json(forecastPayload());
    }));

    const fresh = await fetchWeatherData('palisades', 'best_match', 'avg', true);
    expect(fresh.status).toBe('fresh');
    expect(fresh.cacheAgeMs).toBe(0);

    failForecast = true;
    vi.advanceTimersByTime(CACHE_EXPIRATION_MS + 1);
    const stale = await fetchWeatherData('palisades', 'best_match', 'avg', true);
    expect(stale.status).toBe('stale');
    expect(stale.cacheAgeMs).toBe(CACHE_EXPIRATION_MS + 1);
    expect(stale.fetchedAtMs).toBe(fresh.fetchedAtMs);

    vi.advanceTimersByTime(STALE_IF_ERROR_MAX_AGE_MS - CACHE_EXPIRATION_MS);
    await expect(fetchWeatherData('palisades', 'best_match', 'avg', true))
      .rejects.toThrow('provider unavailable');
  });
});
