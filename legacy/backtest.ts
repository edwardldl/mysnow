import { calcSLR } from './src/slr';

// === CONFIGURATION ===
const LAT = 39.193416; // Palisades Tahoe
const LON = -120.245402;
const START_DATE = '2024-02-04'; // A major atmospheric river event
const END_DATE = '2024-02-06';
const TIMEZONE = 'America/Los_Angeles';

const PRESSURE_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300];

const HOURLY_PARAMS = [
    "snowfall",
    "precipitation",
    "temperature_2m",
    "dew_point_2m",
    "wind_speed_10m",
    "relative_humidity_2m",
    "wet_bulb_temperature_2m",
    "pressure_msl",
    PRESSURE_LEVELS.map(l => `temperature_${l}hPa`).join(','),
    PRESSURE_LEVELS.map(l => `relative_humidity_${l}hPa`).join(','),
    PRESSURE_LEVELS.map(l => `geopotential_height_${l}hPa`).join(','),
    PRESSURE_LEVELS.map(l => `vertical_velocity_${l}hPa`).join(','),
    "wind_speed_700hPa"
].join(",");

const API_URL = `https://historical-forecast-api.open-meteo.com/v1/forecast?latitude=${LAT}&longitude=${LON}&start_date=${START_DATE}&end_date=${END_DATE}&hourly=${HOURLY_PARAMS}&timezone=${TIMEZONE}`;

async function runBacktest() {
    console.log(`\n❄️  Starting Snow Algo Backtest...`);
    console.log(`📅 Period: ${START_DATE} to ${END_DATE}`);
    console.log(`📍 Location: ${LAT}, ${LON}`);
    console.log(`🔗 Requesting: ${API_URL}\n`);

    const res = await fetch(API_URL);
    if (!res.ok) {
        console.error(`Failed to fetch: ${res.statusText}`);
        return;
    }

    const data = await res.json();
    const hours = data.hourly.time;
    
    // Aggregators for different algorithms
    const totals = {
        standard: 0,
        simple: 0,
        complex: 0,
        kinematic: 0,
        model_native: 0,
        liquid: 0
    };

    const dailyTotals: Record<string, typeof totals> = {};

    for (let i = 0; i < hours.length; i++) {
        const dateStr = hours[i].split('T')[0];
        if (!dailyTotals[dateStr]) {
            dailyTotals[dateStr] = { standard: 0, simple: 0, complex: 0, kinematic: 0, model_native: 0, liquid: 0 };
        }

        // Map data to the format expected by calcSLR
        const point = {
            time: hours[i],
            temperature_2m: data.hourly.temperature_2m[i],
            dew_point_2m: data.hourly.dew_point_2m[i],
            wind_speed_10m: data.hourly.wind_speed_10m[i],
            precipitation: data.hourly.precipitation[i],
            snowfall: data.hourly.snowfall[i],
            relative_humidity_2m: data.hourly.relative_humidity_2m[i],
            wet_bulb_temperature_2m: data.hourly.wet_bulb_temperature_2m[i],
            layers: PRESSURE_LEVELS.map(level => ({
                pressure: level,
                temp: data.hourly[`temperature_${level}hPa`][i],
                rh: data.hourly[`relative_humidity_${level}hPa`][i],
                gz: data.hourly[`geopotential_height_${level}hPa`][i],
                omega: data.hourly[`vertical_velocity_${level}hPa`][i],
                wind_speed: (data.hourly[`wind_speed_${level}hPa`] || [])[i] || data.hourly.wind_speed_10m[i]
            }))
        };

        const liquid = point.precipitation || 0;
        dailyTotals[dateStr].liquid += liquid;

        // Run each algorithm
        ['standard', 'simple', 'complex', 'kinematic', 'model_native'].forEach(method => {
            const out = calcSLR(point as any, method);
            if (out.isSnow) {
                dailyTotals[dateStr][method] += out.snow_cm;
            }
        });
    }

    // Print Results
    console.log(`---------------------------------------------------------------------------------------------------`);
    console.log(`| Date       | Liquid (mm) | 10:1 (cm) | Simple (cm) | Complex (cm) | Kinematic (cm) | Model (cm) |`);
    console.log(`---------------------------------------------------------------------------------------------------`);
    
    let grandTotal = { standard: 0, simple: 0, complex: 0, kinematic: 0, model_native: 0, liquid: 0 };

    Object.entries(dailyTotals).forEach(([date, t]) => {
        console.log(`| ${date} | ${t.liquid.toFixed(1).padStart(11)} | ${t.standard.toFixed(1).padStart(9)} | ${t.simple.toFixed(1).padStart(11)} | ${t.complex.toFixed(1).padStart(12)} | ${t.kinematic.toFixed(1).padStart(14)} | ${t.model_native.toFixed(1).padStart(10)} |`);
        grandTotal.liquid += t.liquid;
        grandTotal.standard += t.standard;
        grandTotal.simple += t.simple;
        grandTotal.complex += t.complex;
        grandTotal.kinematic += t.kinematic;
        grandTotal.model_native += t.model_native;
    });

    console.log(`---------------------------------------------------------------------------------------------------`);
    console.log(`| TOTAL      | ${grandTotal.liquid.toFixed(1).padStart(11)} | ${grandTotal.standard.toFixed(1).padStart(9)} | ${grandTotal.simple.toFixed(1).padStart(11)} | ${grandTotal.complex.toFixed(1).padStart(12)} | ${grandTotal.kinematic.toFixed(1).padStart(14)} | ${grandTotal.model_native.toFixed(1).padStart(10)} |`);
    console.log(`---------------------------------------------------------------------------------------------------\n`);
}

runBacktest().catch(console.error);
