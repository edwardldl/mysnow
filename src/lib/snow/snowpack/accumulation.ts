import { MIN_ACCUMULATING_SWE_MM } from '../types';
import type { SnowLayer, SnowfallResult } from '../types';

const WATER_DENSITY_KG_M3 = 1000;

export function accumulationLayerFromSnowfall(result: SnowfallResult, depositionTime: string): SnowLayer | null {
  if (result.frozenSweMm <= MIN_ACCUMULATING_SWE_MM || result.freshSlr === null || result.freshSlr <= 0) {
    return null;
  }
  const initialDensityKgM3 = WATER_DENSITY_KG_M3 / result.freshSlr;
  return {
    depositionTime,
    initialSweMm: result.frozenSweMm,
    currentSweMm: result.frozenSweMm,
    initialDensityKgM3,
    currentDensityKgM3: initialDensityKgM3,
    liquidWaterMm: 0,
    ageHours: 0,
    sourceMethod: result.method,
  };
}
