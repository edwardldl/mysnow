import type { Location, OpenMeteoResponse, WeatherDataResult } from './types';

const DEFAULT_LOCATIONS: Record<string, Location> = {
    palisades: {
        id: 'palisades',
        name: 'Palisades Tahoe',
        latitude: 39.193416,
        longitude: -120.245402,
        elevationFt: 8100,
        elevationM: 2470,
        minElevationM: 1896,
        maxElevationM: 2666,
        timezone: 'America/Los_Angeles',
        isCustom: false
    }
};

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null;
}

function isOpenMeteoResponse(value: unknown): value is OpenMeteoResponse {
    return isRecord(value)
        && isRecord(value.hourly)
        && Array.isArray(value.hourly.time)
        && Array.isArray(value.hourly.temperature_2m)
        && typeof value.timezone === 'string'
        && typeof value.timezone_abbreviation === 'string';
}

function attachResponseMetadata(
    data: OpenMeteoResponse,
    modelIdentity: string,
    lastRunAvailabilityTime?: number,
): OpenMeteoResponse {
    data.lastRunAvailabilityTime = lastRunAvailabilityTime;
    data.modelIdentity = modelIdentity;
    data.profileUnits = {
        pressure: 'hPa',
        geopotentialHeight: 'm',
        temperature: '°C',
        verticalVelocity: 'm/s',
        windSpeed: 'm/s',
    };
    return data;
}

function isStoredLocation(value: unknown): value is Location {
    if (!isRecord(value)) return false;
    return typeof value.id === 'string'
        && typeof value.name === 'string'
        && isValidCoordinate(value.latitude as number, value.longitude as number)
        && (typeof value.elevationM === 'number' || typeof value.elevationM === 'string')
        && (typeof value.elevationFt === 'number' || typeof value.elevationFt === 'string');
}

function getCustomLocations(): Record<string, Location> {
    if (typeof window === 'undefined') return {};

    try {
        const stored = localStorage.getItem('calisnow_locations');
        if (!stored) return {};

        const parsed: unknown = JSON.parse(stored);
        if (!isRecord(parsed)) return {};

        return Object.fromEntries(
            Object.entries(parsed).filter(([id, location]) =>
                !DEFAULT_LOCATIONS[id] && isStoredLocation(location) && location.id === id
            )
        ) as Record<string, Location>;
    } catch (error) {
        console.warn('Ignoring invalid saved locations:', error);
        return {};
    }
}

export function getLocations(): Record<string, Location> {
    return { ...DEFAULT_LOCATIONS, ...getCustomLocations() };
}

export function saveLocation(id: string, name: string, lat: string, lon: string, minElev?: number, maxElev?: number): Record<string, Location> {
    if (typeof window === 'undefined') throw new Error('Locations can only be saved in a browser.');
    if (!id || DEFAULT_LOCATIONS[id]) {
        throw new Error('Please choose a unique name for this location.');
    }

    const customLocs = getCustomLocations();

    const latitude = parseFloat(lat);
    const longitude = parseFloat(lon);

    if (!isValidCoordinate(latitude, longitude)) {
        throw new Error(`Invalid coordinates: ${lat}, ${lon}. Latitude must be between -90 and 90, and longitude between -180 and 180.`);
    }

    // Elevation is populated on first successful API fetch for custom locations.
    customLocs[id] = {
        id,
        name,
        latitude,
        longitude,
        elevationFt: '--',
        elevationM: '--',
        minElevationM: minElev,
        maxElevationM: maxElev,
        isCustom: true
    };

    localStorage.setItem('calisnow_locations', JSON.stringify(customLocs));
    return getLocations();
}

export function removeLocation(id: string): Record<string, Location> {
    if (typeof window === 'undefined') return getLocations();
    if (DEFAULT_LOCATIONS[id]) return getLocations(); // Can't delete defaults

    const customLocs = getCustomLocations();
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

/**
 * Validates if a latitude and longitude are within standard Earth bounds.
 */
export function isValidCoordinate(lat?: number, lon?: number): boolean {
    if (typeof lat !== 'number' || typeof lon !== 'number') return false;
    if (isNaN(lat) || isNaN(lon)) return false;
    // Open-Meteo expects -90 to 90 for latitude and -180 to 180 for longitude.
    if (lat < -90 || lat > 90) return false;
    if (lon < -180 || lon > 180) return false;
    return true;
}

const BASE_URL = 'https://api.open-meteo.com/v1/forecast';
const HISTORICAL_URL = 'https://historical-forecast-api.open-meteo.com/v1/forecast';
const ENSEMBLE_URL = 'https://ensemble-api.open-meteo.com/v1/ensemble';
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
    'ecmwf_aifs': 'ecmwf_aifs025',
    'ecmwf_aifs_ensemble': 'ecmwf_aifs025',
    'gfs': 'ncep_gfs025',
    'icon_global': 'dwd_icon'
};

export interface ModelCapabilities {
    pressureLevels: number[];
    supportsDewPointProfile: boolean;
    supportsVerticalVelocity: boolean;
    supportsSnowfallWaterEquivalent: boolean;
    supportsPrecipitationType: boolean;
}

const STANDARD_PRESSURE_LEVELS = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500, 400];
const DENSE_GFS_PRESSURE_LEVELS = [
    1000, 975, 950, 925, 900, 875, 850, 825, 800, 775, 750, 725, 700,
    675, 650, 625, 600, 575, 550, 525, 500, 475, 450, 425, 400,
];
const DEFAULT_MODEL_CAPABILITIES: ModelCapabilities = {
    pressureLevels: STANDARD_PRESSURE_LEVELS,
    supportsDewPointProfile: true,
    supportsVerticalVelocity: true,
    supportsSnowfallWaterEquivalent: true,
    supportsPrecipitationType: false,
};

