export interface PressureLayer {
    pressure: number;
    temp: number;
    rh: number;
    gz: number;
    omega: number;
    wind_speed: number;
}

export interface OpenMeteoHour {
    time: string;
    temperature_2m: number;
    dew_point_2m: number | null;
    wind_speed_10m: number | null;
    precipitation: number;
    snowfall: number | null;
    relative_humidity_2m: number | null;
    wet_bulb_temperature_2m: number | null;
    specific_humidity_2m: number | null;
    pressure_msl: number | null;
    soil_temperature_0cm: number | null;
    snow_depth: number | null;
    layers: PressureLayer[];
}

export interface SLROutput {
    slr: number | null;
    snow_cm: number;
    method: string;
    isSnow: boolean;
    qpf_corrected?: number;
}

// ── Shared helpers ────────────────────────────────────────────────────────────

/** August-Roche-Magnus approximation for surface relative humidity. */
function calculateRelativeHumidity(T: number, Td: number): number {
    const e  = Math.exp((17.625 * Td) / (243.04 + Td));
    const es = Math.exp((17.625 * T)  / (243.04 + T));
    return 100.0 * (e / es);
}

/** Wet-Bulb Temperature (°C) — Stull (2011) algebraic formula. */
function calculateWetBulb(T: number, RH: number): number {
    return T * Math.atan(0.151977 * Math.pow(RH + 8.313659, 0.5)) +
           Math.atan(T + RH) -
           Math.atan(RH - 1.676331) +
           0.00391838 * Math.pow(RH, 1.5) * Math.atan(0.023101 * RH) -
           4.686035;
}

/** Clamp helper. */
function clamp(v: number, min: number, max: number): number {
    return Math.max(min, Math.min(max, v));
}

/** Resolve surface RH and wet-bulb from an hourly point. */
function surfaceWetBulb(hour: OpenMeteoHour): { RH: number; Tw: number } {
    let RH = hour.relative_humidity_2m;
    if (RH == null) {
        RH = hour.dew_point_2m != null
            ? calculateRelativeHumidity(hour.temperature_2m, hour.dew_point_2m)
            : 70;
    }
    const Tw = hour.wet_bulb_temperature_2m ?? calculateWetBulb(hour.temperature_2m, RH);
    return { RH, Tw };
}

// ── Physics-based kinematic algorithm (Cobb + Kuchera) ───────────────────────

/** Idealized layer SLR lookup table — used by the kinematic Cobb subroutine. */
function getIdealizedLayerSLR(T: number): number {
    if (T >  1)                return  0.0;
    if (T >  0  && T <=  1)   return  3.0;
    if (T > -2  && T <=  0)   return  7.0;
    if (T > -4  && T <= -2)   return  8.5;
    if (T > -6  && T <= -4)   return  9.0;
    if (T > -8  && T <= -6)   return  9.5;
    if (T > -10 && T <= -8)   return 12.0;
    if (T > -12 && T <= -10)  return 17.5;
    if (T > -18 && T <= -12)  return 23.0;
    if (T > -24 && T <= -18)  return 15.0;
    return 10.0;
}

/** Interpolated melt rate (mm/hr) from surface wet-bulb — used by kinematic penalty step. */
function getTheoreticalMeltRate(Tw: number): number {
    if (Tw <= 0)   return 0.0;
    if (Tw >= 3.3) return 999.0;
    const tempKeys  = [0.0, 0.5, 1.1, 1.7, 2.2, 2.8, 3.3];
    const meltRates = [0.0, 0.76, 1.27, 1.78, 2.29, 3.05, 999.0];
    for (let i = 0; i < tempKeys.length - 1; i++) {
        if (Tw >= tempKeys[i] && Tw < tempKeys[i + 1]) {
            const slope = (meltRates[i + 1] - meltRates[i]) / (tempKeys[i + 1] - tempKeys[i]);
            return meltRates[i] + slope * (Tw - tempKeys[i]);
        }
    }
    return 0.0;
}

