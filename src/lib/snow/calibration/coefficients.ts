/**
 * Versioned defaults for downstream SLR modifiers. They are disabled until a
 * documented backtest calibrates them; keeping them here prevents algorithms
 * from silently changing QPF or embedding untracked constants.
 */
export interface SlrCalibration {
  version: string;
  trainedThrough: string | null;
  modifiersEnabled: boolean;
  meltCoefficient: number;
  surfaceWindCoefficient: number;
  shearCoefficient: number;
  rimingCoefficient: number;
}

export const DEFAULT_SLR_CALIBRATION: SlrCalibration = {
  version: 'unvalidated-v0',
  trainedThrough: null,
  modifiersEnabled: false,
  meltCoefficient: 0.00002,
  surfaceWindCoefficient: 0.035,
  shearCoefficient: 0,
  rimingCoefficient: 0,
};
