import { relativeHumidityIceFromDewPoint } from '../humidity';
import { upwardVelocity } from '../profile';
import type { CobbLayerDiagnostic, NormalizedProfile, PressureLayer } from '../types';

const COBB_2011_POINTS: ReadonlyArray<readonly [number, number]> = [
  [-30, 7.2], [-28, 6.8], [-26, 7.0], [-24, 8.8], [-22, 12.0], [-20, 18.0],
  [-18, 23.0], [-16, 26.0], [-14, 22.5], [-12, 17.5], [-10, 12.0], [-8, 9.5],
  [-6, 9.0], [-4, 8.5], [-2, 7.0], [0, 3.0], [1, 0.0],
];
const TARGET_STEP_HPA = 25;
const MAX_INTERPOLATION_GAP_HPA = 125;
const MIN_COBB_WEIGHT = 1e-6;

export interface CobbEstimate {
  freshSlr: number | null;
  fallbackReason: string | null;
  growthZoneLiftMs: number | null;
  dominantGrowthLayerHpa: number | null;
  layers: CobbLayerDiagnostic[];
}

function interpolate(first: number, second: number, fraction: number): number {
  return first + (second - first) * fraction;
}

function interpolateAngle(first: number | null, second: number | null, fraction: number): number | null {
  if (first === null || second === null) return null;
  const delta = ((second - first + 540) % 360) - 180;
  return (first + fraction * delta + 360) % 360;
}

function interpolateLayers(profile: NormalizedProfile): PressureLayer[] {
  const source = profile.aboveGroundLevels
    .filter(level => level.temperatureC !== null
      && level.dewPointC !== null
      && level.geopotentialHeightM !== null
      && level.verticalVelocityMs !== null)
    .sort((a, b) => b.pressureHpa - a.pressureHpa);
  if (source.length < 2) return [];

  const interpolated: PressureLayer[] = [];
  for (let index = 0; index < source.length - 1; index += 1) {
    const lower = source[index];
    const upper = source[index + 1];
    const gapHpa = lower.pressureHpa - upper.pressureHpa;
    if (gapHpa <= 0 || gapHpa > MAX_INTERPOLATION_GAP_HPA) continue;

    const pointCount = Math.max(1, Math.round(gapHpa / TARGET_STEP_HPA));
    for (let point = 0; point < pointCount; point += 1) {
      const fraction = point / pointCount;
      const temperatureC = interpolate(lower.temperatureC!, upper.temperatureC!, fraction);
      const dewPointC = interpolate(lower.dewPointC!, upper.dewPointC!, fraction);
      interpolated.push({
        pressureHpa: interpolate(lower.pressureHpa, upper.pressureHpa, fraction),
        geopotentialHeightM: interpolate(lower.geopotentialHeightM!, upper.geopotentialHeightM!, fraction),
        temperatureC,
        dewPointC,
        relativeHumidityWaterPct: lower.relativeHumidityWaterPct === null || upper.relativeHumidityWaterPct === null
          ? null
          : interpolate(lower.relativeHumidityWaterPct, upper.relativeHumidityWaterPct, fraction),
        relativeHumidityIcePct: relativeHumidityIceFromDewPoint(temperatureC, dewPointC),
        verticalVelocityMs: interpolate(lower.verticalVelocityMs!, upper.verticalVelocityMs!, fraction),
        windSpeedMs: lower.windSpeedMs === null || upper.windSpeedMs === null
          ? null
          : interpolate(lower.windSpeedMs, upper.windSpeedMs, fraction),
        windDirectionDeg: interpolateAngle(lower.windDirectionDeg, upper.windDirectionDeg, fraction),
        cloudCoverPct: lower.cloudCoverPct === null || upper.cloudCoverPct === null
          ? null
          : interpolate(lower.cloudCoverPct, upper.cloudCoverPct, fraction),
        isAboveGround: true,
      });
    }
  }
  const top = source[source.length - 1];
  interpolated.push({
    ...top,
    relativeHumidityIcePct: relativeHumidityIceFromDewPoint(top.temperatureC!, top.dewPointC!),
  });
  return interpolated;
}

function layerThicknessM(levels: PressureLayer[], index: number): number {
  const current = levels[index].geopotentialHeightM!;
  const lower = index > 0 ? levels[index - 1].geopotentialHeightM! : current;
  const upper = index < levels.length - 1 ? levels[index + 1].geopotentialHeightM! : current;
  if (index === 0) return Math.max(0, upper - current) / 2;
  if (index === levels.length - 1) return Math.max(0, current - lower) / 2;
  return Math.max(0, upper - lower) / 2;
}

