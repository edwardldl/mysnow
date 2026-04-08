/**
 * Clamp a value between min and max
 * @param {number} v - Value to clamp
 * @param {number} min - Minimum value
 * @param {number} max - Maximum value
 * @returns {number} Clamped value
 */
function clamp(v, min, max) {
    return Math.max(min, Math.min(max, v));
}

/**
 * Smooth values using 3-hour rolling average (mean3)
 * @param {Array} arr - Array of values
 * @param {number} i - Current index
 * @returns {number|null} Smoothed value
 */
function mean3(arr, i) {
    const vals = [arr[i - 1], arr[i], arr[i + 1]].filter(v => v != null && !isNaN(v));
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : arr[i];
}

/**
 * Simple wet-bulb proxy from T, Td, RH
 * @param {number} T - Temperature in Celsius
 * @param {number} Td - Dew point in Celsius
 * @param {number} RH - Relative humidity in %
 * @returns {number} Wet-bulb temperature
 */
function wetBulb(T, Td, RH) {
    return T - ((T - Td) * (1 - RH / 100) * 0.5);
}

// Snow weather codes from Open-Meteo WMO codes
const SNOW_CODES = new Set([71, 73, 75, 77, 85, 86]);

/**
 * Calculate ECMWF snow level using IFS-specific melting physics
 * @param {Object} hourly - Hourly data from ECMWF
 * @param {number} elevationM - Location elevation in meters
 * @returns {Array} Array of snow level objects keyed by time
 */
function ecmwfSnowLevel(hourly, elevationM) {
    return hourly.time.map((_, i) => {
        const FL = hourly.freezing_level_height[i];
        const T2 = hourly.temperature_2m[i];
        const RH = hourly.relative_humidity_2m[i];
        const precip = hourly.precipitation[i] || 0;

        // base offset from ECMWF melting physics
        let offset = 200; // default for IFS
        if (RH > 80 && precip > 0.5) offset = 280;
        else if (RH < 50) offset = 120;

        // wet snow allowance from ECMWF table
        if (T2 > 0 && T2 < 2 && precip > 0) offset += 100;

        const snowLevel = FL - offset;
        // If snowLevel is negative, add location elevation to get the "right" snow level
        const adjustedSnowLevel = snowLevel < 0 ? snowLevel + elevationM : snowLevel;

        return {
            time: hourly.time[i],
            snowLevel: Math.round(adjustedSnowLevel)
        };
    });
}

/**
 * Check if weather code indicates snow
 * @param {number|null} code - WMO weather code
 * @returns {boolean}
 */
function isSnowWeatherCode(code) {
    return code !== null && SNOW_CODES.has(code);
}

/**
 * Calculate Sierra Nevada maritime SLR using Roebber 2003 with wet-bulb and maritime corrections
 * Calibrated to avoid 14:1+ overestimates common in maritime snow.
 *
 * @param {Array} hourly - Array of hourly data points
 * @returns {Array} Hourly data with calculated SLR and snowfall added
 */
function calculateSierraSnowfall(hourly) {
    const n = hourly.length;
    const out = [];
    let prevSlr = 11.0; // Sierra typical start

    // Pre-smooth drivers
    const Tsm = [], Wsm = [], RHsm = [], Tdsm = [], Twsm = [];
    for (let i = 0; i < n; i++) {
        const t = mean3(hourly.map(h => h.temperature), i);
        const w = mean3(hourly.map(h => h.windSpeed), i);
        const rh = mean3(hourly.map(h => h.rh), i);
        const td = mean3(hourly.map(h => h.dewPoint), i);
        const tw = wetBulb(t, td, rh);
        Tsm.push(t);
        Wsm.push(w);
        RHsm.push(rh);
        Tdsm.push(td);
        Twsm.push(tw);
    }

    for (let i = 0; i < n; i++) {
        const point = hourly[i];
        const precip = point.liquidMM || 0;
        const code = point.weatherCode;
        const Tuse = Math.min(Twsm[i], 0);
        const W = Wsm[i];
        const RH = RHsm[i];
        const Tw = Twsm[i];

        let slr = null;
        let snow = 0;

        const isSnow = isSnowWeatherCode(code) && Tw < 0.5 && precip > 0;

        if (isSnow) {
            // 1. Roebber base
            let rho = 0.0122 * Tuse * Tuse + 0.159 * Tuse + 0.141 + 0.00316 * W;
            // 2. Sierra maritime correction
            rho = rho * 1.18 + 0.004 * precip;
            rho = clamp(rho, 0.05, 0.20);

            const slr0 = 1 / rho;
            const slrMaritime = slr0 * 0.85;

            // 3. Ramps, not steps
            const windPen = 3.0 * clamp((W - 3) / 6, 0, 1);
            const rimePen = 1.5 * clamp((RH - 70) / 20, 0, 1) * clamp(precip / 3, 0, 1);
            const coldBon = 1.0 * clamp((-10 - Tuse) / 5, 0, 1) * (RH / 100);

            let slrAdj = slrMaritime - windPen - rimePen + coldBon;

            // 4. Warm nose cap using wet-bulb
            if (Tw > -2.5) {
                const warmCap = 11 - (Tw + 2.5) * 1.2;
                slrAdj = Math.min(slrAdj, warmCap);
            }

            // 5. Temporal stability
            slrAdj = clamp(slrAdj, prevSlr - 1.5, prevSlr + 1.5);
            slr = clamp(slrAdj, 6, 16);
            prevSlr = slr;

            snow = precip * slr / 10;
        } else {
            // Carry previous for stability but do not accumulate
            prevSlr = clamp(prevSlr, 6, 16);
        }

        out.push({
            ...point,
            slr: slr ? +slr.toFixed(1) : null,
            snowfall: +snow.toFixed(1),
            method: slr ? 'sierra_maritime' : null
        });
    }

    return out;
}

