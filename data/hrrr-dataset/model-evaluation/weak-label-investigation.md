# Why weak-label SLR corrections fail on direct labels

Diagnostic investigation, research-only. No artifact was retained, no gate was changed, and nothing here modifies the production calibration configuration. Every number below is produced by `training/slr_calibration/src/slr_calibration/weak_label_investigation.py` (`investigate-weak-labels` CLI) and is also in `weak_label_investigation.json` next to this file.

**Setup.** Candidate A (the existing hierarchical EB log-residual trainer) was refit for the Colorado Ranges and Sierra Nevada regions on the region-first build's exact weak training slice (WY2024-2025, SNOTEL-differenced labels at qc_weight 0.25, Python-Kuchera baselines over NOAA HRRR 00Z profiles). The build fitted but did not package these two artifacts because the section 6.4 direct-label check demoted both regions; this investigation takes those refits apart. The direct sets are the build's own: Rockies GHCN routed by state (120 Colorado events) and Sierra GHCN (225) + CSSL (35), whose baselines are production TypeScript Kuchera over Open-Meteo best-match profiles. Candidate B is out of scope: it cannot be applied to the direct sets (no stored portable features), and the demotion was incurred by A.

## 0. Reproduction check (the refits are the build's fits)

| Region | Weak WY26 MAE (refit / published) | Direct MAE (refit / published) | Direct Kuchera MAE (refit / published) |
|---|---|---|---|
| colorado-ranges | 3.860 / 3.860 | 4.970 / 4.970 | 4.513 / 4.513 |
| sierra-nevada | 2.815 / 2.815 | 5.556 / 5.556 | 4.350 / 4.350 |

## Verdict summary

- **H1 (differencing level bias) — dominant mechanism, confirmed.** The weak training log-ratio sits 0.72 (Colorado) / 0.89 (Sierra) log units below the direct one. Honest (leave-one-out) recalibration of the single intercept on direct labels recovers 449% / 122% of the degradation and turns both artifacts into improvements over raw Kuchera (2.920 vs 4.513; 4.089 vs 4.350). The level is simply fit to a different label definition.
- **H2 (baseline provenance) — minor.** On identical CSSL station-days the TypeScript/Open-Meteo baseline is a factor 1.053 of the Python/NOAA baseline (~5%), an order of magnitude below the level gap, and it slightly *offsets* the gap rather than causing it. Matched elevation-band comparisons in Colorado even disagree in sign. Provenance cannot explain degradations of this size.
- **H3 (event mix) — real but secondary, and it cuts both ways.** The direct sets are lower, warmer storms (mean baseline 16.9 vs 21.6 in Colorado; 12.5 vs 17.6 in Sierra). Inside the weak training envelope the Colorado degradation flips to -1.072 (the artifact *wins* there in MAE) while Sierra's shrinks to 0.392; the net MAE harm concentrates in low-baseline events the SNOTEL population rarely produces. The log-space level bias itself is mix-independent — mix decides how much of it MAE sees.
- **H4 (hierarchy depth) — all of the damage is the intercept; there is effectively no shape. Mean deeper logBias terms are ≤ 0.05 in magnitude, depth-by-depth MAE is identical to four decimals, and the summed correction saturates the artifact's own 0.5 correction-factor floor for 100% / 100% of direct events: as applied, Candidate A is 'Kuchera x 0.5' almost everywhere. Shrinkage plus a spatially homogeneous differencing bias leave the hierarchy nothing else to learn.
- **H5 (QC censoring) — severe in count, exonerated for the level gap.** Only 42% / 34% of SWE-gain days survive the gates, and survival above 0 C average temperature is ~0 — the weak population is a cold-storm subset. But the differenced-SLR distribution barely moves across the gates (pre-QC vs passed means 8.88 vs 8.88 in Colorado; 6.96 vs 7.17 in Sierra): censoring shapes *which storms* are learned from, it does not create the board-vs-differenced gap, which is fully present inside the QC-passed population (section H2).
- **Salvage — yes, but it is a direct-label model, not a weak-label one.** (i) Weak structure + direct-fit intercept beats raw Kuchera in both regions under LOO and split-half honesty; since the weak shape terms are ~0, what survives is a one-parameter direct-label calibration. (ii) The weak shape alone (intercept dropped) is a wash: marginally better than Kuchera (4.331 / 4.206), within noise of it. (iii) The CSSL differencing offset (factor 1.51) recovers only about half the Sierra level gap and fails on Sierra GHCN events, though transferred to Colorado it happens to beat Kuchera. Bottom line: **weak labels can rank regions and seasons, but they cannot set correction levels** — the level must come from direct labels, and at present direct labels support exactly a one-parameter-per-region correction.

