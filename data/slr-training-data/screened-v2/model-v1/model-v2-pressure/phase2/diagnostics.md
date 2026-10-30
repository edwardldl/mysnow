# Phase 2 — Step 1 diagnostics

Generated 2026-10-29 (PDT) from the pressure-level build outputs and judging
results. All figures computed from `features_pressure_mask.parquet`,
`features_pressure_core.parquet`, `results_pressure_p_core.json`, and the AGL
champion's `era5-expanded-v2/results_variants.json` (the file that carries the
AGL ENS-EB scores). Companion files: `valid_levels_by_region.csv`,
`validity_share_by_level_region.csv`, `loro_delta_by_region.csv`,
`elevation_suspects.csv`, `diag_numbers.json`.

## (i) Valid levels per region vs LORO delta — testing H1

A level counts as valid for an event when it was above ground (level pressure
below the event's fetched hourly surface pressure) at >= 12 of the 24 window
instants. Invalid levels are filled from the nearest valid level in P-core.

| Region | Events | Mean valid levels (of 13) | % all 13 valid | % <= 8 valid | % <= 6 valid | P-core LORO MAE | AGL LORO MAE | Delta MAE | P-core bias | AGL bias |
|---|---|---|---|---|---|---|---|---|---|---|
| Rockies | 9,242 | 6.15 | 0.0 | 99.8 | 58.0 | 3.5029 | 3.4271 | +0.076 | -1.0919 | -0.2431 |
| Sierra Nevada | 956 | 6.29 | 0.0 | 100.0 | 76.7 | 3.5637 | 3.3935 | +0.170 | +0.3603 | +0.3415 |
| Pacific Northwest | 5,089 | 9.85 | 6.1 | 20.8 | 0.0 | 3.5376 | 3.3792 | +0.158 | +0.9558 | +0.5825 |
| Alaska | 1,199 | 10.62 | 1.1 | 1.6 | 0.0 | 4.2056 | 3.9899 | +0.216 | -2.9447 | -2.4240 |
| Great Lakes | 19,032 | 11.54 | 1.9 | 0.0 | 0.0 | 3.4243 | 3.4392 | -0.015 | -0.5712 | -0.7121 |
| Northeast | 21,858 | 11.75 | 23.2 | 0.0 | 0.0 | 3.4938 | 3.5455 | -0.052 | -0.1005 | -0.2303 |

**Reading.** H1 fits the two high-elevation interior regions: no Rockies or
Sierra event has all 13 levels valid, and a majority of their events have six
or fewer real levels — over half the profile the model sees there is
nearest-level copies — and both regions regressed. But H1 cannot be the whole
story: Alaska has the *worst* LORO regression (+0.216) despite averaging 10.62
valid levels, and PNW regressed +0.158 with 9.85. In both of those regions the
pressure model's *bias* grew substantially versus AGL (Alaska -2.42 -> -2.94;
PNW +0.58 -> +0.96), a pattern validity counts do not explain. Conclusion:
P-surf (Step 2) is a clean test for the Rockies/Sierra mechanism; a P-surf win
there with continued Alaska/PNW regressions would confirm the two effects are
separate, and Alaska/PNW would need a different explanation (regional bias
amplification in the pressure representation) rather than more validity work.

## (ii) Elevation-suspect stations — sizing H2

Method: each event's window-mean fetched surface pressure is converted to a
barometric implied elevation (standard atmosphere); a station's disagreement
is the median |implied - GHCN metadata elevation| over its events.

| Threshold | Stations | Events | % of pool | Events by region |
|---|---|---|---|---|
| > 300 m | 94 | 5,030 | 8.77 | AK 680, GL 76, NE 362, PNW 1,895, Rockies 1,937, Sierra 80 |
| > 500 m | 33 | 1,273 | 2.22 | AK 326, GL 76, PNW 811, Rockies 9, Sierra 51 |
| > 700 m | 6 | 107 | 0.19 | AK 27, GL 21, PNW 59 |

Note the Rockies pattern: heavy at 300 m (1,937 events) but nearly absent at
500 m (9 events) — Rockies metadata and grid orography disagree moderately
almost everywhere but rarely extremely.

**Sanity checks on top offenders** (full list in `elevation_suspects.csv`):

- `US1MNLS0026` (Great Lakes, MN): metadata elevation 3,057.8 m is impossible
  — Minnesota's highest point is 701 m. Surface-pressure-implied elevation is
  287 m, consistent with the station's actual lowland setting. **Confirmed
  corrupted metadata** (the value is a feet figure sitting in the metres
  field; our copy is faithful to GHCN).
- `US1AKHB0006` (Alaska, Haines Borough): metadata 65.2 m vs implied 1,013 m.
  The borough's terrain rises steeply from the coast; a low station inside a
  0.25-degree grid cell dominated by peaks produces exactly this signature.
  **Likely grid-orography mismatch; metadata plausible.** Not provable error.
- `US1WASG0017` / `US1WASG0031` (PNW, Skagit County WA): metadata ~94 / 107 m
  (valley floor) vs implied ~946 / 954 m (North Cascades grid cell). Same
  grid-orography signature as above.
- `US1CAMN0004` / `US1CAIN0002` (Sierra Nevada): metadata 1,949 / 1,829 m vs
  implied 2,630 / 2,508 m. Both figures are plausible mountain elevations in
  steep terrain; **ambiguous**, most likely the grid cell is centred on higher
  ground than the station.

**Interpretation for Step 3.** The locked 500 m set (33 stations, 1,273
events, 2.22% of the pool) is a *mix*: a handful of provable metadata errors
plus steep-terrain stations where the ERA5 column genuinely does not
represent the label's location. Step 3 therefore tests the broader proposition
— removing events whose feature column and label location strongly disagree —
and a positive result would not by itself prove metadata error was the
mechanism. The set is concentrated in PNW (811 events) and Alaska (326), the
two regions where P-core's bias grew most, which is what makes the test worth
running despite the mixed causes.
