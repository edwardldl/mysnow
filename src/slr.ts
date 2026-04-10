export interface PressureLayer {
    pressure: number;
    temp: number;
    rh: number;
    gz: number;
    omega: number;
    wind_speed: number;
    cloud_cover: number;
}

export interface OpenMeteoHour {
    time: string;
    temperature_2m: number;
    dew_point_2m: number | null;
    wind_speed_10m: number | null;
    wind_gusts_10m: number | null;
    precipitation: number;
    snowfall: number | null;
    relative_humidity_2m: number | null;
    wet_bulb_temperature_2m: number | null;
    specific_humidity_2m: number | null;
    pressure_msl: number | null;
    surface_pressure: number | null;
    soil_temperature_0cm: number | null;
    shortwave_radiation: number | null;
    cape: number | null;
    lifted_index: number | null;
    convective_inhibition: number | null;
    visibility: number | null;
    boundary_layer_height: number | null;
    total_column_integrated_water_vapour: number | null;
    snow_depth: number | null;
    freezing_level_height: number | null;
    elevation: number;
    weather_code: number;
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
    const e = Math.exp((17.625 * Td) / (243.04 + Td));
    const es = Math.exp((17.625 * T) / (243.04 + T));
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

/**
 * Kuchera piecewise fallback used when no valid atmospheric profile is available.
 * Falls back to the 2m temperature as a proxy for column max temp.
 */
function kucheraFallback(temperature_2m: number): { slr: number; isSnow: boolean } {
    const maxTempK = temperature_2m + 273.15;
    const KUCHERA_PIVOT = 271.16;
    const rawSlr = maxTempK > KUCHERA_PIVOT
        ? 12 + 2 * (KUCHERA_PIVOT - maxTempK)
        : 12 + (KUCHERA_PIVOT - maxTempK);
    return { slr: Math.round(Math.max(1.0, rawSlr) * 10) / 10, isSnow: true };
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
    if (T > 1) return 0.0;
    if (T > 0 && T <= 1) return 3.0;
    if (T > -2 && T <= 0) return 7.0;
    if (T > -4 && T <= -2) return 8.5;
    if (T > -6 && T <= -4) return 9.0;
    if (T > -8 && T <= -6) return 9.5;
    if (T > -10 && T <= -8) return 12.0;
    if (T > -12 && T <= -10) return 17.5;
    if (T > -18 && T <= -12) return 23.0;
    if (T > -24 && T <= -18) return 15.0;
    return 10.0;
}

/** Interpolated melt rate (mm/hr) from surface wet-bulb — used by kinematic penalty step. */
function getTheoreticalMeltRate(Tw: number): number {
    if (Tw <= 0) return 0.0;
    if (Tw >= 3.3) return 999.0;
    const tempKeys = [0.0, 0.5, 1.1, 1.7, 2.2, 2.8, 3.3];
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
    const g = 9.80665; // m s⁻²

    let w_max = 0.0;
    let T_max = -999.0;
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

        const T_K = level.temp + 273.15;
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
                sum_WF += WF_i;
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
    else if (wind_700 > 30) M_700 = 0.70;

    const wind_10m = hour.wind_speed_10m ?? 0;
    let M_10m = 1.0;
    if (wind_10m >= 5.5 && wind_10m <= 30) M_10m = 1.0 - ((wind_10m - 5.5) / 24.5) * 0.40;
    else if (wind_10m > 30) M_10m = 0.60;
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



/* ── Simple SLR Algorithm (Vanilla Kuchera) ──────────────────────────────────
 * Accounts for surface elevation by filtering out subterranean pressure levels.
 *
 * @param hour - The hourly data point from Open-Meteo.
 * @param QPF - Quantitative Precipitation Forecast.
 * @returns SLR and snow flag.
 */
function simpleSLR(hour: OpenMeteoHour, QPF: number): { slr: number; isSnow: boolean } {
    if (QPF <= 0) return { slr: 0, isSnow: false };

    // 1. Filter out pressure levels that are underground.
    const surfacePressure = hour.surface_pressure ?? 1013.25;
    const aboveGroundProfile = hour.layers.filter(
        layer => layer.pressure <= surfacePressure && layer.temp != null
    );

    if (aboveGroundProfile.length === 0) {
        return kucheraFallback(hour.temperature_2m);
    }

    // 2. Find the maximum temperature in the valid, above-ground atmospheric column
    const maxTempC = Math.max(...aboveGroundProfile.map(layer => layer.temp));

    // 3. Convert to Kelvin for the Kuchera equation
    const maxTempK = maxTempC + 273.15;

    // 4. Define the Kuchera pivot point (approx -1.99°C)
    const KUCHERA_PIVOT = 271.16;
    let slr = 12.0;

    // 5. Apply the piecewise regression formula
    if (maxTempK > KUCHERA_PIVOT) {
        slr = 12 + 2 * (KUCHERA_PIVOT - maxTempK);
    } else {
        slr = 12 + (KUCHERA_PIVOT - maxTempK);
    }

    return { slr: Math.round(Math.max(1.0, slr) * 10) / 10, isSnow: true };
}

/**
 * Kuchera (DGZ-Enhanced) — Piecewise regression on max temperature aloft,
 * with a boost for the saturated Dendritic Growth Zone thickness.
 *
 * @param hour - The hourly data point from Open-Meteo.
 * @param QPF - Quantitative Precipitation Forecast.
 * @returns SLR and snow flag.
 */
function kucheraDgzSLR(hour: OpenMeteoHour, QPF: number): { slr: number; isSnow: boolean } {
    if (QPF <= 0) return { slr: 0, isSnow: false };

    // 1. Filter out pressure levels that are underground.
    const surfacePressure = hour.surface_pressure ?? 1013.25;
    const aboveGroundProfile = hour.layers.filter(
        layer => layer.pressure <= surfacePressure && layer.temp != null
    );

    if (aboveGroundProfile.length === 0) {
        return kucheraFallback(hour.temperature_2m);
    }

    // 2. Sort profile from ground up (highest hPa to lowest hPa)
    const sorted = [...aboveGroundProfile].sort((a, b) => b.pressure - a.pressure);

    // 3. Calculate Base Vanilla Kuchera
    const maxTempC = Math.max(...sorted.map(layer => layer.temp));
    const maxTempK = maxTempC + 273.15;
    const KUCHERA_PIVOT = 271.16;

    let baseSlr = 12.0;
    if (maxTempK > KUCHERA_PIVOT) {
        baseSlr = 12 + 2 * (KUCHERA_PIVOT - maxTempK);
    } else {
        baseSlr = 12 + (KUCHERA_PIVOT - maxTempK);
    }
    baseSlr = Math.max(1.0, baseSlr);

    // 4. Calculate Saturated DGZ Boost
    let saturatedDgzThickness_hPa = 0;
    for (let i = 0; i < sorted.length; i++) {
        const layer = sorted[i];
        const isOptimalTemp = layer.temp <= -12 && layer.temp >= -18;
        const isMoist = layer.rh >= 80;

        if (isOptimalTemp && isMoist) {
            const nextPressure = sorted[i + 1] ? sorted[i + 1].pressure : layer.pressure - 50;
            const layerThickness = Math.abs(layer.pressure - nextPressure);
            saturatedDgzThickness_hPa += layerThickness;
        }
    }

    const dgzBoost = (saturatedDgzThickness_hPa / 25) * 1.5;
    const slr = baseSlr + dgzBoost;

    return { slr: Math.round(Math.max(1.0, slr) * 10) / 10, isSnow: true };
}

/**
 * Kuchera (DGZ-Plus) — Enhanced piecewise regression on max temperature aloft,
 * factoring in surface elevation, true physical DGZ depth, and wind compaction.
 *
 * @param hour - The hourly data point from Open-Meteo.
 * @param QPF - Quantitative Precipitation Forecast.
 * @returns SLR and snow flag.
 */
function kucheraDgzPlusSLR(hour: OpenMeteoHour, QPF: number): { slr: number; isSnow: boolean } {
    if (QPF <= 0) return { slr: 0, isSnow: false };

    // 1. Filter out pressure levels that are underground.
    const surfacePressure = hour.surface_pressure ?? 1013.25;
    const aboveGroundProfile = hour.layers.filter(
        layer => layer.pressure <= surfacePressure && layer.temp != null
    );

    if (aboveGroundProfile.length === 0) {
        return kucheraFallback(hour.temperature_2m);
    }

    // 2. Sort profile from ground up (highest hPa to lowest hPa)
    const sorted = [...aboveGroundProfile].sort((a, b) => b.pressure - a.pressure);

    // 3. Calculate Base Vanilla Kuchera
    const maxTempC = Math.max(...sorted.map(layer => layer.temp));
    const maxTempK = maxTempC + 273.15;
    const KUCHERA_PIVOT = 271.16;

    let baseSlr = 12.0;
    if (maxTempK > KUCHERA_PIVOT) {
        baseSlr = 12 + 2 * (KUCHERA_PIVOT - maxTempK);
    } else {
        baseSlr = 12 + (KUCHERA_PIVOT - maxTempK);
    }
    baseSlr = Math.max(1.0, baseSlr);

    // 4. Calculate Saturated DGZ Boost (using True Physical Depth)
    let saturatedDgzDepth_meters = 0;
    for (let i = 0; i < sorted.length; i++) {
        const layer = sorted[i];
        const isOptimalTemp = layer.temp <= -12 && layer.temp >= -18;
        const isMoistAndCloudy = layer.rh >= 80 && layer.cloud_cover >= 80;

        if (isOptimalTemp && isMoistAndCloudy) {
            const nextLayer = sorted[i + 1];
            // If it's the top layer, assume a conservative 300m cap for the final chunk
            const layerThickness_m = nextLayer
                ? Math.abs(nextLayer.gz - layer.gz)
                : 300;
            saturatedDgzDepth_meters += layerThickness_m;
        }
    }

    // Apply the boost: For every 100 meters of optimal, saturated DGZ, add 0.5 to the ratio.
    const dgzBoost = (saturatedDgzDepth_meters / 100) * 0.5;
    let enhancedSlr = baseSlr + dgzBoost;

    // 5. Apply Mechanical Wind Compaction Penalty
    // Average the wind speed of the lowest 2 valid layers (representing the boundary layer/surface)
    // Note: wind_speed is in m/s from the API
    const lowerLayers = sorted.slice(0, 2);
    const avgLowerWindSpeed = lowerLayers.reduce((sum, layer) => sum + layer.wind_speed, 0) / lowerLayers.length;

    // If winds exceed ~30 km/h (8.33 m/s), crystals fracture. 
    // Reduce SLR by 1.5% for every km/h over 30 (which is ~5.4% per m/s over 8.33), capped at a 40% reduction.
    if (avgLowerWindSpeed > 8.33) {
        const excessWindMs = avgLowerWindSpeed - 8.33;
        const compactionFactor = Math.min(0.40, excessWindMs * 0.054);
        enhancedSlr = enhancedSlr * (1 - compactionFactor);
    }

    return { slr: Math.round(Math.max(1.0, enhancedSlr) * 10) / 10, isSnow: true };
}


// ── Dendro Algorithm (Habit diagram + DGZ depth) ───────────────────────────

/**
 * Dendro SLR — Habit diagram based on DGZ depth and thermal profiles.
 * Ported from dendro.ts.
 */
function dendroSLR(hour: OpenMeteoHour, QPF: number): { slr: number; isSnow: boolean } {
    if (QPF <= 0) return { slr: 0, isSnow: false };

    const T2m = hour.temperature_2m;
    const wind10 = hour.wind_speed_10m ?? 0;
    const elev = hour.elevation;

    const layer850 = hour.layers.find(l => l.pressure === 850);
    const layer700 = hour.layers.find(l => l.pressure === 700);
    const layer500 = hour.layers.find(l => l.pressure === 500);

    const t850 = layer850?.temp;
    const t700 = layer700?.temp;
    const t500 = layer500?.temp;
    const rh700 = layer700?.rh;
    const tcwv = hour.total_column_integrated_water_vapour ?? undefined;
    const freezeH = hour.freezing_level_height ?? (T2m > 0 ? 0 : 2000); // fallback

    // Helper: Estimate missing 700 hPa temp if needed
    function estimate700(a?: number, b?: number): number | undefined {
        if (a == null || b == null) return undefined;
        return a - (a - b) * 0.5; // simple linear interp at ~800 hPa
    }
    const T700 = (t700 !== undefined) ? t700 : estimate700(t850, t500);

    // 1) DGZ Score: Find pressures for -12°C and -18°C in profile
    let dgzDepth = 0;
    if (t850 != null && T700 != null && t500 != null) {
        const T1 = t850;
        const T2 = t500;
        function findP(isotherm: number): number {
            const P1 = 850, P2 = 500;
            return P1 + (P2 - P1) * ((isotherm - T1) / (T2 - T1));
        }
        const p18 = findP(-18);
        const p12 = findP(-12);
        dgzDepth = Math.max(0, p18 - p12); // hPa depth of DGZ
    }
    const dgzScore = clamp(dgzDepth / 300, 0, 1);

    // 2) Cold column score: average of 850/700/500 layer
    let coldAvg = 0, count = 0;
    if (t850 != null) { coldAvg += t850; count++; }
    if (T700 != null) { coldAvg += T700; count++; }
    if (t500 != null) { coldAvg += t500; count++; }
    const coldScore = count > 0 ? clamp((-coldAvg / count - 5) / 25, 0, 1) : 0;

    // 3) Moisture score (mid-level RH / precipitable water)
    let moistSum = 0;
    if (rh700 != undefined) moistSum += clamp((rh700 - 80) / 20, 0, 1);
    if (tcwv != undefined) moistSum += clamp(tcwv / 20, 0, 1);
    const moistureScore = clamp(moistSum, 0, 1);

    // 4) Freezing level penalty
    const meltPenalty = freezeH > 2000 ? 0.9 : 1.0;

    // 5) Wind compaction penalty
    const windPen = clamp(wind10 / 15, 0, 1);

    // 6) Orographic (wind*elev) penalty
    const upliftScore = clamp((wind10 * elev) / 200000, 0, 1);
    const upliftPen = 1 - 0.3 * upliftScore;

    // Base SLR
    let slr = 5 + 15 * dgzScore + 10 * coldScore + 5 * moistureScore;
    slr *= (1 - 0.3 * windPen);
    slr *= upliftPen;
    slr *= meltPenalty;

    return { slr: Math.round(clamp(slr, 3, 30) * 10) / 10, isSnow: true };
}

/** Linear interpolation helper for KRC algorithm */
function lerp(x: number, x0: number, x1: number, y0: number, y1: number): number {
    if (x1 === x0) return y0;
    return y0 + ((x - x0) / (x1 - x0)) * (y1 - y0);
}

/**
 * KRC-Comp: High-fidelity snow-to-liquid ratio forecasting.
 * Features: Piecewise Kuchera base, Crystal Habit modification, DGZ weighting,
 * Riming index correction, Vertical wind shear fragmentation, and Wet-bulb profile constraints.
 */
function krcCompSLR(hour: OpenMeteoHour, QPF: number): { slr: number; isSnow: boolean } {
    if (QPF <= 0) return { slr: 0, isSnow: false };

    // Built-in rain gate based on WMO codes and temperature
    const isSnowOrMixed =
        (hour.weather_code >= 56 && hour.weather_code <= 57) ||
        (hour.weather_code >= 66 && hour.weather_code <= 67) ||
        (hour.weather_code >= 70 && hour.weather_code <= 79) ||
        (hour.weather_code >= 85 && hour.weather_code <= 86);

    if (!isSnowOrMixed && hour.temperature_2m > 3) {
        return { slr: 0, isSnow: false };
    }

    const levels = [...hour.layers].sort((a, b) => b.pressure - a.pressure);
    if (levels.length < 2) return advancedSLR(hour, QPF);

    // Kuchera base SLR logic
    const stats = (() => {
        let weightedTemp = 0, weightedRH = 0, totalWeight = 0;
        for (let i = 0; i < levels.length - 1; i++) {
            const lo = levels[i], hi = levels[i + 1];
            const T_mid = (lo.temp + hi.temp) / 2;
            const RH_mid = (lo.rh + hi.rh) / 2;
            const dP = Math.abs(lo.pressure - hi.pressure);
            if (T_mid < 0) {
                weightedTemp += T_mid * dP;
                weightedRH += RH_mid * dP;
                totalWeight += dP;
            }
        }
        if (totalWeight === 0) return { meanTemp: hour.temperature_2m, meanRH: hour.relative_humidity_2m || 70 };
        return { meanTemp: weightedTemp / totalWeight, meanRH: weightedRH / totalWeight };
    })();

    const getBaseSLR = (T: number) => {
        if (T > -1.0) return 4.0;
        if (T > -3.0) return lerp(T, -1.0, -3.0, 4.0, 7.0);
        if (T > -5.0) return lerp(T, -3.0, -5.0, 7.0, 10.0);
        if (T > -8.0) return lerp(T, -5.0, -8.0, 10.0, 14.0);
        if (T > -10.0) return lerp(T, -8.0, -10.0, 14.0, 17.0);
        if (T > -15.0) return lerp(T, -10.0, -15.0, 17.0, 20.0);
        if (T > -20.0) return lerp(T, -15.0, -20.0, 20.0, 17.0);
        if (T > -25.0) return lerp(T, -20.0, -25.0, 17.0, 14.0);
        if (T > -30.0) return lerp(T, -25.0, -30.0, 14.0, 11.0);
        return 10.0;
    };
    const baseSLR = getBaseSLR(stats.meanTemp);

    // Habit modifier from DGZ
    const dgzTemp = (() => {
        let wT = 0, wW = 0;
        for (let i = 0; i < levels.length - 1; i++) {
            const T_mid = (levels[i].temp + levels[i + 1].temp) / 2;
            const dP = Math.abs(levels[i].pressure - levels[i + 1].pressure);
            if (T_mid >= -18 && T_mid <= -12) { wT += T_mid * dP; wW += dP; }
        }
        return wW === 0 ? -10 : wT / wW;
    })();
    const dgzRH = hour.layers.find(l => l.pressure === 850)?.rh || hour.relative_humidity_2m || 70;

    const crystalMod = (T: number, RH: number) => {
        const ss = RH - 100;
        if (RH > 102 && T > -10 && T < -1) return 0.65; // graupel
        if (T >= -18 && T <= -12) return ss >= 0 ? 1.30 : 1.15;
        if (T >= -12 && T < -10) return 1.10;
        if (T >= -10 && T < -8) return 1.00;
        if (T >= -5 && T < -3) return 0.75;
        if (T >= -8 && T < -5) return 0.85;
        if (T >= -3 && T < -1) return 0.70;
        if (T < -18 && T >= -25) return 0.88;
        if (T < -25) return 0.80;
        return 0.95;
    };
    const habitMod = crystalMod(dgzTemp, dgzRH);

    // DGZ Fraction weighting
    const dgzDepth = levels.reduce((acc, curr, i) => {
        if (i === levels.length - 1) return acc;
        const T_mid = (curr.temp + levels[i + 1].temp) / 2;
        if (T_mid >= -18 && T_mid <= -12) return acc + Math.abs(curr.pressure - levels[i + 1].pressure);
        return acc;
    }, 0);
    const totalColdDepth = levels.reduce((acc, curr, i) => {
        if (i === levels.length - 1) return acc;
        const T_mid = (curr.temp + levels[i + 1].temp) / 2;
        if (T_mid < 0) return acc + Math.abs(curr.pressure - levels[i + 1].pressure);
        return acc;
    }, 0);
    const dgzFrac = totalColdDepth > 0 ? dgzDepth / totalColdDepth : 0;
    const amplifiedHabitMod = 1.0 + (habitMod - 1.0) * (0.5 + dgzFrac * 0.5);

    // Riming correction
    let rimingScore = 0;
    const rimLayer = levels.filter(l => l.temp <= -2 && l.temp >= -15);
    const meanRimRH = rimLayer.length ? rimLayer.reduce((s, l) => s + l.rh, 0) / rimLayer.length : 0;
    rimingScore += clamp((meanRimRH - 85) / 15, 0, 1) * 0.40;
    rimingScore += clamp((hour.total_column_integrated_water_vapour || 0) / 30, 0, 1) * 0.20;
    const wb = hour.wet_bulb_temperature_2m ?? 0;
    rimingScore += (wb < 0 ? clamp(1 - Math.abs(wb) / 5, 0, 1) : 0) * 0.25;
    rimingScore += clamp(((hour.wind_gusts_10m || 0) - 5) / 20, 0, 1) * 0.15;
    const rimingCorr = 1.0 - 0.50 * clamp(rimingScore, 0, 1);

    // Wind Shear correction (bulk 1000-500hPa)
    const s1000 = hour.layers.find(l => l.pressure === 1000);
    const s500 = hour.layers.find(l => l.pressure === 500);
    const dV = Math.abs((s500?.wind_speed || 15) - (s1000?.wind_speed || hour.wind_speed_10m || 0));
    const dZ = Math.abs((s500?.gz || 5500) - (s1000?.gz || 0));
    const shear = dZ > 0 ? (dV / dZ) * 1000 : 0;
    let shearCorr = 1.0;
    if (stats.meanTemp >= -18 && stats.meanTemp <= -10 && shear > 2 && shear < 6) shearCorr = 1.05;
    else if (shear > 8) shearCorr = clamp(1.0 - (shear - 8) * 0.025, 0.80, 1.0);

    // Wet-bulb and warm-nose profile penalties
    const warmNoseDepth = levels.filter(l => l.pressure >= 850 && l.temp > 0).length * 25;
    let wbFactor = 1.0;
    if (wb > 0) wbFactor = 0.5;
    else {
        const nosePenalty = clamp(warmNoseDepth / 100, 0, 0.35);
        const nearMeltPenalty = wb > -1 ? clamp((wb + 1) * 0.15, 0, 0.15) : 0;
        wbFactor = clamp(1.0 - nosePenalty - nearMeltPenalty, 0.5, 1.0);
    }

    // CAPE / Instability correction
    const capePenalty = clamp((hour.cape || 0) / 500, 0, 0.20);
    const liPenalty = (hour.lifted_index || 0) < -1 ? clamp((- (hour.lifted_index || 0) - 1) * 0.04, 0, 0.15) : 0;
    const capeCorr = 1.0 - capePenalty - liPenalty;

    const finalSLR = baseSLR * amplifiedHabitMod * rimingCorr * shearCorr * wbFactor * capeCorr;
    return { slr: Math.round(clamp(finalSLR, 3, 30) * 10) / 10, isSnow: true };
}


// ── Cobb Algorithm (DGZ-Enhanced Piecewise) ───────────────────────────────────

/**
 * Adapted Cobb Algorithm, improved with a DGZ multiplier.
 * Ported from cobb.ts.
 */
function cobbSLR(hour: OpenMeteoHour, QPF: number): { slr: number; isSnow: boolean } {
    if (QPF <= 0) return { slr: 0, isSnow: false };

    // 1. Filter out subterranean levels and sort
    const aboveGroundLevels = hour.layers.filter(
        level => level.gz >= hour.elevation
    );
    const sortedLevels = [...aboveGroundLevels].sort(
        (a, b) => a.gz - b.gz
    );

    let totalWeightedSLR = 0;
    let totalWeight = 0;
    let saturatedDGZDepthMeters = 0; // Track the thickness of the saturated DGZ

    for (let i = 0; i < sortedLevels.length - 1; i++) {
        const bottom = sortedLevels[i];
        const top = sortedLevels[i + 1];

        const avgRH = (bottom.rh + top.rh) / 2;
        if (avgRH < 90) continue; // Cloud layer threshold

        const avgTemp = (bottom.temp + top.temp) / 2;
        const thickness = top.gz - bottom.gz;

        // -- Track the DGZ --
        if (avgTemp <= -12 && avgTemp >= -18) {
            saturatedDGZDepthMeters += thickness;
        }

        const layerSLR = getCobbLayerSLR(avgTemp);
        const weight = thickness;

        totalWeightedSLR += layerSLR * weight;
        totalWeight += weight;
    }

    if (totalWeight === 0) return { slr: 10, isSnow: true }; // Fallback

    let baseSLR = totalWeightedSLR / totalWeight;

    // -- Apply DGZ Depth Bonus --
    if (saturatedDGZDepthMeters > 1000) {
        const extraDepth = saturatedDGZDepthMeters - 1000;
        const dgzMultiplier = 1 + (Math.min(extraDepth / 1000, 2.5) * 0.10);
        baseSLR = baseSLR * dgzMultiplier;
    }

    return { slr: baseSLR, isSnow: true };
}

function getCobbLayerSLR(tempC: number): number {
    if (tempC > 0) return 0;
    // Cobb piecewise curve
    if (tempC <= 0 && tempC > -16) return 3 + (tempC * -1.4375);
    if (tempC <= -16 && tempC > -30) return 26 + ((tempC + 16) * 1.342857);
    return 7.2;
}


// ── Hybrid SLR Algorithm (Kuchera Baseline + Cobb Boost + Wind Penalty) ──────

/**
 * Hybrid SLR — Combines Kuchera baseline with a Cobb-inspired DGZ boost,
 * factoring in true physical depth and mechanical wind compaction.
 *
 * @param hour - The hourly data point from Open-Meteo.
 * @param QPF - Quantitative Precipitation Forecast.
 * @returns SLR and snow flag.
 */
function hybridSLR(hour: OpenMeteoHour, QPF: number): { slr: number; isSnow: boolean } {
    if (QPF <= 0) return { slr: 0, isSnow: false };

    // 1. ELEVATION ANCHOR: Filter levels below the location's surface
    const surfacePressure = hour.surface_pressure ?? 1013.25;
    const validProfile = hour.layers.filter(
        layer => layer.pressure <= surfacePressure && layer.temp != null
    );

    if (validProfile.length === 0) {
        return kucheraFallback(hour.temperature_2m);
    }

    // Sort from the ground up (highest pressure to lowest)
    const sorted = [...validProfile].sort((a, b) => b.pressure - a.pressure);

    // 2. KUCHERA BASELINE: The "Warmest Layer" approach
    const maxTempC = Math.max(...sorted.map(layer => layer.temp));
    const maxTempK = maxTempC + 273.15;
    const KUCHERA_PIVOT = 271.16;

    let baseSlr = 12.0;
    if (maxTempK > KUCHERA_PIVOT) {
        baseSlr = 12 + 2 * (KUCHERA_PIVOT - maxTempK);
    } else {
        baseSlr = 12 + (KUCHERA_PIVOT - maxTempK);
    }

    // Safely bound the Kuchera baseline
    baseSlr = Math.max(4.0, baseSlr);

    // 3. COBB-INSPIRED DGZ BOOST: The "Microphysics" approach
    let saturatedDgzDepth_meters = 0;
    let avgLiftInDgz = 0;
    let dgzLayersCount = 0;

    const Rd = 287.058; // J kg⁻¹ K⁻¹
    const g = 9.80665; // m s⁻²

    for (let i = 0; i < sorted.length; i++) {
        const layer = sorted[i];

        // The Magic Intersection: Optimal Temp + High Moisture + Active Cloud
        const isOptimalTemp = layer.temp <= -12 && layer.temp >= -18;
        const isMoist = layer.rh >= 80;
        const isCloudy = layer.cloud_cover >= 80;

        if (isOptimalTemp && isMoist && isCloudy) {
            // TRUE PHYSICAL DEPTH
            const nextLayer = sorted[i + 1];
            // If it's the top layer, assume a standard 300m cap for the final chunk
            const layerThickness_m = nextLayer
                ? Math.abs(nextLayer.gz - layer.gz)
                : 300;

            saturatedDgzDepth_meters += layerThickness_m;

            // Track lift (Omega to m/s conversion)
            if (layer.omega < 0) { // Upward lift
                const T_K = layer.temp + 273.15;
                const P_Pa = layer.pressure * 100.0;
                const w = (-layer.omega * Rd * T_K) / (P_Pa * g);
                if (w > 0) {
                    avgLiftInDgz += w;
                    dgzLayersCount++;
                }
            }
        }
    }

    // Calculate the Cobb Boost
    // For every 100 meters of perfect DGZ, award 0.8 ratio units
    let cobbBoost = (saturatedDgzDepth_meters / 100) * 0.8;

    // Scale boost by upward lift if present
    if (dgzLayersCount > 0) {
        avgLiftInDgz = avgLiftInDgz / dgzLayersCount;
        cobbBoost = cobbBoost * (1 + avgLiftInDgz);
    }

    let hybridSlrValue = baseSlr + cobbBoost;

    // 4. MECHANICAL WIND COMPACTION: The "Sierra Penalty"
    // Average the wind speed of lowest 2 layers (boundary layer)
    const lowerLayers = sorted.slice(0, 2);
    const avgLowerWindSpeedMs = lowerLayers.reduce((sum, layer) => sum + layer.wind_speed, 0) / lowerLayers.length;
    const avgLowerWindSpeedKmh = avgLowerWindSpeedMs * 3.6;

    // High winds fracture dendrites. 
    // Reduce SLR by 1.5% for every km/h over 30, capped at 45% reduction.
    if (avgLowerWindSpeedKmh > 30) {
        const compactionFactor = Math.min(0.45, (avgLowerWindSpeedKmh - 30) * 0.015);
        hybridSlrValue = hybridSlrValue * (1 - compactionFactor);
    }

    return { slr: Math.round(Math.max(1.0, hybridSlrValue) * 10) / 10, isSnow: true };
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
export function calcSLR(hour: OpenMeteoHour, method: string = 'hybrid', prevSlr: number | null = null): SLROutput {
    const P = hour.precipitation ?? 0;

    const out: SLROutput = { slr: null, snow_cm: 0, method, isSnow: false, qpf_corrected: P };

    if (P <= 0) return out;

    // Shared pre-flight rain check
    const rh_sfc = hour.relative_humidity_2m ?? 70;
    const Tw_sfc = hour.wet_bulb_temperature_2m ?? calculateWetBulb(hour.temperature_2m, rh_sfc);
    if (Tw_sfc >= 4.4) return out;

    out.isSnow = true;
    let slr = 10;

    // ── Dispatch table ────────────────────────────────────────────────────────
    // Each entry is [qpfMultiplier, algorithmFn].
    // The 'kinematic' method applies a QPF orographic correction; all others use raw QPF.
    type AlgoFn = (h: OpenMeteoHour, qpf: number) => { slr: number; isSnow: boolean };
    const dispatch: Record<string, [number, AlgoFn]> = {
        kinematic:    [1.43,  advancedSLR],
        simple:       [1,     simpleSLR],
        dendro:       [1,     dendroSLR],
        krc:          [1,     krcCompSLR],
        kuchera_dgz:  [1,     kucheraDgzSLR],
        kuchera_plus: [1,     kucheraDgzPlusSLR],
        cobb:         [1,     cobbSLR],
        hybrid:       [1,     hybridSLR],
    };

    if (method === 'model_native') {
        const h_snowfall_cm = hour.snowfall ?? 0;
        slr = (P > 0 && h_snowfall_cm > 0) ? (h_snowfall_cm * 10) / P : 10;
        out.qpf_corrected = P;

    } else if (method === 'standard') {
        slr = 10;
        out.qpf_corrected = P;

    } else {
        const entry = dispatch[method] ?? dispatch['kinematic'];
        const [qpfMult, fn] = entry;
        const qpf = P * qpfMult;
        out.qpf_corrected = qpf;
        const result = fn(hour, qpf);
        if (!result.isSnow) { out.isSnow = false; return out; }
        slr = result.slr;
    }

    slr = Math.max(1, Math.min(slr, 30));

    // Temporal smoothing (not for model_native)
    if (prevSlr !== null && method !== 'model_native') {
        slr = Math.max(prevSlr - 2.5, Math.min(slr, prevSlr + 2.5));
    }

    out.slr = +slr.toFixed(1);
    out.snow_cm = +((out.qpf_corrected ?? P) * slr / 10).toFixed(1);
    return out;
}
