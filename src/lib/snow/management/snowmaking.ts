export interface SnowmakingPotential {
  favorable: boolean;
  efficiency: number;
  windLossRisk: 'low' | 'moderate' | 'high';
}

/** Potential only: this is not evidence that a snow gun actually operated. */
export function estimateSnowmakingPotential(
  wetBulbTemperatureC: number | null,
  windSpeedMs: number | null,
): SnowmakingPotential {
  const wetBulb = wetBulbTemperatureC ?? Infinity;
  const wind = Math.max(0, windSpeedMs ?? 0);
  const favorable = wetBulb <= -2.5;
  const efficiency = favorable ? Math.max(0, Math.min(1, (-wetBulb - 2) / 6)) : 0;
  return {
    favorable,
    efficiency,
    windLossRisk: wind >= 12 ? 'high' : wind >= 7 ? 'moderate' : 'low',
  };
}
