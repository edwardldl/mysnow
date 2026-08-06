import { describe, expect, it } from 'vitest';
import { distributeTerrainSnow, type DepositionCoefficients } from '../terrain/deposition';
import { interpolateWindShelter } from '../terrain/windShelter';
import type { SlopeReferenceUnit } from '../resort/types';

function unit(id: string, areaM2: number, shelter: number): SlopeReferenceUnit {
  return {
    id,
    areaM2,
    centroidLat: 39,
    centroidLon: -120,
    minElevationM: 2000,
    meanElevationM: 2050,
    maxElevationM: 2100,
    meanSlopeDeg: 20,
    meanAspectDeg: id === 'lee' ? 180 : 0,
    topographicPosition: 0,
    windShelterByDirection: Array(24).fill(shelter),
    curvature: 0,
    canopyFraction: 0,
    exposure: shelter > 0 ? 'sheltered' : shelter < 0 ? 'exposed' : 'neutral',
    management: 'natural',
    terrainSource: 'mvp_reference_classes',
  };
}

describe('terrain deposition', () => {
  it('conserves area-integrated frozen SWE', () => {
    const baselines = [
      { unit: unit('lee', 1000, 0.8), frozenSweMm: 20, freshSlr: 15 },
      { unit: unit('windward', 2000, -0.7), frozenSweMm: 10, freshSlr: 12 },
    ];
    const result = distributeTerrainSnow(baselines, 0);
    const before = baselines.reduce((sum, item) => sum + item.unit.areaM2 * item.frozenSweMm, 0);
    const after = result.reduce((sum, item) => sum + item.unit.areaM2 * item.depositedFrozenSweMm, 0);
    expect(after).toBeCloseTo(before, 8);
    expect(result[0].depositionFactor).not.toBe(result[1].depositionFactor);
  });

  it('reproduces baseline snowfall exactly for a neutral configuration', () => {
    const zero: DepositionCoefficients = {
      shelter: 0,
      topographicPosition: 0,
      curvature: 0,
      leewardAspect: 0,
      slope: 0,
      canopy: 0,
    };
    const result = distributeTerrainSnow([
      { unit: unit('one', 1000, 0), frozenSweMm: 12, freshSlr: 10 },
      { unit: unit('two', 1000, 0), frozenSweMm: 18, freshSlr: 10 },
    ], 270, zero);
    expect(result.map(item => item.depositionFactor)).toEqual([1, 1]);
    expect(result.map(item => item.depositedFrozenSweMm)).toEqual([12, 18]);
  });

  it('interpolates directional shelter across north without a discontinuity', () => {
    const values = Array.from({ length: 24 }, (_, index) => index === 0 ? 1 : index === 23 ? 0.5 : 0);
    expect(interpolateWindShelter(values, 352.5)).toBeCloseTo(0.75);
    expect(interpolateWindShelter(values, 0)).toBe(1);
  });
});
