# Daily SNOTEL observations + HRRR forecast dataset

One row per station-day joining **observed** snow (USDA AWDB SNOTEL daily
data) with **forecast** snow (NOAA HRRR via the Open-Meteo historical
forecast archive) for the 499 active SNOTEL stations whose nearest ski
area in `src/data/ski-areas.json` is within 25 km.

Built by `mysnow-slr-calibration build-hrrr-dataset`
(`training/slr_calibration/src/slr_calibration/hrrr_dataset.py`).
`validation_report.json` next to this file is the machine-readable report
of the build that produced the committed Parquet.

## Status of this build (2026-10-18) — read first

- **Observations: complete.** All 499 stations, 2023-10-01 → 2026-10-18
  (370/370 AWDB chunks fetched and cached).
- **HRRR forecasts: partial.** Only the months **2024-10, 2024-11,
  2024-12** are included (60/60 chunks). Every CONUS station-day in those
  months has `hrrr_status = "ok"` (43,792 station-days = 476 CONUS
  stations × 92 days, 100% coverage). All other station-days carry
  `hrrr_status = "not_requested"` and null forecast fields.
- **Why:** Open-Meteo enforces a daily request limit per egress IP
  ("Daily API request limit exceeded. Please try again tomorrow.",
  HTTP 429, confirmed 2026-10-18 09:37 UTC). 66 of the 500 planned
  Open-Meteo chunks (25 locations × one month each) were fetched before
  the limit stopped further requests; the remaining chunks were recorded
  and skipped, never hot-looped. 6 additional cached Jan-2025 chunks are
  already on disk in the (git-ignored) cache and will be reused.
- **Resume (after the daily quota resets):**

  ```
  cd /home/coder/mysnow
  training/slr_calibration/.venv/bin/mysnow-slr-calibration build-hrrr-dataset
  ```

  The build is fully cache-resumed: cached chunks cost zero requests, so
  the rerun fetches only the missing months (Jan 2025 → May 2025,
  Oct 2025 → May 2026, Oct 2026, then Oct 2023 → May 2024 — 434 chunks)
  and rewrites the Parquet + this report for the full plan. At the
  observed daily quota burn (~66 heavy chunks/day from this IP), the
  remainder may itself span more than one day; each rerun is safe to
  repeat and keeps prior progress.

## Files

- `stations.csv` — the 499-station set, regenerated deterministically
  from the AWDB station list + ski-area catalog haversine ≤ 25 km:
  triplet, name, state, lat/lon, elevation (m), fixed AWDB
  `dataTimeZone` UTC offset, nearest resort name/region, distance (km).
- `daily/water_year=YYYY/data.parquet` — the joined daily rows,
  partitioned by water year (water year = Oct 1 → Sep 30, named by the
  spring year). 550,052 rows in this build: WY2024 179,457 · WY2025
  181,251 · WY2026 181,402 · WY2027 7,942 (Oct 1–18, 2026 only).
- `validation_report.json` — request counts, chunk outcomes, coverage
  and null-rate statistics for the producing build.
- `cache/` — per-chunk API response cache (git-ignored, ~165 MB). Kept
  in the workspace so resume runs make no redundant requests.

## Columns and semantics

Station keys: `station_triplet`, `station_name`, `state`, `latitude`,
`longitude`, `elevation_m`, `data_tz_offset_hours`, `nearest_resort`,
`distance_km`; plus `date` (station-local day) and `water_year`.

Observed (AWDB daily DURATION values, converted to metric):
`swe_mm`, `snow_depth_mm`, `precip_accum_mm` (water-year accumulation),
`tavg_c`, `tmax_c`, `tmin_c`. Null rates in this build are ~0.5–1.3%.

Derived per station-day (vs the previous **consecutive** day only):
`delta_swe_mm`, `delta_depth_mm`, and `precip_mm` — the day-over-day
difference of the accumulated PREC series. On the Oct-1 water-year
reset the differenced value is kept and flagged `precip_wy_reset` in
`obs_flags`; negative glitch differences are nulled and flagged.
Other flags (e.g. `obs_fetch_failed`) are `;`-separated in `obs_flags`.

Weak-event QC labels: `weak_qc_passed`, `weak_qc_rejection`,
`observed_slr_diff`, `weak_qc_weight`. These come from the tracker's
shared weak-event gates (`tracking_events.evaluate_weak_candidate`):
the "observed SLR" is inferred by **differencing the SNOTEL snowpack**
(depth gain vs SWE gain). It is a noisy proxy — **not** a board-measured
fresh-snow SLR — and QC-passed days carry weight 0.25 for that reason.
21,994 station-days pass the gates in the full observation window.

Forecast (HRRR, aggregated to station-local days, see caveats):
`fcst_snowfall_cm`, `fcst_precip_mm` (sums), `fcst_kuchera_slr`
(precip-weighted daily Kuchera ratio from the exact shared
implementation, using the HRRR pressure-level temperature/geopotential
profile), `fcst_kuchera_hours`, `fcst_kuchera_weight_mm`,
`fcst_t2m_mean_c` / `min` / `max`, `fcst_freezing_level_m`, and
`hrrr_hours` (hourly records aggregated into the day, so partial
coverage is visible). `hrrr_available` is per-station (true iff the
station has any HRRR hours in the months included in the build);
`hrrr_status` is per-day: `ok`, `no_coverage`, `fetch_failed`,
`not_requested`.

## Caveats

- **Archived-forecast provenance.** Forecast values are the archived
  values the Open-Meteo historical-forecast API serves for
  `ncep_hrrr_conus`. They are not a per-run operational control record;
  treat them as "the forecast Open-Meteo archived for that valid time",
  suitable for retrospective SLR evaluation, not as proof of what any
  specific operational run issued.
- **Fixed-offset day boundaries.** Hourly values are assigned to
  station-local days using each station's fixed AWDB `dataTimeZone`
  offset. This ignores DST transitions (≤ 1 h boundary shift on two days
  per year) and is a deliberate simplification; intervals follow the
  (t−1, t] daily convention of the underlying AWDB daily values.
- **Season window.** HRRR is fetched only for Oct–May of each water
  year (plus Oct 2026); Jun–Sep station-days are observations-only by
  design.
- **Alaska.** HRRR is CONUS-only: the 23 AK stations keep their
  observations and always have `hrrr_available = false`.
- **Reading the Parquet.** `water_year` is stored both as a column and
  as the hive partition key; a plain `pq.read_table("daily/")` can hit a
  pyarrow schema-merge quirk (int32 vs dictionary). Read the four
  partition files individually and concatenate, or pass an explicit
  partitioning schema.

## Validation (this build)

- Parquet round-trips with pyarrow; per-partition row counts sum to the
  report total (550,052).
- Spot-check 784:CA:SNTL (Palisades Tahoe), 2025-01-10 → 16, against the
  raw cached AWDB rows: **0 mismatches** (SWE 464.82 mm; snow depth
  1117.6 → 1092.2 mm; precip accumulation 538.48 mm).
- Requests used by the producing (cache-only) build: AWDB 0,
  Open-Meteo 0 — every chunk was served from cache, as designed.
