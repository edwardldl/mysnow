/**
 * Represents a single layer in the atmospheric profile.
 */
interface AtmosphericLayer {
    pressure_hPa: number;
    temperature_C: number;
    relative_humidity_percent: number;
    geopotential_height_m: number; // For calculating exact physical depth
    wind_speed_kmh: number;        // For calculating mechanical compaction
    cloud_cover_percent: number;   // To verify the presence of active clouds
}

/**
 * Calculates the Snow-to-Liquid Ratio (SLR) using an enhanced Kuchera method,
 * factoring in surface elevation, true physical DGZ depth, and wind compaction.
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

    // 4. Calculate Saturated DGZ Boost (using True Physical Depth)
    let saturatedDgzDepth_meters = 0;

    for (let i = 0; i < validProfile.length; i++) {
        const layer = validProfile[i];

        // Check if the layer is in the DGZ, has high RH, AND is actively in a cloud
        const isOptimalTemp = layer.temperature_C <= -12 && layer.temperature_C >= -18;
        const isMoistAndCloudy = layer.relative_humidity_percent >= 80 && layer.cloud_cover_percent >= 80;

        if (isOptimalTemp && isMoistAndCloudy) {
            // Calculate the physical height of this atmospheric chunk
            const nextLayer = validProfile[i + 1];
            // If it's the top layer, assume a conservative 300m cap for the final chunk
            const layerThickness_m = nextLayer
                ? Math.abs(nextLayer.geopotential_height_m - layer.geopotential_height_m)
                : 300;

            saturatedDgzDepth_meters += layerThickness_m;
        }
    }

    // Apply the boost: For every 100 meters of optimal, saturated DGZ, add 0.5 to the ratio.
    const dgzBoost = (saturatedDgzDepth_meters / 100) * 0.5;
    let enhancedSlr = baseSlr + dgzBoost;

    // 5. Apply Mechanical Wind Compaction Penalty
    // Average the wind speed of the lowest 2 valid layers (representing the boundary layer/surface)
    const lowerLayers = validProfile.slice(0, 2);
    const avgLowerWindSpeed = lowerLayers.reduce((sum, layer) => sum + layer.wind_speed_kmh, 0) / lowerLayers.length;

    // If winds exceed ~30 km/h, crystals fracture. Reduce SLR by 1.5% for every km/h over 30, capped at a 40% reduction.
    if (avgLowerWindSpeed > 30) {
        const compactionFactor = Math.min(0.40, (avgLowerWindSpeed - 30) * 0.015);
        enhancedSlr = enhancedSlr * (1 - compactionFactor);
    }

    return Math.max(1.0, enhancedSlr); // Ensure SLR never drops below 1:1
}


/*
// Assume `weatherData` is the parsed JSON response from the Open-Meteo API.
const hourIndex = 0;
const surfacePressure = weatherData.hourly.surface_pressure[hourIndex];

// Construct the profile dynamically
const atmosphericProfile: AtmosphericLayer[] = [
    {
        pressure_hPa: 850,
        temperature_C: weatherData.hourly.temperature_850hPa[hourIndex],
        relative_humidity_percent: weatherData.hourly.relative_humidity_850hPa[hourIndex],
        geopotential_height_m: weatherData.hourly.geopotential_height_850hPa[hourIndex],
        wind_speed_kmh: weatherData.hourly.wind_speed_850hPa[hourIndex],
        cloud_cover_percent: weatherData.hourly.cloud_cover_850hPa[hourIndex]
    },
    {
        pressure_hPa: 700, // ~10,000 ft, often where the DGZ sits during cold storms
        temperature_C: weatherData.hourly.temperature_700hPa[hourIndex],
        relative_humidity_percent: weatherData.hourly.relative_humidity_700hPa[hourIndex],
        geopotential_height_m: weatherData.hourly.geopotential_height_700hPa[hourIndex],
        wind_speed_kmh: weatherData.hourly.wind_speed_700hPa[hourIndex],
        cloud_cover_percent: weatherData.hourly.cloud_cover_700hPa[hourIndex]
    },
    // ... continue mapping other relevant levels (e.g., 600, 500, 400)
];

const finalSLR = calculateDGZEnhancedSLR(atmosphericProfile, surfacePressure);

console.log(`Surface Pressure: ${surfacePressure} hPa`);
console.log(`Final Wind & DGZ-Adjusted SLR: ${finalSLR.toFixed(1)}:1`);
*/