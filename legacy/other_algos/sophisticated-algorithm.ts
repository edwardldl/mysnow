// ============================================================
// ALGORITHM 2 — SOPHISTICATED (Column-Integrated Crystal Habit)
// ============================================================
//
// References:
//   Bailey, M.P. & Hallett, J. (2009). "A Comprehensive Habit Diagram for
//   Atmospheric Ice Crystals." Journal of the Atmospheric Sciences, 66.
//
//   Stull, R. (2011). "Wet-Bulb Temperature from Relative Humidity and
//   Air Temperature." Journal of Applied Meteorology and Climatology, 50.
//
//   Stewart, R.E. & King, P. (1987). "Rain-snow boundaries over southern
//   Ontario." Monthly Weather Review, 115.
//
//   Roebber, P.J. et al. (2003). "Improving Snowfall Forecasting by
//   Diagnosing Snow Density." Weather and Forecasting, 18(2), 264–287.
//
// Open-Meteo ECMWF variables used:
//   surface : temperature_2m, dewpoint_2m, wind_speed_10m, precipitation
//   levels  : temperature, relative_humidity, geopotential_height,
//             vertical_velocity, wind_speed (700 hPa only)
//   Levels queried: 1000, 925, 850, 700, 600, 500, 400, 300 hPa
//
// ECMWF IFS omega sign convention:
//   Negative vertical_velocity (Pa/s) = UPWARD motion
//
// ============================================================

import {
  ColumnInputs,
  PressureLevel,
  SophisticatedSLRResult,
  CalcStep,
  HabitLayerResult,
  CrystalHabit,
  SnowType,
} from './types';
import { dewpointToRH, wetBulbStull, iceFraction, clamp } from './utils';

// ── Internal layer type ──────────────────────────────────────────────────────

interface AtmosphericLayer {
  /** Layer thickness (m) derived from geopotential_height difference */
  dZ: number;
  /** Mid-layer temperature (°C) */
  midT: number;
  /** Mid-layer relative humidity (%) */
  midRH: number;
  /** Mid-layer pressure (hPa) */
  midP: number;
  /** Mid-layer vertical velocity (Pa/s); negative = upward in ECMWF IFS */
  omega: number;
  /** Upper-boundary pressure level */
  upper: PressureLevel;
  /** Lower-boundary pressure level */
  lower: PressureLevel;
}

// ── Crystal habit SLR table (Bailey & Hallett 2009) ─────────────────────────

/**
 * Returns the base SLR for a given temperature and RH based on the
 * Bailey & Hallett (2009) habit diagram.
 *
 * Growth regimes:
 *   Dendritic   −10 to −18°C: optimal at high RH, produces the most
 *               voluminous crystals (up to 24:1 at RH ≥ 85%).
 *   Plate       −4  to −10°C: moderately low density (14:1 at RH > 80%).
 *   Needle      −3  to  −6°C: elongated, moderate SLR (~12:1).
 *   Column       0  to  −4°C: compact hexagonal columns (~8:1).
 *   Compact    < −18°C       : very cold, crystals are small & compact
 *               (SLR increases slightly with extreme cold: 10–15:1).
 */
function crystalHabitSLR(T: number, RH: number): { slr: number; habit: CrystalHabit } {
  const isDendritic = T >= -18 && T <= -10;
  const isPlate     = T >  -10 && T <= -4;
  const isNeedle    = T >   -6 && T <= -3;
  const isColumn    = T >   -4 && T <=  0;

  if (isDendritic) {
    // Peak dendritic growth at −14 to −16°C with RH near saturation
    const rh_bonus = RH > 85 ? ((RH - 85) / 15) * 6 : 0;
    const slr = RH > 85 ? 18 + rh_bonus : 13 + Math.max(0, (RH - 70) / 15) * 5;
    return { slr: Math.min(24, slr), habit: 'Dendrite' };
  }
  if (isPlate)  return { slr: RH > 80 ? 14 : 11, habit: 'Plate' };
  if (isNeedle) return { slr: 12, habit: 'Needle' };
  if (isColumn) return { slr: 8,  habit: 'Column' };
  if (T < -18)  return { slr: 10 + Math.min(5, (-T - 18) / 6), habit: 'Compact' };
  return { slr: 10, habit: 'Mixed' };
}

// ── Layer builder ─────────────────────────────────────────────────────────────

/**
 * Derives inter-level atmospheric layers from a sorted (descending pressure)
 * array of PressureLevel objects.
 *
 * Mid-layer values are simple arithmetic means of the bounding levels,
 * which is sufficient for the 8 ECMWF pressure levels used here.
 */
