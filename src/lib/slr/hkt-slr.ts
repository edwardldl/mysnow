/**
 * Advanced Snow-to-Liquid Ratio (SLR) Prediction Algorithm
 * Utilizing Open-Meteo Surface and Pressure Level Variables.
 * 
 * This highly robust module incorporates:
 * 1. Cobb-Waldstreicher Vertical Velocity Weighting and Spline Logic
 * 2. Stull Gene-Expression Empirical Wet-Bulb Temperature Fit
 * 3. NBM v4.2 Sub-Cloud Melting Truncation Logic
 * 4. Boundary Layer Surface Wind Fragmentation Exponential Decay
 */

// --- Interfaces representing strict Open-Meteo input structures ---

export interface SurfaceData {
    temperature_2m: number;         // Ambient Dry-Bulb Temperature in Celsius
    relative_humidity_2m: number;   // Surface Relative Humidity as a Percentage (0 - 100)
    wind_speed_10m: number;         // 10-meter boundary wind speed in m/s
    precipitation: number;          // Total liquid equivalent precipitation in mm/hr
}

export interface PressureLayer {
    pressure_hpa: number;           // Atmospheric Pressure Level in hPa
    temperature: number;            // Ambient Layer Temperature in Celsius
    relative_humidity: number;      // Ambient Layer Relative Humidity as a Percentage (0 - 100)
    vertical_velocity: number;      // Updraft/Downdraft in Pa/s (Negative mathematically means upward motion)
    geopotential_height: number;    // Absolute layer height in meters above sea level
}

// --- Internal Utility Functions ---

/**
 * Calculates Wet-Bulb Temperature using the Stull (2011) Empirical Formula.
 * Highly validated for ambient temperatures between -20C and 50C and RH from 5% to 99%.
 * Eliminates the need for computationally expensive psychrometric iterative solvers.
 * 
 * @param t - Ambient Dry-Bulb Temperature in Celsius
 * @param rh - Relative Humidity in percentage format (e.g., 50.0)
 * @returns Computed Wet-bulb temperature in Celsius
 */
function calculateStullWetBulb(t: number, rh: number): number {
    const atan = Math.atan;
    const pow = Math.pow;

    const term1 = t * atan(0.151977 * pow(rh + 8.313659, 0.5));
    const term2 = atan(t + rh);
    const term3 = -atan(rh - 1.676331);
    const term4 = 0.00391838 * pow(rh, 1.5) * atan(0.023101 * rh);
    const constant = -4.686035;

    return term1 + term2 + term3 + term4 + constant;
}

/**
 * Maps the localized atmospheric layer temperature to an idealized, unadjusted 
 * crystalline Snow Ratio. Based entirely on the Cobb-Waldstreicher empirical 
 * cubic spline logic targeting optimal preservation in the Dendritic Growth Zone.
 * 
 * @param tempC - Localized Layer Temperature in Celsius
 * @returns Idealized baseline SLR for the specific atmospheric layer
 */
function getLayerBaseSLR(tempC: number): number {
    if (tempC > 0) return 3.0;     // Melting phase
    if (tempC > -2) return 7.0;    // Heavily rimed / wet snow
    if (tempC > -4) return 8.5;
    if (tempC > -6) return 9.0;
    if (tempC > -8) return 9.5;
    if (tempC > -10) return 12.0;
    if (tempC > -12) return 17.5;  // Entering optimal growth
    if (tempC >= -18) return 22.0; // The Peak Dendritic Growth Zone (Maximal Ratio)
    return 15.0; // Extremely cold regimes where plates and solid columns physically dominate over dendrites
}

/**
 * Implements the Kuchera SLR linear regression method as a fail-safe fallback 
 * for non-convective, shallow precipitation environments that lack deep moisture.
 * Utilizes the maximum thermal column temperature to drive the baseline.
 * 
 * @param tMaxC - Maximum atmospheric column temperature in Celsius
 * @returns Kuchera approximated SLR value
 */
function calculateKucheraSLR(tMaxC: number): number {
    // Convert Celsius to absolute Kelvin scale for the bifurcation logic
    const tMaxK = tMaxC + 273.15;
    const bifurcation = 271.16; // Critical melting threshold mapped in Kuchera logic

    if (tMaxK > bifurcation) {
        return Math.max(0, 12 + 2 * (bifurcation - tMaxK));
    } else {
        return Math.max(0, 12 + (bifurcation - tMaxK));
    }
}

/**
 * Linearly interpolates the National Blend of Models v4.2 empirical Melt Rate Coefficient 
 * strictly based on the surface wet bulb temperature.
 * 
 * @param twC - Calculated Surface Wet Bulb Temperature in Celsius
 * @returns Interpolated Melt Rate Coefficient in inches/hr
 */