## H1. Differencing level bias — the intercept is fit to the wrong label definition

Mean log(observed/baseline) is the quantity the hierarchy's global node estimates. If differenced labels run systematically lower than board labels, the whole correction is shifted down by roughly the gap.

| Region | Weak train mean log-ratio | Weak WY26 mean log-ratio | Direct mean log-ratio | Weak − direct gap | Fitted global logBias (factor) | Direct-implied intercept |
|---|---|---|---|---|---|---|
| colorado-ranges | -0.996 | -0.898 | -0.274 | -0.721 | -0.987 (0.37×) | -0.257 |
| sierra-nevada | -0.995 | -0.957 | -0.102 | -0.892 | -0.945 (0.39×) | -0.061 |

Direct-label MAE under intercept surgery (degradation = full artifact MAE − Kuchera MAE):

| Region | Kuchera | Full A | Degradation | A minus global (recovered / share) | A, LOO-recalibrated intercept (recovered / share) | A, split-half recalibrated (recovered / share) | Oracle intercept (in-sample) |
|---|---|---|---|---|---|---|---|
| colorado-ranges | 4.513 | 4.970 | 0.456 | 4.331 (0.639 / 140%) | 2.920 (2.049 / 449%) | 2.939 (2.030 / 445%) | 2.898 |
| sierra-nevada | 4.350 | 5.556 | 1.206 | 4.206 (1.350 / 112%) | 4.089 (1.467 / 122%) | 4.116 (1.440 / 119%) | 4.073 |

Full-artifact bias on direct events (predicted − observed SLR): colorado-ranges -4.860; sierra-nevada -5.183. A negative bias is the H1 signature (corrections push SLR too far down).

## H2. Baseline provenance — and the CSSL same-day decomposition

The CSSL laboratory is SNOTEL station `428:CA:SNTL` (same coordinates, 2100 m). Its raw daily release gives board new-snow depth and precipitation-based fresh SWE for every day, so the label-definition effect can be measured on the same station-days with no profile involved. (In the stored dataset the differenced SLR field is populated only on QC-passed rows, so the matched population below is QC-passed station-days — exactly the population the trainer sees.) Date alignment between the CSSL morning-observation date and the AWDB daily date was chosen empirically (the shift maximising the fresh-SWE vs delta-SWE correlation):

| CSSL date shift vs SNOTEL date | Matched board-event days | corr(fresh SWE, ΔSWE) | Mean log(board / differenced SLR) |
|---|---|---|---|
| -1 d | 27 | 0.87 | 0.415 |
| 0 d | 17 | 0.51 | 0.815 |
| 1 d | 11 | 0.44 | 0.866 |

At the chosen shift (-1 d): **27 matched board-event days** (of 86 CSSL board-event days in WY2024-2026; 27 of the matched SNOTEL days passed weak QC). Board SLR exceeds differenced SLR by a factor of **1.515** (mean log 0.415, median 0.262). Decomposition: board depth vs SNOTEL Δdepth contributes log 0.236 (the settlement term), fresh SWE vs ΔSWE contributes log -0.179 (so log board/differenced ≈ depth component − SWE component).

On the 35 profile-aligned CSSL event days, the TypeScript/Open-Meteo baseline runs at a factor of **1.053** of the Python/NOAA baseline (mean log 0.052, median 0.046) on identical station-days — this is the pure provenance offset the correction was never fit against. The label delta on those same aligned days is mean log 0.611.

Baseline distributions (weak train vs direct) by elevation band — mean-baseline log ratio direct/weak within matched bands:

- colorado-ranges: weak mean baseline 21.57 (n=3634), direct mean 16.89 (n=120)
  - 2500-3000 m: weak 18.06 (n=1125), direct 16.01 (n=98), log ratio -0.120
  - 3000-3500 m: weak 23.13 (n=2309), direct 20.80 (n=22), log ratio -0.106
- sierra-nevada: weak mean baseline 17.63 (n=573), direct mean 12.51 (n=260)
  - 2000-2500 m: weak 18.05 (n=336), direct 17.77 (n=54), log ratio -0.016

## H3. Event-mix mismatch

| Region | Population | n | Elev mean (m) | Baseline SLR mean / median | Observed SLR mean / median | Event SWE mean (mm) |
|---|---|---|---|---|---|---|
| colorado-ranges | weak train | 3634 | 3120 | 21.57 / 21.82 | 8.85 / 8.00 | 11.9 |
| colorado-ranges | direct | 120 | 2818 | 16.89 / 16.54 | 13.32 / 12.64 | 11.5 |
| sierra-nevada | weak train | 573 | 2389 | 17.63 / 17.78 | 7.17 / 6.40 | 26.8 |
| sierra-nevada | direct | 260 | 1835 | 12.51 / 12.69 | 11.52 / 11.09 | 17.6 |

