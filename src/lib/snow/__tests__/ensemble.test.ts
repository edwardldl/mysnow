import { describe, expect, it } from 'vitest';
import { calculateEnsembleSnowfall } from '../ensemble';
import { profileFixtures } from './fixtures';

describe('ensemble snowfall', () => {
  it('calculates quantiles from independently processed members', () => {
    const cold = profileFixtures.coldSaturatedDgzStrongLift;
    const rain = profileFixtures.allRain;
    const result = calculateEnsembleSnowfall([cold, rain, null], 'fixed_10');
    expect(result.memberResults).toHaveLength(2);
    expect(result.missingMemberCount).toBe(1);
    expect(result.p10SnowCm).toBeLessThan(result.p90SnowCm);
    expect(result.probabilitySnow).toBeCloseTo(0.5);
  });
});
