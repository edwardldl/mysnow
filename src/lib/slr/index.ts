/**
 * Snowfall & SLR Calculator — Main Entry Point
 *
 * Fetches hourly data from the Open-Meteo ECMWF API for a given
 * latitude/longitude and hour index, then runs both algorithms.
 *
 * Usage:
 *   npx ts-node index.ts
 */

import { FullColumnInputs, PressureLevel, PressureHPa } from "./types";
import { calculateSimpleSLR } from "./simple-algorithm";
import { calculateSophisticatedSLR } from "./sophisticated-algorithm";

const ECMWF_ENDPOINT = "https://api.open-meteo.com/v1/ecmwf";
const PRESSURE_LEVELS: PressureHPa[] = [1000, 925, 850, 700, 600, 500, 400, 300];

function buildApiUrl(lat: number, lon: number): string {
  const surfaceVars = ["temperature_2m","dewpoint_2m","wind_speed_10m","precipitation"];
  const pressureVarTypes = ["temperature","relative_humidity","geopotential_height","vertical_velocity"];
  const pressureVars: string[] = [];
  for (const varType of pressureVarTypes)
    for (const level of PRESSURE_LEVELS)
      pressureVars.push(`${varType}_${level}hPa`);
  pressureVars.push("wind_speed_700hPa");
  const allVars = [...surfaceVars, ...pressureVars].join(",");
  return `${ECMWF_ENDPOINT}?latitude=${lat}&longitude=${lon}&hourly=${allVars}&wind_speed_unit=ms&forecast_days=1`;
}

interface OpenMeteoResponse { hourly: Record<string, number[]>; }

function parseApiResponse(data: OpenMeteoResponse, hourIndex: number): FullColumnInputs {
  const h = data.hourly;
  const i = hourIndex;
  const levels: PressureLevel[] = PRESSURE_LEVELS.map((p) => ({
    pressure: p,
    temp:   h[`temperature_${p}hPa`][i],
    rh:     h[`relative_humidity_${p}hPa`][i],
    height: h[`geopotential_height_${p}hPa`][i],
    omega:  h[`vertical_velocity_${p}hPa`][i],
    wind700: p === 700 ? h[`wind_speed_700hPa`][i] : null,
  }));
  return {
    T2m: h["temperature_2m"][i], Td2m: h["dewpoint_2m"][i],
    precip_mm: h["precipitation"][i], wind_speed_10m: h["wind_speed_10m"][i],
    levels,
  };
}

async function main(): Promise<void> {
  const LATITUDE = 39.74, LONGITUDE = -104.98; // Denver, CO
  console.log("Snowfall & SLR Calculator — Denver, CO\n");
  const url = buildApiUrl(LATITUDE, LONGITUDE);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`API error: ${response.status}`);
  const data: OpenMeteoResponse = await response.json();
  const totalHours = data.hourly["temperature_2m"].length;

  for (let i = 0; i < totalHours; i++) {
    const inputs = parseApiResponse(data, i);
    if (inputs.precip_mm <= 0) continue;

    console.log(`\n─── Hour ${i} ─────────────────────────────────────────`);
    console.log(`  T2m:${inputs.T2m}°C  Td2m:${inputs.Td2m}°C  Precip:${inputs.precip_mm.toFixed(2)}mm  Wind:${inputs.wind_speed_10m.toFixed(1)}m/s`);

    const simple = calculateSimpleSLR(inputs);
    console.log(`  [① Simple]  SLR:${simple.slr.toFixed(1)}:1  Snow:${(simple.snowfall_mm/10).toFixed(1)}cm  Type:${simple.snowType}`);
    if (simple.warning) console.log(`     ⚠  ${simple.warning}`);

    const sophis = calculateSophisticatedSLR(inputs);
    console.log(`  [② Sophisticated]  SLR:${sophis.slr.toFixed(1)}:1  Snow:${(sophis.snowfall_mm/10).toFixed(1)}cm  Type:${sophis.snowType}`);
    for (const s of sophis.steps) console.log(`     ${s.label.padEnd(42)} ${s.value}`);
  }
}

main().catch(console.error);
