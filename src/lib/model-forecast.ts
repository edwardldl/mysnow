/**
 * Access layer for the precomputed ENS-EB model forecasts.
 *
 * The model is served ONLY at catalogue resort points (the tracking
 * registry's pilot resorts x base / mid / summit) and ONLY inside the
 * ECMWF and HRRR forecast-source modes -- including the default
 * `hrrr_ecmwf` blend of the two: its features are derived wholly from
 * those sources, so no other mode can show it. In the literal
 * `ecmwf` / `hrrr` modes the resolved arm is that source's. In blend
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
import type { BlendedHour, DayData, DayModelWindowMark, Location } from './types';
import { referencePointForMode } from './snow/resort/geometry';
import { addCalendarDays } from './time';

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

/**
 * Internal lookups read through this binding so tests can inject a
 * frozen serving batch (model-forecast-frozen.test.ts). Production code
 * never calls the setter; Vitest cannot intercept the JSON import in
 * this setup, hence the seam.
 */
let activeModelForecastData: ModelForecastData = modelForecastData;

export function setModelForecastDataForTests(data: ModelForecastData | null): void {
  activeModelForecastData = data ?? modelForecastData;
}

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
 * horizon, and ECMWF supplies everything beyond it. At
 * window granularity that is HRRR for lead days 1-2 and ECMWF for
 * lead day 3 and beyond; the non-privileged arms remain as fallbacks
 * for windows the privileged arm has no scored record for (e.g. HRRR
 * day-2 windows beyond the 48 h horizon from the 12Z cycle), with the
 * Open-Meteo arm last inside the horizon and ahead of HRRR beyond it
 * (HRRR holds no records there anyway).
 *
 * Any other mode: no arms -- the model does not resolve there.
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
  for (const point of activeModelForecastData.points) {
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
 * Resolve the model forecast for a window, or null when the model does
 * not serve it: wrong mode, non-catalogue location, or no prediction
 * stored for that window (missing predictions are never carried
 * forward). In blend mode the resolved arm is the one actually used
 * for the window, so the per-window source label is truthful.
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
        cycleUtc: activeModelForecastData.cycles[arm] ?? null,
        retrievedAtUtc:
          arm === 'ecmwf_openmeteo' ? (activeModelForecastData.openMeteoArm?.retrievedAtUtc ?? null) : null,
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

const MODEL_WINDOW_END_HOUR_LOCAL = 7;

const windowPartsFormatters = new Map<string, Intl.DateTimeFormat>();

function localPartsAt(
  epochMs: number,
  timeZone: string,
): { date: string; hour: number; minute: number; second: number } | null {
  let formatter = windowPartsFormatters.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-US-u-ca-iso8601', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    });
    windowPartsFormatters.set(timeZone, formatter);
  }
  const parts: Record<string, string> = {};
  for (const part of formatter.formatToParts(new Date(epochMs))) {
    parts[part.type] = part.value;
  }
  if (!parts.year || !parts.month || !parts.day || parts.hour === undefined) return null;
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    hour: Number(parts.hour),
    minute: Number(parts.minute ?? 0),
    second: Number(parts.second ?? 0),
  };
}

/**
 * The model window an hourly entry belongs to, identified by the window's
 * end date (the local date of its 07:00 end), or null when the entry cannot
 * be placed. UI hourly snowfall values are amounts for the hour ENDING at
 * the entry's timestamp -- the preceding-hour convention of the provider
 * feed, and the same convention the serving batch's accumulation
 * differencing produces -- so the window ending 07:00 local on date D
 * comprises the labelled hours D-1 08:00 ... D 07:00. The assignment uses
 * the entry's absolute instant evaluated one second before its end, so a
 * 07:00 label lands inside its own window and the duplicated fall-back
 * 01:00 labels (distinct instants, same label) both land in the window
 * that contains them. When the instant is unavailable, the provider-local
 * label decides instead.
 */
