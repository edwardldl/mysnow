/**
 * Represents a single layer in the atmospheric profile.
 */
interface AtmosphericLayer {
    pressure_hPa: number;
    temperature_C: number;
    relative_humidity_percent: number; // New variable needed for DGZ
}

/**
 * Calculates the Snow-to-Liquid Ratio (SLR) using an enhanced Kuchera method,
 * factoring in surface elevation and the depth of the Saturated Dendritic Growth Zone.
 *
 * @param profile - An array of atmospheric layers (must be sorted from highest pressure to lowest, e.g., surface to sky).
 * @param surfacePressure_hPa - The actual atmospheric pressure at the location's surface.
 * @returns The estimated Snow-to-Liquid Ratio.
 */
export function calculateDGZEnhancedSLR(
    profile: AtmosphericLayer[],
    surfacePressure_hPa: number
): number {
    if (!profile || profile.length === 0) {
        throw new Error("Atmospheric profile cannot be empty.");
    }

    // 1. Filter out underground levels based on actual surface pressure
    const validProfile = profile.filter(
        layer => layer.pressure_hPa <= surfacePressure_hPa && layer.temperature_C != null
    );

    if (validProfile.length === 0) return 10.0; // Fallback

    // 2. Sort profile from ground up (highest hPa to lowest hPa)
    validProfile.sort((a, b) => b.pressure_hPa - a.pressure_hPa);

    // 3. Calculate Base Vanilla Kuchera
    const maxTempC = Math.max(...validProfile.map(layer => layer.temperature_C));
    const maxTempK = maxTempC + 273.15;
    const KUCHERA_PIVOT = 271.16;

    let baseSlr = 12.0;
    if (maxTempK > KUCHERA_PIVOT) {
        baseSlr = 12 + 2 * (KUCHERA_PIVOT - maxTempK);
    } else {
        baseSlr = 12 + (KUCHERA_PIVOT - maxTempK);
    }

    // Safely bound the base SLR
    baseSlr = Math.max(1.0, baseSlr);

    // 4. Calculate the Saturated DGZ Boost
    let saturatedDgzThickness_hPa = 0;

    for (let i = 0; i < validProfile.length; i++) {
        const layer = validProfile[i];

        // Check if the layer is in the DGZ and has enough moisture to produce snow
        const isOptimalTemp = layer.temperature_C <= -12 && layer.temperature_C >= -18;
        const isMoist = layer.relative_humidity_percent >= 80;

        if (isOptimalTemp && isMoist) {
            // Estimate the thickness of the atmosphere this level represents.
            // If it's the highest level in our array, assume a conservative 50 hPa cap.
            const nextPressure = validProfile[i + 1] ? validProfile[i + 1].pressure_hPa : layer.pressure_hPa - 50;
            const layerThickness = Math.abs(layer.pressure_hPa - nextPressure);

            saturatedDgzThickness_hPa += layerThickness;
        }
    }

    // Apply the boost: For every 25 hPa of saturated DGZ, add 1.5 to the ratio.
    // (This is a tunable heuristic; adjust based on specific local climatology if needed).
    const dgzBoost = (saturatedDgzThickness_hPa / 25) * 1.5;

    return baseSlr + dgzBoost;
}


/*
// Assume `weatherData` is the parsed JSON response from the Open-Meteo API.
const hourIndex = 0;
const surfacePressure = weatherData.hourly.surface_pressure[hourIndex];

// Construct the profile, bringing in Relative Humidity
const atmosphericProfile: AtmosphericLayer[] = [
    {
        pressure_hPa: 1000,
        temperature_C: weatherData.hourly.temperature_1000hPa[hourIndex],
        relative_humidity_percent: weatherData.hourly.relative_humidity_1000hPa[hourIndex]
    },
    {
        pressure_hPa: 975,
        temperature_C: weatherData.hourly.temperature_975hPa[hourIndex],
        relative_humidity_percent: weatherData.hourly.relative_humidity_975hPa[hourIndex]
    },
    // ... continue mapping 950, 925, 900, 850, 800, 700, 600 ...
    {
        pressure_hPa: 500,
        temperature_C: weatherData.hourly.temperature_500hPa[hourIndex],
        relative_humidity_percent: weatherData.hourly.relative_humidity_500hPa[hourIndex]
    }
];

const enhancedSLR = calculateDGZEnhancedSLR(atmosphericProfile, surfacePressure);

console.log(`Surface Pressure: ${surfacePressure} hPa`);
console.log(`DGZ-Enhanced SLR: ${enhancedSLR.toFixed(1)}:1`);
*/