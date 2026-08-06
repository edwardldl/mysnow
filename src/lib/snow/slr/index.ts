import { DEFAULT_SLR_CALIBRATION } from '../calibration/coefficients';
import { estimateFixedTenSlr } from './fixed';
import { estimateKucheraSlr } from './kuchera';
import { estimateCobb2011Slr } from './cobb2011';
import { DEFAULT_SLR_METHOD, type SlrDiagnostics, type SlrMethod, type SnowfallInput } from '../types';

const LEGACY_METHOD_MAP: Record<string, SlrMethod> = {
  standard: 'fixed_10',
  fixed_10: 'fixed_10',
  simple: 'kuchera',
  kuchera: 'kuchera',
  kuchera_dgz: 'kuchera',
  kuchera_plus: 'kuchera',
  hybrid: 'cobb_2011',
  cobb: 'cobb_2011',
  kinematic: 'cobb_2011',
  cobb_2011: 'cobb_2011',
  krc: 'experimental_krc',
  dendro: 'experimental_krc',
  model_native: 'open_meteo_snowfall',
  open_meteo_snowfall: 'open_meteo_snowfall',
};

/** Migrates saved legacy selection ids without exposing them in the UI. */
export function toSlrMethod(method: string | undefined): SlrMethod {
  return LEGACY_METHOD_MAP[method ?? ''] ?? DEFAULT_SLR_METHOD;
}

export interface SlrEstimate {
  freshSlr: number | null;
  diagnostics: SlrDiagnostics;
}

function emptyDiagnostics(input: SnowfallInput, sourceMethod: SlrDiagnostics['sourceMethod']): SlrDiagnostics {
  return {
    validProfileLevels: input.profile.validLevelCount,
    sourceMethod,
    fallbackReason: null,
    sourceFreshSlr: null,
    finalFreshSlr: null,
    growthZoneLiftMs: null,
    dominantGrowthLayerHpa: null,
    modifiers: { melting: 1, surfaceWind: 1, shear: 1, riming: 1 },
    layers: [],
    warnings: [],
  };
}

function windComponents(speedMs: number, directionDeg: number): { u: number; v: number } {
  const directionRad = directionDeg * Math.PI / 180;
  return {
    u: -speedMs * Math.sin(directionRad),
    v: -speedMs * Math.cos(directionRad),
  };
}

function applySurfaceModifiers(
  sourceSlr: number,
  input: SnowfallInput,
  diagnostics: SlrDiagnostics,
  warmNoseEnergyCM: number | null,
): number {
  const calibration = DEFAULT_SLR_CALIBRATION;
  if (!calibration.modifiersEnabled) return sourceSlr;

  const melting = Math.max(0.7, Math.min(1, Math.exp(-calibration.meltCoefficient * (warmNoseEnergyCM ?? 0))));
  const windSpeedMs = input.surface.windSpeedMs ?? 0;
  const surfaceWind = Math.max(0.7, Math.min(1, Math.exp(-calibration.surfaceWindCoefficient * Math.max(windSpeedMs - 5, 0))));
  const growthLayer = diagnostics.dominantGrowthLayerHpa === null
    ? null
    : input.profile.aboveGroundLevels.reduce((closest, level) => {
      if (closest === null) return level;
      return Math.abs(level.pressureHpa - diagnostics.dominantGrowthLayerHpa!)
        < Math.abs(closest.pressureHpa - diagnostics.dominantGrowthLayerHpa!) ? level : closest;
    }, null as (typeof input.profile.aboveGroundLevels)[number] | null);
  let shear = 1;
  if (growthLayer && growthLayer.windSpeedMs !== null && growthLayer.windDirectionDeg !== null
    && input.surface.windSpeedMs !== null && input.surface.windDirectionDeg !== null
    && growthLayer.geopotentialHeightM !== null && input.surface.stationElevationM !== null) {
    const growthWind = windComponents(growthLayer.windSpeedMs, growthLayer.windDirectionDeg);
    const surfaceWindVector = windComponents(input.surface.windSpeedMs, input.surface.windDirectionDeg);
    const depthM = growthLayer.geopotentialHeightM - input.surface.stationElevationM;
    if (depthM > 0) {
      const shearMsPerKm = 1000 * Math.hypot(growthWind.u - surfaceWindVector.u, growthWind.v - surfaceWindVector.v) / depthM;
      shear = Math.max(0.85, Math.min(1, Math.exp(-calibration.shearCoefficient * shearMsPerKm)));
    }
  }
  const totalWeight = diagnostics.layers.reduce((sum, layer) => sum + layer.weight, 0);
  const warmWeight = diagnostics.layers
    .filter(layer => layer.temperatureC > -10 && layer.temperatureC < -2)
    .reduce((sum, layer) => sum + layer.weight, 0);
  const riming = totalWeight === 0
    ? 1
    : Math.max(0.8, Math.min(1, 1 - calibration.rimingCoefficient * (warmWeight / totalWeight)));
  diagnostics.modifiers = { melting, surfaceWind, shear, riming };
  return Math.max(3, Math.min(30, sourceSlr * melting * surfaceWind * shear * riming));
}

