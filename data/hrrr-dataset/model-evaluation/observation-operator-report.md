# Step 4: can an observation operator recover board SLR from SNOTEL differencing?

Research-only. No artifact was retained, no gate was changed, and nothing here modifies the production calibration configuration. Every number below is produced by `training/slr_calibration/src/slr_calibration/observation_operator.py` (`investigate-observation-operator` CLI) and is also in `observation_operator.json` next to this file.

**Setup.** The weak-label investigation showed daily SNOTEL differencing at CSSL (SNOTEL `428:CA:SNTL`, co-located with the CSSL board) yields SLR a factor 1.515 below board SLR (mean log 0.415), decomposed into settlement of the measured depth (log 0.236) and the pillow gaining ~20% more SWE than the gauge reports (log -0.179). This step asks whether a forward observation operator -- hourly deposition at a fitted fresh density, Anderson (1976)-style viscous compaction of the new layer under its own load and of the old pack under self-load plus the new load, and a fitted pillow-to-gauge capture factor gamma -- fitted ONLY by matching simulated vs observed hourly depth trajectories over storm windows, can invert SNOTEL differencing back to board-equivalent SLR on held-out winters. Board labels are never used in fitting.

## Verdict summary

- **CSSL, pooled held-out winters (each board day predicted by an operator fitted on the other winter only):** naive daily differencing MAE 6.12 (n=68); the trivial x1.515 rescale MAE 6.86; operator-recovered MAE 4.73 (n=83), bias -0.50. The operator beats naive differencing and beats the trivial constant rescale.
- **Transfer probe (frozen CSSL operator, no refitting):** recovered MAE 3.72 (n=151) vs naive 5.03 (n=120) and trivial 6.22, pooled over the probe pairs. See the per-station table before trusting the pooled number.

## Data and alignment

CSSL hourly series: 24817 hours over ['2023-09-01T00:00:00', '2026-07-01T00:00:00'], SWE coverage 0.66 overall and 0.96 over Nov-Apr (AWDB reports no hourly pillow values through the dry summer season). CSSL board days in the raw release: 432 days, 86 board events (WY2024-2025; the v13 release has no WY2026 file).

Storm windows segmented from the hourly series, by water year of window start: WY2024: 7, WY2025: 14, WY2026: 9.

Alignment verification against the investigation's pairing (CSSL date D vs the daily dataset's row D-1, whose stored delta_swe_mm is compared with the hourly window gain at each candidate): -1d 00:00: mean abs diff 19.40 mm (n=86); -1d 08:00: mean abs diff 16.85 mm (n=85); +0d 00:00: mean abs diff 0.00 mm (n=86); +0d 08:00: mean abs diff 9.02 mm (n=85). The (0d 00:00) window -- [00:00 D-1, 00:00 D] -- reproduces the stored deltas exactly, so the investigation's pairing and this study's hourly windows are the same 24 h; the dataset's internal date convention sits one day ahead of the naive reading of the AWDB daily response.

Specification note: a first run of this protocol used tighter parameter bounds (log eta <= 22, rho0 temperature slope <= 12) and put the new-layer viscosity and the density temperature slope on their bounds in the WY2025 and final fits. The protocol was rerun once with widened bounds (log eta <= 28, slope <= 25); every number below is from the widened run. The widening changed no conclusion: pooled held-out recovered MAE was 4.71 in the bounded run and 4.73 in the widened run, and the transfer pooled recovered MAE was 3.72 in both.

Alignment selection for fitWY2024_validateWY2025 (fit years only; window = 24 h ending at the stated local time relative to the CSSL date):

| Window end | n | corr(window dSWE, board fresh SWE) |
|---|---|---|
| -1d 00:00 | 23 | 0.578 |
| -1d 08:00 | 31 | 0.608 |
| +0d 00:00 | 42 | 0.838 |
| +0d 08:00 | 42 | 0.739 |

Alignment selection for fitWY2025_validateWY2024 (fit years only; window = 24 h ending at the stated local time relative to the CSSL date):

| Window end | n | corr(window dSWE, board fresh SWE) |
|---|---|---|
| -1d 00:00 | 17 | -0.139 |
| -1d 08:00 | 23 | 0.337 |
| +0d 00:00 | 36 | 0.793 |
| +0d 08:00 | 35 | 0.713 |

## Fitted operator parameters