function buildLayers(sortedLevels: PressureLevel[]): AtmosphericLayer[] {
  const layers: AtmosphericLayer[] = [];
  for (let i = 0; i < sortedLevels.length - 1; i++) {
    const upper = sortedLevels[i];
    const lower = sortedLevels[i + 1];
    layers.push({
      dZ:    Math.abs(upper.height - lower.height),
      midT:  (upper.temp  + lower.temp)  / 2,
      midRH: (upper.rh    + lower.rh)    / 2,
      midP:  (upper.pressure + lower.pressure) / 2,
      omega: (upper.omega + lower.omega) / 2,
      upper,
      lower,
    });
  }
  return layers;
}

// ── Phase implementations ─────────────────────────────────────────────────────

/**
 * Phase 3: Column-integrated crystal habit SLR.
 *
 * For each atmospheric layer:
 *   weight = ΔZ × ice_fraction(midT) × (midRH / 100)
 *
 * Layers in the liquid-water or mixed-phase zone below 0°C still contribute
 * via the ice_fraction function, which ramps from 1 (at 0°C) to 0 (at 2°C).
 *
 * The column SLR is the weighted mean of all layer habit SLRs.
 *
 * @returns columnSLR and per-layer habit breakdown
 */
function integrateColumnSLR(layers: AtmosphericLayer[]): {
  columnSLR: number;
  habitDetails: HabitLayerResult[];
} {
  let totalWeight  = 0;
  let weightedSLR  = 0;

  const habitDetails: HabitLayerResult[] = layers.map(layer => {
    const frac = iceFraction(layer.midT);
    const { slr: habitSLR, habit } = crystalHabitSLR(layer.midT, layer.midRH);
    const weight = frac > 0.1
      ? layer.dZ * frac * (layer.midRH / 100)
      : layer.dZ * 0.01; // nominal contribution from below-freezing layers

    totalWeight  += weight;
    weightedSLR  += habitSLR * weight;

    return {
      T:        layer.midT.toFixed(1),
      RH:       layer.midRH.toFixed(0),
      habit,
      habitSLR: habitSLR.toFixed(1),
      weight:   weight.toFixed(0),
    };
  });

  const columnSLR = totalWeight > 0 ? weightedSLR / totalWeight : 10;
  return { columnSLR, habitDetails };
}

/**
 * Phase 4: Omega (vertical velocity) riming factor.
 *
 * ECMWF IFS convention: negative ω = upward motion.
 *
 * Physical rationale:
 *   - Strong updrafts (large |ω|) → supercooled liquid water is abundant
 *     → ice crystals collide with and collect droplets → riming → graupel
 *     → denser snow, LOWER SLR.
 *   - Gentle synoptic ascent (small |ω|) → time for pristine dendrite growth
 *     → lower density → HIGHER SLR.
 *
 * @param layers - All atmospheric layers (ω weighted by layer thickness)
 * @returns Multiplicative factor to apply to column SLR
 */
function omegaRimingFactor(layers: AtmosphericLayer[]): {
  factor: number;
  meanLift: number;
} {
  let liftScore      = 0;
  let totalDZ        = 0;

  for (const layer of layers) {
    // Only count layers with genuine upward motion (negative ω in ECMWF IFS)
    if (layer.omega < 0) {
      liftScore += Math.abs(layer.omega) * layer.dZ;
      totalDZ   += layer.dZ;
    }
  }

  const meanLift = totalDZ > 0 ? liftScore / totalDZ : 0;

  let factor = 1.0;
  if      (meanLift > 0.3)  factor = 0.85; // vigorous convective uplift → riming
  else if (meanLift > 0.1)  factor = 0.93; // moderate lift
  else if (meanLift < 0.02) factor = 1.08; // gentle synoptic ascent → pristine dendrites

  return { factor, meanLift };
}

/**
 * Phase 5: 700 hPa wind compaction factor.
 *
 * Crest-level winds (700 hPa is representative of mountain-top / upper
 * boundary-layer flow) mechanically compact snow as it falls and settles.
 *
 * @param wind700_ms - Wind speed at 700 hPa (m/s)
 * @returns Multiplicative factor to apply to SLR
 */
function wind700CompactionFactor(wind700_ms: number): number {
  if      (wind700_ms > 25) return 0.72;
  else if (wind700_ms > 15) return 0.82;
  else if (wind700_ms >  8) return 0.92;
  return 1.0;
}

