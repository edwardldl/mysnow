# Discussion record: improving the MySnow SLR model — Muse × Codex, 2026-11-02

*Five rounds. Codex (GPT-6 family; rounds 1–2 on its configured astra/xhigh, rounds 3–5 on sol/xhigh at the user's usage direction) received `model-problem-brief.md`, challenged it, was given the pipeline's actual configuration from the code, and converged under two added constraints: experimentation must get cheaper, and the model must apply across regions. Codex's sandbox shell could not run in the Coder container (bubblewrap/user-namespace block), so the brief and all evidence were supplied in the prompts; Codex modified nothing. Total Codex usage across the discussion: ≈777k tokens (34k + 79k + 194k + 204k + 266k by round).*

## What Codex challenged, and how it resolved

- **"Is the target even defined right?"** — It asked whether the label denominator was WESF or total precipitation. From the code: the gold label is **SNOW/WESF**, with hygiene screens (amount floors, the SLR=10 placeholder dropped, [2,50] range, multi-day and QFLAG exclusions, a station screen rejecting WESF copied from gauge PRCP). Challenge closed — the pipeline already does this.
- **"ERA5 skill ≠ served skill."** — Stands in part: transfer was measured for the old 27-feature champion (HRRR-input MAE 3.018 vs Kuchera-on-HRRR 4.101, 6/6 regions, 15,201 events) but **never for the v3 64-feature set**. The often-quoted "+1.04 transfer cost" compares against an in-sample ERA5 number; the honest gap is unmeasured for v3 and likely ~+0.16-scale. Resolution: a *paired* transfer check (same held-out events, both sources) enters the queue; a correction is fitted only if a ≥0.10 stable paired penalty appears.
- **Station-relative geometry (its proposal): withdrawn by Codex itself** once shown v3 features are already station-relative AGL constructions and the raw-pressure-level experiment's skipped "P-geometry" ablation is the only untested residue.
- **Storm chronology: downgraded by Codex** to a single feature (post-snow warm exposure) after learning the v3 set is pervasively precipitation-weighted with coincidence fractions; the rest is largely captured.
- **Alaska level correction: declined by Codex** — a mean bias does not establish an MAE improvement. Consistent with the project's earlier findings.

## Agreed findings about the model itself

- **EB defined:** the champion's "EB" is an empirical-Bayes hierarchical log-residual layer (global → climate regime → H3 cell → site; shrinkage ESS/(ESS+30); correction factor clamped [0.5, 2.0]; final SLR clamped [3, 30]) over the trees' station-grouped out-of-fold predictions. Both trees regress **raw SLR under squared-error loss at sklearn defaults** — i.e. the ensemble estimates conditional *means* while it is judged by MAE, which conditional *medians* minimise.
- **Model families beyond RF/GBM, ranked:** (1) hierarchical spline model with shrunk regional *slopes* (EB pools levels, not slopes — the untested axis); (2) physics-gated mixture of experts (warm mixed-phase vs cold growth regimes); (3) small regularised neural net; (4) approximate kernel regression (cheap check). All framed as blends/corrections to the incumbent with expected gains 0–0.08; stand-alone variants mostly expected to lose. Profile CNNs rejected (inputs are engineered summaries, not consistent vertical arrays across serving sources).

## The efficiency protocol (adopted as queue item 1)

- **Frozen manifests + stored incumbent fold predictions are the permanent baseline.** No more incumbent refits per experiment; comparisons are paired event-by-event at zero fidelity loss. The RF component is reconstructed and cached once. Artefact cache keyed by cohort/feature hashes, split ID, estimator + EB settings, seed, library version.
- **Two-stage judging:** a screen (all six LORO folds + one rep of each other family; kill if pooled LORO gain <0.02, any region worse by >0.05, any sampled family worse by >0.10) feeding the full judge. The screen must itself be validated on ≥6 known configurations (rank correlation ≥0.8, **zero false rejections** of a would-be adopter) before its kill rule is trusted.
- **GBM-only as the research/packaging default** if it qualifies (four-family mean within 0.02 of the ensemble, pooled LORO no worse, no region losing >0.05, temporal/unseen losses ≤0.03): 0.5 MB, trains in seconds; the 2.5 GB RF is produced only when a candidate proves value against it.

## The cross-region gate (now part of every adoption rule)

Adoption requires **all** of: four-family mean SLR MAE improves ≥0.03 vs the stored incumbent; **pooled LORO improves ≥0.02**; **no individual held-out region worsens by >0.05**; temporal and unseen-station do not worsen; randomised worsens ≤0.02; slice, masked-feature, and packaging checks pass. A pooled win cannot override a failed region clause.

## The final experiment queue

1. **Cache + validate the two-stage judge** (~1 day setup). Adopt exact-match caching after reconstruction checks; sampled-fold kill rule only after the correlation audit passes.
2. **Qualify GBM-only** as the efficient default (~0.5–1 day) under the gates above. Serving remains a separate decision.
3. **Median experiment M** (~0.5–1.5 days): GBM refit with `loss="absolute_error"` **plus a median-log-residual EB** (a mean EB could shift a median-optimal base off the MAE optimum — the design's key subtlety), RF + its mean EB unchanged, judged end-to-end on the frozen manifests under the amended rule. Diagnostics (median tree with mean EB, or without EB) are scored but **not adoptable** in this experiment.
4. **Screen + externally benchmark Alta-Collins and SLF manual pairs** (inserted by the data round; displaces the spline trial from this queue). Build high-elevation gold-definition benchmarks before any training use.
5. **Feature-based Kuchera gate** (~0.5–1 day): a depth-2 gate on physical predictors + model–Kuchera disagreement, trained on out-of-fold predictions, blending toward Kuchera only where identifiable *before* the observation. Must pass the same LORO gates. (An observed-SLR<5 win alone is not evidence a usable gate exists.)
6. **Full-pool 62-feature model for the Open-Meteo configuration** (~0.5–1 day): isolates the tclw-drop effect from geography (WALL changed both). Adopt for that arm only if masked-input LORO gain ≥0.03 with no region loss >0.05.
7. **Paired v3 HRRR transfer check** (~1–3 days, retrieval-bound): extend the cached HRRR extraction with the fields the v3 builders need (vertical velocity, humidity species, cloud liquid water); correction only under the ≥0.10 paired-penalty rule.

*Parked:* hierarchical spline blend (displaced by item 4; first reserve if the queue thins), mixture-of-experts and NN trials, the single sequencing feature, W1500/WALL serving questions.

## Data verdict (round 5)

**Current data are good — for what they measure.** Grades: label fidelity **B+** (SNOW/WESF with strong hygiene; residual error is observer practice, clearing/compaction timing, rounding, gauge undercatch raising SLR); profile representativeness **B** nationally, uncertain at resorts (ERA5's ~31 km grid vs ridge/valley structure; no defensible % of MAE attributable — the randomised→unseen-station +0.22 and randomised→LORO +0.40 gaps bundle profile, physics, practice, and generalisation effects); mountain label coverage **D for the served target** — **zero alpine gold** in 57,376 events is the single largest accuracy cap, ahead of any modelling choice. 57,376 *more lowland pairs* would scarcely answer the resort question.

Named public sources, with verdicts:
- **SLF manual study plots (Switzerland) — strongest new find.** CC BY 4.0; publishes `HN_1D` + `HNW_1D` — 24 h new snow *and its water equivalent*, the exact gold-label definition — plausibly 10⁴–10⁵ candidate pairs (unverified; HNW absent at some sites), mostly 1,000–2,000 m with a subset at resort elevations. **Adopt as external benchmark**; train only if U.S. held-out regions still improve. (CERRA's 5.5 km European profiles could later test whether finer profiles help this benchmark.)
- **Alta-Collins (2,945 m)** — CC BY-NC 4.0; 9,940 12-hour records → ~1,000–3,000 screened 24 h pairs. Benchmark first (latest winters reserved untouched), then a limited low-weight training trial; pairs are summed over the actual **04:00–04:00** window (ratio of sums, never the mean of ratios) and labelled as a 04:00-ending benchmark, not a 07:00 product check. Licence recorded with any artefact; commercial use would need revisiting.
- **CSSL / HLY files (Veals et al. 2025)** — public, reuse licence unverified; tens to a few hundred pairs each. Benchmark first; terms before training.
- **ECCC/MSC (Canada): rejected for training** — snowfall water equivalent at ordinary stations is commonly *derived as snowfall/10*; such labels would teach the answer. **JMA: rejected** — no fresh-snow SWE. **ERA5-Land: rejected** as a profile source (no vertical profiles). **ERA5 remains the practical profile ceiling** for the U.S. pool; archived HRRR serves as the profile-transfer benchmark, not a replacement.

## What remains genuinely undecided

Measurable, in queue order: whether GBM-only passes all gates; whether the screen predicts full judging; whether median loss survives median-EB; whether high-site benchmarks (Alta + SLF) expose transferable gains or merely reproduce lowland rankings; whether a Kuchera gate travels; whether v3 has a paired HRRR penalty ≥0.10. User decisions unchanged: what serves, the production cadence/backend, and the §7 items (branch merge, push, gitignored data tree, mountain-design ratification).

## Addendum 2026-11-02 — queue items 1–2 results, put to Codex

Item 1 (cached two-stage judge, commit dd5d764): reconstruction of the incumbent ensemble from stored components was exact (max diff 0.0); screen validation on six configurations gave Spearman 1.000 and zero false rejections — kill rule declared trusted under the pre-registered criteria. Item 2: GBM-only FAILED qualification (four-family +0.0351 vs limit 0.02; unseen-station +0.0704 vs limit 0.03; randomised +0.0674) while pooled LORO improved −0.0042 and all per-region diffs stayed within ±0.05.

Codex's read (round 6): (1) the screen's validation is ONE-SIDED — zero false rejections among zero adopters; it has never preserved a boundary candidate. Prescription: for median M, run screen AND full judge regardless of the screen result, declared in advance; the screen is advisory for that run; if M passes the full gate after a screen kill, withdraw the kill rule. (2) The GBM verdict is CORRECT and the thresholds stand — unseen-station performance is precisely the resort-point case, and +0.067 randomised is not rounding; GBM-only remains a cheap component-experiment tool, not the accuracy reference. (3) Queue order and M's design unchanged; add one scored, non-adoptable diagnostic to M's report: paired per-fold loss changes of old vs median HGB components on unseen-station folds (beside RF and both ensembles), split by EB site-node availability, plus the error correlation between the median HGB and RF.


---

## Addendum — Round 8 (2026-11-04, results review: items 5, 6, frozen-fold report)

**Uphold both verdicts, keep ENS-EB serving, and run the paired v3 HRRR check. These results justify ending the current model-search round; they do not establish a data-imposed accuracy ceiling.**

1. **Kuchera gate: closed. Pool62: real improvement, correctly rejected under its registration.**

   The gate’s exact null is decisive for the tested design. Every learned leaf prefers ENS-EB, including extreme disagreement. The observed-SLR<5 advantage does not support an operational fallback. “Unreachable by *any* forecast-time rule” is stronger than this experiment proves, but there is no evidence worth pursuing another gate now.

   Pool62 exposes a distinction in the adoption policy: **portability and minimum improvement are different requirements**. Its LORO result shows improvement, and every region stays inside the harm guard. Calling this a failure to travel would be inaccurate. It failed the required *size* of the portable improvement.

   I would prospectively judge input-availability remedies by meaningful improvement on the affected configuration plus LORO non-inferiority and regional harm guards. Requiring a fresh minimum LORO gain makes more sense when justifying a new model’s complexity. **That policy change cannot approve pool62 on the same already-examined folds.** Confirmation needs untouched evidence; another random split is not untouched evidence.

2. **“No adopted challenger” is supported; “dataset ceiling” is not.**

   Pool62 itself demonstrates recoverable error. The other outcomes show that several specific changes failed, that RF contributes useful complementary predictions, and that geographically portable gains are harder to obtain than pooled gains. None identifies how much error comes from labels, profiles, missing predictors, or the estimator.

   I would **keep the hierarchical spline parked**. Its regional slope deviations disappear in a fully held-out region, leaving shared slopes or climate terms learned elsewhere. Shared smoothness could help, but the present results give no reason to expect its gains to concentrate in LORO.

   The residual report supports a narrower positive conclusion: the champion retains its edge at higher **Rockies-dominated observed stations**. It does not validate alpine PNW or Alaska. The Sierra cell is a useful investigation flag, but 46 events do not justify a local correction. The outcome-selected tails likewise do not establish an available switching rule.

3. **Item 7 is still worth running as designed.**

   It measures a different uncertainty from every completed challenge: whether the same fold-trained v3 model loses skill when its inputs become actual HRRR forecasts.

   Use identical held-out events, actual report windows, representative forecast lead times, and the complete v3 field set. If the cache still contains only the old 27-feature fields, complete the extraction before calling the result a v3 transfer measurement.

   Its result should change the following decisions:

   | Result | Consequence |
   |---|---|
   | Penalty below 0.10, with no substantial regional concentration | Close the proposed HRRR correction study; retain evidence that v3 transfers reasonably on this gold cohort. |
   | Penalty at least 0.10 | Open the already conditional correction study; promotion still requires held-out-region success. |
   | Small pooled penalty but a large regional or lead-time penalty | Document and investigate that concentration; the pooled number does not establish uniform transfer. |

   This measures SLR transfer, not total snowfall forecast accuracy or alpine validity.

4. **Keep the current OM configuration pending independent confirmation.**

   The measured masking tax is small on average and now quantified. Preserve the reduced-feature/source label. There is no evidence here for changing blend weights toward another forecast source: an ERA5 feature ablation does not compare those sources’ forecast errors.

   The **81.9 versus 18.3 mm** precipitation difference deserves more attention than the average masking tax. First establish whether those are genuinely comparable 24-hour, phase-consistent quantities. Changing weights or clipping inputs before that check could conceal an accumulation or representation problem.

5. **I would run one final cheap diagnostic after item 7: an audit of input coverage during snow-producing windows.**

   Using existing serving records, restrict to windows for which the source predicts snow, verify units and accumulation intervals, and tabulate the fraction of windows outside gold support by source, lead, and resort. Inspect the precipitation and subcloud extremes explicitly. “Some feature exceeds a training maximum somewhere” is too coarse to assess operational exposure.

   This can distinguish mostly dry-window differences from persistent extrapolation during forecast snowfall. It needs no new model or external labels.

   The screen now tracks a **positive scalar improvement** accurately, so its evidence is stronger than the earlier all-loser audit. It still has not demonstrated retention of a full-gate adopter. Matching the deciding clause validates implementation; it does not validate that clause’s scientific threshold.

**Recommendation: run item 7, complete the snow-window input-coverage audit, then close this search round. Keep the mountain benchmark parked and make matched mountain observations and profiles the next accuracy investment before authorising alpine training or reviving the spline.**
## Round 9 — 2026-11-05: item 7 interim HRRR transfer result

Questions put to Codex (fresh thread; all evidence embedded in the prompt, as Codex cannot read files in this environment): (1) commission the correction study's design now vs wait for the formal full-cohort verdict, and what the full cohort could change; (2) preferred correction shape — second EB-style residual layer, per-region/per-source corrections, input harmonisation, or HRRR retraining — and what paired-data evidence discriminates; (3) how much of the penalty is the missing convective-split features vs genuine HRRR/ERA5 differences, and whether a masked-ERA5 decomposition is the right split; (4) whether Kuchera improving on HRRR inputs reopens the item-5 gate or serving conclusions; (5) whether the round-8 closing sequence changes and where a correction study sits. Codex's response, verbatim:

**Commission the correction study’s design now. Keep item 7’s formal verdict pending, and make the first design stage diagnostic.** My preferred starting point is a small, HRRR-specific residual adapter, conditional on the masked-ERA5 decomposition and full-cohort results supporting it. Mountain observations remain the next substantial accuracy investment.

I’m treating your supplied results as the experiment record; local inspection was unavailable.

1. **The interim result justifies design work, but does not settle the correction’s scope.**

   The +0.4370 pooled and +0.1911 LORO-unique penalties provide enough evidence to justify a bounded investigation. They are overlapping views of the same selected cohort, however—not independent confirmations. The breadth gate prevents an extremely narrow snapshot; it does not remove fetch-order selection bias.

   The full cohort could change the design in several consequential ways:

   - **Later winters could change the apparent geography.** Eastern penalties might persist, disappear, or turn out to depend mainly on particular storm regimes or model eras.
   - **Temporal coverage could change the generalisation story.** Forty-six Alaska events cannot establish whether a correction transfers across winters.
   - **The source of the error could change.** A stable directional bias supports a residual correction; greater scatter without predictable direction offers much less correctable signal.
   - **Mountain and serving exposure could change the priority.** Station-region averages do not establish performance at catalogue resorts. Sierra’s positive penalty also prevents a simple “mountains are fine” interpretation.
   - **The aggregate could cross back below the commissioning threshold.** Most eligible events are outside this interim cohort. Its size cannot compensate for systematic selection.

   More fetching **cannot resolve lead versus domain confounding if the sampling design stays unchanged**, and cannot establish performance beyond the sampled short leads.

   Preserve item 7’s decision rule exactly. If the full result has pooled penalty below 0.10 but a region or lead band at or above 0.15, that satisfies neither transfer acceptance nor the stated pooled trigger for commissioning. Record that unresolved branch explicitly rather than inventing a retrospective pass.

2. **Favour a small HRRR-specific residual adapter, with regional effects allowed only through strong partial pooling.**

   Options (a) and (b) are partly complementary: the adapter can use an EB-style structure while being restricted to the HRRR arm. I would start with a strongly regularised adjustment to the **complete ENS-EB prediction**, testing a simple intercept/slope before adding a few serving-available regime variables.

   Crucially, **positive transfer penalty does not mean positive prediction bias**. Alaska’s +0.4658 does not tell you which direction to adjust predictions. Likewise, negative penalties in the Rockies and PNW do not prove that their predictions are unbiased.

   | Shape | Evidence that would favour it |
   |---|---|
   | **HRRR residual adapter — preferred first candidate** | Conditional signed residuals show stable, predictable structure across held-out stations and winters. A simple adjustment improves original-scale SLR MAE. |
   | **Regional effects** | Regional residual differences persist after accounting for temperature regime, elevation mismatch, season and year, and replicate across winters. Use shrinkage; an unseen region must fall back to a shared effect. |
   | **Input harmonisation** | Paired features reveal stable, physically interpretable discrepancies, and correcting them improves held-out SLR predictions. Resolve unit, accumulation-window, interpolation or missing-value inconsistencies before considering statistical mappings. |
   | **HRRR retraining** | The useful feature–SLR relationships differ broadly or nonlinearly, simple adapters fail, and sufficiently representative HRRR training data exist. This is a later, larger investment. |

   I would oppose independent regional offsets selected from the current map. They could encode fetch order, winter mix or storm composition, and they cannot learn a held-out region’s offset without violating LORO.

   I would also oppose wholesale marginal distribution matching. Making HRRR features resemble ERA5 does not establish that their relationship with observed SLR improves; domain adaptation depends on the type of distribution shift. [Zhang et al.](https://proceedings.mlr.press/v28/zhang13d.html)

   Fit the correction against **observed SLR**, not against ERA5-input predictions as a teacher. Otherwise, the procedure could deliberately erase HRRR’s genuine improvements. A log-scale parameterisation is reasonable, but a mean log-residual correction is not automatically optimal for original-scale MAE.

3. **Run the masked-ERA5 decomposition first. No defensible numerical attribution is possible yet.**

   For each fixed fold model and exactly the same paired events, calculate:

   - \(L_E\): MAE using ordinary ERA5 inputs.
   - \(L_{E,m}\): MAE using ERA5 inputs with the three convective-split features represented exactly as the HRRR serving adapter represents their absence.
   - \(L_H\): MAE using production-equivalent HRRR inputs.

   Then, with identical event weights,

   \[
   L_H-L_E
   =
   \underbrace{L_{E,m}-L_E}_{\text{masking penalty on ERA5}}
   +
   \underbrace{L_H-L_{E,m}}_{\text{remaining input-package difference}}.
   \]

   **Reuse the masked-judging machinery, not the 62-feature model.** Keep the fitted model fixed. Apply the actual missing-value/default/imputation behaviour, including affected derived features and indicators; simply dropping columns or zeroing values could test a different intervention.

   This gives an exact accounting decomposition, but not a unique causal attribution:

   - The masking term measures the effect of absence **on ERA5 inputs**.
   - The remainder includes source differences, adapter differences and interactions with missingness. It is not automatically “genuine profile error.”
   - Either term can be negative, or exceed the total. Avoid forcing them into percentages summing to a causal explanation.

   Report both terms by evaluation family, region and winter. The Open-Meteo tclw tax of +0.0461 provides no quantitative estimate for this different feature block.

   If masking explains most of the degradation, I would move a **training-time availability-mask candidate** ahead of profile harmonisation or wholesale HRRR retraining. That tests whether the model can learn to operate without the unavailable fields. It cannot recreate information those fields contained.

   If the two terms substantially cancel, an optional diagnostic is to insert paired ERA5 convective-split values into otherwise HRRR inputs and examine the interaction. Treat that as an offline hybrid experiment: it uses unavailable information and may produce inconsistent feature combinations.

   Thus: **design the diagnostic stage now; complete it before selecting and fitting the correction family.**

4. **Kuchera’s improvement does not reopen item 5 automatically. It does delimit what item 5 established.**

   The ERA5-tested gate’s exact null remains a valid result for that experiment. It does not establish that a gate fitted and evaluated on HRRR inputs would also assign zero weight.

   A temperature-only rule can improve under a source change while a richer model degrades. The two predictors depend on different features and learned relationships; Kuchera also avoids the absent convective-split block. This behaviour is compatible with source shift, but **does not identify its cause** or prove HRRR temperature profiles are more accurate.

   ENS-EB’s roughly 0.61 LORO advantage over Kuchera on these HRRR events argues against a wholesale switch. Average superiority does not rule out useful error complementarity, however. I would reconsider an HRRR-specific gate only if **serving-identifiable conditions predict repeatable Kuchera advantages**, followed by independently judged gains from a fitted gate. The opposite signs of the two transfer penalties alone are insufficient.

   Serving implications are limited:

   - Keep production calibration disabled pending a qualified correction.
   - Keep the old 27-feature HRRR comparison retracted; it supplies no supporting evidence.
   - Do not generalise this result to ECMWF GRIB, Open-Meteo or longer HRRR leads.
   - Verify that the UTC/cache repair reaches every affected consumer. ERA5 prediction-cache agreement validates the refits, not HRRR timestamps or payload identity.

5. **Keep the round-8 closing recommendation, with a bounded correction-study handoff.**

   My sequence is:

   **Finish item 7 → complete the snow-window coverage audit → close this search round, carrying a separately registered correction study if warranted.**

   The decomposition and design document can proceed while fetching finishes. The audit should precede committing to a fitted correction because it establishes the operational target: which resorts, elevations, sources, lead times and missing-feature patterns occur during snow windows.

   A correction aimed at heavily represented lowland observations could have little catalogue benefit. Conversely, meaningful Alaska or eastern-resort exposure could justify a small correction study promptly.

   Continue the mountain-data work in parallel, including resolving the ERA5 pairing bottleneck. A bounded adapter experiment can precede its completion; an expanding search over regional models, feature mappings and HRRR retraining should not displace it. Matched mountain observations are also the evidence needed to judge whether a correction helps the intended product.

I would impose the following pre-registration requirements on the correction study:

1. **Freeze provenance and scope.** Record cohort membership, exclusions, folds, model and builder versions, UTC cycle/valid-time checks, cache identity, and production-equivalent missingness. Declare supported domains and leads. Report fetch failures and coverage gaps without removing difficult cases based on scores.

2. **Declare the estimands and comparator.** Correction gain is the uncorrected v3 **HRRR-input** MAE minus corrected HRRR-input MAE on identical events. Report the remaining transfer penalty against ERA5 separately. Preserve four-family weighting and LORO-unique reporting; do not treat repeated predictions of an event as independent observations.

3. **Use nested validation for the whole adapter pipeline.** Within each outer fold, train the adapter using inner out-of-fold predictions produced entirely within the outer training set. This includes the existing EB layer and any learned preprocessing or harmonisation. Merely fitting a correction to the pooled existing OOF residuals and rescoring those rows is invalid. Keep outer region, station and temporal exclusions intact throughout.

4. **Freeze a small candidate budget after the diagnostic stage.** Declare predictors, hierarchy, shrinkage, tuning ranges, clipping and fallback behaviour before judging correction candidates. Use only information available at serving time. Region/lead effects that the design cannot separate must remain explicitly confounded.

5. **Retain the adoption bars.** Require four-family mean gain ≥0.03, pooled LORO gain ≥0.02, no held-out region worsening by >0.05, temporal and unseen-station non-worsening, and existing slice guards—all against the uncorrected HRRR baseline. Include adequately supported mountain, domain and lead slices. Insufficient temporal support is inconclusive, not a pass. A qualifying improvement does not by itself mean the original transfer limits have been satisfied.

6. **Predeclare uncertainty and confirmation.** Preserve pairing and station/storm dependence in uncertainty estimates, with minimum support rules fixed before scoring. Because this benchmark has already influenced several design decisions, reserve independent station/year confirmation before production activation. Repeated model selection on a fixed benchmark can itself bias reported gains. [Cawley and Talbot](https://jmlr.org/papers/v11/cawley10a.html)

The evidence that would most change my recommendation is **a large masking penalty with little remaining source penalty**, which would favour availability-aware training; **unstable signed residuals across winters**, which would weaken the adapter case; or **persistent, predictable source discrepancies after masking**, which would strengthen targeted harmonisation.

## Round 10 — 2026-11-05: correction-study design + masked decomposition

Questions put to Codex (fresh thread; all evidence embedded in the prompt): (1) review the registered correction-study design (model-v3-hrrr-correction/DESIGN.md) against the round-9 requirements, incl. the nested-validation construction, the candidate budget and the four fitting gates; (2) rule on whether the decomposition (masking exactly 0.0 at full precision; the penalty entirely remainder, with strongly directional signed residuals) supports the adapter-first candidate order C1 -> C2 -> C3, and whether the liquid-band interaction changes C2; (3) whether the masking interpretation (trees never route on the near-constant regime features) is accepted and generalises; (4) flag anything that must change before fitting is authorised. Item 7's formal full-cohort verdict (pooled +0.5141 -> correction_study_warranted) was supplied. Codex's response, verbatim:

**Conditional approval.** The design substantially meets round 9, and the evidence supports proceeding toward an adapter study. Item 7’s verdict is settled: **correction study warranted**. It does not establish that an adapter will succeed.

I require a short registration addendum before fitting. These are specification requirements, not a request to expand the study or repeat the eight-hour decomposition.

1. **Nested validation: the proposed construction is correct for evaluating a fixed adapter.**

   Within each outer training set, inner ENS-EB refits generate predictions for events excluded from those refits; the adapter trains against observed SLR using those predictions. The outer-training ENS-EB refit then supplies the complete prediction that the adapter corrects on the outer test set.

   Make three details explicit:

   - Inner splits preserve the relevant exclusion: region for LORO, station for unseen-station, and chronological separation for temporal validation.
   - All learned preprocessing, tree parameters, EB effects and hyperparameters, and adapter parameters obey those exclusions.
   - Adapter tuning receives its own valid validation within the outer training set. Generating inner OOF base predictions does **not**, by itself, validate adapter hyperparameters. Naively cross-validating an adapter over one precomputed OOF table can leak validation labels through the base models that generated adapter-training rows.

   Fully fixed candidates can each receive valid outer-fold evaluations without another selection layer. If outer results select the winner, report that as benchmark-based selection; the selected winner’s score is not an unbiased estimate of the selection procedure. Your reserved independent confirmation remains essential.

   For C3 specifically, the held-out LORO region must receive the shared-effect fallback. Its diagnostic residuals cannot supply a regional offset or inform a fitted regional prior.

2. **Candidate order: retain C1 → C2 → C3.**

   The directional residual pattern makes intercept/slope correction a sensible first hypothesis. However, negative **mean** signed errors do not establish an MAE benefit: conditional medians govern absolute-error minimisation. Tail errors could drive the reported means.

   Forecast liquid amount should be in the frozen C2 specification. My preferred initial C2 is C1 plus a simple, strongly regularised function of serving-available forecast liquid, with its transformation fixed in advance. The diagnostic establishes liquid-dependent residual structure; it does not yet establish a statistical interaction requiring a liquid-by-SLR product term.

   The remaining proposed variables need narrower justification:

   - Predicted-SLR bands add nonlinear flexibility beyond C1’s slope; specify that purpose and freeze the form.
   - Column temperature is plausible, but the supplied diagnostics do not demonstrate its incremental value.
   - Domain/lead must not be interpreted as an independently identified lead effect: Alaska and lead are confounded here.

   The liquid pattern does **not** justify replacing C1 with a liquid-only first candidate. Keeping C1 first reveals whether a simple correction captures most of the attainable gain. C3 remains last because regional flexibility can improve familiar regions without transferring to a genuinely held-out region.

3. **Masking: accept the empirical result, qualify the mechanism and extrapolation.**

   The demonstrated conclusion is strong: **under the tested masking intervention, the fitted pipeline’s predictions and losses are unchanged to numerical precision on the reduced cohort.** Consequently, the measured penalty on that cohort is entirely the remainder term.

   “The trees never route on these features” is a stronger claim. Identical predictions could also arise because missing and observed values follow equivalent paths, or because affected paths produce equivalent outputs. Near-constant convective inputs are a plausible explanation, not a demonstrated structural proof.

   I accept skipping the full-cohort decomposition for this decision. The large, replicated total penalty and the negligible tested masking sensitivity make that a reasonable allocation of effort. Replace “cannot plausibly become material” with a scoped statement: the audit gives no evidence that masking materially contributes, and provides insufficient justification for an expensive refresh. The full-cohort remainder has not itself been measured.

   **Formally close availability-mask retraining as a candidate in this correction study.** Do not describe retraining as scientifically disproven: inference-time masking and training without features are different interventions. Reopening would require new evidence, such as material masking sensitivity in another cohort or a changed model.

4. **The four fitting gates are appropriate and remain binding.**

   Item 7 is complete. This review supports the design subject to the registration details below. The snow-window audit remains required unless the user explicitly waives it, and fitting still requires separate user authorisation.

   The audit should establish whether the paired lowland cohort adequately represents the intended snow windows. Material coverage gaps should narrow the study’s claims or require a documented design amendment. They must not silently disappear through completeness filtering.

Before fitting, the following details must be explicit in the registration. Where the existing document already specifies them, no additional change is needed:

- **Complete the nesting specification described above.** State the inner split construction, treatment of all learned ENS-EB/adapter components, tuning procedure, and unseen-region fallback.
- **Freeze the actual candidate budget.** C2 currently describes a family of possible models. Freeze its variables, transformations, interactions, regularisation, and any tuning grid; do the same for C3’s regional effects. Specify any log-scale, positivity, or clipping rules. Use original-scale MAE for tuning and selection. Count tried variants within the budget.
- **Make the decision rule executable.** Define four-family aggregation weights, paired event weighting, confidence level and interval construction, and whether adoption thresholds apply to estimates or confidence bounds. State how station- and storm/date-cluster uncertainty will be reconciled, and what produces an inconclusive result. Preserve the agreed numerical bars.
- **Complete support and slice rules.** The supplied region and water-year event minima do not fully specify temporal support or slice guards. Define these using unique events and relevant independent clusters. The full temporal sample resolves the earlier reduced-cohort shortage for item 7; correction-stage conclusions still require their own support assessment.
- **Qualify the decomposition claims.** Keep the empirical zero scoped to the tested cohort and intervention, and distinguish the observed result from the proposed tree-routing explanation.

Optional improvements would strengthen interpretation without becoming new fitting gates:

- Inspect split usage or prediction paths in the existing fitted models. An absence of splits on those features would directly support the proposed mechanism without a full scoring rerun.
- Report median signed error alongside means, and examine liquid dependence within broad region and predicted-SLR strata. This would show whether the pattern is broadly shared or mainly reflects geographic composition and sparse tails.
- Apply comparable audits to feature blocks implicated in any future harmonisation proposal. Use serving-realistic interventions and inspect interactions. A block ablation measures sensitivity; it does not establish that harmonising that block will help.

The evidence most likely to **kill the adapter hypothesis** would be:

- Correcting mean under-prediction fails to improve original-scale MAE because the directional signal is concentrated in tails.
- Gains occur mainly in randomised splits, then disappear under LORO, temporal, or unseen-station validation.
- A global uplift improves Alaska, the Northeast, and Great Lakes but breaches the regional guards elsewhere.
- Liquid-dependent gains disappear across years or regions, or require increasingly flexible specifications.
- C3 succeeds only where regional effects can be estimated and loses its advantage under the required LORO fallback.

Failure of C1 alone would not kill the hypothesis. Failure of the entire frozen C1–C3 budget under the adoption and uncertainty rules would. A benchmark pass followed by failure on the reserved independent confirmation would kill the case for production activation.

## Round 11 — 2026-11-05: snow-window audit + correction-study registration

Questions put to Codex (fresh thread; all evidence embedded in the prompt): (1) whether the snow-window input-coverage audit narrows the correction study's claims or requires a formal design amendment, and whether the registration's claim-scope clause suffices; (2) whether the audit's comparability verdict (the 81.9 mm served liquid extreme is a genuine 24 h total; conditioning, not bookkeeping, is the asymmetry) is the right reading and what it changes; (3) whether the registration addendum (complete nesting specification, frozen C1/C2/C3 budget, executable decision rule, support and slice rules) is complete enough that fitting could be authorised; (4) rulings on point-estimate thresholds with the inconclusive overlay vs confidence-bound thresholds, and on the frozen C3 shrinkage constant (30) and C2 transform (log(1 + L/5mm)). Codex's response, verbatim:

**My ruling: accept the audit’s principal findings and narrow the study’s claims. The scientific design remains viable, but I would not certify the registration as fully executable yet.** Several specific points need freezing before fitting. Item 7’s `correction_study_warranted` verdict stands.

1. **The audit calls for registration-level narrowing, not a redesign. §R4 needs more precise scope language.**

   Keeping the complete paired cohort, candidates, targets, and judging criteria unchanged makes this a limitation on interpretation. A dated reference from `DESIGN.md` to §R4 is sufficient, provided §R4 explicitly supersedes any broader serving claims.

   Three corrections matter:

   - **Forecast hours and daily-window leads are different.** Training inputs at HRRR forecast hour 1 CONUS / hours 1–3 Alaska do not establish coverage of served daily-window leads 1–2. The 18/204 figure establishes **8.8% HRRR-arm exposure**. Calling it arm/**lead** coverage requires provenance showing the forecast hours supplying those windows. Otherwise, record forecast-hour overlap as unestablished.
   - **Full-gold bounds do not automatically become paired-cohort bounds.** The stated reference comes from 57,376 events. The 22,202-event subset can have narrower extrema and different percentiles. Unless equality has been verified, label 67.6% as “inside the full-gold liquid/elevation bounding rectangle,” not “within paired-cohort support.” Exceeding the full-gold maximum certainly exceeds the paired maximum; being below it does not establish paired support.
   - **A two-feature bounding rectangle is only a support diagnostic.** It does not establish regional or multivariate support. Report the HRRR-specific 55.6% alongside the overall 67.6%, with the same reference-envelope qualification.

   Exact forecast-hour provenance and paired-cohort bounds could resolve the first two issues without changing the study. Broadening claims to alpine serving conditions would require relevant validation observations; this single serving cycle cannot supply them.

2. **Yes: accept the liquid-total comparability verdict, with the stated incomplete-window exception.**

   The reconstruction, unit checks, accumulation differencing, and independent parity evidence support genuine totals for complete 24-hour windows. Conditioning on observed qualifying snow versus forecast snow-gated windows is a substantive asymmetry. Nothing presented justifies dividing Open-Meteo totals by six or capping precipitation at the gold maximum.

   The Big Sky row is explicitly a **20-hour observed input sum**, so it should remain visibly separate from claims about complete 24-hour totals. Do not silently rescale it.

   There is also an important limit to the verdict: **total-liquid comparability does not establish hourly-feature comparability.** Uniform six-hour disaggregation preserves totals but can affect hours above an intensity threshold, peak rates, and precipitation’s timing relative to temperature. Thus the liquid exceedance is established; the interpretation of the entire precipitation-feature cluster requires more care.

   Reporting exposure fractions is sufficient to support this **limited offline study**. It is insufficient to establish deployment suitability for extreme windows. Keep the fitting cohort intact and introduce no unregistered clipping, filtering, or reweighting. Any subsequent serving gate, fallback, or input treatment needs its own explicit rationale and validation.

   Evidence of incorrect accumulation semantics would change the bookkeeping ruling. Observations from these warm, high-liquid mountain storms would change the performance assessment. The zero masking term establishes the decomposition result; it does not establish that the remainder is correctable.

3. **Not yet: five execution specifications need completion or an exact reference to an existing frozen definition.**

   These are concrete potential sources of mid-study choices:

   - **Resolve the literal fit-budget conflict.** Three candidates × four lambdas gives 12 **configurations**. Validation across multiple folds requires more than 12 actual adapter fits, followed by final refits and the necessary base-model refits. Register the configuration budget and actual fitting ledger separately. If “≤12 fits” is a literal hard cap, it must be amended before fitting.
   - **Close the remaining nesting ambiguity.** For each lambda-validation split, exclude its validation data from the entire fitting pipeline: both the models generating adapter-training OOF predictions and the models generating validation predictions. Regenerating only predictions for the held-out tuning region is insufficient if adapter-training predictions came from models trained using that region. Apply this recursively to trees and EB, with the temporal equivalent. A frozen split diagram or pseudocode can settle this.
   - **Specify the complete candidate objectives.** State whether gamma is penalised; how regional shifts are estimated and penalised; the ESS definition; the order of estimation, shrinkage, and clipping; and the loss/penalty normalisation. A jointly optimised, freely rescaled regional coefficient can compensate for a multiplicative shrinkage factor, so the stated scaling alone does not uniquely define an EB-style estimator.
   - **Freeze tuning and scoring details.** Specify the lambda-selection metric and fold aggregation, tie-breaking, fold assignments and temporal boundaries, numerical seeds, and slice boundaries. Predicted-SLR bands should use a fixed reference such as C0 predictions. Existing frozen definitions can be incorporated by exact reference rather than duplicated.
   - **Make bootstrap execution unambiguous.** Resample shared station or storm identities consistently across repeated outer-test appearances, candidates, and families, then recompute the registered statistics. Define treatment of empty or degenerate bootstrap slices. Also define “wider interval governs”: the longer interval need not contain the shorter. I recommend the envelope of their endpoints, or declaring a clause inconclusive whenever either interval triggers that condition. If choosing the literal larger-width interval, freeze that choice and its tie rule without describing it as necessarily conservative relative to both.

   The stated precedence—any binding inconclusive clause makes the candidate inconclusive overall—is already explicit. Preserve it in implementation, including when another clause has a point-estimate breach.

4. **Both substantive choices are acceptable freezes. Neither requires expanding the tuning search.**

   **(a) Point-estimate thresholds with an inconclusive overlay: approved.** This is a decision rule combining estimated practical benefit with an uncertainty veto. It does **not** demonstrate that the true gain exceeds every adoption threshold.

   For example, a gain of 0.04 with an interval of [0.01, 0.06] can pass a 0.03 bar under your rule. That is coherent, although the interval includes gains below 0.03. Applying thresholds to confidence bounds would impose a materially stronger criterion and change the registered design. I do not require that change.

   Describe the intervals as nominal uncertainty assessments, without implying simultaneous 95% protection across all candidates and clauses. I would require stronger confidence-based criteria if the intended claim were specifically that minimum benefits had been statistically established.

   **(b) C3’s constant 30 and C2’s `log(1 + L/5 mm)`: approved as fixed modelling choices.** They do not need to become tuning dimensions. Borrowing 30 does not establish its optimality or transferability, but a prospectively fixed choice is legitimate. The essential prerequisite is a fully specified estimator, particularly the C3 issue above.

   C3’s zero regional fallback in LORO is appropriate. Its LORO predictions can still differ from C2 because fitting regional terms can change the shared coefficients. Its evaluation should therefore retain the full registered judging procedure.

   Poor results would justify rejecting a candidate or registering a subsequent study. They would not justify adjusting these constants after inspecting outer-test performance.

**Final pre-registration requirement:** correct §R4’s forecast-hour and support-envelope language, and close the five execution items above. Once those are frozen, I would approve fitting the three registered candidates under the narrowed scope. The user’s separate fitting authorisation remains the final gate.

## Round 12 — 2026-11-05: §R5 revision certification

Questions put to Codex (fresh thread; the complete revised REGISTRATION.md embedded in the prompt): (1) certify item-by-item whether each round-11 execution item — (i) configuration budget vs fitting ledger, (ii) full-pipeline nesting exclusion, (iii) complete candidate objectives, (iv) frozen tuning and scoring details, (v) bootstrap execution — is now closed executably as written, naming any residual ambiguity precisely; (2) whether the §R4 claim-scope language is consistent with round 11's corrections (8.8% as HRRR-arm exposure; forecast-hour overlap unestablished; paired-cohort envelope with the full-gold rectangle labelled); (3) whether the registration is fully executable as written, listing every remaining mid-study choice; (4) whether anything beyond the user's separate fitting authorisation still blocks fitting the three registered candidates under the narrowed scope. Codex's response, verbatim:

**I would not yet certify the registration as fully executable.** The substantive closures are largely sound, but several decision-relevant execution details remain unspecified, alongside one direct textual contradiction.

1. **(i) Configuration budget and fitting ledger — CLOSED under the stated counting definitions.**

   The configuration budget is now clearly separated from executed fits. The arithmetic is consistent:

   - ENS-EB refits: \(27\times36+4=976\).
   - Adapter fits: \(27\times63+15=1,716\).
   - Tree fits: \(976\times12=11,712\).
   - EB-layer derivations: \(976\times2=1,952\).

   Counting C3’s two stages together as one adapter fit is explicitly defined. The temporal reuse is also explicitly accounted for. These are registered execution counts, rather than minimum counts achievable through additional caching.

2. **(ii) Full-pipeline nesting — CONSTRUCTION CLOSED; one contradictory invariant prevents literal certification.**

   The pseudocode correctly regenerates adapter-training predictions inside T′ and generates validation predictions from a fit on T′. Validation exclusion therefore extends through the trees and EB layers. The temporal reuse is valid: the generators for B3/B4 and the predictor for B5 all exclude B5.

   However, the invariant says:

   > “during tuning neither does V … not to the trees, not to any EB node, not to the adapter, **not to lambda selection**.”

   That contradicts §R6(iv), which selects λ using validation MAE. Validation data necessarily inform λ selection.

   Replace that restriction with: **V participates only in validation scoring and λ selection; it contributes to no base-model fit or adapter parameter estimation at a fixed λ.** No additional nesting level is needed.

3. **(iii) Candidate objectives — STATISTICAL DEFINITION CLOSED; numerical estimator not completely frozen.**

   The revised objective resolves the substantive questions: mean-loss normalisation, natural parameter units, penalised γ, unpenalised regional shifts, fixed shared parameters during regional estimation, and estimate → shrink → clip. The previous rescaling freedom is removed.

   The remaining problem is:

   > “Nelder–Mead from the zero correction, fixed tolerance 1e-8, iterates projected onto the stated parameter boxes.”

   This does not uniquely specify a numerical procedure. An implementer still chooses the optimiser implementation, initial simplex, interpretation of tolerance, stopping limits, and precise bounds/projection behaviour. The treatment of nonconvergence also remains unspecified. Flat or nonunique regional minima make deterministic solution selection relevant.

   Freeze an exact optimiser callable/version and options—or an equivalent complete algorithm reference—including termination and unsuccessful-fit handling. A pinned deterministic solver can supply solution selection; a separate global-optimisation requirement is unnecessary.

   There is also a **nonblocking prose correction**:

   > “the inner OOF rows cover every training event exactly once”

   This is false for temporal fitting because of burn-in. §R6’s governing definition, **ESS_r = |S_r|**, already resolves execution: temporal final-fit ESS counts rows from B3–B5, and tuning ESS counts rows from B3–B4. Correct the explanatory sentence without changing that definition.

4. **(iv) Tuning and scoring details — MOSTLY CLOSED; two details need tightening.**

   The supplied references and constructions freeze outer assignments, station folds, temporal blocks, seeds, pooled validation MAE, λ tie-breaking, and C0-based predicted-SLR bands.

   Two remaining points prevent an unconditional executable verdict:

   - **Elevation endpoint membership.** The labels  
     > “<1000, 1000–1500, ≥1500 m”  
     should explicitly make the middle band **1000 ≤ elevation < 1500**. Otherwise the range notation leaves possible overlap at exactly 1500 m. This requires no threshold change.

   - **Integrity-check comparator.** The pseudocode defines `base_X` using HRRR inputs, then specifies:  
     > “integrity gate vs the queue-1 cache (max abs diff <= 1e-6)”  
     Name the exact cache artifact and prediction field, including its input arm. The supplied text does not establish that the comparator contains HRRR predictions. If it contains ERA5 predictions, the integrity check must compare separately generated ERA5 predictions against that reference; an HRRR-versus-ERA5 comparison would test something different.

   The optimiser details identified under (iii) also need freezing, but are not a separate additional issue here.

5. **(v) Bootstrap execution — CORE PROTOCOL CLOSED; composite-statistic and numerical execution details remain.**

   Shared cluster multiplicities across candidates, families and repeated appearances are correctly specified. The either-interval rule and candidate-level inconclusive precedence are explicit. Empty individual-statistic samples and the 1,000-valid-replicate threshold are also specified.

   Two gaps remain:

   - **Four-family composite and its validity rule.** §R6(v) says:

     > “Every registered statistic is recomputed … MAE_C = Σ w·|ŷ_C − y| / Σ w over the statistic’s rows”

     That formula directly defines a pooled MAE, but the registered four-family statistic is an **unweighted mean of four separately normalised family gains**. Explicitly preserve:
     \[
     G_b=\frac14\sum_{f=1}^{4}G_{f,b}.
     \]

     The degenerate rule currently addresses only a statistic whose row set has total weight zero. It does not explicitly handle a composite whose union has positive weight but one constituent family has zero weight. Freeze that **a four-family replicate is valid only when all four family gains are defined**; otherwise it contributes no composite value. Neither pooling all families nor averaging only the surviving families is acceptable.

   - **Reproducible draws and percentile calculation.**  
     > “B = 2000, seed 20261105”  
     and  
     > “95% percentile interval”  
     leave the RNG implementation, cluster ordering, allocation of random streams between clusterings, and percentile interpolation convention unspecified. Freeze these through an exact routine/reference or explicit settings. They can affect classification near a decision boundary.

**The §R4 claim-scope language is now consistent with the round-11 corrections.** It identifies 18/204, or 8.8%, as HRRR-arm exposure; distinguishes daily-window leads from forecast hours; states that forecast-hour overlap is unestablished; labels 67.6% against the full-gold rectangle; and distinguishes the paired-cohort figures of 66.2% overall and 38.9% within the HRRR arm. It also expressly limits the two-feature support interpretation.

**Overall verdict: not yet fully executable as written.** The remaining execution choices are the optimiser procedure and failure handling, exact elevation endpoint membership, integrity-check comparator, composite-bootstrap validity handling, and bootstrap RNG/percentile conventions. The λ-selection invariant also needs correction. The temporal ESS explanation needs cleanup, although §R6 already determines its implementation.

These specification closures still block fitting under a fully frozen registration, independently of the user’s outstanding authorisation. No new candidate, changed constant, revised adoption bar, or broader scientific redesign is required. §R5’s statement that **only** user authorisation remains is therefore premature.

## Round 13 — 2026-11-05: §R5 revision certification

Questions put to Codex (fresh thread; the complete revised REGISTRATION.md, now including the §R7 round-12 closure, embedded in the prompt): (1) item-by-item, whether the six round-12 residuals — the λ-selection invariant, the optimiser freeze, the temporal ESS prose, the elevation endpoints, the integrity-check comparator, and the bootstrap composite-validity + RNG/percentile freezes — are now closed as written; (2) whether the registration is fully executable as written; (3) whether anything beyond the user's separate fitting authorisation still blocks fitting the three registered candidates under the narrowed scope. Codex's response, verbatim:

**Not yet fully executable as written.** Five residuals are closed; the optimiser residual still leaves consequential implementation and failure-handling choices.

1. **λ-selection invariant — CLOSED.**  
   The revised invariant correctly permits V to determine validation scores and λ selection while excluding it from base-model fitting and adapter parameter estimation at a fixed λ. It now agrees with §R6(iv).

2. **Optimiser freeze — NOT FULLY CLOSED.**  
   The method, starting point, initial-simplex construction, numerical tolerances, stopping limits and prohibition on altered-setting retries are specified. Three gaps remain.

   **Boundary handling:** §R7(a) says:

   > “Parameters are clamped into their registered box at every objective evaluation … the returned point is the solver's best in-box result.”

   This permits two different procedures: passing native `bounds` to SciPy, which clips simplex vertices, or running an unbounded simplex against `J(clip(x))` and clipping the returned parameters. These can follow different search paths and apply `xatol` to different coordinates. SciPy explicitly documents vertex clipping for native bounds. [SciPy Nelder–Mead documentation](https://docs.scipy.org/doc/scipy/reference/optimize.minimize-neldermead.html)

   **Required closure:** specify which procedure is used, including the `bounds` argument or its omission, the objective wrapper, and the exact mapping from `result.x` to fitted parameters. “Best in-box result” alone does not determine those choices.

   **Implementation version:** §R7(a) says:

   > “The scipy version is the training venv's at fitting time and is recorded in the executed ledger.”

   This specifies provenance recording, but leaves the library revision mutable until fitting. For the claimed implementation freeze, identify the SciPy version or an immutable training-environment reference before fitting.

   **Failure propagation:** §R7(a) says:

   > “A λ with any failed tuning fit is ineligible for selection for that candidate and outer instance”

   and:

   > “if the final refit at a selected λ fails, the candidate is reported fit-failed for that outer instance.”

   It does not specify the branch where **all four λ values are ineligible**, or how an outer-instance failure determines the candidate’s overall judging status. An implementer must decide whether to terminate that candidate, report partial results, or attempt a reduced-set judgment.

   **Required closure:** define the no-eligible-λ outcome and its propagation through scoring and selection. A sufficient rule would mark that candidate/outer instance fit-failed, make any candidate lacking a complete registered prediction set ineligible for adoption or nomination, prohibit reduced-cohort judgment, and permit the other candidates to proceed. Distinguish fit failure from statistical rejection and inconclusiveness.

3. **Temporal ESS prose — CLOSED.**  
   The text now correctly distinguishes complete station-family OOF coverage from temporal burn-in. ESS counts the actual rows: blocks 3–5 for the final adapter fit and blocks 3–4 for temporal λ-validation.

4. **Elevation endpoints — CLOSED.**  
   The bands explicitly assign 1000 m to the middle band and 1500 m to the upper band, without overlap or a gap.

5. **Integrity-check comparator — CLOSED as a comparator specification.**  
   The registration identifies the queue-1 cache, instance O, ENS-EB prediction and ERA5-input arm. It requires comparison against the **same refit’s ERA5-input predictions on X**, with the stated tolerance. The HRRR predictions are correctly excluded from that comparison. This certifies the specified comparison, not the existence or contents of the referenced artifact.

6. **Bootstrap composite validity and RNG/percentile conventions — CLOSED.**  
   All four family gains must be defined for a composite replicate. Cluster ordering, separate seeded generators, draw calls, sequential replicate allocation and linear percentile interpolation are specified. §R6’s precedence resolves the secondary seed’s difference from §R3’s abbreviated seed statement.

**Overall verdict: certification withheld pending completion of residual 2.** An implementer still needs to choose the optimiser’s boundary procedure and resolve unhandled failure branches; the numerical implementation identity also remains unfrozen.

Those specification gaps still block certification beyond the user’s outstanding fitting authorisation. Item (i) remains closed, and the narrowed claim scope remains consistent. §R5’s conditional statement that only user authorisation remains has therefore **not yet taken effect**.

## Round 14 — 2026-11-05: §R5 revision certification

Questions put to Codex (fresh thread; the complete revised REGISTRATION.md, now including the §R8 round-13 closure, embedded in the prompt): (1) whether the three round-13 optimiser gaps — boundary procedure, implementation identity, failure propagation — are now closed as written; (2) whether the registration is fully executable as written; (3) whether anything beyond the user's separate fitting authorisation still blocks fitting the three registered candidates under the narrowed scope. Codex's response, verbatim:

1. **Boundary procedure — CLOSED.** §R7(a) now fixes native `bounds`, excludes an objective-clipping wrapper and post-hoc boundary repair, and takes `result.x` exactly. There is no remaining choice between the two previously permitted procedures. For C3 stage B, the solver’s returned scalar is **δ̂_r**; the separately registered estimate → shrink → clip sequence then produces **δ_r**.

2. **Implementation identity — CLOSED.** SciPy **1.18.1** is explicitly pinned, with verification before any fit, abort on mismatch, and ledger recording. “The training venv’s version” no longer leaves the revision mutable because the numeric pin governs. The named version is an [officially released SciPy version](https://scipy.org/news/).

3. **Failure propagation — CLOSED.** Both previously missing branches are specified: no eligible λ and failure of the selected-λ final refit produce a candidate/outer-instance fit failure. Any such instance makes the candidate terminally **fit-failed**, ineligible for adoption or nomination, with no partial-instance judgment. Other candidates proceed, and fit failure remains distinct from statistical rejection and inconclusiveness.

**Overall verdict — CERTIFIED.** The supplied registration is now fully executable as a fitting-and-judging protocol. The amendments close the sole outstanding round-13 residual without reopening the previously closed provisions. No further study-specification choice or registration amendment is required before implementing the registered procedure.

Beyond the user’s separate fitting authorisation, **no unresolved design-registration blocker remains** for C1–C3 under the narrowed §R4 scope. Execution must satisfy the registered version and integrity gates and follow the prescribed failure handling. This certification does not grant fitting authorisation or change the production and independent-confirmation gates.


## Round 15 — 2026-11-05/06: correction-study fitting results

Questions put to Codex (fresh thread; the complete fitting results embedded in the prompt — execution fidelity and ledger reconciliation, the three verdicts with full clause evidence, the structural support analysis, and the C2/C3 LORO-equality and Rockies details): (1) whether the three INCONCLUSIVE verdicts correctly apply the registration (clause definitions, straddle semantics including the C1-vs-C2/C3 Rockies asymmetry, support thresholds, precedence, parsimony); (2) closure versus follow-up — close the study, amend the §R4 support thresholds under a new registration, or designate a candidate of record for a future DESIGN §6 independent confirmation; (3) whether anything in the execution record warrants audit or a caveat before closure. Execution record as reported to Codex: SciPy 1.18.1 gate verified; executed ledger exactly the registered ledger (976 ENS-EB refits, 1,716 adapter fits, 0 failed; per-instance counts exact for all 28 instances); integrity max abs diff 8.88e-15 across all instances; 434-test suite green; implementation committed as d728871. Verdicts: C1, C2, C3 all INCONCLUSIVE (temporal clause support-inconclusive — frozen test span is water year 2026 only; regional clause support-inconclusive — Alaska 17 and Sierra Nevada 7 paired stations; C2/C3 additionally carry the binding Rockies interval straddle), nominee none, no candidate fit-failed, no binding clause failed on its point estimate, all 83 binding slice guards passed. Codex's response, verbatim:

**Q1 — Verdict review.** I uphold **C1: INCONCLUSIVE; C2: INCONCLUSIVE; C3: INCONCLUSIVE; nominee: none**, against the registration as reproduced here. One regional clause interpretation needs an explicit registration citation, but it cannot change the overall verdicts.

- **Temporal support:** one evaluation water year cannot satisfy a three-water-year minimum. More events, dates, bootstrap draws, or refits cannot repair that deficiency.
- **C1 Rockies:** the point gain, −0.0458, clears the −0.05 bar. Both intervals extend below −0.05 but remain entirely negative. Under the stated rule requiring an interval to reach both unacceptable harm and zero-or-better gain, neither straddles the band. The registered pass is therefore correct. It is nevertheless evidence of deterioration, with uncertainty extending into unacceptable harm. A procedural pass must not be described as demonstrated regional safety.
- **C2/C3 Rockies:** the reported station-cluster interval reaches below −0.05 and above zero. That satisfies the registered straddle condition and makes the sub-clause inconclusive.
- **Positive-gain thresholds:** C1’s four-family and pooled-LORO point estimates clear their bars. Their intervals do **not** establish +0.03 and +0.02 as lower confidence bounds. Passing is consistent with the stated point-estimate rule; the record should preserve that distinction.
- **Precedence and parsimony:** inconclusive precedence is applied consistently, although no reported point-estimate rejection competes with it here. Parsimony cannot select an adopted nominee when no candidate is eligible.

The regional qualification is this: **“report-only” does not by itself imply that the parent regional clause is inconclusive.** The ≥30-station threshold establishes which regions bind. A separate coverage requirement must establish that insufficient support in Alaska or Sierra Nevada blocks the parent clause. Cite that requirement explicitly. If §R4 merely excludes unsupported regions from binding assessment, correct the parent clause’s support-inconclusive label. All three overall verdicts would remain inconclusive because of temporal support; C2/C3 would additionally retain their Rockies blocker.

**Q2 — Ruling on closure and follow-up.** I endorse **(a) closure plus (c) prospective designation of C2 for independent confirmation**. I do not endorse lowering support thresholds to obtain acceptance from these results.

Close the present study with no adopted nominee, nothing packaged or served, and production calibration disabled. Replace “strongly positive on every evaluable clause” with:

> All candidates showed positive four-family and pooled-LORO gains, passed the reported point-estimate checks and all 83 binding slice guards, but did not establish adoption eligibility under the registration. C1 showed Rockies deterioration; C2/C3 retained unresolved Rockies harm uncertainty.

C2 is a reasonable **candidate of record for future confirmation**, explicitly selected using this study’s results. That designation is separate from the registered adoption nomination. C3’s additional four-family gain is approximately 0.0045 SLR units. Its reported advantages also include temporal and unseen-station performance, so the comparison should not describe its advantage as confined to randomised evaluation. Rounded LORO equality does not establish equivalence. Nevertheless, choosing C2’s simpler specification for one independent confirmatory test is defensible.

The DESIGN §6 confirmation addendum should contain:

1. **A single locked comparison: C2 against C0.** Freeze the candidate equations, features, transformations, constraints, loss, regularisation grid, selection and tie rules, and baseline construction. Specify whether confirmation tests a frozen fitted artifact or a learning procedure with prescribed refitting. The 28 instance-specific fits are not automatically one deployable artifact.

2. **An independent evaluation cohort.** Previously judged outcomes must not become independent confirmation evidence. Prespecified training may use existing data where permitted, but test outcomes must remain unused for candidate selection, tuning, and protocol amendment. Untouched historical data could qualify if it satisfies independence and temporal-order requirements.

3. **A support feasibility gate before fitting or judging.** Require ≥3 distinct water years in the temporal **evaluation set**, and ≥30 eligible stations in every regional paired-LORO evaluation slice required for the original coverage claim, including Alaska and Sierra Nevada. Apply all other registered support requirements. Count support in the relevant evaluation slices, not across training and testing together.

4. **Frozen sampling, splits, and estimands.** Specify the population, gold screening, HRRR/ERA5 pairing, station identity handling, temporal boundaries, four-family weighting, regional aggregation, slices, and overlap rules. Enforce the station, region, and time separation appropriate to each generalisation claim. Determine eligibility without examining candidate gains.

5. **A precision plan, especially for Rockies.** Three water years and thirty stations are minimum support conditions, not guarantees of a decisive result. Plan sample size and clustering to assess the registered benefit and harm margins with useful precision. Use conservative assumptions rather than treating the selected candidate’s observed gains as assured.

6. **A complete decision and stopping rule.** Preserve the existing adoption rules unless a replacement is explicitly justified in a new protocol. Fix bootstrap procedures, uncertainty semantics, precedence, accrual horizon, and analysis timing before judging new outcomes. If C2 is inconclusive or unsuccessful, do not automatically switch to C3 using the same confirmation set.

A principled version of **(b)** is possible, but “precommitted before new data” is necessary rather than sufficient. A new protocol must justify its support requirements through the scientific target, dependence structure, precision, and error control. A narrower geographic claim could also be legitimate, with unsupported regions explicitly outside its scope.

Any such protocol must be identified as informed by these results, preserve this study’s verdicts, and receive independent evaluation. Thresholds chosen simply to admit 17 Alaska stations, seven Sierra stations, or one water year would be cosmetic. A prospective revision of the unusual harm-straddle rule could also be considered; it cannot be applied retrospectively.

**Q3 — Execution caveats and focused audit.** The supplied execution evidence is strong. The ledger arithmetic reconciles: 27 instances × 36 refits plus four temporal refits gives 976; 27 × 63 adapter fits plus 15 temporal fits gives 1,716. The environment gate, exact instance counts, zero failed fits, and ERA5 reconstruction agreement support execution fidelity. Nothing supplied warrants a blanket rerun.

Before closure, resolve or document these specific points:

- **Regularisation selections:** replace “one further instance” with its identifier and archive the complete selection table and tuning losses. Confirm the objective scaling, penalty implementation, and largest-λ tie handling. The concentration at λ=3 is not itself suspicious. If 3 is a grid endpoint, record that limitation without extending the grid for this study. These selections also do not automatically authorise replacing future nested tuning with a universal λ=3.
- **C2/C3 LORO equality:** report full-precision values and explain whether the equality follows from the registered model construction or is an empirical near-equality. Centred regional shifts do not generally cancel under absolute-error loss. Verify the prescribed treatment of a held-out region before attributing equality to “pooling out.”
- **“Liquid-term fix”:** C2 improves the Rockies point gain over C1 by 0.0375, but its own gain remains negative and its interval still includes unacceptable harm. Call this attenuation of the observed deterioration, not a resolved regional failure. Attribution specifically to the liquid term requires the candidate definitions and refitting differences to support that interpretation.
- **Transfer-penalty accounting:** document the denominators and weights connecting item 7’s +0.5141 pooled penalty, family-specific penalties, and the equal-family gain. The reported residual penalties reconcile with the candidate gains at the supplied precision; different aggregation schemes should remain clearly labelled.
- **Integrity scope:** matching ERA5 predictions validates reconstruction of that reference path. Retain the audit evidence for adapter training isolation, validation-only λ selection, and HRRR pairing and units; the ERA5 gate alone does not establish those properties.

**Required amendments to the study record.**

First, correct the round-14 feasibility assessment. **The computation was executable, but the frozen design could not produce an adoption verdict under the temporal requirement.** That contradiction was discoverable before fitting and should have been flagged in my round-14 review. The regional deficiency adds another structural blocker if the parent coverage requirement is confirmed. Record this as a design-review oversight, not an execution deviation or candidate failure.

Second, retain a complete clause table showing support counts, binding/report-only status, point thresholds, both bootstrap intervals, and the exact reason for each verdict. Include the regional parent-clause citation.

Third, replace the blanket positive-clause claim and “Rockies fix” wording, and resolve the selection-table and LORO-equality details above.

Finally, record two separate decisions: **the registered study is closed without adoption; C2 is selected for a future, independently registered confirmation.** The original study’s verdicts remain unchanged by subsequent work.


---

## Addendum (2026-11-10) — ENS-EB UI rework review (Codex, fresh threads)

Scope: remove the dedicated `ModelForecastPanel` card so ENS-EB is presented like every other snow-physics method — only through the Physics-menu method selection and the standard day displays (DateRibbon marks, ForecastDashboard Reference Fresh Snow trend caption, CurrentWeatherCard note). Labelling duties moved inline via a new pure helper `modelWindowTrendLabel(mark)`; `modelWindowStatuses` (panel-only) removed; `DayModelWindowMark` gains `reducedFeatureVariant`.

- **Round 1 (20,823 tokens): REJECT — one MAJOR.** The new caption appended the same unqualified "<timestamp>Z cycle" to every arm; for the Open-Meteo arm that timestamp is an *anchor* cycle and the panel had explicitly stated the arm's forecast instance is not cycle-verified. "Reduced-feature variant" does not carry that distinction, and the helper comment ("the cycle the value was computed from") overstated the provenance. All other aspects explicitly cleared: gating, arm preference, hourly rescale, statuses removal, flag propagation, dead code.
- **Fix:** for the Open-Meteo arm the caption now reads "… · anchored to <timestamp>Z cycle · Open-Meteo cycle unverified"; comment corrected; tests assert the distinction (and that GRIB labels carry no "anchored to").
- **Round 2 (8,791 tokens): ACCEPT.** "The Open-Meteo test requires both `anchored to` and `Open-Meteo cycle unverified` in the returned inline label, preserving the distinction independently of `reduced-feature variant`. … No new defect is evident." (Noted reliance on the reported passing tests for the composed label output.)

Verification on the final tree: typecheck clean, vitest 134/134, production build succeeded, lint 0 errors.


---

## Addendum (2026-11-10) — ENS-EB day-total / hourly-bars mismatch fix (Codex, fresh thread)

User report: "the hourly snow bar does not add up to the predicted snowfall number for the day." Diagnosis (scripted reproduction through the production functions, Palisades base, stored serving records): for physics methods `groupData` derives `totalSnowfall` as the sum of the calendar day's hourly snowfall (measured diff 0.0000), but under the ENS-EB method `applyModelMethodToDays` replaced the total with the serving record of the 24 h window ending 07:00 local — a span straddling two calendar days. The 584b62b rescale made each *window's* hours sum exactly to its record, yet the UI compares on the calendar-day axis: blend 2026-11-01 displayed 10.72 cm vs a 2.44 cm bar sum; literal ECMWF 2026-11-02 0.44 vs 4.95; even a Kuchera-fallback day was off (+0.58) because its afternoon hours were rescaled into the next window while its total stayed the raw sum. Not rounding (window sums exact to 1e-9). The defect predates the UI rework; its tests only ever pinned the window invariant.

Fix: the per-window rescale is untouched (each window's hours still sum exactly to its record), and the displayed day total now follows the calendar day — re-derived in `applyModelMethodToDays` as the sum of the day's final hourly series (positive part), exactly `groupData`'s derivation for the other methods; days carrying no hourly series keep the record total. New regression suite `model-day-sum.test.ts` pins the invariant for blend / literal ECMWF / literal HRRR (model and fallback days), a closed-form straddle value, the physics path, a DST fall-back day (both 01:00 instants), and the array-boundary allocation behaviour.

- **Codex review (16,672 tokens): ACCEPT-WITH-FIXES.** The arithmetic change was upheld on every specific question (re-derivation coverage, zero records, downstream `avgSlr`/threshold coherence, preserved arm preference and gating). One MAJOR: the day-level provenance label (`modelWindowTrendLabel`) named only the morning window's arm — or claimed an unqualified Kuchera fallback — while the total now aggregates both windows. Fixed as prescribed: the caption now leads with "Calendar-day snowfall · ENS-EB windows where available, Kuchera elsewhere" and scopes the arm / reduced-feature / cycle segments explicitly to "the 24 h window ending 07:00 local"; the CurrentWeatherCard source note gained the "calendar-day total" qualifier. One MINOR: DST-duplicate and boundary-allocation test pins — added (above).

Verification on the final tree: typecheck clean, vitest 140/140, lint 0 errors in changed files. Production build skipped: the user's own `next dev` server is live on port 3000 and shares `.next`.

---

## Addendum (2026-11-10) — California Ikon/Epic catalogue expansion (Codex, two fresh threads)

User instruction: include ALL California resorts on the Ikon and Epic passes in the ENS-EB serving catalogue. Verified rosters (official sources, fetched 2026-11-10): Ikon CA per ikonpass.com/en/destinations = Palisades Tahoe, Mammoth Mountain, June Mountain, Big Bear Mountain Resort (with Bear Mountain and Snow Summit ALSO listed as separate destination entries), Snow Valley, and Sierra-at-Tahoe (new 2026-27 partner — not in the user's expected list, but on the official roster, so completeness wins); Epic CA per epicpass.com = Heavenly (CA/NV), Northstar California, Kirkwood. Delta: 8 resorts x base/mid/summit = 24 points, catalogue 11 -> 19 resorts (33 -> 57 points), following the Mount Rose precedent (b1dca04): geometry from the app's ski-areas.json entries under the established "estimated" convention (base=min, mid=mean, summit=max at the resort coordinates), REGION_BY_SLUG -> sierra-nevada for all 8. Big Bear = TWO entries (bear-mountain, snow-summit): Ikon lists the mountains separately, the app locates them separately, and a dedicated test pins disambiguation inside the 0.05-degree match radius (0.031 degrees apart, resolved by nearest distance + the 100 m elevation gate). The three San Bernardino resorts' registry match_notes disclose that they are served under the Sierra region/regime as the catalogue's California calibration proxy (no Southern California region exists in the gold pool).

Proof batch: the old anchor cycle (2026-10-31 12Z) is no longer reproducible (its ECMWF open-data files return 404 upstream), so the full catalogue was re-run at 2026-11-09 12Z rather than mixing cycles: GRIB+HRRR 684 records (ECMWF 519 predicted; HRRR 63), Open-Meteo 279/279 scored, merge verified pre-existing content unchanged; final serving JSON 57 points, 861 scored rows. Every new point scores in all three arms (ECMWF lead 10 and HRRR lead 2 legitimately missing past their horizons, the Mount Rose coverage pattern). The Open-Meteo arm's cycle field is the batch ANCHOR by established design (openMeteoArm note + "anchored to ... cycle unverified" UI wording); only the GRIB arms' cycle fields are source cycles.

Tests: the refresh re-anchored the batch-dependent day-sum/rescale suites to the analogous 2026-11-09 windows (12 tests, structural assertions unchanged). Codex round 1 (24,969 tokens): ACCEPT-WITH-FIXES — MAJOR 1: the re-anchored DST fixture constructed its duplicate hour on 2026-11-14, where first+1h IS the ordinary 02:00 instant, so the "25-hour" day held only 24 distinct instants (a real defect in the re-anchor). MAJOR 2: the review prompt's "one coherent cycle" phrasing overclaimed the Open-Meteo arm (claim corrected; the anchor semantics are the disclosed design, no code change). MINORs: nonzero HRRR->ECMWF blend coverage lost in the re-anchor; SoCal proxy disclosure; stale day-1/day-2 test titles. Fixes: the constructed test is deleted; a new batch-independent suite (model-forecast-frozen.test.ts) injects FROZEN records — the real 2026-10-31 Palisades point for the genuine fall-back day (25 distinct instants, both real 01:00s, window sums to the frozen 10.72 cm record) and a synthetic point with positive HRRR 12.0 / ECMWF 6.0 records for blend selection+allocation. Injection uses a minimal seam in model-forecast.ts (internal reads through a swappable binding; public export unchanged; production never calls the setter) because Vitest 4 here does not intercept JSON module mocks (probed: relative specifier and hoisted computed paths fall through; only a machine-specific absolute literal works). A genuine fallback day (2026-11-24) was restored to the day-sum blend test. Confirmation round (12,759 tokens): ACCEPT — all five dispositions confirmed, the seam explicitly accepted.

Verification on the final tree: vitest 147/147 (24 files), training suite 434 OK, typecheck clean, lint 0 errors (3 pre-existing vendored-file warnings). Production build skipped: the user's next dev server is live on port 3000 (standing rule).

---

## Addendum (2026-11-10) — Full North America pass catalogue expansion (Codex, two fresh threads)

User instruction: expand the ENS-EB serving catalogue to ALL Ikon + Epic resorts in North America plus notable independents. Catalogue 19 → 129 resorts (57 → 387 points), registry diff append-only (existing 19 entries byte-identical). Verified rosters (official sources, 2026-11-10): Ikon per ikonpass.com/en/destinations fetched live — 47 core NA destinations (Windham NY is absent from the official 2026-27 roster) + 8 Ikon Bonus Mountains from the official Alterra announcement (Tamarack, Devil's Head, Wild Mountain, Buck Hill, Cranmore, Jiminy Peak, Ski Butternut, Grouse Mountain; 2-day tier, flagged in match_notes); Epic owned = the 37 Vail-owned NA resorts on the official epicpass.com list (31 new; direct fetch bot-blocked, content via search index) + partners Telluride and the six RCR resorts. Non-NA partners excluded (model is trained on North American data); heli/cat without a fixed lift-served area excluded. Independents (17) are a CURATED selection under the stated criterion — on neither pass AND (≥1,000 ft vertical OR ≥500 acres) AND destination-notable: Sugar Bowl, Powder Mountain, Grand Targhee, Whitefish, Bridger Bowl, Loveland, Wolf Creek, Monarch, Purgatory, Jay Peak, Mad River Glen, Cannon Mountain, Saddleback, Whiteface, Mt. Hood Meadows, Timberline, Marmot Basin. Rejected after screening (regional day-trip hills failing the notability prong): Bogus Basin, Lookout Pass, Mt. Spokane, 49° North, White Pass, Ski Santa Fe, Angel Fire, Bretton Woods, Castle Mountain, Discovery, Gore Mountain, Windham.

Granularity (Big Bear precedent — one entry per separately located area the pass lists): Aspen gains Highlands/Buttermilk/Snowmass beside the existing aspen (= Aspen Mountain); SkiBig3 splits into Sunshine/Lake Louise/Norquay; Summit at Snoqualmie and Alpental are two entries (dataset name for the Summit's connected cluster is "Three Summits" — recorded in match_note); Boston Mills/Brandywine is ONE entry (one official line item, one ticket; the single point also serves the Brandywine app location); Snowriver is one entry (Jackson Creek Summit geometry); Sun Valley = Bald Mountain only; Loveland = Basin only.

Regimes (REGION_BY_SLUG; counts now sierra-nevada 12, rockies 41, pacific-northwest 19, northeast 38, great-lakes 18, alaska 1): Midwest → great-lakes; Ontario → great-lakes (Georgian Bay snowbelt), Québec → northeast; the Columbia Mountains wet belt (Revelstoke, SilverStar, Sun Peaks, RED, Panorama, Kicking Horse, Kimberley, Schweitzer) → pacific-northwest; Rocky Mountain ranges → rockies; Alyeska → alaska. Missouri hills take great-lakes as the disclosed nearest analogue (match_notes), following the San Bernardino precedent. Watch item (Codex): Panorama and Kimberley are the first assignments to scrutinise when the Columbia → PNW rule is validated comparatively.

New code: load_catalogue passes a per-point "country" (registry field, default "United States"; 21 Canadian resorts carry "Canada"); hrrr_cell_distances_km helper; and an HRRR domain guard in run_model_serving.py — previously the nearest HRRR cell was used with NO distance limit, so off-domain points would silently inherit border-cell data. The guard's decision is the pure helper hrrr_domain_exclusion(country, distance_km): non-US points are excluded by SERVING POLICY (HRRR is a CONUS product; the grid footprint overlaps southern Canada, so nearby cells can exist) and US points >10 km from any cell centre are excluded GEOMETRICALLY (a coarse safeguard, not an exact footprint boundary; catches Alaska). Excluded points get a marker series whose domain reason score_arm reports ahead of the horizon branch; their t2m cross-check entries are emptied; the manifest lists domain_excluded_points.

Proof batch (GRIB arms complete): whole catalogue re-anchored to 2026-11-10 00Z (the 12Z ECMWF cycle was not yet disseminated — step-144 index 404 at 19:15 UTC — and cycles are never mixed per point), run as three parallel registry shards and merged (cycles asserted identical, point ids disjoint and complete). ECMWF 3,483/3,870 predicted — the 387 missing are exactly lead 1 at every point (at 00Z the lead-1 07:00-local window starts before the cycle; horizon limit, recorded as such), so every point serves leads [2..10]. HRRR 321/774 — every US point's lead 2 (lead 1 horizon-missing, same cause); domain-excluded exactly 66 = 63 Canadian + 3 Alyeska, verified per point with zero violations. ECMWF-vs-HRRR t2m cross-check mean |diff| 1.88–2.33 °C per shard. Tracking: data/tracking/model-serving/records-20261110T00Z.jsonl (4,644 records). Merged GRIB frontend (387 points) preserved at data/slr-training-data/screened-v2/model-v1/model-serving-na-expansion/frontend-merged.json.

Open-Meteo arm BLOCKED; frontend JSON deliberately NOT replaced: the tail pull completed 5 of 7 chunks (300 locations), then Open-Meteo returned HTTP 429 — a hard stop for the provider under standing policy, no retry; the partial pull was not persisted. src/data/model-forecasts.json therefore stays at the 2026-11-09 12Z batch (57 points, all three arms intact) rather than shipping a GRIB-only file that strips the leads 11–14 tail, or hand-merging old OM rows and falsifying the cycle anchor. This commit is catalogue/backend readiness; the frontend expansion is a follow-up that must extend the saved 00Z artifact through the OM script's own anchor contract (or regenerate the GRIB arms at a fresh cycle and run the OM arm against that), together with the deferred app catalogue tests and their 00Z re-anchoring. App tests green against the old JSON are not validation of the 387-point rollout.

- **Codex review (fresh thread): ACCEPT-WITH-FIXES.** Findings: (1) Medium — the country clause is a US-only policy, not a physical-domain check; separate the reasons. Fixed: hrrr_domain_exclusion above with distinct HRRR_POLICY_REASON / HRRR_DOMAIN_REASON strings; the batch's tracking records and shard predictions were relabelled accordingly (126 records → policy reason; Alyeska's 6 keep the domain reason) — a post-review labelling fix, disclosed here. (2) Medium — 10 km threshold is a proximity safeguard, not an exact boundary; document it (done, helper docstring) and add runner/scorer regression tests (done: threshold-edge, precedence, and a score_arm test pinning the domain reason ahead of the horizon branch; the t2m emptying inside the GRIB-bound builder is noted as not unit-testable without GRIB fixtures). (3) Medium — frontend deferral is sound; keep the existing JSON for this commit; the follow-up must satisfy the anchor contract genuinely. (4) Low — no regime reassignment compelled; Panorama/Kimberley watch item recorded. (5) Low — roster arithmetic reconciles (47+8+31+7+17 = 110); independents to be described as a curated selection. **Confirmation round (8,106 tokens): ACCEPT — all five findings dispositioned.**

Verification on the final tree: training suite 444 OK (incl. 10 new guard/country tests), app vitest 147/147 (against the unchanged frontend JSON), typecheck clean. Production build skipped: the user's next dev server is live on port 3000 (standing rule).

---

## Addendum (2026-11-10) — 10-day forecast cap on all surfaces; NA rollout completed; Open-Meteo arm retired (Codex, three fresh threads)

User decision (2026-11-10): "let's only do 10 days for all forecast across all surfaces." The decision unblocks the North America rollout: the Open-Meteo tail pull that hit HTTP 429 is moot, because the tail (windows beyond the GRIB ceiling, through lead 14) is exactly what the cap removes. The verified 387-point GRIB frontend from the expansion run (cycle 2026-11-10 00Z; ECMWF leads [2..10] at every point, HRRR lead [2] at the 321 in-domain points, 66 domain-excluded) is installed byte-identical as src/data/model-forecasts.json — programmatic re-verification after install: 387 points / 129 resorts, arms exactly {ecmwf, hrrr}, cycles both 2026-11-10T00:00:00Z, no openMeteoArm metadata, zero records beyond lead 10. The Open-Meteo ENS-EB arm is RETIRED: the serving merge (run_model_serving_openmeteo.py) is out of the batch path — its tail leads are capped at MAX_SERVED_LEAD_DAY, its frontend merge writes nothing (no records, no cycle, no metadata), it validates the frontend against the cap BEFORE any scoring or output write, and its scoring code is retained for analysis only. The serving library gains the guard itself (model_serving.py: MAX_SERVED_LEAD_DAY = 10, frontend_lead_violations, assert_frontend_within_lead_cap), and run_model_serving.py asserts before its own frontend writes. Precision note (Codex): the arm's windows were ceiling+1..14, and under a 12Z cycle the western ceiling is lead 9, so the arm also used to fill the WITHIN-cap lead-10 window at western points on 12Z batches; the installed 00Z batch serves lead 10 from GRIB everywhere, and future 12Z batches will leave those windows on Kuchera — the accepted consequence of a uniform cap, recorded in both scripts' headers and in model-forecast.ts.

The cap is enforced at every layer. Fetch: api.ts gains MAX_FORECAST_DAYS = 10 and every Open-Meteo forecast request uses it (fetchWeatherData, fetchBulkWeatherData, fetchElevationTriad — best_match, the hrrr_ecmwf ECMWF sub-fetch, the dedicated ecmwf branch, the ensemble branch, and the generic-branch default; shorter native horizons unchanged; past_days untouched). Display: page.tsx applies the new pure capForecastDays (lib/data.ts) to grouped days before setForecastDays — dateStr <= today + 9 in the location timezone — as a backstop against pre-cap cache entries (raw responses are cached ungrouped and re-grouped through this same path on every load; page.tsx is the only forecast consumer of grouped days — the other groupData call site is HistorySection, history mode over explicit past dates). Model resolution: model-forecast.ts gains MAX_MODEL_LEAD_DAY = 10; modelForecastFor ignores rows beyond it and gathers only ecmwf/hrrr rows; modelArmPreference never lists ecmwf_openmeteo. The arm remains in the type union, label table, and trend-label branch so historical batches stay describable, but it can never resolve. No UI copy advertised a longer range (grep-verified), so no copy changes were needed. One adjacent behaviour fix fell out of the review: the shipped batch contains a genuinely negative record (Palisades window ending 2026-11-15, -0.0266 cm); rescaleModelWindowHours now clamps its rescale target at 0, so a negative record behaves like the established record-of-0 case (window zeroed) instead of scaling hours below zero and breaking the day-sum invariant.

Tests: the batch-dependent suites re-anchor to the 00Z shape (ECMWF [2..10], HRRR [2], no OM tail) with structural assertions intact — the day-sum invariant and rescale pins move to the windows ending 2026-11-17/18/19 (records 4.963 / 5.477 / 24.654 cm, all non-negative), the frozen DST fixture suite is untouched, and model-forecast.test.ts gains the full NA roster completeness table (129 slugs, exactly base/mid/summit per resort, per-point lead ranges, 321 HRRR points). New forecast-cap.test.ts pins the cap at every layer: constants agree (fetch/display/model all 10); a strict fetch sweep over 15 entry-point/mode cases (no swallowed rejections; branch-coverage assertions — hrrr_ecmwf must show both the gfs_hrrr and /v1/ecmwf requests, ensemble must hit the ensemble API, triad cases must carry all three latitudes in a combined request); capForecastDays boundaries; resolution cap under seam-injected lead-12 + Open-Meteo data; and a blend lead-2 fallback fixture (ECMWF present, HRRR absent — the preferred-arm-missing case the 00Z batch no longer exhibits at in-domain points).

- **Codex review (fresh thread, 43,188 tokens): ACCEPT-WITH-FIXES.** Four P2s: (1) the re-anchored blend fallback test had lost its original meaning (lead-3 fallback is not preferred-arm-missing) — fixed with the seam-injected lead-2 fixture above. (2) The retired OM script validated the frontend only after writing outputs — fixed: the cap assert now runs immediately after the frontend is loaded, before scoring and any write; the "no in-repo caller" claim was grep-verified (the batch was operator-run scripts; remaining "openmeteo" hits are unrelated profile-backfill functions). (3) The fetch sweep swallowed errors and under-covered branches — fixed per the strict sweep above. (4) The negative record was deliberately avoided by the invariant fixtures, leaving its behaviour unpinned — fixed with the rescale clamp plus a regression asserting the shipped record's value, MODEL marking, zero window sum, and the invariant. **Confirmation round 1 (20,040 tokens): F1/F4 resolved; F2/F3 partially** — the retirement wording still said "leads 11-14" in two comments (contradicting the precision note), and the sweep still caught rejections. Both corrected (ceiling-relative wording everywhere; bare awaits with branch-valid stubs). **Confirmation round 2 (12,129 tokens): ACCEPT — all findings resolved, no blocking issues remain.**

Verification on the final tree: app vitest 156/156 (25 files; was 147 before the cap), training suite 450 OK (incl. 6 new lead-cap guard tests; serving subset re-run after the wording edits, 25 OK), typecheck clean. Production build skipped: the user's next dev server is live on port 3000 (standing rule; verified listening).


## Addendum 2026-11-10 (later): Heavenly SLR inversion — display hybrid-ratio fix; record-level non-monotonicity diagnosed, not clamped

- **User report:** Heavenly, window ending 2026-11-18 — the ENS-EB display showed base SLR above mid and summit. **The records are innocent for that window:** ECMWF arm, cycle 2026-11-10 00Z, lead 9 — base/mid/summit modelSlr 13.218 / 13.500 / 14.892, liquid 1.4043 mm at all three points, Kuchera baselines 10.886 / 12.095 / 15.743. Registry geometry verified (shared lat/lon 38.937462/−119.910798; elevations 2027/2532/3037 m); the points sample one identical profile; their batch feature rows differ only through elevation-driven AGL construction and the diffs are physically monotone (t_300 −0.88/−3.82/−6.56 °C, RH falling, wind rising with elevation); re-running the packaged champion on the stored feature rows reproduces the records exactly (RF+EB and GBM+EB parts both in hand); the EB layer keys geography off lat/lon, so the three points share one EB path.
- **Root cause (display):** `DateRibbon.tsx` and `DayCard.tsx` computed the presented day SLR as `totalSnowfall × 10 / Σ hourly frozenSweMm`. On model days the numerator is the ENS-EB record total while the denominator is the live substrate's frozen SWE at the selected elevation — the hybrid recombination the `applyModelMethodToDays` docblock already forbids for totals. With ~1.4 mm of liquid, the substrate's smaller frozen SWE at the warmer base inflated the base ratio above mid/summit.
- **Fix:** new `displayDaySlr(day)` in `model-forecast.ts` — a model day presents the mark's own record SLR; every other day reproduces the substrate ratio exactly. New `displayDayIsPowder(day)` + `snowfallCm` on `DayModelWindowMark` so the powder badge's SLR and snowfall threshold describe the same window (Codex P2-2). A null-SLR model mark presents 0, never the hybrid ratio (Codex P2-1; unreachable in production — marks are created only from records carrying both SLR and snowfall). Regression suite `display-day-slr.test.ts` (8 tests) pins the Heavenly-shaped case (old formula inverts 21.1 > 15.4 > 13.8; the helper returns the ordered records).
- **Codex (fresh threads): round 1 ACCEPT-WITH-FIXES (two P2s above, 11,495 tokens); confirmation ACCEPT, no remaining findings (9,604 tokens).** App suite 164/164, training suite 450 OK, typecheck clean. Build skipped (user's dev server live on :3000).
- **Separate diagnosis (no fix applied):** record-level SLR ordering violations are widespread — **611 of 1,268 three-point windows** (patterns: mid-dip 299, mid-peak 158, monotone-down 154; ECMWF 537, HRRR 74; largest single-step inversion ~2.6 SLR; Heavenly's own violations are small, ≤0.5, on 11-11/11-12/11-15). Feature construction is verified monotone and EB is shared, and the champion's RF/GBM were trained with **no monotonicity constraints** — this is genuine tree-ensemble non-monotonicity on near-identical inputs, not a scorer bug. No post-hoc clamp was applied: remedies (isotonic/monotone recalibration per resort triple, monotone-constrained refit, or elevation-aware post-processing) change model outputs and need their own judged evaluation and the user's approval.