| Fit | Windows | Depth RMSE (mm) | gamma | rho0 intercept | rho0 /degC | log eta new | log eta old |
|---|---|---|---|---|---|---|---|
| fitWY2024_validateWY2025 | 7 | 172.5 | 1.0852 | 87.718 | 8.1043 | 27.9977 | 15.5703 |
| fitWY2025_validateWY2024 | 14 | 80.6 | 1.6157 | 62.222 | 25.0 | 27.8319 | 17.2231 |
| final (WY2024+2025) | 21 | 138.8 | 1.2874 | 105.277 | 25.0 | 28.0 | 16.8014 |

Window-resample bootstrap of the final fit (30 reps), 5-95% intervals: gamma 1.0927-1.6872 (median 1.2549); rho0 intercept 46.9454-195.9019; log eta new 23.2687-28.0; log eta old 16.176-17.1367.

Sensitivity to the fixed viscosity density exponent k_eta (default 0.019): k=0.01: gamma 1.2052, depth RMSE 143.2 mm; k=0.03: gamma 1.3505, depth RMSE 138.3 mm.

Per-window trajectory fit (final fit): median window depth RMSE 74.5 mm; worst windows: 2024-02-25 (224 h, gain 274.3 mm): 273.2 mm; 2024-02-16 (223 h, gain 116.8 mm): 267.7 mm; 2024-05-03 (84 h, gain 48.3 mm): 205.6 mm; 2025-01-30 (91 h, gain 38.1 mm): 167.9 mm; 2025-02-10 (105 h, gain 114.3 mm): 162.5 mm. The trajectory fit is much weaker than the SLR recovery it supports: over multi-day windows the lumped old pack drifts (no melt, no water routing), which inflates depth RMSE without bearing directly on the 24 h new-layer reconstruction.

For reference, the investigation's same-day decomposition implies a pillow/gauge ratio of e^0.179 = 1.196. Gamma here is identified only through the depth trajectory (deposition scale vs load-driven compaction), so agreement or disagreement with 1.196 is itself a result.

Identification limits, stated plainly: the bootstrap distribution of log eta (new layer) piles up at its upper bound -- the hourly depth data do not identify a finite new-layer viscosity, so in the fitted operator the new layer densifies almost entirely through the fixed metamorphism branch and the temperature-dependent deposition density (whose slope also runs to its bound, i.e. the data want a steep rise of fresh density toward 0 C). The old pack's viscosity is the one compaction parameter the trajectories do identify (bootstrap intervals are tight, and removing old-pack settlement from the fit degrades both the trajectory fit and the recovery bias -- see the fold tables). Treat the fitted values as an effective reduced form of the Anderson structure, not as measurements of Anderson's constants.

## CSSL held-out validation

Each fold fits on one winter's storm windows and is scored on the other winter's board-event days. 'Recovered (naive subset)' restricts the operator to the days where naive differencing is defined (delta-depth > 0), for a like-for-like comparison; the operator also returns values on days when differencing fails outright.

### fitWY2024_validateWY2025

| Method | n | MAE | Bias | Median abs err |
|---|---|---|---|---|
| Naive daily differencing | 32 | 5.38 | -2.00 | 4.38 |
| Trivial x1.515 | 32 | 6.53 | 2.25 | 4.56 |
| Operator-recovered | 38 | 3.78 | -0.49 | 3.46 |
| Operator-recovered (naive subset) | 32 | 3.90 | -1.18 | 3.56 |
| Ablation: gamma = 1 (no capture correction) | 38 | 3.78 | -0.49 | 3.46 |
| Ablation: no new-layer compaction at inversion | 38 | 4.92 | 3.13 | 4.76 |
| Ablation: refit without old-pack settlement | 38 | 4.09 | -2.19 | 3.29 |

Reproduction check: mean log(board / naive) on this fold = 0.231 (investigation, QC-matched population: 0.415). Board days where naive differencing is undefined (delta-depth <= 0): 6 of 38; on those days the operator's MAE is 3.16 (n=6).

### fitWY2025_validateWY2024

| Method | n | MAE | Bias | Median abs err |
|---|---|---|---|---|
| Naive daily differencing | 36 | 6.78 | -5.00 | 3.44 |
| Trivial x1.515 | 36 | 7.15 | -0.98 | 4.13 |
| Operator-recovered | 45 | 5.53 | -0.51 | 4.60 |
| Operator-recovered (naive subset) | 36 | 5.82 | -1.05 | 4.58 |
| Ablation: gamma = 1 (no capture correction) | 46 | 5.51 | -0.39 | 4.62 |
| Ablation: no new-layer compaction at inversion | 45 | 10.89 | 7.84 | 11.57 |
| Ablation: refit without old-pack settlement | 45 | 5.59 | -1.17 | 4.30 |