/**
 * Open-Meteo models expose different pressure grids. Keep the supported grid
 * next to the request builder instead of assuming every model has the same 12
 * pressure surfaces.
 */
export const MODEL_CAPABILITIES: Record<string, ModelCapabilities> = {
    hrrr: { ...DEFAULT_MODEL_CAPABILITIES, pressureLevels: DENSE_GFS_PRESSURE_LEVELS },
    hrrr_ecmwf: { ...DEFAULT_MODEL_CAPABILITIES, pressureLevels: DENSE_GFS_PRESSURE_LEVELS },
    gfs: { ...DEFAULT_MODEL_CAPABILITIES, pressureLevels: DENSE_GFS_PRESSURE_LEVELS },
    nam: { ...DEFAULT_MODEL_CAPABILITIES, pressureLevels: DENSE_GFS_PRESSURE_LEVELS },
    nbm: { ...DEFAULT_MODEL_CAPABILITIES, pressureLevels: DENSE_GFS_PRESSURE_LEVELS },
    ecmwf: DEFAULT_MODEL_CAPABILITIES,
    best_match: DEFAULT_MODEL_CAPABILITIES,
    ecmwf_aifs: DEFAULT_MODEL_CAPABILITIES,
    ecmwf_aifs_ensemble: DEFAULT_MODEL_CAPABILITIES,
    gem_hrdps_west: DEFAULT_MODEL_CAPABILITIES,
    gem_regional: DEFAULT_MODEL_CAPABILITIES,
    icon_global: DEFAULT_MODEL_CAPABILITIES,
};

/**
 * Calculates the elevation parameter for Open-Meteo.
 * Returns the min, max, or average of elevation if available,
 * otherwise falls back to single elevation or 'nan' for grid default.
 */
function getApiElevation(loc: Location, mode: string = 'avg'): string {
    const min = loc.minElevationM;
    const max = loc.maxElevationM;

    if (min != null && max != null) {
        if (mode === 'min') return min.toString();
        if (mode === 'max') return max.toString();
        return ((min + max) / 2).toString();
    }
    
    // Fallback if min/max not available
    if (typeof loc.elevationM === 'number') {
        return loc.elevationM.toString();
    }
    
    return 'nan';
}

function estimatedSurfacePressureHpa(loc: Location, elevationMode: string): number | null {
    const elevation = getApiElevation(loc, elevationMode);
    const elevationM = Number(elevation);
    if (!Number.isFinite(elevationM)) return null;
    // Standard-atmosphere approximation is used only to avoid requesting known
    // subterranean levels; profile normalization remains the final authority.
    return 1013.25 * Math.pow(1 - (2.25577e-5 * elevationM), 5.25588);
}

function requestedPressureLevels(loc: Location, modelMode: string, elevationMode: string): number[] {
    const capabilities = MODEL_CAPABILITIES[modelMode] ?? DEFAULT_MODEL_CAPABILITIES;
    const surfacePressureHpa = estimatedSurfacePressureHpa(loc, elevationMode);
    return capabilities.pressureLevels.filter(level => level >= 400 && (surfacePressureHpa === null || level <= surfacePressureHpa));
}

async function fetchModelStatus(modelKey: string): Promise<number | undefined> {
    const metaKey = MODEL_META_MAP[modelKey];
    if (!metaKey) return undefined;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 2000);

    try {
        const res = await fetch(`${META_BASE_URL}/${metaKey}/static/meta.json`, { signal: controller.signal });
        clearTimeout(timeoutId);
        if (!res.ok) return undefined;
        const data: unknown = await res.json();
        return isRecord(data) && typeof data.last_run_availability_time === 'number'
            ? data.last_run_availability_time
            : undefined;
    } catch (error: unknown) {
        clearTimeout(timeoutId);
        if (error instanceof DOMException && error.name === 'AbortError') {
            console.warn(`Metadata fetch for ${modelKey} timed out after 2s`);
        }
        return undefined;
    }
}

type WeatherCacheItem = {
    hrrrData: OpenMeteoResponse | null;
    ecmwfData: OpenMeteoResponse;
    ensembleMembers?: OpenMeteoResponse[];
    location: Location;
    mode: string;
    timestamp: number;
};

const CACHE_EXPIRATION_MS = 30 * 60 * 1000; // 30 minutes
const WEATHER_CACHE_PREFIX = 'mysnow_weather_cache_';

function isWeatherCacheItem(value: unknown): value is WeatherCacheItem {
    if (!isRecord(value) || typeof value.timestamp !== 'number' || !Number.isFinite(value.timestamp) || typeof value.mode !== 'string') {
        return false;
    }

    if (!isStoredLocation(value.location) || !isOpenMeteoResponse(value.ecmwfData)) {
        return false;
    }

    const hasValidHrrrData = value.hrrrData === null || isOpenMeteoResponse(value.hrrrData);
    const hasValidEnsembleMembers = value.ensembleMembers === undefined
        || (Array.isArray(value.ensembleMembers) && value.ensembleMembers.every(isOpenMeteoResponse));
    return hasValidHrrrData && hasValidEnsembleMembers;
}

