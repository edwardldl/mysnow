export interface PressureLevelData {
    pressure_hPa: number;
    temperature_C: number;
    relative_humidity_pct: number;
    geopotential_height_m: number;
}

/**
 * Calculates the SLR based on an adapted Cobb Algorithm, improved with a DGZ multiplier.
 */
export function calculateEnhancedSLR(
    levels: PressureLevelData[],
    surfaceElevationMeters: number
): number {

    // 1. Filter out subterranean levels and sort
    const aboveGroundLevels = levels.filter(
        level => level.geopotential_height_m >= surfaceElevationMeters
    );
    const sortedLevels = [...aboveGroundLevels].sort(
        (a, b) => a.geopotential_height_m - b.geopotential_height_m
    );

    let totalWeightedSLR = 0;
    let totalWeight = 0;
    let saturatedDGZDepthMeters = 0; // Track the thickness of the saturated DGZ

    for (let i = 0; i < sortedLevels.length - 1; i++) {
        const bottom = sortedLevels[i];
        const top = sortedLevels[i + 1];

        const avgRH = (bottom.relative_humidity_pct + top.relative_humidity_pct) / 2;
        if (avgRH < 90) continue; // Cloud layer threshold

        const avgTemp = (bottom.temperature_C + top.temperature_C) / 2;
        const thickness = top.geopotential_height_m - bottom.geopotential_height_m;

        // -- NEW: Track the DGZ --
        // If the layer's average temperature falls cleanly in the DGZ, add to our tracker
        if (avgTemp <= -12 && avgTemp >= -18) {
            saturatedDGZDepthMeters += thickness;
        }

        const layerSLR = getLayerSLR(avgTemp);
        const weight = thickness;

        totalWeightedSLR += layerSLR * weight;
        totalWeight += weight;
    }

    if (totalWeight === 0) return 0;

    let baseSLR = totalWeightedSLR / totalWeight;

    // -- NEW: Apply DGZ Depth Bonus --
    // If the saturated DGZ is thicker than 1,000 meters, it will dominate crystal production.
    // We apply a gentle multiplier (up to a max of ~1.25x) based on how deep the DGZ is.
    if (saturatedDGZDepthMeters > 1000) {
        // For every 1000m of saturated DGZ beyond the first 1000m, add a 10% bonus
        const extraDepth = saturatedDGZDepthMeters - 1000;
        const dgzMultiplier = 1 + (Math.min(extraDepth / 1000, 2.5) * 0.10);

        baseSLR = baseSLR * dgzMultiplier;
    }

    return baseSLR;
}

function getLayerSLR(tempC: number): number {
    if (tempC > 0) return 0;
    // Cobb piecewise curve
    if (tempC <= 0 && tempC > -16) return 3 + (tempC * -1.4375);
    if (tempC <= -16 && tempC > -30) return 26 + ((tempC + 16) * 1.342857);
    return 7.2;
}