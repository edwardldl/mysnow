import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Location, OpenMeteoResponse } from '../types';

const { normalizeProfileMock, calculateSnowfallMock } = vi.hoisted(() => ({
  normalizeProfileMock: vi.fn(),
  calculateSnowfallMock: vi.fn(),
}));

vi.mock('../snow/profile', async importOriginal => {
  const actual = await importOriginal<typeof import('../snow/profile')>();
  return {
    ...actual,
    normalizeProfile: (...args: Parameters<typeof actual.normalizeProfile>) => {
      normalizeProfileMock(...args);
      return actual.normalizeProfile(...args);
    },
  };
});

vi.mock('../snow/snowfall', async importOriginal => {
  const actual = await importOriginal<typeof import('../snow/snowfall')>();
  return {
    ...actual,
    calculateSnowfall: (...args: Parameters<typeof actual.calculateSnowfall>) => {
      calculateSnowfallMock(...args);
      return actual.calculateSnowfall(...args);
    },
  };
});

import { blendForecasts, groupData } from '../data';

const location: Location = {
  id: 'test-resort',
  name: 'Test Resort',
  latitude: 39,
  longitude: -120,
  elevationM: 2499,
  elevationFt: 8200,
  timezone: 'America/Los_Angeles',
};

function response(requestedElevationM: number, snowDepthM = 2.4): OpenMeteoResponse {
  return {
    latitude: 39,
    longitude: -120,
    elevation: requestedElevationM,
    timezone: 'America/Los_Angeles',
    timezone_abbreviation: 'PST',
    requestMetadata: {
      requestedLatitude: 39,
      requestedLongitude: -120,
      requestedElevationM,
      modelGridElevationM: requestedElevationM,
      rawProfileAttached: false,
    },
    hourly: {
      time: ['2026-01-15T12:00'],
      temperature_2m: [-4],
      precipitation: [0],
      snowfall: [0],
      snow_depth: [snowDepthM],
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

describe('forecast point elevation', () => {
  beforeEach(() => {
    normalizeProfileMock.mockClear();
    calculateSnowfallMock.mockClear();
  });

  it('passes each requested base, mid, and summit elevation into profile physics', () => {
    for (const elevationM of [1889, 2499, 2708]) {
      const forecast = blendForecasts(null, response(elevationM), location, 'fixed_10');
      expect(forecast.hourly[0].provenance?.requestedElevationM).toBe(elevationM);
      expect(forecast.hourly[0].snowLevel).toBe(Math.round(elevationM - 4 * (1000 / 6.5) - 275));
    }

    expect(normalizeProfileMock.mock.calls.map(call => call[1]?.stationElevationM))
      .toEqual([1889, 2499, 2708]);
    expect(calculateSnowfallMock.mock.calls.map(call => call[0].surface.stationElevationM))
      .toEqual([1889, 2499, 2708]);
    expect(calculateSnowfallMock.mock.calls.map(call => call[2]?.context?.elevationM))
      .toEqual([1889, 2499, 2708]);
  });
});

describe('snowpack semantics', () => {
  it('keeps provider total depth separate from the zero-initialized rolling pack', () => {
    const grouped = groupData(blendForecasts(null, response(1889, 2.4), location, 'fixed_10'));
    const hour = grouped[0].hourly[0];

    expect(hour.providerSnowDepthM).toBe(2.4);
    expect(hour.modeledSnowpackDepthM).toBe(0);
    expect(grouped[0].providerSnowDepth).toBe('240 cm');
    expect(grouped[0].modeledRollingSnowpackDepth).toBe('0 cm');
    expect(grouped[0].modeledSnowpackHorizonStart).toBe('2026-01-15T12:00');
  });
});
