// ============================================================
// ALGORITHM 1 — SIMPLE (Roebber Climatological SLR)
// ============================================================
//
// References:
//   Roebber, P.J., Bruening, S.L., Schultz, D.M., & Cortinas, J.V. (2003).
//   "Improving Snowfall Forecasting by Diagnosing Snow Density."
//   Weather and Forecasting, 18(2), 264–287.
//
//   Stull, R. (2011). "Wet-Bulb Temperature from Relative Humidity and
//   Air Temperature." Journal of Applied Meteorology and Climatology, 50.
//
// Open-Meteo ECMWF variables used:
//   surface: temperature_2m, dewpoint_2m, wind_speed_10m, precipitation
//
// ============================================================

import { SurfaceInputs, SimpleSLRResult, CalcStep, SnowType } from './types';
import { dewpointToRH, wetBulbStull, clamp } from './utils';

/**
 * Roebber piecewise temperature-to-SLR curve.
 *
 * Maps wet-bulb temperature (°C) to a base snow-to-liquid ratio using a
 * climatologically derived piecewise linear approximation:
 *
 *   Tw ≤ −18°C  →  20:1  (very cold, compact but low-density crystals)
 *   −18 to −10  →  15–20:1 (transitional)
 *   −10 to  −5  →  10–15:1 (plates / dendrites)
 *   −5  to   0  →   5–10:1 (columns / needles, near-melting)
 *   > 0°C       →   3–5:1  (wet snow / sleet)
 */
function roebberSLRCurve(Tw: number): number {
  if (Tw <= -18) return 20;
  if (Tw <= -10) return 15 + ((-10 - Tw) / 8) * 5;   // 15 → 20
  if (Tw <= -5)  return 10 + ((-5  - Tw) / 5) * 5;   // 10 → 15
  if (Tw <=  0)  return  5 + ((0   - Tw) / 5) * 5;   //  5 → 10
  return Math.max(3, 5 - Tw * 2);                     // near/above freezing
}

/**
 * Wind compaction penalty.
 *
 * Turbulent mixing and mechanical compaction during deposition reduce
 * snow depth. Penalty is proportional to wind speed, capped at 4 SLR units.
 *
 * @param wind_ms - Wind speed at 10 m (m/s)
 * @returns SLR units to subtract from base SLR
 */
function windCompactionPenalty(wind_ms: number): number {
  return Math.min(4, wind_ms / 10);
}

/**
 * Simple snowfall & SLR calculator (Roebber climatological method).
 *
 * Suitable for quick operational forecasts where pressure-level data
 * is unavailable or insufficient.
 *
 * @param inputs - Surface variables from Open-Meteo ECMWF API
 * @returns SimpleSLRResult with snowfall depth, SLR, snow type, and trace
 *
 * @example
 * const result = calculateSimple({
 *   T2m: -12, Td2m: -15, precip_mm: 5, wind_speed_10m: 3
 * });
 * console.log(`${result.snowfall_mm} mm of snow at ${result.slr}:1 SLR`);
 */
export function calculateSimple(inputs: SurfaceInputs): SimpleSLRResult {
  const { T2m, Td2m, precip_mm, wind_speed_10m } = inputs;
  const steps: CalcStep[] = [];

  // ── Step 1: Wet-bulb temperature ──────────────────────────────────────────
  const RH_sfc = dewpointToRH(T2m, Td2m);
  const Tw = wetBulbStull(T2m, RH_sfc);

  steps.push({
    label: 'Surface wet-bulb (Stull 2011)',
    value: `${Tw.toFixed(2)} °C`,
    detail: `T₂ₘ=${T2m}°C, Td=${Td2m}°C → RH=${RH_sfc.toFixed(0)}%`,
  });

  // ── Gate 1: Rain check ────────────────────────────────────────────────────
  // Tw > 1.5°C means the sub-cloud layer is too warm for snow to survive
  // descent to the surface (Stewart & King 1987 threshold).
  if (Tw > 1.5) {
    return {
      snowfall_mm: 0,
      slr: 0,
      snowType: 'rain',
      steps,
      warning: `Wet-bulb ${Tw.toFixed(1)}°C > 1.5°C — precipitation falls as rain`,
    };
  }

  // ── Gate 2: No precipitation ──────────────────────────────────────────────
  if (precip_mm <= 0) {
    return {
      snowfall_mm: 0,
      slr: 10,
      snowType: 'none',
      steps,
      warning: 'No precipitation (precip_mm ≤ 0)',
    };
  }

  // ── Step 2: Base SLR from Roebber T-curve ─────────────────────────────────
  const slr_base = roebberSLRCurve(Tw);
  steps.push({
    label: 'Base SLR (Roebber T-curve)',
    value: `${slr_base.toFixed(1)} : 1`,
    detail: `Piecewise linear interpolation from Tw=${Tw.toFixed(1)}°C`,
  });

  // ── Step 3: Wind compaction penalty ──────────────────────────────────────
  const wind_penalty = windCompactionPenalty(wind_speed_10m);
  steps.push({
    label: 'Wind compaction penalty',
    value: `−${wind_penalty.toFixed(2)}`,
    detail: `${wind_speed_10m} m/s at 10 m → −${wind_penalty.toFixed(2)} SLR units`,
  });

  // ── Step 4: Final SLR ─────────────────────────────────────────────────────
  const slr = Math.round(clamp(slr_base - wind_penalty, 3, 25) * 10) / 10;
  steps.push({
    label: 'Final SLR',
    value: `${slr.toFixed(1)} : 1`,
    detail: `${slr_base.toFixed(1)} (base) − ${wind_penalty.toFixed(2)} (wind) = ${slr.toFixed(1)}`,
  });

  // ── Step 5: Snowfall depth ────────────────────────────────────────────────
  const snowfall_mm = precip_mm * slr;
  steps.push({
    label: 'Snow water equivalent input',
    value: `${precip_mm.toFixed(2)} mm`,
    detail: 'From Open-Meteo precipitation field',
  });
  steps.push({
    label: 'Snowfall depth',
    value: `${snowfall_mm.toFixed(1)} mm  (${(snowfall_mm / 10).toFixed(1)} cm)`,
    detail: `${precip_mm.toFixed(2)} mm SWE × ${slr.toFixed(1)} SLR`,
  });

  // ── Snow type classification ──────────────────────────────────────────────
  const snowType: SnowType =
    slr >= 20 ? 'powder' :
    slr >= 15 ? 'light'  :
    slr >= 10 ? 'average':
    slr >=  7 ? 'dense'  : 'wet';

  return {
    snowfall_mm,
    slr,
    slr_base,
    wind_penalty,
    snowType,
    steps,
    Tw,
    RH_sfc,
  };
}
