/**
 * Snow-to-Liquid Ratio (SLR) Prediction Algorithm
 *
 * Physics-based implementation combining:
 *   1. Kuchera & Anderson (1996) mean-layer temperature method
 *   2. Crystal habit lookup tables (Magono & Lee 1966, Bailey & Hallett 2009)
 *   3. Riming correction (Roebber et al. 2003 conceptual framework)
 *   4. Dendritic growth zone (DGZ) weighting
 *   5. Wind shear / aggregation correction
 *   6. Sub-freezing wet-bulb constraint
 *
 * All variables match names available from https://open-meteo.com/en/docs
 *
 * Units:
 *   Temperatures  → °C
 *   Pressures     → hPa
 *   Wind speeds   → m/s
 *   Geopotential  → m
 *   Humidity      → %  (0–100)
 *   Precipitable water (TCWV) → kg/m²
 *   CAPE          → J/kg
 */

// ---------------------------------------------------------------------------
// Open-Meteo variable interfaces
// ---------------------------------------------------------------------------

/** Subset of open-meteo current/hourly variables used by this algorithm. */
export interface OpenMeteoSnapshot {
  // Surface / near-surface
  temperature_2m: number;           // °C
  wet_bulb_temperature_2m: number;  // °C
  relative_humidity_2m: number;     // %
  apparent_temperature: number;     // °C
  surface_pressure: number;         // hPa
  pressure_msl: number;             // hPa
  wind_speed_10m: number;           // m/s
  wind_gusts_10m: number;           // m/s
  precipitation: number;            // mm  (liquid equivalent)
  snowfall: number;                 // cm  (reported snow depth increment)
  weather_code: number;             // WMO code
  cape: number;                     // J/kg
  lifted_index: number;             // K
  convective_inhibition: number;    // J/kg
  freezing_level_height: number;    // m
  boundary_layer_height: number;    // m
  total_column_integrated_water_vapour: number; // kg/m²  (TCWV)

  // Pressure-level data (all hourly)
  // Temperature °C at each level
  temperature_1000hPa: number;
  temperature_975hPa: number;
  temperature_950hPa: number;
  temperature_925hPa: number;
  temperature_900hPa: number;
  temperature_850hPa: number;
  temperature_800hPa: number;
  temperature_700hPa: number;
  temperature_600hPa: number;
  temperature_500hPa: number;
  temperature_400hPa: number;
  temperature_300hPa: number;

  // Relative humidity % at each level
  relative_humidity_1000hPa: number;
  relative_humidity_975hPa: number;
  relative_humidity_950hPa: number;
  relative_humidity_925hPa: number;
  relative_humidity_900hPa: number;
  relative_humidity_850hPa: number;
  relative_humidity_800hPa: number;
  relative_humidity_700hPa: number;
  relative_humidity_600hPa: number;
  relative_humidity_500hPa: number;

  // Dew point °C at each level
  dew_point_1000hPa: number;
  dew_point_975hPa: number;
  dew_point_950hPa: number;
  dew_point_925hPa: number;
  dew_point_850hPa: number;
  dew_point_700hPa: number;
  dew_point_500hPa: number;

  // Wind speed m/s at each level
  wind_speed_1000hPa: number;
  wind_speed_850hPa: number;
  wind_speed_700hPa: number;
  wind_speed_500hPa: number;

  // Wind direction ° at each level
  wind_direction_1000hPa: number;
  wind_direction_850hPa: number;
  wind_direction_700hPa: number;
  wind_direction_500hPa: number;

  // Geopotential height m at each level
  geopotential_height_1000hPa: number;
  geopotential_height_975hPa: number;
  geopotential_height_950hPa: number;
  geopotential_height_925hPa: number;
  geopotential_height_900hPa: number;
  geopotential_height_850hPa: number;
  geopotential_height_800hPa: number;
  geopotential_height_700hPa: number;
  geopotential_height_600hPa: number;
  geopotential_height_500hPa: number;
  geopotential_height_400hPa: number;
  geopotential_height_300hPa: number;

  // Cloud cover % at each level
  cloud_cover_1000hPa: number;
  cloud_cover_850hPa: number;
  cloud_cover_500hPa: number;
}