export function estimateFreshSnowSlr(
  input: SnowfallInput,
  method: SlrMethod,
  warmNoseEnergyCM: number | null = null,
): SlrEstimate {
  if (method === 'fixed_10') {
    const freshSlr = estimateFixedTenSlr();
    const diagnostics = emptyDiagnostics(input, 'fixed_10');
    diagnostics.sourceFreshSlr = freshSlr;
    diagnostics.finalFreshSlr = freshSlr;
    return { freshSlr, diagnostics };
  }

  if (method === 'open_meteo_snowfall') {
    const diagnostics = emptyDiagnostics(input, 'open_meteo_snowfall');
    // Open-Meteo's generic snowfall field uses a fixed conversion. Keep it as
    // a comparison depth and never reinterpret it as an observed/model SLR.
    if (input.snowfallCm === null || input.snowfallCm < 0) {
      diagnostics.warnings.push('MODEL_SNOWFALL_UNAVAILABLE');
    }
    return { freshSlr: null, diagnostics };
  }

  if (method === 'cobb_2011') {
    const cobb = estimateCobb2011Slr(input.profile);
    const diagnostics = emptyDiagnostics(input, 'cobb_2011');
    diagnostics.fallbackReason = cobb.fallbackReason;
    diagnostics.growthZoneLiftMs = cobb.growthZoneLiftMs;
    diagnostics.dominantGrowthLayerHpa = cobb.dominantGrowthLayerHpa;
    diagnostics.layers = cobb.layers;
    if (cobb.freshSlr !== null) {
      diagnostics.sourceFreshSlr = cobb.freshSlr;
      diagnostics.finalFreshSlr = applySurfaceModifiers(cobb.freshSlr, input, diagnostics, warmNoseEnergyCM);
      return { freshSlr: diagnostics.finalFreshSlr, diagnostics };
    }
    const freshSlr = estimateKucheraSlr(input.profile, input.surface.temperatureC);
    diagnostics.sourceMethod = 'kuchera';
    diagnostics.sourceFreshSlr = freshSlr;
    diagnostics.finalFreshSlr = freshSlr;
    diagnostics.warnings.push('KUCHERA_FALLBACK');
    if (cobb.fallbackReason === 'SPARSE_PROFILE') diagnostics.warnings.push('SPARSE_PROFILE');
    if (cobb.fallbackReason === 'MISSING_VERTICAL_VELOCITY') diagnostics.warnings.push('MISSING_VERTICAL_VELOCITY');
    if (cobb.fallbackReason === 'MISSING_DEW_POINT_PROFILE') diagnostics.warnings.push('MISSING_DEW_POINT_PROFILE');
    return { freshSlr, diagnostics };
  }

  // Experimental KRC now shares the stable, phase-separated baseline until it
  // has calibration and independent validation.
  const freshSlr = estimateKucheraSlr(input.profile, input.surface.temperatureC);
  const diagnostics = emptyDiagnostics(input, 'kuchera');
  diagnostics.sourceFreshSlr = freshSlr;
  diagnostics.finalFreshSlr = freshSlr;
  if (method === 'experimental_krc') diagnostics.fallbackReason = 'EXPERIMENTAL_BASELINE';
  return { freshSlr, diagnostics };
}
