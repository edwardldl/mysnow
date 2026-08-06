import { relativeHumidityIceFromDewPoint } from './humidity';
import type { NormalizedProfile, PressureLayer } from './types';

export const VERTICAL_VELOCITY_CONVENTION = 'positive_up' as const;

const PRESSURE_TOLERANCE_HPA = 1;
const ELEVATION_TOLERANCE_M = 100;

export function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** Convert a geometric vertical velocity into a positive-up ascent value. */
export function upwardVelocity(
  value: number | null,
  convention: 'positive_up' | 'positive_down' = VERTICAL_VELOCITY_CONVENTION,
): number {
  if (value === null) return 0;
  return convention === 'positive_up' ? Math.max(value, 0) : Math.max(-value, 0);
}

function within(value: number | null, min: number, max: number): number | null {
  return value !== null && value >= min && value <= max ? value : null;
}

export interface RawPressureLayer {
  pressureHpa: number;
  geopotentialHeightM?: number | null;
  temperatureC?: number | null;
  dewPointC?: number | null;
  relativeHumidityWaterPct?: number | null;
  verticalVelocityMs?: number | null;
  windSpeedMs?: number | null;
  windDirectionDeg?: number | null;
  cloudCoverPct?: number | null;
}

export interface ProfileNormalizationOptions {
  surfacePressureHpa: number | null;
  stationElevationM: number | null;
  elevationToleranceM?: number;
}

/**
 * Normalizes raw model levels once. Missing and implausible observations stay
 * null; they are never turned into physically meaningful zeroes.
 */
export function normalizeProfile(
  rawLevels: RawPressureLayer[],
  { surfacePressureHpa, stationElevationM, elevationToleranceM = ELEVATION_TOLERANCE_M }: ProfileNormalizationOptions,
): NormalizedProfile {
  const validSurfacePressure = within(surfacePressureHpa, 300, 1100);
  const validElevation = stationElevationM !== null && Number.isFinite(stationElevationM)
    ? stationElevationM
    : null;

  const levels = rawLevels
    .filter(layer => Number.isFinite(layer.pressureHpa) && layer.pressureHpa > 0)
    .map((layer): PressureLayer => {
      const pressureHpa = layer.pressureHpa;
      const geopotentialHeightM = within(finiteNumber(layer.geopotentialHeightM), -500, 30000);
      const temperatureC = within(finiteNumber(layer.temperatureC), -100, 50);
      const dewPointC = within(finiteNumber(layer.dewPointC), -120, 50);
      const relativeHumidityWaterPct = within(finiteNumber(layer.relativeHumidityWaterPct), 0, 150);
      const verticalVelocityMs = within(finiteNumber(layer.verticalVelocityMs), -50, 50);
      const windSpeedMs = within(finiteNumber(layer.windSpeedMs), 0, 150);
      const windDirectionRaw = finiteNumber(layer.windDirectionDeg);
      const windDirectionDeg = windDirectionRaw !== null && windDirectionRaw >= 0 && windDirectionRaw <= 360
        ? windDirectionRaw
        : null;
      const cloudCoverPct = within(finiteNumber(layer.cloudCoverPct), 0, 100);
      const pressureAboveGround = validSurfacePressure === null
        ? true
        : pressureHpa <= validSurfacePressure + PRESSURE_TOLERANCE_HPA;
      const heightAboveGround = geopotentialHeightM !== null && (validElevation === null
        || geopotentialHeightM >= validElevation - elevationToleranceM);

      return {
        pressureHpa,
        geopotentialHeightM,
        temperatureC,
        dewPointC,
        relativeHumidityWaterPct,
        relativeHumidityIcePct: temperatureC === null || dewPointC === null
          ? null
          : relativeHumidityIceFromDewPoint(temperatureC, dewPointC),
        verticalVelocityMs,
        windSpeedMs,
        windDirectionDeg,
        cloudCoverPct,
        isAboveGround: pressureAboveGround && heightAboveGround,
      };
    })
    .sort((a, b) => b.pressureHpa - a.pressureHpa);

  const aboveGroundLevels = levels.filter(level => level.isAboveGround);
  return {
    levels,
    aboveGroundLevels,
    validLevelCount: aboveGroundLevels.length,
    surfacePressureHpa: validSurfacePressure,
    stationElevationM: validElevation,
  };
}
