import type { Location } from '../../types';
import type {
  ReferencePointRole,
  ResortReferencePoint,
  SlopeReferenceUnit,
  WindExposureClass,
} from './types';

const ROLES: ReferencePointRole[] = ['base', 'mid_mountain', 'summit'];
const ASPECTS = [0, 90, 180, 270];
const EXPOSURES: WindExposureClass[] = ['sheltered', 'neutral', 'exposed'];
const DIRECTION_BIN_COUNT = 24;

function elevationForRole(location: Location, role: ReferencePointRole): number {
  const fallback = typeof location.elevationM === 'number' ? location.elevationM : 1000;
  const minimum = location.minElevationM ?? fallback;
  const maximum = location.maxElevationM ?? fallback;
  if (role === 'base') return minimum;
  if (role === 'summit') return maximum;
  return (minimum + maximum) / 2;
}

export function resortReferencePoints(location: Location): ResortReferencePoint[] {
  if (location.referencePoints?.length) {
    return ROLES.map(role => location.referencePoints!.find(point => point.role === role))
      .filter((point): point is ResortReferencePoint => point !== undefined);
  }

  return ROLES.map(role => ({
    id: `${location.id}-${role}`,
    name: role === 'mid_mountain' ? 'Mid-mountain estimate' : `${role[0].toUpperCase()}${role.slice(1)} estimate`,
    role,
    latitude: location.latitude,
    longitude: location.longitude,
    elevationM: elevationForRole(location, role),
    geometrySource: 'estimated' as const,
  }));
}

export function referencePointForMode(location: Location, mode: string): ResortReferencePoint {
  const role: ReferencePointRole = mode === 'min' || mode === 'base'
    ? 'base'
    : mode === 'max' || mode === 'summit'
      ? 'summit'
      : 'mid_mountain';
  return resortReferencePoints(location).find(point => point.role === role)
    ?? resortReferencePoints(location)[0];
}

function directionalShelter(aspectDeg: number, exposure: WindExposureClass): number[] {
  const exposureOffset = exposure === 'sheltered' ? 0.65 : exposure === 'exposed' ? -0.65 : 0;
  return Array.from({ length: DIRECTION_BIN_COUNT }, (_, index) => {
    const windFromDeg = index * (360 / DIRECTION_BIN_COUNT);
    const relative = (windFromDeg - aspectDeg) * Math.PI / 180;
    return Math.max(-1, Math.min(1, exposureOffset + 0.2 * Math.cos(relative)));
  });
}

/**
 * Create honest SRU-scale MVP classes. These are reference classes, not a DEM
 * map; their source is carried with every unit so the UI cannot imply 10 m
 * precision.
 */
export function createMvpSlopeUnits(location: Location): SlopeReferenceUnit[] {
  const points = resortReferencePoints(location);
  const minimum = Math.min(...points.map(point => point.elevationM));
  const maximum = Math.max(...points.map(point => point.elevationM));
  const bandCount = Math.max(1, Math.ceil((maximum - minimum) / 100));
  const bandSize = (maximum - minimum) / bandCount || 100;
  const center = points.find(point => point.role === 'mid_mountain') ?? points[0];
  const units: SlopeReferenceUnit[] = [];

  for (let band = 0; band < bandCount; band += 1) {
    const minElevationM = minimum + band * bandSize;
    const maxElevationM = band === bandCount - 1 ? maximum : minElevationM + bandSize;
    const meanElevationM = (minElevationM + maxElevationM) / 2;
    for (const aspect of ASPECTS) {
      for (const exposure of EXPOSURES) {
        for (const management of ['natural', 'groomed'] as const) {
          const radians = aspect * Math.PI / 180;
          const distanceDeg = 0.0015 * ((meanElevationM - minimum) / Math.max(maximum - minimum, 1));
          units.push({
            id: `${location.id}-${Math.round(meanElevationM)}-${aspect}-${exposure}-${management}`,
            areaM2: 50_000,
            centroidLat: center.latitude + Math.cos(radians) * distanceDeg,
            centroidLon: center.longitude + Math.sin(radians) * distanceDeg,
            minElevationM,
            meanElevationM,
            maxElevationM,
            meanSlopeDeg: management === 'groomed' ? 14 : 22,
            meanAspectDeg: aspect,
            topographicPosition: exposure === 'sheltered' ? -0.45 : exposure === 'exposed' ? 0.45 : 0,
            windShelterByDirection: directionalShelter(aspect, exposure),
            curvature: exposure === 'sheltered' ? 0.25 : exposure === 'exposed' ? -0.15 : 0,
            canopyFraction: exposure === 'sheltered' ? 0.35 : exposure === 'neutral' ? 0.15 : 0.02,
            exposure,
            management,
            terrainSource: 'mvp_reference_classes',
          });
        }
      }
    }
  }
  return units;
}
