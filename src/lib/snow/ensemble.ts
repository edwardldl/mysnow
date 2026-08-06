import { calculateSnowfall } from './snowfall';
import { MIN_ACCUMULATING_SWE_MM, type EnsembleSnowfallResult, type SlrMethod, type SnowfallInput, type SnowfallResult } from './types';

function quantile(values: number[], probability: number): number {
  if (values.length === 0) return 0;
  const index = (values.length - 1) * probability;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  return values[lower] + (values[upper] - values[lower]) * (index - lower);
}

/** Process each member independently; never average nonlinear inputs first. */
export function calculateEnsembleSnowfall(
  members: Array<SnowfallInput | null>,
  method: SlrMethod,
): EnsembleSnowfallResult {
  const memberResults: SnowfallResult[] = [];
  let missingMemberCount = 0;
  for (const member of members) {
    if (member === null) {
      missingMemberCount += 1;
      continue;
    }
    memberResults.push(calculateSnowfall(member, method));
  }
  return summarizeEnsembleSnowfall(memberResults, missingMemberCount);
}

export function summarizeEnsembleSnowfall(
  memberResults: SnowfallResult[],
  missingMemberCount = 0,
): EnsembleSnowfallResult {
  const snowfallValues = memberResults.map(result => result.freshSnowCm).sort((a, b) => a - b);
  const count = memberResults.length;
  return {
    memberResults,
    p10SnowCm: quantile(snowfallValues, 0.1),
    p25SnowCm: quantile(snowfallValues, 0.25),
    medianSnowCm: quantile(snowfallValues, 0.5),
    p75SnowCm: quantile(snowfallValues, 0.75),
    p90SnowCm: quantile(snowfallValues, 0.9),
    meanSnowCm: count === 0 ? 0 : snowfallValues.reduce((sum, value) => sum + value, 0) / count,
    probabilitySnow: count === 0 ? 0 : memberResults.filter(result => result.frozenSweMm > MIN_ACCUMULATING_SWE_MM).length / count,
    probabilitySlrAbove15: count === 0 ? 0 : memberResults.filter(result => (result.freshSlr ?? 0) > 15).length / count,
    probabilitySnowAbove10Cm: count === 0 ? 0 : memberResults.filter(result => result.freshSnowCm > 10).length / count,
    missingMemberCount,
  };
}
