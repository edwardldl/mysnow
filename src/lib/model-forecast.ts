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
 * Forecasts are capped at lead day 10 on every surface (user decision
 * 2026-11-10: "only do 10 days for all forecast across all surfaces").
 * The third arm that used to serve the tail windows beyond the GRIB
 * ceiling -- `ecmwf_openmeteo`, ECMWF IFS via Open-Meteo, added
 * 2026-11-01 under the third-source treatment from the E1 parity
 * report -- is RETIRED by that decision: the serving JSON carries no
 * Open-Meteo records, the serving merge enforces the same cap so a
 * future batch cannot reintroduce the tail, and this access layer
 * never resolves the arm or any record beyond lead day 10 (see
 * MAX_MODEL_LEAD_DAY), whatever a stale or hand-edited serving file
 * might contain. The arm remains in the data types and labels only so
 * historical batches stay describable. Windows beyond lead day 10
 * have no model record from any arm.
 * Precision note: the retired arm's windows were "GRIB ceiling + 1"
 * through 14, and under a 12Z cycle the western ceiling is lead 9 --
 * so the arm also used to fill the WITHIN-cap lead-10 window at
 * western points on 12Z batches. The current 00Z batch's GRIB arm
 * serves lead 10 at every point, so this install loses nothing; on a
 * future 12Z batch those western lead-10 windows will stay Kuchera
 * rather than borrowing the Open-Meteo arm. That is the accepted
 * consequence of the uniform cap (Codex review, 2026-11-10).
 * Every other location keeps Kuchera only, exactly as before. Data is
 * produced by the offline serving batch
 * (training/slr_calibration/scripts/run_model_serving.py; the
 * Open-Meteo companion script is retired and out of the batch path)
 * and imported as static JSON -- this app has no database; the JSON
 * is the serving layer in the workspace.
 */
import rawData from '../data/model-forecasts.json';
import { getSLRCategory } from './data';
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
   * Present only on records the isotonic serving layer adjusted for
   * elevation ordering (2026-11-10): the record's SLR and snowfall
   * before adjustment. modelSlr/modelSnowfallCm carry the adjusted
   * (served) values. Provenance only -- the display path consumes
   * modelSlr and never recomputes snowfall from these fields.
   */
  preIsotonicSlr?: number | null;
  preIsotonicSnowfallCm?: number | null;
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

/**
 * The forecast horizon cap for the model, in lead days (user decision
 * 2026-11-10: 10 days on all surfaces). Resolution ignores any serving
 * record beyond this lead, so no surface can show an ENS-EB value for
 * a day the cap excludes -- the serving merge enforces the same bound
 * on the data side (MAX_SERVED_LEAD_DAY in the serving batch).
 */
export const MAX_MODEL_LEAD_DAY = 10;

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
 * Literal `ecmwf` mode: the GRIB ECMWF arm only -- never HRRR. (The
 * Open-Meteo arm is retired as of the 2026-11-10 10-day cap and is
 * never resolved; see the file header.)
 *
 * Blend mode (`hrrr_ecmwf`) mirrors blendForecasts: HRRR overrides
 * ECMWF hour-by-hour wherever HRRR data exists inside its 0-48 h
 * horizon, and ECMWF supplies everything beyond it. At
 * window granularity that is HRRR for lead days 1-2 and ECMWF for
 * lead day 3 and beyond; the non-privileged arm remains as a fallback
 * for windows the privileged arm has no scored record for (e.g. HRRR
 * day-2 windows beyond the 48 h horizon from the 12Z cycle).
 *
 * Any other mode: no arms -- the model does not resolve there.
 */
