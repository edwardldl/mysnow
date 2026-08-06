import type { NormalizedProfile } from '../types';

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

/** Vanilla Kuchera relationship on maximum temperature in the valid column. */
export function estimateKucheraSlr(profile: NormalizedProfile, surfaceTemperatureC: number | null): number {
  const temperatures = profile.aboveGroundLevels
    .map(level => level.temperatureC)
    .filter((temperature): temperature is number => temperature !== null);
  const maximumTemperatureC = temperatures.length > 0
    ? Math.max(...temperatures)
    : surfaceTemperatureC;

  if (maximumTemperatureC === null) return 10;

  const maxTemperatureK = maximumTemperatureC + 273.15;
  const pivotK = 271.16;
  const slr = maxTemperatureK > pivotK
    ? 12 + 2 * (pivotK - maxTemperatureK)
    : 12 + (pivotK - maxTemperatureK);
  return Math.round(clamp(slr, 3, 30) * 10) / 10;
}
