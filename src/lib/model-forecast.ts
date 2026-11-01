/**
 * Access layer for the precomputed ENS-EB model forecasts.
 *
 * The model is served ONLY at catalogue resort points (the tracking
 * registry's pilot resorts x base / mid / summit) and ONLY inside the
 * ECMWF and HRRR forecast-source modes -- including the default
 * `hrrr_ecmwf` blend of the two: its features are derived wholly from
 * those sources, so no other mode can show it. In the literal
 * `ecmwf` / `hrrr` modes the panel shows that source's arm. In blend
 * mode it mirrors the app's own blend (blendForecasts in lib/data.ts,
 * fed by the hrrr_ecmwf branch of fetchWeatherData in lib/api.ts):
 * ECMWF is the spine and HRRR overrides inside its 0-48 h horizon, so
 * per window the blend-privileged arm is HRRR for lead days 1-2 and
 * ECMWF for lead day 3 and beyond (the ECMWF arm is precomputed out to
 * the step-240 open-data ceiling -- lead day 9 or 10 depending on the
 * point's timezone), with fallback to the other arm when the
 * privileged arm has no scored record for that window.
 *
 * Beyond the GRIB ceiling, a THIRD arm serves the tail windows (lead
 * days ceiling+1 through 14): `ecmwf_openmeteo` -- ECMWF IFS via
 * Open-Meteo, added 2026-11-01 under the third-source treatment from
 * the E1 parity report. It is NOT the GRIB ECMWF arm continued:
 * Open-Meteo publishes no cycle metadata and its forecast instance
 * could not be cycle-verified against the GRIB arm, and it is scored
 * as a reduced-feature variant (total column liquid water is
 * unavailable from Open-Meteo, so the tclw feature pair is NaN under
 * the validated pass-through). It resolves only where the GRIB ECMWF
 * arm has no record, in the literal `ecmwf` mode and in the blend --
 * never in literal `hrrr` mode -- and is always labelled distinctly.
 * Windows beyond lead day 14 have no model record from any arm.
 * Every other location keeps Kuchera only, exactly as before. Data is
 * produced by the offline serving batches
 * (training/slr_calibration/scripts/run_model_serving.py and
 * run_model_serving_openmeteo.py) and imported as static JSON -- this
 * app has no database; the JSON is the serving layer in the workspace.
 */
import rawData from '../data/model-forecasts.json';
import type { DayData, Location } from './types';
import { referencePointForMode } from './snow/resort/geometry';

export type ModelSourceArm = 'ecmwf' | 'hrrr' | 'ecmwf_openmeteo';

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
  /**
   * True on Open-Meteo arm rows: the window was scored as a
   * reduced-feature variant (tclw pair NaN -- total column liquid
   * water is not available from Open-Meteo).
   */
  reducedFeatureVariant?: boolean;
}

