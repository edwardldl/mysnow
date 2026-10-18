# NOAA HRRR prototype vs Open-Meteo — 2024-12

Source: NOAA HRRR, AWS Open Data bucket noaa-hrrr-bdp-pds, 00Z cycle f01-f24, wrfsfc+wrfprs. Stations: 476 CONUS. Shared station-days: 14756.

## NOAA vs Open-Meteo (identical station-days)

| variable | n | mean bias (NOAA − OM) | MAE | correlation |
| --- | --- | --- | --- | --- |
| snowfall (cm) | 14756 | 2.2538 | 2.3796 | 0.7635 |
| precip (mm) | 14756 | 1.4672 | 1.892 | 0.9099 |
| Kuchera SLR | 4439 | 0.249 | 0.8974 | 0.9736 |

## Daily snowfall vs SNOTEL observations (QC-passed weak-label days)

| source | n | mean bias (cm) | MAE (cm) | correlation |
| --- | --- | --- | --- | --- |
| NOAA (00Z, leads 1–24 h) | 1559 | 3.2276 | 6.4751 | 0.6023 |
| Open-Meteo archive | 1559 | -6.2051 | 6.4595 | 0.6338 |

## Operations

- Files: 1488 fetched, 96 cached, 0 failed of 1584 planned
- Wall time: 4583.2 s; requests: 17888; bytes transferred: 18727662250; cache on disk: 46021521 bytes

## Semantic differences

- NOAA prototype: single fixed 00Z cycle, exact leads 1-24 h per day; Open-Meteo archive blends runs opaquely.
- NOAA snowfall: HRRR native ASNOW differenced from run accumulation; Open-Meteo: its hourly snowfall field summed.
- fcst_freezing_level_m is null in the NOAA output (not in the trimmed variable set).
- Kuchera levels identical (profiles.PRESSURE_LEVELS_HPA subset of the extracted 13 levels).
