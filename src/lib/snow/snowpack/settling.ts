import type { SnowLayer } from '../types';

/**
 * Mechanical settling changes density, never SWE. Overburden is represented by
 * the mass above each layer and is deliberately bounded for numerical safety.
 */
export function settleSnowpack(layers: SnowLayer[], surfaceTemperatureC: number | null): SnowLayer[] {
  let sweAboveMm = 0;
  return [...layers].reverse().map(layer => {
    const temperatureFactor = surfaceTemperatureC === null
      ? 0.5
      : Math.max(0.1, 1 - Math.min(Math.abs(surfaceTemperatureC), 10) / 10);
    const settlingRate = 0.005 * temperatureFactor + 0.01 * (sweAboveMm / 1000);
    const currentDensityKgM3 = Math.min(600, layer.currentDensityKgM3 * (1 + settlingRate));
    sweAboveMm += layer.currentSweMm;
    return {
      ...layer,
      ageHours: layer.ageHours + 1,
      currentDensityKgM3,
    };
  }).reverse();
}