export function modelArmPreference(modelId: string, leadDay: number): ModelSourceArm[] {
  const single = modelArmForMode(modelId);
  if (single === 'ecmwf') return ['ecmwf'];
  if (single === 'hrrr') return ['hrrr'];
  if (modelId !== BLEND_MODEL_ID) return [];
  return leadDay <= BLEND_HRRR_MAX_LEAD_DAY
    ? ['hrrr', 'ecmwf']
    : ['ecmwf', 'hrrr'];
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
   * Legacy field from the retired Open-Meteo arm (its records carried
   * a retrieval time because Open-Meteo publishes no cycle). Always
   * null since the 2026-11-10 10-day cap retired that arm.
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

  // Records beyond the 10-day cap never resolve (MAX_MODEL_LEAD_DAY),
  // and neither does the retired Open-Meteo arm: only the ECMWF and
  // HRRR rows are gathered here, whatever else a serving file carries.
  const rowFor = (arm: ModelSourceArm): ModelWindowForecast | null => {
    const row = point.forecasts[arm]?.find((r) => r.windowEndLocalDate === windowEndLocalDate) ?? null;
    return row !== null && row.leadDay <= MAX_MODEL_LEAD_DAY ? row : null;
  };

  // Find the window in either serving arm to learn its lead day, then
  // walk the mode's preference order (modelArmPreference). The
  // preference order is what confines each mode to its own arms:
  // literal HRRR can never resolve ECMWF rows gathered here, and
  // literal ECMWF can never resolve HRRR.
  const rows: Partial<Record<ModelSourceArm, ModelWindowForecast>> = {};
  for (const arm of ['ecmwf', 'hrrr'] as ModelSourceArm[]) {
    const row = rowFor(arm);
    if (row) rows[arm] = row;
  }
  const anyRow = rows.ecmwf ?? rows.hrrr;
  if (!anyRow) return null;
  for (const arm of modelArmPreference(modelId, anyRow.leadDay)) {
    const forecast = rows[arm];
    // A record resolves only with a usable SLR: under the pure-SLR
    // semantics (2026-11-11) the SLR is the ONLY quantity the display
    // path consumes from a record, so a record whose SLR is missing or
    // not finite and positive serves nothing and the window falls back
    // to Kuchera. (The serving batch clamps scored SLRs to [3, 30];
    // this is a corrupt-data guard.)
    if (
      forecast &&
      forecast.modelSlr !== null &&
      Number.isFinite(forecast.modelSlr) &&
      forecast.modelSlr > 0
    ) {
      return {
        point,
        arm,
        blended,
        cycleUtc: activeModelForecastData.cycles[arm] ?? null,
        // The Open-Meteo arm is retired (2026-11-10 cap): no resolved
        // window carries a retrieval timestamp any more.
        retrievedAtUtc: null,
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
      const frozenSweMm = bucket.reduce((sum, h) => sum + (h.frozenSweMm ?? 0), 0);
      return {
        ...window,
        snowfall,
        avgSlr: snowfall > 0 && frozenSweMm > 0 ? (snowfall * 10) / frozenSweMm : window.avgSlr,
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
 * Apply each model window's record SLR to the live substrate's hourly
 * frozen SWE, replacing the hourly snowfall inside the window with
 * frozenSweMm x recordSlr / 10 -- the exact conversion every other SLR
 * method performs per hour (snow/snowfall.ts: freshSnowCm =
 * frozenSweMm x freshSlr / 10). ENS-EB supplies the ratio, never the
 * precipitation (user directive, 2026-11-11: "the precipitation total
 * should be the same regardless of the SLR model", stated as the
 * identity snowfall / SLR = precipitation). Two distinct statements,
 * kept distinct (Codex gate, 2026-11-11): (a) TOTAL precipitation --
 * the hour's liquidMM, precipitation and the day's totalPrecipitation
 * -- is not modified by any method, so it is identical across methods;
 * (b) the identity snowfall / SLR = precipitation holds against the
 * FROZEN precipitation (frozenSweMm), the only part an SLR can act on:
 * when part of an hour's precipitation is rain, snowfall / SLR
 * recovers the frozen part, not the total, under ENS-EB exactly as
 * under Kuchera. The hour's frozenSweMm is NOT modified either, so
 * every ratio consumer -- the hourly chart's labels and colours, the
 * rolling 24/48 h statistics, the day cards -- sees snowfall, SLR and
 * frozen precipitation from one consistent triple: the window's
 * applied snowfall divided by the record SLR is exactly the
 * substrate's frozen SWE over the window.
 *
 * Each transformed hour also carries the record SLR itself (the
 * 2026-11-10 invariants from commit 3ea7016 hold unchanged: model
 * hours present the model's own SLR, and no hybrid of model snowfall
 * over a foreign denominator can arise, because the denominator is
 * never touched). Hours live on the real timeline: an hour belongs to
 * exactly one window, so a calendar day's display straddles two
 * windows and each part is converted by its own window's SLR; the
 * "24 h window ending 07:00 local" caption covers that accounting.
 * Only windows whose end-date day is present in the input are applied
 * (the marks drive application, as before); windows without a record
 * are not touched, and hours outside every served window keep the
 * substrate values object-for-object. The snowpack/depth outputs are
 * the layer model's sequential state and cannot be re-run post hoc,
 * and `snowfallResult` remains the substrate calculation's audit
 * trail. Input days and hours are never mutated.
 *
 * Returns the transformed days and, per model window end date, the
 * applied window snowfall (the sum over the window's hours present in
 * the array) for the day marks.
 */
function applyModelSlrToWindowHours(
  days: DayData[],
  point: ModelForecastPoint,
): { days: DayData[]; appliedSnowfallByWindowEnd: Map<string, number> } {
  const appliedSnowfallByWindowEnd = new Map<string, number>();
  const slrByWindowEnd = new Map<string, number>();
  for (const day of days) {
    if (day.modelWindow?.kind === 'model' && day.modelWindow.slr !== null) {
      slrByWindowEnd.set(day.dateStr, day.modelWindow.slr);
    }
  }
  if (slrByWindowEnd.size === 0) return { days, appliedSnowfallByWindowEnd };

  const slotsByWindow = new Map<string, ModelHourSlot[]>();
  days.forEach((day, dayIndex) => {
    (day.hourly ?? []).forEach((hour, hourIndex) => {
      const end = modelWindowEndDateForHour(hour, point.timezone);
      if (end === null || !slrByWindowEnd.has(end)) return;
      const slots = slotsByWindow.get(end) ?? [];
      slots.push({ dayIndex, hourIndex, hour });
      slotsByWindow.set(end, slots);
    });
  });
  if (slotsByWindow.size === 0) return { days, appliedSnowfallByWindowEnd };

  const key = (slot: ModelHourSlot) => `${slot.dayIndex}:${slot.hourIndex}`;
  const newValues = new Map<string, { snowfall: number; slr: number }>();

  for (const [end, slots] of slotsByWindow) {
    const recordSlr = slrByWindowEnd.get(end) as number;
    let applied = 0;
    for (const slot of slots) {
      const frozenSweMm = slot.hour.frozenSweMm ?? 0;
      const snowfall = (frozenSweMm * recordSlr) / 10;
      applied += snowfall;
      newValues.set(key(slot), { snowfall, slr: recordSlr });
    }
    appliedSnowfallByWindowEnd.set(end, applied);
  }

  const changedDays = new Set<number>();
  for (const k of newValues.keys()) changedDays.add(Number(k.split(':')[0]));
  if (changedDays.size === 0) return { days, appliedSnowfallByWindowEnd };
  const out = days.map((day, dayIndex) => {
    if (!changedDays.has(dayIndex)) return day;
    const hourly = (day.hourly ?? []).map((hour, hourIndex) => {
      const value = newValues.get(`${dayIndex}:${hourIndex}`);
      return value
        ? {
            ...hour,
            snowfall: value.snowfall,
            slr: value.slr,
            slrCategory: getSLRCategory(value.slr, hour.liquidMM),
          }
        : hour;
    });
    return { ...day, hourly, ...rebuildSnowfallAggregates(day, hourly) };
  });
  return { days: out, appliedSnowfallByWindowEnd };
}

/**
 * Apply the ENS-EB menu method to grouped forecast days. Each calendar
 * day is marked by the 24 h model window ending 07:00 local on that
 * date: when a serving record with a usable SLR exists the day is a
 * model day, otherwise it is a Kuchera fallback day.
 *
 * ENS-EB is applied as a pure SLR method (user directive, 2026-11-11,
 * superseding the 2026-10-31 "liquid decision" that substituted the
 * record's own snowfall): the record's SLR -- the isotonic-adjusted
 * value where the serving layer adjusted it -- converts the LIVE
 * substrate's frozen SWE over the same window, hour by hour, exactly
 * as Kuchera converts it with its own per-hour SLRs. The precipitation
 * is therefore the substrate's under every method; switching methods
 * changes only the ratio. The trade-off is stated plainly: the stored
 * SLR was predicted on its own serving cycle's profile and liquid, and
 * it is now applied to the live cycle's liquid, like every other SLR
 * method in the menu. The record's stored snowfall and liquid remain
 * batch provenance in the serving file; they are never presented as
 * the forecast's precipitation or snowfall.
 *
 * The DISPLAYED day total follows the calendar day, derived exactly
 * as groupData derives it for every other method: the sum of the
 * day's own hourly snowfall (positive part) over the final series
 * (the day/hour consistency requirement of commit a49de33). A model
 * window straddles two calendar days, so a model day's morning hours
 * carry the SLR of the window ending that morning while its afternoon
 * hours carry the next window's. Days carrying no hourly series keep
 * their substrate total (there is no series to convert).
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
    if (result !== null) {
      return {
        ...day,
        modelWindow: {
          kind: 'model' as const,
          slr: result.forecast.modelSlr,
          snowfallCm: null,
          arm: result.arm,
          blended: result.blended,
          cycleUtc: result.cycleUtc,
          reducedFeatureVariant: result.forecast.reducedFeatureVariant ?? false,
          isotonicAdjusted: result.forecast.preIsotonicSlr != null,
          preIsotonicSlr: result.forecast.preIsotonicSlr ?? null,
        },
      };
    }
    return {
      ...day,
      modelWindow: {
        kind: 'kuchera_fallback' as const,
        slr: null,
        snowfallCm: null,
        arm: null,
        blended: modelId === BLEND_MODEL_ID,
        cycleUtc: null,
        reducedFeatureVariant: false,
        isotonicAdjusted: false,
        preIsotonicSlr: null,
      },
    };
  });
  const point = availability.point;
  const { days: withHours, appliedSnowfallByWindowEnd } = point
    ? applyModelSlrToWindowHours(marked, point)
    : { days: marked, appliedSnowfallByWindowEnd: new Map<string, number>() };
  return withHours.map((day) => {
    let out = day;
    const applied = appliedSnowfallByWindowEnd.get(day.dateStr);
    if (out.modelWindow?.kind === 'model' && applied !== undefined) {
      out = { ...out, modelWindow: { ...out.modelWindow, snowfallCm: applied } };
    }
    if (!Array.isArray(out.hourly) || out.hourly.length === 0) return out;
    const totalSnowfall = out.hourly.reduce(
      (sum, h) => sum + (h.snowfall > 0 ? h.snowfall : 0),
      0,
    );
    return totalSnowfall === out.totalSnowfall ? out : { ...out, totalSnowfall };
  });
}

/**
 * The SLR a day card / ribbon cell should present for one day: the
 * day's OWN effective ratio, 10 x totalSnowfall / sum of the day's
 * frozen SWE, so the presented pair (snowfall, SLR) always satisfies
 * the user's identity snowfall / SLR = frozen precipitation for the
 * day itself (directive, 2026-11-11; Codex gate on the pure-SLR
 * change). A calendar day straddles two model windows, so no single
 * window's record SLR can describe the day's total; the record SLR is
 * a WINDOW value and is presented as such (the day mark and the trend
 * label), never as the day's ratio.
 *
 * This recomputation is not the hybrid the 2026-11-10 Heavenly fix
 * (f18d03b) forbade: that ratio divided the RECORD's snowfall by the
 * live substrate's frozen SWE -- two different forecasts. Under the
 * pure-SLR semantics the numerator here is the applied snowfall built
 * from this very frozen SWE series, hour by hour, so the ratio is the
 * frozen-weighted mean of the day's own hour SLRs (record SLRs inside
 * model windows, substrate SLRs elsewhere). On Kuchera fallback days
 * and under every other method the formula is the substrate's own
 * ratio, exactly as before.
 */
export function displayDaySlr(day: DayData): number {
  const frozenSweMm = (day.hourly ?? []).reduce(
    (sum, hour) => sum + (hour.frozenSweMm ?? 0),
    0,
  );
  return frozenSweMm > 0 ? (day.totalSnowfall * 10) / frozenSweMm : 0;
}

/**
 * Whether a day earns the "powder" presentation. The SLR and the
 * snowfall threshold must describe the SAME accumulation: on a model
 * day both come from the same model window (the record's SLR and the
 * applied window snowfall -- that SLR x the substrate's frozen SWE
 * over the window), never the window SLR paired with the calendar-day
 * total (a small fluffy window snowfall plus dense snow later in the
 * day would badge a powder day neither interval supports on its own).
 * On every other day both come from the substrate day, as before.
 */
export function displayDayIsPowder(day: DayData): boolean {
  if (
    day.modelWindow?.kind === 'model' &&
    day.modelWindow.slr !== null &&
    day.modelWindow.snowfallCm !== null
  ) {
    return day.modelWindow.slr >= 15 && day.modelWindow.snowfallCm > 2;
  }
  return displayDaySlr(day) >= 15 && day.totalSnowfall > 2;
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
  if (mark.slr !== null) label += ` · window SLR ${mark.slr.toFixed(1)}:1`;
  if (mark.reducedFeatureVariant) label += ' · reduced-feature variant';
  if (mark.isotonicAdjusted) {
    label += mark.preIsotonicSlr !== null
      ? ` · SLR isotonic-adjusted from ${mark.preIsotonicSlr.toFixed(1)}:1 for elevation ordering`
      : ' · SLR isotonic-adjusted for elevation ordering';
  }
  if (mark.cycleUtc) {
    label += mark.arm === 'ecmwf_openmeteo'
      ? ` · anchored to ${formatCycleUtc(mark.cycleUtc)} · Open-Meteo cycle unverified`
      : ` · ${formatCycleUtc(mark.cycleUtc)}`;
  }
  if (mark.snowfallCm === null) {
    // A record exists for this window but no hour OF THIS WINDOW is in
    // view, so this window's SLR was not applied. The disclaimer is
    // scoped to the window, not the day: the day's afternoon hours may
    // still have been converted by the NEXT window's record (a partial
    // first day in the array), and claiming the day "shows substrate
    // values" would then be false (Codex gate, 2026-11-11).
    label += " · no hours from this window are in view -- this window's SLR was not applied";
  }
  return label;
}
