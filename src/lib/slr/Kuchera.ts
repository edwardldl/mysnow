/**
 * Represents a single layer in the atmospheric profile.
 */
interface AtmosphericLayer {
    pressure_hPa: number;
    temperature_C: number;
}

/**
 * Calculates the Snow-to-Liquid Ratio (SLR) using the Vanilla Kuchera method,
 * accounting for surface elevation by filtering out subterranean pressure levels.
 *
 * @param profile - An array of atmospheric layers containing pressure and temperature.
 * @param surfacePressure_hPa - The actual atmospheric pressure at the location's surface.
 * @returns The estimated Snow-to-Liquid Ratio (e.g., 14.5).
 */
export function calculateKucheraSLR(
    profile: AtmosphericLayer[],
    surfacePressure_hPa: number
): number {
    if (!profile || profile.length === 0) {
        throw new Error("Atmospheric profile cannot be empty.");
    }

    // 1. Filter out pressure levels that are underground.
    // A standard pressure level (e.g., 1000 hPa) is underground if it is 
    // GREATER than the actual surface pressure (e.g., 850 hPa in the mountains).
    const aboveGroundProfile = profile.filter(
        layer => layer.pressure_hPa <= surfacePressure_hPa && layer.temperature_C != null
    );

    if (aboveGroundProfile.length === 0) {
        // Fallback if data is missing or highly anomalous
        return 10.0;
    }

    // 2. Find the maximum temperature in the valid, above-ground atmospheric column
    const maxTempC = Math.max(...aboveGroundProfile.map(layer => layer.temperature_C));

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

    // Safety bound: Prevent unrealistic negative or highly skewed ratios 
    // if temperatures are extremely warm. 
    return Math.max(1.0, slr);
}

// ==========================================
// Example Usage with Open-Meteo variables
// ==========================================

/*
// Assume `weatherData` is the parsed JSON response from the Open-Meteo API.
const hourIndex = 0; // Example: evaluating the current hour

// Extract the surface pressure for this specific hour
const currentSurfacePressure = weatherData.hourly.surface_pressure[hourIndex];

// Construct the atmospheric profile, pairing the level with its temperature
const atmosphericProfile: AtmosphericLayer[] = [
    { pressure_hPa: 1000, temperature_C: weatherData.hourly.temperature_1000hPa[hourIndex] },
    { pressure_hPa: 975, temperature_C: weatherData.hourly.temperature_975hPa[hourIndex] },
    { pressure_hPa: 950, temperature_C: weatherData.hourly.temperature_950hPa[hourIndex] },
    { pressure_hPa: 925, temperature_C: weatherData.hourly.temperature_925hPa[hourIndex] },
    { pressure_hPa: 900, temperature_C: weatherData.hourly.temperature_900hPa[hourIndex] },
    { pressure_hPa: 850, temperature_C: weatherData.hourly.temperature_850hPa[hourIndex] },
    { pressure_hPa: 800, temperature_C: weatherData.hourly.temperature_800hPa[hourIndex] },
    { pressure_hPa: 700, temperature_C: weatherData.hourly.temperature_700hPa[hourIndex] },
    { pressure_hPa: 600, temperature_C: weatherData.hourly.temperature_600hPa[hourIndex] },
    { pressure_hPa: 500, temperature_C: weatherData.hourly.temperature_500hPa[hourIndex] }
];

const currentSLR = calculateKucheraSLR(atmosphericProfile, currentSurfacePressure);

console.log(`Surface Pressure: ${currentSurfacePressure} hPa`);
console.log(`Estimated Kuchera SLR: ${currentSLR.toFixed(1)}:1`);
*/