/** Provenance metadata for the Open-Meteo third arm (see file header). */
export interface OpenMeteoArmMetadata {
  sourceId: string;
  displayName: string;
  /** The GRIB serving cycle the arm's windows are anchored on. */
  anchorCycleUtc: string;
  /** When the Open-Meteo data was retrieved (it publishes no cycle). */
  retrievedAtUtc: string;
  reducedFeatureVariant: boolean;
  note: string;
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
  /** Present when the serving JSON carries the Open-Meteo third arm. */
  openMeteoArm?: OpenMeteoArmMetadata;
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
 * Literal `hrrr` mode: that arm only -- no cross-source fallback.
 * Literal `ecmwf` mode: the GRIB ECMWF arm, then the Open-Meteo arm --
 * the tail extension of the same ECMWF family (it holds only windows
 * beyond the GRIB ceiling, so the two never compete for a window);
 * never HRRR.
 *
 * Blend mode (`hrrr_ecmwf`) mirrors blendForecasts: HRRR overrides
 * ECMWF hour-by-hour wherever HRRR data exists inside its 0-48 h
 * horizon, and ECMWF supplies everything beyond it. At the panel's
 * window granularity that is HRRR for lead days 1-2 and ECMWF for
 * lead day 3 and beyond; the non-privileged arms remain as fallbacks
 * for windows the privileged arm has no scored record for (e.g. HRRR
 * day-2 windows beyond the 48 h horizon from the 12Z cycle), with the
 * Open-Meteo arm last inside the horizon and ahead of HRRR beyond it
 * (HRRR holds no records there anyway).
 *
 * Any other mode: no arms -- the panel does not exist there.
 */
export function modelArmPreference(modelId: string, leadDay: number): ModelSourceArm[] {
  const single = modelArmForMode(modelId);
  if (single === 'ecmwf') return ['ecmwf', 'ecmwf_openmeteo'];
  if (single === 'hrrr') return ['hrrr'];
  if (modelId !== BLEND_MODEL_ID) return [];
  return leadDay <= BLEND_HRRR_MAX_LEAD_DAY
    ? ['hrrr', 'ecmwf', 'ecmwf_openmeteo']
    : ['ecmwf', 'ecmwf_openmeteo', 'hrrr'];
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
  /**
   * Open-Meteo arm only: when its data was retrieved. Open-Meteo
   * publishes no cycle, so for that arm cycleUtc is the anchor cycle
   * the windows are defined on, and this is the retrieval time.
   */
  retrievedAtUtc?: string | null;
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

  // Find the window in any arm to learn its lead day, then walk the
  // mode's preference order (modelArmPreference). The preference order
  // is what confines each mode to its own arms: literal HRRR can never
  // resolve the ECMWF or Open-Meteo rows gathered here, and literal
  // ECMWF can never resolve HRRR.
  const rows: Partial<Record<ModelSourceArm, ModelWindowForecast>> = {};
  for (const arm of ['ecmwf', 'hrrr', 'ecmwf_openmeteo'] as ModelSourceArm[]) {
    const row = rowFor(arm);
    if (row) rows[arm] = row;
  }
  const anyRow = rows.ecmwf ?? rows.ecmwf_openmeteo ?? rows.hrrr;
  if (!anyRow) return null;
  for (const arm of modelArmPreference(modelId, anyRow.leadDay)) {
    const forecast = rows[arm];
    if (forecast && forecast.modelSlr !== null) {
      return {
        point,
        arm,
        blended,
        cycleUtc: modelForecastData.cycles[arm] ?? null,
        retrievedAtUtc:
          arm === 'ecmwf_openmeteo' ? (modelForecastData.openMeteoArm?.retrievedAtUtc ?? null) : null,
        forecast,
      };
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// ENS-EB as a selectable snow-physics method (the "Physics" menu)
// ---------------------------------------------------------------------------

/**
 * Option id of the ENS-EB model in the snow-physics (ALGORITHMS) menu.
 * NOTE: src/lib/snow/slr/index.ts maps this same literal to the Kuchera
 * substrate in toSlrMethod -- keep the two in sync (a test pins both).
 */
export const ENS_EB_METHOD_ID = 'ens_eb_model';

/** Why the ENS-EB menu option is unavailable in the current context. */
export type ModelMethodUnavailableReason = 'unsupported-mode' | 'non-catalogue-location';

export interface ModelMethodAvailability {
  available: boolean;
  reason: ModelMethodUnavailableReason | null;
  /** The matched catalogue point when the location is a catalogue point. */
  point: ModelForecastPoint | null;
}

/**
 * The model option is available only where the model can actually serve:
 * a forecast-source mode with a serving arm (literal ECMWF, literal HRRR,
 * or the hrrr_ecmwf blend) AND a catalogue resort point for the location.
 * Per-window record coverage is NOT part of availability -- a missing
 * window is a marked fallback, not an unavailable option.
 */
export function modelMethodAvailability(
  modelId: string,
  location: Location | null | undefined,
  elevationMode: string,
): ModelMethodAvailability {
  if (modelArmForMode(modelId) === null && modelId !== BLEND_MODEL_ID) {
    return { available: false, reason: 'unsupported-mode', point: null };
  }
  const point = location ? cataloguePointForLocation(location, elevationMode) : null;
  if (point === null) {
    return { available: false, reason: 'non-catalogue-location', point: null };
  }
  return { available: true, reason: null, point };
}

export interface ModelWindowStatus {
  windowEndLocalDate: string;
  leadDay: number;
  /** 'model' when a serving record resolved for this window, else the Kuchera fallback applies. */
  status: 'model' | 'fallback';
  /** Serving arm when status is 'model'. */
  arm: ModelSourceArm | null;
  result: ModelForecastResult | null;
}

export interface ModelWindowStatuses {
  point: ModelForecastPoint;
  windows: ModelWindowStatus[];
}

/**
 * Per-window model/fallback status for every window stored for the matched
 * catalogue point, resolved through modelForecastFor so the menu-driven
 * display and the panel can never disagree. Returns null when the model
 * method is unavailable in this context (unsupported mode / non-catalogue).
 */
export function modelWindowStatuses(
  location: Location | null | undefined,
  modelId: string,
  elevationMode: string,
): ModelWindowStatuses | null {
  const availability = modelMethodAvailability(modelId, location, elevationMode);
  if (!availability.available || availability.point === null || !location) return null;
  const point = availability.point;
  const byDate = new Map<string, ModelWindowForecast>();
  for (const arm of ['ecmwf', 'hrrr', 'ecmwf_openmeteo'] as const) {
    for (const row of point.forecasts[arm] ?? []) {
      const existing = byDate.get(row.windowEndLocalDate);
      if (!existing || row.leadDay < existing.leadDay) byDate.set(row.windowEndLocalDate, row);
    }
  }
  const windows = [...byDate.values()]
    .sort((a, b) => a.leadDay - b.leadDay)
    .map((row) => {
      const result = modelForecastFor(location, modelId, elevationMode, row.windowEndLocalDate);
      return {
        windowEndLocalDate: row.windowEndLocalDate,
        leadDay: row.leadDay,
        status: result !== null ? ('model' as const) : ('fallback' as const),
        arm: result?.arm ?? null,
        result,
      };
    });
  return { point, windows };
}

/**
 * Apply the ENS-EB menu method to grouped forecast days: for each calendar
 * day, the 24 h model window ending 07:00 local on that date -- when a
 * serving record exists -- replaces the day total with the record's model
 * snowfall, the same value the panel shows. The record snowfall is used,
 * not model SLR x UI liquid: the model SLR was predicted conditioned on the
 * serving window's own liquid (fcst_liquid_mm is an input feature) over the
 * exact window, while the UI day liquid covers a different period from a
 * different, live cycle -- recombining them would break the prediction's
 * conditioning and make the menu display disagree with the panel. Days
 * without a record keep their Kuchera totals and are marked as fallback.
 * Returns the days unchanged when the method is unavailable in context.
 * Hourly data is never altered: the model serves window aggregates only.
 */
export function applyModelMethodToDays(
  days: DayData[],
  location: Location | null | undefined,
  modelId: string,
  elevationMode: string,
): DayData[] {
  if (!location) return days;
  const availability = modelMethodAvailability(modelId, location, elevationMode);
  if (!availability.available) return days;
  return days.map((day) => {
    const result = modelForecastFor(location, modelId, elevationMode, day.dateStr);
    if (result !== null && result.forecast.modelSnowfallCm !== null) {
      return {
        ...day,
        totalSnowfall: result.forecast.modelSnowfallCm,
        modelWindow: {
          kind: 'model' as const,
          slr: result.forecast.modelSlr,
          arm: result.arm,
          blended: result.blended,
          cycleUtc: result.cycleUtc,
        },
      };
    }
    return {
      ...day,
      modelWindow: {
        kind: 'kuchera_fallback' as const,
        slr: null,
        arm: null,
        blended: modelId === BLEND_MODEL_ID,
        cycleUtc: null,
      },
    };
  });
}
