const LOCAL_DATE_TIME_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/;

type LocalDateTimeParts = {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
};

function parseLocalDateTime(value: string): LocalDateTimeParts {
  const match = LOCAL_DATE_TIME_PATTERN.exec(value);
  if (!match) throw new Error(`Invalid provider-local timestamp: ${value}`);
  const [, year, month, day, hour, minute, second = '0'] = match;
  return {
    year: Number(year),
    month: Number(month),
    day: Number(day),
    hour: Number(hour),
    minute: Number(minute),
    second: Number(second),
  };
}

function formatParts(epochMs: number, timeZone: string): LocalDateTimeParts {
  const formatter = new Intl.DateTimeFormat('en-US-u-ca-iso8601', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  });
  const parts = formatter.formatToParts(new Date(epochMs));
  const value = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find(part => part.type === type)?.value);
  return {
    year: value('year'),
    month: value('month'),
    day: value('day'),
    hour: value('hour'),
    minute: value('minute'),
    second: value('second'),
  };
}

function sameParts(left: LocalDateTimeParts, right: LocalDateTimeParts): boolean {
  return left.year === right.year
    && left.month === right.month
    && left.day === right.day
    && left.hour === right.hour
    && left.minute === right.minute
    && left.second === right.second;
}

function offsetAt(epochMs: number, timeZone: string): number {
  const parts = formatParts(epochMs, timeZone);
  const representedAsUtc = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second);
  return representedAsUtc - Math.floor(epochMs / 1000) * 1000;
}

/**
 * Returns every instant represented by an offset-free provider-local timestamp.
 * Ambiguous fall-back times can produce two instants; spring-forward gaps produce none.
 */
export function possibleInstantsForLocalTime(localTimestamp: string, timeZone: string): number[] {
  const parts = parseLocalDateTime(localTimestamp);
  const nominalUtc = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second);
  const offsets = new Set<number>();
  for (const hours of [-48, -24, -12, 0, 12, 24, 48]) {
    offsets.add(offsetAt(nominalUtc + hours * 3_600_000, timeZone));
  }
  return [...offsets]
    .map(offset => nominalUtc - offset)
    .filter(candidate => sameParts(formatParts(candidate, timeZone), parts))
    .filter((candidate, index, all) => all.indexOf(candidate) === index)
    .sort((a, b) => a - b);
}

export function providerLocalTimeToEpochMs(localTimestamp: string, timeZone: string): number {
  const candidates = possibleInstantsForLocalTime(localTimestamp, timeZone);
  if (candidates.length === 0) {
    throw new Error(`Provider-local timestamp ${localTimestamp} does not exist in ${timeZone}`);
  }
  return candidates[0];
}

/**
 * Converts a provider series while preserving chronological order through a repeated
 * fall-back hour. Open-Meteo supplies local labels without offsets, so sequence order
 * is needed to distinguish the two occurrences of an ambiguous local hour.
 */
export function providerLocalTimesToEpochMs(localTimestamps: string[], timeZone: string): number[] {
  let previous = Number.NEGATIVE_INFINITY;
  return localTimestamps.map(localTimestamp => {
    const candidates = possibleInstantsForLocalTime(localTimestamp, timeZone);
    const candidate = candidates.find(value => value > previous);
    if (candidate === undefined) {
      throw new Error(`Provider-local timestamp series is not chronological at ${localTimestamp} in ${timeZone}`);
    }
    previous = candidate;
    return candidate;
  });
}

export function addCalendarDays(date: string, days: number): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!match) throw new Error(`Invalid calendar date: ${date}`);
  const next = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + days));
  return next.toISOString().slice(0, 10);
}

export function nextResortOpening(date: string, timeZone: string, openingHour = 8): { localTime: string; epochMs: number } {
  if (!Number.isInteger(openingHour) || openingHour < 0 || openingHour > 23) {
    throw new Error(`Invalid opening hour: ${openingHour}`);
  }
  const localTime = `${addCalendarDays(date, 1)}T${String(openingHour).padStart(2, '0')}:00`;
  return { localTime, epochMs: providerLocalTimeToEpochMs(localTime, timeZone) };
}

export function formatDataAge(ageMs: number): string {
  const totalMinutes = Math.max(0, Math.floor(ageMs / 60_000));
  if (totalMinutes < 1) return 'less than 1 min old';
  if (totalMinutes < 60) return `${totalMinutes} min old`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes === 0 ? `${hours} hr old` : `${hours} hr ${minutes} min old`;
}
