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
