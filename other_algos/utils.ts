// ============================================================
// Shared meteorological utility functions
// ============================================================

/**
 * Relative humidity from temperature and dewpoint.
 * Uses the Magnus formula approximation.
 * @param T_C  - Air temperature (°C)
 * @param Td_C - Dewpoint temperature (°C)
 * @returns RH in percent, clamped to [0, 100]
 */
export function dewpointToRH(T_C: number, Td_C: number): number {
  const a = 17.625;
  const b = 243.04;
  const alpha_T  = (a * T_C)  / (b + T_C);
  const alpha_Td = (a * Td_C) / (b + Td_C);
  return Math.max(0, Math.min(100, 100 * Math.exp(alpha_Td - alpha_T)));
}

/**
 * Wet-bulb temperature approximation.
 * Stull (2011) — accurate to ±0.35°C for typical atmospheric conditions.
 * @param T_C - Dry-bulb temperature (°C)
 * @param RH  - Relative humidity (%)
 * @returns Wet-bulb temperature (°C)
 */
export function wetBulbStull(T_C: number, RH: number): number {
  return (
    T_C * Math.atan(0.151977 * Math.pow(RH + 8.313659, 0.5)) +
    Math.atan(T_C + RH) -
    Math.atan(RH - 1.676331) +
    0.00391838 * Math.pow(RH, 1.5) * Math.atan(0.023101 * RH) -
    4.686035
  );
}

/**
 * Ice fraction for a given temperature.
 * Linearly interpolates from fully liquid (T ≥ 2°C) to fully frozen (T ≤ 0°C).
 */
export function iceFraction(T_C: number): number {
  if (T_C <= 0) return 1.0;
  if (T_C >= 2) return 0.0;
  return 1.0 - T_C / 2.0;
}

/**
 * Clamp a value to [min, max].
 */
export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}