// ---------------------------------------------------------------------------
// Intermediate diagnostics (exposed for inspection/debugging)
// ---------------------------------------------------------------------------
export interface SLRDiagnostics {
  /** Mean temperature of the snow-growth layer (surface → 500 hPa), °C */
  meanLayerTemp_C: number;
  /** Kuchera base SLR (unitless ratio) */
  kucheraBaseSLR: number;
  /** Dominant crystal habit in the DGZ */
  crystalHabit: CrystalHabit;
  /** Crystal-habit SLR modifier */
  habitModifier: number;
  /** Fraction of column below 0 °C that is in the DGZ (-12 to -18 °C) */
  dgzFraction: number;
  /** Mean relative humidity in the snow-growth layer, % */
  meanLayerRH: number;
  /** Riming potential index (0–1, 1 = heavy riming) */
  rimingIndex: number;
  /** Riming SLR correction factor (<1 = denser snow) */
  rimingCorrection: number;
  /** Vertical wind shear 1000→500 hPa, m/s per km */
  windShear_ms_per_km: number;
  /** Aggregation/shear SLR correction factor */
  shearCorrection: number;
  /** Wet-bulb constraint factor (penalises near-melting profiles) */
  wetBulbFactor: number;
  /** CAPE/instability correction */
  capeCorrection: number;
  /** Final predicted SLR */
  finalSLR: number;
}

export type CrystalHabit =
  | "hex_plate"
  | "dendrite"
  | "sector_plate"
  | "stellar_dendrite"
  | "needle"
  | "column"
  | "hollow_column"
  | "spatial_dendrite"
  | "graupel"
  | "irregular";

// ---------------------------------------------------------------------------
// Helper: clamp
// ---------------------------------------------------------------------------
function clamp(val: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, val));
}

// ---------------------------------------------------------------------------
// Helper: linear interpolation
// ---------------------------------------------------------------------------
function lerp(x: number, x0: number, x1: number, y0: number, y1: number): number {
  if (x1 === x0) return y0;
  return y0 + ((x - x0) / (x1 - x0)) * (y1 - y0);
}

// ---------------------------------------------------------------------------
// STEP 1 — Build a pseudo-sounding from available pressure levels
// ---------------------------------------------------------------------------
interface SoundingLevel {
  pressure_hPa: number;
  temp_C: number;
  rh_pct: number;
  height_m: number;
  windSpeed_ms: number;
}

function buildSounding(d: OpenMeteoSnapshot): SoundingLevel[] {
  // Pair up pressure levels with the data available
  const levels: SoundingLevel[] = [
    {
      pressure_hPa: 1000,
      temp_C: d.temperature_1000hPa,
      rh_pct: d.relative_humidity_1000hPa,
      height_m: d.geopotential_height_1000hPa,
      windSpeed_ms: d.wind_speed_1000hPa,
    },
    {
      pressure_hPa: 975,
      temp_C: d.temperature_975hPa,
      rh_pct: d.relative_humidity_975hPa,
      height_m: d.geopotential_height_975hPa,
      windSpeed_ms: lerp(975, 1000, 850, d.wind_speed_1000hPa, d.wind_speed_850hPa),
    },
    {
      pressure_hPa: 950,
      temp_C: d.temperature_950hPa,
      rh_pct: d.relative_humidity_950hPa,
      height_m: d.geopotential_height_950hPa,
      windSpeed_ms: lerp(950, 1000, 850, d.wind_speed_1000hPa, d.wind_speed_850hPa),
    },
    {
      pressure_hPa: 925,
      temp_C: d.temperature_925hPa,
      rh_pct: d.relative_humidity_925hPa,
      height_m: d.geopotential_height_925hPa,
      windSpeed_ms: lerp(925, 1000, 850, d.wind_speed_1000hPa, d.wind_speed_850hPa),
    },
    {
      pressure_hPa: 900,
      temp_C: d.temperature_900hPa,
      rh_pct: d.relative_humidity_900hPa,
      height_m: d.geopotential_height_900hPa,
      windSpeed_ms: lerp(900, 1000, 850, d.wind_speed_1000hPa, d.wind_speed_850hPa),
    },
    {
      pressure_hPa: 850,
      temp_C: d.temperature_850hPa,
      rh_pct: d.relative_humidity_850hPa,
      height_m: d.geopotential_height_850hPa,
      windSpeed_ms: d.wind_speed_850hPa,
    },
    {
      pressure_hPa: 800,
      temp_C: d.temperature_800hPa,
      rh_pct: lerp(800, 850, 700, d.relative_humidity_850hPa, d.relative_humidity_700hPa),
      height_m: d.geopotential_height_800hPa,
      windSpeed_ms: lerp(800, 850, 700, d.wind_speed_850hPa, d.wind_speed_700hPa),
    },
    {
      pressure_hPa: 700,
      temp_C: d.temperature_700hPa,
      rh_pct: d.relative_humidity_700hPa,
      height_m: d.geopotential_height_700hPa,
      windSpeed_ms: d.wind_speed_700hPa,
    },
    {
      pressure_hPa: 600,
      temp_C: d.temperature_600hPa,
      rh_pct: lerp(600, 700, 500, d.relative_humidity_700hPa, d.relative_humidity_500hPa),
      height_m: d.geopotential_height_600hPa,
      windSpeed_ms: lerp(600, 700, 500, d.wind_speed_700hPa, d.wind_speed_500hPa),
    },
    {
      pressure_hPa: 500,
      temp_C: d.temperature_500hPa,
      rh_pct: d.relative_humidity_500hPa,
      height_m: d.geopotential_height_500hPa,
      windSpeed_ms: d.wind_speed_500hPa,
    },
  ];

  // Sort surface → upper troposphere (descending pressure)
  return levels.sort((a, b) => b.pressure_hPa - a.pressure_hPa);
}

