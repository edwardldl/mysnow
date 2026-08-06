import { estimatePrecipitationPhase } from './phase';
import { estimateFreshSnowSlr } from './slr';
import type { QpfAdjustmentResult, SlrMethod, SnowfallInput, SnowfallResult } from './types';

export function adjustPrecipitation(rawPrecipitationMm: number): QpfAdjustmentResult {
  return {
    rawPrecipitationMm,
    adjustedPrecipitationMm: rawPrecipitationMm,
    multiplier: 1,
    method: 'none',
    confidence: 1,
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
    frozenSweMm,
    phase.diagnostics.warmNoseEnergyCM,
  );
  const freshSlr = frozenSweMm > 0 ? slrEstimate.freshSlr : null;
  const freshSnowCm = freshSlr === null ? 0 : (frozenSweMm * freshSlr) / 10;

  return {
    precipitationMm: rawPrecipitationMm,
    snowFraction: phase.snowFraction,
    frozenSweMm,
    freshSlr,
    freshSnowCm,
    rainMm,
    phase,
    method,
    diagnostics: {
      ...slrEstimate.diagnostics,
      warnings: [...new Set([...slrEstimate.diagnostics.warnings, ...phase.warnings])],
    },
    qpfAdjustment,
  };
}
