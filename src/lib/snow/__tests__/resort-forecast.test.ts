import { describe, expect, it } from 'vitest';
import { getLocations } from '../../api';
import type { BlendedHour, DayData } from '../../types';
import { buildResortSnowForecasts } from '../resort/forecast';
import { resortReferencePoints } from '../resort/geometry';
import { calculateSnowfall } from '../snowfall';
import { profileFixtures } from './fixtures';

function referenceDay(precipitationMm: number, temperature: number): DayData {
  const snowfallResult = calculateSnowfall({
    ...profileFixtures.coldSaturatedDgzStrongLift,
    precipitationMm,
    surface: { ...profileFixtures.coldSaturatedDgzStrongLift.surface, temperatureC: temperature },
  }, 'cobb_2011');
  const hour = {
    time: '2026-01-15T12:00',
    dateObj: new Date('2026-01-15T12:00:00Z'),
    model: 'HRRR',
    precipitation: precipitationMm,
    snowfall: snowfallResult.freshSnowCm,
    frozenSweMm: snowfallResult.frozenSweMm,
    snowfallResult,
    temperature,
    windSpeed: 14,
    windDir: 225,
  } as BlendedHour;
  return {
    dateStr: '2026-01-15',
    dateObj: hour.dateObj,
    totalSnowfall: hour.snowfall,
    totalPrecipitation: precipitationMm,
    hourly: [hour],
  } as DayData;
}

describe('resort forecast assembly', () => {
  it('keeps reference, natural-slope, opening, and managed products distinct', () => {
    const location = getLocations().palisades;
    const points = resortReferencePoints(location);
    const output = buildResortSnowForecasts(location, [
      { point: points[0], days: [referenceDay(4, -1)] },
      { point: points[1], days: [referenceDay(7, -4)] },
      { point: points[2], days: [referenceDay(10, -7)] },
    ])['2026-01-15'];
    expect(output.referencePoints).toHaveLength(3);
    expect(output.resortSummary.freshSnowCm.p50).toBeGreaterThan(0);
    expect(output.resortSummary.openingSnowCm.p50).toBeLessThan(output.resortSummary.freshSnowCm.p50);
    expect(output.slopeUnits.length).toBeGreaterThan(0);
    expect(output.management.available).toBe(false);
    expect(output.diagnostics.productMode).toBe('physical_pipeline');

    const baselineMass = output.slopeUnits.reduce(
      (sum, unit) => sum + unit.unit.areaM2 * unit.baselineFrozenSweMm,
      0,
    );
    const depositedMass = output.slopeUnits.reduce(
      (sum, unit) => sum + unit.unit.areaM2 * unit.depositedFrozenSweMm,
      0,
    );
    expect(depositedMass).toBeCloseTo(baselineMass, 6);
  });
});
