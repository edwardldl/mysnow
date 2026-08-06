import type { DriftAssessment, WindExposureClass } from '../resort/types';

export interface DriftInput {
  windSpeedMs: number | null;
  windFromDeg: number | null;
  exposure: WindExposureClass;
  freshSnowDensityKgM3: number;
  directionSpreadDeg?: number;
}

/** MVP categorical transport guidance; it deliberately does not invent depth. */
export function assessDrift(input: DriftInput): DriftAssessment {
  const windSpeedMs = Math.max(0, input.windSpeedMs ?? 0);
  const loadingDirectionDeg = input.windFromDeg === null
    ? null
    : (input.windFromDeg + 180) % 360;
  const erodible = input.freshSnowDensityKgM3 < 180;
  if (windSpeedMs < 6 || !erodible) {
    return { category: 'drift_unlikely', loadingDirectionDeg, confidence: 0.7 };
  }
  if ((input.directionSpreadDeg ?? 0) > 90 || windSpeedMs >= 18) {
    return { category: 'high_redistribution_uncertainty', loadingDirectionDeg, confidence: 0.45 };
  }
  if (input.exposure === 'exposed') {
    return { category: 'exposed_depletion_likely', loadingDirectionDeg, confidence: 0.7 };
  }
  if (input.exposure === 'sheltered') {
    return { category: 'significant_lee_loading', loadingDirectionDeg, confidence: 0.65 };
  }
  return { category: 'high_redistribution_uncertainty', loadingDirectionDeg, confidence: 0.5 };
}