/**
 * Physics-based Cobb + Kuchera consensus with post-depositional penalties.
 * Used by the 'kinematic' method.
 */
function advancedSLR(hour: OpenMeteoHour, QPF: number): { slr: number; isSnow: boolean } {
    if (QPF <= 0.0) return { slr: 0.0, isSnow: false };

    const { RH: RH_sfc, Tw: Tw_sfc } = surfaceWetBulb(hour);
    if (Tw_sfc >= 4.4) return { slr: 0.0, isSnow: false };

    const Rd = 287.058; // J kg⁻¹ K⁻¹
    const g  = 9.80665; // m s⁻²

    let w_max    = 0.0;
    let T_max    = -999.0;
    let wind_700 = 0.0;

    const sortedLevels = [...hour.layers].sort((a, b) => b.pressure - a.pressure);
    const processedLevels: Array<PressureLayer & { deltaZ: number; w: number; isCloudy: boolean }> = [];

    for (let i = 0; i < sortedLevels.length; i++) {
        const level = sortedLevels[i];
        let deltaZ = 0;
        if (i < sortedLevels.length - 1) {
            deltaZ = Math.abs(sortedLevels[i + 1].gz - level.gz);
        } else if (i > 0) {
            deltaZ = Math.abs(level.gz - sortedLevels[i - 1].gz);
        }

        const T_K  = level.temp + 273.15;
        const P_Pa = level.pressure * 100.0;
        let w = (-level.omega * Rd * T_K) / (P_Pa * g);
        w = w > 0 ? w : 0;

        if (level.pressure === 700) wind_700 = level.wind_speed;
        if (level.pressure <= 925 && level.pressure >= 400 && level.temp > T_max) T_max = level.temp;

        const isCloudy = level.rh >= 80.0;
        if (isCloudy && w > w_max) w_max = w;
        processedLevels.push({ ...level, deltaZ, w, isCloudy });
    }

    // 4A: Cobb — profile-weighted vertical velocity
    let SLR_Cobb = 0.0;
    if (w_max > 0) {
        let sum_WF = 0.0, sum_Weighted_SLR = 0.0;
        for (const lev of processedLevels) {
            if (lev.isCloudy) {
                const WF_i = (lev.w / w_max) * lev.deltaZ;
                sum_Weighted_SLR += WF_i * getIdealizedLayerSLR(lev.temp);
                sum_WF           += WF_i;
            }
        }
        if (sum_WF > 0) SLR_Cobb = sum_Weighted_SLR / sum_WF;
    }

    // 4B: Kuchera — 5th-order polynomial on max temp aloft
    let SLR_Kuchera = 0.0;
    if (T_max !== -999.0) {
        SLR_Kuchera = 0.0000045 * Math.pow(T_max, 5) +
                      0.0004432 * Math.pow(T_max, 4) +
                      0.0130903 * Math.pow(T_max, 3) +
                      0.0585968 * Math.pow(T_max, 2) -
                      1.8150809 * T_max +
                      5.9805722;
        if (SLR_Kuchera < 0) SLR_Kuchera = 0;
    }

    let SLR_Base = 0.5 * SLR_Cobb + 0.5 * SLR_Kuchera;
    if (SLR_Base === 0) SLR_Base = 10.0;

    // 5A: Wind compaction
    let M_700 = 1.0;
    if (wind_700 > 10 && wind_700 <= 30) M_700 = 1.0 - 0.015 * (wind_700 - 10.0);
    else if (wind_700 > 30)              M_700 = 0.70;

    const wind_10m = hour.wind_speed_10m ?? 0;
    let M_10m = 1.0;
    if (wind_10m >= 5.5 && wind_10m <= 30) M_10m = 1.0 - ((wind_10m - 5.5) / 24.5) * 0.40;
    else if (wind_10m > 30)                M_10m = 0.60;
    const C_wind = Math.max(0.50, M_700 * M_10m);

    // 5B: Melting penalty
    let M_sfc = 1.0;
    if (Tw_sfc >= 3.3) {
        M_sfc = 0.0;
    } else if (Tw_sfc > 0.0) {
        const MR = getTheoreticalMeltRate(Tw_sfc);
        M_sfc = Math.max(0.0, (QPF - MR) / QPF);
    }

    return { slr: SLR_Base * C_wind * M_sfc, isSnow: true };
}

