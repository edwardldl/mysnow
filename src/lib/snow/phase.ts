import { wetBulbFromTemperatureAndDewPoint } from './humidity';
import type { PhaseDistribution, PhaseResult, SnowDiagnosticWarning, SnowfallInput } from './types';

const PRECIPITATION_EPSILON_MM = 0.01;

function clamp(value: number, min = 0, max = 1): number {
  return Math.max(min, Math.min(max, value));
}

function logisticSnowFraction(wetBulbC: number): number {
  // A smooth, intentionally conservative surface fallback. These coefficients
  // are isolated here so they can be calibrated later without changing SLR.
  return 1 / (1 + Math.exp(1.35 * (wetBulbC - 0.5)));
}

function weatherCodeIndicatesRain(weatherCode: number | null): boolean {
  return weatherCode !== null && ((weatherCode >= 51 && weatherCode <= 67) || weatherCode === 80 || weatherCode === 81 || weatherCode === 82);
}

function mapPrecipitationType(type: number | null): number | null {
  if (type === null) return null;
  // Open-Meteo model categories are not universally available. Keep mappings
  // intentionally fractional for mixed categories and fail closed otherwise.
  const fractions: Record<number, number> = {
    0: 0,
    1: 0,
    2: 0.25,
    3: 0.5,
    4: 0.75,
    5: 1,
  };
  return fractions[type] ?? null;
}

interface ThermalEnergy {
  warmNoseEnergyCM: number | null;
  refreezeEnergyCM: number | null;
  hasProfile: boolean;
}

function integrateThermalProfile(input: SnowfallInput): ThermalEnergy {
  const levels = input.profile.aboveGroundLevels
    .map(level => ({
      heightM: level.geopotentialHeightM,
      wetBulbC: wetBulbFromTemperatureAndDewPoint(
        level.temperatureC,
        level.dewPointC,
        level.relativeHumidityWaterPct,
      ),
    }))
    .filter((level): level is { heightM: number; wetBulbC: number } => level.heightM !== null && level.wetBulbC !== null)
    .sort((a, b) => a.heightM - b.heightM);

  if (levels.length < 2) {
    return { warmNoseEnergyCM: null, refreezeEnergyCM: null, hasProfile: false };
  }

  let warmNoseEnergyCM = 0;
  const segments: Array<{ wetBulbC: number; energyCM: number }> = [];
  for (let index = 0; index < levels.length - 1; index += 1) {
    const bottom = levels[index];
    const top = levels[index + 1];
    const thicknessCm = Math.max(0, top.heightM - bottom.heightM) * 100;
    const wetBulbC = (bottom.wetBulbC + top.wetBulbC) / 2;

    const energyCM = Math.abs(wetBulbC) * thicknessCm;
    segments.push({ wetBulbC, energyCM });
    if (wetBulbC > 0) warmNoseEnergyCM += energyCM;
  }
  const lowestWarmSegment = segments.findIndex(segment => segment.wetBulbC > 0);
  const refreezeEnergyCM = lowestWarmSegment <= 0
    ? 0
    : segments.slice(0, lowestWarmSegment)
      .filter(segment => segment.wetBulbC < 0)
      .reduce((sum, segment) => sum + segment.energyCM, 0);

  return { warmNoseEnergyCM, refreezeEnergyCM, hasProfile: true };
}

