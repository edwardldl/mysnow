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

## U.S. Rockies research slice

The Rockies slice is deliberately a research data cut, not an official physiographic polygon. It retains GHCN stations at or above 1,200 m under these explicit state-coordinate rules:

- Colorado and Wyoming: longitude <= -104.5
- Montana: longitude <= -108.0
- Utah: longitude >= -113.5
- Idaho: longitude >= -116.5
- New Mexico: latitude >= 35.5 and -107.5 <= longitude <= -104.5

This mask is intentionally reproducible and easy to audit. It is not suitable for deciding that every included station is in mountain terrain or that every excluded station is outside the Rockies. In the current GHCN direct-pair extract it yields 14,213 events from 146 stations. Colorado supplies 13,631 events from 134 stations; Wyoming 258/5, New Mexico 194/6, and Montana 130/1. The one available Utah station and one Idaho station are both below 1,200 m and therefore supply no retained events. There are no recent Montana events in the WY2024-WY2025 profile-alignment period.

`import-ghcn-rockies` accepts only same-row, same-date GHCN `SNOW` + `WESF` pairs that already passed upstream QC. It never derives labels from total-pack `SNWD` or `WESD`. Accumulated/multiday flags, traces or non-positive amounts, out-of-range SLR, invalid coordinates/times, and observations outside the mask are rejected with explicit reasons. A GHCN `2400` observation time is rejected conservatively rather than silently assigned to the wrong civil day. Other times are interpreted as the documented local time at the station using `America/Denver`, including daylight-saving transitions. The modeled feature window is exactly `(observation - 24h, observation]`.

`backfill-openmeteo-rockies` requests station-water-year chunks from Open-Meteo Historical Forecast (`best_match`) and caches the raw JSON plus request metadata and SHA-256 checksums. The current audited sample intentionally balances usable volume with geographical transfer evidence:

- training candidates: Crested Butte 6.2 N, Ward 4.6 NE, Fairplay 2.7 WNW, and Buena Vista 4.0 WNW;
- entirely held-out stations: Nederland 4.2 E, Laramie 1.3 SE, Cody 2.4 WSW, and Alcalde 19 N.

The split trains only on WY2024 events at the training stations and tests only on WY2025 events at the unseen stations. Regional contiguous-date storm groups are kept wholly on one side. This combines chronological, station, and storm leakage protection. Reports include overall, state, station, and out-of-Colorado metrics with sample sizes. Sparse Wyoming and New Mexico results are transfer checks, not independent regional validation.

The Open-Meteo archive is recent and model-dependent. Its `best_match` endpoint does not expose the exact contributing model for every value. Hosted/commercial redistribution rights have not been cleared. The requested profile lacks the dew point and vertical velocity needed for Cobb, so the bridge evaluates the exact MySnow production Kuchera path only. Any artifact stays `unvalidated`, is rejected by normal runtime validation, and does not enable production calibration.

Example pipeline:

```bash
python -m slr_calibration.cli import-ghcn-rockies \
  --input data/slr-training-data/normalized/observations/ghcn_qc_passed.csv \
  --output-events data/slr-training-data/normalized/observations/ghcn_rockies_events.csv \
  --output-rejections data/slr-training-data/normalized/observations/ghcn_rockies_rejections.csv \
  --output-report data/slr-training-data/normalized/observations/ghcn_rockies_import_report.json

python -m slr_calibration.cli select-rockies-split \
  --input data/slr-training-data/normalized/observations/ghcn_rockies_events.csv \
  --output data/slr-training-data/normalized/observations/ghcn_rockies_selected_split.csv \
  --train-stations <comma-separated-training-stations> \
  --held-out-stations <comma-separated-unseen-stations>

python -m slr_calibration.cli backfill-openmeteo-rockies \
  --input-events data/slr-training-data/normalized/observations/ghcn_rockies_selected_split.csv \
  --cache-dir data/slr-training-data/raw/openmeteo/rockies \
  --output data/slr-training-data/normalized/profiles/ghcn_rockies_openmeteo_profiles.jsonl \
  --output-report data/slr-training-data/normalized/profiles/ghcn_rockies_openmeteo_report.json \
  --start-date 2023-10-01 --end-date 2025-09-30 \
  --station-ids <comma-separated-audited-stations>

npx tsx training/slr_calibration/scripts/cssl-baseline.ts \
  --input data/slr-training-data/normalized/profiles/ghcn_rockies_openmeteo_profiles.jsonl \
  --output data/slr-training-data/normalized/baseline/ghcn_rockies_kuchera.csv \
  --report data/slr-training-data/normalized/baseline/ghcn_rockies_kuchera_report.json

python -m slr_calibration.cli evaluate-rockies \
  --input data/slr-training-data/normalized/baseline/ghcn_rockies_kuchera.csv \
  --artifact-output data/slr-training-data/artifacts/regional-slr-research-us-rockies-kuchera-v1.json \
  --report-output data/slr-training-data/reports/rockies-station-temporal-holdout.json \
  --held-out-stations <comma-separated-unseen-stations>
```

