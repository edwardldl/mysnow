import { describe, expect, it } from 'vitest';
import { applyManagementEvents } from '../management/events';
import type { ManagedSnowState } from '../management/types';
import { estimateSnowmakingPotential } from '../management/snowmaking';
import { evolveToOpening } from '../snowpack/opening';

describe('opening-time and management conservation', () => {
  it('settles cold snow without changing SWE', () => {
    const result = evolveToOpening({
      frozenSweMm: 20,
      freshSlr: 15,
      elapsedHours: 8,
      meanTemperatureC: -5,
      meanWindSpeedMs: 3,
      exposed: false,
    });
    expect(result.state.sweMm).toBeCloseTo(20);
    expect(result.state.depthCm).toBeLessThan(result.freshDepthCm);
    expect(result.meltSweMm).toBe(0);
  });

  it('removes mass only through explicit warm melt or exposed wind loss', () => {
    const result = evolveToOpening({
      frozenSweMm: 20,
      freshSlr: 12,
      elapsedHours: 12,
      meanTemperatureC: 4,
      meanWindSpeedMs: 15,
      exposed: true,
    });
    expect(result.meltSweMm).toBeGreaterThan(0);
    expect(result.state.sweMm).toBeLessThan(20 - result.meltSweMm + 1e-9);
    expect(result.windCompactionOrLossCm).toBeGreaterThan(0);
    expect(result.state.depthCm + result.settlementCm + result.windCompactionOrLossCm + result.meltDepthCm)
      .toBeCloseTo(result.freshDepthCm, 8);
  });

  it('grooming preserves SWE and transfers preserve domain mass', () => {
    const states: ManagedSnowState[] = [
      { unitId: 'a', areaM2: 100, sweMm: 20, depthCm: 20, densityKgM3: 100, isOpen: false },
      { unitId: 'b', areaM2: 200, sweMm: 10, depthCm: 10, densityKgM3: 100, isOpen: false },
    ];
    const groomed = applyManagementEvents(states, [{
      type: 'grooming',
      time: '2026-01-01T05:00',
      compactionFraction: 0.25,
      affectedUnits: ['a'],
    }]);
    expect(groomed[0].sweMm).toBe(20);
    expect(groomed[0].depthCm).toBeLessThan(20);
    const transferred = applyManagementEvents(groomed, [{
      type: 'snow_transfer',
      time: '2026-01-01T06:00',
      fromUnit: 'a',
      toUnit: 'b',
      sweKg: 500,
    }]);
    const beforeMass = groomed.reduce((sum, state) => sum + state.sweMm * state.areaM2, 0);
    const afterMass = transferred.reduce((sum, state) => sum + state.sweMm * state.areaM2, 0);
    expect(afterMass).toBeCloseTo(beforeMass);
  });

  it('reports snowmaking potential without claiming production occurred', () => {
    const favorable = estimateSnowmakingPotential(-6, 4);
    const warm = estimateSnowmakingPotential(-1, 4);
    expect(favorable.favorable).toBe(true);
    expect(favorable.efficiency).toBeGreaterThan(0);
    expect(warm.favorable).toBe(false);
    expect(warm.efficiency).toBe(0);
  });
});