function getCachedData(key: string, allowExpired = false): WeatherCacheItem | null {
    if (typeof window === 'undefined') return null;
    try {
        const cached = localStorage.getItem(WEATHER_CACHE_PREFIX + key);
        if (!cached) return null;
        const item: unknown = JSON.parse(cached);
        if (!isWeatherCacheItem(item)) {
            localStorage.removeItem(WEATHER_CACHE_PREFIX + key);
            return null;
        }
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

/**
 * Removes the oldest weather cache entries from localStorage to free up space.
 * returns true if it cleared something, false otherwise.
 */
function evictOldestCacheEntries(neededEntries = 5): boolean {
    if (typeof window === 'undefined') return false;
    try {
        const weatherKeys: { key: string, timestamp: number }[] = [];
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (key && key.startsWith(WEATHER_CACHE_PREFIX)) {
                try {
                    const item: unknown = JSON.parse(localStorage.getItem(key) || '{}');
                    if (isRecord(item) && typeof item.timestamp === 'number' && Number.isFinite(item.timestamp)) {
                        weatherKeys.push({ key, timestamp: item.timestamp });
                    }
                } catch {
                    // If parsing fails, just treat it as very old or corrupt and potentially delete it
                    weatherKeys.push({ key, timestamp: 0 });
                }
            }
        }

        if (weatherKeys.length === 0) return false;

        // Sort by timestamp (oldest first)
        weatherKeys.sort((a, b) => a.timestamp - b.timestamp);

        // Remove the oldest entries
        const toRemove = weatherKeys.slice(0, Math.min(neededEntries, weatherKeys.length));
        toRemove.forEach(entry => localStorage.removeItem(entry.key));
        
        console.warn(`Cache quota exceeded. Evicted ${toRemove.length} oldest weather entries.`);
        return true;
    } catch (e) {
        console.error("Error during cache eviction:", e);
        return false;
    }
}

function setCachedData(key: string, data: Omit<WeatherCacheItem, 'timestamp'>): void {
    if (typeof window === 'undefined') return;
    try {
        const item: WeatherCacheItem = { ...data, timestamp: Date.now() };
        localStorage.setItem(WEATHER_CACHE_PREFIX + key, JSON.stringify(item));
    } catch (error: unknown) {
        const quotaExceeded = error instanceof DOMException
            && (error.name === 'QuotaExceededError' || error.name === 'NS_ERROR_DOM_QUOTA_REACHED');
        if (quotaExceeded) {
            const cleared = evictOldestCacheEntries(8); // Clear a decent chunk
            if (cleared) {
                try {
                    // Try one more time after internal cleanup
                    const item: WeatherCacheItem = { ...data, timestamp: Date.now() };
                    localStorage.setItem(WEATHER_CACHE_PREFIX + key, JSON.stringify(item));
                    return;
                } catch (retryError) {
                    console.error("Critical: Still out of space after eviction.", retryError);
                }
            }
        }
        console.error("Error writing weather cache:", error);
    }
}

interface EnsembleFetchData {
    displayData: OpenMeteoResponse;
    members: OpenMeteoResponse[];
}

/**
 * Open-Meteo's ensemble endpoint stores members as `variable_memberNN` beside
 * the mean variable. Extract each member instead of averaging atmospheric
 * fields before phase and SLR are evaluated.
 */
function extractEnsembleMembers(
    data: unknown,
    lastRunAvailabilityTime?: number,
    modelIdentity = 'ensemble',
): EnsembleFetchData {
    if (!isOpenMeteoResponse(data)) throw new Error('Invalid ensemble data received');
    const memberIds = [...new Set(Object.keys(data.hourly)
        .map(key => key.match(/_member(\d+)$/)?.[1])
        .filter((id): id is string => id !== undefined))]
        .sort();
    const displayData = attachResponseMetadata({ ...data }, modelIdentity, lastRunAvailabilityTime);
    const memberKeys = Object.keys(data.hourly).filter(key => key !== 'time' && !/_member\d+$/.test(key));
    const members = memberIds.map(memberId => {
        const hourly: OpenMeteoResponse['hourly'] = {
            time: data.hourly.time,
            temperature_2m: [],
        };
        for (const key of memberKeys) {
            const memberValue = data.hourly[`${key}_member${memberId}`];
            const value = Array.isArray(memberValue) ? memberValue : data.hourly[key];
            if (Array.isArray(value)) hourly[key] = value;
        }
        return attachResponseMetadata({
            hourly,
            daily: data.daily,
            elevation: data.elevation,
            timezone: data.timezone,
            timezone_abbreviation: data.timezone_abbreviation,
        }, modelIdentity, lastRunAvailabilityTime);
    }).filter(isOpenMeteoResponse);
    return { displayData, members };
}

async function fetchEnsembleData(url: string, lastRunAvailabilityTime?: number, modelIdentity?: string): Promise<EnsembleFetchData> {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Ensemble fetch failed: ${res.status}`);
    return extractEnsembleMembers(await res.json(), lastRunAvailabilityTime, modelIdentity);
}

export function hasValidCache(locationKey: string, modelMode: string, elevationMode: string = 'avg'): boolean {
    const cacheKey = `${locationKey}|${modelMode}|${elevationMode}`;
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
            const customLocs = getCustomLocations();
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

const BASE_HOURLY_PARAMS = [
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
];

function buildHourlyParams(pressureLevels: number[], capabilities: ModelCapabilities): string {
    const profileVariables = [
        ...pressureLevels.map(level => `temperature_${level}hPa`),
        ...pressureLevels.map(level => `relative_humidity_${level}hPa`),
        ...pressureLevels.map(level => `geopotential_height_${level}hPa`),
        ...pressureLevels.map(level => `cloud_cover_${level}hPa`),
        ...pressureLevels.map(level => `wind_speed_${level}hPa`),
        ...pressureLevels.map(level => `wind_direction_${level}hPa`),
    ];
    if (capabilities.supportsDewPointProfile) {
        profileVariables.push(...pressureLevels.map(level => `dew_point_${level}hPa`));
    }
    if (capabilities.supportsVerticalVelocity) {
        profileVariables.push(...pressureLevels.map(level => `vertical_velocity_${level}hPa`));
    }
    const modelVariables = [
        ...(capabilities.supportsSnowfallWaterEquivalent ? ['snowfall_water_equivalent'] : []),
        ...(capabilities.supportsPrecipitationType ? ['precipitation_type'] : []),
    ];
    return [...BASE_HOURLY_PARAMS, ...modelVariables, ...profileVariables].join(',');
}

function weatherHourlyParams(loc: Location, modelMode: string, elevationMode: string): string {
    const capabilities = MODEL_CAPABILITIES[modelMode] ?? DEFAULT_MODEL_CAPABILITIES;
    return buildHourlyParams(requestedPressureLevels(loc, modelMode, elevationMode), capabilities);
}

function weatherHourlyParamsForLocations(locations: Location[], modelMode: string, elevationMode: string): string {
    const highestLocation = locations.reduce<Location | null>((highest, location) => {
        const elevation = Number(getApiElevation(location, elevationMode));
        const highestElevation = highest === null ? -Infinity : Number(getApiElevation(highest, elevationMode));
        return Number.isFinite(elevation) && elevation > highestElevation ? location : highest;
    }, null);
    return highestLocation === null
        ? buildHourlyParams((MODEL_CAPABILITIES[modelMode] ?? DEFAULT_MODEL_CAPABILITIES).pressureLevels, MODEL_CAPABILITIES[modelMode] ?? DEFAULT_MODEL_CAPABILITIES)
        : weatherHourlyParams(highestLocation, modelMode, elevationMode);
}

const ENSEMBLE_HOURLY_PARAMS = [
    "snowfall",
    "precipitation",
    "temperature_2m",
    "dew_point_2m",
    "wind_speed_10m",
    "snow_depth",
    "relative_humidity_2m",
    "freezing_level_height",
    "weather_code",
    "snowfall_water_equivalent",
    "temperature_850hPa",
    "temperature_700hPa"
].join(",");

/**
 * Fetch data from Open-Meteo API
 */
export async function fetchWeatherData(locationKey: string, modelMode = 'best_match', elevationMode = 'avg', forceRefresh = false): Promise<WeatherDataResult> {
    const cacheKey = `${locationKey}|${modelMode}|${elevationMode}`;
    if (!forceRefresh) {
        const cached = getCachedData(cacheKey);
        if (cached) return { ...cached, status: 'cached' as const };
    }

    const locs = getLocations();
    const loc = locs[locationKey];
    if (!loc) throw new Error("Invalid location");

    if (!isValidCoordinate(loc.latitude, loc.longitude)) {
        throw new Error(`Invalid coordinates for ${loc.name}: ${loc.latitude}, ${loc.longitude}`);
    }

    const timezone = "auto";
    const metaPromise = fetchModelStatus(modelMode);

    if (modelMode === 'best_match') {
        const url = `${BASE_URL}?latitude=${loc.latitude}&longitude=${loc.longitude}` +
            `&elevation=${getApiElevation(loc, elevationMode)}` +
            `&hourly=${weatherHourlyParams(loc, 'best_match', elevationMode)}` +
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
            const data = attachResponseMetadata(await res.json(), 'best_match', lastRunAvailabilityTime);

            updateCustomLocationMetadata(loc, data);

            const result = { hrrrData: null, ecmwfData: data, location: loc, mode: 'best_match', status: 'fresh' as const };
            setCachedData(cacheKey, result);
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
            `&elevation=${getApiElevation(loc, elevationMode)}` +
            `&hourly=${weatherHourlyParams(loc, 'hrrr', elevationMode)}` +
            `&daily=sunrise,sunset` +
            `&models=gfs_hrrr` +
            `&forecast_days=3` +
            `&past_days=7` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        // 2. Fetch ECMWF IFS (Up to 16 days, we'll fetch 15)
        const ecmwfUrl = `https://api.open-meteo.com/v1/ecmwf?latitude=${loc.latitude}&longitude=${loc.longitude}` +
            `&elevation=${getApiElevation(loc, elevationMode)}` +
            `&hourly=${weatherHourlyParams(loc, 'ecmwf', elevationMode)}` +
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

            const hrrrData = attachResponseMetadata(await hrrrRes.json(), 'hrrr', lastRunAvailabilityTime);
            const ecmwfData = attachResponseMetadata(await ecmwfRes.json(), 'ecmwf', lastRunAvailabilityTime);

            updateCustomLocationMetadata(loc, ecmwfData);

            const result = { hrrrData, ecmwfData, location: loc, mode: 'hrrr_ecmwf', status: 'fresh' as const };
            setCachedData(cacheKey, result);
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
            'ecmwf_aifs': 'ecmwf_aifs025_single',
            'ecmwf_aifs_ensemble': 'ecmwf_aifs025_ensemble',
            'gfs': 'gfs_global',
            'icon_global': 'icon_global'
        };
        const omModel = modelMap[modelMode] || modelMode;
        
        // Use dedicated high-resolution ECMWF endpoint if specified
        if (modelMode === 'ecmwf') {
            const ecmwfUrl = `https://api.open-meteo.com/v1/ecmwf?latitude=${loc.latitude}&longitude=${loc.longitude}` +
                `&elevation=${getApiElevation(loc, elevationMode)}` +
                `&hourly=${weatherHourlyParams(loc, 'ecmwf', elevationMode)}` +
                `&daily=sunrise,sunset` +
                `&forecast_days=15` +
                `&past_days=7` +
                `&wind_speed_unit=ms` +
                `&timezone=${timezone}`;
                
            try {
                console.time(`fetchWeatherData:${modelMode}`);
                const [res, lastRunAvailabilityTime] = await Promise.all([fetch(ecmwfUrl), metaPromise]);
                console.timeEnd(`fetchWeatherData:${modelMode}`);
                if (!res.ok) throw new Error(`ECMWF High-Res fetch failed: ${res.status}`);
                const data = attachResponseMetadata(await res.json(), 'ecmwf', lastRunAvailabilityTime);
                updateCustomLocationMetadata(loc, data);
                const result = { hrrrData: null, ecmwfData: data, location: loc, mode: 'ecmwf', status: 'fresh' as const };
                setCachedData(cacheKey, result);
                return result;
            } catch (error) {
                console.error("Error fetching ECMWF High-Res data:", error);
                const stale = getCachedData(cacheKey, true);
                if (stale) return { ...stale, status: 'stale' as const };
                throw error;
            }
        }

        // Use dedicated Ensemble API for ensemble models
        if (modelMode.endsWith('_ensemble')) {
            const url = `${ENSEMBLE_URL}?latitude=${loc.latitude}&longitude=${loc.longitude}` +
                `&elevation=${getApiElevation(loc, elevationMode)}` +
                `&hourly=${ENSEMBLE_HOURLY_PARAMS}` +
                `&daily=sunrise,sunset` +
                `&models=${omModel}` +
                `&forecast_days=15` +
                `&past_days=7` +
                `&wind_speed_unit=ms` +
                `&timezone=${timezone}`;

            try {
                console.time(`fetchWeatherData:${modelMode}`);
                const [lastRunAvailabilityTime] = await Promise.all([metaPromise]);
                const { displayData, members } = await fetchEnsembleData(url, lastRunAvailabilityTime, modelMode);
                console.timeEnd(`fetchWeatherData:${modelMode}`);
                
                updateCustomLocationMetadata(loc, displayData);
                const result = { hrrrData: null, ecmwfData: displayData, ensembleMembers: members, location: loc, mode: modelMode, status: 'fresh' as const };
                setCachedData(cacheKey, result);
                return result;
            } catch (error) {
                console.error(`Error fetching ensemble data ${omModel}:`, error);
                const stale = getCachedData(cacheKey, true);
                if (stale) return { ...stale, status: 'stale' as const };
                throw error;
            }
        }

        // Models like HRRR, NAM, and GEM have shorter leads.
        let days = 15;
        if (omModel === 'gfs_hrrr' || omModel === 'gem_hrdps_west') days = 2;
        else if (omModel === 'ncep_nam_conus') days = 4;
        else if (omModel === 'ncep_nbm_conus') days = 7;
        else if (omModel === 'gem_regional') days = 4;
        else if (omModel === 'icon_global') days = 7;

        const url = `${BASE_URL}?latitude=${loc.latitude}&longitude=${loc.longitude}` +
            `&elevation=${getApiElevation(loc, elevationMode)}` +
            `&hourly=${weatherHourlyParams(loc, modelMode, elevationMode)}` +
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
            const data = attachResponseMetadata(await res.json(), modelMode, lastRunAvailabilityTime);

            updateCustomLocationMetadata(loc, data);

            const result = { hrrrData: null, ecmwfData: data, location: loc, mode: modelMode, status: 'fresh' as const };
            setCachedData(cacheKey, result);
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
export async function fetchHistoricalWeatherData(locationKey: string, startDate: string, endDate: string, model = 'best_match', elevationMode = 'avg') {
    const locs = getLocations();
    const loc = locs[locationKey];
    if (!loc) throw new Error("Invalid location");

    if (!isValidCoordinate(loc.latitude, loc.longitude)) {
        throw new Error(`Invalid coordinates for ${loc.name}: ${loc.latitude}, ${loc.longitude}`);
    }

    const timezone = "auto";
    const metaPromise = fetchModelStatus(model);

    const url = `${HISTORICAL_URL}?latitude=${loc.latitude}&longitude=${loc.longitude}` +
        `&elevation=${getApiElevation(loc, elevationMode)}` +
        `&start_date=${startDate}&end_date=${endDate}` +
        `&hourly=${weatherHourlyParams(loc, model, elevationMode)}` +
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
        const data = attachResponseMetadata(await res.json(), model, lastRunAvailabilityTime);

        updateCustomLocationMetadata(loc, data);

        return { hrrrData: null, ecmwfData: data, location: loc, mode: 'historical', model };
    } catch (error) {
        console.error("Error fetching historical data:", error);
        throw error;
    }
}