// ── Legacy algorithm (Kuchera quadratic formula from prod branch) ───────────
//
// Reference: Kuchera (2000). "Snow-to-Liquid Ratio Forecasting."
// Formula: SLR = 12.0 - (0.5 * T) + (0.06 * T²)
// Adjustments: Mechanical fracturing (wind compaction).

/**
 * Legacy SLR — Kuchera empirical quadratic curve + basic wind penalty.
 * Used in the 'legacy' method from the original prod/src/data.js.
 */
function legacySLR(hour: OpenMeteoHour, QPF: number): { slr: number; isSnow: boolean } {
    if (QPF <= 0) return { slr: 0, isSnow: false };

    const T = hour.temperature_2m;
    const windSpeed = hour.wind_speed_10m ?? 0;

    // Rain threshold
    if (T > 2) return { slr: 0, isSnow: false };

    // Kuchera quadratic formula
    let slr = 12.0 - (0.5 * T) + (0.06 * T * T);

    // Wind adjustment: mechanical fracturing of snow crystals
    if (windSpeed > 50) {
        slr = Math.min(slr, 10); // Cap at 10:1 in extreme wind
    } else if (windSpeed > 25) {
        slr *= 0.85; // 15% reduction for moderate wind
    }

    return { slr: Math.round(clamp(slr, 1, 30) * 10) / 10, isSnow: true };
}

// ── Simple algorithm (Roebber climatological piecewise curve) ─────────────────
//
// Reference: Roebber et al. (2003). "Improving Snowfall Forecasting by
// Diagnosing Snow Density." Weather and Forecasting, 18(2), 264–287.
//
// Uses only surface variables: temperature_2m, dewpoint_2m, wind_speed_10m.

/**
 * Roebber piecewise wet-bulb → SLR curve.
 * Climatologically derived interpolation from the Roebber 2003 habit diagram.
 */
function roebberSLRCurve(Tw: number): number {
    if (Tw <= -18) return 20;
    if (Tw <= -10) return 15 + ((-10 - Tw) / 8) * 5;  // 15 → 20
    if (Tw <=  -5) return 10 + ((-5  - Tw) / 5) * 5;  // 10 → 15
    if (Tw <=   0) return  5 + ((0   - Tw) / 5) * 5;  //  5 → 10
    return Math.max(3, 5 - Tw * 2);                     // near/above-freezing wet snow
}

/**
 * Simple SLR — Roebber (2003) piecewise wet-bulb curve + proportional wind penalty.
 * Rain gate: Tw > 1.5°C (Stewart & King 1987 sub-cloud threshold).
 */
function simpleSLR(hour: OpenMeteoHour, QPF: number): { slr: number; isSnow: boolean } {
    if (QPF <= 0) return { slr: 0, isSnow: false };

    const RH = hour.relative_humidity_2m
        ?? (hour.dew_point_2m != null
            ? calculateRelativeHumidity(hour.temperature_2m, hour.dew_point_2m)
            : 70);
    const Tw = hour.wet_bulb_temperature_2m ?? calculateWetBulb(hour.temperature_2m, RH);

    // Rain gate — Stewart & King (1987)
    if (Tw > 1.5) return { slr: 0, isSnow: false };

    const slr_base    = roebberSLRCurve(Tw);
    const wind_ms     = hour.wind_speed_10m ?? 0;
    const wind_penalty = Math.min(4, wind_ms / 10); // proportional, capped at −4
    const slr          = Math.round(clamp(slr_base - wind_penalty, 3, 25) * 10) / 10;

    return { slr, isSnow: true };
}

