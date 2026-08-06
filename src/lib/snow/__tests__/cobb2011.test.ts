import { describe, expect, it } from 'vitest';
import { estimateCobb2011Slr } from '../slr/cobb2011';
import { estimateKucheraSlr } from '../slr/kuchera';
import { profileFixtures } from './fixtures';

describe('Cobb 2011 SLR', () => {
  it('weights saturated ascent without converting geometric vertical velocity', () => {
    const result = estimateCobb2011Slr(profileFixtures.coldSaturatedDgzStrongLift.profile);
    expect(result.freshSlr).not.toBeNull();
    expect(result.growthZoneLiftMs).toBeCloseTo(0.09);
    expect(result.layers.some(layer => layer.weight > 0)).toBe(true);
    expect(result.layers.reduce((sum, layer) => sum + layer.contribution, 0)).toBeCloseTo(result.freshSlr!, 5);
  });

  it('falls back when saturated ascent is unavailable', () => {
    const result = estimateCobb2011Slr(profileFixtures.coldSaturatedDgzNoLift.profile);
    expect(result.freshSlr).toBeNull();
    expect(result.fallbackReason).toBe('NO_SATURATED_ASCENT');
  });

  it('falls back when required dew point or vertical velocity is missing', () => {
    const result = estimateCobb2011Slr(profileFixtures.missingPressureValues.profile);
    expect(result.freshSlr).toBeNull();
    expect(result.fallbackReason).toMatch(/MISSING_(DEW_POINT_PROFILE|VERTICAL_VELOCITY)/);
  });

  it('keeps Kuchera available as a deterministic profile fallback', () => {
    expect(estimateKucheraSlr(profileFixtures.highElevationUndergroundLevels.profile, -4)).toBeGreaterThan(3);
  });
});
