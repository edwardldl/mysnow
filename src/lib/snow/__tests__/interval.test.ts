import { describe, expect, it } from 'vitest';
import { intervalDirectionMean, intervalRepresentativeLayers } from '../atmosphere/interval';

describe('precipitation interval alignment', () => {
  it('averages instantaneous profile values at both ends of (t-1,t]', () => {
    const layers = intervalRepresentativeLayers(
      [{ pressureHpa: 700, temperatureC: -10, verticalVelocityMs: -0.02, windDirectionDeg: 350 }],
      [{ pressureHpa: 700, temperatureC: -14, verticalVelocityMs: -0.08, windDirectionDeg: 10 }],
    );
    expect(layers[0].temperatureC).toBe(-12);
    expect(layers[0].verticalVelocityMs).toBeCloseTo(-0.05);
    expect(layers[0].windDirectionDeg).toBeCloseTo(0);
  });

  it('uses circular rather than arithmetic wind-direction averaging', () => {
    expect(intervalDirectionMean(350, 10)).toBeCloseTo(0);
  });
});
