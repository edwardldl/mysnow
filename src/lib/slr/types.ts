// ============================================================
// Open-Meteo ECMWF API — Snowfall & SLR Calculator Types
// Endpoint: https://api.open-meteo.com/v1/ecmwf
// ============================================================

export type PressureHPa = 1000 | 925 | 850 | 700 | 600 | 500 | 400 | 300;

export type SnowType = 'powder' | 'light' | 'average' | 'dense' | 'wet' | 'rain' | 'none';

export type CrystalHabit = 'Dendrite' | 'Plate' | 'Needle' | 'Column' | 'Compact' | 'Mixed';

/** One pressure level's worth of ECMWF variables */
export interface PressureLevel {
  /** hPa — one of the 8 discrete ECMWF levels */
  pressure: PressureHPa;
  /** °C — temperature at this pressure level */
  temp: number;
  /** % — relative_humidity at this pressure level */
  rh: number;
  /** m — geopotential_height; used to compute layer thickness ΔZ */
  height: number;
  /**
   * Pa/s — vertical_velocity (omega).
   * ECMWF IFS convention: NEGATIVE values = upward motion.
   */
  omega: number;
  /** m/s — wind_speed specifically at 700 hPa (crest-level compaction) */
  wind700?: number;
}

/** Surface-level variables from Open-Meteo ECMWF */
export interface SurfaceInputs {
  /** °C — temperature_2m */
  T2m: number;
  /** °C — dewpoint_2m */
  Td2m: number;
  /** mm liquid water equivalent — precipitation sum for the hour */
  precip_mm: number;
  /** m/s — wind_speed_10m (fallback when 700 hPa wind unavailable) */
  wind_speed_10m: number;
}

/** Full set of inputs for the sophisticated algorithm */
export interface ColumnInputs extends SurfaceInputs {
  /** Array of all 8 pressure levels, any order (sorted internally) */
  levels: PressureLevel[];
}

/** A single step in the calculation trace */
export interface CalcStep {
  label: string;
  value: string;
  detail?: string;
  color?: string;
}

/** Crystal habit breakdown for one inter-level layer */
export interface HabitLayerResult {
  /** Mid-layer temperature (°C) */
  T: string;
  /** Mid-layer relative humidity (%) */
  RH: string;
  /** Dominant crystal habit in this layer */
  habit: CrystalHabit;
  /** SLR contribution from this habit */
  habitSLR: string;
  /** Thickness-weighted integration weight */
  weight: string;
}

/** Output from either algorithm */
export interface SLRResult {
  /** mm — total snowfall depth */
  snowfall_mm: number;
  /** Snow-to-liquid ratio (e.g. 15 = 15:1) */
  slr: number;
  /** Descriptive snow type category */
  snowType: SnowType;
  /** Step-by-step calculation trace */
  steps: CalcStep[];
  /** Optional advisory / gate message */
  warning?: string;
}

/** Extended output from the sophisticated algorithm */
export interface SophisticatedSLRResult extends SLRResult {
  /** Pre-bounds column SLR before final clamp */
  columnSLR_pre: number;
  /** Omega (vertical velocity) riming multiplier */
  omegaFactor: number;
  /** 700 hPa wind compaction multiplier */
  windFactor: number;
  /** Warm-nose melt penalty (0–0.4) */
  meltPenalty: number;
  /** Surface temperature aggregation factor */
  sfcFactor: number;
  /** Per-layer crystal habit analysis */
  habitDetails: HabitLayerResult[];
  /** Diagnosed surface wet-bulb (°C) */
  Tw_sfc: number;
  /** Surface relative humidity (%) */
  RH_sfc: number;
}

/** Extended output from the simple algorithm */
export interface SimpleSLRResult extends SLRResult {
  /** Base SLR from Roebber T-curve before wind penalty */
  slr_base?: number;
  /** Wind compaction penalty subtracted from base SLR */
  wind_penalty?: number;
  /** Diagnosed wet-bulb temperature (°C) */
  Tw?: number;
  /** Surface relative humidity (%) */
  RH_sfc?: number;
}
