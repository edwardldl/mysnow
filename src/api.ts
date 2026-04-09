const DEFAULT_LOCATIONS = {
    palisades: {
        id: 'palisades',
        name: "Palisades Tahoe",
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

export function saveLocation(id, name, lat, lon) {
    const customLocsJson = localStorage.getItem('calisnow_locations');
    const customLocs = customLocsJson ? JSON.parse(customLocsJson) : {};

    // Rough estimate of elevation based on location or just set to 0 for simplicity
    // User isn't inputting elevation
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

export function removeLocation(id) {
    if (DEFAULT_LOCATIONS[id]) return getLocations(); // Can't delete defaults

    const customLocsJson = localStorage.getItem('calisnow_locations');
    if (!customLocsJson) return getLocations();

    const customLocs = JSON.parse(customLocsJson);
    delete customLocs[id];

    localStorage.setItem('calisnow_locations', JSON.stringify(customLocs));
    return getLocations();
}

const BASE_URL = "https://api.open-meteo.com/v1/forecast";
const HISTORICAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast";

const PRESSURE_LEVELS = ["1000hPa", "925hPa", "850hPa", "700hPa", "600hPa", "500hPa", "400hPa", "300hPa"];

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
    "soil_temperature_0cm",
    PRESSURE_LEVELS.map(l => `temperature_${l}`).join(','),
    PRESSURE_LEVELS.map(l => `relative_humidity_${l}`).join(','),
    PRESSURE_LEVELS.map(l => `geopotential_height_${l}`).join(','),
    PRESSURE_LEVELS.map(l => `vertical_velocity_${l}`).join(','),
    "wind_speed_700hPa"
].join(",");

/**
 * Fetch data from Open-Meteo API
 */
export async function fetchWeatherData(locationKey, modelMode = 'hrrr_ecmwf') {
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

            if (loc.isCustom && data.elevation) {
                loc.elevationM = Math.round(data.elevation);
                loc.elevationFt = Math.round(data.elevation * 3.28084);
            }

            return { hrrrData: null, ecmwfData: data, location: loc, mode: 'best_match' };
        } catch (error) {
            console.error("Error fetching Best Match data:", error);
            throw error;
        }
    } else {
        // 1. Fetch HRRR (0-48 hours)
        const hrrrUrl = `${BASE_URL}?latitude=${loc.latitude}&longitude=${loc.longitude}` +
            `&hourly=${HOURLY_PARAMS}` +
            `&daily=sunrise,sunset` +
            `&models=gfs_hrrr` +
            `&forecast_days=2` +
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

            // Sometimes the elevation comes back from the API! Let's optionally overwrite missing ones
            if (loc.isCustom && ecmwfData.elevation) {
                loc.elevationM = Math.round(ecmwfData.elevation);
                loc.elevationFt = Math.round(ecmwfData.elevation * 3.28084);
            }

            return { hrrrData, ecmwfData, location: loc, mode: 'hrrr_ecmwf' };
        } catch (error) {
            console.error("Error fetching weather data:", error);
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

        if (loc.isCustom && data.elevation) {
            loc.elevationM = Math.round(data.elevation);
            loc.elevationFt = Math.round(data.elevation * 3.28084);
        }

        return { hrrrData: null, ecmwfData: data, location: loc, mode: 'historical', model };
    } catch (error) {
        console.error("Error fetching historical data:", error);
        throw error;
    }
}
