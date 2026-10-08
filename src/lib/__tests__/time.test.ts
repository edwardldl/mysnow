import { describe, expect, it } from 'vitest';
import {
  formatDataAge,
  nextResortOpening,
  providerLocalTimeToEpochMs,
  providerLocalTimesToEpochMs,
} from '../time';

describe('provider-local time handling', () => {
  it('maps the same wall-clock label to the configured resort timezone', () => {
    const local = '2026-01-15T08:00';
    const california = providerLocalTimeToEpochMs(local, 'America/Los_Angeles');
    const japan = providerLocalTimeToEpochMs(local, 'Asia/Tokyo');

    expect(california - japan).toBe(17 * 60 * 60 * 1000);
    expect(new Date(california).toISOString()).toBe('2026-01-15T16:00:00.000Z');
    expect(new Date(japan).toISOString()).toBe('2026-01-14T23:00:00.000Z');
  });

  it('keeps repeated fall-back timestamps chronological', () => {
    const instants = providerLocalTimesToEpochMs([
      '2026-11-01T00:00',
      '2026-11-01T01:00',
      '2026-11-01T01:00',
      '2026-11-01T02:00',
    ], 'America/Los_Angeles');

    expect(instants.slice(1).map((instant, index) => instant - instants[index]))
      .toEqual([3_600_000, 3_600_000, 3_600_000]);
  });

  it('resolves next opening across spring DST and rejects skipped local times', () => {
    const snowfall = providerLocalTimeToEpochMs('2026-03-07T12:00', 'America/Los_Angeles');
    const opening = nextResortOpening('2026-03-07', 'America/Los_Angeles');

    expect(opening.localTime).toBe('2026-03-08T08:00');
    expect(opening.epochMs - snowfall).toBe(19 * 60 * 60 * 1000);
    expect(() => providerLocalTimeToEpochMs('2026-03-08T02:30', 'America/Los_Angeles'))
      .toThrow('does not exist');
  });

  it('resolves a remote-zone opening without the browser timezone', () => {
    const opening = nextResortOpening('2026-12-31', 'Europe/Zurich');
    expect(opening.localTime).toBe('2027-01-01T08:00');
    expect(new Date(opening.epochMs).toISOString()).toBe('2027-01-01T07:00:00.000Z');
  });
});

describe('data age labels', () => {
  it('formats minute and hour ages without hiding staleness', () => {
    expect(formatDataAge(25_000)).toBe('less than 1 min old');
    expect(formatDataAge(12 * 60_000)).toBe('12 min old');
    expect(formatDataAge((2 * 60 + 15) * 60_000)).toBe('2 hr 15 min old');
  });
});
