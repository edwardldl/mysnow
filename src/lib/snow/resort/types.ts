import type { ForecastProvenance, PhaseDistribution, Quantiles } from '../types';

export type ReferencePointRole = 'base' | 'mid_mountain' | 'summit';
export type SlopeManagement = 'natural' | 'groomed' | 'groomed_snowmaking';
export type WindExposureClass = 'sheltered' | 'neutral' | 'exposed';

export interface ResortReferencePoint {
  id: string;
  name: string;
  role: ReferencePointRole;
  latitude: number;
  longitude: number;
  elevationM: number;
  geometrySource: 'curated' | 'estimated';
}

export interface SlopeReferenceUnit {
  id: string;
  areaM2: number;
  centroidLat: number;
  centroidLon: number;
  minElevationM: number;
  meanElevationM: number;
  maxElevationM: number;
  meanSlopeDeg: number;
  meanAspectDeg: number;
  topographicPosition: number;
  windShelterByDirection: number[];
  curvature: number;
  canopyFraction: number;
  exposure: WindExposureClass;
  management: SlopeManagement;
  terrainSource: 'mvp_reference_classes' | 'dem';
}

export interface DriftAssessment {
  category:
    | 'drift_unlikely'
    | 'exposed_depletion_likely'
    | 'significant_lee_loading'
    | 'high_redistribution_uncertainty';
  loadingDirectionDeg: number | null;
  confidence: number;
}

export interface ReferencePointForecast {
  point: ResortReferencePoint;
  freshSnowCm: Quantiles;
  openingSnowCm: Quantiles;
  frozenSweMm: Quantiles;
  phase: PhaseDistribution;
  medianSlr: number | null;
}

export interface SlopeUnitForecast {
  unit: SlopeReferenceUnit;
  baselineFrozenSweMm: number;
  depositedFrozenSweMm: number;
  freshSnowCm: Quantiles;
  openingSnowCm: Quantiles;
  settlementCm: number;
  windCompactionOrLossCm: number;
  meltDepthCm: number;
  depositionFactor: number;
  drift: DriftAssessment;
}

export interface ResortSnowForecast {
  generatedAt: string;
  openingTime: string;
  referencePoints: ReferencePointForecast[];
  slopeUnits: SlopeUnitForecast[];
  resortSummary: {
    freshSnowCm: Quantiles;
    openingSnowCm: Quantiles;
    frozenSweMm: Quantiles;
    basePhase: PhaseDistribution;
    summitPhase: PhaseDistribution;
  };
  terrainSummary: Record<WindExposureClass, Quantiles>;
  management: {
    available: boolean;
    label: string;
  };
  diagnostics: {
    dominantUncertainty: 'qpf' | 'phase' | 'slr' | 'wind_redistribution' | 'settlement' | 'management';
    modelsUsed: string[];
    fallbackMethods: string[];
    observationAgeHours: number | null;
    geometryQuality: 'curated' | 'estimated';
    terrainResolution: 'sru';
    terrainCalibration: 'uncalibrated_mvp';
    productMode: 'physical_pipeline' | 'open_meteo_comparison';
  };
  provenance: ForecastProvenance[];
}
