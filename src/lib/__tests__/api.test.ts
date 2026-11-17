import { afterEach, describe, expect, it, vi } from 'vitest';
import { CACHE_EXPIRATION_MS, fetchWeatherData, isCacheSupersededByNewerRun, STALE_IF_ERROR_MAX_AGE_MS } from '../api';
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

  it('routes Open-Meteo calls through the same-origin caching proxy when a browser origin exists', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.spyOn(console, 'time').mockImplementation(() => undefined);
    vi.spyOn(console, 'timeEnd').mockImplementation(() => undefined);

    const storage = new Map<string, string>();
    vi.stubGlobal('window', { location: { origin: 'http://localhost:3000' } });
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
      key: (index: number) => [...storage.keys()][index] ?? null,
      get length() { return storage.size; },
      clear: () => storage.clear(),
    });

    const requested: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const raw = typeof input === 'string' || input instanceof URL ? input.toString() : input.url;
      requested.push(raw);
      const proxyUrl = new URL(raw);
      const upstream = new URL(proxyUrl.searchParams.get('url') ?? '');
      if (upstream.pathname.startsWith('/data/')) return new Response(null, { status: 404 });
      return Response.json(forecastPayload());
    }));

    const result = await fetchWeatherData('palisades', 'best_match', 'avg', true);
    expect(result.status).toBe('fresh');
    expect(requested.length).toBeGreaterThan(0);
    // Every browser fetch goes to our origin's proxy route...
    for (const raw of requested) {
      expect(raw.startsWith('http://localhost:3000/api/open-meteo?url=')).toBe(true);
    }
    // ...carrying the real Open-Meteo upstream URL (forecast + meta).
    const upstreams = requested.map((raw) => new URL(new URL(raw).searchParams.get('url') ?? ''));
    expect(upstreams.some((u) => u.hostname === 'api.open-meteo.com' && u.pathname === '/v1/forecast')).toBe(true);
    expect(upstreams.some((u) => u.pathname === '/data/ecmwf_ifs/static/meta.json')).toBe(true);
  });

  function stubBrowserEnv() {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.spyOn(console, 'time').mockImplementation(() => undefined);
    vi.spyOn(console, 'timeEnd').mockImplementation(() => undefined);
    const storage = new Map<string, string>();
    vi.stubGlobal('window', { location: { origin: 'http://localhost:3000' } });
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
      key: (index: number) => [...storage.keys()][index] ?? null,
      get length() { return storage.size; },
      clear: () => storage.clear(),
    });
    return storage;
  }

  function proxiedUpstream(input: string | URL | Request): URL {
    const raw = typeof input === 'string' || input instanceof URL ? input.toString() : input.url;
    return new URL(new URL(raw).searchParams.get('url') ?? '');
  }

  it('treats a proxy STALE answer as an upstream failure, not as fresh data', async () => {
    stubBrowserEnv();
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const upstream = proxiedUpstream(input);
      if (upstream.pathname.startsWith('/data/')) return new Response(null, { status: 404 });
      return Response.json(forecastPayload(), { headers: { 'X-Cache': 'STALE', Age: '3600' } });
    }));

    await expect(fetchWeatherData('palisades', 'best_match', 'avg', true))
      .rejects.toThrow(/stale/i);
  });

  it('backdates client freshness by the server-cache Age of the payload', async () => {
    const storage = stubBrowserEnv();
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const upstream = proxiedUpstream(input);
      if (upstream.pathname.startsWith('/data/')) return new Response(null, { status: 404 });
      return Response.json(forecastPayload(), { headers: { 'X-Cache': 'HIT', Age: '600' } });
    }));

    const before = Date.now();
    const result = await fetchWeatherData('palisades', 'best_match', 'avg', true);
    expect(result.status).toBe('cached');
    expect(result.cacheAgeMs).toBe(600_000);
    expect(result.fetchedAtMs).toBeLessThanOrEqual(before - 600_000 + 10_000);
    expect(result.ecmwfData.requestMetadata?.serverCacheAgeMs).toBe(600_000);

    const cacheEntry = [...storage.entries()].find(([key]) => key.includes('mysnow_weather_cache_'));
    expect(cacheEntry).toBeDefined();
    const stored = JSON.parse(cacheEntry![1]) as { timestamp: number };
    expect(stored.timestamp).toBeLessThanOrEqual(before - 600_000 + 10_000);
  });

  it('continues with surface forcing only when the proxy answers 502 for the raw profile', async () => {
    stubBrowserEnv();
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const upstream = proxiedUpstream(input);
      if (upstream.pathname.startsWith('/data/')) return new Response(null, { status: 404 });
      if (upstream.searchParams.get('cell_selection') === 'nearest') {
        return new Response('Open-Meteo upstream request failed', { status: 502 });
      }
      return Response.json(forecastPayload());
    }));

    const result = await fetchWeatherData('palisades', 'best_match', 'avg', true);
    expect(result.status).toBe('fresh');
    expect(result.ecmwfData.requestMetadata?.rawProfileAttached).toBe(false);
  });
});

