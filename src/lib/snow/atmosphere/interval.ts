import type { RawPressureLayer } from '../profile';

export function intervalMean(first: number | null | undefined, second: number | null | undefined): number | null {
  const values = [first, second].filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  return values.length === 0 ? null : values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function intervalDirectionMean(first: number | null | undefined, second: number | null | undefined): number | null {
  const values = [first, second].filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  if (values.length === 0) return null;
  const components = values.map(value => value * Math.PI / 180);
  const x = components.reduce((sum, value) => sum + Math.cos(value), 0);
  const y = components.reduce((sum, value) => sum + Math.sin(value), 0);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

/** Average instantaneous profiles at t-1 and t for precipitation interval (t-1,t]. */
export function intervalRepresentativeLayers(
  previous: RawPressureLayer[] | undefined,
  current: RawPressureLayer[] | undefined,
): RawPressureLayer[] {
  const byPressure = new Map<number, { previous?: RawPressureLayer; current?: RawPressureLayer }>();
  for (const layer of previous ?? []) byPressure.set(layer.pressureHpa, { previous: layer });
  for (const layer of current ?? []) {
    byPressure.set(layer.pressureHpa, { ...byPressure.get(layer.pressureHpa), current: layer });
  }
  return [...byPressure.entries()].sort((a, b) => b[0] - a[0]).map(([pressureHpa, pair]) => ({
    pressureHpa,
    geopotentialHeightM: intervalMean(pair.previous?.geopotentialHeightM, pair.current?.geopotentialHeightM),
    temperatureC: intervalMean(pair.previous?.temperatureC, pair.current?.temperatureC),
    dewPointC: intervalMean(pair.previous?.dewPointC, pair.current?.dewPointC),
    relativeHumidityWaterPct: intervalMean(pair.previous?.relativeHumidityWaterPct, pair.current?.relativeHumidityWaterPct),
    verticalVelocityMs: intervalMean(pair.previous?.verticalVelocityMs, pair.current?.verticalVelocityMs),
    windSpeedMs: intervalMean(pair.previous?.windSpeedMs, pair.current?.windSpeedMs),
    windDirectionDeg: intervalDirectionMean(pair.previous?.windDirectionDeg, pair.current?.windDirectionDeg),
    cloudCoverPct: intervalMean(pair.previous?.cloudCoverPct, pair.current?.cloudCoverPct),
  }));
}
