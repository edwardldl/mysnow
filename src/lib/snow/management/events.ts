import type { ManagedSnowState, ManagementEvent } from './types';

function depthFromMass(sweMm: number, densityKgM3: number): number {
  return densityKgM3 > 0 ? 100 * sweMm / densityKgM3 : 0;
}

/** Apply only known operational events. Unknown operations never become snow. */
export function applyManagementEvents(
  states: ManagedSnowState[],
  events: ManagementEvent[],
): ManagedSnowState[] {
  const next = new Map(states.map(state => [state.unitId, { ...state }]));

  for (const event of events) {
    if (event.type === 'grooming') {
      const compactionFraction = Math.max(0, Math.min(0.6, event.compactionFraction));
      for (const unitId of event.affectedUnits) {
        const state = next.get(unitId);
        if (!state) continue;
        state.densityKgM3 = Math.min(650, state.densityKgM3 / Math.max(0.4, 1 - compactionFraction));
        state.depthCm = depthFromMass(state.sweMm, state.densityKgM3);
      }
      continue;
    }

    if (event.type === 'snow_transfer') {
      const from = next.get(event.fromUnit);
      const to = next.get(event.toUnit);
      if (!from || !to || event.sweKg <= 0) continue;
      const availableKg = from.sweMm * from.areaM2;
      const transferKg = Math.min(availableKg, event.sweKg);
      from.sweMm -= transferKg / from.areaM2;
      to.sweMm += transferKg / to.areaM2;
      from.depthCm = depthFromMass(from.sweMm, from.densityKgM3);
      to.depthCm = depthFromMass(to.sweMm, to.densityKgM3);
      continue;
    }

    if (event.type === 'snowmaking') {
      const startMs = Date.parse(event.start);
      const endMs = Date.parse(event.end);
      const durationHours = Number.isFinite(startMs) && Number.isFinite(endMs)
        ? Math.max(0, endMs - startMs) / 3_600_000
        : 0;
      const totalWaterKg = Math.max(0, event.waterFlowM3h) * durationHours * 1000;
      const affected = event.affectedUnits.map(unitId => next.get(unitId)).filter((state): state is ManagedSnowState => state !== undefined);
      const totalAreaM2 = affected.reduce((sum, state) => sum + state.areaM2, 0);
      for (const state of affected) {
        const addedSweMm = totalAreaM2 > 0 ? totalWaterKg / totalAreaM2 : 0;
        state.sweMm += addedSweMm;
        state.densityKgM3 = Math.max(state.densityKgM3, event.expectedDensityKgM3);
        state.depthCm = depthFromMass(state.sweMm, state.densityKgM3);
      }
      continue;
    }

    for (const unitId of event.affectedUnits) {
      const state = next.get(unitId);
      if (state) state.isOpen = event.type === 'opening';
    }
  }

  return states.map(state => next.get(state.unitId) ?? state);
}
