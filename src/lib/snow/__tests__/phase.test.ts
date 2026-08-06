import { describe, expect, it } from 'vitest';
import { estimatePrecipitationPhase } from '../phase';
import { profileFixtures } from './fixtures';

describe('precipitation phase', () => {
  it('prefers model snowfall water equivalent and keeps mixed phase fractional', () => {
    const result = estimatePrecipitationPhase(profileFixtures.mixedRainSnow);
    expect(result.source).toBe('snowfall_water_equivalent');
    expect(result.snowFraction).toBeCloseTo(0.4);
    expect(result.rainFraction).toBeCloseTo(0.6);
  });

  it('recognizes a completely frozen wet-bulb profile', () => {
    const result = estimatePrecipitationPhase(profileFixtures.coldSaturatedDgzStrongLift);
    expect(result.source).toBe('wet_bulb_profile');
    expect(result.snowFraction).toBe(1);
  });

  it('returns little frozen precipitation for an all-rain profile', () => {
    const result = estimatePrecipitationPhase(profileFixtures.allRain);
    expect(result.snowFraction).toBeLessThan(0.1);
  });

  it('does not let a rain weather code silently erase a physically frozen phase', () => {
    const input = { ...profileFixtures.coldSaturatedDgzStrongLift, weatherCode: 63 };
    const result = estimatePrecipitationPhase(input);
    expect(result.snowFraction).toBe(1);
    expect(result.warnings).toContain('PHASE_DISAGREEMENT');
  });
});
