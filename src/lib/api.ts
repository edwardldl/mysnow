import type { Location } from './types';

const DEFAULT_LOCATIONS: Record<string, Location> = {
    palisades: {
        id: 'palisades',
        name: 'Palisades Tahoe',
        latitude: 39.193416,
        longitude: -120.245402,
        elevationFt: 8100,
        elevationM: 2470,
        isCustom: false
    }
};

export function getLocations() {
    const customLocsJson = localStorage.getItem('calisnow_locations');
    const customLocs = customLocsJson ? JSON.parse(customLocsJson) : {};
    return { ...DEFAULT_LOCATIONS, ...customLocs };
}

export function saveLocation(id: string, name: string, lat: string, lon: string): Record<string, Location> {
    const customLocsJson = localStorage.getItem('calisnow_locations');
    const customLocs: Record<string, Location> = customLocsJson ? JSON.parse(customLocsJson) : {};

    // Elevation is populated on first successful API fetch for custom locations.
    customLocs[id] = {
        id,
        name,
        latitude: parseFloat(lat),
        longitude: parseFloat(lon),
        elevationFt: '--',
        elevationM: '--',
        isCustom: true
    };

    localStorage.setItem('calisnow_locations', JSON.stringify(customLocs));
    return getLocations();
}

export function removeLocation(id: string): Record<string, Location> {
    if (DEFAULT_LOCATIONS[id]) return getLocations(); // Can't delete defaults

    const customLocsJson = localStorage.getItem('calisnow_locations');
    if (!customLocsJson) return getLocations();

    const customLocs = JSON.parse(customLocsJson);
    delete customLocs[id];

    localStorage.setItem('calisnow_locations', JSON.stringify(customLocs));
    return getLocations();
}

const BASE_URL = 'https://api.open-meteo.com/v1/forecast';
const HISTORICAL_URL = 'https://historical-forecast-api.open-meteo.com/v1/forecast';

const weatherCache = new Map<string, { hrrrData: any, ecmwfData: any, location: Location, mode: string }>();

/** Backfill elevation fields for custom locations once the API response is available. */
function updateCustomElevation(loc: Location, apiElevation: number | undefined): void {
    if (loc.isCustom && apiElevation != null) {
        loc.elevationM = Math.round(apiElevation);
        loc.elevationFt = Math.round(apiElevation * 3.28084);
    }
}

const PRESSURE_LEVELS = ["1000hPa", "975hPa", "950hPa", "925hPa", "900hPa", "850hPa", "800hPa", "700hPa", "600hPa", "500hPa", "400hPa", "300hPa"];

const HOURLY_PARAMS = [
    "snowfall",
    "precipitation",
    "temperature_2m",
    "dew_point_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "snow_depth",
    "precipitation_probability",
    "apparent_temperature",
    "relative_humidity_2m",
    "wind_gusts_10m",
    "cloud_cover",
    "freezing_level_height",
    "weather_code",
    "wet_bulb_temperature_2m",
    "pressure_msl",
    "surface_pressure",
    "soil_temperature_0cm",
    "shortwave_radiation",
    "cape",
    "visibility",
    "boundary_layer_height",
    "total_column_integrated_water_vapour",
    "lifted_index",
    "convective_inhibition",
    PRESSURE_LEVELS.map(l => `temperature_${l}`).join(','),
    PRESSURE_LEVELS.map(l => `relative_humidity_${l}`).join(','),
    PRESSURE_LEVELS.map(l => `geopotential_height_${l}`).join(','),
    PRESSURE_LEVELS.map(l => `vertical_velocity_${l}`).join(','),
    PRESSURE_LEVELS.map(l => `cloud_cover_${l}`).join(','),
    PRESSURE_LEVELS.map(l => `wind_speed_${l}`).join(',')
].join(",");

/**
 * Fetch data from Open-Meteo API
 */