// ── Complex algorithm (Column-Integrated Crystal Habit) ───────────────────────
//
// Reference: Bailey & Hallett (2009). "A Comprehensive Habit Diagram for
// Atmospheric Ice Crystals." Journal of the Atmospheric Sciences, 66.
//
// Requires multi-level data: temperature, rh, geopotential_height,
// vertical_velocity at 1000/925/850/700/600/500/400/300 hPa, plus wind at 700.

type CrystalHabit = 'Dendrite' | 'Plate' | 'Needle' | 'Column' | 'Compact' | 'Mixed';

interface ComplexLayer {
    dZ: number;
    midT: number;
    midRH: number;
    omega: number; // raw Pa/s — ECMWF IFS convention: negative = upward
    lower: PressureLayer;
    upper: PressureLayer;
}

/**
 * Ice fraction: 1.0 at T ≤ 0°C, ramps to 0 at T ≥ 2°C.
 * Used to weight layers in the mixed-phase zone.
 */
function iceFraction(T: number): number {
    if (T <= 0) return 1.0;
    if (T >= 2) return 0.0;
    return 1.0 - T / 2.0;
}

/**
 * Bailey & Hallett (2009) crystal habit → base SLR lookup.
 * Dendritic growth zone (−10 to −18°C) produces the lowest density snow.
 */
function crystalHabitSLR(T: number, RH: number): { slr: number; habit: CrystalHabit } {
    if (T >= -18 && T <= -10) {
        const rh_bonus = RH > 85 ? ((RH - 85) / 15) * 6 : 0;
        const slr = RH > 85 ? 18 + rh_bonus : 13 + Math.max(0, (RH - 70) / 15) * 5;
        return { slr: Math.min(24, slr), habit: 'Dendrite' };
    }
    if (T >  -10 && T <= -4)  return { slr: RH > 80 ? 14 : 11, habit: 'Plate'   };
    if (T >   -6 && T <= -3)  return { slr: 12,                  habit: 'Needle'  };
    if (T >   -4 && T <=  0)  return { slr:  8,                  habit: 'Column'  };
    if (T < -18)               return { slr: 10 + Math.min(5, (-T - 18) / 6), habit: 'Compact' };
    return { slr: 10, habit: 'Mixed' };
}

/** Build inter-level layers from pressure levels sorted descending (1000→300 hPa). */
function buildComplexLayers(sorted: PressureLayer[]): ComplexLayer[] {
    const layers: ComplexLayer[] = [];
    for (let i = 0; i < sorted.length - 1; i++) {
        const upper = sorted[i];
        const lower = sorted[i + 1];
        layers.push({
            dZ:    Math.abs(upper.gz - lower.gz),
            midT:  (upper.temp  + lower.temp)  / 2,
            midRH: (upper.rh    + lower.rh)    / 2,
            omega: (upper.omega + lower.omega) / 2,
            upper,
            lower,
        });
    }
    return layers;
}

/**
 * Phase 3: Column-integrated crystal habit SLR.
 * Weight = ΔZ × ice_fraction(midT) × (midRH / 100).
 */
function integrateColumnSLR(layers: ComplexLayer[]): number {
    let totalWeight = 0, weightedSLR = 0;
    for (const layer of layers) {
        const frac = iceFraction(layer.midT);
        const { slr: habitSLR } = crystalHabitSLR(layer.midT, layer.midRH);
        const weight = frac > 0.1
            ? layer.dZ * frac * (layer.midRH / 100)
            : layer.dZ * 0.01;
        totalWeight  += weight;
        weightedSLR  += habitSLR * weight;
    }
    return totalWeight > 0 ? weightedSLR / totalWeight : 10;
}