### Rockies run result (2026-08-26)

The source import retained 14,213 direct `SNOW`/`WESF` events at 146 stations from 17,236 upstream-QC rows (1998-06-17 through 2026-05-21). The split-aware recent candidate contained 148 events at eight stations. Eight station-water-year profile requests completed without request failures; strict complete-window checks aligned 124/148 events at seven stations. Eighteen events were rejected because the model had no snowfall in the observed window, and six because at least one of the 24 hourly endpoints was missing. The exact TypeScript Kuchera bridge produced baselines for all 124 aligned events.

The leakage-safe split trained on 103 WY2024 events / 31 regional storm groups / four Colorado stations and tested on 21 WY2025 events / 15 groups / three entirely unseen stations. On the unseen-station holdout:

| Metric | Kuchera | Calibrated |
| --- | ---: | ---: |
| Bias | 4.868 | 0.788 |
| MAE | 5.180 | 2.857 |
| Median absolute error | 4.220 | 2.420 |
| RMSE | 6.084 | 3.718 |

MAE improved by 44.8%. The Colorado holdout (17 events) improved from 4.946 to 2.909 MAE. The out-of-Colorado transfer slice contains only four Wyoming events at two stations and improved from 6.176 to 2.639 MAE. The single New Mexico candidate did not survive strict profile alignment, so there is no New Mexico test evidence. Both station slices with at least two test events improved; the one-event Cody slice also improved but is descriptive only.

The approximate nominal P10-P90 interval covered only 13/21 events (61.9%), below the nominal 80%. Trainer shrinkage support is also currently counted by row rather than independent storm, so effective sample size is overstated when stations share a storm. This candidate therefore preserves useful held-out median correction evidence but not calibrated uncertainty. The committed artifact is research-only and `unvalidated`; runtime production enablement remains false.

## Swiss Alps / EnviDat 590 workflow

The Swiss importer is `slr_calibration.swiss.import_swiss_new_snow`; profile pairing is `slr_calibration.profiles.backfill_openmeteo_swiss_profiles`; unseen-station evaluation is `slr_calibration.swiss_evaluation.evaluate_swiss_station_chronological_holdout`.

Raw ZIP, extraction, Open-Meteo responses, paired JSONL/CSV, rejection CSV, and reports live under gitignored `data/swiss-alps-slr/`. Only source, tests, documentation, and the small derived research artifact are committed. The pipeline:

1. Verifies the HN/HNW file descriptions and units (`observed`, cm/mm), exact station columns, identical timestamps, fixed 06:00 CET window, and measured station metadata.
2. Emits one explicit reason for every rejected station-day pair. It never opens HS/SWE inputs.
3. Requests `temperature_2m`, precipitation, snowfall, freezing-level height, and temperature/geopotential height at 1000/975/950/925/900/850/800/700/600/500 hPa from Open-Meteo Historical Forecast, caching responses by station/water year.
4. Runs `training/slr_calibration/scripts/cssl-baseline.ts`, which calls the current production TypeScript Kuchera implementation with calibration disabled and below-ground profile masking.
5. Fits only the earlier period and evaluates only later events from 12 entirely unseen, elevation-stratified stations (`SLF.1GT,SLF.1LS,SLF.2SO,SLF.3BR,SLF.4LA,SLF.4WI,SLF.5BI,SLF.5PU,SLF.5SA,SLF.5SE,SLF.5WJ,SLF.7SN`; cutoff 2022-01-01). The artifact is written only when overall MAE improves by at least 0.25 SLR, median AE does not worsen, at least half of eligible station and elevation slices improve, and the split includes at least six held-out stations across all three elevation bands.

The committed artifact is `unvalidated`, Swiss-Alps-only, not runtime-registered, and not production-eligible. See `docs/regional-slr-calibration.md` for metrics and limitations. Attribution: Magnusson et al., EnviDat DOI `10.16904/envidat.590`, version 1.0, CC BY 4.0.

## Sierra Nevada GHCN research slice (outside CSSL)

This slice is independent of the single-site CSSL case study. A broad CA/NV screen found 2,193 positive same-date `SNOW`+`WESF` pairs, rejected five flagged pairs, and left 157 threshold-pass events at 32 stations (2021-01-23 through 2026-02-20; 1,193.9–2,069.9 m). The executable importer deliberately narrows that screen to the 28-station `SIERRA_STATIONS` whitelist covering the Sierra western slope, Tahoe high country, and eastern Sierra. It explicitly excludes Reno, Sparks, Carson City, Dayton, Susanville, and Mount Shasta-area leakage. CSSL is not in the whitelist.

