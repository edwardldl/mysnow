import { describe, expect, it } from 'vitest';
import {
  ECMWF_STEP_CEILING_HOURS,
  expectedServedWindows,
  HRRR_HORIZON_HOURS,
  type ServedWindow,
} from './model-geometry';

const ny = { timezone: 'America/New_York' };
const la = { timezone: 'America/Los_Angeles' };
const w = (leadDay: number, windowEndLocalDate: string): ServedWindow => ({
  leadDay,
  windowEndLocalDate,
});

/**
 * Boundary cases for the geometry derivation, pinned against the
 * producer's rule (run_model_serving.py score_arm: start = end − 24
 * elapsed hours; served iff start + 1 h >= cycle and end <= cycle +
 * ceiling). Each case below was computed independently from the producer
 * code, not from the helper under test. The installed batches (November,
 * standard time, no transition in range) cannot discriminate any of
 * these: the +1 h gate and the 24-elapsed-hour start only matter at DST
 * transitions and in daylight time.
 */
describe('expectedServedWindows boundary cases', () => {
  it('includes the lead-1 window across the fall-back transition (2026-10-31 12Z, New York)', () => {
    // DST ends 2026-11-01 02:00 local: the lead-1 window ends 07:00 EST
    // Nov 1 (12:00Z) and starts exactly 24 elapsed hours earlier, at
    // the cycle. This case pins the combined fall-back behaviour (the
    // previous implementation excluded lead 1 here); it does not
    // isolate the 24-elapsed-hour start from the +1 h gate — a
    // previous-calendar-day 07:00 start (11:00Z) would also pass the
    // corrected gate — so the start definition itself rests on
    // correspondence with the producer code (Codex confirmation,
    // 2026-11-12).
    const cycle = '2026-10-31T12:00:00Z';
    expect(expectedServedWindows(cycle, ny, ECMWF_STEP_CEILING_HOURS)).toEqual([
      w(1, '2026-11-01'), w(2, '2026-11-02'), w(3, '2026-11-03'), w(4, '2026-11-04'),
      w(5, '2026-11-05'), w(6, '2026-11-06'), w(7, '2026-11-07'), w(8, '2026-11-08'),
      w(9, '2026-11-09'), w(10, '2026-11-10'),
    ]);
    expect(expectedServedWindows(cycle, ny, HRRR_HORIZON_HOURS)).toEqual([
      w(1, '2026-11-01'), w(2, '2026-11-02'),
    ]);
    expect(expectedServedWindows(cycle, la, ECMWF_STEP_CEILING_HOURS)).toEqual([
      w(1, '2026-11-01'), w(2, '2026-11-02'), w(3, '2026-11-03'), w(4, '2026-11-04'),
      w(5, '2026-11-05'), w(6, '2026-11-06'), w(7, '2026-11-07'), w(8, '2026-11-08'),
      w(9, '2026-11-09'),
    ]);
    expect(expectedServedWindows(cycle, la, HRRR_HORIZON_HOURS)).toEqual([w(1, '2026-11-01')]);
  });

  it('includes the lead-1 window across the spring-forward transition (2026-03-07 12Z, New York)', () => {
    // DST begins 2026-03-08 02:00 local: the lead-1 window ends 07:00
    // EDT Mar 8 (11:00Z) and starts 24 elapsed hours earlier, 11:00Z
    // Mar 7 — an hour before the cycle — yet is served, because the
    // producer's gate needs start + 1 h, exactly the cycle.
    const cycle = '2026-03-07T12:00:00Z';
    expect(expectedServedWindows(cycle, ny, ECMWF_STEP_CEILING_HOURS)).toEqual([
      w(1, '2026-03-08'), w(2, '2026-03-09'), w(3, '2026-03-10'), w(4, '2026-03-11'),
      w(5, '2026-03-12'), w(6, '2026-03-13'), w(7, '2026-03-14'), w(8, '2026-03-15'),
      w(9, '2026-03-16'), w(10, '2026-03-17'),
    ]);
    expect(expectedServedWindows(cycle, ny, HRRR_HORIZON_HOURS)).toEqual([
      w(1, '2026-03-08'), w(2, '2026-03-09'),
    ]);
    expect(expectedServedWindows(cycle, la, ECMWF_STEP_CEILING_HOURS)).toEqual([
      w(1, '2026-03-08'), w(2, '2026-03-09'), w(3, '2026-03-10'), w(4, '2026-03-11'),
      w(5, '2026-03-12'), w(6, '2026-03-13'), w(7, '2026-03-14'), w(8, '2026-03-15'),
      w(9, '2026-03-16'),
    ]);
    expect(expectedServedWindows(cycle, la, HRRR_HORIZON_HOURS)).toEqual([w(1, '2026-03-08')]);
  });

  it('includes the lead-1 window in daylight time via the producer +1 h gate (2026-07-01 12Z, New York)', () => {
    // In daylight time an eastern point's lead-1 window ends 23 h after
    // a 12Z cycle (07:00 EDT = 11:00Z) and starts an hour before it; a
    // strict start >= cycle rule would wrongly exclude it. The
    // producer's gate (start + 1 h >= cycle) admits it exactly.
    const cycle = '2026-07-01T12:00:00Z';
    expect(expectedServedWindows(cycle, ny, ECMWF_STEP_CEILING_HOURS)).toEqual([
      w(1, '2026-07-02'), w(2, '2026-07-03'), w(3, '2026-07-04'), w(4, '2026-07-05'),
      w(5, '2026-07-06'), w(6, '2026-07-07'), w(7, '2026-07-08'), w(8, '2026-07-09'),
      w(9, '2026-07-10'), w(10, '2026-07-11'),
    ]);
    expect(expectedServedWindows(cycle, ny, HRRR_HORIZON_HOURS)).toEqual([
      w(1, '2026-07-02'), w(2, '2026-07-03'),
    ]);
    expect(expectedServedWindows(cycle, la, ECMWF_STEP_CEILING_HOURS)).toEqual([
      w(1, '2026-07-02'), w(2, '2026-07-03'), w(3, '2026-07-04'), w(4, '2026-07-05'),
      w(5, '2026-07-06'), w(6, '2026-07-07'), w(7, '2026-07-08'), w(8, '2026-07-09'),
      w(9, '2026-07-10'),
    ]);
    expect(expectedServedWindows(cycle, la, HRRR_HORIZON_HOURS)).toEqual([w(1, '2026-07-02')]);
  });
});