export function modelWindowEndDateForHour(hour: BlendedHour, timeZone: string): string | null {
  if (Number.isFinite(hour.instantMs)) {
    const parts = localPartsAt(hour.instantMs - 1000, timeZone);
    if (parts) {
      const pastEnd =
        parts.hour > MODEL_WINDOW_END_HOUR_LOCAL ||
        (parts.hour === MODEL_WINDOW_END_HOUR_LOCAL && (parts.minute > 0 || parts.second > 0));
      return pastEnd ? addCalendarDays(parts.date, 1) : parts.date;
    }
  }
  const label = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/.exec(hour.time ?? '');
  if (!label) return null;
  const hh = Number(label[2]);
  const mm = Number(label[3]);
  const pastEnd = hh > MODEL_WINDOW_END_HOUR_LOCAL || (hh === MODEL_WINDOW_END_HOUR_LOCAL && mm > 0);
  return pastEnd ? addCalendarDays(label[1], 1) : label[1];
}

interface ModelHourSlot {
  dayIndex: number;
  hourIndex: number;
  hour: BlendedHour;
}

function rebuildSnowfallAggregates(day: DayData, hourly: BlendedHour[]): Partial<DayData> {
  const out: Partial<DayData> = {};
  if (day.maxHourlySnowfall !== undefined) {
    out.maxHourlySnowfall = hourly.reduce((max, h) => Math.max(max, h.snowfall), 0);
  }
  if (Array.isArray(day.windows) && day.windows.length > 0) {
    const windows = day.windows.map((window, index) => {
      const bucket = hourly.filter(
        (h) => Math.floor(Number(h.time.slice(11, 13)) / 3) === index,
      );
      if (bucket.length === 0) return window;
      const snowfall = bucket.reduce((sum, h) => sum + (h.snowfall > 0 ? h.snowfall : 0), 0);
      const liquid = bucket.reduce((sum, h) => sum + (h.liquidMM ?? 0), 0);
      return {
        ...window,
        snowfall,
        avgSlr: snowfall > 0 && liquid > 0 ? (snowfall * 10) / liquid : window.avgSlr,
      };
    });
    out.windows = windows;
    if (day.maxWindowSnowfall !== undefined) {
      out.maxWindowSnowfall = windows.reduce((max, w) => Math.max(max, w.snowfall), 0);
    }
  }
  return out;
}

/**
 * Rescale the hourly snowfall inside each model window so the window's
 * hours sum EXACTLY to the serving record's snowfall, so every consumer
 * of the hourly series (the hourly chart, the rolling 24/48 h statistics)
 * sees the model's own window accounting. Hours live on the real
 * timeline: an hour belongs to exactly one window, so a calendar day's
 * 00:00-24:00 display straddles two windows and each part is scaled by its
 * own window's factor; the "24 h window ending 07:00 local" caption covers
 * that accounting. Windows without a record are not touched.
 *
 * Rule per window, with R = record snowfall and S = sum of the UI hourly
 * snowfall over the window's hours:
 * - R = 0: every hour in the window becomes 0.
 * - S > 0: every hour is scaled by R / S (and its per-hour SLR by the same
 *   factor, keeping the chart's SLR colouring coherent); the floating
 *   point residue is absorbed into the largest scaled hour so the sum is
 *   exact.
 * - S = 0 with R > 0 (zero denominator): R is distributed in proportion
 *   to the UI's hourly liquid amounts (liquidMM, falling back to the raw
 *   precipitation field); if those are also all zero, R is split evenly
 *   across the window's displayed hours. (The record carries no hourly
 *   shape of its own, so that branch of the distribution rule cannot
 *   apply.) Per-hour SLR is left unchanged in the distribution cases --
 *   there is no UI snowfall shape to scale it with.
 * Only `snowfall` (and, in the scaling case, `slr`) change: the UI's
 * liquid, frozen SWE and precipitation are the live feed's own values,
 * the snowpack/depth outputs are the layer model's sequential state and
 * cannot be re-run post hoc, and `snowfallResult` remains the substrate
 * calculation's audit trail. Input days and hours are never mutated.
 */
