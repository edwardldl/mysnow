import { OpenMeteoResponse, OpenMeteoDaily, Location, BlendedHour, DayData, RollingStats } from './types';
import { normalizeProfile } from './snow/profile';
import { intervalDirectionMean, intervalMean, intervalRepresentativeLayers } from './snow/atmosphere/interval';
import { calculateSnowfall } from './snow/snowfall';
import { advanceSnowpack } from './snow/snowpack';
import { summarizeEnsembleSnowfall } from './snow/ensemble';
import { toSlrMethod } from './snow/slr';
import { DEFAULT_SLR_METHOD, type ForecastProvenance, type SnowLayer } from './snow/types';

const SEVERITY_WEIGHTS: Record<number, number> = {
    // Thunderstorm
    99: 100, 96: 99, 95: 98,
    // Snow
    75: 90, 86: 89, 73: 88, 85: 87, 71: 86, 77: 85,
    // Rain
    65: 80, 82: 79, 63: 78, 81: 77, 61: 76, 80: 75,
    67: 74, 66: 73,
    // Drizzle
    55: 60, 53: 59, 51: 58, 57: 57, 56: 56,
    // Fog
    48: 50, 45: 49,
    // Cloudy/Clear
    3: 40, 2: 39, 1: 38, 0: 37
};

function forecastProvenance(data: OpenMeteoResponse, index: number): ForecastProvenance {
    const validTime = data.hourly.time[index] ?? '';
    const firstTime = Date.parse(data.hourly.time[0] ?? '');
    const secondTime = Date.parse(data.hourly.time[1] ?? '');
    const returnedTimeStepMinutes = Number.isFinite(firstTime) && Number.isFinite(secondTime)
        ? Math.max(1, Math.round((secondTime - firstTime) / 60_000))
        : 60;
    const metadata = data.requestMetadata;
    return {
        modelId: data.modelIdentity ?? 'unknown',
        initializationTime: null,
        validTime,
        leadHours: null,
        nativeTimeStepMinutes: null,
        returnedTimeStepMinutes,
        requestedLatitude: metadata?.requestedLatitude ?? data.latitude ?? 0,
        requestedLongitude: metadata?.requestedLongitude ?? data.longitude ?? 0,
        returnedLatitude: data.latitude ?? metadata?.requestedLatitude ?? 0,
        returnedLongitude: data.longitude ?? metadata?.requestedLongitude ?? 0,
        requestedElevationM: metadata?.requestedElevationM ?? null,
        modelGridElevationM: metadata?.modelGridElevationM ?? null,
        predictionVersion: 'slope-aware-v1',
        calibrationVersion: 'uncalibrated-mvp-v1',
    };
}

/**
 * Calculate absolute snow level based on evaporative and diabatic cooling
 */
function calcSnowLevel(FL: number, RH: number, precip: number): number {
    let offset = 250;
    if (RH < 100) offset += (100 - RH) * 2.5;
    if (precip > 1.0) offset += Math.min(precip * 15, 200);
    return Math.max(0, FL - offset);
}

/**
 * Applies the shared precipitation → phase → SLR → snowpack pipeline.
 */