/**
 * Phase 4: Omega riming factor.
 * Strong upward motion (negative ω in ECMWF IFS) → riming → denser snow → lower SLR.
 * Gentle synoptic ascent → pristine dendrite growth → higher SLR.
 */
function omegaRimingFactor(layers: ComplexLayer[]): number {
    let liftScore = 0, totalDZ = 0;
    for (const layer of layers) {
        if (layer.omega < 0) { // negative omega = upward in ECMWF IFS
            liftScore += Math.abs(layer.omega) * layer.dZ;
            totalDZ   += layer.dZ;
        }
    }
    const meanLift = totalDZ > 0 ? liftScore / totalDZ : 0;
    if      (meanLift > 0.3)  return 0.85; // vigorous convective uplift → riming
    else if (meanLift > 0.1)  return 0.93; // moderate lift
    else if (meanLift < 0.02) return 1.08; // gentle synoptic ascent → pristine dendrites
    return 1.0;
}

/**
 * Phase 5: 700 hPa crest-level wind compaction factor.
 */
function wind700CompactionFactor(wind700: number): number {
    if      (wind700 > 25) return 0.72;
    else if (wind700 > 15) return 0.82;
    else if (wind700 >  8) return 0.92;
    return 1.0;
}

/**
 * Phase 6: Warm-nose melt penalty.
 * Above-freezing layers between 700–1000 hPa partially melt falling snow.
 * Returns a fractional penalty [0, 0.4].
 */
function warmNosePenalty(sorted: PressureLayer[]): number {
    const warmLevels = sorted.filter(l => l.pressure >= 700 && l.pressure <= 1000 && l.temp > 0);
    if (warmLevels.length === 0) return 0;
    const meltDepth = warmLevels.reduce((sum, l) => {
        const idx = sorted.indexOf(l);
        const dZ  = idx > 0 ? Math.abs(sorted[idx].gz - sorted[idx - 1].gz) : 100;
        return sum + dZ;
    }, 0);
    return clamp(meltDepth / 3000, 0, 0.4);
}

/**
 * Phase 7: Surface temperature aggregation factor.
 * Very cold → crystal aggregation → bulkier snowpack.
 * Near-freezing → wet, dense snow.
 */
function surfaceTemperatureFactor(Tw: number): number {
    if      (Tw < -10) return 1.12;
    else if (Tw <  -5) return 1.06;
    else if (Tw <   0) return 1.00;
    return 0.88;
}

/**
 * Complex SLR — column-integrated crystal habit (Bailey & Hallett 2009) with
 * omega riming, wind compaction, warm-nose melting, and surface aggregation.
 * Requires multi-level pressure data.
 */
function sophisticatedSLR(hour: OpenMeteoHour, QPF: number): { slr: number; isSnow: boolean } {
    if (QPF <= 0) return { slr: 0, isSnow: false };

    const RH_sfc = hour.relative_humidity_2m
        ?? (hour.dew_point_2m != null
            ? calculateRelativeHumidity(hour.temperature_2m, hour.dew_point_2m)
            : 70);
    const Tw_sfc = hour.wet_bulb_temperature_2m ?? calculateWetBulb(hour.temperature_2m, RH_sfc);

    // Rain gate — Stewart & King (1987) adapted threshold
    if (Tw_sfc > 2.5) return { slr: 0, isSnow: false };

    // Fall back to simple Roebber if no pressure-level data available
    if (!hour.layers || hour.layers.length < 2) {
        return simpleSLR(hour, QPF);
    }

    const sorted = [...hour.layers].sort((a, b) => b.pressure - a.pressure);
    const layers = buildComplexLayers(sorted);

    // Phase 3: crystal habit column integration
    let columnSLR = integrateColumnSLR(layers);

    // Phase 4: omega riming
    columnSLR *= omegaRimingFactor(layers);

    // Phase 5: 700 hPa wind compaction
    const lv700   = sorted.find(l => l.pressure === 700);
    const wind700 = lv700?.wind_speed ?? (hour.wind_speed_10m ?? 0);
    columnSLR *= wind700CompactionFactor(wind700);

    // Phase 6: warm-nose melt penalty
    const meltPenalty = warmNosePenalty(sorted);
    if (meltPenalty > 0) columnSLR *= (1 - meltPenalty);

    // Phase 7: surface aggregation
    columnSLR *= surfaceTemperatureFactor(Tw_sfc);

    // Phase 8: bound [3, 30]
    const slr = Math.round(clamp(columnSLR, 3, 30) * 10) / 10;
    return { slr, isSnow: true };
}

