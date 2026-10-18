# NOAA HRRR prototype — December 2024 benchmark

Direct extraction of HRRR daily station aggregates from NOAA's operational
GRIB2 archive on AWS, benchmarked against this dataset's Open-Meteo
aggregates for the one month both sources cover completely
(December 2024), and against the SNOTEL observations in the dataset.

## Why

The Open-Meteo build is bounded by Open-Meteo's per-IP daily quota
(~66 heavy chunks/day), so the remaining backlog (Jan 2025 – May 2025,
Oct 2025 – May 2026, Oct 2026, Oct 2023 – May 2024) takes roughly a week
of quota resets. The NOAA archive (`noaa-hrrr-bdp-pds`, anonymous S3) has
no daily call quota; this prototype measures whether switching the
forecast source to NOAA is faster, and whether the numbers agree.

## Method

- **Source**: `https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.YYYYMMDD/conus/hrrr.t00z.{wrfsfc,wrfprs}f{FF}.grib2`
- **Selection**: 00Z cycle only, forecast hours f01–f24, cycles
  2024-11-30 → 2025-01-01. Every UTC instant comes from exactly one
  (cycle, fxx) pair, so lead times are exact (1–24 h; per-day min/max are
  in the parquet as `noaa_lead_min_h` / `noaa_lead_max_h`).
- **Transfer**: each file's `.grib2.idx` sidecar is parsed and only the
  wanted messages' byte ranges are fetched (HTTP Range) — never whole
  files (surface files are ~158 MB, pressure files ~430 MB). Decoding is
  ecCodes; station values are the nearest gridpoint (no interpolation),
  with grid longitudes normalized from the archive's 0–360 convention.
- **Variables**: surface TMP/DPT 2 m, APCP, ASNOW, WEASD, PRES, HGT;
  pressure TMP/HGT at 1000–500 hPa (13 levels).
- **Accumulation**: the idx descriptors verify APCP and ASNOW are
  `0-N hour acc` (run-accumulated). Hourly values are differences within
  one run (f01 against the zero baseline), never across a cycle boundary.
- **Aggregation**: identical to the Open-Meteo build — station-local days
  from the fixed AWDB `dataTimeZone` offset, (t−1, t] interval
  attribution, and the package's shared precip-weighted Kuchera
  (`tracking_align.compute_baseline_slr`) on the same 10-level subset
  (`profiles.PRESSURE_LEVELS_HPA`) the Open-Meteo profiles use.
- **Stations**: the 476 CONUS stations from `../stations.csv` (the 23 AK
  stations are outside the CONUS HRRR domain, `hrrr_available=false`).

## Semantic differences vs the Open-Meteo aggregates

1. Single fixed 00Z cycle with known leads vs Open-Meteo's blended
   archive (issue times unknown).
2. Snowfall is HRRR's native ASNOW diagnostic differenced from run
   accumulation; Open-Meteo's is its own hourly snowfall field.
3. `fcst_freezing_level_m` is null here — the freezing-level diagnostic
   is not in the trimmed variable set.
4. Observation truth in the comparison is the dataset's own weak label
   (differenced SNOTEL depth on QC-passed days, weight 0.25) — a total-
   pack difference, not a snowboard measurement.

## Files

- `daily_2024-12.parquet` — NOAA daily aggregates, same `fcst_*` schema
  as the main dataset plus lead columns.
- `comparison_report.json` — full machine-readable benchmark + ops stats
  + full-backlog extrapolation.
- `summary.md` — generated headline tables.

## Reproduce

```bash
cd training/slr_calibration
.venv/bin/pip install -e ".[noaa]"   # eccodes / cfgrib / herbie-data
cd ../..
training/slr_calibration/.venv/bin/mysnow-slr-calibration extract-noaa-hrrr
```

Extracted per-file point values are cached (gzipped JSON) in
`../noaa-cache/` (git-ignored); a rerun with a complete cache makes zero
network requests. Implementation: `training/slr_calibration/src/slr_calibration/noaa_hrrr.py`.
