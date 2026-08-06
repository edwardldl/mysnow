export type ModelId =
  | 'hrrr'
  | 'ecmwf'
  | 'gfs'
  | 'nam'
  | 'nbm'
  | 'icon_global'
  | 'ecmwf_aifs'
  | 'ecmwf_aifs_ensemble'
  | 'best_match';

export interface ModelPools {
  qpf: ModelId[];
  phase: ModelId[];
  atmosphericProfile: ModelId[];
  shortRangeEnsemble: ModelId[];
  mediumRangeEnsemble: ModelId[];
}

/** Explicit sources used by the first production blend. */
export const DEFAULT_MODEL_POOLS: Readonly<ModelPools> = {
  qpf: ['hrrr', 'ecmwf'],
  phase: ['hrrr', 'ecmwf'],
  atmosphericProfile: ['hrrr', 'ecmwf'],
  shortRangeEnsemble: ['ecmwf_aifs_ensemble'],
  mediumRangeEnsemble: ['ecmwf_aifs_ensemble'],
};

export const DEFAULT_FORECAST_MODE = 'hrrr_ecmwf';
