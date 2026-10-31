/**
 * Access layer for the precomputed ENS-EB model forecasts.
 *
 * The model is served ONLY at catalogue resort points (the tracking
 * registry's pilot resorts x base / mid / summit) and ONLY inside the
 * ECMWF and HRRR forecast-source modes -- including the default
 * `hrrr_ecmwf` blend of the two: its features are derived wholly from
 * those two sources, so no other mode can show it. In the literal
 * `ecmwf` / `hrrr` modes the panel shows that source's arm. In blend
 * mode it mirrors the app's own blend (blendForecasts in lib/data.ts,
 * fed by the hrrr_ecmwf branch of fetchWeatherData in lib/api.ts):
 * ECMWF is the spine and HRRR overrides inside its 0-48 h horizon, so
 * per window the blend-privileged arm is HRRR for lead days 1-2 and
 * ECMWF for lead day 3, with fallback to the other arm when the
 * privileged arm has no scored record for that window. Every other
 * location keeps Kuchera only, exactly as before. Data is produced by
 * the offline serving batch
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

/** The app's default forecast-source mode: the HRRR+ECMWF blend. */
export const BLEND_MODEL_ID = 'hrrr_ecmwf';

/**
 * Lead days whose 24 h windows fall inside HRRR's 0-48 h horizon -- the
 * span the app fetches HRRR for in blend mode (fetchWeatherData:
 * "Fetch HRRR (0-48 hours)") and the span the serving batch scores for
 * the HRRR arm.
 */
const BLEND_HRRR_MAX_LEAD_DAY = 2;

/** The single arm a literal source mode maps to, or null. */
export function modelArmForMode(modelId: string): ModelSourceArm | null {
  if (modelId === 'ecmwf') return 'ecmwf';
  if (modelId === 'hrrr') return 'hrrr';
  return null;
}

/**
 * Arm preference order for one window, most preferred first.
 *
 * Literal `ecmwf` / `hrrr` modes: that arm only -- no cross-source
 * fallback, exactly as the panel behaved before blend support.
 *
 * Blend mode (`hrrr_ecmwf`) mirrors blendForecasts: HRRR overrides
 * ECMWF hour-by-hour wherever HRRR data exists inside its 0-48 h
 * horizon, and ECMWF supplies everything beyond it. At the panel's
 * window granularity that is HRRR for lead days 1-2 and ECMWF for
 * lead day 3; the non-privileged arm remains as a fallback for windows
 * the privileged arm has no scored record for (e.g. HRRR day-2 windows
 * beyond the 48 h horizon from the 12Z cycle).
 *
 * Any other mode: no arms -- the panel does not exist there.
 */
export function modelArmPreference(modelId: string, leadDay: number): ModelSourceArm[] {
  const single = modelArmForMode(modelId);
  if (single) return [single];
  if (modelId !== BLEND_MODEL_ID) return [];
  return leadDay <= BLEND_HRRR_MAX_LEAD_DAY ? ['hrrr', 'ecmwf'] : ['ecmwf', 'hrrr'];
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
  /** The arm actually shown for this window (per-window in blend mode). */
  arm: ModelSourceArm;
  /** True when resolved in the hrrr_ecmwf blend mode. */
  blended: boolean;
  cycleUtc: string | null;
  forecast: ModelWindowForecast;
}

/**
 * Resolve the model forecast to display, or null when the panel must not
 * appear: wrong mode, non-catalogue location, or no prediction stored
 * for that window (missing predictions are never carried forward). In
 * blend mode the resolved arm is the one actually shown for the window,
 * so the panel's source label is per-window truthful.
 */
export function modelForecastFor(
  location: Location | undefined,
  modelId: string,
  elevationMode: string,
  windowEndLocalDate: string | null,
): ModelForecastResult | null {
  if (!location || !windowEndLocalDate) return null;
  const blended = modelId === BLEND_MODEL_ID;
  if (!blended && !modelArmForMode(modelId)) return null;
  const point = cataloguePointForLocation(location, elevationMode);
  if (!point) return null;

  const rowFor = (arm: ModelSourceArm): ModelWindowForecast | null =>
    point.forecasts[arm]?.find((row) => row.windowEndLocalDate === windowEndLocalDate) ?? null;

  if (!blended) {
    const arm = modelArmForMode(modelId) as ModelSourceArm;
    const forecast = rowFor(arm);
    if (!forecast || forecast.modelSlr === null) return null;
    return { point, arm, blended: false, cycleUtc: modelForecastData.cycles[arm] ?? null, forecast };
  }

  // Blend mode: find the window in either arm to learn its lead day,
  // then walk the blend's preference order with fallback.
  const rows: Partial<Record<ModelSourceArm, ModelWindowForecast>> = {};
  for (const arm of ['ecmwf', 'hrrr'] as ModelSourceArm[]) {
    const row = rowFor(arm);
    if (row) rows[arm] = row;
  }
  const anyRow = rows.ecmwf ?? rows.hrrr;
  if (!anyRow) return null;
  for (const arm of modelArmPreference(modelId, anyRow.leadDay)) {
    const forecast = rows[arm];
    if (forecast && forecast.modelSlr !== null) {
      return { point, arm, blended: true, cycleUtc: modelForecastData.cycles[arm] ?? null, forecast };
    }
  }
  return null;
}