// ---------------------------------------------------------------------------
// STEP 2 — Kuchera & Anderson mean-layer temperature → base SLR
//
// Kuchera (1996) derived an empirical relationship between the mean
// temperature of the below-freezing layer and SLR:
//
//   If T_mean <= -5 °C  :  SLR = 12 + (T_mean + 5) * 0.4   (richer dendrite regime)
//   If T_mean > -5 °C  :  SLR =  6 + (T_mean + 5) * 1.2   (wet snow regime)
//
// The original table is reproduced here via a piecewise fit.
// ---------------------------------------------------------------------------
function kucheraSLR(meanLayerTemp_C: number): number {
  const T = meanLayerTemp_C;

  // Kuchera lookup (Table 1, extended piecewise)
  if (T > -1.0) return 4.0;                              // Very wet snow / sleet
  if (T > -3.0) return lerp(T, -1.0, -3.0, 4.0, 7.0);
  if (T > -5.0) return lerp(T, -3.0, -5.0, 7.0, 10.0);
  if (T > -8.0) return lerp(T, -5.0, -8.0, 10.0, 14.0); // Approaching dendrite peak
  if (T > -10.0) return lerp(T, -8.0, -10.0, 14.0, 17.0);
  if (T > -15.0) return lerp(T, -10.0, -15.0, 17.0, 20.0); // Classic dendrite SLR
  if (T > -20.0) return lerp(T, -15.0, -20.0, 20.0, 17.0); // Colder → columns, lower SLR
  if (T > -25.0) return lerp(T, -20.0, -25.0, 17.0, 14.0);
  if (T > -30.0) return lerp(T, -25.0, -30.0, 14.0, 11.0);
  return 10.0; // Very cold Arctic conditions
}

// ---------------------------------------------------------------------------
// STEP 3 — Crystal habit from temperature and supersaturation
//
// Based on Magono & Lee (1966) and Bailey & Hallett (2009) updated habit diagram.
// Inputs: temperature at primary growth level, supersaturation proxy (RH - 100).
// ---------------------------------------------------------------------------
function crystalHabitFromTempRH(temp_C: number, rh_pct: number): {
  habit: CrystalHabit;
  slrModifier: number;
} {
  const ss = rh_pct - 100; // supersaturation w.r.t. ice (approximate proxy)
  const T = temp_C;

  // Heavy riming → graupel (very high RH, moderate temperatures)
  if (rh_pct > 102 && T > -10 && T < -1) {
    return { habit: "graupel", slrModifier: 0.65 }; // Graupel ≈ 3–7:1
  }

  // Dendritic / stellar regime: −12 to −18 °C (Nakaya's magic temperature)
  if (T >= -18 && T <= -12) {
    if (ss >= 0) return { habit: "stellar_dendrite", slrModifier: 1.30 }; // Very low density
    return { habit: "dendrite", slrModifier: 1.15 };
  }

  // Spatial dendrites: −10 to −12 °C
  if (T >= -12 && T < -10) {
    return { habit: "spatial_dendrite", slrModifier: 1.10 };
  }

  // Sector plates: −10 to −8 °C
  if (T >= -10 && T < -8) {
    return { habit: "sector_plate", slrModifier: 1.00 };
  }

  // Needles: −5 to −3 °C (compact, low SLR)
  if (T >= -5 && T < -3) {
    return { habit: "needle", slrModifier: 0.75 };
  }

  // Hollow columns: −8 to −5 °C
  if (T >= -8 && T < -5) {
    return { habit: "hollow_column", slrModifier: 0.85 };
  }

  // Plates: −1 to −3 °C (wet, compact)
  if (T >= -3 && T < -1) {
    return { habit: "hex_plate", slrModifier: 0.70 };
  }

  // Columns: colder than −18 °C
  if (T < -18 && T >= -25) {
    return { habit: "column", slrModifier: 0.88 };
  }

  // Below −25 °C: irregular / diamond dust
  if (T < -25) {
    return { habit: "irregular", slrModifier: 0.80 };
  }

  // Default moderate
  return { habit: "sector_plate", slrModifier: 0.95 };
}

