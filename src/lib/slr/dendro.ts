type ForecastData = {
    // Hourly arrays from Open-Meteo forecast JSON:
    hourly: {
        time: string[];
        temperature_2m: number[];  // °C
        relative_humidity_2m: number[];  // %
        wind_speed_10m: number[]; // km/h
        // Pressure-level temperatures (e.g., from ERA5 via Open-Meteo):
        temperature_850hPa?: number[];  // °C
        temperature_700hPa?: number[];  // °C
        temperature_500hPa?: number[];  // °C
        relative_humidity_700hPa?: number[]; // %
        // Other features:
        total_column_integrated_water_vapour?: number[]; // mm
        cape?: number[];  // J/kg
        precipitation: number[]; // mm/h
        rain: number[];
        snowfall: number[]; // cm/h
        snow_depth: number[]; // m
        freezing_level_height: number[]; // m
    };
    elevation: number; // meters (user-specified or default 90m)
};

function calcSLRForHour(data: ForecastData, idx: number): number {
    // Extract variables for this hour
    const wind10 = data.hourly.wind_speed_10m[idx];
    const elev = data.elevation;
    const t850 = data.hourly.temperature_850hPa ? data.hourly.temperature_850hPa[idx] : undefined;
    const t700 = data.hourly.temperature_700hPa ? data.hourly.temperature_700hPa[idx] : undefined;
    const t500 = data.hourly.temperature_500hPa ? data.hourly.temperature_500hPa[idx] : undefined;
    const rh700 = data.hourly.relative_humidity_700hPa ? data.hourly.relative_humidity_700hPa[idx] : undefined;
    const tcwv = data.hourly.total_column_integrated_water_vapour ? data.hourly.total_column_integrated_water_vapour[idx] : undefined;
    const freezeH = data.hourly.freezing_level_height[idx];

    // Helper: Estimate missing 700 hPa temp if needed
    function estimate700(a?: number, b?: number): number | undefined {
        if (a == null || b == null) return undefined;
        return a - (a - b) * 0.5; // simple linear interp at ~800 hPa
    }
    const T700 = (t700 != undefined) ? t700 : estimate700(t850, t500);

    // 1) DGZ Score: Find pressures for -12°C and -18°C in profile
    let dgzDepth = 0;
    if (t850 != null && T700 != null && t500 != null) {
        // Linear interpolation approach: find where line between (850hPa,T850) and (500hPa,T500) crosses -12 and -18
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
    const dgzScore = clamp(dgzDepth / 300, 0, 1);  // 300 hPa = full score

    // 2) Cold column score: average of 850/700/500 layer
    let coldAvg = 0, count = 0;
    if (t850 != null) { coldAvg += t850; count++; }
    if (T700 != null) { coldAvg += T700; count++; }
    if (t500 != null) { coldAvg += t500; count++; }
    const coldScore = count > 0 ? clamp((-coldAvg / count - 5) / 25, 0, 1) : 0;
    // (Maps coldAvg ~ -30°C →1, -5°C →0)

    // 3) Moisture score (mid-level RH / precipitable water)
    let moistSum = 0;
    if (rh700 != undefined) moistSum += clamp((rh700 - 80) / 20, 0, 1);
    if (tcwv != undefined) moistSum += clamp(tcwv / 20, 0, 1);
    const moistureScore = clamp(moistSum, 0, 1);

    // 4) Freezing level penalty
    const meltPenalty = freezeH > 2000 ? 0.9 : 1.0;
    // (If freezing above 2000m, multiply SLR by 0.9)

    // 5) Wind compaction penalty
    const windPen = clamp(wind10 / 15, 0, 1);

    // 6) Orographic (wind*elev) penalty
    const upliftScore = clamp((wind10 * elev) / 200000, 0, 1);
    const upliftPen = 1 - 0.3 * upliftScore;

    // Base SLR from growth and moisture
    let slr = 5 + 15 * dgzScore + 10 * coldScore + 5 * moistureScore;
    // Apply penalties
    slr *= (1 - 0.3 * windPen);
    slr *= upliftPen;
    slr *= meltPenalty;

    // Clamp to realistic range
    return clamp(slr, 3, 30);
}

/**
 * Calculate SLR for each hour of forecast
 * Returns array of SLR values (per hour) based on input JSON data.
 */
export function calculateHourlySLR(forecast: ForecastData): number[] {
    const n = forecast.hourly.time.length;
    const results: number[] = [];
    for (let i = 0; i < n; i++) {
        // If no snow is falling, SLR=0 (no snow)
        if (forecast.hourly.precipitation[i] === 0 && forecast.hourly.snowfall[i] === 0) {
            results.push(0);
        } else {
            results.push(calcSLRForHour(forecast, i));
        }
    }
    return results;
}

// Helper clamp
function clamp(v: number, min: number, max: number): number {
    return Math.max(min, Math.min(max, v));
}