function processSnowfallVariables(hourly: Partial<BlendedHour>[], algorithm: string, elevation: number): BlendedHour[] {
    const out: BlendedHour[] = [];
    let snowLayersOnGround: SnowLayer[] = [];
    const method = toSlrMethod(algorithm);

    for (let i = 0; i < hourly.length; i++) {
        const point = hourly[i];
        const previous = i > 0 ? hourly[i - 1] : undefined;
        const representativeSurfacePressure = intervalMean(previous?.surface_pressure, point.surface_pressure);
        const profile = normalizeProfile(intervalRepresentativeLayers(previous?.layers, point.layers), {
            surfacePressureHpa: representativeSurfacePressure,
            stationElevationM: elevation,
        });
        const snowfallResult = calculateSnowfall({
            time: point.time ?? '',
            precipitationMm: point.precipitation ?? null,
            precipitationProbabilityPct: point.precipChance ?? null,
            snowfallCm: point.snowfall_raw ?? null,
            snowfallWaterEquivalentMm: point.snowfallWaterEquivalentMm ?? null,
            precipitationType: point.precipitationType ?? null,
            weatherCode: point.weatherCode ?? null,
            surface: {
                temperatureC: intervalMean(previous?.temperature, point.temperature),
                dewPointC: intervalMean(previous?.dewPoint, point.dewPoint),
                relativeHumidityPct: intervalMean(previous?.rh, point.rh),
                wetBulbTemperatureC: intervalMean(previous?.wet_bulb_temperature_2m, point.wet_bulb_temperature_2m),
                windSpeedMs: intervalMean(previous?.windSpeed, point.windSpeed),
                windDirectionDeg: intervalDirectionMean(previous?.windDir, point.windDir),
                surfacePressureHpa: representativeSurfacePressure,
                stationElevationM: elevation,
            },
            profile,
        }, method);
        const snowpack = advanceSnowpack(
            snowLayersOnGround,
            snowfallResult,
            point.time ?? '',
            point.temperature ?? null,
        );
        snowLayersOnGround = snowpack.layers;

        out.push({
            ...point,
            liquidMM: snowfallResult.precipitationMm,
            slr: snowfallResult.freshSlr,
            snowfall: snowfallResult.freshSnowCm,
            method: snowfallResult.method,
            slrCategory: null,
            snowDepth: snowpack.depthCm / 100,
            snowfallResult,
            snowFraction: snowfallResult.snowFraction,
            frozenSweMm: snowfallResult.frozenSweMm,
            rainMm: snowfallResult.rainMm,
            snowpackStep: snowpack,
        } as BlendedHour);
    }

    return out;
}

export function getSLRCategory(slr: number | null, liquidMM: number): string | null {
    if ((!slr || slr <= 0) && liquidMM > 0) return 'rain';
    if (!slr || slr <= 0) return null;
    if (slr < 10) return 'wet';
    if (slr < 15) return 'std';
    return 'powder';
}

