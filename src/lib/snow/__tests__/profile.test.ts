import { describe, expect, it } from 'vitest';
import { normalizeProfile, upwardVelocity } from '../profile';
import { profileFixtures } from './fixtures';

describe('profile normalization', () => {
  it('preserves missing profile values as null and excludes underground levels', () => {
    const profile = profileFixtures.missingPressureValues.profile;
    expect(profile.levels.some(level => level.dewPointC === null)).toBe(true);
    expect(profile.levels.some(level => level.verticalVelocityMs === null)).toBe(true);

    const highElevationProfile = profileFixtures.highElevationUndergroundLevels.profile;
    expect(highElevationProfile.aboveGroundLevels.every(level => level.pressureHpa <= 620)).toBe(true);
    expect(highElevationProfile.aboveGroundLevels.every(level => level.geopotentialHeightM! >= 3800)).toBe(true);
  });

  it('uses one explicit geometric-velocity sign convention', () => {
    expect(upwardVelocity(0.08, 'positive_up')).toBe(0.08);
    expect(upwardVelocity(-0.08, 'positive_up')).toBe(0);
    expect(upwardVelocity(-0.08, 'positive_down')).toBe(0.08);
  });

  it('rejects implausible values instead of recasting them as zero', () => {
    const profile = normalizeProfile([{
      pressureHpa: 700,
      temperatureC: 120,
      dewPointC: -20,
      relativeHumidityWaterPct: 250,
      geopotentialHeightM: 3000,
      verticalVelocityMs: 0,
    }], { surfacePressureHpa: 800, stationElevationM: 1000 });
    expect(profile.levels[0].temperatureC).toBeNull();
    expect(profile.levels[0].relativeHumidityWaterPct).toBeNull();
    expect(profile.levels[0].verticalVelocityMs).toBe(0);
  });
});