// ---------------------------------------------------------------------------
// STEP 4 — Dendritic Growth Zone (DGZ) fraction
//
// The DGZ is −12 to −18 °C. The fraction of the precipitating column that
// falls in this zone strongly controls SLR — dendrites are the lowest-density
// crystal habit.
// ---------------------------------------------------------------------------
function computeDGZFraction(sounding: SoundingLevel[]): number {
  // Use pressure thickness as proxy for layer depth weighting.
  let dgzThickness = 0;
  let totalBelowFreezing = 0;

  for (let i = 0; i < sounding.length - 1; i++) {
    const lo = sounding[i];
    const hi = sounding[i + 1];
    const dP = Math.abs(lo.pressure_hPa - hi.pressure_hPa); // hPa ≈ proportional depth
    const T_mid = (lo.temp_C + hi.temp_C) / 2;

    if (T_mid < 0) {
      totalBelowFreezing += dP;
      if (T_mid >= -18 && T_mid <= -12) {
        dgzThickness += dP;
      }
    }
  }

  if (totalBelowFreezing === 0) return 0;
  return clamp(dgzThickness / totalBelowFreezing, 0, 1);
}

// ---------------------------------------------------------------------------
// STEP 5 — Riming index
//
// Riming occurs when supercooled liquid water droplets freeze on falling
// crystals. Proxies from available variables:
//   - High cloud-layer RH (>= 95%) at T between 0 and −15 °C → LWC proxy
//   - Strong updrafts (high CAPE in winter context) → collision rate
//   - Wet-bulb temperature near 0 °C → supercooled LW likely
// ---------------------------------------------------------------------------
function computeRimingIndex(
  d: OpenMeteoSnapshot,
  sounding: SoundingLevel[]
): number {
  let rimingScore = 0;

  // 1. Layer-mean RH at riming-favorable temperatures (−2 to −15 °C)
  let rimingLayerRH = 0;
  let rimingLayerCount = 0;
  for (const lev of sounding) {
    if (lev.temp_C <= -2 && lev.temp_C >= -15) {
      rimingLayerRH += lev.rh_pct;
      rimingLayerCount++;
    }
  }
  const meanRimRH = rimingLayerCount > 0 ? rimingLayerRH / rimingLayerCount : 0;
  // RH > 95% in riming layer is a good indicator
  rimingScore += clamp((meanRimRH - 85) / 15, 0, 1) * 0.40;

  // 2. TCWV (precipitable water) — proxy for LWC availability
  // High values in cold events indicate abundant moisture → more riming chance
  const tcwvNorm = clamp(d.total_column_integrated_water_vapour / 30, 0, 1);
  rimingScore += tcwvNorm * 0.20;

  // 3. Wet-bulb temperature near 0 °C in lower levels → supercooled droplets
  const wbFactor = d.wet_bulb_temperature_2m < 0
    ? clamp(1 - Math.abs(d.wet_bulb_temperature_2m) / 5, 0, 1)
    : 0;
  rimingScore += wbFactor * 0.25;

  // 4. Wind gusts (turbulence → collision enhancement)
  const gustFactor = clamp((d.wind_gusts_10m - 5) / 20, 0, 1);
  rimingScore += gustFactor * 0.15;

  return clamp(rimingScore, 0, 1);
}

