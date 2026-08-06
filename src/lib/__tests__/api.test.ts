import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchWeatherData } from '../api';
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
});
