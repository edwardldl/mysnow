import { normalizeProfile, type RawPressureLayer } from '../profile';
import type { SnowfallInput, SurfaceMeteorology } from '../types';

const baseLayers: RawPressureLayer[] = [
  { pressureHpa: 850, geopotentialHeightM: 1500, temperatureC: -5, dewPointC: -5.2, relativeHumidityWaterPct: 97, verticalVelocityMs: -0.03, windSpeedMs: 6, windDirectionDeg: 220, cloudCoverPct: 95 },
  { pressureHpa: 800, geopotentialHeightM: 2000, temperatureC: -8, dewPointC: -8.2, relativeHumidityWaterPct: 97, verticalVelocityMs: -0.04, windSpeedMs: 7, windDirectionDeg: 220, cloudCoverPct: 95 },
  { pressureHpa: 700, geopotentialHeightM: 3100, temperatureC: -13, dewPointC: -13.2, relativeHumidityWaterPct: 97, verticalVelocityMs: -0.08, windSpeedMs: 9, windDirectionDeg: 225, cloudCoverPct: 95 },
  { pressureHpa: 600, geopotentialHeightM: 4300, temperatureC: -16, dewPointC: -16.2, relativeHumidityWaterPct: 97, verticalVelocityMs: -0.09, windSpeedMs: 11, windDirectionDeg: 230, cloudCoverPct: 95 },
  { pressureHpa: 500, geopotentialHeightM: 5700, temperatureC: -21, dewPointC: -21.2, relativeHumidityWaterPct: 97, verticalVelocityMs: -0.06, windSpeedMs: 14, windDirectionDeg: 235, cloudCoverPct: 95 },
  { pressureHpa: 400, geopotentialHeightM: 7200, temperatureC: -28, dewPointC: -28.2, relativeHumidityWaterPct: 97, verticalVelocityMs: -0.03, windSpeedMs: 18, windDirectionDeg: 240, cloudCoverPct: 95 },
];

function cloneLayers(overrides: Partial<RawPressureLayer>[] = []): RawPressureLayer[] {
  return baseLayers.map((layer, index) => ({ ...layer, ...overrides[index] }));
}

type FixtureOverrides = Omit<Partial<SnowfallInput>, 'surface' | 'profile'> & {
  surface?: Partial<SurfaceMeteorology>;
  layers?: RawPressureLayer[];
};

export function makeFixture(overrides: FixtureOverrides = {}): SnowfallInput {
  const { layers, surface: surfaceOverrides, ...inputOverrides } = overrides;
  const surfacePressureHpa = surfaceOverrides?.surfacePressureHpa ?? 790;
  const stationElevationM = surfaceOverrides?.stationElevationM ?? 1800;
  return {
    time: '2026-01-15T12:00',
    precipitationMm: 5,
    snowfallCm: null,
    snowfallWaterEquivalentMm: null,
    precipitationType: null,
    weatherCode: 71,
    surface: {
      temperatureC: -3,
      dewPointC: -3.5,
      relativeHumidityPct: 95,
      wetBulbTemperatureC: -3.2,
      windSpeedMs: 4,
      windDirectionDeg: 210,
      surfacePressureHpa,
      stationElevationM,
      ...surfaceOverrides,
    },
    profile: normalizeProfile(layers ?? cloneLayers(), { surfacePressureHpa, stationElevationM }),
    ...inputOverrides,
  };
}

export const profileFixtures = {
  coldSaturatedDgzStrongLift: makeFixture(),
  coldSaturatedDgzNoLift: makeFixture({ layers: cloneLayers().map(layer => ({ ...layer, verticalVelocityMs: 0 })) }),
  strongLiftAboveDgz: makeFixture({ layers: cloneLayers([{ verticalVelocityMs: -0.01 }, {}, {}, { verticalVelocityMs: -0.14 }, { verticalVelocityMs: -0.12 }]) }),
  strongLiftBelowDgz: makeFixture({ layers: cloneLayers([{ verticalVelocityMs: -0.12 }, { verticalVelocityMs: -0.14 }, { verticalVelocityMs: -0.02 }]) }),
  completelyDryDgz: makeFixture({ layers: cloneLayers().map(layer => ({ ...layer, dewPointC: layer.temperatureC! - 12, relativeHumidityWaterPct: 35 })) }),
  elevatedWarmNose: makeFixture({ layers: cloneLayers([
    {}, {}, { temperatureC: 1, dewPointC: 0.7 }, { temperatureC: 2, dewPointC: 1.7 }, { temperatureC: -5, dewPointC: -5.2 },
  ]) }),
  warmSurfaceSubfreezingWetBulb: makeFixture({ surface: { temperatureC: 1, dewPointC: -2, relativeHumidityPct: 65, wetBulbTemperatureC: -0.3, windSpeedMs: 4, windDirectionDeg: 210, surfacePressureHpa: 790, stationElevationM: 1800 } }),
  allRain: makeFixture({
    surface: { temperatureC: 5, dewPointC: 4, relativeHumidityPct: 93, wetBulbTemperatureC: 4.4, windSpeedMs: 4, windDirectionDeg: 210, surfacePressureHpa: 790, stationElevationM: 1800 },
    layers: cloneLayers().map(layer => ({ ...layer, temperatureC: Math.max(2, layer.temperatureC! + 15), dewPointC: Math.max(1.5, layer.dewPointC! + 15) })),
    weatherCode: 63,
  }),
  mixedRainSnow: makeFixture({ snowfallWaterEquivalentMm: 2, weatherCode: 67 }),
  highElevationUndergroundLevels: makeFixture({
    surface: { temperatureC: -4, dewPointC: -4.3, relativeHumidityPct: 96, wetBulbTemperatureC: -4.1, windSpeedMs: 4, windDirectionDeg: 210, surfacePressureHpa: 620, stationElevationM: 3900 },
  }),
  missingPressureValues: makeFixture({ layers: cloneLayers([{}, {}, { dewPointC: null }, { verticalVelocityMs: null }]) }),
  extremeWind: makeFixture({ surface: { temperatureC: -3, dewPointC: -3.5, relativeHumidityPct: 95, wetBulbTemperatureC: -3.2, windSpeedMs: 35, windDirectionDeg: 210, surfacePressureHpa: 790, stationElevationM: 1800 } }),
};
