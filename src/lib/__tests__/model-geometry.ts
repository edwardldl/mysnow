import { addCalendarDays, providerLocalTimeToEpochMs } from '../time';

/** ECMWF open-data GRIB ceiling: deterministic steps run to 240 h. */
export const ECMWF_STEP_CEILING_HOURS = 240;
/** HRRR forecast horizon: 48 h on the synoptic cycles the batch rides. */
export const HRRR_HORIZON_HOURS = 48;

/** One served 24 h window, as the batch file labels it. */
export interface ServedWindow {
  leadDay: number;
  windowEndLocalDate: string;
}

/**
 * The served-window geometry for one catalogue point under one cycle,
 * derived from the serving contract — not from the batch file's rows.
 * It mirrors the producer exactly (training/slr_calibration/scripts/
 * run_model_serving.py, score_arm): the window start is the end minus
 * exactly 24 elapsed hours, and the horizon gate needs the first needed
 * hour — start + 1 h, the step covering the window's first hour — to be
 * at or after the cycle, with the end at or before the arm's ceiling
 * (240 h for ECMWF, 48 h for HRRR). The lead day is the calendar-day
 * distance from the cycle's local date at the point to the window-end
 * local date.
 *
 * That is the whole geometry, and it differs by cycle and season. From
 * a 00Z cycle the first complete window ends the next local day and is
 * lead 2 (the cycle falls on the previous local day everywhere in North
 * America), while from a 12Z cycle it is lead 1. The +1 h in the gate
 * matters at the margins: from a 12Z cycle in daylight time an eastern
 * point's lead-1 window starts an hour before the cycle yet is served
 * (its end is 23 h out, so start + 1 h is exactly the cycle). The last
 * window is lead 10 only where the step-240 ceiling reaches it — every
 * point from a 00Z cycle, and from a 12Z cycle only at points whose UTC
 * offset is −5 at the window end (eastern time in standard time;
 * central time in daylight time, where 07:00 CDT is 12:00Z and the
 * lead-10 window ends exactly 240 h out), lead 9 elsewhere. The HRRR
 * 48 h horizon likewise reaches a second window only at those
 * offset-−5 points from a 12Z cycle (in daylight time their lead-2
 * window ends exactly 48 h out). The HRRR
 * domain guard (Canadian points + Alyeska serve no HRRR rows at all) is
 * a point property, not geometry, and is not modelled here — callers pin
 * it by point identity.
 *
 * Candidate local end dates run two days before to twelve days after the
 * cycle (UTC date), comfortably bracketing every window the ceilings
 * could reach. 07:00 local always exists exactly once (transitions fall
 * at 02:00), so the local-to-UTC conversion is unambiguous. Note the
 * start is NOT 07:00 local the previous day: across a DST transition
 * that would be a 23- or 25-hour window, while the producer always uses
 * end minus 24 elapsed hours. (The fall-back regression case pins the
 * combined behaviour, not the start definition alone — under the
 * corrected +1 h gate a previous-day 07:00 start would also pass there;
 * the definition itself follows the producer code. Codex confirmation,
 * 2026-11-12.)
 */
// Memoised conversions: the catalogue has only a handful of timezones,
// so the same (local timestamp, timezone) pair recurs for hundreds of
// points. Without this the per-point catalogue test blows the default
// 5 s test timeout (each conversion probes seven candidate offsets).
const instantCache = new Map<string, number>();
function localToEpochMs(localTimestamp: string, timeZone: string): number {
  const key = `${timeZone}|${localTimestamp}`;
  const hit = instantCache.get(key);
  if (hit !== undefined) return hit;
  const value = providerLocalTimeToEpochMs(localTimestamp, timeZone);
  instantCache.set(key, value);
  return value;
}

const localDateCache = new Map<string, string>();
function localDateOf(epochMs: number, timeZone: string): string {
  const key = `${timeZone}|${epochMs}`;
  const hit = localDateCache.get(key);
  if (hit !== undefined) return hit;
  const value = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(epochMs));
  localDateCache.set(key, value);
  return value;
}