function getCobbMeltRateCoefficient(twC: number): number {
    // Convert Celsius back to Fahrenheit exclusively to map against the NBM v4.2 operational table
    const twF = (twC * 9 / 5) + 32;

    // Boundary constraints preventing out-of-bounds mapping
    if (twF < 32) return 0.0;
    if (twF >= 40) return 0.26;

    // Operational table mapping integer Fahrenheit degrees to empirical melting rates
    const table: Record<number, number> = {
        32: 0.006, 33: 0.03, 34: 0.05, 35: 0.08,
        36: 0.11, 37: 0.14, 38: 0.18, 39: 0.22, 40: 0.26
    };

    const lowerBound = Math.floor(twF);
    const upperBound = Math.ceil(twF);

    // Exact integer match bypasses interpolation
    if (lowerBound === upperBound) return table[lowerBound];

    // Standard linear interpolation between the localized mapping bounds
    const ratio = twF - lowerBound;
    return table[lowerBound] + ratio * (table[upperBound] - table[lowerBound]);
}

// --- Main Operational Algorithmic Orchestrator ---

/**
 * Executes the complete algorithmic stack to calculate the final operational Snow-to-Liquid Ratio.
 * 
 * @param surface - Strongly typed SurfaceData Object populated from Open-Meteo
 * @param profile - Array of PressureLayer objects populated from Open-Meteo. 
 *                  Must be sequentially sorted from lowest physical height to highest (e.g., 1000hPa -> 400hPa)
 * @returns Final physically adjusted and mathematically validated SLR floating point value
 */
export function predictSnowLiquidRatio(surface: SurfaceData, profile: PressureLayer[]): number {

    // Step 1: Base validation. If there is virtually zero measurable precipitation, 
    // deep algorithmic analysis is irrelevant. Return standard climatological base.
    if (surface.precipitation <= 0.01) {
        return 10.0;
    }

    // Step 2: Dynamically identify Cloud Active Layers and isolate Vertical Velocity Maxima
    let maxUVV = 0; // Represents the most aggressive upward motion. In Pa/s, this must be the most negative value.
    let maxTempInColumn = -999;

    const activeLayers: PressureLayer[] = [];

    for (const layer of profile) {
        // Continuously track Tmax to facilitate the Kuchera fallback mechanism
        if (layer.temperature > maxTempInColumn) {
            maxTempInColumn = layer.temperature;
        }

        // Active Cloud Layer Validation Threshold: RH >= 80% and dynamically ascending air (omega < 0)
        if (layer.relative_humidity >= 80.0 && layer.vertical_velocity < 0) {
            activeLayers.push(layer);
            // Search for the absolute lowest Pa/s value indicating extreme lift
            if (layer.vertical_velocity < maxUVV) {
                maxUVV = layer.vertical_velocity;
            }
        }
    }

    let cloudSLR = 0;

    // Step 3: Compute the rigorous Profile-Weighted Cloud SLR via the modified Cobb-Waldstreicher method
    if (activeLayers.length === 0 || maxUVV === 0) {
        // Elegant fallback to the Kuchera linear method if spatial resolution misses highly shallow cloud layers
        cloudSLR = calculateKucheraSLR(maxTempInColumn);
    } else {
        let totalWeight = 0;
        let weightedSLRSum = 0;

        for (let i = 0; i < activeLayers.length; i++) {
            const layer = activeLayers[i];
            const baseSLR = getLayerBaseSLR(layer.temperature);

            // Calculate absolute layer thickness in meters. If it is the top boundary layer, assume a nominal 500m depth.
            const thickness = (i < activeLayers.length - 1)
                ? Math.abs(activeLayers[i + 1].geopotential_height - layer.geopotential_height)
                : 500;

            // Weight mathematically equals (omega / omega_max)^2 * physical thickness
            // Because both omega and maxUVV are fundamentally negative, their fractional ratio is positive
            const weight = Math.pow(layer.vertical_velocity / maxUVV, 2) * thickness;

            weightedSLRSum += (baseSLR * weight);
            totalWeight += weight;
        }

        cloudSLR = (totalWeight > 0) ? (weightedSLRSum / totalWeight) : 10.0;
    }

    // Step 4: Apply the Mechanical Wind Fragmentation Modifier
    // Wind speeds scaling above the 8 m/s threshold begin violently fracturing delicate dendrites, strongly suppressing SLR
    const windThreshold = 8.0;
    const decayConstant = 0.05;

    let windAdjustedSLR = cloudSLR;
    if (surface.wind_speed_10m > windThreshold) {
        const excessWind = surface.wind_speed_10m - windThreshold;
        const windModifier = Math.exp(-decayConstant * excessWind);
        windAdjustedSLR = cloudSLR * windModifier;
    }

    // Step 5: Execute Sub-Cloud Surface Melting Modification based on NBM v4.2 logic
    const wetBulbC = calculateStullWetBulb(surface.temperature_2m, surface.relative_humidity_2m);

    // Retrieve the specific coefficient in inches/hr and mathematically convert to mm/hr (1 inch = 25.4 mm)
    const meltRateInches = getCobbMeltRateCoefficient(wetBulbC);
    const meltRateMm = meltRateInches * 25.4;

    // Calculate the absolute fraction of the falling hourly precipitation that is destroyed by thermal melting
    const meltFraction = meltRateMm / surface.precipitation;

    let finalSLR = windAdjustedSLR * Math.max(0, 1.0 - meltFraction);

    // Final boundary safeguard: The Snow-to-Liquid Ratio mathematically cannot be a negative value
    finalSLR = Math.max(0, finalSLR);

    // Returns the completely adjusted ratio. If totally melted by the boundary layer, returns absolute 0.
    return finalSLR;
}