export function getSLRCategory(slr, liquidMM) {
    if ((!slr || slr <= 0) && liquidMM > 0) return 'rain';
    if (!slr || slr <= 0) return null;
    if (slr < 10) return 'wet';
    if (slr <= 15) return 'std';
    return 'powder';
}

/**
 * Blend HRRR (0-48h) with ECMWF (48h+)
 * Uses Sierra Nevada maritime SLR with wet-bulb temperature and temporal smoothing
 * @param {Object} hrrr - HRRR forecast data
 * @param {Object} ecmwf - ECMWF/Best Match forecast data
 * @param {Object} location - Location object with elevationM
 * @returns {Object} Blended hourly and daily data
 */
export function blendForecasts(hrrr, ecmwf, location) {
    // Calculate ECMWF-specific snow levels if elevation available
    let ecmwfSnowLevels = null;
    if (location && location.elevationM && typeof location.elevationM === 'number' && ecmwf && ecmwf.hourly) {
        const snowLevelData = ecmwfSnowLevel(ecmwf.hourly, location.elevationM);
        ecmwfSnowLevels = new Map(snowLevelData.map(item => [item.time, item.snowLevel]));
    }

    // First pass: collect raw hourly data
    const rawHourly = [];
    const ecmwfTimes = ecmwf.hourly.time;
    const hrrrTimes = hrrr ? hrrr.hourly.time : [];

    for (let i = 0; i < ecmwfTimes.length; i++) {
        const time = ecmwfTimes[i];
        const dateObj = new Date(time);
        const hrrrIdx = hrrrTimes.indexOf(time);

        let point = { time, dateObj };

        // Use HRRR if available, else ECMWF (or Best Match data which is passed as ecmwf)
        if (hrrr && hrrrIdx !== -1) {
            point.model = 'HRRR';
            point.precipitation = hrrr.hourly.precipitation[hrrrIdx];
            point.liquidMM = point.precipitation || 0;
            point.temperature = hrrr.hourly.temperature_2m[hrrrIdx];
            point.dewPoint = hrrr.hourly.dew_point_2m ? hrrr.hourly.dew_point_2m[hrrrIdx] : null;
            point.windSpeed = hrrr.hourly.wind_speed_10m ? hrrr.hourly.wind_speed_10m[hrrrIdx] : null;
            point.windDir = hrrr.hourly.wind_direction_10m ? hrrr.hourly.wind_direction_10m[hrrrIdx] : null;
            point.snowDepth = hrrr.hourly.snow_depth ? hrrr.hourly.snow_depth[hrrrIdx] : null;
            point.precipChance = hrrr.hourly.precipitation_probability ? hrrr.hourly.precipitation_probability[hrrrIdx] : null;
            point.feelsLike = hrrr.hourly.apparent_temperature ? hrrr.hourly.apparent_temperature[hrrrIdx] : null;
            point.rh = hrrr.hourly.relative_humidity_2m ? hrrr.hourly.relative_humidity_2m[hrrrIdx] : null;
            point.gusts = hrrr.hourly.wind_gusts_10m ? hrrr.hourly.wind_gusts_10m[hrrrIdx] : null;
            point.clouds = hrrr.hourly.cloud_cover ? hrrr.hourly.cloud_cover[hrrrIdx] : null;
            point.snowLevel = hrrr.hourly.freezing_level_height ? hrrr.hourly.freezing_level_height[hrrrIdx] : null;
            point.weatherCode = hrrr.hourly.weather_code ? hrrr.hourly.weather_code[hrrrIdx] : null;
        } else {
            point.model = hrrr ? 'ECMWF' : 'BEST';
            point.precipitation = ecmwf.hourly.precipitation[i];
            point.liquidMM = (ecmwf.hourly.snowfall_water_equivalent && ecmwf.hourly.snowfall_water_equivalent[i] != null)
                ? ecmwf.hourly.snowfall_water_equivalent[i]
                : (point.precipitation || 0);
            point.temperature = ecmwf.hourly.temperature_2m[i];
            point.dewPoint = ecmwf.hourly.dew_point_2m ? ecmwf.hourly.dew_point_2m[i] : null;
            point.windSpeed = ecmwf.hourly.wind_speed_10m ? ecmwf.hourly.wind_speed_10m[i] : null;
            point.windDir = ecmwf.hourly.wind_direction_10m ? ecmwf.hourly.wind_direction_10m[i] : null;
            point.snowDepth = ecmwf.hourly.snow_depth ? ecmwf.hourly.snow_depth[i] : null;
            point.precipChance = ecmwf.hourly.precipitation_probability ? ecmwf.hourly.precipitation_probability[i] : null;
            point.feelsLike = ecmwf.hourly.apparent_temperature ? ecmwf.hourly.apparent_temperature[i] : null;
            point.rh = ecmwf.hourly.relative_humidity_2m ? ecmwf.hourly.relative_humidity_2m[i] : null;
            point.gusts = ecmwf.hourly.wind_gusts_10m ? ecmwf.hourly.wind_gusts_10m[i] : null;
            point.clouds = ecmwf.hourly.cloud_cover ? ecmwf.hourly.cloud_cover[i] : null;
            // Use ECMWF-specific snow level calculation if available, else fall back to freezing level
            if (ecmwfSnowLevels && ecmwfSnowLevels.has(time)) {
                point.snowLevel = ecmwfSnowLevels.get(time);
            } else {
                point.snowLevel = ecmwf.hourly.freezing_level_height ? ecmwf.hourly.freezing_level_height[i] : null;
            }
            point.weatherCode = ecmwf.hourly.weather_code ? ecmwf.hourly.weather_code[i] : null;
        }

        rawHourly.push(point);
    }

    // Second pass: calculate Sierra Nevada maritime SLR with temporal smoothing
    const blended = calculateSierraSnowfall(rawHourly);

    // Apply SLR categories
    blended.forEach(point => {
        if (point.slr) {
            point.slrCategory = getSLRCategory(point.slr, point.liquidMM);
        } else if (point.precipitation > 0) {
            point.slrCategory = 'rain';
        } else {
            point.slrCategory = null;
        }
    });

    return {
        hourly: blended,
        daily: {
            time: ecmwf.daily ? ecmwf.daily.time : [],
            sunrise: ecmwf.daily ? ecmwf.daily.sunrise : [],
            sunset: ecmwf.daily ? ecmwf.daily.sunset : []
        }
    };
}

