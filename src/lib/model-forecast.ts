/**
 * Access layer for the precomputed ENS-EB model forecasts.
 *
 * The model is served ONLY at catalogue resort points (the tracking
 * registry's pilot resorts x base / mid / summit) and ONLY inside the
 * ECMWF and HRRR forecast-source modes: its features are derived wholly
 * from those two sources, so the panel follows the active mode (ECMWF
 * mode -> ECMWF arm, HRRR mode -> HRRR arm) and does not exist in any
 * other mode. Every other location keeps Kuchera only, exactly as
 * before. Data is produced by the offline serving batch
 * (training/slr_calibration/scripts/run_model_serving.py) and imported
 * as static JSON -- this app has no database; the JSON is the serving
 * layer in the workspace.
 */
import rawData from '../data/model-forecasts.json';
import type { Location } from './types';
import { referencePointForMode } from './snow/resort/geometry';

export type ModelSourceArm = 'ecmwf' | 'hrrr';

export interface ModelWindowForecast {
  leadDay: number;
  windowStartUtc: string;
  windowEndUtc: string;
  windowEndLocalDate: string;
  modelSlr: number | null;
  modelSnowfallCm: number | null;
  kucheraSlr: number | null;
  kucheraSnowfallCm: number | null;
  liquidMm: number | null;
}

export interface ModelForecastPoint {
  pointId: string;
  resortSlug: string;
  resortName: string;
  pointName: string;
  role: string;
  latitude: number;
  longitude: number;
  elevationM: number;
  timezone: string;
  forecasts: Partial<Record<ModelSourceArm, ModelWindowForecast[]>>;
}

export interface ModelForecastData {
  generatedAtUtc: string;
  modelVersion: string;
  modelLabel: string;
  featureBuilderVersion: string;
  nanPolicy: string;
  cycles: Partial<Record<ModelSourceArm, string>>;
  points: ModelForecastPoint[];
}

export const modelForecastData = rawData as unknown as ModelForecastData;

/** The design's mode gate: the model exists only in ECMWF / HRRR modes. */
export function modelArmForMode(modelId: string): ModelSourceArm | null {
  if (modelId === 'ecmwf') return 'ecmwf';
  if (modelId === 'hrrr') return 'hrrr';
  return null;
}

const MAX_POINT_DISTANCE_DEG = 0.05;
const MAX_ELEVATION_DELTA_M = 100;

/**
 * The catalogue point matching the location's currently selected
 * reference point (base / mid / summit via elevation mode), or null when
 * the location is not a catalogue resort point.
 */
export function cataloguePointForLocation(
  location: Location,
  elevationMode: string,
): ModelForecastPoint | null {
  const reference = referencePointForMode(location, elevationMode);
  let best: ModelForecastPoint | null = null;
  let bestDistance = Infinity;
  for (const point of modelForecastData.points) {
    if (point.role !== reference.role) continue;
    const distance = Math.hypot(
      point.latitude - reference.latitude,
      point.longitude - reference.longitude,
    );
    if (distance < bestDistance) {
      best = point;
      bestDistance = distance;
    }
  }
  if (!best || bestDistance > MAX_POINT_DISTANCE_DEG) return null;
  if (Math.abs(best.elevationM - reference.elevationM) > MAX_ELEVATION_DELTA_M) return null;
  return best;
}

export interface ModelForecastResult {
  point: ModelForecastPoint;
  arm: ModelSourceArm;
  cycleUtc: string | null;
  forecast: ModelWindowForecast;
}

/**
 * Resolve the model forecast to display, or null when the panel must not
 * appear: wrong mode, non-catalogue location, or no prediction stored
 * for that source / window (missing predictions are never carried
 * forward).
 */
export function modelForecastFor(
  location: Location | undefined,
  modelId: string,
  elevationMode: string,
  windowEndLocalDate: string | null,
): ModelForecastResult | null {
  if (!location || !windowEndLocalDate) return null;
  const arm = modelArmForMode(modelId);
  if (!arm) return null;
  const point = cataloguePointForLocation(location, elevationMode);
  if (!point) return null;
  const rows = point.forecasts[arm];
  if (!rows) return null;
  const forecast = rows.find((row) => row.windowEndLocalDate === windowEndLocalDate);
  if (!forecast || forecast.modelSlr === null) return null;
  return { point, arm, cycleUtc: modelForecastData.cycles[arm] ?? null, forecast };
}
