import { describe, expect, it } from 'vitest';
import { calculateSnowfall } from '../snowfall';
import { profileFixtures } from './fixtures';

describe('snowfall calculation', () => {
  it('uses frozen SWE consistently in the fresh snowfall equation', () => {
    const result = calculateSnowfall(profileFixtures.mixedRainSnow, 'fixed_10');
    expect(result.frozenSweMm).toBeCloseTo(2);
    expect(result.freshSnowCm).toBeCloseTo(2);
    expect(result.rainMm).toBeCloseTo(3);
  });

  it('does not change QPF when the SLR method changes', () => {
    const kuchera = calculateSnowfall(profileFixtures.coldSaturatedDgzStrongLift, 'kuchera');
    const cobb = calculateSnowfall(profileFixtures.coldSaturatedDgzStrongLift, 'cobb_2011');
    expect(kuchera.qpfAdjustment.multiplier).toBe(1);
    expect(cobb.qpfAdjustment.multiplier).toBe(1);
    expect(kuchera.precipitationMm).toBe(cobb.precipitationMm);
  });

  it('reports an explicit Kuchera fallback when Cobb cannot use its profile', () => {
    const result = calculateSnowfall(profileFixtures.coldSaturatedDgzNoLift, 'cobb_2011');
    expect(result.freshSlr).not.toBeNull();
    expect(result.diagnostics.warnings).toContain('KUCHERA_FALLBACK');
  });
});