Restricting direct events to the weak training envelope (P1-P99 of baseline SLR, elevation, event SWE, and training months):

| Region | Direct n (full / in-envelope) | Degradation full | Degradation in-envelope |
|---|---|---|---|
| colorado-ranges | 120 / 82 | 0.456 | -1.072 |
| sierra-nevada | 260 / 114 | 1.206 | 0.392 |
- colorado-ranges: the 38 out-of-envelope direct events carry a per-event degradation of 3.754 — the measured MAE harm lives disproportionately in the part of the direct mix (low baselines, low elevations, shoulder months) that the SNOTEL population barely samples.
- sierra-nevada: the 146 out-of-envelope direct events carry a per-event degradation of 1.842 — the measured MAE harm lives disproportionately in the part of the direct mix (low baselines, low elevations, shoulder months) that the SNOTEL population barely samples.

## H4. Which hierarchy level does the damage

Direct-label MAE by walk depth (identity = raw Kuchera). Mean logBias contributed per level is over the events that reach that level; GHCN direct stations are not SNOTEL sites, so the local level rarely fires on direct events.

- colorado-ranges (direct): identity 4.513 → +global 4.970 → +climate 4.970 → +region cell 4.970 → +local 4.970; deepest-level counts {'climate': 39, 'region': 81}; mean logBias by level {'climate': -0.0081, 'global': -0.9875, 'region': -0.0135}
- colorado-ranges (weak WY26): identity 11.490 → +global 3.860 → +climate 3.860 → +region cell 3.860 → +local 3.860; deepest-level counts {'local': 1065, 'region': 13}; mean logBias by level {'climate': -0.0081, 'global': -0.9875, 'local': -0.0056, 'region': -0.003}
- sierra-nevada (direct): identity 4.350 → +global 5.556 → +climate 5.556 → +region cell 5.556 → +local 5.556; deepest-level counts {'climate': 47, 'global': 30, 'region': 183}; mean logBias by level {'climate': -0.0449, 'global': -0.9452, 'region': -0.0018}
- sierra-nevada (weak WY26): identity 10.393 → +global 2.815 → +climate 2.815 → +region cell 2.815 → +local 2.815; deepest-level counts {'climate': 2, 'local': 214}; mean logBias by level {'climate': -0.0328, 'global': -0.9452, 'local': 0.0064, 'region': -0.0071}

## H5. QC censoring of the weak label population

From the full daily dataset (all station-days in each region, WY2024-2026), not just QC-passed rows:

- colorado-ranges: 108815 QC-evaluated days, 4775 passed (4%). Of 11247 SWE-gain days (ΔSWE ≥ 5 mm), only 42% survive all gates. Top rejections: INSUFFICIENT_SWE_GAIN 73197, DEPTH_DECREASED_MELT_OR_SETTLEMENT 24856, RAIN_PHASE_SUSPECTED_TMAX 1748, RAIN_PHASE_SUSPECTED_PRECIP_EXCESS 1588.
  - tavg_lt-8: 3312 SWE-gain days, QC pass rate 59%, share with Δdepth < 0 (settlement-censored) 10%
  - tavg_-8_-3: 4636 SWE-gain days, QC pass rate 53%, share with Δdepth < 0 (settlement-censored) 10%
  - tavg_-3_0: 2183 SWE-gain days, QC pass rate 17%, share with Δdepth < 0 (settlement-censored) 11%
  - tavg_0_0.5: 267 SWE-gain days, QC pass rate 0%, share with Δdepth < 0 (settlement-censored) 15%
  - tavg_gt0.5: 805 SWE-gain days, QC pass rate 0%, share with Δdepth < 0 (settlement-censored) 11%
  - tavg_missing: 44 SWE-gain days, QC pass rate 0%, share with Δdepth < 0 (settlement-censored) 2%
  - SLR distributions: pre-QC computable (ΔSWE ≥ 5, Δdepth > 0) mean/median 8.88 / 8.00 (n=8773); QC-passed 8.88 / 8.18 (n=4775); warm-censored 7.78 / 6.67 (n=2273); direct observed 13.32 / 12.64 (n=120). Settlement-censored SWE-gain days: 1191 (median Δdepth -25.4 mm despite the SWE gain).