The importer reads NOAA GHCN-Daily `csv.gz/by_station` rows. It accepts only a same-station/same-date `SNOW` and `WESF` pair with blank measurement and quality flags, one identical valid observation time, positive non-accumulated 24-hour values, `SNOW >= 25 mm`, `WESF >= 2.5 mm`, and `2 < SLR < 50`. `SNOW` is millimetres; the by-station `WESF` integer is tenths of millimetres and is divided by ten. It never reads `SNWD`, `WESD`, or infers precipitation phase. Source-N exact 10:1 pairs are rejected as possible circular/default WESF. GHCN does not provide a separate WESF independence field; therefore blank source QC plus the circular-ratio rejection is the strongest reproducible screen available, and the result remains research-only. GHCN-Daily is a NOAA/U.S. government data product; local downloads and manifests are gitignored and carry URL, byte length, and SHA-256 provenance.

Reproduction (raw/cache/report paths remain gitignored):

```bash
PYTHONPATH=training/slr_calibration/src training/slr_calibration/.venv/bin/python -m slr_calibration.cli import-ghcn-sierra \
  --station-directory data/sierra-slr-research/raw/ghcn \
  --station-metadata data/slr-training-data/raw/ghcn/ghcnd-stations.txt \
  --output-events data/sierra-slr-research/normalized/sierra_events.csv \
  --output-rejections data/sierra-slr-research/normalized/sierra_rejections.csv \
  --output-report data/sierra-slr-research/reports/import.json

PYTHONPATH=training/slr_calibration/src training/slr_calibration/.venv/bin/python -m slr_calibration.cli select-sierra-split \
  --input data/sierra-slr-research/normalized/sierra_events.csv \
  --output data/sierra-slr-research/normalized/sierra_selected_split.csv \
  --train-stations US1CACV0012,US1CAED0020,US1CAMN0004,US1CAMN0006,US1CANV0051,US1CAPM0002,US1CAPM0009,USC00048760 \
  --held-out-stations US1CACV0014,US1CAED0035,US1CAMN0005

PYTHONPATH=training/slr_calibration/src training/slr_calibration/.venv/bin/python -m slr_calibration.cli backfill-openmeteo-sierra \
  --input-events data/sierra-slr-research/normalized/sierra_selected_split.csv \
  --cache-dir data/sierra-slr-research/raw/openmeteo \
  --output data/sierra-slr-research/normalized/sierra_profiles.jsonl \
  --output-report data/sierra-slr-research/reports/profiles.json \
  --start-date 2022-10-01 --end-date 2024-09-30 \
  --station-ids US1CACV0012,US1CAED0020,US1CAMN0004,US1CAMN0006,US1CANV0051,US1CAPM0002,US1CAPM0009,USC00048760,US1CACV0014,US1CAED0035,US1CAMN0005

npx tsx training/slr_calibration/scripts/cssl-baseline.ts \
  --input data/sierra-slr-research/normalized/sierra_profiles.jsonl \
  --output data/sierra-slr-research/normalized/sierra_kuchera.csv \
  --report data/sierra-slr-research/reports/baseline.json

PYTHONPATH=training/slr_calibration/src training/slr_calibration/.venv/bin/python -m slr_calibration.cli evaluate-ghcn-sierra \
  --input data/sierra-slr-research/normalized/sierra_kuchera.csv \
  --artifact-output training/slr_calibration/artifacts/regional-slr-research-sierra-nevada-kuchera-v1.json \
  --report-output data/sierra-slr-research/reports/evaluation.json \
  --held-out-stations US1CACV0014,US1CAED0035,US1CAMN0005
```

The frozen whitelist import has 6,596 raw same-date pairs, 1,529 accepted events at 26 stations, and 5,067 explicit rejects, including 75 source-N exact 10:1 pairs. The selected recent split had 245 observations at 11 stations. Open-Meteo Historical Forecast aligned 225 (20 rejected: 14 incomplete windows, six with no modeled snowfall), and the exact current TypeScript production bridge produced all 225 Kuchera baselines with regional calibration disabled and below-ground pressure levels masked.

Training used 206 WY2023 events / 22 regional storms / eight stations. Testing used 19 WY2024 events / 11 storms / three entirely unseen stations (Arnold 0.6 ENE, South Lake Tahoe 1.4 ESE, Mammoth Lakes 16.3 ESE). Baseline to calibrated metrics were: bias 0.374 -> -0.119, MAE 3.767 -> 3.222, median AE 3.517 -> 2.365, RMSE 4.675 -> 4.077, and approximate P10-P90 coverage 57.9% -> 63.2%. MAE improved at all three stations, all three geographic slices, and all three elevation slices. The 14.5% MAE reduction passes the conservative retention gate, but the holdout is small and uncertainty remains under-covered. The artifact is `unvalidated`, lives only under offline training artifacts, is not registered by the runtime, and cannot enable production calibration.
