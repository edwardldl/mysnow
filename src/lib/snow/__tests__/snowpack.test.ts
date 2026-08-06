import { describe, expect, it } from 'vitest';
import { advanceSnowpack } from '../snowpack';
import { calculateSnowfall } from '../snowfall';
import { profileFixtures } from './fixtures';

describe('snowpack mass conservation', () => {
  it('adds the same frozen SWE that snowfall reports and preserves it without melt', () => {
    const snowfall = calculateSnowfall(profileFixtures.mixedRainSnow, 'fixed_10');
    const first = advanceSnowpack([], snowfall, snowfall.phase.source, -5);
    const second = advanceSnowpack(first.layers, calculateSnowfall({ ...profileFixtures.mixedRainSnow, precipitationMm: 0 }, 'fixed_10'), 'next-hour', -5);
    const firstSwe = first.layers.reduce((sum, layer) => sum + layer.currentSweMm, 0);
    const secondSwe = second.layers.reduce((sum, layer) => sum + layer.currentSweMm, 0);
    expect(firstSwe).toBeCloseTo(snowfall.frozenSweMm);
    expect(secondSwe).toBeCloseTo(firstSwe);
  });

  it('removes SWE only through explicit melt', () => {
    const snowfall = calculateSnowfall(profileFixtures.coldSaturatedDgzStrongLift, 'fixed_10');
    const initial = advanceSnowpack([], snowfall, 'hour-one', -4);
    const melted = advanceSnowpack(initial.layers, calculateSnowfall({ ...profileFixtures.coldSaturatedDgzStrongLift, precipitationMm: 0 }, 'fixed_10'), 'hour-two', 3);
    expect(melted.meltSweMm).toBeGreaterThan(0);
    expect(melted.layers.reduce((sum, layer) => sum + layer.currentSweMm, 0)).toBeCloseTo(
      snowfall.frozenSweMm - melted.meltSweMm,
    );
  });
});
