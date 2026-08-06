import type { SnowLayer } from '../types';

/** A conservative degree-hour melt placeholder with explicit SWE removal. */
export function meltSnowpack(layers: SnowLayer[], surfaceTemperatureC: number | null): { layers: SnowLayer[]; meltSweMm: number } {
  const meltDemandMm = surfaceTemperatureC !== null && surfaceTemperatureC > 1
    ? (surfaceTemperatureC - 1) * 1.5
    : 0;
  let remainingMeltMm = meltDemandMm;
  const nextLayers = layers.map(layer => ({ ...layer }));

  for (let index = nextLayers.length - 1; index >= 0 && remainingMeltMm > 0; index -= 1) {
    const layer = nextLayers[index];
    const meltFromLayer = Math.min(layer.currentSweMm, remainingMeltMm);
    layer.currentSweMm -= meltFromLayer;
    remainingMeltMm -= meltFromLayer;
  }

  return {
    layers: nextLayers.filter(layer => layer.currentSweMm > 0),
    meltSweMm: meltDemandMm - remainingMeltMm,
  };
}
