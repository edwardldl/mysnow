import type { BlendedHour, DayData, Location } from '../../types';
import { evolveToOpening } from '../snowpack/opening';
import type { PhaseDistribution, Quantiles } from '../types';
import { assessDrift } from '../terrain/drift';
import { distributeTerrainSnow, type TerrainBaseline } from '../terrain/deposition';
import { createMvpSlopeUnits } from './geometry';
import type {
  ReferencePointForecast,
  ResortReferencePoint,
  ResortSnowForecast,
  SlopeUnitForecast,
  WindExposureClass,
} from './types';

export interface ReferenceForecastInput {
  point: ResortReferencePoint;
  days: DayData[];
}

function quantile(values: number[], probability: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = (sorted.length - 1) * probability;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (index - lower);
}

function sumFreshSnowQuantiles(day: DayData): Quantiles {
  const memberCount = Math.max(0, ...day.hourly.map(hour => hour.ensembleSnowfall?.memberResults.length ?? 0));
  if (memberCount >= 2) {
    const totals = Array.from({ length: memberCount }, (_, memberIndex) => day.hourly.reduce(
      (sum, hour) => sum + (hour.ensembleSnowfall?.memberResults[memberIndex]?.freshSnowCm ?? 0),
      0,
    ));
    return { p10: quantile(totals, 0.1), p50: quantile(totals, 0.5), p90: quantile(totals, 0.9) };
  }
  return day.hourly.reduce<Quantiles>((total, hour) => {
    const range = hour.snowfallResult?.freshSnowQuantilesCm ?? { p10: 0, p50: hour.snowfall, p90: 0 };
    return { p10: total.p10 + range.p10, p50: total.p50 + range.p50, p90: total.p90 + range.p90 };
  }, { p10: 0, p50: 0, p90: 0 });
}

function sumFrozenSweQuantiles(day: DayData): Quantiles {
  const median = day.hourly.reduce((sum, hour) => sum + (hour.frozenSweMm ?? 0), 0);
  const phaseConfidence = precipitationWeightedPhase(day.hourly).confidence;
  const spread = 0.15 + (1 - phaseConfidence) * 0.35;
  return { p10: median * (1 - spread), p50: median, p90: median * (1 + spread) };
}

function precipitationWeightedPhase(hours: BlendedHour[]): PhaseDistribution {
  const totalWeight = hours.reduce((sum, hour) => sum + Math.max(0, hour.precipitation), 0);
  if (totalWeight <= 0) {
    return { snow: 0, mixedRainSnow: 0, rain: 1, icePellets: 0, freezingRain: 0, expectedFrozenFraction: 0, confidence: 1 };
  }
  const fields: Array<keyof Omit<PhaseDistribution, 'confidence'>> = [
    'snow', 'mixedRainSnow', 'rain', 'icePellets', 'freezingRain', 'expectedFrozenFraction',
  ];
  const result: PhaseDistribution = {
    snow: 0,
    mixedRainSnow: 0,
    rain: 0,
    icePellets: 0,
    freezingRain: 0,
    expectedFrozenFraction: 0,
    confidence: 0,
  };
  for (const hour of hours) {
    const weight = Math.max(0, hour.precipitation) / totalWeight;
    const distribution = hour.snowfallResult?.phase.distribution;
    if (!distribution) continue;
    for (const field of fields) result[field] += distribution[field] * weight;
    result.confidence += distribution.confidence * weight;
  }
  return result;
}

function mean(values: Array<number | null>): number {
  const present = values.filter((value): value is number => value !== null && Number.isFinite(value));
  return present.length === 0 ? 0 : present.reduce((sum, value) => sum + value, 0) / present.length;
}

