import type { Quantiles } from '../types';
import type { SlopeReferenceUnit } from '../resort/types';
import { interpolateWindShelter, leewardAlignment } from './windShelter';

export interface TerrainBaseline {
  unit: SlopeReferenceUnit;
  frozenSweMm: number;
  freshSlr: number;
}

export interface TerrainDeposition {
  unit: SlopeReferenceUnit;
  baselineFrozenSweMm: number;
  depositedFrozenSweMm: number;
  depositionFactor: number;
  freshSnowCm: Quantiles;
}

export interface DepositionCoefficients {
  shelter: number;
  topographicPosition: number;
  curvature: number;
  leewardAspect: number;
  slope: number;
  canopy: number;
}

export const MVP_DEPOSITION_COEFFICIENTS: Readonly<DepositionCoefficients> = {
  shelter: 0.28,
  topographicPosition: -0.12,
  curvature: 0.08,
  leewardAspect: 0.16,
  slope: -0.04,
  canopy: -0.1,
};

function potential(
  baseline: TerrainBaseline,
  windFromDeg: number | null,
  coefficients: DepositionCoefficients,
): number {
  const { unit } = baseline;
  const shelter = interpolateWindShelter(unit.windShelterByDirection, windFromDeg);
  const slopeTerm = Math.max(-1, Math.min(1, (unit.meanSlopeDeg - 20) / 20));
  const exponent = coefficients.shelter * shelter
    + coefficients.topographicPosition * unit.topographicPosition
    + coefficients.curvature * unit.curvature
    + coefficients.leewardAspect * leewardAlignment(unit.meanAspectDeg, windFromDeg)
    + coefficients.slope * slopeTerm
    + coefficients.canopy * unit.canopyFraction;
  return Math.exp(exponent);
}

/**
 * Apply directional preferential deposition while exactly conserving
 * area-integrated frozen SWE across the supplied resort domain.
 */
export function distributeTerrainSnow(
  baselines: TerrainBaseline[],
  windFromDeg: number | null,
  coefficients: DepositionCoefficients = MVP_DEPOSITION_COEFFICIENTS,
): TerrainDeposition[] {
  if (baselines.length === 0) return [];
  const potentials = baselines.map(baseline => potential(baseline, windFromDeg, coefficients));
  const baselineMass = baselines.reduce(
    (sum, baseline) => sum + baseline.unit.areaM2 * Math.max(0, baseline.frozenSweMm),
    0,
  );
  const potentialMass = baselines.reduce(
    (sum, baseline, index) => sum + baseline.unit.areaM2 * Math.max(0, baseline.frozenSweMm) * potentials[index],
    0,
  );
  const normalization = potentialMass > 0 ? baselineMass / potentialMass : 1;

  return baselines.map((baseline, index) => {
    const depositionFactor = potentials[index] * normalization;
    const depositedFrozenSweMm = Math.max(0, baseline.frozenSweMm) * depositionFactor;
    const p50 = depositedFrozenSweMm * baseline.freshSlr / 10;
    return {
      unit: baseline.unit,
      baselineFrozenSweMm: Math.max(0, baseline.frozenSweMm),
      depositedFrozenSweMm,
      depositionFactor,
      freshSnowCm: {
        p10: p50 * 0.72,
        p50,
        p90: p50 * 1.35,
      },
    };
  });
}
