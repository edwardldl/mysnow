import { latLngToCell } from 'h3-js';
import type { SlrMethod } from '../types';
import {
  REGIONAL_SLR_ARTIFACT_SCHEMA_VERSION,
  type CalibratableSlrMethod,
  type CalibrationHierarchyLevel,
  type RegionalSlrArtifact,
  type RegionalSlrCalibrationDiagnostics,
  type RegionalSlrCalibrationResult,
  type RegionalSlrContext,
  type RegionalSlrMethodModel,
  type RegionalSlrNode,
} from './types';

const NORMAL_P10_Z = 1.2815515655446004;

export interface ApplyRegionalSlrCalibrationOptions {
  enabled: boolean;
  allowUnvalidated?: boolean;
  artifact: unknown;
  context?: RegionalSlrContext | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function finiteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function validNode(value: unknown): value is RegionalSlrNode {
  if (!isRecord(value)) return false;
  return finiteNumber(value.logBias)
    && finiteNumber(value.rawLogBias)
    && finiteNumber(value.residualStdLog) && value.residualStdLog >= 0
    && Number.isInteger(value.rawSampleCount) && (value.rawSampleCount as number) >= 0
    && finiteNumber(value.effectiveSampleSize) && value.effectiveSampleSize >= 0
    && finiteNumber(value.shrinkageWeight) && value.shrinkageWeight >= 0 && value.shrinkageWeight <= 1;
}

function validNodeMap(value: unknown): value is Record<string, RegionalSlrNode> {
  return isRecord(value) && Object.values(value).every(validNode);
}

function validMethod(value: unknown): value is RegionalSlrMethodModel {
  return isRecord(value) && validNode(value.global) && validNodeMap(value.climate)
    && validNodeMap(value.region) && validNodeMap(value.local);
}

export function validateRegionalSlrArtifact(value: unknown): value is RegionalSlrArtifact {
  if (!isRecord(value)
    || value.schemaVersion !== REGIONAL_SLR_ARTIFACT_SCHEMA_VERSION
    || typeof value.artifactId !== 'string'
    || typeof value.artifactVersion !== 'string'
    || !['unvalidated', 'validated', 'production'].includes(String(value.status))
    || typeof value.generatedAt !== 'string'
    || typeof value.trainedThrough !== 'string'
    || value.modelKind !== 'hierarchical-log-residual-eb'
    || !isRecord(value.regionIndex) || value.regionIndex.system !== 'h3' || !Number.isInteger(value.regionIndex.resolution)
    || !isRecord(value.climateIndex) || value.climateIndex.system !== 'h3' || !Number.isInteger(value.climateIndex.resolution)
    || !isRecord(value.climateIndex.assignments) || !Object.values(value.climateIndex.assignments).every(item => typeof item === 'string')
    || !isRecord(value.bounds)
    || !finiteNumber(value.bounds.minCorrectionFactor) || !finiteNumber(value.bounds.maxCorrectionFactor)
    || !finiteNumber(value.bounds.minSlr) || !finiteNumber(value.bounds.maxSlr)
    || value.bounds.minCorrectionFactor <= 0 || value.bounds.minCorrectionFactor > value.bounds.maxCorrectionFactor
    || value.bounds.minSlr <= 0 || value.bounds.minSlr > value.bounds.maxSlr
    || !isRecord(value.training) || !finiteNumber(value.training.minEffectiveSampleSize)
    || !isRecord(value.coverage) || !Array.isArray(value.coverage.supportedMethods)
    || !isRecord(value.methods)) return false;
  return Object.entries(value.methods).every(([method, model]) =>
    (method === 'kuchera' || method === 'cobb_2011') && validMethod(model));
}

export function regionalSlrIdentityDiagnostics(
  reason: RegionalSlrCalibrationDiagnostics['reason'] = 'disabled',
  artifact: RegionalSlrArtifact | null = null,
): RegionalSlrCalibrationDiagnostics {
  return {
    applied: false,
    reason,
    artifactId: artifact?.artifactId ?? null,
    artifactVersion: artifact?.artifactVersion ?? null,
    schemaVersion: artifact?.schemaVersion ?? null,
    trainedThrough: artifact?.trainedThrough ?? null,
    hierarchyChain: [],
    highestSpecificity: null,
    correctionFactor: 1,
    logResidual: 0,
    rawSampleCount: 0,
    effectiveSampleSize: 0,
    residualStdLog: null,
    qualityTier: artifact?.coverage.qualityTier ?? 'none',
    regionCell: null,
    climateRegime: null,
    slrQuantiles: null,
  };
}

function calibratableMethod(method: SlrMethod): method is CalibratableSlrMethod {
  return method === 'kuchera' || method === 'cobb_2011';
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

/** Apply an artifact after physical SLR selection; every failure returns identity. */
export function applyRegionalSlrCalibration(
  physicalSlr: number,
  method: SlrMethod,
  options: ApplyRegionalSlrCalibrationOptions,
): RegionalSlrCalibrationResult {
  if (!options.enabled) return { slr: physicalSlr, quantiles: null, diagnostics: regionalSlrIdentityDiagnostics('disabled', null) };
  if (!validateRegionalSlrArtifact(options.artifact)) {
    return { slr: physicalSlr, quantiles: null, diagnostics: regionalSlrIdentityDiagnostics('invalid_artifact', null) };
  }
  const artifact = options.artifact;
  if (artifact.status === 'unvalidated' && !options.allowUnvalidated) {
    return { slr: physicalSlr, quantiles: null, diagnostics: regionalSlrIdentityDiagnostics('unvalidated_artifact', artifact) };
  }
  if (!calibratableMethod(method) || !artifact.coverage.supportedMethods.includes(method)) {
    return { slr: physicalSlr, quantiles: null, diagnostics: regionalSlrIdentityDiagnostics('unsupported_method', artifact) };
  }
  const context = options.context;
  if (!context || !Number.isFinite(context.latitude) || !Number.isFinite(context.longitude)
    || context.latitude < -90 || context.latitude > 90 || context.longitude < -180 || context.longitude > 180) {
    return { slr: physicalSlr, quantiles: null, diagnostics: regionalSlrIdentityDiagnostics('missing_context', artifact) };
  }
  const model = artifact.methods[method];
  if (!model || model.global.effectiveSampleSize < artifact.training.minEffectiveSampleSize) {
    return { slr: physicalSlr, quantiles: null, diagnostics: regionalSlrIdentityDiagnostics('insufficient_coverage', artifact) };
  }

  let regionCell: string;
  let climateCell: string;
  try {
    regionCell = latLngToCell(context.latitude, context.longitude, artifact.regionIndex.resolution);
    climateCell = latLngToCell(context.latitude, context.longitude, artifact.climateIndex.resolution);
  } catch {
    return { slr: physicalSlr, quantiles: null, diagnostics: regionalSlrIdentityDiagnostics('missing_context', artifact) };
  }
  const climateRegime = artifact.climateIndex.assignments[climateCell] ?? null;
  const candidates: Array<[CalibrationHierarchyLevel, RegionalSlrNode | undefined]> = [
    ['global', model.global],
    ['climate', climateRegime ? model.climate[climateRegime] : undefined],
    ['region', model.region[regionCell]],
    ['local', context.siteId ? model.local[context.siteId] : undefined],
  ];
  const used: Array<[CalibrationHierarchyLevel, RegionalSlrNode]> = [];
  for (const [level, node] of candidates) {
    if (!node || node.effectiveSampleSize < artifact.training.minEffectiveSampleSize) break;
    used.push([level, node]);
  }
  if (used.length === 0) {
    return { slr: physicalSlr, quantiles: null, diagnostics: regionalSlrIdentityDiagnostics('insufficient_coverage', artifact) };
  }
  const rawLogResidual = used.reduce((sum, [, node]) => sum + node.logBias, 0);
  const minLog = Math.log(artifact.bounds.minCorrectionFactor);
  const maxLog = Math.log(artifact.bounds.maxCorrectionFactor);
  const logResidual = clamp(rawLogResidual, minLog, maxLog);
  const correctionFactor = Math.exp(logResidual);
  const slr = clamp(physicalSlr * correctionFactor, artifact.bounds.minSlr, artifact.bounds.maxSlr);
  const [highestSpecificity, highestNode] = used[used.length - 1];
  const p10 = clamp(physicalSlr * Math.exp(logResidual - NORMAL_P10_Z * highestNode.residualStdLog), artifact.bounds.minSlr, artifact.bounds.maxSlr);
  const p90 = clamp(physicalSlr * Math.exp(logResidual + NORMAL_P10_Z * highestNode.residualStdLog), artifact.bounds.minSlr, artifact.bounds.maxSlr);
  const quantiles = { p10: Math.min(p10, slr), p50: slr, p90: Math.max(p90, slr) };
  const diagnostics: RegionalSlrCalibrationDiagnostics = {
    ...regionalSlrIdentityDiagnostics('applied', artifact),
    applied: true,
    hierarchyChain: used.map(([level]) => level),
    highestSpecificity,
    correctionFactor,
    logResidual,
    rawSampleCount: highestNode.rawSampleCount,
    effectiveSampleSize: highestNode.effectiveSampleSize,
    residualStdLog: highestNode.residualStdLog,
    regionCell,
    climateRegime,
    slrQuantiles: quantiles,
  };
  return { slr, quantiles, diagnostics };
}