describe('ECMWF vintage guard', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function stubEnv() {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.spyOn(console, 'time').mockImplementation(() => undefined);
    vi.spyOn(console, 'timeEnd').mockImplementation(() => undefined);
    const storage = new Map<string, string>();
    vi.stubGlobal('window', { location: { origin: 'http://localhost:3000' } });
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
      key: (index: number) => [...storage.keys()][index] ?? null,
      get length() { return storage.size; },
      clear: () => storage.clear(),
    });
    return storage;
  }

  function upstreamOf(input: string | URL | Request): URL {
    const raw = typeof input === 'string' || input instanceof URL ? input.toString() : input.url;
    return new URL(new URL(raw).searchParams.get('url') ?? '');
  }

  function payloadWithTemp(temp: number) {
    const payload = forecastPayload();
    return { ...payload, hourly: { ...payload.hourly, temperature_2m: [temp] } };
  }

  function storedEntry(storage: Map<string, string>, mode: string) {
    const entry = [...storage.entries()].find(([key]) => key.includes(`|${mode}|`));
    return entry ? (JSON.parse(entry[1]) as { ecmwfRunAvailabilityTime?: number }) : undefined;
  }

  it('decides supersession from run stamps only', () => {
    expect(isCacheSupersededByNewerRun(undefined, undefined)).toBe(false);
    expect(isCacheSupersededByNewerRun(1000, undefined)).toBe(false);
    expect(isCacheSupersededByNewerRun(undefined, 1000)).toBe(true);
    expect(isCacheSupersededByNewerRun(999, 1000)).toBe(true);
    expect(isCacheSupersededByNewerRun(1000, 1000)).toBe(false);
    expect(isCacheSupersededByNewerRun(1001, 1000)).toBe(false);
  });

  it('refetches an ECMWF cache entry once a newer run is available', async () => {
    const storage = stubEnv();
    let currentRun = 1000;
    let marker = -5;
    let ecmwfHits = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = upstreamOf(input);
      if (url.pathname.endsWith('/static/meta.json')) {
        return Response.json({ last_run_availability_time: url.pathname.includes('ecmwf_ifs') ? currentRun : 500 });
      }
      if (url.pathname.startsWith('/data/')) return new Response(null, { status: 404 });
      if (url.pathname === '/v1/ecmwf') {
        ecmwfHits += 1;
        return Response.json(payloadWithTemp(marker));
      }
      return Response.json(forecastPayload());
    }));

    const first = await fetchWeatherData('palisades', 'ecmwf', 'avg', true);
    expect(first.ecmwfData.hourly.temperature_2m[0]).toBe(-5);
    expect(storedEntry(storage, 'ecmwf')?.ecmwfRunAvailabilityTime).toBe(1000);

    // Same run still current: the cache is served, no new fetch.
    const hitsAfterFirst = ecmwfHits;
    const cachedHit = await fetchWeatherData('palisades', 'ecmwf', 'avg');
    expect(cachedHit.status).toBe('cached');
    expect(ecmwfHits).toBe(hitsAfterFirst);

    // A newer run lands: the next read refetches instead of serving
    // the previous vintage, and restamps the entry.
    currentRun = 2000;
    marker = -9;
    const refreshed = await fetchWeatherData('palisades', 'ecmwf', 'avg');
    expect(refreshed.status).toBe('fresh');
    expect(refreshed.ecmwfData.hourly.temperature_2m[0]).toBe(-9);
    expect(storedEntry(storage, 'ecmwf')?.ecmwfRunAvailabilityTime).toBe(2000);
  });

  it('applies the same guard to the blend mode spine', async () => {
    const storage = stubEnv();
    let currentRun = 1000;
    let marker = -5;
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = upstreamOf(input);
      if (url.pathname.endsWith('/static/meta.json')) {
        return Response.json({ last_run_availability_time: url.pathname.includes('ecmwf_ifs') ? currentRun : 500 });
      }
      if (url.pathname.startsWith('/data/')) return new Response(null, { status: 404 });
      if (url.searchParams.get('models') === 'gfs_hrrr') {
        return Response.json({ reason: 'Location outside model geographic coverage' }, { status: 400 });
      }
      if (url.pathname === '/v1/ecmwf') return Response.json(payloadWithTemp(marker));
      return Response.json(forecastPayload());
    }));

    const first = await fetchWeatherData('palisades', 'hrrr_ecmwf', 'avg', true);
    expect(first.ecmwfData.hourly.temperature_2m[0]).toBe(-5);
    expect(storedEntry(storage, 'hrrr_ecmwf')?.ecmwfRunAvailabilityTime).toBe(1000);

    currentRun = 2000;
    marker = -9;
    const refreshed = await fetchWeatherData('palisades', 'hrrr_ecmwf', 'avg');
    expect(refreshed.ecmwfData.hourly.temperature_2m[0]).toBe(-9);
    expect(storedEntry(storage, 'hrrr_ecmwf')?.ecmwfRunAvailabilityTime).toBe(2000);
  });

  it('keeps serving the cache when the run clock is unavailable', async () => {
    stubEnv();
    let metaOk = true;
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = upstreamOf(input);
      if (url.pathname.endsWith('/static/meta.json')) {
        if (!metaOk) return new Response(null, { status: 503 });
        return Response.json({ last_run_availability_time: 1000 });
      }
      if (url.pathname.startsWith('/data/')) return new Response(null, { status: 404 });
      return Response.json(forecastPayload());
    }));

    await fetchWeatherData('palisades', 'ecmwf', 'avg', true);
    metaOk = false;
    const served = await fetchWeatherData('palisades', 'ecmwf', 'avg');
    expect(served.status).toBe('cached');
  });

  it('does not stamp a server-cached older payload with the newer run', async () => {
    const storage = stubEnv();
    vi.useFakeTimers();
    // 2026-01-15T12:00:00Z. Run A became available earlier; run B
    // ten minutes ago.
    vi.setSystemTime(new Date('2026-01-15T12:00:00Z'));
    const runA = 1768470000;
    const runB = 1768477800;
    let currentRun = runA;
    let serveAgeSeconds = 0;
    let ecmwfHits = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = upstreamOf(input);
      if (url.pathname.endsWith('/static/meta.json')) {
        return Response.json({ last_run_availability_time: url.pathname.includes('ecmwf_ifs') ? currentRun : 500 });
      }
      if (url.pathname.startsWith('/data/')) return new Response(null, { status: 404 });
      if (url.pathname === '/v1/ecmwf') {
        ecmwfHits += 1;
        const headers = serveAgeSeconds > 0 ? { Age: String(serveAgeSeconds) } : undefined;
        return Response.json(payloadWithTemp(-5), { headers });
      }
      return Response.json(forecastPayload());
    }));

    await fetchWeatherData('palisades', 'ecmwf', 'avg', true);
    expect(storedEntry(storage, 'ecmwf')?.ecmwfRunAvailabilityTime).toBe(runA);

    // Run B lands, but the refetch is answered from the server
    // cache with a 20-minute-old copy of run A's payload: its
    // effective fetch time predates run B, so it must not be
    // stamped as run B.
    currentRun = runB;
    serveAgeSeconds = 1200;
    const refetched = await fetchWeatherData('palisades', 'ecmwf', 'avg');
    expect(refetched.ecmwfData.hourly.temperature_2m[0]).toBe(-5);
    expect(storedEntry(storage, 'ecmwf')?.ecmwfRunAvailabilityTime).toBeUndefined();

    // And the next read retries the refetch instead of serving the
    // unstamped entry as current.
    const hitsBefore = ecmwfHits;
    await fetchWeatherData('palisades', 'ecmwf', 'avg');
    expect(ecmwfHits).toBeGreaterThan(hitsBefore);
  });
});