export function expectedServedWindows(
  cycleUtc: string | undefined,
  point: { timezone: string },
  ceilingHours: number,
): ServedWindow[] {
  // The batch file's cycles are Partial<Record<...>>; a missing cycle
  // makes the geometry underivable, so fail loudly rather than
  // deriving from undefined.
  if (cycleUtc === undefined) throw new Error('no cycle in serving data');
  const cycleMs = Date.parse(cycleUtc);
  const cycleUtcDate = cycleUtc.slice(0, 10);
  const cycleLocalDate = localDateOf(cycleMs, point.timezone);
  const windows: ServedWindow[] = [];
  for (let offset = -2; offset <= 12; offset += 1) {
    const endLocalDate = addCalendarDays(cycleUtcDate, offset);
    const endMs = localToEpochMs(`${endLocalDate}T07:00:00`, point.timezone);
    // Exactly 24 elapsed hours before the end (the producer's
    // definition), not 07:00 local the previous day — see the doc
    // comment above for the DST reason.
    const startMs = endMs - 24 * 3_600_000;
    // The producer's gate: the first needed hour (start + 1 h) must be
    // at or after the cycle, and the end at or before the ceiling.
    if (startMs + 3_600_000 < cycleMs) continue;
    if (endMs - cycleMs > ceilingHours * 3_600_000) continue;
    const leadDay = Math.round(
      (Date.parse(endLocalDate) - Date.parse(cycleLocalDate)) / 86_400_000,
    );
    windows.push({ leadDay, windowEndLocalDate: endLocalDate });
  }
  return windows;
}

/**
 * The served-window expectation for one arm of a possibly MERGED
 * frontend (tiered cadence, 2026-11-16): records can carry different
 * scoring cycles per window. Merges never add or remove windows, so
 * the window set is the one the OLDEST contributing cycle serves;
 * each window's lead day is the one its own record's cycle assigns.
 * The window set is anchored to the seed (full-batch) cycle when the
 * document carries seedCycles; records without per-record provenance
 * fall back to the arm cycle, which reduces this to
 * expectedServedWindows for a plain batch file.
 */
export function expectedMergedWindows(
  rows: { leadDay: number; windowEndLocalDate: string; cycleUtc?: string }[],
  baselineCycleUtc: string | undefined,
  fallbackCycleUtc: string | undefined,
  point: { timezone: string },
  ceilingHours: number,
): ServedWindow[] {
  const cycleOf = (row: { cycleUtc?: string }): string | undefined => row.cycleUtc ?? fallbackCycleUtc;
  const cycles = [...new Set(rows.map(cycleOf))];
  // Coverage is anchored to the seed (full-batch) cycle when the
  // document carries one; only without it does the oldest surviving
  // record cycle stand in. With neither, nothing is expected.
  const baseline = baselineCycleUtc ?? (cycles.length > 0 ? cycles.reduce((a, b) => (a! <= b! ? a : b)) : undefined);
  if (baseline === undefined) return [];
  const baselineWindows = expectedServedWindows(baseline, point, ceilingHours);
  const byCycle = new Map(
    cycles.map((cycle) => [
      cycle,
      new Map(expectedServedWindows(cycle, point, ceilingHours).map((w) => [w.windowEndLocalDate, w.leadDay])),
    ]),
  );
  const leadByWindow = new Map<string, number>();
  for (const row of rows) {
    const lead = byCycle.get(cycleOf(row))?.get(row.windowEndLocalDate);
    if (lead === undefined) {
      throw new Error(
        `record window ${row.windowEndLocalDate} is not served by its own cycle ${cycleOf(row) ?? 'unknown'}`,
      );
    }
    leadByWindow.set(row.windowEndLocalDate, lead);
  }
  // Every baseline window must have a record: a missing one is a
  // coverage loss, not an expectation to derive around.
  return baselineWindows
    .map((w) => {
      const leadDay = leadByWindow.get(w.windowEndLocalDate);
      if (leadDay === undefined) {
        throw new Error(`expected window ${w.windowEndLocalDate} (seed cycle ${baseline}) has no record`);
      }
      return { leadDay, windowEndLocalDate: w.windowEndLocalDate };
    })
    .sort((a, b) => (a.windowEndLocalDate < b.windowEndLocalDate ? -1 : 1));
}