/**
 * Phase 6: Warm-nose melt penalty.
 *
 * A "warm nose" is a layer of above-freezing air between the cloud base
 * and the surface. Snow partially melts, then may refreeze, producing
 * wet or ice-pellet precipitation that has a much lower SLR.
 *
 * @param sortedLevels - All pressure levels (sorted descending pressure)
 * @returns Fractional penalty [0, 0.4] to subtract from SLR multiplier
 */
function warmNosePenalty(sortedLevels: PressureLevel[]): {
  penalty: number;
  warmLayerCount: number;
} {
  // Only consider levels between 1000 hPa (near-surface) and 700 hPa
  const warmLevels = sortedLevels.filter(
    l => l.pressure >= 700 && l.pressure <= 1000 && l.temp > 0
  );

  if (warmLevels.length === 0) return { penalty: 0, warmLayerCount: 0 };

  // Estimate melt depth from geopotential heights
  const meltDepth = warmLevels.reduce((sum, l) => {
    const idx = sortedLevels.indexOf(l);
    const dZ  = idx > 0
      ? Math.abs(sortedLevels[idx].height - sortedLevels[idx - 1].height)
      : 100;
    return sum + dZ;
  }, 0);

  return {
    penalty:        clamp(meltDepth / 3000, 0, 0.4),
    warmLayerCount: warmLevels.length,
  };
}

/**
 * Phase 7: Surface temperature aggregation factor.
 *
 * Very cold surface temperatures promote crystal aggregation (snowflakes
 * clumping together → bulkier, lower-density snowpack).
 * Near-freezing temperatures produce wet, dense snow.
 */
function surfaceTemperatureFactor(Tw_sfc: number): number {
  if      (Tw_sfc < -10) return 1.12;
  else if (Tw_sfc <  -5) return 1.06;
  else if (Tw_sfc <   0) return 1.00;
  return 0.88; // above-freezing wet-bulb, but below the rain gate
}

// ── Main export ───────────────────────────────────────────────────────────────

/**
 * Sophisticated snowfall & SLR calculator.
 *
 * Integrates crystal habit growth physics across the full atmospheric column
 * using 8 ECMWF pressure levels, then applies a cascade of physically-based
 * correction factors for vertical motion, wind compaction, warm-nose melting,
 * and surface temperature.
 *
 * Open-Meteo API variables required (hourly, ECMWF model):
 *   Surface:  temperature_2m, dewpoint_2m, wind_speed_10m, precipitation
 *   Levels:   temperature_{P}hPa, relative_humidity_{P}hPa,
 *             geopotential_height_{P}hPa, vertical_velocity_{P}hPa
 *             wind_speed_700hPa
 *   Where P ∈ {1000, 925, 850, 700, 600, 500, 400, 300}
 *
 * @param inputs - Surface variables + all 8 pressure levels
 * @returns SophisticatedSLRResult with full breakdown
 *
 * @example
 * const result = calculateSophisticated({
 *   T2m: -12, Td2m: -15, precip_mm: 5, wind_speed_10m: 3,
 *   levels: ecmwfLevels,
 * });
 * console.log(`${(result.snowfall_mm / 10).toFixed(1)} cm at ${result.slr}:1`);
 */
