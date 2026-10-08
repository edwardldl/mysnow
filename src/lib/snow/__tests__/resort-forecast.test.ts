import { describe, expect, it } from 'vitest';
import { getLocations } from '../../api';
import type { BlendedHour, DayData } from '../../types';
import { buildResortSnowForecasts } from '../resort/forecast';
import { resortReferencePoints } from '../resort/geometry';
import { calculateSnowfall } from '../snowfall';
import { providerLocalTimeToEpochMs } from '../../time';
import { profileFixtures } from './fixtures';

function referenceDay(
  precipitationMm: number,
  temperature: number,
  hourOfDay = 12,
  timezone = 'America/Los_Angeles',
  date = '2026-01-15',
): DayData {
  const snowfallResult = calculateSnowfall({
    ...profileFixtures.coldSaturatedDgzStrongLift,
    precipitationMm,
    surface: { ...profileFixtures.coldSaturatedDgzStrongLift.surface, temperatureC: temperature },
  }, 'fixed_10');
  const time = `${date}T${String(hourOfDay).padStart(2, '0')}:00`;
  const instantMs = providerLocalTimeToEpochMs(time, timezone);
  snowfallResult.snowFraction = 1;
  snowfallResult.frozenSweMm = precipitationMm;
  snowfallResult.freshSlr = 10;
  snowfallResult.freshSnowCm = precipitationMm;
  snowfallResult.freshSnowQuantilesCm = { p10: precipitationMm, p50: precipitationMm, p90: precipitationMm };
  snowfallResult.rainMm = 0;
  snowfallResult.method = 'fixed_10';
  const hour = {
    time,
    instantMs,
    dateObj: new Date(instantMs),
    providerTimezone: timezone,
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
    dateStr: date,
    dateObj: hour.dateObj,
    totalSnowfall: hour.snowfall,
    totalPrecipitation: precipitationMm,
    hourly: [hour],
  } as DayData;
}

function overnightConditions(temperature: number): DayData {
  const hours = Array.from({ length: 8 }, (_, hour) => (
    referenceDay(0, temperature, hour, 'America/Los_Angeles', '2026-01-16').hourly[0]
  ));
  return {
    dateStr: '2026-01-16',
    dateObj: hours[0].dateObj,
    totalSnowfall: 0,
    totalPrecipitation: 0,
    hourly: hours,
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
    expect(output.openingTime).toBe('2026-01-16T08:00');
    expect(output.openingTimeZone).toBe('America/Los_Angeles');

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

  it('evolves each snowfall interval to the actual next local opening', () => {
    const location = getLocations().palisades;
    const point = resortReferencePoints(location)[1];
    const early = buildResortSnowForecasts(location, [
      { point, days: [referenceDay(10, -7, 0)] },
    ])['2026-01-15'];
    const late = buildResortSnowForecasts(location, [
      { point, days: [referenceDay(10, -7, 23)] },
    ])['2026-01-15'];

    expect(early.referencePoints[0].freshSnowCm.p50).toBeCloseTo(late.referencePoints[0].freshSnowCm.p50, 6);
    expect(early.referencePoints[0].openingSnowCm.p50).toBeLessThan(late.referencePoints[0].openingSnowCm.p50);
  });

  it('uses subsequent hourly weather rather than extrapolating the snowfall-hour temperature', () => {
    const location = getLocations().palisades;
    const point = resortReferencePoints(location)[1];
    const snowfall = referenceDay(10, -7, 23);
    const cold = buildResortSnowForecasts(location, [
      { point, days: [snowfall, overnightConditions(-7)] },
    ])['2026-01-15'];
    const warm = buildResortSnowForecasts(location, [
      { point, days: [snowfall, overnightConditions(8)] },
    ])['2026-01-15'];

    expect(warm.referencePoints[0].openingSnowCm.p50)
      .toBeLessThan(cold.referencePoints[0].openingSnowCm.p50);
  });

  it('retains opening snow on interpolated slopes when the nearest reference is dry', () => {
    const location = getLocations().palisades;
    const points = resortReferencePoints(location);
    const output = buildResortSnowForecasts(location, [
      { point: points[0], days: [referenceDay(0, -4)] },
      { point: points[2], days: [referenceDay(10, -4)] },
    ])['2026-01-15'];
    const positiveFreshSnow = output.slopeUnits.filter(unit => unit.freshSnowCm.p50 > 0);

    expect(positiveFreshSnow.length).toBeGreaterThan(0);
    expect(positiveFreshSnow.every(unit => unit.openingSnowCm.p50 > 0)).toBe(true);
  });
});
