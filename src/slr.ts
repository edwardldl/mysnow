/**
 * SLR Calculator - Multi-Algorithm Snow-to-Liquid Ratio Physics
 */

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
    dew_point_2m: number;
    wind_speed_10m: number;
    precipitation: number;
    snowfall: number;
    relative_humidity_2m: number;
    wet_bulb_temperature_2m: number;
    specific_humidity_2m: number;
    pressure_msl: number;
    soil_temperature_0cm: number;
    snow_depth: number;
    layers: PressureLayer[];
}

export interface SLROutput {
    slr: number | null;
    snow_cm: number;
    method: string;
    isSnow: boolean;
}

/**
 * Clamp a value between min and max
 */
function clamp(v: number, min: number, max: number): number {
    return Math.max(min, Math.min(max, v));
}

/**
 * Calculates Wet-Bulb approximation if native isn't available
 */
export function calcWetBulbFallback(T: number, RH: number): number {
    return T - ((100 - RH) / 5);
}

/**
 * Standard NWS Kuchera Method
 * Emphasizes maximum temperature in the atmospheric profile below 500hPa (approximated here via 700 & 850hPa).
 */
function kucheraSLR(T_2m: number, T_850: number, T_700: number): number {
    // Convert max temp to Kelvin
    const maxT = Math.max(T_2m, T_850, T_700);
    const maxTK = maxT + 273.15;
    
    // Kuchera equations
    if (maxTK > 271.16) {
        return 12 + 2 * (271.16 - maxTK);
    } else {
        return 12 + (271.16 - maxTK);
    }
}

/**
 * Roebber (2003) Neural Network Approximation
 * Calibrated using Surface Temp (C), RH (%), and Wind Speed (m/s).
 */
function roebberApprox(T: number, RH: number, W: number): number {
    // simplified polynomial representation
    let rho = 0.0122 * T * T + 0.159 * T + 0.141 + 0.00316 * W;
    rho = rho * 1.18; // maritime tune
    rho = clamp(rho, 0.05, 0.20);
    return 1 / (rho * 10); // Base SLR usually given as cm/mm
}

/**
 * Cobb Method Approximation
 * Maximize SLR if dendritic growth zone (-12 to -18C) is hit at 700/850mb.
 */
function cobbApprox(T_850: number, T_700: number, T_2m: number): number {
    let slr = 14; 
    
    const inDGZ = (t: number) => t <= -12 && t >= -18;
    if (inDGZ(T_850) || inDGZ(T_700)) {
        slr = 22; // huge boost for DGZ
    } else if (T_850 > 0 || T_700 > 0) {
        slr = 5; // Melting layer
    } else {
        slr = 14 + ((-10 - Math.max(T_850, T_700, T_2m)) * 0.5); 
    }
    return slr;
}

/**
 * Main switchboard to calculate SLR
 * @param hour The hourly data point from Open-Meteo
 * @param method The algorithm mode to use 
 */
/**
 * Cobb-Waldstreicher 2011 Temperature Mapping
 */
function getLayerSnowRatio(temp: number): number {
    if (temp <= -20) return 18.0;
    if (temp <= -18) return 18.0 + ((temp - -20) / 2) * (23.0 - 18.0);
    if (temp <= -16) return 23.0 + ((temp - -18) / 2) * (26.0 - 23.0);
    if (temp <= -14) return 26.0 + ((temp - -16) / 2) * (22.5 - 26.0);
    if (temp <= -12) return 22.5 + ((temp - -14) / 2) * (17.5 - 22.5);
    if (temp <= -10) return 17.5 + ((temp - -12) / 2) * (12.0 - 17.5);
    if (temp <= -8) return 12.0 + ((temp - -10) / 2) * (9.5 - 12.0);
    if (temp <= -4) return 9.5 + ((temp - -8) / 4) * (8.5 - 9.5);
    if (temp <= -2) return 8.5 + ((temp - -4) / 2) * (7.0 - 8.5);
    if (temp <= 0) return 7.0 + ((temp - -2) / 2) * (3.0 - 7.0);
    if (temp < 1) return 3.0 + (temp / 1) * (0.0 - 3.0);
    return 0.0;
}


/**
 * Advanced Kinematic Physical Snow Model (Steps 1-6)
 */