export async function fetchWeatherData(locationKey: string, modelMode = 'best_match') {
    const cacheKey = `${locationKey}|${modelMode}`;
    if (weatherCache.has(cacheKey)) {
        return weatherCache.get(cacheKey)!;
    }

    const locs = getLocations();
    const loc = locs[locationKey];
    if (!loc) throw new Error("Invalid location");

    const timezone = "America/Los_Angeles";

    if (modelMode === 'best_match') {
        const url = `${BASE_URL}?latitude=${loc.latitude}&longitude=${loc.longitude}` +
            `&hourly=${HOURLY_PARAMS},snowfall_water_equivalent` +
            `&daily=sunrise,sunset` +
            `&models=best_match` +
            `&forecast_days=15` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`Best Match fetch failed: ${res.status}`);
            const data = await res.json();

            updateCustomElevation(loc, data.elevation);

            const result = { hrrrData: null, ecmwfData: data, location: loc, mode: 'best_match' };
            weatherCache.set(cacheKey, result);
            return result;
        } catch (error) {
            console.error("Error fetching Best Match data:", error);
            throw error;
        }
    } else if (modelMode === 'hrrr_ecmwf') {
        // 1. Fetch HRRR (0-48 hours)
        const hrrrUrl = `${BASE_URL}?latitude=${loc.latitude}&longitude=${loc.longitude}` +
            `&hourly=${HOURLY_PARAMS}` +
            `&daily=sunrise,sunset` +
            `&models=gfs_hrrr` +
            `&forecast_days=3` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        // 2. Fetch ECMWF IFS (Up to 16 days, we'll fetch 15)
        const ecmwfUrl = `https://api.open-meteo.com/v1/ecmwf?latitude=${loc.latitude}&longitude=${loc.longitude}` +
            `&hourly=${HOURLY_PARAMS},snowfall_water_equivalent` +
            `&daily=sunrise,sunset` +
            `&forecast_days=15` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        try {
            const [hrrrRes, ecmwfRes] = await Promise.all([
                fetch(hrrrUrl),
                fetch(ecmwfUrl)
            ]);

            if (!hrrrRes.ok) throw new Error(`HRRR fetch failed: ${hrrrRes.status}`);
            if (!ecmwfRes.ok) throw new Error(`ECMWF fetch failed: ${ecmwfRes.status}`);

            const hrrrData = await hrrrRes.json();
            const ecmwfData = await ecmwfRes.json();

            updateCustomElevation(loc, ecmwfData.elevation);

            const result = { hrrrData, ecmwfData, location: loc, mode: 'hrrr_ecmwf' };
            weatherCache.set(cacheKey, result);
            return result;
        } catch (error) {
            console.error("Error fetching weather data:", error);
            throw error;
        }
    } else {
        // Specific model mode
        // Mapping internal simplified keys to Open-Meteo model keys
        const modelMap: Record<string, string> = {
            'hrrr': 'gfs_hrrr',
            'gem_hrdps_west': 'gem_hrdps_west',
            'nbm': 'ncep_nbm_conus',
            'nam': 'ncep_nam_conus',
            'gem_regional': 'gem_regional',
            'ecmwf': 'ecmwf_ifs',
            'gfs': 'gfs_global'
        };
        const omModel = modelMap[modelMode] || modelMode;
        
        // Models like HRRR, NAM, and GEM have shorter leads. 
        let days = 15;
        if (omModel === 'gfs_hrrr' || omModel === 'gem_hrdps_west') days = 2;
        else if (omModel === 'ncep_nam_conus') days = 4;
        else if (omModel === 'ncep_nbm_conus') days = 7;
        else if (omModel === 'gem_regional') days = 4;

        const url = `${BASE_URL}?latitude=${loc.latitude}&longitude=${loc.longitude}` +
            `&hourly=${HOURLY_PARAMS},snowfall_water_equivalent` +
            `&daily=sunrise,sunset` +
            `&models=${omModel}` +
            `&forecast_days=${days}` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        try {
            const res = await fetch(url);
            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                if (errData.reason && errData.reason.includes("geographic")) {
                    throw new Error(`Location outside ${omModel} coverage area.`);
                }
                if (errData.reason && errData.reason.includes("data is available")) {
                    throw new Error(`No data available for this location in ${omModel}.`);
                }
                throw new Error(`${omModel} fetch failed: ${res.status}`);
            }
            const data = await res.json();

            updateCustomElevation(loc, data.elevation);

            const result = { hrrrData: null, ecmwfData: data, location: loc, mode: modelMode };
            weatherCache.set(cacheKey, result);
            return result;
        } catch (error) {
            console.error(`Error fetching ${omModel} data:`, error);
            throw error;
        }
    }
}

/**
 * Fetch historical data from Open-Meteo Historical Forecast API
 */
export async function fetchHistoricalWeatherData(locationKey, startDate, endDate, model = 'best_match') {
    const locs = getLocations();
    const loc = locs[locationKey];
    if (!loc) throw new Error("Invalid location");

    const timezone = "America/Los_Angeles";

    const url = `${HISTORICAL_URL}?latitude=${loc.latitude}&longitude=${loc.longitude}` +
        `&start_date=${startDate}&end_date=${endDate}` +
        `&hourly=${HOURLY_PARAMS},snowfall_water_equivalent` +
        `&daily=sunrise,sunset` +
        `&models=${model}` +
        `&wind_speed_unit=ms` +
        `&timezone=${timezone}`;

    try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`Historical fetch failed: ${res.status}`);
        const data = await res.json();

        updateCustomElevation(loc, data.elevation);

        return { hrrrData: null, ecmwfData: data, location: loc, mode: 'historical', model };
    } catch (error) {
        console.error("Error fetching historical data:", error);
        throw error;
    }
}