function rescaleModelWindowHours(days: DayData[], point: ModelForecastPoint): DayData[] {
  const recordByWindowEnd = new Map<string, number>();
  for (const day of days) {
    if (day.modelWindow?.kind === 'model') recordByWindowEnd.set(day.dateStr, day.totalSnowfall);
  }
  if (recordByWindowEnd.size === 0) return days;

  const slotsByWindow = new Map<string, ModelHourSlot[]>();
  days.forEach((day, dayIndex) => {
    (day.hourly ?? []).forEach((hour, hourIndex) => {
      const end = modelWindowEndDateForHour(hour, point.timezone);
      if (end === null || !recordByWindowEnd.has(end)) return;
      const slots = slotsByWindow.get(end) ?? [];
      slots.push({ dayIndex, hourIndex, hour });
      slotsByWindow.set(end, slots);
    });
  });
  if (slotsByWindow.size === 0) return days;

  const key = (slot: ModelHourSlot) => `${slot.dayIndex}:${slot.hourIndex}`;
  const newValues = new Map<string, { snowfall: number; slr: number | null }>();

  for (const [end, slots] of slotsByWindow) {
    const record = recordByWindowEnd.get(end) as number;
    if (slots.length === 0) continue;
    if (record === 0) {
      for (const slot of slots) {
        newValues.set(key(slot), { snowfall: 0, slr: slot.hour.slr });
      }
      continue;
    }
    const uiTotal = slots.reduce(
      (sum, s) => sum + (s.hour.snowfall > 0 ? s.hour.snowfall : 0),
      0,
    );
    if (uiTotal > 0) {
      const factor = record / uiTotal;
      let scaledTotal = 0;
      let largest: ModelHourSlot | null = null;
      let largestValue = -1;
      for (const slot of slots) {
        const base = slot.hour.snowfall > 0 ? slot.hour.snowfall : 0;
        const value = base * factor;
        scaledTotal += value;
        if (value > largestValue) {
          largestValue = value;
          largest = slot;
        }
        newValues.set(key(slot), {
          snowfall: value,
          slr: slot.hour.slr !== null && slot.hour.slr !== undefined ? slot.hour.slr * factor : slot.hour.slr,
        });
      }
      if (largest) {
        const current = newValues.get(key(largest));
        if (current) current.snowfall += record - scaledTotal;
      }
      continue;
    }
    const weights = slots.map((s) => {
      const w = Number.isFinite(s.hour.liquidMM) ? s.hour.liquidMM : s.hour.precipitation;
      return w > 0 ? w : 0;
    });
    const weightTotal = weights.reduce((a, b) => a + b, 0);
    let assigned = 0;
    let largestIndex = 0;
    let largestShare = -1;
    slots.forEach((slot, i) => {
      const value = weightTotal > 0 ? (record * weights[i]) / weightTotal : record / slots.length;
      assigned += value;
      if (value > largestShare) {
        largestShare = value;
        largestIndex = i;
      }
      newValues.set(key(slot), { snowfall: value, slr: slot.hour.slr });
    });
    const anchor = newValues.get(key(slots[largestIndex]));
    if (anchor) anchor.snowfall += record - assigned;
  }

  const changedDays = new Set<number>();
  for (const k of newValues.keys()) changedDays.add(Number(k.split(':')[0]));
  if (changedDays.size === 0) return days;
  return days.map((day, dayIndex) => {
    if (!changedDays.has(dayIndex)) return day;
    const hourly = (day.hourly ?? []).map((hour, hourIndex) => {
      const value = newValues.get(`${dayIndex}:${hourIndex}`);
      return value ? { ...hour, snowfall: value.snowfall, slr: value.slr } : hour;
    });
    return { ...day, hourly, ...rebuildSnowfallAggregates(day, hourly) };
  });
}