/**
 * Fetch bulk weather data for multiple locations in a single request (or minimal requests).
 * This is used for pre-fetching and background updates.
 */
export async function fetchBulkWeatherData(locationIds: string[], modelMode = 'best_match', elevationMode = 'avg'): Promise<void> {
    const locs = getLocations();
    const targets = locationIds.map(id => locs[id]).filter(Boolean);

    // Filter out locations that are invalid or already have a valid cache
    const toFetch = targets.filter(l => {
        const valid = isValidCoordinate(l.latitude, l.longitude);
        if (!valid) {
            console.warn(`Skipping bulk fetch for ${l.name} (${l.id}) due to invalid coordinates: ${l.latitude}, ${l.longitude}`);
        }
        return valid && !hasValidCache(l.id, modelMode, elevationMode);
    });
    if (toFetch.length === 0) return;

    const latitudes = toFetch.map(l => l.latitude).join(',');
    const longitudes = toFetch.map(l => l.longitude).join(',');
    const elevations = toFetch.map(l => getApiElevation(l, elevationMode)).join(',');
    const timezone = "auto";
    const metaPromise = fetchModelStatus(modelMode);

    if (modelMode === 'best_match') {
        const url = `${BASE_URL}?latitude=${latitudes}&longitude=${longitudes}` +
            `&elevation=${elevations}` +
            `&hourly=${weatherHourlyParamsForLocations(toFetch, 'best_match', elevationMode)}` +
            `&daily=sunrise,sunset` +
            `&models=best_match` +
            `&forecast_days=15` +
            `&past_days=7` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        try {
            console.time(`fetchBulkWeatherData:${modelMode}`);
            const [res, lastRunAvailabilityTime] = await Promise.all([fetch(url), metaPromise]);
            console.timeEnd(`fetchBulkWeatherData:${modelMode}`);

            if (!res.ok) throw new Error(`Bulk Best Match fetch failed: ${res.status}, ${url}`);
            const data = await res.json();
            const results = Array.isArray(data) ? data : [data];

            results.forEach((item, idx) => {
                const loc = toFetch[idx];
                if (!loc) return;
                item.lastRunAvailabilityTime = lastRunAvailabilityTime;
                updateCustomLocationMetadata(loc, item);
                const result = { hrrrData: null, ecmwfData: item, location: loc, mode: 'best_match' };
                setCachedData(`${loc.id}|best_match|${elevationMode}`, result);
            });
        } catch (e) {
            console.error("Bulk prefetch failed:", e);
        }
    } else if (modelMode === 'hrrr_ecmwf') {
        const hrrrUrl = `${BASE_URL}?latitude=${latitudes}&longitude=${longitudes}` +
            `&elevation=${elevations}` +
            `&hourly=${weatherHourlyParamsForLocations(toFetch, 'hrrr', elevationMode)}` +
            `&daily=sunrise,sunset` +
            `&models=gfs_hrrr` +
            `&forecast_days=3` +
            `&past_days=7` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        const ecmwfUrl = `https://api.open-meteo.com/v1/ecmwf?latitude=${latitudes}&longitude=${longitudes}` +
            `&elevation=${elevations}` +
            `&hourly=${weatherHourlyParamsForLocations(toFetch, 'ecmwf', elevationMode)}` +
            `&daily=sunrise,sunset` +
            `&forecast_days=15` +
            `&past_days=7` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        try {
            console.time(`fetchBulkWeatherData:${modelMode}`);
            const [hrrrRes, ecmwfRes, lastRunAvailabilityTime] = await Promise.all([
                fetch(hrrrUrl),
                fetch(ecmwfUrl),
                metaPromise
            ]);
            console.timeEnd(`fetchBulkWeatherData:${modelMode}`);

            if (!hrrrRes.ok || !ecmwfRes.ok) throw new Error("Bulk HRRR/ECMWF fetch failed");
            const hrrrData = await hrrrRes.json();
            const ecmwfData = await ecmwfRes.json();

            const hrrrResults = Array.isArray(hrrrData) ? hrrrData : [hrrrData];
            const ecmwfResults = Array.isArray(ecmwfData) ? ecmwfData : [ecmwfData];

            ecmwfResults.forEach((item, idx) => {
                const loc = toFetch[idx];
                const hrrrItem = hrrrResults[idx];
                if (!loc) return;
                item.lastRunAvailabilityTime = lastRunAvailabilityTime;
                if (hrrrItem) hrrrItem.lastRunAvailabilityTime = lastRunAvailabilityTime;

                updateCustomLocationMetadata(loc, item);
                const result = { hrrrData: hrrrItem, ecmwfData: item, location: loc, mode: 'hrrr_ecmwf' };
                setCachedData(`${loc.id}|hrrr_ecmwf|${elevationMode}`, result);
            });
        } catch (e) {
            console.error("Bulk hrrr_ecmwf prefetch failed:", e);
        }
    } else {
        const modelMap: Record<string, string> = {
            'hrrr': 'gfs_hrrr',
            'gem_hrdps_west': 'gem_hrdps_west',
            'nbm': 'ncep_nbm_conus',
            'nam': 'ncep_nam_conus',
            'gem_regional': 'gem_regional',
            'ecmwf': 'ecmwf_ifs',
            'ecmwf_aifs': 'ecmwf_aifs025_single',
            'ecmwf_aifs_ensemble': 'ecmwf_aifs025_ensemble',
            'gfs': 'gfs_global'
        };
        const omModel = modelMap[modelMode] || modelMode;

        // Use dedicated high-resolution ECMWF endpoint if specified
        if (modelMode === 'ecmwf') {
            const ecmwfUrl = `https://api.open-meteo.com/v1/ecmwf?latitude=${latitudes}&longitude=${longitudes}` +
                `&elevation=${elevations}` +
                `&hourly=${weatherHourlyParamsForLocations(toFetch, 'ecmwf', elevationMode)}` +
                `&daily=sunrise,sunset` +
                `&forecast_days=15` +
                `&past_days=7` +
                `&wind_speed_unit=ms` +
                `&timezone=${timezone}`;

            try {
                console.time(`fetchBulkWeatherData:${modelMode}`);
                const [res, lastRunAvailabilityTime] = await Promise.all([fetch(ecmwfUrl), metaPromise]);
                console.timeEnd(`fetchBulkWeatherData:${modelMode}`);
                if (!res.ok) throw new Error("Bulk ECMWF High-Res fetch failed");
                const data = await res.json();
                const results = Array.isArray(data) ? data : [data];
                results.forEach((item, idx) => {
                    const loc = toFetch[idx];
                    if (!loc) return;
                    item.lastRunAvailabilityTime = lastRunAvailabilityTime;
                    updateCustomLocationMetadata(loc, item);
                    const result = { hrrrData: null, ecmwfData: item, location: loc, mode: 'ecmwf' };
                    setCachedData(`${loc.id}|ecmwf|${elevationMode}`, result);
                });
                return;
            } catch (e) {
                console.error("Bulk ECMWF High-Res prefetch failed:", e);
                return;
            }
        }

        if (modelMode.endsWith('_ensemble')) {
            const url = `${ENSEMBLE_URL}?latitude=${latitudes}&longitude=${longitudes}` +
                `&elevation=${elevations}` +
                `&hourly=${ENSEMBLE_HOURLY_PARAMS}` +
                `&daily=sunrise,sunset` +
                `&models=${omModel}` +
                `&forecast_days=15` +
                `&past_days=7` +
                `&wind_speed_unit=ms` +
                `&timezone=${timezone}`;

            try {
                console.time(`fetchBulkWeatherData:${modelMode}`);
                const [lastRunAvailabilityTime] = await Promise.all([metaPromise]);
                const res = await fetch(url);
                console.timeEnd(`fetchBulkWeatherData:${modelMode}`);
                if (!res.ok) throw new Error(`Bulk Ensemble fetch failed: ${res.status}`);
                const data = await res.json();
                
                // For bulk, data might be an array of arrays (one per location, each containing members)
                const locationResults = Array.isArray(data) ? data : [data];
                
                locationResults.forEach((locData, idx) => {
                    const loc = toFetch[idx];
                    if (!loc) return;
                    const { displayData, members } = extractEnsembleMembers(locData, lastRunAvailabilityTime);
                    updateCustomLocationMetadata(loc, displayData);
                    const result = { hrrrData: null, ecmwfData: displayData, ensembleMembers: members, location: loc, mode: modelMode };
                    setCachedData(`${loc.id}|${modelMode}|${elevationMode}`, result);
                });
                return;
            } catch (e) {
                console.error(`Bulk ensemble prefetch failed for ${omModel}:`, e);
                return;
            }
        }

        let days = 15;
        if (omModel === 'gfs_hrrr' || omModel === 'gem_hrdps_west') days = 2;
        else if (omModel === 'ncep_nam_conus') days = 4;
        else if (omModel === 'ncep_nbm_conus') days = 7;
        else if (omModel === 'gem_regional') days = 4;

        const url = `${BASE_URL}?latitude=${latitudes}&longitude=${longitudes}` +
            `&elevation=${elevations}` +
            `&hourly=${weatherHourlyParamsForLocations(toFetch, modelMode, elevationMode)}` +
            `&daily=sunrise,sunset` +
            `&models=${omModel}` +
            `&forecast_days=${days}` +
            `&past_days=7` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        try {
            console.time(`fetchBulkWeatherData:${modelMode}`);
            const [res, lastRunAvailabilityTime] = await Promise.all([fetch(url), metaPromise]);
            console.timeEnd(`fetchBulkWeatherData:${modelMode}`);

            if (!res.ok) throw new Error(`Bulk ${modelMode} fetch failed: ${res.status}`);
            const data = await res.json();
            const results = Array.isArray(data) ? data : [data];

            results.forEach((item, idx) => {
                const loc = toFetch[idx];
                if (!loc) return;
                item.lastRunAvailabilityTime = lastRunAvailabilityTime;
                updateCustomLocationMetadata(loc, item);
                const result = { hrrrData: null, ecmwfData: item, location: loc, mode: modelMode };
                setCachedData(`${loc.id}|${modelMode}|${elevationMode}`, result);
            });
        } catch (e) {
            console.error(`Bulk ${modelMode} prefetch failed:`, e);
        }
    }
}

