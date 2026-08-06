const FULL_CIRCLE_DEG = 360;

function normalizeDirection(directionDeg: number): number {
  return ((directionDeg % FULL_CIRCLE_DEG) + FULL_CIRCLE_DEG) % FULL_CIRCLE_DEG;
}

/** Circularly interpolate a directional shelter lookup such as 15° Sx bins. */
export function interpolateWindShelter(values: number[], windFromDeg: number | null): number {
  if (values.length === 0 || windFromDeg === null || !Number.isFinite(windFromDeg)) return 0;
  const position = normalizeDirection(windFromDeg) / FULL_CIRCLE_DEG * values.length;
  const lower = Math.floor(position) % values.length;
  const upper = (lower + 1) % values.length;
  const fraction = position - Math.floor(position);
  return values[lower] + (values[upper] - values[lower]) * fraction;
}

export function leewardAlignment(aspectDeg: number, windFromDeg: number | null): number {
  if (windFromDeg === null || !Number.isFinite(windFromDeg)) return 0;
  const differenceRad = (normalizeDirection(aspectDeg - windFromDeg)) * Math.PI / 180;
  return (1 - Math.cos(differenceRad)) / 2;
}