function circularMean(values: Array<number | null>): number | null {
  const present = values.filter((value): value is number => value !== null && Number.isFinite(value));
  if (present.length === 0) return null;
  const x = present.reduce((sum, value) => sum + Math.cos(value * Math.PI / 180), 0);
  const y = present.reduce((sum, value) => sum + Math.sin(value * Math.PI / 180), 0);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

function nextOpeningTime(date: string): string {
  const nextDay = new Date(`${date}T00:00:00Z`);
  nextDay.setUTCDate(nextDay.getUTCDate() + 1);
  return `${nextDay.toISOString().slice(0, 10)}T08:00`;
}

function referenceForecast(point: ResortReferencePoint, day: DayData): ReferencePointForecast {
  const freshSnowCm = sumFreshSnowQuantiles(day);
  const frozenSweMm = sumFrozenSweQuantiles(day);
  const isComparison = day.hourly.some(hour => hour.snowfallResult?.method === 'open_meteo_snowfall');
  const medianSlr = !isComparison && frozenSweMm.p50 > 0 ? freshSnowCm.p50 * 10 / frozenSweMm.p50 : null;
  const evolution = evolveToOpening({
    frozenSweMm: frozenSweMm.p50,
    freshSlr: medianSlr ?? 10,
    elapsedHours: 8,
    meanTemperatureC: mean(day.hourly.map(hour => hour.temperature)),
    meanWindSpeedMs: mean(day.hourly.map(hour => hour.windSpeed)),
    exposed: false,
  });
  const remainingFraction = freshSnowCm.p50 > 0 ? evolution.state.depthCm / freshSnowCm.p50 : 1;
  return {
    point,
    freshSnowCm,
    frozenSweMm,
    openingSnowCm: isComparison ? { p10: 0, p50: 0, p90: 0 } : {
        p10: freshSnowCm.p10 * remainingFraction * 0.9,
        p50: evolution.state.depthCm,
        p90: freshSnowCm.p90 * Math.min(1, remainingFraction * 1.08),
      },
    phase: precipitationWeightedPhase(day.hourly),
    medianSlr,
  };
}

function interpolateReference(
  references: ReferencePointForecast[],
  elevationM: number,
): { frozenSweMm: number; freshSlr: number } {
  const sorted = [...references].sort((a, b) => a.point.elevationM - b.point.elevationM);
  const lower = [...sorted].reverse().find(reference => reference.point.elevationM <= elevationM) ?? sorted[0];
  const upper = sorted.find(reference => reference.point.elevationM >= elevationM) ?? sorted[sorted.length - 1];
  if (lower === upper || upper.point.elevationM === lower.point.elevationM) {
    return { frozenSweMm: lower.frozenSweMm.p50, freshSlr: lower.medianSlr ?? 10 };
  }
  const fraction = (elevationM - lower.point.elevationM) / (upper.point.elevationM - lower.point.elevationM);
  return {
    frozenSweMm: lower.frozenSweMm.p50 + (upper.frozenSweMm.p50 - lower.frozenSweMm.p50) * fraction,
    freshSlr: (lower.medianSlr ?? 10) + ((upper.medianSlr ?? 10) - (lower.medianSlr ?? 10)) * fraction,
  };
}

function terrainSummary(
  slopeUnits: SlopeUnitForecast[],
  exposure: WindExposureClass,
): Quantiles {
  const units = slopeUnits.filter(forecast => forecast.unit.exposure === exposure && forecast.unit.management === 'natural');
  return {
    p10: quantile(units.map(unit => unit.openingSnowCm.p10), 0.1),
    p50: quantile(units.map(unit => unit.openingSnowCm.p50), 0.5),
    p90: quantile(units.map(unit => unit.openingSnowCm.p90), 0.9),
  };
}

export function buildResortSnowForecasts(
  location: Location,
  references: ReferenceForecastInput[],
): Record<string, ResortSnowForecast> {
  const dates = [...new Set(references.flatMap(reference => reference.days.map(day => day.dateStr)))];
  const slopeUnits = createMvpSlopeUnits(location);
  const forecasts: Record<string, ResortSnowForecast> = {};

  for (const date of dates) {
    const available = references.flatMap(reference => {
      const day = reference.days.find(candidate => candidate.dateStr === date);
      return day ? [{ input: reference, day }] : [];
    });
    if (available.length === 0) continue;
    const referencePoints = available.map(({ input, day }) => referenceForecast(input.point, day));
    const comparisonOnly = available.every(({ day }) => day.hourly.some(hour => hour.snowfallResult?.method === 'open_meteo_snowfall'));
    const allHours = available.flatMap(({ day }) => day.hourly);
    const windFromDeg = circularMean(allHours.map(hour => hour.windDir));
    const meanWindSpeedMs = mean(allHours.map(hour => hour.windSpeed));
    const meanTemperatureC = mean(allHours.map(hour => hour.temperature));
    const baselines: TerrainBaseline[] = slopeUnits.map(unit => ({ unit, ...interpolateReference(referencePoints, unit.meanElevationM) }));
    const deposited = comparisonOnly ? [] : distributeTerrainSnow(baselines, windFromDeg);
    const slopeForecasts: SlopeUnitForecast[] = deposited.map(deposition => {
      const evolution = evolveToOpening({
        frozenSweMm: deposition.depositedFrozenSweMm,
        freshSlr: baselines.find(baseline => baseline.unit.id === deposition.unit.id)?.freshSlr ?? 10,
        elapsedHours: 8,
        meanTemperatureC,
        meanWindSpeedMs,
        exposed: deposition.unit.exposure === 'exposed',
      });
      const openingScale = deposition.freshSnowCm.p50 > 0
        ? evolution.state.depthCm / deposition.freshSnowCm.p50
        : 1;
      return {
        unit: deposition.unit,
        baselineFrozenSweMm: deposition.baselineFrozenSweMm,
        depositedFrozenSweMm: deposition.depositedFrozenSweMm,
        freshSnowCm: deposition.freshSnowCm,
        openingSnowCm: {
          p10: deposition.freshSnowCm.p10 * openingScale * 0.85,
          p50: evolution.state.depthCm,
          p90: deposition.freshSnowCm.p90 * Math.min(1, openingScale * 1.1),
        },
        settlementCm: evolution.settlementCm,
        windCompactionOrLossCm: evolution.windCompactionOrLossCm,
        meltDepthCm: evolution.meltDepthCm,
        depositionFactor: deposition.depositionFactor,
        drift: assessDrift({
          windSpeedMs: meanWindSpeedMs,
          windFromDeg,
          exposure: deposition.unit.exposure,
          freshSnowDensityKgM3: evolution.state.densityKgM3,
        }),
      };
    });
    const summit = referencePoints.find(reference => reference.point.role === 'summit') ?? referencePoints[referencePoints.length - 1];
    const base = referencePoints.find(reference => reference.point.role === 'base') ?? referencePoints[0];
    const warnings = allHours.flatMap(hour => hour.snowfallResult?.diagnostics.warnings ?? []);
    const provenance = allHours.flatMap(hour => hour.provenance ? [hour.provenance] : []);
    const terrainRanges = {
      sheltered: terrainSummary(slopeForecasts, 'sheltered'),
      neutral: terrainSummary(slopeForecasts, 'neutral'),
      exposed: terrainSummary(slopeForecasts, 'exposed'),
    };
    const phaseConfidence = Math.min(base.phase.confidence, summit.phase.confidence);
    forecasts[date] = {
      generatedAt: new Date().toISOString(),
      openingTime: nextOpeningTime(date),
      referencePoints,
      slopeUnits: slopeForecasts,
      resortSummary: {
        freshSnowCm: summit.freshSnowCm,
        openingSnowCm: summit.openingSnowCm,
        frozenSweMm: summit.frozenSweMm,
        basePhase: base.phase,
        summitPhase: summit.phase,
      },
      terrainSummary: terrainRanges,
      management: {
        available: false,
        label: 'Natural-snow estimate over groomed terrain; grooming and snowmaking are not included.',
      },
      diagnostics: {
        dominantUncertainty: phaseConfidence < 0.55
          ? 'phase'
          : meanWindSpeedMs >= 12
            ? 'wind_redistribution'
            : 'qpf',
        modelsUsed: [...new Set(allHours.map(hour => hour.model))],
        fallbackMethods: [...new Set(warnings.map(warning => warning.replaceAll('_', ' ').toLowerCase()))],
        observationAgeHours: null,
        geometryQuality: referencePoints.every(reference => reference.point.geometrySource === 'curated') ? 'curated' : 'estimated',
        terrainResolution: 'sru',
        terrainCalibration: 'uncalibrated_mvp',
        productMode: comparisonOnly ? 'open_meteo_comparison' : 'physical_pipeline',
      },
      provenance,
    };
  }
  return forecasts;
}
