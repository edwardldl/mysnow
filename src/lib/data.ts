import { calcSLR, OpenMeteoHour } from './slr';
import { OpenMeteoResponse, Location, BlendedHour, DayData } from './types';

// Snow weather codes from Open-Meteo WMO codes
const SNOW_CODES = new Set([71, 73, 75, 77, 85, 86]);

function isSnowWeatherCode(code: number | null): boolean {
    return code !== null && SNOW_CODES.has(code);
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
 * Applies the selected SLR algorithm to the hourly unified points
 */
function processSnowfallVariables(hourly: Partial<BlendedHour>[], algorithm: string, elevation: number): BlendedHour[] {
    const out: BlendedHour[] = [];
    let prevSlr: number | null = null;
    
    // Track snow layers on ground
    const snowLayersOnGround: Array<{ SWE_mm: number; density: number; ageInHours: number }> = [];
    const RHO_WATER = 1000; // kg/m^3

    for (let i = 0; i < hourly.length; i++) {
        const point = hourly[i];

        // Format for slr.ts
        const meteoHour: OpenMeteoHour = {
            time: point.time || '',
            temperature_2m: point.temperature ?? 0,
            dew_point_2m: point.dewPoint ?? null,
            wind_speed_10m: point.windSpeed ?? null,
            wind_gusts_10m: point.gusts ?? null,
            precipitation: point.liquidMM ?? 0,
            snowfall: point.snowfall_raw ?? null,
            relative_humidity_2m: point.rh ?? null,
            wet_bulb_temperature_2m: point.wet_bulb_temperature_2m ?? null,
            specific_humidity_2m: point.specific_humidity_2m ?? null,
            pressure_msl: point.pressure_msl ?? null,
            surface_pressure: point.surface_pressure ?? null,
            soil_temperature_0cm: point.soil_temperature_0cm ?? null,
            shortwave_radiation: point.shortwave_radiation ?? null,
            cape: point.cape ?? null,
            lifted_index: point.lifted_index ?? null,
            convective_inhibition: point.convective_inhibition ?? null,
            visibility: point.visibility ?? null,
            boundary_layer_height: point.boundary_layer_height ?? null,
            total_column_integrated_water_vapour: point.total_column_integrated_water_vapour ?? null,
            snow_depth: point.snowDepth ?? null,
            freezing_level_height: point.freezing_level_height ?? null,
            elevation: elevation,
            weather_code: point.weatherCode ?? 0,
            layers: point.layers || []
        };

        const result = calcSLR(meteoHour, algorithm, prevSlr);
        
        let slr = null;
        let snow = 0;

        if (result.isSnow && isSnowWeatherCode(point.weatherCode ?? null)) {
            slr = result.slr;
            snow = result.snow_cm;
            prevSlr = slr;
        }

        // Densification Model - Compaction of existing layers
        let totalSWEAbove = 0;
        for (let j = snowLayersOnGround.length - 1; j >= 0; j--) {
            const layer = snowLayersOnGround[j];
            layer.ageInHours += 1;
            
            // Metamorphism and Overburden constants
            const C1 = 0.005; // Settling rate
            const C2 = 0.01; // Compaction per kg of SWE
            const tempFactor = Math.max(0.1, 1.0 - Math.abs(point.temperature || 0) / 10);
            
            const dRho = layer.density * (C1 * tempFactor + C2 * (totalSWEAbove / 1000));
            // Layer naturally caps at ~600 kg/m^3 (firn density)
            layer.density = Math.min(layer.density + dRho, 600);
            
            totalSWEAbove += layer.SWE_mm;
        }

        const qpf_corrected = result.qpf_corrected !== undefined ? result.qpf_corrected : (point.precipitation || 0);

        // Create new layer if accumulating
        if (result.isSnow && qpf_corrected > 0 && result.slr) {
            const rho_init = RHO_WATER / result.slr;
            snowLayersOnGround.push({
                SWE_mm: qpf_corrected,
                density: rho_init,
                ageInHours: 0
            });
        }

        // Surface melt if above freezing
        if ((point.temperature || 0) > 1.0 && snowLayersOnGround.length > 0) {
            let meltAmountMM = ((point.temperature || 0) - 1.0) * 1.5;
            while(meltAmountMM > 0 && snowLayersOnGround.length > 0) {
                const topLayer = snowLayersOnGround[snowLayersOnGround.length - 1];
                if (topLayer.SWE_mm > meltAmountMM) {
                     topLayer.SWE_mm -= meltAmountMM;
                     meltAmountMM = 0;
                } else {
                     meltAmountMM -= topLayer.SWE_mm;
                     snowLayersOnGround.pop();
                }
            }
        }

        const hs_cm = snowLayersOnGround.reduce((sum, l) => sum + (l.SWE_mm / 10) * (RHO_WATER / l.density), 0);
        // snowDepth is stored in metres (UI multiplies by 100 to display as cm)
        point.snowDepth = hs_cm / 100;

        out.push({
            ...point,
            slr: slr,
            snowfall: snow,
            method: result.method,
            slrCategory: null,
            snowDepth: hs_cm / 100 // storing as meters internally since UI multiplies by 100
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
  slrAlgorithm = 'kinematic', 
  modelMode = 'best_match'
): { hourly: BlendedHour[], daily: OpenMeteoDaily } {
    const rawHourly: Partial<BlendedHour>[] = [];
    const ecmwfTimes = ecmwf.hourly.time;
    const hrrrTimes = hrrr ? hrrr.hourly.time : [];

    const LEVELS = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500, 400, 300];

    const extractLayers = (dataSource: OpenMeteoResponse | null, index: number) => {
        if (!dataSource || !dataSource.hourly) return [];
        return LEVELS.map(level => {
            return {
                pressure: level,
                temp: dataSource.hourly[`temperature_${level}hPa`] ? dataSource.hourly[`temperature_${level}hPa`][index] : 0,
                rh: dataSource.hourly[`relative_humidity_${level}hPa`] ? dataSource.hourly[`relative_humidity_${level}hPa`][index] : 0,
                gz: dataSource.hourly[`geopotential_height_${level}hPa`] ? dataSource.hourly[`geopotential_height_${level}hPa`][index] : 0,
                omega: dataSource.hourly[`vertical_velocity_${level}hPa`] ? dataSource.hourly[`vertical_velocity_${level}hPa`][index] : 0,
                wind_speed: (dataSource.hourly[`wind_speed_${level}hPa`] || dataSource.hourly.wind_speed_10m || [])[index] || 0,
                cloud_cover: dataSource.hourly[`cloud_cover_${level}hPa`] ? (dataSource.hourly[`cloud_cover_${level}hPa`] as number[])[index] : 0
            };
        });
    };

    const modelLabel = modelMode === 'best_match' ? 'BEST' : modelMode.toUpperCase();

    for (let i = 0; i < ecmwfTimes.length; i++) {
        const time = ecmwfTimes[i];
        const dateObj = new Date(time);
        const hrrrIdx = hrrrTimes.indexOf(time);

        const point: Partial<BlendedHour> = { time, dateObj };

        if (hrrr && hrrrIdx !== -1) {
            point.model = 'HRRR';
            point.precipitation = hrrr.hourly.precipitation ? hrrr.hourly.precipitation[hrrrIdx] : 0;
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
            
            point.layers = extractLayers(hrrr, hrrrIdx);
        } else {
            point.model = hrrr ? 'ECMWF' : modelLabel;
            point.precipitation = ecmwf.hourly.precipitation ? ecmwf.hourly.precipitation[i] : 0;
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

            point.layers = extractLayers(ecmwf, i);
        }

        rawHourly.push(point);
    }

    const elevation = typeof location.elevationM === 'number' ? location.elevationM : 1000;
    const blended = processSnowfallVariables(rawHourly, slrAlgorithm, elevation);

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
        }
    };
}

export function groupData(blendedData: { hourly: BlendedHour[], daily: OpenMeteoDaily }): DayData[] {
    const days: Record<string, DayData> = {};
    const { hourly, daily } = blendedData;

    // Get current local date in PST (America/Los_Angeles) to filter out past days.
    const todayStr = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Los_Angeles' });

    hourly.forEach(point => {
        // Skip points with no data (end of model lead time)
        if (point.temperature === null && point.snowfall === null) return;

        const dateStr = point.time.split('T')[0];
        
        // Filter out dates before today
        if (dateStr < todayStr) return;

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
                windows: [],
                snowDepthValues: [],
                snowLayersOnGround: []
            };
        }

        days[dateStr].hourly.push(point);
        days[dateStr].models.add(point.model);
        if (point.snowfall > 0) days[dateStr].totalSnowfall += point.snowfall;
        if (point.precipitation > 0) days[dateStr].totalPrecipitation += point.precipitation;
        if (point.snowDepth != null) days[dateStr].snowDepthValues.push(point.snowDepth);
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
                snowLevels: []
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
            'GFS_GLOBAL': 'GFS'
        };
        day.modelString = Array.from(day.models)
            .map(m => modelDisplayMap[m] || m)
            .join('-');
    });

    return result;
}
