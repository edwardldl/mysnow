# Daily SNOTEL observations + HRRR forecast dataset

One row per station-day joining **observed** snow (USDA AWDB SNOTEL daily
data) with **forecast** snow (NOAA HRRR, extracted directly from NOAA's
operational GRIB2 archive on AWS) for the 499 active SNOTEL stations
whose nearest ski area in `src/data/ski-areas.json` is within 25 km.

Built by `mysnow-slr-calibration build-hrrr-dataset`
(`training/slr_calibration/src/slr_calibration/hrrr_dataset.py`);
forecasts are populated by `extract-noaa-months`
(`training/slr_calibration/src/slr_calibration/noaa_hrrr.py`).
`validation_report.json` next to this file is the machine-readable
report of the build that produced the committed Parquet.

## Status of this build (2026-11-01) — read first

- **Observations: complete.** All 499 stations, 2023-10-01 → 2026-10-18
  (370/370 AWDB chunks fetched and cached; rebuilds make zero AWDB
  requests).
- **Forecasts (NOAA): complete.** All 25 planned months are
  extracted (2023-10 → 2024-05, 2024-10 → 2025-05,
  2025-10 → 2026-05, and 2026-10). Every CONUS station-day in those
  months has `hrrr_status = "ok"` (362,236 station-days = 347,480
  for the three full Oct-May seasons plus 14,756 for Oct 2026,
  100% coverage). Per-month extraction state (completeness, files,
  bytes, wall time) lives in `noaa-monthly/manifest.json`.
- **Oct 2026 completed 2026-11-01:** a month extraction needs GRIB
  cycles through the day after the month ends, so 2026-10 completed
  only once the 2026-11-01 00Z cycle published; its final 48 files
  were fetched in the 2026-11-01 rerun with zero failures.
- **Resume the campaign** (safe to repeat; completed months and files
  are skipped from cache at zero network cost):

  ```
  cd /home/coder/mysnow
  training/slr_calibration/.venv/bin/mysnow-slr-calibration extract-noaa-months
  training/slr_calibration/.venv/bin/mysnow-slr-calibration build-hrrr-dataset --obs-end 2026-10-18
  ```

## Why the forecast source is NOAA GRIB (switched 2026-10-18)

The dataset originally joined Open-Meteo historical-forecast values for
`ncep_hrrr_conus`. Open-Meteo enforces a daily request limit per egress
IP ("Daily API request limit exceeded. Please try again tomorrow.",
HTTP 429, confirmed 2026-10-18 09:37 UTC): 66 of the 500 planned
Open-Meteo chunks were fetched before the limit stopped the build,
leaving ~7 days of quota resets for the backlog. NOAA publishes the
HRRR output itself in the AWS Open Data bucket `noaa-hrrr-bdp-pds`
(anonymous, no daily quota); the extractor fetches only the needed
variables via each file's `.grib2.idx` byte ranges and decodes them
with ecCodes — whole GRIB files are never downloaded.

Before switching, December 2024 (a month Open-Meteo already covered
completely) was extracted both ways and benchmarked — see
`noaa-prototype/comparison_report.json` and `noaa-prototype/summary.md`:

- Kuchera SLR agreement r = 0.974 (MAE 0.90); precip r = 0.910
  (bias +1.47 mm) on 14,756 identical station-days.
- Against SNOTEL observations (QC-passed weak-label days), daily
  snowfall MAE tied: NOAA 6.48 cm vs Open-Meteo 6.46 cm.
- **The snowfall quantities differ:** NOAA `fcst_snowfall_cm` is HRRR's
  native ASNOW diagnostic differenced from its run accumulation;
  Open-Meteo's was its hourly snowfall field. ASNOW ran +2.25 cm/day
  higher in December 2024. They are not interchangeable — one reason
  the dataset is uniformly single-source NOAA, with Oct–Dec 2024
  re-extracted rather than kept from Open-Meteo.

NOAA also gives exact per-run provenance: a fixed 00Z cycle at leads
1–24 h (recorded per row), instead of the Open-Meteo archive's opaque
run blending. The Open-Meteo fetch path remains in code
(`build-hrrr-dataset --forecast-source open-meteo`) for reproducibility
but no longer feeds the committed dataset.

## Files

- `stations.csv` — the 499-station set, regenerated deterministically
  from the AWDB station list + ski-area catalog haversine ≤ 25 km:
  triplet, name, state, lat/lon, elevation (m), fixed AWDB
  `dataTimeZone` UTC offset, nearest resort name/region, distance (km).