function kinematicPhysicalSLR(hour: OpenMeteoHour): number {
    // Step 1: Isolate the Snow Production Zone (SPZ)
    const activeLayers = hour.layers.filter(l => l.rh >= 90 && l.omega < 0);

    if (activeLayers.length === 0) return 10.0; // Fallback if no active SPZ but precip occurs

    // Sort by pressure descending (bottom to top)
    activeLayers.sort((a, b) => b.pressure - a.pressure);

    // Calculate Layer thicknesses (Delta Z)
    const spzWithWeights = activeLayers.map((layer, idx) => {
        let deltaZ = 1000;
        if (idx < activeLayers.length - 1) {
            deltaZ = Math.max(100, activeLayers[idx+1].gz - layer.gz);
        }
        return {
            ...layer,
            deltaZ,
            lsr: getLayerSnowRatio(layer.temp),
            kinematic_weight: Math.abs(layer.omega) * deltaZ
        };
    });

    const totalWeight = spzWithWeights.reduce((sum, l) => sum + l.kinematic_weight, 0) || 1;

    // Step 2: Calculate Kinematic Base Ratio (SLR_base)
    const SLR_base = spzWithWeights.reduce((sum, l) => {
        const W_i = l.kinematic_weight / totalWeight;
        return sum + (W_i * l.lsr);
    }, 0);

    // Step 3: Apply Dynamic Riming Penalty (F_rime)
    const warmLayers = spzWithWeights.filter(l => l.temp >= -10 && l.temp <= 0);
    const omega_warm_max = warmLayers.length > 0 ? Math.max(...warmLayers.map(l => Math.abs(l.omega))) : 0;
    const omega_max = Math.max(...spzWithWeights.map(l => Math.abs(l.omega)));
    
    let F_rime = 1.0;
    if (omega_max > 0) {
        F_rime = 1.0 - (0.50 * (omega_warm_max / omega_max));
    }

    // Step 4: Apply Wind Compaction Penalty (F_wind)
    let wind_speed_700 = hour.layers.find(l => l.pressure === 700)?.wind_speed || 0;
    let U_max = Math.max(hour.wind_speed_10m || 0, wind_speed_700);
    
    let F_wind = 1.0;
    if (U_max > 8) {
        F_wind = 1.0 - 0.15 * Math.log(U_max - 7);
        F_wind = Math.max(0.2, F_wind);
    }

    // Step 5: Apply Boundary Layer Melt Penalty (F_melt)
    const Tw = hour.wet_bulb_temperature_2m !== null && hour.wet_bulb_temperature_2m !== undefined 
        ? hour.wet_bulb_temperature_2m 
        : calcWetBulbFallback(hour.temperature_2m, hour.relative_humidity_2m);
        
    let F_melt = 1.0;
    if (hour.temperature_2m > 0) {
        F_melt = Math.exp(-0.5 * Math.max(0, Tw));
    }

    // Step 6: Final Calculation
    let SLR_Final = SLR_base * F_rime * F_wind * F_melt;

    if (hour.temperature_2m >= 1.6) {
        SLR_Final = SLR_Final / 2;
    }

    return clamp(SLR_Final, 1, 30);
}

/**
 * Main switchboard to calculate SLR
 * @param hour The hourly data point from Open-Meteo
 * @param method The algorithm mode to use 
 */
export function calcSLR(hour: OpenMeteoHour, method: string = 'kinematic', prevSlr: number | null = null): SLROutput {
    const P = hour.precipitation || 0;
    const T2 = hour.temperature_2m;
    const Tw = hour.wet_bulb_temperature_2m !== undefined && hour.wet_bulb_temperature_2m !== null ? hour.wet_bulb_temperature_2m : calcWetBulbFallback(T2, hour.relative_humidity_2m || 70);
    const RH = hour.relative_humidity_2m || 70;
    
    // Fallback estimation of upper temps if layers don't have it (for other modes)
    const t850_layer = hour.layers.find(l => l.pressure === 850);
    const t700_layer = hour.layers.find(l => l.pressure === 700);
    const T850 = t850_layer ? t850_layer.temp : T2 - 5;
    const T700 = t700_layer ? t700_layer.temp : T850 - 5;
    const WS = hour.wind_speed_10m || 0; // m/s
    
    let out = { slr: null as number | null, snow_cm: 0, method: method, isSnow: false };

    if (P <= 0 || Tw > 1.5) {
        return out; 
    }

    out.isSnow = true;
    let slr = 10; 

    if (method === 'model_native') {
        const h_snowfall_cm = hour.snowfall || 0;
        if (P > 0 && h_snowfall_cm > 0) {
            slr = (h_snowfall_cm * 10) / P;
        } else {
            slr = 10;
        }
    } 
    else if (method === 'standard') {
        slr = 10;
    }
    else if (method === 'kuchera') {
        slr = kucheraSLR(T2, T850, T700);
    }
    else if (method === 'roebber') {
        let tempUse = Math.min(Tw, 0); // clamp for Roebber poly
        slr = roebberApprox(tempUse, RH, WS);
    }
    else if (method === 'cobb_approx') {
        slr = cobbApprox(T850, T700, T2);
    }
    else if (method === 'sierra_custom' || method === 'kinematic') {
        // Advanced Kinematic Physical Model
        slr = kinematicPhysicalSLR(hour);
    }

    // Check soil melting for all physical approximations
    if (['sierra_custom', 'cobb_approx', 'kuchera', 'roebber', 'kinematic'].includes(method)) {
        if (hour.soil_temperature_0cm !== undefined && hour.soil_temperature_0cm > 1.0) {
            slr = slr * Math.max(0.2, (1 - (hour.soil_temperature_0cm / 5)));
        }
    }

    slr = clamp(slr, 1, 30);
    
    if (prevSlr !== null && method !== 'model_native') {
        slr = clamp(slr, prevSlr - 2.5, prevSlr + 2.5);
    }

    out.slr = +slr.toFixed(1);
    out.snow_cm = +(P * slr / 10).toFixed(1);

    return out;
}
