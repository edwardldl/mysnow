import type { Location, OpenMeteoResponse, WeatherDataResult } from './types';

const DEFAULT_LOCATIONS: Record<string, Location> = {
    palisades: {
        id: 'palisades',
        name: 'Palisades Tahoe',
        latitude: 39.193416,
        longitude: -120.245402,
        elevationFt: 8100,
        elevationM: 2470,
        timezone: 'America/Los_Angeles',
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

export function getLastLocationId(): string | null {
    if (typeof window === 'undefined') return null;
    return localStorage.getItem('calisnow_last_location_id');
}

export function setLastLocationId(id: string): void {
    if (typeof window === 'undefined') return;
    localStorage.setItem('calisnow_last_location_id', id);
}

const BASE_URL = 'https://api.open-meteo.com/v1/forecast';
const HISTORICAL_URL = 'https://historical-forecast-api.open-meteo.com/v1/forecast';
const META_BASE_URL = 'https://api.open-meteo.com/data';

/** Mapping of internal models to Open-Meteo static metadata names */
const MODEL_META_MAP: Record<string, string> = {
    'best_match': 'ecmwf_ifs',
    'hrrr_ecmwf': 'ncep_hrrr_conus',
    'hrrr': 'ncep_hrrr_conus',
    'gem_hrdps_west': 'cmc_gem_hrdps',
    'nbm': 'ncep_nbm_conus',
    'nam': 'ncep_nam_conus',
    'gem_regional': 'cmc_gem_rdps',
    'ecmwf': 'ecmwf_ifs',
    'gfs': 'ncep_gfs025'
};

async function fetchModelStatus(modelKey: string): Promise<number | undefined> {
    const metaKey = MODEL_META_MAP[modelKey];
    if (!metaKey) return undefined;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 2000);

    try {
        const res = await fetch(`${META_BASE_URL}/${metaKey}/static/meta.json`, { signal: controller.signal });
        clearTimeout(timeoutId);
        if (!res.ok) return undefined;
        const data = await res.json();
        return data.last_run_availability_time;
    } catch (e: any) {
        clearTimeout(timeoutId);
        if (e.name === 'AbortError') {
            console.warn(`Metadata fetch for ${modelKey} timed out after 2s`);
        }
        return undefined;
    }
}

type WeatherCacheItem = {
    hrrrData: OpenMeteoResponse | null;
    ecmwfData: OpenMeteoResponse;
    location: Location;
    mode: string;
    timestamp: number;
};

const CACHE_EXPIRATION_MS = 30 * 60 * 1000; // 30 minutes
const WEATHER_CACHE_PREFIX = 'mysnow_weather_cache_';

function getCachedData(key: string, allowExpired = false): WeatherCacheItem | null {
    if (typeof window === 'undefined') return null;
    try {
        const cached = localStorage.getItem(WEATHER_CACHE_PREFIX + key);
        if (!cached) return null;
        const item: WeatherCacheItem = JSON.parse(cached);
        const age = Date.now() - item.timestamp;
        if (!allowExpired && age > CACHE_EXPIRATION_MS) {
            return null;
        }
        return item;
    } catch (e) {
        console.error("Error reading weather cache:", e);
        return null;
    }
}

function setCachedData(key: string, data: Omit<WeatherCacheItem, 'timestamp'>): void {
    if (typeof window === 'undefined') return;
    try {
        const item: WeatherCacheItem = { ...data, timestamp: Date.now() };
        localStorage.setItem(WEATHER_CACHE_PREFIX + key, JSON.stringify(item));
    } catch (e) {
        console.error("Error writing weather cache:", e);
    }
}

export function hasValidCache(locationKey: string, modelMode: string): boolean {
    const cacheKey = `${locationKey}|${modelMode}`;
    return getCachedData(cacheKey) !== null;
}

/** Metadata backfill (elevation, timezone) for custom locations once the API response is available. */
function updateCustomLocationMetadata(loc: Location, data: OpenMeteoResponse): void {
    if (loc.isCustom) {
        let changed = false;
        if (data.elevation != null && loc.elevationM === '--') {
            loc.elevationM = Math.round(data.elevation);
            loc.elevationFt = Math.round(data.elevation * 3.28084);
            changed = true;
        }
        if (data.timezone && !loc.timezone) {
            loc.timezone = data.timezone;
            changed = true;
        }

        if (changed) {
            const customLocsJson = localStorage.getItem('calisnow_locations');
            const customLocs: Record<string, Location> = customLocsJson ? JSON.parse(customLocsJson) : {};
            customLocs[loc.id] = { ...loc };
            localStorage.setItem('calisnow_locations', JSON.stringify(customLocs));
        }
    } else {
        // For default locations, just ensure the timezone is attached to the object in memory
        if (data.timezone && !loc.timezone) {
            loc.timezone = data.timezone;
        }
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
    "uv_index",
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

const HISTORICAL_HOURLY_PARAMS = [
    "snowfall",
    "precipitation",
    "temperature_2m",
    "dew_point_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "snow_depth",
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
    "uv_index",
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
export async function fetchWeatherData(locationKey: string, modelMode = 'best_match', forceRefresh = false): Promise<WeatherDataResult> {
    const cacheKey = `${locationKey}|${modelMode}`;
    if (!forceRefresh) {
        const cached = getCachedData(cacheKey);
        if (cached) return { ...cached, status: 'cached' as const };
    }

    const locs = getLocations();
    const loc = locs[locationKey];
    if (!loc) throw new Error("Invalid location");

    const timezone = "auto";
    const metaPromise = fetchModelStatus(modelMode);

    if (modelMode === 'best_match') {
        const url = `${BASE_URL}?latitude=${loc.latitude}&longitude=${loc.longitude}` +
            `&hourly=${HOURLY_PARAMS},snowfall_water_equivalent` +
            `&daily=sunrise,sunset` +
            `&models=best_match` +
            `&forecast_days=15` +
            `&past_days=7` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        try {
            console.time(`fetchWeatherData:${modelMode}`);
            const [res, lastRunAvailabilityTime] = await Promise.all([
                fetch(url),
                metaPromise
            ]);
            console.timeEnd(`fetchWeatherData:${modelMode}`);

            if (!res.ok) throw new Error(`Best Match fetch failed: ${res.status}`);
            const data = await res.json();
            data.lastRunAvailabilityTime = lastRunAvailabilityTime;

            updateCustomLocationMetadata(loc, data);

            const result = { hrrrData: null, ecmwfData: data, location: loc, mode: 'best_match', status: 'fresh' as const };
            const { status, ...cacheable } = result;
            setCachedData(cacheKey, cacheable);
            return result;
        } catch (error) {
            console.error("Error fetching Best Match data:", error);
            const stale = getCachedData(cacheKey, true);
            if (stale) return { ...stale, status: 'stale' as const };
            throw error;
        }
    } else if (modelMode === 'hrrr_ecmwf') {
        // 1. Fetch HRRR (0-48 hours)
        const hrrrUrl = `${BASE_URL}?latitude=${loc.latitude}&longitude=${loc.longitude}` +
            `&hourly=${HOURLY_PARAMS}` +
            `&daily=sunrise,sunset` +
            `&models=gfs_hrrr` +
            `&forecast_days=3` +
            `&past_days=7` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        // 2. Fetch ECMWF IFS (Up to 16 days, we'll fetch 15)
        const ecmwfUrl = `https://api.open-meteo.com/v1/ecmwf?latitude=${loc.latitude}&longitude=${loc.longitude}` +
            `&hourly=${HOURLY_PARAMS},snowfall_water_equivalent` +
            `&daily=sunrise,sunset` +
            `&forecast_days=15` +
            `&past_days=7` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        try {
            console.time(`fetchWeatherData:${modelMode}`);
            const [hrrrRes, ecmwfRes, lastRunAvailabilityTime] = await Promise.all([
                fetch(hrrrUrl),
                fetch(ecmwfUrl),
                metaPromise
            ]);
            console.timeEnd(`fetchWeatherData:${modelMode}`);

            if (!hrrrRes.ok) throw new Error(`HRRR fetch failed: ${hrrrRes.status}`);
            if (!ecmwfRes.ok) throw new Error(`ECMWF fetch failed: ${ecmwfRes.status}`);

            const hrrrData = await hrrrRes.json();
            const ecmwfData = await ecmwfRes.json();

            hrrrData.lastRunAvailabilityTime = lastRunAvailabilityTime;
            ecmwfData.lastRunAvailabilityTime = lastRunAvailabilityTime;

            updateCustomLocationMetadata(loc, ecmwfData);

            const result = { hrrrData, ecmwfData, location: loc, mode: 'hrrr_ecmwf', status: 'fresh' as const };
            const { status, ...cacheable } = result;
            setCachedData(cacheKey, cacheable);
            return result;
        } catch (error) {
            console.error("Error fetching weather data:", error);
            const stale = getCachedData(cacheKey, true);
            if (stale) return { ...stale, status: 'stale' as const };
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
            `&past_days=7` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        try {
            console.time(`fetchWeatherData:${modelMode}`);
            const [res, lastRunAvailabilityTime] = await Promise.all([
                fetch(url),
                metaPromise
            ]);
            console.timeEnd(`fetchWeatherData:${modelMode}`);

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                const modelName = modelMode.toUpperCase();
                if (errData.reason && errData.reason.includes("geographic")) {
                    throw new Error(`Location outside ${modelName} coverage area.`);
                }
                if (errData.reason && errData.reason.includes("data is available")) {
                    throw new Error(`No data available for this location in ${modelName}.`);
                }
                throw new Error(`${modelName} fetch failed: ${res.status}`);
            }
            const data = await res.json();
            data.lastRunAvailabilityTime = lastRunAvailabilityTime;

            updateCustomLocationMetadata(loc, data);

            const result = { hrrrData: null, ecmwfData: data, location: loc, mode: modelMode, status: 'fresh' as const };
            const { status, ...cacheable } = result;
            setCachedData(cacheKey, cacheable);
            return result;
        } catch (error) {
            console.error(`Error fetching ${omModel} data:`, error);
            const stale = getCachedData(cacheKey, true);
            if (stale) return { ...stale, status: 'stale' as const };
            throw error;
        }
    }
}

/**
 * Fetch historical data from Open-Meteo Historical Forecast API
 */
export async function fetchHistoricalWeatherData(locationKey: string, startDate: string, endDate: string, model = 'best_match') {
    const locs = getLocations();
    const loc = locs[locationKey];
    if (!loc) throw new Error("Invalid location");

    const timezone = "auto";
    const metaPromise = fetchModelStatus(model);

    const url = `${HISTORICAL_URL}?latitude=${loc.latitude}&longitude=${loc.longitude}` +
        `&start_date=${startDate}&end_date=${endDate}` +
        `&hourly=${HISTORICAL_HOURLY_PARAMS},snowfall_water_equivalent` +
        `&daily=sunrise,sunset` +
        `&models=${model}` +
        `&wind_speed_unit=ms` +
        `&timezone=${timezone}`;

    try {
        console.time(`fetchHistoricalWeatherData:${model}`);
        const [res, lastRunAvailabilityTime] = await Promise.all([
            fetch(url),
            metaPromise
        ]);
        console.timeEnd(`fetchHistoricalWeatherData:${model}`);

        if (!res.ok) throw new Error(`${model.toUpperCase()} historical fetch failed: ${res.status}`);
        const data = await res.json();
        data.lastRunAvailabilityTime = lastRunAvailabilityTime;

        updateCustomLocationMetadata(loc, data);

        return { hrrrData: null, ecmwfData: data, location: loc, mode: 'historical', model };
    } catch (error) {
        console.error("Error fetching historical data:", error);
        throw error;
    }
}