- `daily/water_year=YYYY/data.parquet` — the joined daily rows,
  partitioned by water year (water year = Oct 1 → Sep 30, named by the
  spring year). 553,021 rows in this build: WY2024 181,454 · WY2025
  181,716 · WY2026 181,909 · WY2027 7,942 (Oct 1–18, 2026 only). The
  row set is the union of observed station-days and forecast-covered
  station-days: a station-day inside an extracted forecast month with
  no AWDB observation row (dead or gapped stations) still appears,
  with null observation fields — 465 such forecast-skeleton rows in
  WY2025 — so the total grows as more forecast months land.
  Observation values and the QC-passed count (21,994) do not change.
- `validation_report.json` — request counts, chunk outcomes, coverage
  and null-rate statistics for the producing build, including
  `forecast_source`, `hrrr.months_fed` and `hrrr.months_missing`.
- `noaa-monthly/` — one `daily_YYYY-MM.parquet` of NOAA daily
  aggregates per extracted month (476 CONUS stations) plus
  `manifest.json`. Only manifest-complete months (zero failed files,
  full 24 h on every station-day) feed the build.
- `noaa-prototype/` — the December 2024 NOAA-vs-Open-Meteo benchmark
  that validated the source switch (`comparison_report.json`,
  `summary.md`, the prototype month Parquet).
- `cache/` — per-chunk AWDB + legacy Open-Meteo response cache
  (git-ignored, ~165 MB). Kept in the workspace so resume runs make no
  redundant requests.
- `noaa-cache/` — per-file extracted NOAA station point values
  (git-ignored, ~46 MB/month) plus cached grid indices. A rerun of a
  completed month performs zero network requests (verified on
  2024-12: 0 requests, 0 bytes, 1,584/1,584 files from cache).

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
`fcst_snowfall_cm` (HRRR native ASNOW, differenced hourly and summed),
`fcst_precip_mm` (APCP differenced and summed), `fcst_kuchera_slr`
(precip-weighted daily Kuchera ratio from the exact shared
implementation, using the HRRR pressure-level temperature/geopotential
profile), `fcst_kuchera_hours`, `fcst_kuchera_weight_mm`,
`fcst_t2m_mean_c` / `min` / `max`, and `hrrr_hours` (hourly records
aggregated into the day, so partial coverage is visible).
`fcst_freezing_level_m` is always null in the NOAA build (not in the
extractor's trimmed variable set). Provenance: `hrrr_source` is
`noaa_grib_00z` on every forecast-fed row (null when no forecast was
fed), and `hrrr_lead_min_h` / `hrrr_lead_max_h` record the day's
forecast-lead range (1 / 24 for full days). `hrrr_available` is
per-station (true iff the station has any HRRR hours in the months
included in the build); `hrrr_status` is per-day: `ok`, `no_coverage`,
`fetch_failed`, `not_requested`.

## Caveats

- **Per-run forecast provenance.** Each valid hour comes from exactly
  one operational run: the 00Z cycle, lead 1–24 h. The dataset
  therefore measures ~1-day-lead HRRR skill, not analysis quality, and
  (unlike the old Open-Meteo build) the run behind every value is
  known exactly.
- **Fixed-offset day boundaries.** Hourly values are assigned to
  station-local days using each station's fixed AWDB `dataTimeZone`
  offset. This ignores DST transitions (≤ 1 h boundary shift on two days
  per year) and is a deliberate simplification; intervals follow the
  (t−1, t] daily convention of the underlying AWDB daily values.
- **Season window.** HRRR is extracted only for Oct–May of each water
  year (plus Oct 2026); Jun–Sep station-days are observations-only by
  design.
- **Alaska.** HRRR here is CONUS-only: the 23 AK stations keep their
  observations and always have `hrrr_available = false`.
- **Reading the Parquet.** `water_year` is stored both as a column and
  as the hive partition key; a plain `pq.read_table("daily/")` can hit a
  pyarrow schema-merge quirk (int32 vs dictionary). Read the four
  partition files individually and concatenate, or pass an explicit
  partitioning schema.

## Validation (this build)

- Parquet round-trips with pyarrow; per-partition row counts sum to the
  report total (553,021).
- Spot-check 784:CA:SNTL (Palisades Tahoe), 2025-01-10 → 16, against the
  raw cached AWDB rows: **0 mismatches** (SWE 464.82 mm; snow depth
  1117.6 → 1092.2 mm; precip accumulation 538.48 mm).
- NOAA spot-check 784:CA:SNTL, 2024-12-29 (the benchmark storm day):
  rebuilt row reproduces the prototype exactly — snowfall 7.968 cm,
  precip 60.1 mm, Kuchera 14.483931, leads 1/24, source
  `noaa_grib_00z`. Days in un-extracted months carry
  `hrrr_status = "not_requested"` with null forecast fields.
- Requests used by the producing build: AWDB 0, Open-Meteo 0 — every
  observation chunk was served from cache and forecasts from the
  NOAA monthly store, as designed. Extraction accounting is in
  `noaa-monthly/manifest.json`.
