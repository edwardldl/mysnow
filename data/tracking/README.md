# MySnow snowfall tracking & SLR learning loop

This directory holds the data plane of a continuous learning loop: it tracks
**real snowfall observations** and **archived weather forecasts** at ten pilot
resorts so that observed snow-to-liquid ratios (SLR) can be paired with the
same physical Kuchera baseline the app runs, and a residual calibration
candidate can be trained and honestly evaluated over time.

Everything here is research infrastructure. **Production calibration stays
disabled** (`src/lib/snow/calibration/coefficients.ts`: `modifiersEnabled`,
`regionalBiasEnabled`, and `allowUnvalidatedRegionalArtifact` are all
`false`); tracking artifacts are always exported with status `unvalidated`.

## Mechanism

1. **Registry** (`track-registry`) — pilot resorts are resolved from
   `src/data/ski-areas.json` (Palisades Tahoe uses the app's curated default
   location in `src/lib/api.ts`). Base/mid/summit forecast points are derived
   exactly like `src/lib/snow/resort/geometry.ts`. Observation stations are
   resolved programmatically: the nearest active AWDB **SNTL** station within
   40 km (triplet, distance, elevation delta recorded; never hardcoded), and
   GHCN paired stations from the unified station registry within 40 km ranked
   by `paired_records`.
2. **Forecast snapshots** (`track-snapshot`) — for every registry point, the
   Open-Meteo forecast API is fetched for `ecmwf_ifs` and `gfs_seamless` with
   the same hourly variable set as `training/slr_calibration`'s `profiles.py`,
   7 forecast days, UTC. Raw JSON is stored gzipped under
   `snapshots/YYYY/MM/DD/` and indexed in `snapshots/index.jsonl`
   (`issued_at_utc`, `point_id`, `model`, `sha256`, `bytes`, `forecast_days`).
   One snapshot set per UTC day unless `--force`.
3. **SNOTEL ingest** (`track-ingest-snotel`) — AWDB daily WTEQ, SNWD, PREC,
   TAVG, TMAX, TMIN for the registry stations, append-deduped by
   `(station_triplet, date)` into `observations/snotel_daily.csv` (the
   existing SNOTEL CSV schema).
4. **GHCN ingest** (`track-ingest-ghcn`) — NCEI access CSVs for the registry
   GHCN stations, last two water years, written to
   `observations/ghcn_daily.csv` in the intermediate schema the existing
   GHCN importer consumes. GHCN WESF is converted from tenths of a millimetre.
   The access CSV has no observation-time field; `obs_time` is the CoCoRaHS
   standard `0700` local, matching the dominant value in the existing
   bootstrap pairs. Accumulation flags come from GHCN DAPR/MDSF fields.
5. **Event building** (`track-build-events`) — writes `events/events.csv` in
   the normalized event schema plus a `label_quality` column, and a
   `events/build_report.json` with rejection accounting.
6. **Alignment** (`track-align`, weekly) — each event is paired with a
   production Kuchera baseline (precipitation-weighted, `(t-1, t]`
   interval-mean profiles, replicating `scripts/cssl-baseline.ts`). Local
   snapshots are preferred when they cover the event window; otherwise the
   Open-Meteo historical-forecast backfill from `profiles.py` is used. The
   source is recorded per row (`baseline_source`, `profile_source`,
   `profile_model`). Output: `alignment/trainer_rows.csv` in the existing
   baseline CSV schema.
7. **Training** (`track-train`, weekly) — the existing hierarchical
   log-residual trainer fits an `unvalidated` candidate; when data allow, a
   storm-grouped temporal (water-year) holdout reports Kuchera-baseline MAE
   vs candidate MAE. Outputs land in `artifacts/`.
8. **Status report** (`track-report`) — `reports/status.json` + `status.md`:
   coverage by source/region, stations observed in the last 3 days, snapshot
   days, event counts by `label_quality`, latest holdout metrics, and the
   live production-gate state read from `coefficients.ts`.

`track-run` executes snapshot + ingests + events + report in order (building
the registry first if missing).

## Label quality tiers

- **`direct`** — paired new-snow depth + new-snow SWE measured for the same
  24-hour window (GHCN SNOW+WESF, incl. CoCoRaHS). These are the only labels
  treated as ground truth for SLR.
- **`weak_snotel_diff`** — SNOTEL **total-pack** differencing. SNOTEL
  measures the whole snowpack's SWE and depth, *not* fresh snow on a board,
  so a day-over-day difference is only a proxy: it is accepted solely when
  ΔSWE ≥ 5 mm, Δdepth ≥ 2 cm, daily mean temperature ≤ 0.5 °C, depth did not
  decrease, and no rain-phase indicator fires (warm daily max or gauge
  precipitation well above the SWE gain). `observed_slr = Δdepth / ΔSWE`,
  `qc_weight` ≤ 0.30, and the source string states the differencing method.
  Rejected candidates are counted in `events/build_report.json`.

## Leakage rules

- A forecast snapshot may only be paired with an event when its
  **issue time precedes the event end** and its hourly payload covers the
  full event window; otherwise the event falls back to the
  historical-forecast archive backfill.
- Training/evaluation splits are grouped by `storm_group_id` and ordered by
  water year: no storm contributes rows to both train and holdout, and the
  holdout year is strictly later than every training year.

## Schedule

`.github/workflows/slr-tracking.yml` runs daily at 12:30 UTC
(registry + `track-run`, committing `data/tracking/**` when changed) and
weekly on Mondays at 13:45 UTC (`track-align` + `track-train` + report,
committing artifacts/reports). It needs no secrets.

## Production-enablement gates

A tracking candidate may only be considered for production when **all** of
the following hold — none is satisfied by this loop alone:

1. Held-out (station + temporal) MAE improves on the Kuchera baseline across
   multiple regions and water years, with adequate event counts.
2. The artifact is re-exported with an explicit validated status through the
   existing research review process (see `training/slr_calibration`).
3. A separate, deliberate production change flips the gates in
   `src/lib/snow/calibration/coefficients.ts`. The tracking workflow never
   edits `src/`.

## Manual commands

```sh
pip install -e training/slr_calibration
mysnow-slr-calibration track-registry
mysnow-slr-calibration track-snapshot
mysnow-slr-calibration track-ingest-snotel --start 2025-10-01 --end 2026-10-17
mysnow-slr-calibration track-ingest-ghcn
mysnow-slr-calibration track-build-events
mysnow-slr-calibration track-align
mysnow-slr-calibration track-train
mysnow-slr-calibration track-report
mysnow-slr-calibration track-run           # daily chain
```

## Data layout

```
data/tracking/
  registry.json / registry.csv   pilot resorts, forecast points, stations
  snapshots/index.jsonl          snapshot index (issued_at, sha256, bytes)
  snapshots/YYYY/MM/DD/*.json.gz raw Open-Meteo forecast payloads
  observations/snotel_daily.csv  AWDB daily totals (append-deduped)
  observations/ghcn_daily.csv    GHCN intermediate-schema daily rows
  events/events.csv              normalized events + label_quality
  events/build_report.json       acceptance/rejection accounting
  alignment/trainer_rows.csv     events paired with Kuchera baselines
  alignment/alignment_report.json
  alignment/profile_cache/       historical-forecast backfill cache
  artifacts/tracking-candidate-v1.json   unvalidated candidate artifact
  artifacts/training_report.json         holdout metrics (when computable)
  reports/status.json / status.md
```