export function calculateSophisticated(inputs: ColumnInputs): SophisticatedSLRResult {
  const { T2m, Td2m, precip_mm, wind_speed_10m, levels } = inputs;
  const steps: CalcStep[] = [];

  // ── Phase 1: Surface viability ────────────────────────────────────────────
  const RH_sfc  = dewpointToRH(T2m, Td2m);
  const Tw_sfc  = wetBulbStull(T2m, RH_sfc);

  steps.push({
    label: 'Surface wet-bulb (Stull 2011)',
    value: `${Tw_sfc.toFixed(2)} °C`,
    detail: `T₂ₘ=${T2m}°C, Td=${Td2m}°C → RH=${RH_sfc.toFixed(0)}%`,
    color: Tw_sfc < 0 ? 'var(--cold)' : 'var(--warm)',
  });

  if (Tw_sfc > 2.5) {
    return {
      snowfall_mm: 0, slr: 0, columnSLR_pre: 0,
      omegaFactor: 1, windFactor: 1, meltPenalty: 0, sfcFactor: 1,
      snowType: 'rain', steps, habitDetails: [], Tw_sfc, RH_sfc,
      warning: `Surface wet-bulb ${Tw_sfc.toFixed(1)}°C > 2.5°C — precipitation falls as rain`,
    };
  }

  if (precip_mm <= 0) {
    return {
      snowfall_mm: 0, slr: 10, columnSLR_pre: 10,
      omegaFactor: 1, windFactor: 1, meltPenalty: 0, sfcFactor: 1,
      snowType: 'none', steps, habitDetails: [], Tw_sfc, RH_sfc,
      warning: 'No precipitation (precip_mm ≤ 0)',
    };
  }

  // ── Phase 2: Build atmospheric layers ────────────────────────────────────
  // Sort pressure levels descending (1000 → 300 hPa)
  const sortedLevels = [...levels].sort((a, b) => b.pressure - a.pressure);
  const layers = buildLayers(sortedLevels);

  // ── Phase 3: Column crystal habit integration ─────────────────────────────
  const { columnSLR: rawColumnSLR, habitDetails } = integrateColumnSLR(layers);
  let columnSLR = rawColumnSLR;

  steps.push({
    label: 'Column-integrated crystal habit SLR',
    value: `${columnSLR.toFixed(2)} : 1`,
    detail: `Weighted mean over ${layers.length} layers (Bailey & Hallett 2009)`,
    color: 'var(--accent2)',
  });

  // ── Phase 4: Omega riming factor ──────────────────────────────────────────
  const { factor: omegaFactor, meanLift } = omegaRimingFactor(layers);
  columnSLR *= omegaFactor;

  steps.push({
    label: 'Omega riming correction (ECMWF IFS: −ω = up)',
    value: `× ${omegaFactor.toFixed(2)}`,
    detail: `Mean |ω| (upward layers) = ${meanLift.toFixed(3)} Pa/s`,
    color: meanLift > 0.2 ? 'var(--amber)' : 'var(--green)',
  });

  // ── Phase 5: 700 hPa wind compaction ────────────────────────────────────
  const lv700    = levels.find(l => l.pressure === 700);
  const wind700  = lv700?.wind700 ?? wind_speed_10m;
  const windFactor = wind700CompactionFactor(wind700);
  columnSLR *= windFactor;

  steps.push({
    label: '700 hPa wind compaction',
    value: `× ${windFactor.toFixed(2)}`,
    detail: `${wind700.toFixed(0)} m/s at 700 hPa`,
    color: wind700 > 20 ? 'var(--amber)' : 'var(--text)',
  });

  // ── Phase 6: Warm-nose melt penalty ──────────────────────────────────────
  const { penalty: meltPenalty, warmLayerCount } = warmNosePenalty(sortedLevels);
  if (meltPenalty > 0) {
    columnSLR *= (1 - meltPenalty);
    steps.push({
      label: 'Warm-nose melt penalty',
      value: `−${(meltPenalty * 100).toFixed(0)}%`,
      detail: `${warmLayerCount} above-freezing level(s) below 700 hPa`,
      color: 'var(--warm)',
    });
  }

  // ── Phase 7: Surface temperature aggregation factor ───────────────────────
  const sfcFactor = surfaceTemperatureFactor(Tw_sfc);
  columnSLR *= sfcFactor;

  steps.push({
    label: 'Surface temperature aggregation',
    value: `× ${sfcFactor.toFixed(2)}`,
    detail: `Tw_sfc = ${Tw_sfc.toFixed(1)}°C`,
    color: Tw_sfc < -5 ? 'var(--cold)' : 'var(--text)',
  });

  // ── Phase 8: Bounds & final output ───────────────────────────────────────
  // SLR physically cannot exceed ~30:1 (ultra-low-density Arctic snow) or
  // fall below 3:1 (saturated wet snow / refrozen sleet).
  const columnSLR_pre = columnSLR;
  const slr = Math.round(clamp(columnSLR, 3, 30) * 10) / 10;
  const snowfall_mm = precip_mm * slr;

  steps.push({
    label: 'Final SLR (bounded [3, 30])',
    value: `${slr.toFixed(1)} : 1`,
    detail: `Pre-bound: ${columnSLR_pre.toFixed(2)}`,
    color: 'var(--accent2)',
  });
  steps.push({
    label: 'Snowfall depth',
    value: `${snowfall_mm.toFixed(1)} mm  (${(snowfall_mm / 10).toFixed(1)} cm)`,
    detail: `${precip_mm.toFixed(2)} mm SWE × ${slr.toFixed(1)} SLR`,
    color: 'var(--green)',
  });

  const snowType: SnowType =
    slr >= 20 ? 'powder' :
    slr >= 15 ? 'light'  :
    slr >= 10 ? 'average':
    slr >=  7 ? 'dense'  : 'wet';

  return {
    snowfall_mm,
    slr,
    columnSLR_pre,
    omegaFactor,
    windFactor,
    meltPenalty,
    sfcFactor,
    snowType,
    steps,
    habitDetails,
    Tw_sfc,
    RH_sfc,
  };
}
