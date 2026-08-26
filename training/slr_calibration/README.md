# MySnow regional SLR calibration

Offline, CPU-only training/export pipeline for multiplicative bias corrections to MySnow's physical SLR baselines. The target is:

```text
log(observed fresh-snow SLR / physical baseline SLR)
```

The physical forecast remains authoritative. The runtime multiplies it by `exp(hierarchical residual)` and clamps the result to the configured physical bounds. Artifacts are explicit about validation status and coverage.

## Normalized storm-event input

CSV and Parquet are supported. Required columns:

- `event_id`: storm identifier used as the leakage/backtest grouping key
- `station_id`, optional `site_id`
- `valid_time`: ISO-8601 timestamp
- `latitude`, `longitude`
- `observed_slr`: independently QC'd fresh-snow depth / fresh-SWE
- `baseline_slr`: MySnow physical baseline evaluated with event-time predictors
- `baseline_method`: `kuchera` or `cobb_2011`
- optional `climate_regime` (missing values become `unclassified`)
- optional `qc_passed`, `qc_weight` in `(0, 1]`

QC rejects invalid coordinates/timestamps, unsupported methods, observed SLR outside `(2, 50)`, physical baselines outside `[3, 30]`, failed source QC, duplicate event/station/method rows, and invalid weights.

## Reproducible setup and export

```bash
python3 -m venv .venv
.venv/bin/pip install --require-virtualenv -e .
.venv/bin/mysnow-slr-calibration train \
  --input tests/fixtures/smoke-events.csv \
  --output /tmp/unvalidated-smoke-v1.json \
  --artifact-id regional-slr-unvalidated-smoke-v1 \
  --artifact-version unvalidated-smoke-v1 \
  --status unvalidated \
  --source-label "synthetic test fixture; not meteorological evidence" \
  --prior-effective-sample-size 4 \
  --min-effective-sample-size 2
.venv/bin/python -m unittest discover -s tests -v
```

Rows and object keys are sorted before hashing/export; `generatedAt` is derived from the maximum training date rather than wall-clock time, so identical input/config produces identical bytes.

## Hierarchy and shrinkage

Each supported baseline has additive log-residual nodes:

1. global
2. climate regime
3. H3 regional cell
4. local site

Every child is fit on residual remaining after its parent contributions. The node estimate is shrunk toward zero with `n_eff / (n_eff + prior_n_eff)`, where weighted Kish effective sample size is used. Missing or low-support nodes fall back to the next broader usable level at runtime. Coverage counts, shrinkage, residual uncertainty, source digest, and QC rejections are embedded in the artifact.

## Bootstrap data

`inspect-bootstrap` summarizes the provided GHCN QC CSV without creating a residual model:

```bash
mysnow-slr-calibration inspect-bootstrap \
  --input ../../data/slr-training-data/normalized/observations/ghcn_qc_passed.csv \
  --output ../../data/slr-training-data/bootstrap-inspection.json
```

The current bootstrap observations do **not** contain event-aligned MySnow Kuchera/Cobb baseline predictions or pressure-profile predictors. Therefore they cannot honestly train the runtime residual target. The committed smoke artifact is synthetic, marked `unvalidated`, and the runtime gate remains off.

## CSSL Sierra research workflow

The first real-data experiment uses the UC Berkeley Central Sierra Snow Laboratory release (Dryad DOI `10.6078/D1941T`, CC0 1.0). Raw files, API caches, normalized events, and per-run reports remain under the gitignored `data/` tree. Only the small CC0 regression fixture, reusable code, documentation, and—if the holdout improves—a clearly `unvalidated` research artifact are committed.

```bash
# 1. Normalize direct fresh-snow labels. Never difference total pack depth/SWE.
.venv/bin/mysnow-slr-calibration import-cssl \
  --input-dir ../../data/cssl/v13/raw \
  --output-events ../../data/cssl/v13/normalized/cssl-events.csv \
  --output-rejections ../../data/cssl/v13/normalized/cssl-rejections.csv \
  --output-report ../../data/cssl/v13/normalized/cssl-import-report.json

# 2. Backfill recent profiles only. Requests are monthly, cached, and restartable.
.venv/bin/mysnow-slr-calibration backfill-openmeteo \
  --input-events ../../data/cssl/v13/normalized/cssl-events.csv \
  --cache-dir ../../data/openmeteo/cssl-cache \
  --output ../../data/cssl/v13/research/cssl-profile-events.jsonl \
  --output-report ../../data/cssl/v13/research/openmeteo-profile-report.json \
  --start-date 2023-10-01 --end-date 2025-09-30

# 3. Reuse the exact TypeScript MySnow Kuchera implementation, with production
#    regional calibration explicitly disabled.
npm run slr:baseline -- \
  --input data/cssl/v13/research/cssl-profile-events.jsonl \
  --output data/cssl/v13/research/cssl-aligned-baselines.csv \
  --report data/cssl/v13/research/cssl-baseline-report.json

# 4. Train on earlier complete water years and hold out the latest water year,
#    keeping every contiguous storm group wholly on one side of the split.
.venv/bin/mysnow-slr-calibration evaluate-sierra \
  --input ../../data/cssl/v13/research/cssl-aligned-baselines.csv \
  --artifact-output ../../data/cssl/v13/research/candidate-artifact.json \
  --report-output ../../data/cssl/v13/research/evaluation-report.json
```

CSSL labels require at least 2.5 cm fresh depth, at least 2.5 mm fresh SWE, 100% snow phase, a computed SLR strictly inside `(2, 50)`, and no unsafe measurement remark. The fresh SWE is `24-hour precipitation × snow fraction`; total snowpack fields are intentionally ignored. Version 13 does not actually include a published snowfall:precipitation-ratio column, so the importer records that comparison as unavailable rather than inventing it. The frozen version-13 import processed 19,724 rows from 54 water-year CSVs, accepting 1,163 event days in 686 contiguous storm groups and rejecting 18,561 rows with explicit reason codes.

The Open-Meteo source is its recent, model-dependent Historical Forecast archive. The response does not expose the exact model behind `best match`, and CSSL does not document precipitation timing precisely enough to prove exact overlap with its 08:00/16:00 PST snowboard sum. The experiment therefore uses a fixed-PST calendar-day proxy and rejects observations with no aligned model snowfall. The bridge uses MySnow's production `(t-1,t]` interval-profile averaging and 100 m elevation tolerance before computing Kuchera, then weights modeled-snow intervals by model precipitation as a frozen-SWE timing proxy. In the frozen run, 700/600/500 hPa contributed in all 342 intervals and 800 hPa in 6; lower requested levels remained underground. Available fields support exact MySnow Kuchera evaluation but not true Cobb, which requires dew point and vertical velocity.

The frozen two-water-year run found 35 model-aligned event days: 21 events / 15 storms in WY2024 for training and 14 events / 11 storms in the complete WY2025 holdout. Holdout MAE improved from 7.302 to 4.751 SLR units and median absolute error from 6.874 to 5.387, so the candidate was retained as `artifacts/regional-slr-research-cssl-kuchera-v1.json`. It remains `unvalidated`; the normal runtime rejects it.

This experiment is one-site temporal validation only. It is not Sierra-regional validation, does not justify a worldwide correction, never changes production gates, and does not authorize use of the hosted Open-Meteo API for commercial production.