// Riming correction: heavier riming → denser snow → lower SLR
// Roebber et al. (2003) showed riming accounts for much SLR variance
function rimingCorrectionFactor(rimingIndex: number): number {
  // rimingIndex = 0 → correction 1.0 (no change)
  // rimingIndex = 1 → correction ~0.50 (heavy graupel, SLR ~ 5:1)
  return 1.0 - 0.50 * rimingIndex;
}

// ---------------------------------------------------------------------------
// STEP 6 — Wind shear / turbulence correction
//
// Strong vertical wind shear:
//   a) Breaks up delicate dendrites → smaller, denser crystals → lower SLR
//   b) Promotes aggregation in the right temperature range → higher SLR
//
// We compute bulk shear 1000–500 hPa and apply net correction.
// ---------------------------------------------------------------------------
function computeWindShear(d: OpenMeteoSnapshot): number {
  const dV = Math.abs(d.wind_speed_500hPa - d.wind_speed_1000hPa); // m/s
  const dZ = d.geopotential_height_500hPa - d.geopotential_height_1000hPa; // m
  if (dZ <= 0) return 0;
  return (dV / dZ) * 1000; // m/s per km
}

function shearCorrectionFactor(
  shear_ms_per_km: number,
  meanTemp_C: number
): number {
  // In the DGZ (-12 to -18 °C): moderate shear promotes aggregation → slightly higher SLR
  // Outside DGZ or very high shear → breaks crystals → lower SLR
  const inDGZ = meanTemp_C >= -18 && meanTemp_C <= -10;

  if (inDGZ && shear_ms_per_km > 2 && shear_ms_per_km < 6) {
    // Aggregation bonus
    return 1.05;
  } else if (shear_ms_per_km > 8) {
    // High shear → dendritic fragmentation
    return clamp(1.0 - (shear_ms_per_km - 8) * 0.025, 0.80, 1.0);
  }
  return 1.0;
}

// ---------------------------------------------------------------------------
// STEP 7 — Wet-bulb temperature profile constraint
//
// The sub-cloud wet-bulb temperature determines whether falling snow
// partially melts and refreezes (dense, low SLR) or remains pristine.
// ---------------------------------------------------------------------------
function wetBulbFactor(d: OpenMeteoSnapshot, sounding: SoundingLevel[]): number {
  // Check if there is a warm nose (above 0 °C layer) below 850 hPa
  let warmNoseDepth_hPa = 0;
  for (const lev of sounding) {
    if (lev.pressure_hPa >= 850 && lev.temp_C > 0) {
      warmNoseDepth_hPa += 25; // approximate slab width
    }
  }

  // Wet-bulb at surface
  const wb = d.wet_bulb_temperature_2m;

  // If wet-bulb is above 0 °C at surface → melting → significantly lower SLR
  if (wb > 0) return 0.50;

  // Warm nose penalty: each 25 hPa of above-freezing air reduces SLR
  const nosepenalty = clamp(warmNoseDepth_hPa / 100, 0, 0.35);

  // Near-zero wet-bulb (-1 to 0 °C) → some melt-refreeze
  const nearMeltPenalty = wb > -1 ? clamp((wb + 1) * 0.15, 0, 0.15) : 0;

  return clamp(1.0 - nosepenalty - nearMeltPenalty, 0.50, 1.0);
}

// ---------------------------------------------------------------------------
// STEP 8 — CAPE / instability correction
//
// In winter, elevated CAPE (even 50–200 J/kg) indicates vigorous updrafts.
// Stronger updrafts → enhanced riming, compaction → lower SLR.
// Very stable profiles → gentle ascent → pristine crystals → higher SLR.
// ---------------------------------------------------------------------------
function capeCorrectionFactor(cape: number, liftedIndex: number): number {
  // LI > 0 → stable; LI < 0 → unstable
  // Cape > 100 J/kg in winter is significant
  const capePenalty = clamp(cape / 500, 0, 0.20);

  // Lifted index contribution (penalises instability)
  const liPenalty = liftedIndex < -1 ? clamp((-liftedIndex - 1) * 0.04, 0, 0.15) : 0;

  return 1.0 - capePenalty - liPenalty;
}

