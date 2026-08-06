import { describe, expect, it } from 'vitest';
import { getLocations } from '../../api';
import { createMvpSlopeUnits, referencePointForMode, resortReferencePoints } from '../resort/geometry';

describe('resort reference geometry', () => {
  it('uses distinct curated base, mid-mountain, and summit coordinates', () => {
    const location = getLocations().palisades;
    const points = resortReferencePoints(location);
    expect(points.map(point => point.role)).toEqual(['base', 'mid_mountain', 'summit']);
    expect(new Set(points.map(point => `${point.latitude},${point.longitude}`)).size).toBe(3);
    expect(points.every(point => point.geometrySource === 'curated')).toBe(true);
    expect(referencePointForMode(location, 'min').role).toBe('base');
    expect(referencePointForMode(location, 'max').role).toBe('summit');
  });

  it('publishes deterministic SRUs instead of a false fine-grid map', () => {
    const units = createMvpSlopeUnits(getLocations().palisades);
    expect(units.length).toBeGreaterThan(0);
    expect(units.every(unit => unit.areaM2 > 0)).toBe(true);
    expect(units.every(unit => unit.terrainSource === 'mvp_reference_classes')).toBe(true);
    expect(new Set(units.map(unit => unit.management))).toEqual(new Set(['natural', 'groomed']));
  });
});
