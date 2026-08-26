import type { Quantiles } from '../types';

export const REGIONAL_SLR_ARTIFACT_SCHEMA_VERSION = 1 as const;
export type RegionalSlrArtifactStatus = 'unvalidated' | 'validated' | 'production';
export type CalibratableSlrMethod = 'kuchera' | 'cobb_2011';
export type CalibrationHierarchyLevel = 'global' | 'climate' | 'region' | 'local';

export interface RegionalSlrNode {
  logBias: number;
  rawLogBias: number;
  residualStdLog: number;
  rawSampleCount: number;
  effectiveSampleSize: number;
  shrinkageWeight: number;
}

export interface RegionalSlrMethodModel {
  global: RegionalSlrNode;
  climate: Record<string, RegionalSlrNode>;
  region: Record<string, RegionalSlrNode>;
  local: Record<string, RegionalSlrNode>;
}

export interface RegionalSlrArtifact {
  schemaVersion: typeof REGIONAL_SLR_ARTIFACT_SCHEMA_VERSION;
  artifactId: string;
  artifactVersion: string;
  status: RegionalSlrArtifactStatus;
  generatedAt: string;
  trainedThrough: string;
  modelKind: 'hierarchical-log-residual-eb';
  regionIndex: { system: 'h3'; resolution: number };
  climateIndex: { system: 'h3'; resolution: number; assignments: Record<string, string> };
  bounds: {
    minCorrectionFactor: number;
    maxCorrectionFactor: number;
    minSlr: number;
    maxSlr: number;
  };
  training: {
    sourceLabel: string;
    sourceDigestSha256: string;
    acceptedEventCount: number;
    rejectedEventCount: number;
    rejectionCounts: Record<string, number>;
    stationCount: number;
    priorEffectiveSampleSize: number;
    minEffectiveSampleSize: number;
    leakageGroup: 'event_id';
    target: 'log(observed_slr / physical_baseline_slr)';
  };
  coverage: {
    qualityTier: 'unvalidated' | 'limited' | 'regional' | 'production';
    geographicNote: string;
    supportedMethods: CalibratableSlrMethod[];
  };
  methods: Partial<Record<CalibratableSlrMethod, RegionalSlrMethodModel>>;
}

export interface RegionalSlrContext {
  latitude: number;
  longitude: number;
  elevationM?: number | null;
  modelId?: string | null;
  siteId?: string | null;
  validTime?: string | null;
}

export interface RegionalSlrCalibrationDiagnostics {
  applied: boolean;
  reason:
    | 'applied'
    | 'disabled'
    | 'invalid_artifact'
    | 'unvalidated_artifact'
    | 'unsupported_method'
    | 'missing_context'
    | 'insufficient_coverage';
  artifactId: string | null;
  artifactVersion: string | null;
  schemaVersion: number | null;
  trainedThrough: string | null;
  hierarchyChain: CalibrationHierarchyLevel[];
  highestSpecificity: CalibrationHierarchyLevel | null;
  correctionFactor: number;
  logResidual: number;
  rawSampleCount: number;
  effectiveSampleSize: number;
  residualStdLog: number | null;
  qualityTier: RegionalSlrArtifact['coverage']['qualityTier'] | 'none';
  regionCell: string | null;
  climateRegime: string | null;
  slrQuantiles: Quantiles | null;
}

export interface RegionalSlrCalibrationResult {
  slr: number;
  quantiles: Quantiles | null;
  diagnostics: RegionalSlrCalibrationDiagnostics;
}