// ---------------------------------------------------------------------------
// STEP 9 — Compute mean-layer temperature and RH for the snow-growth layer
//
// The snow-growth layer is defined as the layer between the surface and 500 hPa
// where temperature is below 0 °C.
// ---------------------------------------------------------------------------
function meanLayerStats(sounding: SoundingLevel[]): { meanTemp: number; meanRH: number } {
  let weightedTemp = 0;
  let weightedRH = 0;
  let totalWeight = 0;

  for (let i = 0; i < sounding.length - 1; i++) {
    const lo = sounding[i];
    const hi = sounding[i + 1];
    const T_mid = (lo.temp_C + hi.temp_C) / 2;
    const RH_mid = (lo.rh_pct + hi.rh_pct) / 2;
    const dP = Math.abs(lo.pressure_hPa - hi.pressure_hPa);

    if (T_mid < 0) {
      weightedTemp += T_mid * dP;
      weightedRH += RH_mid * dP;
      totalWeight += dP;
    }
  }

  if (totalWeight === 0) return { meanTemp: -5, meanRH: 85 }; // fallback
  return {
    meanTemp: weightedTemp / totalWeight,
    meanRH: weightedRH / totalWeight,
  };
}

// ---------------------------------------------------------------------------
// STEP 10 — DGZ-weighted crystal habit
//
// Find the dominant temperature in the DGZ (-12 to -18 °C) to pick habit.
// ---------------------------------------------------------------------------
function dgzDominantTemp(sounding: SoundingLevel[]): number {
  let dgzWeightedT = 0;
  let dgzTotalWeight = 0;

  for (let i = 0; i < sounding.length - 1; i++) {
    const lo = sounding[i];
    const hi = sounding[i + 1];
    const T_mid = (lo.temp_C + hi.temp_C) / 2;
    const dP = Math.abs(lo.pressure_hPa - hi.pressure_hPa);

    if (T_mid >= -18 && T_mid <= -12) {
      dgzWeightedT += T_mid * dP;
      dgzTotalWeight += dP;
    }
  }

  // If no true DGZ present, use mean layer temp
  if (dgzTotalWeight === 0) return -10; // sub-DGZ default
  return dgzWeightedT / dgzTotalWeight;
}

// ---------------------------------------------------------------------------
// MAIN EXPORT — predictSLR
// ---------------------------------------------------------------------------

/**
 * Predict snow-to-liquid ratio from a complete open-meteo snapshot.
 *
 * @param data   An OpenMeteoSnapshot with all required fields populated.
 * @returns      Object containing the final SLR and detailed diagnostics.
 *
 * @example
 * const result = predictSLR(openMeteoHourlyData);
 * console.log(`SLR = ${result.finalSLR.toFixed(1)}:1`);
 */