function buildResult(
  snowFraction: number,
  confidence: number,
  source: PhaseResult['source'],
  surfaceWetBulbC: number | null,
  thermal: ThermalEnergy,
  weatherCode: number | null,
): PhaseResult {
  const warnings: SnowDiagnosticWarning[] = [];
  const boundedSnowFraction = clamp(snowFraction);
  if (boundedSnowFraction > 0.8 && weatherCodeIndicatesRain(weatherCode)) {
    warnings.push('PHASE_DISAGREEMENT');
  }
  if ((thermal.warmNoseEnergyCM ?? 0) > 50_000) warnings.push('LARGE_WARM_NOSE');

  const mixedRainSnow = Math.min(0.35, 0.7 * Math.min(boundedSnowFraction, 1 - boundedSnowFraction));
  const warmNose = thermal.warmNoseEnergyCM ?? 0;
  const refreeze = thermal.refreezeEnergyCM ?? 0;
  const icePellets = warmNose > 50_000 && refreeze > warmNose * 0.25
    ? Math.min(0.2, boundedSnowFraction * 0.3)
    : 0;
  const freezingRain = warmNose > 50_000 && refreeze <= warmNose * 0.25
    ? Math.min(0.2, (1 - boundedSnowFraction) * 0.3)
    : 0;
  const rawDistribution = {
    snow: Math.max(0, boundedSnowFraction - mixedRainSnow / 2 - icePellets),
    mixedRainSnow,
    rain: Math.max(0, 1 - boundedSnowFraction - mixedRainSnow / 2 - freezingRain),
    icePellets,
    freezingRain,
  };
  const distributionTotal = Object.values(rawDistribution).reduce((sum, value) => sum + value, 0) || 1;
  const distribution: PhaseDistribution = {
    snow: rawDistribution.snow / distributionTotal,
    mixedRainSnow: rawDistribution.mixedRainSnow / distributionTotal,
    rain: rawDistribution.rain / distributionTotal,
    icePellets: rawDistribution.icePellets / distributionTotal,
    freezingRain: rawDistribution.freezingRain / distributionTotal,
    expectedFrozenFraction: boundedSnowFraction,
    confidence: clamp(confidence),
  };

  return {
    snowFraction: boundedSnowFraction,
    rainFraction: 1 - boundedSnowFraction,
    confidence: clamp(confidence),
    distribution,
    source,
    diagnostics: {
      surfaceWetBulbC,
      warmNoseEnergyCM: thermal.warmNoseEnergyCM,
      refreezeEnergyCM: thermal.refreezeEnergyCM,
    },
    warnings,
  };
}

/**
 * Estimate frozen precipitation separately from fresh-snow density. The
 * hierarchy deliberately does not use WMO weather code as a snow/no-snow gate.
 */
export function estimatePrecipitationPhase(input: SnowfallInput): PhaseResult {
  const precipitationMm = Math.max(0, input.precipitationMm ?? 0);
  const surfaceWetBulbC = input.surface.wetBulbTemperatureC
    ?? wetBulbFromTemperatureAndDewPoint(
      input.surface.temperatureC,
      input.surface.dewPointC,
      input.surface.relativeHumidityPct,
    );
  const thermal = integrateThermalProfile(input);

  if (precipitationMm <= PRECIPITATION_EPSILON_MM) {
    return buildResult(0, 1, 'surface_fallback', surfaceWetBulbC, thermal, input.weatherCode);
  }

  const modelSweMm = input.snowfallWaterEquivalentMm;
  if (modelSweMm !== null && Number.isFinite(modelSweMm) && modelSweMm >= 0 && modelSweMm <= precipitationMm * 1.1 + PRECIPITATION_EPSILON_MM) {
    return buildResult(
      modelSweMm / precipitationMm,
      0.95,
      'snowfall_water_equivalent',
      surfaceWetBulbC,
      thermal,
      input.weatherCode,
    );
  }

  const precipitationTypeFraction = mapPrecipitationType(input.precipitationType);
  if (precipitationTypeFraction !== null) {
    return buildResult(
      precipitationTypeFraction,
      0.8,
      'precipitation_type',
      surfaceWetBulbC,
      thermal,
      input.weatherCode,
    );
  }

  if (thermal.hasProfile && surfaceWetBulbC !== null) {
    const profileWetBulbs = input.profile.aboveGroundLevels
      .map(level => wetBulbFromTemperatureAndDewPoint(level.temperatureC, level.dewPointC, level.relativeHumidityWaterPct))
      .filter((value): value is number => value !== null);
    const completelyFrozen = profileWetBulbs.length > 0 && profileWetBulbs.every(value => value <= 0);
    if (completelyFrozen) {
      return buildResult(1, 0.85, 'wet_bulb_profile', surfaceWetBulbC, thermal, input.weatherCode);
    }

    const warmNosePenalty = Math.exp(-0.00002 * (thermal.warmNoseEnergyCM ?? 0));
    const refreezeRecovery = 1 - Math.exp(-0.00002 * (thermal.refreezeEnergyCM ?? 0));
    const baseSnowFraction = logisticSnowFraction(surfaceWetBulbC);
    const snowFraction = baseSnowFraction * warmNosePenalty
      + (1 - baseSnowFraction) * refreezeRecovery * 0.35;
    return buildResult(snowFraction, 0.65, 'wet_bulb_profile', surfaceWetBulbC, thermal, input.weatherCode);
  }

  return buildResult(
    surfaceWetBulbC === null ? 0.5 : logisticSnowFraction(surfaceWetBulbC),
    surfaceWetBulbC === null ? 0.2 : 0.45,
    'surface_fallback',
    surfaceWetBulbC,
    thermal,
    input.weatherCode,
  );
}