Reproduction check: mean log(board / naive) on this fold = 0.554 (investigation, QC-matched population: 0.415). Board days where naive differencing is undefined (delta-depth <= 0): 10 of 46; on those days the operator's MAE is 4.37 (n=9).

### Pooled held-out (both folds)

| Method | n | MAE | Bias | Median abs err |
|---|---|---|---|---|
| Naive daily differencing | 68 | 6.12 | -3.59 | 3.75 |
| Trivial x1.515 | 68 | 6.86 | 0.54 | 4.34 |
| Operator-recovered | 83 | 4.73 | -0.50 | 4.16 |

### What makes the recovery work (reading the ablations)

Two structural facts explain the result. First, the capture factor gamma nearly cancels in the recovered SLR: the recovered quantity is a ratio (simulated new-layer depth / deposited SWE) and both sides scale with 1/gamma, so gamma survives only through load-dependent compaction -- which is why the gamma = 1 ablation is nearly identical to the full operator while the fitted gamma itself swings between winters (the fold estimates bracket the investigation's 1.196). The pillow-vs-gauge SWE term that the investigation measured (log -0.179) is therefore NOT what an SLR recovery needs to fix; it matters for absolute SWE, not for its ratio to depth. Second, the active ingredient is new-layer compaction: switching it off at inversion drives the bias strongly positive (the simulated layer stays at deposition density, so recovered SLR overshoots), while the full operator's bias is near zero. Physically, the SNOTEL depth sensor sees the new layer already partly compacted at measurement time; reconstructing that compaction is what closes most of the board-vs-differenced gap. Old-pack settlement matters mainly inside the fit (it keeps storm-time depth decline from being loaded onto the new layer's parameters) -- compare the refit ablation rows and the fit RMSEs.

## Transfer probe (frozen CSSL operator, no refitting)

Probe pairs were chosen mechanically: per region, the two SNOTEL stations closest (<= 10 km) to an admitted step-1 gold station with >= 25 gold events in WY2024-2026. Each gold event's 24 h window ends at the gold station's own local-standard observation time. Board truth is the gold label's observed SLR (GHCN SNOW + WESF).

| Pair | Dist (km) | dElev (m) | Gold events | Paired | Naive MAE (n) | Trivial MAE | Recovered MAE (n) |
|---|---|---|---|---|---|---|---|
| 834:CA:SNTL vs US1CANV0051 | 5.6 | 4 | 54 | 51 | 5.16 (40) | 6.77 | 3.05 (51) |
| 724:CA:SNTL vs USC00048760 | 6.8 | 380 | 48 | 48 | 4.88 (46) | 6.25 | 3.23 (47) |
| 938:CO:SNTL vs US1COPK0015 | 9.6 | 234 | 45 | 29 | 4.12 (14) | 4.46 | 3.92 (26) |
| 802:CO:SNTL vs US1COSU0008 | 1.8 | 116 | 27 | 27 | 5.78 (20) | 6.27 | 5.63 (27) |

Pooled: naive MAE 5.03 (n=120), trivial 6.22, recovered 3.72 (n=151), recovered bias -2.30.

## Caveats

- CSSL board SWE is precipitation x snow fraction from the laboratory's own gauge, not a weighed core; 'board-equivalent' inherits that definition. The CSSL release (v13) contains no WY2026 board record, so the winter cross-validation is WY2024 <-> WY2025 only.
- Gamma and fresh density are partially degenerate in depth-only fitting (the trajectory identifies deposition/density jointly; gamma separately enters through load-driven compaction). The bootstrap intervals, not the point estimates, are the honest statement of what the hourly depth signal identifies.
- Hourly SNWD is quantized to whole inches; sub-quantization settlement within a storm is invisible, which bounds how well any hourly operator can do at CSSL.
- The operator ignores melt, liquid water, and wind scour inside windows; windows are gain-dominated by construction (the same cold-storm censoring the investigation documented as H5 applies to the fitting population).
- Transfer pairs are near but not co-located (up to ~10 km, up to ~380 m elevation difference): the probe tests the operator under realistic label-transfer conditions, including genuine storm differences between the two sites, which count against it.