export function blendForecasts(
    hrrr: OpenMeteoResponse | null,
    ecmwf: OpenMeteoResponse,
    location: Location,
    slrAlgorithm: string = DEFAULT_SLR_METHOD,
    modelMode = 'best_match',
    ensembleMembers?: OpenMeteoResponse[],
): { hourly: BlendedHour[], daily: OpenMeteoDaily, lastRunAvailabilityTime?: number } {
    const rawHourly: Partial<BlendedHour>[] = [];
    const ecmwfTimes = ecmwf.hourly.time;
    const hrrrTimes = hrrr ? hrrr.hourly.time : [];

    const extractLayers = (dataSource: OpenMeteoResponse | null, index: number) => {
        if (!dataSource || !dataSource.hourly) return [];
        const hourly = dataSource.hourly;
        const getHourlyNumber = (key: string): number | null => {
            const values = hourly[key];
            if (!Array.isArray(values)) return null;
            const value = values[index];
            return typeof value === 'number' && Number.isFinite(value) ? value : null;
        };
        const levels = Object.keys(hourly)
            .map(key => key.match(/^temperature_(\d+)hPa$/)?.[1])
            .filter((level): level is string => level !== undefined)
            .map(Number)
            .filter((level, index, all) => all.indexOf(level) === index)
            .sort((a, b) => b - a);

        return levels.map(level => ({
            pressureHpa: level,
            temperatureC: getHourlyNumber(`temperature_${level}hPa`),
            dewPointC: getHourlyNumber(`dew_point_${level}hPa`),
            relativeHumidityWaterPct: getHourlyNumber(`relative_humidity_${level}hPa`),
            geopotentialHeightM: getHourlyNumber(`geopotential_height_${level}hPa`),
            verticalVelocityMs: getHourlyNumber(`vertical_velocity_${level}hPa`),
            windSpeedMs: getHourlyNumber(`wind_speed_${level}hPa`),
            windDirectionDeg: getHourlyNumber(`wind_direction_${level}hPa`),
            cloudCoverPct: getHourlyNumber(`cloud_cover_${level}hPa`),
        }));
    };

    const modelLabel = modelMode === 'best_match'
        ? 'BEST'
        : (modelMode === 'hrrr_ecmwf' && !hrrr
            ? (ecmwf.modelIdentity ?? 'ecmwf').toUpperCase()
            : modelMode.toUpperCase());

    for (let i = 0; i < ecmwfTimes.length; i++) {
        const time = ecmwfTimes[i];
        const dateObj = new Date(time);
        const hrrrIdx = hrrrTimes.indexOf(time);

        const point: Partial<BlendedHour> = { time, dateObj };
        let pointSource = ecmwf;

        if (hrrr && hrrrIdx !== -1 && hrrr.hourly.temperature_2m[hrrrIdx] !== null) {
            pointSource = hrrr;
            point.model = 'HRRR';
            point.precipitation = hrrr.hourly.precipitation ? hrrr.hourly.precipitation[hrrrIdx] : 0;
            point.liquidMM = point.precipitation;
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

            let FL = hrrr.hourly.freezing_level_height ? hrrr.hourly.freezing_level_height[hrrrIdx] : null;
            if (FL == null && location && typeof location.elevationM === 'number' && point.temperature != null) {
                FL = location.elevationM + (point.temperature * (1000 / 6.5));
            }

            if (FL != null) {
                const precip = point.precipitation || 0;
                const RH = point.rh || 70;
                point.snowLevel = Math.round(calcSnowLevel(FL, RH, precip));
            } else {
                point.snowLevel = null;
            }

            point.weatherCode = hrrr.hourly.weather_code ? hrrr.hourly.weather_code[hrrrIdx] : null;
            point.wet_bulb_temperature_2m = hrrr.hourly.wet_bulb_temperature_2m ? hrrr.hourly.wet_bulb_temperature_2m[hrrrIdx] : null;
            point.specific_humidity_2m = hrrr.hourly.specific_humidity_2m ? hrrr.hourly.specific_humidity_2m[hrrrIdx] : null;
            point.pressure_msl = hrrr.hourly.pressure_msl ? hrrr.hourly.pressure_msl[hrrrIdx] : null;
            point.surface_pressure = hrrr.hourly.surface_pressure ? hrrr.hourly.surface_pressure[hrrrIdx] : null;
            point.soil_temperature_0cm = hrrr.hourly.soil_temperature_0cm ? hrrr.hourly.soil_temperature_0cm[hrrrIdx] : null;
            point.shortwave_radiation = hrrr.hourly.shortwave_radiation ? hrrr.hourly.shortwave_radiation[hrrrIdx] : null;
            point.cape = hrrr.hourly.cape ? hrrr.hourly.cape[hrrrIdx] : null;
            point.lifted_index = hrrr.hourly.lifted_index ? hrrr.hourly.lifted_index[hrrrIdx] : null;
            point.convective_inhibition = hrrr.hourly.convective_inhibition ? hrrr.hourly.convective_inhibition[hrrrIdx] : null;
            point.visibility = hrrr.hourly.visibility ? hrrr.hourly.visibility[hrrrIdx] : null;
            point.total_column_integrated_water_vapour = hrrr.hourly.total_column_integrated_water_vapour ? hrrr.hourly.total_column_integrated_water_vapour[hrrrIdx] : null;
            point.freezing_level_height = hrrr.hourly.freezing_level_height ? hrrr.hourly.freezing_level_height[hrrrIdx] : null;
            point.snowfall_raw = hrrr.hourly.snowfall ? hrrr.hourly.snowfall[hrrrIdx] : null;
            point.snowfallWaterEquivalentMm = hrrr.hourly.snowfall_water_equivalent
                ? hrrr.hourly.snowfall_water_equivalent[hrrrIdx]
                : null;
            point.precipitationType = hrrr.hourly.precipitation_type
                ? hrrr.hourly.precipitation_type[hrrrIdx]
                : null;
            point.uvIndex = hrrr.hourly.uv_index ? hrrr.hourly.uv_index[hrrrIdx] : null;

            point.layers = extractLayers(hrrr, hrrrIdx);
        } else {
            point.model = hrrr ? 'ECMWF' : modelLabel;
            point.precipitation = ecmwf.hourly.precipitation ? ecmwf.hourly.precipitation[i] : 0;
            point.liquidMM = point.precipitation;
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

            let ecmwfFL = ecmwf.hourly.freezing_level_height ? ecmwf.hourly.freezing_level_height[i] : null;
            if (ecmwfFL == null && location && typeof location.elevationM === 'number' && point.temperature != null) {
                ecmwfFL = location.elevationM + (point.temperature * (1000 / 6.5));
            }

            if (ecmwfFL != null) {
                const precip = point.precipitation || 0;
                const RH = point.rh || 70;
                point.snowLevel = Math.round(calcSnowLevel(ecmwfFL, RH, precip));
            } else {
                point.snowLevel = null;
            }
            point.weatherCode = ecmwf.hourly.weather_code ? ecmwf.hourly.weather_code[i] : null;
            point.wet_bulb_temperature_2m = ecmwf.hourly.wet_bulb_temperature_2m ? ecmwf.hourly.wet_bulb_temperature_2m[i] : null;
            point.specific_humidity_2m = ecmwf.hourly.specific_humidity_2m ? ecmwf.hourly.specific_humidity_2m[i] : null;
            point.pressure_msl = ecmwf.hourly.pressure_msl ? ecmwf.hourly.pressure_msl[i] : null;
            point.surface_pressure = ecmwf.hourly.surface_pressure ? ecmwf.hourly.surface_pressure[i] : null;
            point.soil_temperature_0cm = ecmwf.hourly.soil_temperature_0cm ? ecmwf.hourly.soil_temperature_0cm[i] : null;
            point.shortwave_radiation = ecmwf.hourly.shortwave_radiation ? ecmwf.hourly.shortwave_radiation[i] : null;
            point.cape = ecmwf.hourly.cape ? ecmwf.hourly.cape[i] : null;
            point.lifted_index = ecmwf.hourly.lifted_index ? ecmwf.hourly.lifted_index[i] : null;
            point.convective_inhibition = ecmwf.hourly.convective_inhibition ? ecmwf.hourly.convective_inhibition[i] : null;
            point.visibility = ecmwf.hourly.visibility ? ecmwf.hourly.visibility[i] : null;
            point.total_column_integrated_water_vapour = ecmwf.hourly.total_column_integrated_water_vapour ? ecmwf.hourly.total_column_integrated_water_vapour[i] : null;
            point.freezing_level_height = ecmwf.hourly.freezing_level_height ? ecmwf.hourly.freezing_level_height[i] : null;
            point.snowfall_raw = ecmwf.hourly.snowfall ? ecmwf.hourly.snowfall[i] : null;
            point.snowfallWaterEquivalentMm = ecmwf.hourly.snowfall_water_equivalent
                ? ecmwf.hourly.snowfall_water_equivalent[i]
                : null;
            point.precipitationType = ecmwf.hourly.precipitation_type
                ? ecmwf.hourly.precipitation_type[i]
                : null;
            point.uvIndex = ecmwf.hourly.uv_index ? ecmwf.hourly.uv_index[i] : null;

            point.layers = extractLayers(ecmwf, i);
        }

        point.provenance = forecastProvenance(pointSource, pointSource === hrrr ? hrrrIdx : i);
        rawHourly.push(point);
    }

    const elevation = typeof location.elevationM === 'number' ? location.elevationM : 1000;
    const blended = processSnowfallVariables(rawHourly, slrAlgorithm, elevation);

    if (ensembleMembers && ensembleMembers.length > 0) {
        const memberForecasts = ensembleMembers.map(member =>
            blendForecasts(null, member, location, slrAlgorithm, modelMode),
        );
        blended.forEach((hour, hourIndex) => {
            const memberHours = memberForecasts
                .map(forecast => forecast.hourly[hourIndex])
                .filter((memberHour): memberHour is BlendedHour => memberHour !== undefined);
            const memberResults = memberHours
                .map(memberHour => memberHour.snowfallResult)
                .filter((result): result is NonNullable<typeof result> => result !== undefined);
            if (memberResults.length === 0) return;

            const ensembleSnowfall = summarizeEnsembleSnowfall(memberResults, ensembleMembers.length - memberResults.length);
            const meanFrozenSweMm = memberResults.reduce((sum, result) => sum + result.frozenSweMm, 0) / memberResults.length;
            const meanRainMm = memberResults.reduce((sum, result) => sum + result.rainMm, 0) / memberResults.length;
            const meanSnowFraction = memberResults.reduce((sum, result) => sum + result.snowFraction, 0) / memberResults.length;
            const medianResult = [...memberResults].sort((a, b) => a.freshSnowCm - b.freshSnowCm)[Math.floor(memberResults.length / 2)];
            const meanDepthM = memberHours.reduce((sum, memberHour) => sum + (memberHour.snowDepth ?? 0), 0) / memberHours.length;

            hour.snowfall = ensembleSnowfall.meanSnowCm;
            hour.slr = meanFrozenSweMm > 0 ? (ensembleSnowfall.meanSnowCm * 10) / meanFrozenSweMm : null;
            hour.snowDepth = meanDepthM;
            hour.snowfallResult = medianResult;
            hour.snowFraction = meanSnowFraction;
            hour.frozenSweMm = meanFrozenSweMm;
            hour.rainMm = meanRainMm;
            hour.ensembleSnowfall = ensembleSnowfall;
        });
    }

    blended.forEach(p => {
        if (p.slr) {
            p.slrCategory = getSLRCategory(p.slr, p.liquidMM);
        } else if (p.precipitation > 0) {
            p.slrCategory = 'rain';
        } else {
            p.slrCategory = null;
        }
    });

    return {
        hourly: blended,
        daily: {
            time: ecmwf.daily?.time || [],
            sunrise: ecmwf.daily?.sunrise || [],
            sunset: ecmwf.daily?.sunset || []
        },
        lastRunAvailabilityTime: ecmwf.lastRunAvailabilityTime || hrrr?.lastRunAvailabilityTime
    };
}

