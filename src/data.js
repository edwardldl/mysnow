/**
 * Calculate Dynamic SLR using the Kuchera Method
 * Uses 2m temperature and 10m wind speed to derive snow fluffiness.
 * Ignores the API's pre-calculated snowfall field (which uses a fixed ~7:1 ratio).
 *
 * Formula: SLR = 12.0 - (0.5 * T) + (0.06 * T²)
 * Validation: T=-10°C → SLR ≈ 23:1, T=0°C → SLR = 12:1
 *
 * @param {number|null} temperature - 2m temperature in Celsius
 * @param {number|null} windSpeed - 10m wind speed in km/h
 * @returns {number} SLR ratio (0 if temp > 2°C / rain)
 */
export function calculateKucheraSLR(temperature, windSpeed) {
    // Rain threshold
    if (temperature === null || temperature > 2) return 0;

    // Kuchera quadratic formula
    let slr = 12.0 - (0.5 * temperature) + (0.06 * temperature * temperature);

    // Wind adjustment: mechanical fracturing of snow crystals
    if (windSpeed !== null && windSpeed > 50) {
        slr = Math.min(slr, 10); // Cap at 10:1 in extreme wind
    } else if (windSpeed !== null && windSpeed > 25) {
        slr *= 0.85; // 15% reduction for moderate wind
    }

    return slr;
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
 */
export function blendForecasts(hrrr, ecmwf) {
    const blended = [];

    // Process hourly
    const ecmwfTimes = ecmwf.hourly.time;
    const hrrrTimes = hrrr ? hrrr.hourly.time : [];

    for (let i = 0; i < ecmwfTimes.length; i++) {
        const time = ecmwfTimes[i];
        const dateObj = new Date(time);

        const hrrrIdx = hrrrTimes.indexOf(time);

        let point = { time, dateObj };

        // Use HRRR if available, else ECMWF (or Best Match data which is passed as ecmwf)
        if (hrrr && hrrrIdx !== -1 && hrrr.hourly.snowfall[hrrrIdx] !== null) {
            point.model = 'HRRR';
            point.precipitation = hrrr.hourly.precipitation[hrrrIdx];
            point.liquidMM = point.precipitation || 0;
            point.temperature = hrrr.hourly.temperature_2m[hrrrIdx];
            point.windSpeed = hrrr.hourly.wind_speed_10m ? hrrr.hourly.wind_speed_10m[hrrrIdx] : null;
            point.windDir = hrrr.hourly.wind_direction_10m ? hrrr.hourly.wind_direction_10m[hrrrIdx] : null;
            point.snowDepth = hrrr.hourly.snow_depth ? hrrr.hourly.snow_depth[hrrrIdx] : null;
            point.precipChance = hrrr.hourly.precipitation_probability ? hrrr.hourly.precipitation_probability[hrrrIdx] : null;
            point.feelsLike = hrrr.hourly.apparent_temperature ? hrrr.hourly.apparent_temperature[hrrrIdx] : null;
            point.rh = hrrr.hourly.relative_humidity_2m ? hrrr.hourly.relative_humidity_2m[hrrrIdx] : null;
            point.gusts = hrrr.hourly.wind_gusts_10m ? hrrr.hourly.wind_gusts_10m[hrrrIdx] : null;
            point.clouds = hrrr.hourly.cloud_cover ? hrrr.hourly.cloud_cover[hrrrIdx] : null;
            point.snowLevel = hrrr.hourly.freezing_level_height ? hrrr.hourly.freezing_level_height[hrrrIdx] : null;
        } else {
            point.model = hrrr ? 'ECMWF' : 'BEST'; // If hrrr is null, we are in best_match mode
            point.precipitation = ecmwf.hourly.precipitation[i];
            // ECMWF / Best Match: prefer snowfall_water_equivalent (snow-specific liquid), fall back to precipitation
            point.liquidMM = (ecmwf.hourly.snowfall_water_equivalent && ecmwf.hourly.snowfall_water_equivalent[i] != null)
                ? ecmwf.hourly.snowfall_water_equivalent[i]
                : (point.precipitation || 0);
            point.temperature = ecmwf.hourly.temperature_2m[i];
            point.windSpeed = ecmwf.hourly.wind_speed_10m ? ecmwf.hourly.wind_speed_10m[i] : null;
            point.windDir = ecmwf.hourly.wind_direction_10m ? ecmwf.hourly.wind_direction_10m[i] : null;
            point.snowDepth = ecmwf.hourly.snow_depth ? ecmwf.hourly.snow_depth[i] : null;
            point.precipChance = ecmwf.hourly.precipitation_probability ? ecmwf.hourly.precipitation_probability[i] : null;
            point.feelsLike = ecmwf.hourly.apparent_temperature ? ecmwf.hourly.apparent_temperature[i] : null;
            point.rh = ecmwf.hourly.relative_humidity_2m ? ecmwf.hourly.relative_humidity_2m[i] : null;
            point.gusts = ecmwf.hourly.wind_gusts_10m ? ecmwf.hourly.wind_gusts_10m[i] : null;
            point.clouds = ecmwf.hourly.cloud_cover ? ecmwf.hourly.cloud_cover[i] : null;
            point.snowLevel = ecmwf.hourly.freezing_level_height ? ecmwf.hourly.freezing_level_height[i] : null;
        }

        // Compute dynamic SLR and snowfall depth using Kuchera Method
        if (point.liquidMM > 0) {
            point.slr = calculateKucheraSLR(point.temperature, point.windSpeed);
            if (point.slr > 0) {
                point.snowfall = (point.liquidMM * point.slr) / 10;
                point.slrCategory = getSLRCategory(point.slr, point.liquidMM);
            } else {
                point.snowfall = 0;
                point.slrCategory = 'rain';
            }
        } else if (point.precipitation > 0) {
            point.slr = null;
            point.snowfall = 0;
            point.slrCategory = 'rain';
        } else {
            point.slr = null;
            point.snowfall = 0;
            point.slrCategory = null;
        }

        blended.push(point);
    }

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
