# MySnow SLR tracking status

Generated for 2026-11-26.

## Coverage

- Pilot resorts: 10 (30 forecast points)
- Resorts with a SNOTEL station within 40 km: 7
- Resorts with GHCN paired stations within 40 km: 1 (2 stations)
- SNOTEL daily rows: 2807 across 7 stations
- GHCN daily rows: 712 across 2 stations

## Recent observations (last 3 days)

- SNOTEL 1308:UT:SNTL: latest 2026-11-25
- SNOTEL 1326:CO:SNTL: latest 2026-11-25
- SNOTEL 590:MT:SNTL: latest 2026-11-25
- SNOTEL 689:WY:SNTL: latest 2026-11-25
- SNOTEL 784:CA:SNTL: latest 2026-11-25
- SNOTEL 791:WA:SNTL: latest 2026-11-25
- SNOTEL 909:WA:SNTL: latest 2026-11-25

## Forecast snapshots

- Snapshots stored: 479 over 8 day(s)
- Models: ecmwf_ifs, gfs_seamless

## Events

- Total events: 185 (2025-10-14 .. 2026-11-23)
- label_quality=direct: 43
- label_quality=weak_snotel_diff: 142
- region continental_mountain: 130
- region pnw_maritime_mountain: 33
- region sierra_maritime_mountain: 22

## Alignment and training

- Trainer rows aligned: 140
- Holdout MAE: not computable yet (insufficient aligned events).
- Note: Temporal holdout not computable (needs >=10 rows, a test water year with >=5 rows, and >=5 earlier-year training rows); candidate fit on all rows without holdout metrics.

## Production gates

- modifiersEnabled: False
- regionalBiasEnabled: False
- allowUnvalidatedRegionalArtifact: False
- Tracking artifact status: unvalidated
- Production calibration remains DISABLED. Tracking artifacts are unvalidated
  research candidates; enabling production requires validated holdout evidence
  and a separate, explicit production change.
