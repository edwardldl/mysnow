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
    "temperature_850hPa",
    "temperature_700hPa",
    "soil_temperature_0cm",
    "temperature_1000hPa,temperature_925hPa,temperature_500hPa,temperature_300hPa",
    "relative_humidity_1000hPa,relative_humidity_925hPa,relative_humidity_850hPa,relative_humidity_700hPa,relative_humidity_500hPa,relative_humidity_300hPa",
    "geopotential_height_1000hPa,geopotential_height_925hPa,geopotential_height_850hPa,geopotential_height_700hPa,geopotential_height_500hPa,geopotential_height_300hPa",
    "vertical_velocity_1000hPa,vertical_velocity_925hPa,vertical_velocity_850hPa,vertical_velocity_700hPa,vertical_velocity_500hPa,vertical_velocity_300hPa",
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
        const ecmwfUrl = `${BASE_URL}?latitude=${loc.latitude}&longitude=${loc.longitude}` +
            `&hourly=${HOURLY_PARAMS},snowfall_water_equivalent` +
            `&daily=sunrise,sunset` +
            `&models=ecmwf_ifs` +
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
