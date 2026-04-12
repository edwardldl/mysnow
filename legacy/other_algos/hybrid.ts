/**
 * Represents a single layer in the atmospheric profile.
 */
interface AtmosphericLayer {
    pressure_hPa: number;
    temperature_C: number;
    relative_humidity_percent: number;
    geopotential_height_m: number; // For True Physical Depth
    wind_speed_kmh: number;        // For Mechanical Compaction
    cloud_cover_percent: number;   // For Active Cloud Verification
    vertical_velocity_ms?: number; // Optional: True Cobb requires upward lift
}

/**
 * Calculates a hybrid Snow-to-Liquid Ratio (SLR) by combining a Kuchera baseline
 * with a Cobb-inspired Saturated Dendritic Growth Zone (DGZ) microphysics boost,
 * adjusted for elevation and mechanical wind compaction.
 *
 * @param profile - Array of atmospheric layers (surface to sky).
 * @param surfacePressure_hPa - Actual atmospheric pressure at the location's surface.
 * @returns The estimated Snow-to-Liquid Ratio.
 */
export function calculateHybridSLR(
    profile: AtmosphericLayer[],
    surfacePressure_hPa: number
): number {
    if (!profile || profile.length === 0) {
        throw new Error("Atmospheric profile cannot be empty.");
    }

    // 1. ELEVATION ANCHOR: Filter out layers below the mountain surface
    const validProfile = profile.filter(
        layer => layer.pressure_hPa <= surfacePressure_hPa && layer.temperature_C != null
    );

    if (validProfile.length === 0) return 10.0; // Fallback rule of thumb

    // Sort from the ground up (highest pressure to lowest)
    validProfile.sort((a, b) => b.pressure_hPa - a.pressure_hPa);


    // 2. KUCHERA BASELINE: The "Warmest Layer" approach
    const maxTempC = Math.max(...validProfile.map(layer => layer.temperature_C));
    const maxTempK = maxTempC + 273.15;
    const KUCHERA_PIVOT = 271.16;

    let baseSlr = 12.0;
    if (maxTempK > KUCHERA_PIVOT) {
        baseSlr = 12 + 2 * (KUCHERA_PIVOT - maxTempK);
    } else {
        baseSlr = 12 + (KUCHERA_PIVOT - maxTempK);
    }

    // Safely bound the Kuchera baseline (Sierra cement can get dense, but rarely below 4:1)
    baseSlr = Math.max(4.0, baseSlr);


    // 3. COBB-INSPIRED DGZ BOOST: The "Microphysics" approach
    let saturatedDgzDepth_meters = 0;
    let avgLiftInDgz = 0;
    let dgzLayersCount = 0;

    for (let i = 0; i < validProfile.length; i++) {
        const layer = validProfile[i];

        // The Magic Intersection: Optimal Temp + High Moisture + Active Cloud
        const isOptimalTemp = layer.temperature_C <= -12 && layer.temperature_C >= -18;
        const isMoist = layer.relative_humidity_percent >= 80;
        const isCloudy = layer.cloud_cover_percent >= 80;

        if (isOptimalTemp && isMoist && isCloudy) {
            // TRUE PHYSICAL DEPTH: Calculate the height of this atmospheric chunk
            const nextLayer = validProfile[i + 1];
            // If it's the top layer, assume a standard 300m cap for the final chunk
            const layerThickness_m = nextLayer
                ? Math.abs(nextLayer.geopotential_height_m - layer.geopotential_height_m)
                : 300;

            saturatedDgzDepth_meters += layerThickness_m;

            // Track lift if we are passing in Orographic Lift calculations
            if (layer.vertical_velocity_ms && layer.vertical_velocity_ms > 0) {
                avgLiftInDgz += layer.vertical_velocity_ms;
                dgzLayersCount++;
            }
        }
    }

    // Calculate the Cobb Boost
    // Dendrites need space to grow. We award a ratio boost for every 100 meters of perfect DGZ.
    let cobbBoost = (saturatedDgzDepth_meters / 100) * 0.8;

    // If we have upward lift data (Omega / Orographic Force), scale the boost heavily
    if (dgzLayersCount > 0) {
        avgLiftInDgz = avgLiftInDgz / dgzLayersCount;
        cobbBoost = cobbBoost * (1 + avgLiftInDgz);
    }

    // Combine Kuchera Baseline + Cobb Boost
    let hybridSlr = baseSlr + cobbBoost;


    // 4. MECHANICAL WIND COMPACTION: The "Sierra Penalty"
    // Average the wind speed of the lowest 2 valid layers (the boundary layer interacting with terrain)
    const lowerLayers = validProfile.slice(0, 2);
    const avgLowerWindSpeed = lowerLayers.reduce((sum, layer) => sum + layer.wind_speed_kmh, 0) / lowerLayers.length;

    // High winds fracture dendrites into needles and grains. 
    // Reduce SLR by 1.5% for every km/h over 30, capped at a massive 45% reduction for extreme gales.
    if (avgLowerWindSpeed > 30) {
        const compactionFactor = Math.min(0.45, (avgLowerWindSpeed - 30) * 0.015);
        hybridSlr = hybridSlr * (1 - compactionFactor);
    }

    return Math.max(1.0, hybridSlr); // Ensure SLR is physically possible
}