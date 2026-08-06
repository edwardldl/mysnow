import type { NaturalSnowState } from '../types';

export interface OpeningEvolutionInput {
  frozenSweMm: number;
  freshSlr: number;
  elapsedHours: number;
  meanTemperatureC: number;
  meanWindSpeedMs: number;
  exposed: boolean;
}

export interface OpeningEvolutionResult {
  state: NaturalSnowState;
  freshDepthCm: number;
  settlementCm: number;
  windCompactionOrLossCm: number;
  meltSweMm: number;
  meltDepthCm: number;
}

/** Evolve new snow only; old-pack depth is intentionally outside this product. */
export function evolveToOpening(input: OpeningEvolutionInput): OpeningEvolutionResult {
  const freshDensityKgM3 = 1000 / Math.max(3, input.freshSlr);
  const freshDepthCm = Math.max(0, input.frozenSweMm) * input.freshSlr / 10;
  const elapsedHours = Math.max(0, input.elapsedHours);
  const targetDensityKgM3 = Math.min(450, 180 + Math.max(input.meanTemperatureC, -10) * 8 + Math.max(0, input.frozenSweMm - 20));
  const tauHours = input.meanTemperatureC >= 0 ? 10 : 20;
  const relaxedDensity = freshDensityKgM3
    + Math.max(0, targetDensityKgM3 - freshDensityKgM3) * (1 - Math.exp(-elapsedHours / tauHours));
  const windCompactionIndex = input.exposed
    ? Math.max(0, input.meanWindSpeedMs - 5) * elapsedHours / 100
    : 0;
  const compactedDensity = Math.min(550, relaxedDensity * (1 + Math.min(0.35, windCompactionIndex * 0.08)));
  const meltDemandMm = Math.max(0, input.meanTemperatureC - 1) * elapsedHours * 0.08;
  const meltSweMm = Math.min(Math.max(0, input.frozenSweMm), meltDemandMm);
  const windLossFraction = input.exposed
    ? Math.min(0.18, Math.max(0, input.meanWindSpeedMs - 10) * elapsedHours * 0.0008)
    : 0;
  const sweAfterMelt = Math.max(0, input.frozenSweMm - meltSweMm);
  const sweMm = sweAfterMelt * (1 - windLossFraction);
  const depthCm = compactedDensity > 0 ? 100 * sweMm / compactedDensity : 0;
  const relaxedDepthCm = 100 * Math.max(0, input.frozenSweMm) / Math.max(relaxedDensity, 1);
  const compactedDepthBeforeMeltCm = 100 * Math.max(0, input.frozenSweMm) / Math.max(compactedDensity, 1);
  const compactedDepthAfterMeltCm = 100 * sweAfterMelt / Math.max(compactedDensity, 1);
  const settlementCm = Math.max(0, freshDepthCm - relaxedDepthCm);
  const windCompactionOrLossCm = Math.max(0, relaxedDepthCm - compactedDepthBeforeMeltCm)
    + Math.max(0, compactedDepthAfterMeltCm - depthCm);
  const meltDepthCm = Math.max(0, compactedDepthBeforeMeltCm - compactedDepthAfterMeltCm);

  return {
    freshDepthCm,
    settlementCm,
    windCompactionOrLossCm,
    meltSweMm,
    meltDepthCm,
    state: {
      sweMm,
      depthCm,
      densityKgM3: compactedDensity,
      liquidWaterMm: 0,
      surfaceTemperatureC: input.meanTemperatureC,
      ageHours: elapsedHours,
      windCompactionIndex,
      crustProbability: input.meanTemperatureC > 0 && elapsedHours >= 6 ? Math.min(1, elapsedHours / 24) : 0,
    },
  };
}
