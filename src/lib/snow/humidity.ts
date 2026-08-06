/** Saturation vapour pressure over liquid water in hPa (Bolton-style form). */
export function saturationVaporPressureWaterHpa(temperatureC: number): number {
  return 6.112 * Math.exp((17.67 * temperatureC) / (temperatureC + 243.5));
}

/** Saturation vapour pressure over ice in hPa. */
export function saturationVaporPressureIceHpa(temperatureC: number): number {
  return 6.112 * Math.exp((22.46 * temperatureC) / (temperatureC + 272.62));
}

export function relativeHumidityFromDewPoint(temperatureC: number, dewPointC: number): number {
  return Math.max(0, Math.min(150,
    100 * saturationVaporPressureWaterHpa(dewPointC) / saturationVaporPressureWaterHpa(temperatureC),
  ));
}

export function relativeHumidityIceFromDewPoint(temperatureC: number, dewPointC: number): number {
  return Math.max(0, Math.min(200,
    100 * saturationVaporPressureWaterHpa(dewPointC) / saturationVaporPressureIceHpa(temperatureC),
  ));
}

/** Stull's approximation is stable for the ranges used by the phase fallback. */
export function wetBulbTemperatureC(temperatureC: number, relativeHumidityPct: number): number {
  const rh = Math.max(1, Math.min(100, relativeHumidityPct));
  return temperatureC * Math.atan(0.151977 * Math.sqrt(rh + 8.313659))
    + Math.atan(temperatureC + rh)
    - Math.atan(rh - 1.676331)
    + 0.00391838 * Math.pow(rh, 1.5) * Math.atan(0.023101 * rh)
    - 4.686035;
}

export function wetBulbFromTemperatureAndDewPoint(
  temperatureC: number | null,
  dewPointC: number | null,
  relativeHumidityPct: number | null,
): number | null {
  if (temperatureC === null) return null;
  const humidity = typeof relativeHumidityPct === 'number' && Number.isFinite(relativeHumidityPct)
    ? relativeHumidityPct
    : (dewPointC === null ? null : relativeHumidityFromDewPoint(temperatureC, dewPointC));
  return humidity === null ? null : wetBulbTemperatureC(temperatureC, humidity);
}