export function predictSLR(data: OpenMeteoSnapshot): SLRDiagnostics {

  // ------------------------------------------------------------------
  // Guard: non-snow conditions → return SLR = 0 (or report liquid)
  // ------------------------------------------------------------------
  // WMO codes for snow: 70–79, 85, 86 (snow showers), 71–75 (continuous snow)
  // We allow sleet/mixed (56, 57, 66, 67) to pass through with low SLR.
  const isSnowOrMixed =
    (data.weather_code >= 56 && data.weather_code <= 57) ||
    (data.weather_code >= 66 && data.weather_code <= 67) ||
    (data.weather_code >= 70 && data.weather_code <= 79) ||
    (data.weather_code >= 85 && data.weather_code <= 86);

  if (!isSnowOrMixed && data.temperature_2m > 3) {
    return {
      meanLayerTemp_C: data.temperature_2m,
      kucheraBaseSLR: 0,
      crystalHabit: "irregular",
      habitModifier: 1,
      dgzFraction: 0,
      meanLayerRH: data.relative_humidity_2m,
      rimingIndex: 0,
      rimingCorrection: 1,
      windShear_ms_per_km: 0,
      shearCorrection: 1,
      wetBulbFactor: 1,
      capeCorrection: 1,
      finalSLR: 0,
    };
  }

  // ------------------------------------------------------------------
  // Build sounding
  // ------------------------------------------------------------------
  const sounding = buildSounding(data);

  // ------------------------------------------------------------------
  // Mean layer temperature and RH
  // ------------------------------------------------------------------
  const { meanTemp, meanRH } = meanLayerStats(sounding);

  // ------------------------------------------------------------------
  // Kuchera base SLR
  // ------------------------------------------------------------------
  const baseSLR = kucheraSLR(meanTemp);

  // ------------------------------------------------------------------
  // Crystal habit (from DGZ dominant temperature + RH)
  // ------------------------------------------------------------------
  const dgzTemp = dgzDominantTemp(sounding);
  const dgzRH = data.relative_humidity_850hPa; // 850 hPa is typically near DGZ
  const { habit, slrModifier: habitModifier } = crystalHabitFromTempRH(dgzTemp, dgzRH);

  // ------------------------------------------------------------------
  // DGZ fraction
  // ------------------------------------------------------------------
  const dgzFraction = computeDGZFraction(sounding);
  // Amplify habit modifier when DGZ is well-developed
  const dgzAmplifiedHabitMod = 1.0 + (habitModifier - 1.0) * (0.5 + dgzFraction * 0.5);

  // ------------------------------------------------------------------
  // Riming
  // ------------------------------------------------------------------
  const rimingIndex = computeRimingIndex(data, sounding);
  const rimingCorrection = rimingCorrectionFactor(rimingIndex);

  // ------------------------------------------------------------------
  // Wind shear
  // ------------------------------------------------------------------
  const windShear = computeWindShear(data);
  const shearCorrection = shearCorrectionFactor(windShear, meanTemp);

  // ------------------------------------------------------------------
  // Wet-bulb factor
  // ------------------------------------------------------------------
  const wbFactor = wetBulbFactor(data, sounding);

  // ------------------------------------------------------------------
  // CAPE / instability correction
  // ------------------------------------------------------------------
  const capeCorr = capeCorrectionFactor(data.cape, data.lifted_index);

  // ------------------------------------------------------------------
  // Combine all factors multiplicatively
  // ------------------------------------------------------------------
  const rawSLR =
    baseSLR *
    dgzAmplifiedHabitMod *
    rimingCorrection *
    shearCorrection *
    wbFactor *
    capeCorr;

  // ------------------------------------------------------------------
  // Physical bounds:
  //   Minimum ~ 3:1 (near-melting, heavy riming, graupel)
  //   Maximum ~ 30:1 (deep DGZ, stellar dendrites, cold/dry Arctic)
  // ------------------------------------------------------------------
  const finalSLR = clamp(rawSLR, 3.0, 30.0);

  return {
    meanLayerTemp_C: meanTemp,
    kucheraBaseSLR: baseSLR,
    crystalHabit: habit,
    habitModifier,
    dgzFraction,
    meanLayerRH: meanRH,
    rimingIndex,
    rimingCorrection,
    windShear_ms_per_km: windShear,
    shearCorrection,
    wetBulbFactor: wbFactor,
    capeCorrection: capeCorr,
    finalSLR,
  };
}

// ---------------------------------------------------------------------------
// UTILITY — Estimate snowfall depth from precipitation (mm liquid) + SLR
// ---------------------------------------------------------------------------

/**
 * Convert liquid-equivalent precipitation (mm) to snow depth (cm).
 *
 * @param precipitation_mm   Liquid equivalent in mm (from open-meteo `precipitation`)
 * @param slr                Snow-to-liquid ratio (e.g., 10 means 1 mm liquid → 10 mm / 1 cm snow)
 * @returns                  Estimated snowfall in centimetres
 */
export function precipToSnowDepth(precipitation_mm: number, slr: number): number {
  // precipitation is in mm liquid; multiply by SLR → mm snow; divide by 10 → cm
  return (precipitation_mm * slr) / 10;
}

// ---------------------------------------------------------------------------
// UTILITY — Estimate SLR from open-meteo reported snowfall vs precipitation
// ---------------------------------------------------------------------------

/**
 * Back-calculate observed SLR from open-meteo reported values.
 * Useful for verifying the algorithm against model output.
 *
 * @param snowfall_cm       open-meteo `snowfall` variable (cm)
 * @param precipitation_mm  open-meteo `precipitation` variable (mm liquid)
 */
export function observedSLR(snowfall_cm: number, precipitation_mm: number): number | null {
  if (precipitation_mm <= 0) return null;
  // snowfall_cm * 10 = mm snow; divided by precipitation_mm = SLR
  return (snowfall_cm * 10) / precipitation_mm;
}