/**
 * Group hourly data into daily chunks, then 3-hourly blocks.
 */
export function groupData(blendedData) {
    const days = {};
    const { hourly, daily } = blendedData;

    hourly.forEach(point => {
        // time format from API: "YYYY-MM-DDTHH:MM"
        const dateStr = point.time.split('T')[0];
        const hour = parseInt(point.time.split('T')[1].split(':')[0], 10);

        if (!days[dateStr]) {
            let sunriseStr = null;
            let sunsetStr = null;
            if (daily && daily.time) {
                const dIdx = daily.time.indexOf(dateStr);
                if (dIdx !== -1) {
                    sunriseStr = daily.sunrise[dIdx];
                    sunsetStr = daily.sunset[dIdx];
                }
            }

            days[dateStr] = {
                dateStr,
                dateObj: point.dateObj,
                sunrise: sunriseStr,
                sunset: sunsetStr,
                totalSnowfall: 0,
                totalPrecipitation: 0,
                models: new Set(),
                hourly: [],
                windows: [], // 3-hour windows
                snowDepthValues: []
            };
        }

        days[dateStr].hourly.push(point);
        days[dateStr].models.add(point.model);
        if (point.snowfall > 0) {
            days[dateStr].totalSnowfall += point.snowfall;
        }
        if (point.precipitation > 0) {
            days[dateStr].totalPrecipitation += point.precipitation;
        }
        if (point.snowDepth !== null && point.snowDepth !== undefined) {
            days[dateStr].snowDepthValues.push(point.snowDepth);
        }
    });

    // Process 3-hourly blocks per day
    Object.values(days).forEach(day => {
        const lastDepth = day.snowDepthValues.length ? day.snowDepthValues[day.snowDepthValues.length - 1] : null;
        day.snowDepth = lastDepth !== null ? (lastDepth * 100).toFixed(0) + ' cm' : '--';

        // Initialize windows (0-2, 3-5, 6-8, 9-11, 12-14, 15-17, 18-20, 21-23)
        for (let i = 0; i < 8; i++) {
            day.windows.push({
                startHour: i * 3,
                label: `${i * 3}:00`,
                snowfall: 0,
                precip: 0,
                temps: [],
                windSpeeds: [],
                windDirs: [],
                slrs: [],
                precipChances: [],
                feelsLikes: [],
                rhs: [],
                gusts: [],
                clouds: [],
                snowLevels: []
            });
        }

        day.hourly.forEach(point => {
            const hour = parseInt(point.time.split('T')[1].split(':')[0], 10);
            const windowIdx = Math.floor(hour / 3);
            const window = day.windows[windowIdx];

            if (point.snowfall > 0) window.snowfall += point.snowfall;
            if (point.precipitation > 0) window.precip += point.precipitation;
            if (point.temperature !== null) window.temps.push(point.temperature);
            if (point.windSpeed !== null) window.windSpeeds.push(point.windSpeed);
            if (point.windDir !== null) window.windDirs.push(point.windDir);
            if (point.slr !== null) window.slrs.push(point.slr);
            if (point.precipChance !== null) window.precipChances.push(point.precipChance);
            if (point.feelsLike !== null) window.feelsLikes.push(point.feelsLike);
            if (point.rh !== null) window.rhs.push(point.rh);
            if (point.gusts !== null) window.gusts.push(point.gusts);
            if (point.clouds !== null) window.clouds.push(point.clouds);
            if (point.snowLevel !== null) window.snowLevels.push(point.snowLevel);
        });

        // Finalize window calculations
        let dayMaxSnowfall = 0;
        day.windows.forEach(w => {
            w.avgTemp = w.temps.length ? w.temps.reduce((a, b) => a + b) / w.temps.length : null;
            w.avgWindSpeed = w.windSpeeds.length ? w.windSpeeds.reduce((a, b) => a + b) / w.windSpeeds.length : null;

            // For wind direction, simplistic average is okay if we just take the last or most prominent
            // better to just take the middle hour's direction for simplicity
            w.dominantWindDir = w.windDirs.length ? w.windDirs[Math.floor(w.windDirs.length / 2)] : null;

            w.avgSlr = w.slrs.length ? w.slrs.reduce((a, b) => a + b) / w.slrs.length : null;
            w.slrCategory = getSLRCategory(w.avgSlr, w.precip);

            // New params
            w.maxPrecipChance = w.precipChances.length ? Math.max(...w.precipChances) : null;
            w.avgFeelsLike = w.feelsLikes.length ? w.feelsLikes.reduce((a, b) => a + b) / w.feelsLikes.length : null;
            w.avgRh = w.rhs.length ? w.rhs.reduce((a, b) => a + b) / w.rhs.length : null;
            w.maxGust = w.gusts.length ? Math.max(...w.gusts) : null;
            w.avgCloud = w.clouds.length ? w.clouds.reduce((a, b) => a + b) / w.clouds.length : null;
            w.avgSnowLevel = w.snowLevels.length ? w.snowLevels.reduce((a, b) => a + b) / w.snowLevels.length : null;

            if (w.snowfall > dayMaxSnowfall) dayMaxSnowfall = w.snowfall;
        });

        let maxHourlySnowfall = 0;
        day.hourly.forEach(h => {
            if (h.snowfall && h.snowfall > maxHourlySnowfall) maxHourlySnowfall = h.snowfall;
        });
        day.maxHourlySnowfall = maxHourlySnowfall;

        day.maxWindowSnowfall = dayMaxSnowfall;
        day.modelString = Array.from(day.models).join('-');
    });

    // Return as array sorted by date
    return Object.values(days).sort((a, b) => a.dateObj - b.dateObj);
}
