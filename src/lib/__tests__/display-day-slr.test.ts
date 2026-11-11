import { describe, expect, it } from 'vitest';
import { displayDayIsPowder, displayDaySlr } from '../model-forecast';
import type { DayData, DayModelWindowMark } from '../types';

/**
 * Regression suite for the Heavenly 2026-11-18 inversion the user
 * spotted: the served ENS-EB records for the window ending 2026-11-18
 * are physically ordered (base 13.218, mid 13.500, summit 14.892), but
 * the day cards divided the MODEL snowfall by the LIVE SUBSTRATE's
 * frozen SWE, whose amount shrinks towards the warmer base -- a hybrid
 * ratio that inverted the displayed SLR across elevations. Model days
 * must present the model's own SLR (the mark); every other day keeps
 * the substrate ratio, where snowfall and frozen SWE share one source.
 * The powder badge follows the same rule: SLR and snowfall threshold
 * always describe the same accumulation (Codex review, 2026-11-10).
 */
const modelMark = (slr: number, snowfallCm: number): DayModelWindowMark => ({
  kind: 'model',
  slr,
  snowfallCm,
  arm: 'ecmwf',
  blended: true,
  cycleUtc: '2026-11-10T00:00:00Z',
  reducedFeatureVariant: false,
});

const fallbackMark: DayModelWindowMark = {
  kind: 'kuchera_fallback',
  slr: null,
  snowfallCm: null,
  arm: null,
  blended: true,
  cycleUtc: null,
  reducedFeatureVariant: false,
};

const mkDay = (partial: Partial<DayData>): DayData => partial as DayData;
const hoursWithFrozenSwe = (mm: number) => [{ frozenSweMm: mm }] as DayData['hourly'];

describe('displayDaySlr', () => {
  it('presents the model SLR on model days, never the hybrid substrate ratio', () => {
    // Substrate frozen SWE chosen so the OLD formula (totalSnowfall x 10
    // / frozenSwe) inverts: 21.1 (base) > 15.4 (mid) > 13.8 (summit).
    const base = mkDay({
      totalSnowfall: 1.9,
      hourly: hoursWithFrozenSwe(0.9),
      modelWindow: modelMark(13.21793298656373, 1.856),
    });
    const mid = mkDay({
      totalSnowfall: 2.0,
      hourly: hoursWithFrozenSwe(1.3),
      modelWindow: modelMark(13.5, 1.896),
    });
    const summit = mkDay({
      totalSnowfall: 2.2,
      hourly: hoursWithFrozenSwe(1.6),
      modelWindow: modelMark(14.89227518709731, 2.091),
    });
    expect(displayDaySlr(base)).toBe(13.21793298656373);
    expect(displayDaySlr(mid)).toBe(13.5);
    expect(displayDaySlr(summit)).toBe(14.89227518709731);
    // The physical ordering of the records survives to the display.
    expect(displayDaySlr(base)).toBeLessThan(displayDaySlr(mid));
    expect(displayDaySlr(mid)).toBeLessThan(displayDaySlr(summit));
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

  it('returns 0 when there is no frozen SWE and no model mark', () => {
    expect(displayDaySlr(mkDay({ totalSnowfall: 0, hourly: [] }))).toBe(0);
    expect(
      displayDaySlr(mkDay({ totalSnowfall: 4, hourly: hoursWithFrozenSwe(0) })),
    ).toBe(0);
  });

  it('never recomputes the hybrid ratio for a model mark with no SLR', () => {
    // Unreachable via applyModelMethodToDays (a model mark is created
    // only from a record carrying both SLR and snowfall), but if one
    // ever appears the display must show no SLR instead of dividing
    // possibly model-derived snowfall by substrate frozen SWE.
    const day = mkDay({
      totalSnowfall: 3.0,
      hourly: hoursWithFrozenSwe(2.0),
      modelWindow: { ...modelMark(0, 0), slr: null },
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