export function cobb2011LayerSlr(temperatureC: number): number {
  if (temperatureC <= COBB_2011_POINTS[0][0]) return COBB_2011_POINTS[0][1];
  const last = COBB_2011_POINTS[COBB_2011_POINTS.length - 1];
  if (temperatureC >= last[0]) return last[1];
  for (let index = 0; index < COBB_2011_POINTS.length - 1; index += 1) {
    const [leftTemperature, leftSlr] = COBB_2011_POINTS[index];
    const [rightTemperature, rightSlr] = COBB_2011_POINTS[index + 1];
    if (temperatureC >= leftTemperature && temperatureC <= rightTemperature) {
      return interpolate(leftSlr, rightSlr, (temperatureC - leftTemperature) / (rightTemperature - leftTemperature));
    }
  }
  return last[1];
}

/** Faithful Cobb-Waldstreicher 2011 cloud/ascent weighting. */
export function estimateCobb2011Slr(profile: NormalizedProfile): CobbEstimate {
  if (profile.validLevelCount < 4) {
    return { freshSlr: null, fallbackReason: 'SPARSE_PROFILE', growthZoneLiftMs: null, dominantGrowthLayerHpa: null, layers: [] };
  }
  if (profile.aboveGroundLevels.some(level => level.dewPointC === null)) {
    return { freshSlr: null, fallbackReason: 'MISSING_DEW_POINT_PROFILE', growthZoneLiftMs: null, dominantGrowthLayerHpa: null, layers: [] };
  }
  if (profile.aboveGroundLevels.some(level => level.verticalVelocityMs === null)) {
    return { freshSlr: null, fallbackReason: 'MISSING_VERTICAL_VELOCITY', growthZoneLiftMs: null, dominantGrowthLayerHpa: null, layers: [] };
  }

  const levels = interpolateLayers(profile);
  if (levels.length < 4) {
    return { freshSlr: null, fallbackReason: 'INSUFFICIENT_CONTIGUOUS_PROFILE', growthZoneLiftMs: null, dominantGrowthLayerHpa: null, layers: [] };
  }
  const cloudyAscending = levels.filter(level => level.relativeHumidityIcePct! >= 90 && upwardVelocity(level.verticalVelocityMs) > 0);
  const maxUpwardVelocityMs = Math.max(0, ...cloudyAscending.map(level => upwardVelocity(level.verticalVelocityMs)));
  if (maxUpwardVelocityMs === 0) {
    return { freshSlr: null, fallbackReason: 'NO_SATURATED_ASCENT', growthZoneLiftMs: 0, dominantGrowthLayerHpa: null, layers: [] };
  }

  const layers: CobbLayerDiagnostic[] = levels.map((level, index) => {
    const upwardVelocityMs = upwardVelocity(level.verticalVelocityMs);
    const thicknessM = layerThicknessM(levels, index);
    const eligible = level.relativeHumidityIcePct! >= 90 && upwardVelocityMs > 0;
    const weight = eligible
      ? upwardVelocityMs * Math.pow(upwardVelocityMs / maxUpwardVelocityMs, 2) * thicknessM
      : 0;
    return {
      pressureHpa: level.pressureHpa,
      temperatureC: level.temperatureC!,
      relativeHumidityIcePct: level.relativeHumidityIcePct!,
      upwardVelocityMs,
      thicknessM,
      weight,
      layerSlr: cobb2011LayerSlr(level.temperatureC!),
      contribution: 0,
    };
  });
  const weightSum = layers.reduce((sum, layer) => sum + layer.weight, 0);
  if (weightSum < MIN_COBB_WEIGHT) {
    return { freshSlr: null, fallbackReason: 'NO_SATURATED_ASCENT', growthZoneLiftMs: maxUpwardVelocityMs, dominantGrowthLayerHpa: null, layers };
  }
  for (const layer of layers) layer.contribution = (layer.weight * layer.layerSlr) / weightSum;
  const freshSlr = layers.reduce((sum, layer) => sum + layer.contribution, 0);
  const dominantLayer = layers.reduce((best, layer) => layer.weight > best.weight ? layer : best, layers[0]);
  return {
    freshSlr: Math.max(3, Math.min(30, freshSlr)),
    fallbackReason: null,
    growthZoneLiftMs: maxUpwardVelocityMs,
    dominantGrowthLayerHpa: Math.round(dominantLayer.pressureHpa),
    layers,
  };
}
