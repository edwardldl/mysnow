import type { NormalizedProfile, PressureLayer } from '../types';

const GRAVITY_MS2 = 9.80665;

export interface MoistureTransport {
  ivtXKgM1S1: number;
  ivtYKgM1S1: number;
  ivtMagnitudeKgM1S1: number;
  upslopeFluxKgM1S1: number;
  validLayerCount: number;
}

function specificHumidityFromDewPoint(dewPointC: number, pressureHpa: number): number {
  const vapourPressureHpa = 6.112 * Math.exp(17.67 * dewPointC / (dewPointC + 243.5));
  return 0.622 * vapourPressureHpa / Math.max(1, pressureHpa - 0.378 * vapourPressureHpa);
}

function windComponents(speedMs: number, windFromDeg: number): { u: number; v: number } {
  const radians = windFromDeg * Math.PI / 180;
  return { u: -speedMs * Math.sin(radians), v: -speedMs * Math.cos(radians) };
}

function pressureThicknessHpa(levels: PressureLayer[], index: number): number {
  if (levels.length === 1) return 0;
  if (index === 0) return Math.abs(levels[0].pressureHpa - levels[1].pressureHpa) / 2;
  if (index === levels.length - 1) return Math.abs(levels[index - 1].pressureHpa - levels[index].pressureHpa) / 2;
  return Math.abs(levels[index - 1].pressureHpa - levels[index + 1].pressureHpa) / 2;
}

/** Pressure-coordinate IVT and positive cross-barrier moisture flux. */
export function calculateMoistureTransport(
  profile: NormalizedProfile,
  ridgeNormalTowardDeg: number,
): MoistureTransport {
  const levels = profile.aboveGroundLevels.filter(level => level.dewPointC !== null
    && level.windSpeedMs !== null
    && level.windDirectionDeg !== null)
    .sort((a, b) => b.pressureHpa - a.pressureHpa);
  const normalRadians = ridgeNormalTowardDeg * Math.PI / 180;
  const normalX = Math.sin(normalRadians);
  const normalY = Math.cos(normalRadians);
  let ivtXKgM1S1 = 0;
  let ivtYKgM1S1 = 0;
  let upslopeFluxKgM1S1 = 0;

  levels.forEach((level, index) => {
    const q = specificHumidityFromDewPoint(level.dewPointC!, level.pressureHpa);
    const wind = windComponents(level.windSpeedMs!, level.windDirectionDeg!);
    const pressureThicknessPa = pressureThicknessHpa(levels, index) * 100;
    const scale = q * pressureThicknessPa / GRAVITY_MS2;
    ivtXKgM1S1 += wind.u * scale;
    ivtYKgM1S1 += wind.v * scale;
    upslopeFluxKgM1S1 += Math.max(0, wind.u * normalX + wind.v * normalY) * scale;
  });

  return {
    ivtXKgM1S1,
    ivtYKgM1S1,
    ivtMagnitudeKgM1S1: Math.hypot(ivtXKgM1S1, ivtYKgM1S1),
    upslopeFluxKgM1S1,
    validLayerCount: levels.length,
  };
}