/**
 * Apply the ENS-EB menu method to grouped forecast days. Each calendar
 * day is marked by the 24 h model window ending 07:00 local on that date:
 * when a serving record exists the day is a model day, otherwise it is a
 * Kuchera fallback day. The record snowfall is used as the window total,
 * not model SLR x UI liquid: the model SLR was predicted conditioned on
 * the serving window's own liquid (fcst_liquid_mm is an input feature)
 * over the exact window, while the UI day liquid covers a different
 * period from a different, live cycle -- recombining them would break
 * the prediction's conditioning.
 * The hourly snowfall series is rescaled per model window (see
 * rescaleModelWindowHours); the model still serves window aggregates
 * only -- the rescale distributes each record total over the UI's own
 * hourly shape, it does not invent a model hourly forecast.
 *
 * The DISPLAYED day total then follows the calendar day, derived exactly
 * as groupData derives it for every other method: the sum of the day's
 * own hourly snowfall (positive part) over the final, rescaled series.
 * A model window straddles two calendar days, so a model day's morning
 * hours carry the record of the window ending that morning while its
 * afternoon hours carry the next window's record -- the record value
 * itself is NOT the calendar-day sum, and showing it as the day total
 * made the headline number disagree with the day's hourly bars. Days
 * carrying no hourly series keep the record total set by the marking
 * step (there is no series to derive a total from).
 * Returns the days unchanged when the method is unavailable in context.
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
  const marked = days.map((day) => {
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
          reducedFeatureVariant: result.forecast.reducedFeatureVariant ?? false,
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
        reducedFeatureVariant: false,
      },
    };
  });
  const point = cataloguePointForLocation(location, elevationMode);
  const withHours = point ? rescaleModelWindowHours(marked, point) : marked;
  return withHours.map((day) => {
    if (!Array.isArray(day.hourly) || day.hourly.length === 0) return day;
    const totalSnowfall = day.hourly.reduce(
      (sum, h) => sum + (h.snowfall > 0 ? h.snowfall : 0),
      0,
    );
    return totalSnowfall === day.totalSnowfall ? day : { ...day, totalSnowfall };
  });
}

/** Inline display labels for the serving arms, as carried by the standard forecast display. */
export const MODEL_ARM_LABEL: Record<ModelSourceArm, string> = {
  ecmwf: 'ECMWF IFS',
  hrrr: 'NOAA HRRR',
  ecmwf_openmeteo: 'ECMWF (Open-Meteo)',
};

function formatCycleUtc(cycleUtc: string): string {
  return `${cycleUtc.slice(0, 13).replace('T', ' ')}Z cycle`;
}

/**
 * The inline caption for a day under the ENS-EB menu method -- the small
 * trend line the standard forecast display carries under the Reference
 * Fresh Snow total (ForecastDashboard), the same way every other
 * method's days are presented: there is no dedicated model surface.
 *
 * The number it captions is a CALENDAR-DAY total (the sum of the day's
 * hourly bars), so the caption describes the day first: its hours are
 * allocated from ENS-EB windows where records exist and are Kuchera
 * elsewhere. The remaining segments keep the serving design's
 * per-window labelling duties -- the arm that served the window ending
 * 07:00 local that morning, its reduced-feature disclosure, and its
 * serving cycle (for the Open-Meteo arm, the anchor cycle its windows
 * are tied to -- that arm's forecast instance is not cycle-verified,
 * and the label says so) -- explicitly scoped to that one window: a
 * day's afternoon hours belong to the NEXT window, which may have been
 * served by a different arm or fallen back, and the label must not
 * present the morning window's provenance as the whole day's.
 */
export function modelWindowTrendLabel(mark: DayModelWindowMark): string {
  const dayPrefix =
    'Calendar-day snowfall · ENS-EB windows where available, Kuchera elsewhere';
  if (mark.kind === 'kuchera_fallback') {
    return `${dayPrefix} · the 24 h window ending 07:00 local fell back to Kuchera (no ENS-EB record)`;
  }
  const arm = mark.arm ? MODEL_ARM_LABEL[mark.arm] : MODEL_ARM_LABEL.ecmwf;
  let label = `${dayPrefix} · the 24 h window ending 07:00 local: ENS-EB model · ${arm} arm`;
  if (mark.reducedFeatureVariant) label += ' · reduced-feature variant';
  if (mark.cycleUtc) {
    label += mark.arm === 'ecmwf_openmeteo'
      ? ` · anchored to ${formatCycleUtc(mark.cycleUtc)} · Open-Meteo cycle unverified`
      : ` · ${formatCycleUtc(mark.cycleUtc)}`;
  }
  return label;
}
