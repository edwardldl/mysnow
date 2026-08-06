import { describe, expect, it } from 'vitest';
import { relativeHumidityIceFromDewPoint, saturationVaporPressureIceHpa, saturationVaporPressureWaterHpa } from '../humidity';

describe('humidity calculations', () => {
  it('uses distinct saturation vapour-pressure curves over water and ice', () => {
    expect(saturationVaporPressureWaterHpa(-10)).toBeGreaterThan(saturationVaporPressureIceHpa(-10));
  });

  it('computes RH over ice from temperature and dew point', () => {
    const rhIce = relativeHumidityIceFromDewPoint(-15, -15.2);
    expect(rhIce).toBeGreaterThan(100);
    expect(rhIce).toBeLessThan(120);
  });
});
