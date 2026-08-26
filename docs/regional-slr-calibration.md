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

Large/raw source data and API caches are gitignored. Only redistributable small fixtures, reusable code, documentation, and an explicitly `unvalidated` research artifact that passes its stated holdout gate may be committed.

## CSSL Kuchera case study (research only)

The first real-label experiment uses UC Berkeley Central Sierra Snow Laboratory (CSSL) Daily Snow and Meteorological Data, Dryad version 13, DOI `10.6078/D1941T` (CC0 1.0). The frozen archive was retrieved on 2026-08-26:

- complete-version download: `https://datadryad.org/api/v2/versions/399062/download`
- archive SHA-256: `823b47fd52935ccffa93c75e1f838a76dc35e8ec58b2b38b0ec54995a00434a7`
- 56 files, 987,904 uncompressed bytes; every file matched the Dryad manifest
- 54 water-year CSVs: WY1971–2019 and WY2021–2025 (WY2020 is absent)
- 19,724 daily rows from 1970-10-01 through 2025-09-30

### Label policy and import result

A label is accepted only when the row has at least 2.5 cm of `New Snow`, at least 2.5 mm of fresh SWE calculated from `24-hour Total Precip × % of Precip as Snow`, exactly 100% snow phase, computed SLR strictly inside `(2, 50)`, and no unsafe remark. Total snowpack depth and total-pack SWE are never used to derive a fresh SLR label. Version 13 has no published snow:precipitation-ratio column, so no such comparison was fabricated.

The frozen import accepted **1,163** events in **686** contiguous storm groups from 1987-11-30 through 2025-04-27 and rejected 18,561 rows with recorded reason codes. Most rejections were missing snow fraction (15,035), no new snow (1,438), or mixed/uncertain phase (566).

### Event-time physical baseline

Recent accepted events were paired with Open-Meteo Historical Forecast hourly snowfall and pressure-level temperatures. The bridge reuses MySnow's production `(t-1,t]` interval-profile averaging and `normalizeProfile` path, then calls the exact `estimateFreshSnowSlr(..., 'kuchera', ..., { enabled: false })` implementation for each modeled-snowfall interval. The daily physical baseline is weighted by model precipitation in those snow intervals (a frozen-SWE timing proxy because accepted CSSL labels are exactly 100% snow), with modeled snowfall used only when positive precipitation is absent. Regional correction is explicitly disabled. Production's 100 m elevation tolerance is retained: 700/600/500 hPa contributed in all 342 evaluated intervals, while 800 hPa contributed in 6; lower requested levels remained underground. The sparse profile is accepted because MySnow Kuchera uses the maximum temperature among valid above-ground levels. It cannot support true Cobb because dew point and vertical velocity are absent.

The study uses a fixed-PST calendar day as a research proxy. CSSL's twice-daily snowboard measurement and 24-hour precipitation timing are not documented precisely enough to guarantee identical overlap. Open-Meteo's archive is recent and model-dependent, the `best match` response does not identify the exact contributing model, and hosted API commercial terms are not cleared by this work.

### Leakage-safe evaluation

Complete `storm_group_id` groups are assigned to one side of a chronological water-year split. Only water years strictly earlier than the held-out year may train the hierarchy. The comparison reports bias, MAE, median absolute error, RMSE, and approximate P10–P90 interval coverage for the unchanged physical baseline versus calibrated predictions.

Frozen run result: 55 accepted CSSL labels occurred in the requested two-water-year period; 35 had aligned model snowfall and a usable above-ground profile, while 20 were rejected for no modeled-snowfall alignment. No requested month was unavailable and no event was rejected for an incomplete local day. Training used **21 events / 15 storm groups from WY2024**; the complete **WY2025 holdout contained 14 events / 11 storm groups**. Baseline versus calibrated holdout metrics were:

| Metric (SLR units except coverage) | Physical baseline | Calibrated |
| --- | ---: | ---: |
| Bias | 6.626 | 1.717 |
| MAE | 7.302 | 4.751 |
| Median absolute error | 6.874 | 5.387 |
| RMSE | 8.365 | 5.340 |
| Approximate P10–P90 coverage | — | 12/14 (85.7%) |

The candidate passed the narrow commit gate: MAE improved by 2.551 SLR units and median absolute error improved by 1.487. It was committed only as `training/slr_calibration/artifacts/regional-slr-research-cssl-kuchera-v1.json`, with `status: "unvalidated"` and 21 training events. This gate does not authorize production use.

This is one fixed site and a temporal holdout only. It provides no Sierra-wide spatial validation and no worldwide evidence. The artifact remains `unvalidated`, production feature gates remain off, and normal runtime rejects it.

### License-blocked reconnaissance

A one-day CoCoRaHS export was inspected only to verify schema and direct fresh-depth/fresh-SWE availability. Three direct pairs were present, but commercial training and redistribution rights were not established. Those records remain gitignored and were not used for fitting or evaluation.
