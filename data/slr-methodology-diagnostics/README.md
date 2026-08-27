# Regional SLR methodology diagnostics

> **Research only · unvalidated · no artifact produced · production remains disabled.**

This deterministic pure-stdlib diagnostic compares the retained Kuchera baseline, a shrunk residual intercept, a thermal-profile ridge, and a full-available-feature ridge. Shared hyperparameters are selected exclusively inside the Rockies, Sierra, and Swiss training partitions using grouped CV. Every fixed regional holdout is evaluated once only after choices are frozen; Northeast is excluded from all selection.

## Exact retained splits

| Region | Train constraint | Train events | Test constraint | Test events |
|---|---:|---:|---:|---:|
| Rockies | WY2024 + exact stations | 103 | WY2025 + exact stations | 21 |
| Sierra | WY2023 + exact stations | 206 | WY2024 + exact stations | 19 |
| Swiss | 2021-11-02 to 2021-12-30 + exact stations | 165 | 2022-01-05 to 2022-04-30 + exact stations | 125 |
| Northeast | WY2024 + exact stations | 60 | WY2025 + exact stations | 80 |

## U.S. label provenance warning

> **Authorization unresolved.** All selected U.S. labels are source-N/CoCoRaHS: Rockies 148/148 N; Sierra 197 SNOW:N/WESF:N plus 48 SNOW:7/WESF:N; Northeast 179/179 N. NOAA documentation defines source N as CoCoRaHS. The later strict broad-U.S. audit excludes source N because underlying reuse and commercial-training authorization was not positively established. This does not assert illegality; authorization is unresolved. U.S. results therefore remain research-only even where physical pairs pass non-circular quality control.

| Region | Selected labels | Source-flag counts |
|---|---:|---|
| Rockies | 148 | N: 148 |
| Sierra | 245 | SNOW:7;WESF:N: 48, SNOW:N;WESF:N: 197 |
| Northeast | 179 | SNOW:N;WESF:N: 179 |

## Training-only grouped-CV selection

Candidate scores use no fixed-holdout events. Each region combines deterministic leave-one-station-out CV with up to five contiguous chronological blocks of complete storms; regions and fold families receive equal weight.

| Candidate | Balance | Hyperparameter | Macro CV MAE |
|---|---|---:|---:|
| Shrunk intercept | station | shrinkage 64.0 | 3.856 |
| Thermal-profile ridge | station | alpha 100.0 | 3.554 |
| Full-feature ridge | event | alpha 10.0 | 3.312 |

Full candidate-grid grouped-CV scores are stored separately from fixed-holdout outcomes in `diagnostics.json`.

## Leave-one-region-out global-prior check

A station-balanced log-residual intercept was fitted on the other development-region training sets and applied in the single post-selection holdout pass. Northeast was excluded from every fit and selection decision.

| Target | Log offset | Baseline MAE | Global-prior MAE | Baseline RMSE | Global-prior RMSE |
|---|---:|---:|---:|---:|---:|
| Rockies | -0.0638 | 5.180 | 4.368 | 6.084 | 5.212 |
| Sierra | -0.0769 | 3.767 | 3.567 | 4.675 | 4.529 |
| Swiss | -0.1387 | 5.186 | 4.039 | 6.303 | 5.014 |
| Northeast (final) | -0.0792 | 4.913 | 5.081 | 7.048 | 7.344 |

**Decision: no global fallback.** The prior transfers on development-region holdouts but fails on untouched Northeast; do not use a global fallback.

## Fixed-holdout outcomes

Each row below comes from the one post-selection evaluation of that regional holdout.

| Region | Method | MAE | Bias | RMSE |
|---|---|---:|---:|---:|
| Rockies | baseline | 5.180 | 4.868 | 6.084 |
| Rockies | intercept | 3.491 | 2.494 | 4.420 |
| Rockies | ridgeThermal | 2.851 | 0.164 | 3.645 |
| Rockies | ridgeFull | 2.928 | 0.475 | 3.866 |
| Sierra | baseline | 3.767 | 0.374 | 4.675 |
| Sierra | intercept | 3.742 | -0.110 | 4.661 |
| Sierra | ridgeThermal | 3.939 | 1.692 | 4.485 |
| Sierra | ridgeFull | 2.694 | 0.651 | 3.331 |
| Swiss | baseline | 5.186 | 3.927 | 6.303 |
| Swiss | intercept | 4.887 | 3.386 | 5.981 |
| Swiss | ridgeThermal | 4.422 | 2.929 | 5.374 |
| Swiss | ridgeFull | 3.291 | 1.678 | 4.067 |
| Northeast | baseline | 4.913 | -2.042 | 7.048 |
| Northeast | intercept | 4.931 | -2.119 | 7.070 |
| Northeast | ridgeThermal | 4.487 | -2.784 | 6.904 |
| Northeast | ridgeFull | 4.147 | -2.131 | 6.516 |

### Improved fixed-holdout slices vs baseline

Counts cover station, complete storm, available geographic-slice, and elevation-band slices. Per-slice metrics are in `diagnostics.json`.

