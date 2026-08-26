# Regional SLR residual calibration

MySnow retains its physical Kuchera/Cobb SLR estimate and may optionally apply a versioned multiplicative residual:

`calibrated SLR = physical SLR × exp(global + climate + H3 region + local site)`

Each child term is trained on the residual remaining after its parents and is empirically shrunk toward zero. Runtime nodes below the artifact's minimum effective sample size are skipped, so the same artifact operates worldwide with transparent fallback to broader levels. All paths retain the 3–30 physical SLR clamp.

## Runtime safety

- `DEFAULT_SLR_CALIBRATION.regionalBiasEnabled` is `false`.
- The bundled smoke artifact is synthetic and `unvalidated`; production rejects it even if the feature gate is enabled unless a test/backtest explicitly opts in.
- Invalid schema versions, missing location, unsupported physical methods, missing models, and insufficient support return identity (factor 1).
- Fixed 10:1, Open-Meteo comparison snowfall, and the experimental KRC selection are not calibrated.
- Cobb's explicit Kuchera fallback uses the Kuchera calibration model.
- Calibration happens after physical SLR estimation and before SLR/snowfall quantiles. It cannot modify QPF, precipitation phase, frozen SWE, or rain.

Diagnostics include artifact/schema/version, trained-through date, applied hierarchy, highest specificity, H3 region, climate regime, correction/log residual, raw/effective sample counts, residual uncertainty, quality tier, and an explicit no-correction reason.

## Artifact lifecycle

The offline package in `training/slr_calibration` ingests normalized CSV or Parquet storm events, validates QC, fits weighted hierarchical log residuals with empirical-Bayes shrinkage, and exports deterministic JSON. Training artifacts must remain `unvalidated` until station- and storm-held-out backtests show improvement, geographic coverage is reviewed, licenses are cleared, and the exact production physical-baseline predictors have been paired with observations.

The large source data directory is gitignored at `data/slr-training-data/`; only synthetic fixtures and the synthetic smoke artifact are committed.