/**
 * Fetch min, avg, and max elevations for a single location in one bulk request.
 * This ensures that switching elevation modes for the active location is instantaneous.
 */
export async function fetchElevationTriad(locationId: string, modelMode = 'best_match'): Promise<void> {
    const locs = getLocations();
    const loc = locs[locationId];
    if (!loc || !isValidCoordinate(loc.latitude, loc.longitude)) return;

    const modes = ['min', 'avg', 'max'];
    const missingModes = modes.filter(mode => !hasValidCache(locationId, modelMode, mode));
    
    if (missingModes.length === 0) return;

    const latitudes = missingModes.map(() => loc.latitude).join(',');
    const longitudes = missingModes.map(() => loc.longitude).join(',');
    const elevations = missingModes.map(mode => getApiElevation(loc, mode)).join(',');
    const timezone = "auto";
    const metaPromise = fetchModelStatus(modelMode);

    // Reuse the logic for different models, similar to fetchBulkWeatherData
    // but caching with the specific mode from missingModes[idx]
    
    if (modelMode === 'best_match') {
        const url = `${BASE_URL}?latitude=${latitudes}&longitude=${longitudes}` +
            `&elevation=${elevations}` +
            `&hourly=${weatherHourlyParams(loc, 'best_match', 'max')}` +
            `&daily=sunrise,sunset` +
            `&models=best_match` +
            `&forecast_days=15` +
            `&past_days=7` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        try {
            const [res, lastRunAvailabilityTime] = await Promise.all([fetch(url), metaPromise]);
            if (!res.ok) return;
            const data = await res.json();
            const results = Array.isArray(data) ? data : [data];

            results.forEach((item, idx) => {
                const mode = missingModes[idx];
                item.lastRunAvailabilityTime = lastRunAvailabilityTime;
                updateCustomLocationMetadata(loc, item);
                const result = { hrrrData: null, ecmwfData: item, location: loc, mode: 'best_match' };
                setCachedData(`${loc.id}|best_match|${mode}`, result);
            });
        } catch (e) {
            console.error("Triad prefetch failed:", e);
        }
    } else if (modelMode === 'hrrr_ecmwf') {
        const hrrrUrl = `${BASE_URL}?latitude=${latitudes}&longitude=${longitudes}` +
            `&elevation=${elevations}` +
            `&hourly=${weatherHourlyParams(loc, 'hrrr', 'max')}` +
            `&daily=sunrise,sunset` +
            `&models=gfs_hrrr` +
            `&forecast_days=3` +
            `&past_days=7` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        const ecmwfUrl = `https://api.open-meteo.com/v1/ecmwf?latitude=${latitudes}&longitude=${longitudes}` +
            `&elevation=${elevations}` +
            `&hourly=${weatherHourlyParams(loc, 'ecmwf', 'max')}` +
            `&daily=sunrise,sunset` +
            `&forecast_days=15` +
            `&past_days=7` +
            `&wind_speed_unit=ms` +
            `&timezone=${timezone}`;

        try {
            const [hrrrRes, ecmwfRes, lastRunAvailabilityTime] = await Promise.all([
                fetch(hrrrUrl), fetch(ecmwfUrl), metaPromise
            ]);
            if (!hrrrRes.ok || !ecmwfRes.ok) return;
            const hrrrData = await hrrrRes.json();
            const ecmwfData = await ecmwfRes.json();
            const hrrrResults = Array.isArray(hrrrData) ? hrrrData : [hrrrData];
            const ecmwfResults = Array.isArray(ecmwfData) ? ecmwfData : [ecmwfData];

            ecmwfResults.forEach((item, idx) => {
                const mode = missingModes[idx];
                const hrrrItem = hrrrResults[idx];
                item.lastRunAvailabilityTime = lastRunAvailabilityTime;
                if (hrrrItem) hrrrItem.lastRunAvailabilityTime = lastRunAvailabilityTime;
                updateCustomLocationMetadata(loc, item);
                const result = { hrrrData: hrrrItem, ecmwfData: item, location: loc, mode: 'hrrr_ecmwf' };
                setCachedData(`${loc.id}|hrrr_ecmwf|${mode}`, result);
            });
        } catch (e) {
            console.error("Triad hrrr_ecmwf prefetch failed:", e);
        }
    } else {
        // Generic handling for other models
        const modelMap: Record<string, string> = {
            'hrrr': 'gfs_hrrr',
            'gem_hrdps_west': 'gem_hrdps_west',
            'nbm': 'ncep_nbm_conus',
            'nam': 'ncep_nam_conus',
            'gem_regional': 'gem_regional',
            'ecmwf': 'ecmwf_ifs',
            'ecmwf_aifs': 'ecmwf_aifs025_single',
            'ecmwf_aifs_ensemble': 'ecmwf_aifs025_ensemble',
            'gfs': 'gfs_global'
        };
        const omModel = modelMap[modelMode] || modelMode;

        if (modelMode.endsWith('_ensemble')) {
            const url = `${ENSEMBLE_URL}?latitude=${latitudes}&longitude=${longitudes}` +
                `&elevation=${elevations}` +
                `&hourly=${ENSEMBLE_HOURLY_PARAMS}` +
                `&daily=sunrise,sunset` +
                `&models=${omModel}` +
                `&forecast_days=15` +
                `&past_days=7` +
                `&wind_speed_unit=ms` +
                `&timezone=${timezone}`;

            try {
                const [lastRunAvailabilityTime] = await Promise.all([metaPromise]);
                const res = await fetch(url);
                if (!res.ok) return;
                const data = await res.json();
                const locationResults = Array.isArray(data) ? data : [data];
                
                locationResults.forEach((locData, idx) => {
                    const mode = missingModes[idx];
                    const { displayData, members } = extractEnsembleMembers(locData, lastRunAvailabilityTime);
                    updateCustomLocationMetadata(loc, displayData);
                    const result = { hrrrData: null, ecmwfData: displayData, ensembleMembers: members, location: loc, mode: modelMode };
                    setCachedData(`${loc.id}|${modelMode}|${mode}`, result);
                });
            } catch (e) {
                console.error("Triad ensemble prefetch failed:", e);
            }
        } else {
            let days = 15;
            if (omModel === 'gfs_hrrr' || omModel === 'gem_hrdps_west') days = 2;
            else if (omModel === 'ncep_nam_conus') days = 4;
            else if (omModel === 'ncep_nbm_conus') days = 7;
            else if (omModel === 'gem_regional') days = 4;

            const url = `${BASE_URL}?latitude=${latitudes}&longitude=${longitudes}` +
                `&elevation=${elevations}` +
                `&hourly=${weatherHourlyParams(loc, modelMode, 'max')}` +
                `&daily=sunrise,sunset` +
                `&models=${omModel}` +
                `&forecast_days=${days}` +
                `&past_days=7` +
                `&wind_speed_unit=ms` +
                `&timezone=${timezone}`;

            try {
                const [res, lastRunAvailabilityTime] = await Promise.all([fetch(url), metaPromise]);
                if (!res.ok) return;
                const data = await res.json();
                const results = Array.isArray(data) ? data : [data];

                results.forEach((item, idx) => {
                    const mode = missingModes[idx];
                    item.lastRunAvailabilityTime = lastRunAvailabilityTime;
                    updateCustomLocationMetadata(loc, item);
                    const result = { hrrrData: null, ecmwfData: item, location: loc, mode: modelMode };
                    setCachedData(`${loc.id}|${modelMode}|${mode}`, result);
                });
            } catch (e) {
                console.error(`Triad ${modelMode} prefetch failed:`, e);
            }
        }
    }
}
