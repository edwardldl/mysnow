import { accumulationLayerFromSnowfall } from './accumulation';
import { meltSnowpack } from './melt';
import { settleSnowpack } from './settling';
import type { SnowLayer, SnowpackStep, SnowfallResult } from '../types';

const WATER_DENSITY_KG_M3 = 1000;

export function snowpackDepthCm(layers: SnowLayer[]): number {
  return layers.reduce(
    (depthCm, layer) => depthCm + (layer.currentSweMm / 10) * (WATER_DENSITY_KG_M3 / layer.currentDensityKgM3),
    0,
  );
}

/** Apply one forecast hour while conserving SWE except for the explicit melt. */
export function advanceSnowpack(
  existingLayers: SnowLayer[],
  snowfall: SnowfallResult,
  time: string,
  surfaceTemperatureC: number | null,
): SnowpackStep {
  const settledLayers = settleSnowpack(existingLayers, surfaceTemperatureC);
  const accumulated = accumulationLayerFromSnowfall(snowfall, time);
  const withNewLayer = accumulated === null ? settledLayers : [...settledLayers, accumulated];
  const melted = meltSnowpack(withNewLayer, surfaceTemperatureC);
  return {
    layers: melted.layers,
    depthCm: snowpackDepthCm(melted.layers),
    newSnowCm: snowfall.freshSnowCm,
    addedSweMm: accumulated?.currentSweMm ?? 0,
    meltSweMm: melted.meltSweMm,
  };
}