export function groupData(blendedData: { hourly: BlendedHour[], daily: OpenMeteoDaily, lastRunAvailabilityTime?: number }): DayData[] {
    const days: Record<string, DayData> = {};
    const weatherCodesByDay: Record<string, number[]> = {};
    const { hourly, daily, lastRunAvailabilityTime } = blendedData;

    hourly.forEach(point => {
        // Skip points with no data (end of model lead time)
        if (point.temperature === null && point.snowfall === null) return;

        const dateStr = point.time.split('T')[0];

        // Removed filter for dates before today to allow historical data

        if (!days[dateStr]) {
            let sunriseStr = null;
            let sunsetStr = null;
            if (daily && daily.time) {
                const dIdx = daily.time.indexOf(dateStr);
                if (dIdx !== -1 && daily.sunrise && daily.sunset) {
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
                windows: [],
                snowDepthValues: [],
                snowLayersOnGround: [],
                minTemp: 100,
                maxTemp: -100,
                weatherCode: null,
                lastRunAvailabilityTime: lastRunAvailabilityTime
            };
            weatherCodesByDay[dateStr] = [];
        }

        days[dateStr].hourly.push(point);
        days[dateStr].models.add(point.model);
        if (point.snowfall > 0) days[dateStr].totalSnowfall += point.snowfall;
        if (point.precipitation > 0) days[dateStr].totalPrecipitation += point.precipitation;
        if (point.snowDepth != null) days[dateStr].snowDepthValues.push(point.snowDepth);
        if (point.snowpackStep) days[dateStr].snowLayersOnGround = point.snowpackStep.layers;

        if (point.temperature != null) {
            if (point.temperature < days[dateStr].minTemp) days[dateStr].minTemp = point.temperature;
            if (point.temperature > days[dateStr].maxTemp) days[dateStr].maxTemp = point.temperature;
        }

        if (point.weatherCode !== null) {
            weatherCodesByDay[dateStr].push(point.weatherCode);
        }
    });

    const result = Object.values(days).sort((a, b) => a.dateObj.getTime() - b.dateObj.getTime());

    result.forEach(day => {
        const lastDepth = day.snowDepthValues.length ? day.snowDepthValues[day.snowDepthValues.length - 1] : null;
        day.snowDepth = lastDepth != null ? (lastDepth * 100).toFixed(0) + ' cm' : '--';

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
                snowLevels: [],
                uvIndices: [],
                visibilities: []
            });
        }

        day.hourly.forEach(point => {
            const hour = parseInt(point.time.split('T')[1].split(':')[0], 10);
            const windowIdx = Math.floor(hour / 3);
            const window = day.windows[windowIdx];

            if (point.snowfall > 0) window.snowfall += point.snowfall;
            if (point.precipitation > 0) window.precip += point.precipitation;
            if (point.temperature != null) window.temps.push(point.temperature);
            if (point.windSpeed != null) window.windSpeeds.push(point.windSpeed);
            if (point.windDir != null) window.windDirs.push(point.windDir);
            if (point.slr != null) window.slrs.push(point.slr);
            if (point.precipChance != null) window.precipChances.push(point.precipChance);
            if (point.feelsLike != null) window.feelsLikes.push(point.feelsLike);
            if (point.rh != null) window.rhs.push(point.rh);
            if (point.gusts != null) window.gusts.push(point.gusts);
            if (point.clouds != null) window.clouds.push(point.clouds);
            if (point.snowLevel != null) window.snowLevels.push(point.snowLevel);
            if (point.uvIndex != null) window.uvIndices.push(point.uvIndex);
            if (point.visibility != null) window.visibilities.push(point.visibility);
        });

        let dayMaxSnowfall = 0;
        day.windows.forEach(w => {
            w.avgTemp = w.temps.length ? w.temps.reduce((a, b) => a + b) / w.temps.length : null;
            w.avgWindSpeed = w.windSpeeds.length ? w.windSpeeds.reduce((a, b) => a + b) / w.windSpeeds.length : null;
            w.dominantWindDir = w.windDirs.length ? w.windDirs[Math.floor(w.windDirs.length / 2)] : null;
            w.avgSlr = w.slrs.length ? w.slrs.reduce((a, b) => a + b) / w.slrs.length : null;
            w.slrCategory = getSLRCategory(w.avgSlr, w.precip);
            w.maxPrecipChance = w.precipChances.length ? Math.max(...w.precipChances) : null;
            w.avgFeelsLike = w.feelsLikes.length ? w.feelsLikes.reduce((a, b) => a + b) / w.feelsLikes.length : null;
            w.avgRh = w.rhs.length ? w.rhs.reduce((a, b) => a + b) / w.rhs.length : null;
            w.maxGust = w.gusts.length ? Math.max(...w.gusts) : null;
            w.avgCloud = w.clouds.length ? w.clouds.reduce((a, b) => a + b) / w.clouds.length : null;
            w.avgSnowLevel = w.snowLevels.length ? w.snowLevels.reduce((a, b) => a + b) / w.snowLevels.length : null;
            w.avgUvIndex = w.uvIndices.length ? w.uvIndices.reduce((a, b) => a + b) / w.uvIndices.length : null;
            w.avgVisibility = w.visibilities.length ? w.visibilities.reduce((a, b) => a + b) / w.visibilities.length : null;
            if (w.snowfall > dayMaxSnowfall) dayMaxSnowfall = w.snowfall;
        });

        let maxHourlySnowfall = 0;
        day.hourly.forEach(h => {
            if (h.snowfall > maxHourlySnowfall) maxHourlySnowfall = h.snowfall;
        });
        day.maxHourlySnowfall = maxHourlySnowfall;
        day.maxWindowSnowfall = dayMaxSnowfall;

        // Clean up model names for the UI
        const modelDisplayMap: Record<string, string> = {
            'GFS_HRRR': 'HRRR',
            'NCEP_NAM_CONUS': 'NAM',
            'GEM_HRDPS_WEST': 'GEM HRDPS',
            'GEM_REGIONAL': 'GEM Reg.',
            'NCEP_NBM_CONUS': 'NBM',
            'ECMWF_IFS': 'ECMWF',
            'ECMWF_AIFS025_SINGLE': 'AIFS',
            'ECMWF_AIFS025_ENSEMBLE': 'AIFS Ens',
            'GFS_GLOBAL': 'GFS'
        };
        day.modelString = Array.from(day.models)
            .map(m => modelDisplayMap[m] || m)
            .join('-');

        // Finalize weather code based on severity
        const codes = weatherCodesByDay[day.dateStr];
        if (codes.length > 0) {
            let topCode = codes[0];
            let topWeight = -1;

            codes.forEach(c => {
                const w = SEVERITY_WEIGHTS[c] ?? 0;
                if (w > topWeight) {
                    topWeight = w;
                    topCode = c;
                }
            });
            day.weatherCode = topCode;
        }
    });

    return result;
}

/**
 * Calculates rolling snowfall and SLR for the last 24 and 48 hours relative to a reference time.
 */
export function calculateRollingStats(allHourly: BlendedHour[], nowISO: string): RollingStats {
    const nowTs = new Date(nowISO).getTime();
    const ts24 = nowTs - 24 * 60 * 60 * 1000;
    const ts48 = nowTs - 48 * 60 * 60 * 1000;

    const aggregate = (hours: BlendedHour[]) => {
        const snow = hours.reduce((sum, h) => sum + h.snowfall, 0);
        const frozenSwe = hours.reduce((sum, h) => sum + (h.frozenSweMm ?? 0), 0);
        const slr = frozenSwe > 0 ? (snow * 10) / frozenSwe : null;
        return { snow, slr };
    };

    const h24 = allHourly.filter(h => {
        const t = new Date(h.time).getTime();
        return t <= nowTs && t > ts24;
    });

    const h48 = allHourly.filter(h => {
        const t = new Date(h.time).getTime();
        return t <= nowTs && t > ts48;
    });

    const s24 = aggregate(h24);
    const s48 = aggregate(h48);

    return {
        snow24h: s24.snow,
        slr24h: s24.slr,
        snow48h: s48.snow,
        slr48h: s48.slr
    };
}
