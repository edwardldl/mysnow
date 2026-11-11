import { describe, expect, it } from 'vitest';
import { displayDayIsPowder, displayDaySlr } from '../model-forecast';
import type { DayData, DayModelWindowMark } from '../types';

/**
 * Day-card / ribbon SLR presentation.
 *
 * History: the Heavenly 2026-11-18 inversion (fix f18d03b) was a hybrid
 * ratio -- the RECORD's snowfall divided by the LIVE SUBSTRATE's frozen
 * SWE -- and the fix presented the record's SLR on model days. The
 * 2026-11-11 pure-SLR change supersedes the presentation rule again:
 * the day's snowfall is now BUILT from the day's own frozen SWE series
 * (record SLRs inside model windows, substrate SLRs elsewhere), so the
 * day's effective ratio 10 x totalSnowfall / sum(frozenSweMm) is a
 * same-source weighted mean, not a hybrid, and it is the only day
 * ratio that satisfies the user's identity snowfall / SLR =
 * precipitation for the calendar day (a day straddles two windows, so
 * no single window's record SLR can describe its total -- Codex gate,
 * 2026-11-11). The record SLR remains a window value, carried on the
 * mark and named in the trend label. The powder badge follows its own
 * same-window rule: SLR and snowfall threshold always describe the
 * same accumulation.
 */
const modelMark = (slr: number, snowfallCm: number | null): DayModelWindowMark => ({
  kind: 'model',
  slr,
  snowfallCm,
  arm: 'ecmwf',
  blended: true,
  cycleUtc: '2026-11-10T00:00:00Z',
  reducedFeatureVariant: false,
  isotonicAdjusted: false,
  preIsotonicSlr: null,
});

const fallbackMark: DayModelWindowMark = {
  kind: 'kuchera_fallback',
  slr: null,
  snowfallCm: null,
  arm: null,
  blended: true,
  cycleUtc: null,
  reducedFeatureVariant: false,
  isotonicAdjusted: false,
  preIsotonicSlr: null,
};

const mkDay = (partial: Partial<DayData>): DayData => partial as DayData;
const hoursWithFrozenSwe = (mm: number) => [{ frozenSweMm: mm }] as DayData['hourly'];

describe('displayDaySlr', () => {
  it('on a day lying in a single model window, the day ratio is the record SLR (Heavenly values)', () => {
    // The served records for the window ending 2026-11-18 are physically
    // ordered (13.218 / 13.500 / 14.892 on 1.4043 mm at all three
    // points); applied hours built as frozen x SLR / 10 keep that
    // ordering in the day ratio.
    const mk = (slr: number, snowfallCm: number) =>
      mkDay({
        totalSnowfall: snowfallCm,
        hourly: hoursWithFrozenSwe(1.4043),
        modelWindow: modelMark(slr, snowfallCm),
      });
    const base = mk(13.21793298656373, 1.8561943293031447);
    const mid = mk(13.499933670704241, 1.8957956853769968);
    const summit = mk(14.89227518709731, 2.0913222045240754);
    expect(displayDaySlr(base)).toBeCloseTo(13.21793298656373, 6);
    expect(displayDaySlr(mid)).toBeCloseTo(13.499933670704241, 6);
    expect(displayDaySlr(summit)).toBeCloseTo(14.89227518709731, 6);
    expect(displayDaySlr(base)).toBeLessThan(displayDaySlr(mid));
    expect(displayDaySlr(mid)).toBeLessThan(displayDaySlr(summit));
  });

  it('on a model day spanning two windows, presents the effective ratio, not either window SLR', () => {
    // Morning window at SLR 10 over 10 mm frozen, afternoon window at
    // SLR 20 over 10 mm frozen: day snowfall 30 cm on 20 mm. The
    // morning window's record SLR (10, on the mark) would imply 30 mm
    // of precipitation; the effective ratio 15 satisfies the identity
    // for the day: 30 / 15 = 2.0 cm = 20 mm of frozen precipitation.
    const day = mkDay({
      totalSnowfall: 30,
      hourly: [{ frozenSweMm: 10 }, { frozenSweMm: 10 }] as DayData['hourly'],
      modelWindow: modelMark(10, 10),
    });
    expect(displayDaySlr(day)).toBe(15);
    expect(day.totalSnowfall / displayDaySlr(day)).toBeCloseTo(2.0, 9);
  });

  it('keeps the substrate ratio on Kuchera fallback days under the model method', () => {
    const day = mkDay({
      totalSnowfall: 3.0,
      hourly: hoursWithFrozenSwe(2.0),
      modelWindow: fallbackMark,
    });
    expect(displayDaySlr(day)).toBe(15);
  });

  it('keeps the substrate ratio on unmarked days (all other methods)', () => {
    const day = mkDay({ totalSnowfall: 2.5, hourly: hoursWithFrozenSwe(2.0) });
    expect(displayDaySlr(day)).toBe(12.5);
  });

  it('a model mark alone does not change the ratio: same hours, same SLR, with or without the mark', () => {
    const hours = hoursWithFrozenSwe(2.0);
    const marked = mkDay({ totalSnowfall: 3.0, hourly: hours, modelWindow: modelMark(11, 3.0) });
    const unmarked = mkDay({ totalSnowfall: 3.0, hourly: hours });
    expect(displayDaySlr(marked)).toBe(displayDaySlr(unmarked));
  });

  it('returns 0 when there is no frozen SWE and no model mark', () => {
    expect(displayDaySlr(mkDay({ totalSnowfall: 0, hourly: [] }))).toBe(0);
    expect(
      displayDaySlr(mkDay({ totalSnowfall: 4, hourly: hoursWithFrozenSwe(0) })),
    ).toBe(0);
  });

  it('returns 0 for a model day with no hourly series in view (record exists, nothing applied)', () => {
    const day = mkDay({
      totalSnowfall: 999,
      hourly: [],
      modelWindow: modelMark(12, null),
    });
    expect(displayDaySlr(day)).toBe(0);
  });
});

describe('displayDayIsPowder', () => {
  it('badges a model day only when the SAME window clears both thresholds', () => {
    const powder = mkDay({
      totalSnowfall: 1.0, // calendar-day total is irrelevant on a model day
      hourly: hoursWithFrozenSwe(5.0),
      modelWindow: modelMark(16, 3.5),
    });
    expect(displayDayIsPowder(powder)).toBe(true);
  });

  it('does not badge a model day whose fluffy window snowfall is tiny, however big the calendar-day total', () => {
    const mixed = mkDay({
      totalSnowfall: 12.0, // dense snow later in the calendar day
      hourly: hoursWithFrozenSwe(8.0),
      modelWindow: modelMark(18, 1.0), // fluffy but only 1 cm in the window
    });
    expect(displayDayIsPowder(mixed)).toBe(false);
  });

  it('keeps the substrate rule on fallback and unmarked days', () => {
    const fallback = mkDay({
      totalSnowfall: 3.0,
      hourly: hoursWithFrozenSwe(2.0), // substrate SLR 15
      modelWindow: fallbackMark,
    });
    expect(displayDayIsPowder(fallback)).toBe(true);
    const dense = mkDay({ totalSnowfall: 3.0, hourly: hoursWithFrozenSwe(3.0) });
    expect(displayDayIsPowder(dense)).toBe(false);
  });
});
