import { estimatePrecipitationPhase } from './phase';
import { estimateFreshSnowSlr } from './slr';
import type { QpfAdjustmentResult, QpfDistribution, Quantiles, SlrMethod, SnowfallInput, SnowfallResult } from './types';

export function adjustPrecipitation(rawPrecipitationMm: number): QpfAdjustmentResult {
  return {
    rawPrecipitationMm,
    adjustedPrecipitationMm: rawPrecipitationMm,
    multiplier: 1,
    method: 'none',
    confidence: 1,
  };
}

function freshSlrDistribution(
  freshSlr: number | null,
  method: SlrMethod,
  usedFallback: boolean,
): Quantiles | null {
  if (freshSlr === null) return null;
  const baseSpread = method === 'fixed_10' ? 1.5 : method === 'cobb_2011' ? 2.5 : 3;
  const spread = baseSpread + (usedFallback ? 1 : 0);
  return {
    p10: Math.max(3, freshSlr - spread),
    p50: freshSlr,
    p90: Math.min(30, freshSlr + spread),
  };
}

function freshSnowDistribution(
  frozenSweMm: number,
  slr: Quantiles | null,
  phaseConfidence: number,
): Quantiles {
  if (frozenSweMm <= 0 || slr === null) return { p10: 0, p50: 0, p90: 0 };
  // This is an explicitly uncalibrated physics envelope. Ensemble-member
  // quantiles replace it whenever an ensemble is available. Combine component
  // spread as one scenario envelope; never multiply independent P90 inputs.
  const median = frozenSweMm * slr.p50 / 10;
  const slrRelativeSpread = (slr.p90 - slr.p10) / Math.max(2 * slr.p50, 1);
  const phaseRelativeSpread = (1 - phaseConfidence) * 0.5;
  const qpfRelativeSpread = 0.3;
  const combinedSpread = Math.min(0.75, Math.hypot(slrRelativeSpread, phaseRelativeSpread, qpfRelativeSpread));
  return {
    p10: median * (1 - combinedSpread),
    p50: median,
    p90: median * (1 + combinedSpread * 1.2),
  };
}

function qpfDistribution(input: SnowfallInput, precipitationMm: number): QpfDistribution {
  const probability = input.precipitationProbabilityPct !== null
    && input.precipitationProbabilityPct !== undefined
    && Number.isFinite(input.precipitationProbabilityPct)
    ? Math.max(0, Math.min(1, input.precipitationProbabilityPct / 100))
    : precipitationMm > 0.1 ? 0.75 : 0.15;
  return {
    probabilityWet: probability,
    amountMm: {
      p10: precipitationMm * 0.6,
      p50: precipitationMm,
      p90: precipitationMm * 1.5,
    },
    method: 'raw_model_envelope',
    confidence: input.precipitationProbabilityPct == null ? 0.35 : 0.55,
  };
}

/** Single source of truth for precipitation → phase → SLR → fresh snow. */
export function calculateSnowfall(input: SnowfallInput, method: SlrMethod): SnowfallResult {
  const rawPrecipitationMm = Math.max(0, input.precipitationMm ?? 0);
  const qpfAdjustment = adjustPrecipitation(rawPrecipitationMm);
  const normalizedInput: SnowfallInput = {
    ...input,
    precipitationMm: qpfAdjustment.adjustedPrecipitationMm,
  };
  const phase = estimatePrecipitationPhase(normalizedInput);
  const frozenSweMm = qpfAdjustment.adjustedPrecipitationMm * phase.snowFraction;
  const rainMm = qpfAdjustment.adjustedPrecipitationMm - frozenSweMm;
  const slrEstimate = estimateFreshSnowSlr(
    normalizedInput,
    method,
    phase.diagnostics.warmNoseEnergyCM,
  );
  const isOpenMeteoComparison = method === 'open_meteo_snowfall';
  const freshSlr = !isOpenMeteoComparison && frozenSweMm > 0 ? slrEstimate.freshSlr : null;
  const freshSnowCm = isOpenMeteoComparison
    ? Math.max(0, input.snowfallCm ?? 0)
    : freshSlr === null ? 0 : (frozenSweMm * freshSlr) / 10;
  const freshSlrQuantiles = freshSlrDistribution(
    freshSlr,
    method,
    slrEstimate.diagnostics.fallbackReason !== null,
  );
  const freshSnowQuantilesCm = isOpenMeteoComparison
    ? { p10: freshSnowCm * 0.75, p50: freshSnowCm, p90: freshSnowCm * 1.35 }
    : freshSnowDistribution(frozenSweMm, freshSlrQuantiles, phase.confidence);

  return {
    precipitationMm: rawPrecipitationMm,
    snowFraction: phase.snowFraction,
    frozenSweMm,
    freshSlr,
    freshSlrQuantiles,
    freshSnowCm,
    freshSnowQuantilesCm,
    rainMm,
    phase,
    method,
    diagnostics: {
      ...slrEstimate.diagnostics,
      warnings: [...new Set([...slrEstimate.diagnostics.warnings, ...phase.warnings])],
    },
    qpfAdjustment,
    qpfDistribution: qpfDistribution(input, rawPrecipitationMm),
  };
}