// ── Public switchboard ────────────────────────────────────────────────────────

/**
 * Calculates the SLR and snowfall for a single hourly data point.
 *
 * Supported methods:
 *   'kinematic'   — Physics-based Cobb + Kuchera consensus (default)
 *   'complex'     — Column-integrated crystal habit (Bailey & Hallett 2009)
 *   'simple'      — Roebber (2003) piecewise wet-bulb curve + wind penalty
 *   'standard'    — Fixed 10:1 ratio
 *   'model_native'— Use the model's own snowfall output directly
 *
 * @param hour     The hourly data point from Open-Meteo
 * @param method   Algorithm mode
 * @param prevSlr  Previous hour's SLR for temporal smoothing
 */
export function calcSLR(hour: OpenMeteoHour, method: string = 'kinematic', prevSlr: number | null = null): SLROutput {
    const P = hour.precipitation ?? 0;

    const out: SLROutput = { slr: null, snow_cm: 0, method, isSnow: false, qpf_corrected: P };

    if (P <= 0) return out;

    // Shared pre-flight rain check
    const rh_sfc = hour.relative_humidity_2m ?? 70;
    const Tw_sfc = hour.wet_bulb_temperature_2m ?? calculateWetBulb(hour.temperature_2m, rh_sfc);
    if (Tw_sfc >= 4.4) return out;

    out.isSnow = true;
    let slr = 10;

    if (method === 'model_native') {
        const h_snowfall_cm = hour.snowfall ?? 0;
        slr = (P > 0 && h_snowfall_cm > 0) ? (h_snowfall_cm * 10) / P : 10;
        out.qpf_corrected = P;

    } else if (method === 'standard') {
        slr = 10;
        out.qpf_corrected = P;

    } else if (method === 'simple') {
        out.qpf_corrected = P;
        const result = simpleSLR(hour, P);
        if (result.isSnow) { slr = result.slr; } else { out.isSnow = false; return out; }

    } else if (method === 'complex') {
        out.qpf_corrected = P;
        const result = sophisticatedSLR(hour, P);
        if (result.isSnow) { slr = result.slr; } else { out.isSnow = false; return out; }

    } else if (method === 'legacy') {
        out.qpf_corrected = P;
        const result = legacySLR(hour, P);
        if (result.isSnow) { slr = result.slr; } else { out.isSnow = false; return out; }

    } else {
        // 'kinematic' (default) — physics-based with orographic QPF bump
        const qpf_corrected = P * 1.43;
        out.qpf_corrected   = qpf_corrected;
        const result = advancedSLR(hour, qpf_corrected);
        if (result.isSnow) { slr = result.slr; } else { out.isSnow = false; return out; }
    }

    slr = Math.max(1, Math.min(slr, 30));

    // Temporal smoothing (not for model_native)
    if (prevSlr !== null && method !== 'model_native') {
        slr = Math.max(prevSlr - 2.5, Math.min(slr, prevSlr + 2.5));
    }

    out.slr     = +slr.toFixed(1);
    out.snow_cm = +((out.qpf_corrected ?? P) * slr / 10).toFixed(1);
    return out;
}