| Region | Method | Station | Storm | Geographic | Elevation |
|---|---|---:|---:|---:|---:|
| Rockies | intercept | 3/3 | 12/15 | n/a | 3/3 |
| Rockies | ridgeThermal | 3/3 | 11/15 | n/a | 3/3 |
| Rockies | ridgeFull | 3/3 | 11/15 | n/a | 3/3 |
| Rockies | globalPrior | 3/3 | 12/15 | n/a | 3/3 |
| Sierra | intercept | 2/3 | 4/11 | n/a | 2/3 |
| Sierra | ridgeThermal | 1/3 | 6/11 | n/a | 1/3 |
| Sierra | ridgeFull | 3/3 | 8/11 | n/a | 3/3 |
| Sierra | globalPrior | 2/3 | 6/11 | n/a | 2/3 |
| Swiss | intercept | 9/12 | 12/15 | n/a | 2/3 |
| Swiss | ridgeThermal | 10/12 | 11/15 | n/a | 3/3 |
| Swiss | ridgeFull | 9/12 | 14/15 | n/a | 3/3 |
| Swiss | globalPrior | 9/12 | 13/15 | n/a | 2/3 |
| Northeast | intercept | 2/4 | 9/27 | 2/4 | 0/2 |
| Northeast | ridgeThermal | 4/4 | 20/27 | 4/4 | 2/2 |
| Northeast | ridgeFull | 4/4 | 21/27 | 4/4 | 2/2 |
| Northeast | globalPrior | 1/4 | 10/27 | 1/4 | 0/2 |

## Northeast root-cause diagnostic

- Train log(obs/baseline): mean `-0.0183`, median `-0.1426`; station-mean range `-0.168` to `+0.065`.
- Test log(obs/baseline): mean `+0.0984`, median `+0.1187`; held-out station means: `US1NHCS0019` -0.070, `US1NYDL0032` +0.284, `US1NYWR0021` -0.088, `US1VTFR0030` +0.210.
- Existing evaluation hierarchy paths: `global>climate` for 62/80 events and `global>climate>region` for 18/80.
- The aggregate log-residual changes sign from training to test. Training-station means occupy a comparatively narrow negative-to-near-zero range, while held-out station means span both negative and strongly positive values. The existing hierarchy therefore applies a near-uniform regional correction with the wrong aggregate sign and cannot represent held-out-station heterogeneity.

## Northeast uncertainty diagnostics

The JSON report also contains deterministic station- and storm-cluster bootstrap 95% intervals for every fixed-holdout method and MAE delta in Rockies, Sierra, Swiss, and Northeast.

### Station-cluster bootstrap (95%, 2000 replicates)

- `baseline.mae`: [3.542, 5.980]
- `globalPrior.mae`: [3.470, 6.334]
- `globalPrior.maeDeltaVsBaseline`: [-0.072, 0.456]
- `intercept.mae`: [3.540, 6.012]
- `intercept.maeDeltaVsBaseline`: [-0.002, 0.042]
- `ridgeFull.mae`: [2.586, 5.361]
- `ridgeFull.maeDeltaVsBaseline`: [-0.956, -0.619]
- `ridgeThermal.mae`: [2.853, 5.757]
- `ridgeThermal.maeDeltaVsBaseline`: [-0.692, -0.209]

### Storm-cluster bootstrap (95%, 2000 replicates)

- `baseline.mae`: [3.585, 6.659]
- `globalPrior.mae`: [3.698, 6.825]
- `globalPrior.maeDeltaVsBaseline`: [-0.165, 0.451]
- `intercept.mae`: [3.606, 6.676]
- `intercept.maeDeltaVsBaseline`: [-0.008, 0.039]
- `ridgeFull.mae`: [2.945, 5.708]
- `ridgeFull.maeDeltaVsBaseline`: [-1.326, -0.308]
- `ridgeThermal.mae`: [3.161, 6.205]
- `ridgeThermal.maeDeltaVsBaseline`: [-0.799, -0.139]

### Empirical grouped-crossfit interval heuristics (80% target)

> **No conformal guarantee.** Pooled event residuals from grouped cross-fits are used only as an empirical interval heuristic. They do not establish event exchangeability, cluster-level exchangeability, group-conditional coverage, or a finite-sample conformal guarantee.

- `intercept` / `leaveStationOut`: empirical radius 5.511, Northeast coverage 0.713, width 11.023.
- `intercept` / `leaveStormOut`: empirical radius 5.543, Northeast coverage 0.713, width 11.086.
- `ridgeThermal` / `leaveStationOut`: empirical radius 5.171, Northeast coverage 0.750, width 10.343.
- `ridgeThermal` / `leaveStormOut`: empirical radius 5.525, Northeast coverage 0.775, width 11.050.
- `ridgeFull` / `leaveStationOut`: empirical radius 5.978, Northeast coverage 0.812, width 11.955.
- `ridgeFull` / `leaveStormOut`: empirical radius 5.760, Northeast coverage 0.787, width 11.519.

## Predictor discipline

Only retained temperature2mC, freezingLevelHeightM, pressure-level temperature/geopotential, precipitationMm, snowfallCm, elevation, baseline SLR and SWE proxy fields are used; no humidity or wind is synthesized.

- Thermal ablation: temperature2m, freezing-level, and pressure-level temperature/geopotential summaries only.
- Full ridge: thermal profile plus retained precipitation, snowfall, elevation, baseline SLR, SWE proxy, and profile-hour count.

## Limitations

- Retrospective diagnostic on four retained regional datasets; not a production validation.
- Hyperparameters are shared, but coefficients are fitted separately within each region.
- Contiguous storm CV holds out complete chronological storm blocks but is not forward-chaining.
- Cluster bootstrap intervals describe sampling variability across the respective observed station or storm clusters within each fixed holdout only.
- Grouped-crossfit intervals are empirical heuristics without finite-sample, exchangeability, or group-conditional coverage guarantees.
- No artifact is emitted and production calibration remains disabled.

Full candidate-grid CV scores, fixed-holdout outcomes, source hashes, exact station lists, bootstrap intervals, and empirical interval details are in [`diagnostics.json`](diagnostics.json).
The regional failure taxonomy, literature review, and implications are in [`RESEARCH.md`](RESEARCH.md).