- sierra-nevada: 23737 QC-evaluated days, 792 passed (3%). Of 2343 SWE-gain days (ΔSWE ≥ 5 mm), only 34% survive all gates. Top rejections: INSUFFICIENT_SWE_GAIN 15638, DEPTH_DECREASED_MELT_OR_SETTLEMENT 6055, RAIN_PHASE_SUSPECTED_TMAX 497, WARM_DAY 309.
  - tavg_lt-8: 103 SWE-gain days, QC pass rate 61%, share with Δdepth < 0 (settlement-censored) 15%
  - tavg_-8_-3: 700 SWE-gain days, QC pass rate 58%, share with Δdepth < 0 (settlement-censored) 13%
  - tavg_-3_0: 905 SWE-gain days, QC pass rate 35%, share with Δdepth < 0 (settlement-censored) 12%
  - tavg_0_0.5: 146 SWE-gain days, QC pass rate 5%, share with Δdepth < 0 (settlement-censored) 10%
  - tavg_gt0.5: 489 SWE-gain days, QC pass rate 0%, share with Δdepth < 0 (settlement-censored) 19%
  - SLR distributions: pre-QC computable (ΔSWE ≥ 5, Δdepth > 0) mean/median 6.96 / 6.25 (n=1826); QC-passed 7.17 / 6.67 (n=792); warm-censored 6.78 / 5.66 (n=806); direct observed 11.52 / 11.09 (n=260). Settlement-censored SWE-gain days: 321 (median Δdepth -50.8 mm despite the SWE gain).

## Synthesis — attribution and salvage

Attribution of the direct-label degradation (MAE units; shares are of each region's degradation and are not strictly additive across mechanisms):

| Region | Degradation | Recovered by honest intercept recalibration (LOO) | Recovered by dropping the intercept (shape only) | Degradation inside the training envelope |
|---|---|---|---|---|
| colorado-ranges | 0.456 | 2.049 (449%) | 0.639 (140%) | -1.072 (n=82) |
| sierra-nevada | 1.206 | 1.467 (122%) | 1.350 (112%) | 0.392 (n=114) |

Attribution in log space (Sierra, where the same-day measurement exists): the weak-vs-direct level gap is 0.89. The CSSL decomposition assigns 0.42-0.61 of it (47%-68%) to the label definition (board vs differenced), 0.05 to baseline provenance — with the *opposite* sign, i.e. provenance slightly masks the label gap — leaving 0.33-0.53 to population differences the single-site decomposition cannot see (CSSL board days vs GHCN observer stations vs the SNOTEL station mix, and which days produce a differenced label at all). For Colorado (gap 0.72) no co-located pair exists; if the CSSL label effect is representative it accounts for roughly 0.4-0.6 of the gap, provenance is bounded by the matched-band comparison at ~0.1, and the remainder is population/mix. In MAE space the level term alone over-explains the degradation in both regions (recovery shares above 100%), because the direct-optimal intercept also absorbs part of raw Kuchera's own bias against board labels.

Salvage tests:

- colorado-ranges (i) weak structure + direct-recalibrated intercept (LOO): MAE 2.920 vs Kuchera 4.513 — beats raw Kuchera. Split-half corroboration: 2.939 ± 0.230.
- colorado-ranges (ii) weak shape with no region intercept: MAE 4.331 vs Kuchera 4.513 — beats raw Kuchera.
- sierra-nevada (i) weak structure + direct-recalibrated intercept (LOO): MAE 4.089 vs Kuchera 4.350 — beats raw Kuchera. Split-half corroboration: 4.116 ± 0.250.
- sierra-nevada (ii) weak shape with no region intercept: MAE 4.206 vs Kuchera 4.350 — beats raw Kuchera.
- sierra-nevada (iii) weak refit with labels shifted by the CSSL differencing-bias factor 1.515 (log 0.415): on Sierra GHCN events only (honest for the offset) MAE 4.826 vs Kuchera 3.663 vs unshifted 5.424 (n=225).
- colorado-ranges (iii, exploratory transfer of the Sierra constant): shifted-refit MAE 4.189 vs Kuchera 4.513 vs unshifted 4.970.

## Caveats

- Intercept surgery works in log space before the artifact's output clamps ([0.5, 2.0] factor, [3, 30] SLR); clamping is reapplied identically for every variant, but the recalibrated intercept is a log-space mean, not an MAE-optimal constant.
- The CSSL board SWE is precipitation x snow fraction from the laboratory's own gauge, not a weighed snow core; the GHCN direct labels are observer SNOW + WESF. 'Direct' is itself two instruments, and the CSSL offset is estimated at a single Sierra site.
- Baseline provenance (H2) is measured exactly only at CSSL (same station-days, both pipelines). For Colorado there is no co-located pair, so provenance there is bounded by the matched-band distribution comparison, not measured.
- The direct sets span different years than the weak training slice (Sierra GHCN reaches back to 2008; the aligned subsets are the profile-covered events). H3's envelope test controls covariates, not climate drift between decades.
- MAE shares of degradation are descriptive decompositions, not a variance partition; mechanisms overlap (e.g. censoring is one cause of the level bias).
