/**
 * Shared snow-science domain types. These deliberately live outside of any
 * individual SLR implementation so API normalization and algorithms have a
 * single, explicit contract.
 */

export type SlrMethod =
  | 'fixed_10'
  | 'kuchera'
  | 'cobb_2011'
  | 'experimental_krc'
  | 'open_meteo_snowfall';

/** Keep the UI, processing pipeline, and fallbacks on one production default. */
export const DEFAULT_SLR_METHOD: SlrMethod = 'kuchera';

export const MIN_ACCUMULATING_SWE_MM = 0.01;

export interface Quantiles {
  p10: number;
  p50: number;
  p90: number;
}

export interface PhaseDistribution {
  snow: number;
  mixedRainSnow: number;
  rain: number;
  icePellets: number;
  freezingRain: number;
  expectedFrozenFraction: number;
  confidence: number;
}

export interface PressureLayer {
  pressureHpa: number;
  geopotentialHeightM: number | null;
  temperatureC: number | null;
  dewPointC: number | null;
  relativeHumidityWaterPct: number | null;
  relativeHumidityIcePct: number | null;
  /** Geometric vertical velocity in m/s, as returned by Open-Meteo. */
  verticalVelocityMs: number | null;
  windSpeedMs: number | null;
  windDirectionDeg: number | null;
  cloudCoverPct: number | null;
  /** True only when the pressure surface is physically above the site. */
  isAboveGround: boolean;
}

export interface SurfaceMeteorology {
  temperatureC: number | null;
  dewPointC: number | null;
  relativeHumidityPct: number | null;
  wetBulbTemperatureC: number | null;
  windSpeedMs: number | null;
  windDirectionDeg: number | null;
  surfacePressureHpa: number | null;
  stationElevationM: number | null;
}

export interface NormalizedProfile {
  levels: PressureLayer[];
  aboveGroundLevels: PressureLayer[];
  validLevelCount: number;
  surfacePressureHpa: number | null;
  stationElevationM: number | null;
}

export interface PhaseResult {
  snowFraction: number;
  rainFraction: number;
  iceFraction?: number;
  confidence: number;
  distribution: PhaseDistribution;
  source:
    | 'snowfall_water_equivalent'
    | 'precipitation_type'
    | 'wet_bulb_profile'
    | 'surface_fallback';
  diagnostics: {
    surfaceWetBulbC: number | null;
    warmNoseEnergyCM: number | null;
    refreezeEnergyCM: number | null;
  };
  warnings: SnowDiagnosticWarning[];
}

export type SnowDiagnosticWarning =
  | 'KUCHERA_FALLBACK'
  | 'MISSING_VERTICAL_VELOCITY'
  | 'MISSING_DEW_POINT_PROFILE'
  | 'SPARSE_PROFILE'
  | 'PHASE_DISAGREEMENT'
  | 'LARGE_WARM_NOSE'
  | 'MODEL_SNOWFALL_UNAVAILABLE';

export interface CobbLayerDiagnostic {
  pressureHpa: number;
  temperatureC: number;
  relativeHumidityIcePct: number;
  upwardVelocityMs: number;
  thicknessM: number;
  weight: number;
  layerSlr: number;
  contribution: number;
}

export interface SlrDiagnostics {
  validProfileLevels: number;
  sourceMethod: 'fixed_10' | 'kuchera' | 'cobb_2011' | 'open_meteo_snowfall';
  fallbackReason: string | null;
  sourceFreshSlr: number | null;
  finalFreshSlr: number | null;
  growthZoneLiftMs: number | null;
  dominantGrowthLayerHpa: number | null;
  modifiers: {
    melting: number;
    surfaceWind: number;
    shear: number;
    riming: number;
  };
  layers: CobbLayerDiagnostic[];
  warnings: SnowDiagnosticWarning[];
}

export interface QpfAdjustmentResult {
  rawPrecipitationMm: number;
  adjustedPrecipitationMm: number;
  multiplier: number;
  method: 'none' | 'terrain_calibrated';
  confidence: number;
}

export interface QpfDistribution {
  probabilityWet: number;
  amountMm: Quantiles;
  method: 'raw_model_envelope' | 'ensemble';
  confidence: number;
}

export interface SnowfallResult {
  precipitationMm: number;
  snowFraction: number;
  frozenSweMm: number;
  freshSlr: number | null;
  freshSlrQuantiles: Quantiles | null;
  freshSnowCm: number;
  freshSnowQuantilesCm: Quantiles;
  rainMm: number;
  phase: PhaseResult;
  method: SlrMethod;
  diagnostics: SlrDiagnostics;
  qpfAdjustment: QpfAdjustmentResult;
  qpfDistribution: QpfDistribution;
}

export interface SnowLayer {
  depositionTime: string;
  initialSweMm: number;
  currentSweMm: number;
  initialDensityKgM3: number;
  currentDensityKgM3: number;
  liquidWaterMm: number;
  ageHours: number;
  sourceMethod: SlrMethod;
}

export interface SnowpackStep {
  layers: SnowLayer[];
  depthCm: number;
  newSnowCm: number;
  addedSweMm: number;
  meltSweMm: number;
}

export interface NaturalSnowState {
  sweMm: number;
  depthCm: number;
  densityKgM3: number;
  liquidWaterMm: number;
  surfaceTemperatureC: number;
  ageHours: number;
  windCompactionIndex: number;
  crustProbability: number;
}

export interface EnsembleSnowfallResult {
  memberResults: SnowfallResult[];
  p10SnowCm: number;
  p25SnowCm: number;
  medianSnowCm: number;
  p75SnowCm: number;
  p90SnowCm: number;
  meanSnowCm: number;
  probabilitySnow: number;
  probabilitySlrAbove15: number;
  probabilitySnowAbove10Cm: number;
  missingMemberCount: number;
}

export interface ForecastProvenance {
  modelId: string;
  initializationTime: string | null;
  validTime: string;
  leadHours: number | null;
  nativeTimeStepMinutes: number | null;
  returnedTimeStepMinutes: number;
  requestedLatitude: number;
  requestedLongitude: number;
  returnedLatitude: number;
  returnedLongitude: number;
  requestedElevationM: number | null;
  modelGridElevationM: number | null;
  predictionVersion: string;
  calibrationVersion: string;
}

export interface SnowfallInput {
  time: string;
  precipitationMm: number | null;
  precipitationProbabilityPct?: number | null;
  snowfallCm: number | null;
  snowfallWaterEquivalentMm: number | null;
  precipitationType: number | null;
  weatherCode: number | null;
  surface: SurfaceMeteorology;
  profile: NormalizedProfile;
}
