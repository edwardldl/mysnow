import { describe, expect, it } from 'vitest';
import { calculateMoistureTransport } from '../atmosphere/moistureTransport';
import { profileFixtures } from './fixtures';

describe('mountain moisture transport', () => {
  it('calculates IVT and only positive flow toward the ridge normal', () => {
    const towardNortheast = calculateMoistureTransport(profileFixtures.coldSaturatedDgzStrongLift.profile, 45);
    const opposite = calculateMoistureTransport(profileFixtures.coldSaturatedDgzStrongLift.profile, 225);
    expect(towardNortheast.validLayerCount).toBeGreaterThan(1);
    expect(towardNortheast.ivtMagnitudeKgM1S1).toBeGreaterThan(0);
    expect(towardNortheast.upslopeFluxKgM1S1).toBeGreaterThanOrEqual(0);
    expect(opposite.upslopeFluxKgM1S1).not.toBe(towardNortheast.upslopeFluxKgM1S1);
  });
});
