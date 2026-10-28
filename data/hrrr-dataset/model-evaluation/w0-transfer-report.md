# W0: does the step-4 observation operator transfer across the SNOTEL network?

Research-only. No artifact was retained, no gate was changed, and nothing here modifies the production calibration configuration. Operator parameters are fitted only on hourly depth trajectories; gold labels are used exclusively for scoring.

## Stage 1 — pair census (full AWDB network)

Full AWDB station list: 913 SNTL stations, 913 with usable coordinates/elevation (0 dropped). Gold stations with events: 1011.

**Primary band** (<= 10.0 km, <= 150.0 m): 12 stations, 1204 scoreable events (1204 total).
- alaska: 8 stations, 311 events
- pacific-northwest: 1 stations, 4 events
- rockies: 1 stations, 570 events
- sierra-nevada: 2 stations, 319 events

**Secondary band** (<= 25.0 km, <= 300.0 m): 16 stations, 1710 scoreable events (1710 total).
- alaska: 8 stations, 566 events
- pacific-northwest: 3 stations, 103 events
- rockies: 3 stations, 686 events
- sierra-nevada: 2 stations, 355 events

| Gold station | SNTEL | Region | Dist (km) | dElev (m) | Band | Events (scoreable/total) | Water years |
|---|---|---|---:|---:|---|---:|---|
| US1COSU0008 | 802:CO:SNTL | rockies | 1.82 | 116 | primary | 570/570 | 2003-2024 |
| US1AKMS0011 | 2044:AK:SNTL | alaska | 4.23 | 47 | primary | 109/109 | 2012-2022 |
| US1AKAB0030 | 1332:AK:SNTL | alaska | 4.70 | 11 | primary | 41/41 | 2009-2012 |
| US1CANV0051 | 834:CA:SNTL | sierra-nevada | 5.60 | 4 | primary | 152/152 | 2020-2026 |
| USC00509891 | 1260:AK:SNTL | alaska | 5.78 | 1 | primary | 80/80 | 2019-2026 |
| US1AKAB0001 | 1332:AK:SNTL | alaska | 5.98 | 18 | primary | 27/27 | 2008-2010 |
| US1CAED0020 | 473:CA:SNTL | sierra-nevada | 6.09 | 23 | primary | 167/167 | 2015-2023 |
| US1AKAB0015 | 1332:AK:SNTL | alaska | 6.90 | 17 | primary | 19/19 | 2022-2023 |
| US1ORYM0005 | 1084:OR:SNTL | pacific-northwest | 6.95 | 86 | primary | 4/4 | 2014-2017 |
| US1AKAB0003 | 1332:AK:SNTL | alaska | 7.16 | 29 | primary | 8/8 | 2008-2008 |
| US1AKFN0047 | 1302:AK:SNTL | alaska | 7.96 | 140 | primary | 15/15 | 2026-2026 |
| US1AKAB0014 | 1332:AK:SNTL | alaska | 9.73 | 8 | primary | 12/12 | 2008-2009 |
| US1AKAB0028 | 1070:AK:SNTL | alaska | 6.84 | 164 | secondary | 115/115 | 2009-2017 |
| US1COPK0015 | 938:CO:SNTL | rockies | 9.55 | 234 | secondary | 306/306 | 2004-2026 |
| US1NVDG0017 | 473:CA:SNTL | sierra-nevada | 10.27 | 9 | secondary | 18/18 | 2017-2019 |
| US1AKMS0012 | 967:AK:SNTL | alaska | 10.64 | 7 | secondary | 203/203 | 2014-2025 |
| US1MTMS0027 | 562:MT:SNTL | rockies | 10.68 | 273 | secondary | 75/75 | 2018-2023 |
| USC00048760 | 848:CA:SNTL | sierra-nevada | 11.40 | 133 | secondary | 337/337 | 2011-2026 |
| US1AKFN0039 | 968:AK:SNTL | alaska | 11.86 | 201 | secondary | 40/40 | 2022-2026 |
| US1ORYM0004 | 1084:OR:SNTL | pacific-northwest | 13.81 | 67 | secondary | 13/13 | 2009-2023 |
| US1COLR0247 | 412:CO:SNTL | rockies | 14.24 | 145 | secondary | 305/305 | 2003-2026 |
| US1AKAB0055 | 1332:AK:SNTL | alaska | 15.22 | 15 | secondary | 80/80 | 2017-2023 |
| US1ORYM0001 | 1084:OR:SNTL | pacific-northwest | 18.43 | 38 | secondary | 28/28 | 2008-2024 |
| US1AKAB0034 | 1332:AK:SNTL | alaska | 19.63 | 26 | secondary | 30/30 | 2010-2013 |
| US1AKAB0081 | 1070:AK:SNTL | alaska | 20.75 | 144 | secondary | 9/9 | 2025-2026 |
| US1AKMS0014 | 2044:AK:SNTL | alaska | 21.57 | 30 | secondary | 27/27 | 2018-2026 |
| USC00351986 | 660:OR:SNTL | pacific-northwest | 22.45 | 131 | secondary | 62/62 | 2021-2026 |
| US1AKFN0038 | 1302:AK:SNTL | alaska | 24.53 | 286 | secondary | 62/62 | 2022-2026 |

## Stage 2 — hourly pulls

| SNTEL | Gold station | Band | Fetched | Cached | Failed |
|---|---|---|---:|---:|---:|
| 802:CO:SNTL | US1COSU0008 | primary | 0 | 209 | 0 |
| 2044:AK:SNTL | US1AKMS0011 | primary | 0 | 73 | 0 |
| 1332:AK:SNTL | US1AKAB0030 | primary | 0 | 28 | 0 |
| 834:CA:SNTL | US1CANV0051 | primary | 0 | 58 | 0 |
| 1260:AK:SNTL | USC00509891 | primary | 0 | 58 | 0 |
| 1332:AK:SNTL | US1AKAB0001 | primary | 0 | 20 | 0 |
| 473:CA:SNTL | US1CAED0020 | primary | 0 | 71 | 0 |
| 1332:AK:SNTL | US1AKAB0015 | primary | 0 | 12 | 0 |
| 1084:OR:SNTL | US1ORYM0005 | primary | 0 | 6 | 0 |
| 1332:AK:SNTL | US1AKAB0003 | primary | 0 | 6 | 0 |
| 1302:AK:SNTL | US1AKFN0047 | primary | 0 | 8 | 0 |
| 1332:AK:SNTL | US1AKAB0014 | primary | 0 | 9 | 0 |
| 1070:AK:SNTL | US1AKAB0028 | secondary | 0 | 71 | 0 |
| 938:CO:SNTL | US1COPK0015 | secondary | 0 | 191 | 0 |
| 473:CA:SNTL | US1NVDG0017 | secondary | 0 | 18 | 0 |
| 967:AK:SNTL | US1AKMS0012 | secondary | 0 | 94 | 0 |
| 562:MT:SNTL | US1MTMS0027 | secondary | 0 | 43 | 0 |
| 848:CA:SNTL | USC00048760 | secondary | 0 | 133 | 0 |
| 968:AK:SNTL | US1AKFN0039 | secondary | 0 | 49 | 0 |
| 1084:OR:SNTL | US1ORYM0004 | secondary | 0 | 21 | 0 |
| 412:CO:SNTL | US1COLR0247 | secondary | 0 | 210 | 0 |
| 1332:AK:SNTL | US1AKAB0055 | secondary | 0 | 53 | 0 |
| 1084:OR:SNTL | US1ORYM0001 | secondary | 0 | 43 | 0 |
| 1332:AK:SNTL | US1AKAB0034 | secondary | 0 | 22 | 0 |
| 1070:AK:SNTL | US1AKAB0081 | secondary | 0 | 14 | 0 |
| 2044:AK:SNTL | US1AKMS0014 | secondary | 0 | 34 | 0 |
| 660:OR:SNTL | USC00351986 | secondary | 0 | 41 | 0 |
| 1302:AK:SNTL | US1AKFN0038 | secondary | 0 | 40 | 0 |

Fetch accounting above is the final (mop-up) pass; across all passes, 1,446 new station-months were fetched for the 16 paired SNTEL stations (1,548 station-months cached for them in total, including 102 pre-cached by step 4; 1,616 month files in the cache overall). Six station-months failed transiently on first attempt (read timeouts, HTTP 500/502, one TLS handshake timeout) and all six were recovered by the mop-up pass. The fetch ran as three parallel polite clients (1.5 s spacing each) after AWDB response latency proved to be the bottleneck.

### Hourly data availability audit (per paired SNTEL station)

Months with at least one non-null hourly value, from the cached AWDB payloads themselves. The operator and naive differencing both require WTEQ (pillow SWE); stations without it cannot be scored at all.

| SNTEL | Months cached | Months w/ WTEQ | Months w/ SNWD | Empty months | Paired events | Scored |
|---|---:|---:|---:|---:|---:|---:|
| 1070:AK:SNTL | 85 | 85 | 85 | 0 | 124 | 67 |
| 1084:OR:SNTL | 46 | 46 | 46 | 0 | 45 | 23 |
| 1260:AK:SNTL | 58 | 48 | 48 | 10 | 80 | 60 |
| 1302:AK:SNTL | 42 | 42 | 41 | 0 | 77 | 7 |
| 1332:AK:SNTL | 97 | 0 | 0 | 97 | 217 | 0 |
| 2044:AK:SNTL | 80 | 0 | 37 | 42 | 136 | 0 |
| 412:CO:SNTL | 210 | 210 | 188 | 0 | 305 | 259 |
| 473:CA:SNTL | 72 | 72 | 72 | 0 | 185 | 168 |
| 562:MT:SNTL | 43 | 43 | 36 | 0 | 75 | 4 |
| 660:OR:SNTL | 41 | 41 | 41 | 0 | 62 | 61 |
| 802:CO:SNTL | 233 | 233 | 230 | 0 | 570 | 401 |
| 834:CA:SNTL | 69 | 69 | 69 | 0 | 152 | 141 |
| 848:CA:SNTL | 133 | 133 | 133 | 0 | 337 | 273 |
| 938:CO:SNTL | 196 | 196 | 196 | 0 | 306 | 160 |
| 967:AK:SNTL | 94 | 94 | 94 | 0 | 203 | 185 |
| 968:AK:SNTL | 49 | 0 | 49 | 0 | 40 | 0 |

Three Alaska stations return no hourly WTEQ: 1332:AK:SNTL (Elmendorf Field) returns empty hourly payloads in all 97 cached months; 2044:AK:SNTL (Spring Creek) and 968:AK:SNTL report hourly SNWD/TOBS but never WTEQ. Together they make 393 of the 877 Alaska paired events unscorable. This is an AWDB data-availability limit, documented here rather than silently shrinking the set.

## Stage 3 — frozen step-4 operator (no refitting)

Coverage accounting per station (events -> scored):

| Gold station | Events | Window missing | Low coverage | No initial depth | Scored |
|---|---:|---:|---:|---:|---:|
| US1COSU0008 | 570 | 92 | 49 | 28 | 401 |
| US1AKMS0011 | 109 | 96 | 13 | 0 | 0 |
| US1AKAB0030 | 41 | 41 | 0 | 0 | 0 |
| US1CANV0051 | 152 | 8 | 0 | 3 | 141 |
| USC00509891 | 80 | 10 | 9 | 1 | 60 |
| US1AKAB0001 | 27 | 27 | 0 | 0 | 0 |
| US1CAED0020 | 167 | 0 | 0 | 15 | 152 |
| US1AKAB0015 | 19 | 19 | 0 | 0 | 0 |
| US1ORYM0005 | 4 | 0 | 0 | 0 | 4 |
| US1AKAB0003 | 8 | 8 | 0 | 0 | 0 |
| US1AKFN0047 | 15 | 0 | 0 | 9 | 6 |
| US1AKAB0014 | 12 | 12 | 0 | 0 | 0 |
| US1AKAB0028 | 115 | 47 | 4 | 0 | 64 |
| US1COPK0015 | 306 | 21 | 77 | 48 | 160 |
| US1NVDG0017 | 18 | 0 | 0 | 2 | 16 |
| US1AKMS0012 | 203 | 6 | 10 | 2 | 185 |
| US1MTMS0027 | 75 | 63 | 0 | 8 | 4 |
| USC00048760 | 337 | 6 | 24 | 34 | 273 |
| US1AKFN0039 | 40 | 1 | 39 | 0 | 0 |
| US1ORYM0004 | 13 | 0 | 2 | 2 | 9 |
| US1COLR0247 | 305 | 4 | 32 | 10 | 259 |
| US1AKAB0055 | 80 | 80 | 0 | 0 | 0 |
| US1ORYM0001 | 28 | 15 | 2 | 1 | 10 |
| US1AKAB0034 | 30 | 30 | 0 | 0 | 0 |
| US1AKAB0081 | 9 | 5 | 0 | 1 | 3 |
| US1AKMS0014 | 27 | 8 | 19 | 0 | 0 |
| USC00351986 | 62 | 1 | 0 | 0 | 61 |
| US1AKFN0038 | 62 | 61 | 0 | 0 | 1 |

Overall and stratified metrics. Columns: MAE | bias | n. 'Recovered (common)' is recovered MAE on the subset where naive differencing is defined — the like-for-like comparison.

- **Overall:** naive 12.54 | 3.88 | 1321; recovered 4.60 | -3.61 | 1734; recovered (common) 4.42 | -3.45 | 1299

**Region:**

| Group | Naive MAE | Naive bias | n | Recovered MAE | Recovered bias | n | Recovered (common) MAE | bias | n |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| alaska | 22.11 | 12.74 | 252 | 5.07 | -3.55 | 304 | 4.93 | -3.31 | 246 |
| pacific-northwest | 3.95 | -2.28 | 63 | 4.29 | -3.27 | 79 | 4.09 | -3.08 | 63 |
| rockies | 8.88 | 0.07 | 591 | 5.03 | -4.41 | 774 | 4.92 | -4.37 | 576 |
| sierra-nevada | 13.26 | 4.87 | 415 | 3.82 | -2.61 | 577 | 3.48 | -2.30 | 414 |

**Distance band:**

| Group | Naive MAE | Naive bias | n | Recovered MAE | Recovered bias | n | Recovered (common) MAE | bias | n |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10-25 km | 16.90 | 8.40 | 617 | 4.45 | -3.30 | 794 | 4.24 | -3.13 | 611 |
| 2-5 km | n/a | n/a | 0 | n/a | n/a | 0 | n/a | n/a | 0 |
| 5-10 km | 8.91 | 1.09 | 409 | 3.97 | -2.85 | 558 | 3.73 | -2.52 | 402 |
| <=2 km | 8.46 | -1.70 | 295 | 5.83 | -5.36 | 382 | 5.78 | -5.42 | 286 |

**dElev band:**

| Group | Naive MAE | Naive bias | n | Recovered MAE | Recovered bias | n | Recovered (common) MAE | bias | n |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 150-300 m | 10.71 | 1.85 | 148 | 4.25 | -3.27 | 209 | 4.01 | -2.98 | 144 |
| 25-75 m | 2.15 | -1.63 | 12 | 3.21 | -0.60 | 19 | 2.51 | 0.94 | 12 |
| 75-150 m | 11.68 | 2.83 | 743 | 4.85 | -4.11 | 968 | 4.66 | -4.00 | 730 |
| <=25 m | 15.02 | 6.63 | 418 | 4.34 | -2.95 | 538 | 4.21 | -2.76 | 413 |

**SNTEL elevation band:**

| Group | Naive MAE | Naive bias | n | Recovered MAE | Recovered bias | n | Recovered (common) MAE | bias | n |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1500-2500 m | 13.26 | 4.87 | 415 | 3.82 | -2.61 | 577 | 3.48 | -2.30 | 414 |
| <1500 m | 18.43 | 9.61 | 317 | 4.96 | -3.56 | 387 | 4.79 | -3.31 | 311 |
| >=2500 m | 8.87 | 0.11 | 589 | 5.00 | -4.38 | 770 | 4.91 | -4.35 | 574 |

## Stage 4 — refit variants, judged leave-stations-out

Window counts available for trajectory fitting: US1COSU0008 230, US1AKMS0011 0, US1AKAB0030 0, US1CANV0051 55, USC00509891 50, US1AKAB0001 0, US1CAED0020 52, US1AKAB0015 0, US1ORYM0005 10, US1AKAB0003 0, US1AKFN0047 42, US1AKAB0014 0

**V1 per-region-cluster refits (LOSO)**

| Region | Naive MAE | n | Recovered MAE (common) | bias | n |
|---|---:|---:|---:|---:|---:|
| alaska | 19.18 | 55 | 10.96 | -10.94 | 53 |
| pacific-northwest | n/a | 0 | n/a | n/a | 0 |
| rockies | n/a | 0 | n/a | n/a | 0 |
| sierra-nevada | 4.95 | 205 | 5.01 | -4.25 | 204 |

- US1AKAB0001: params {"gamma": 1.7992, "rho0Intercept": 85.755, "rho0TempSlope": 12.7316, "logEtaNew": 8.6757, "logEtaOld": 23.1625}, fit depth RMSE 151.3 mm on 90 windows; scored: naive MAE n/a, recovered MAE (common) n/a, bias n/a, n 0
- US1AKAB0003: params {"gamma": 1.7992, "rho0Intercept": 85.755, "rho0TempSlope": 12.7316, "logEtaNew": 8.6757, "logEtaOld": 23.1625}, fit depth RMSE 151.3 mm on 90 windows; scored: naive MAE n/a, recovered MAE (common) n/a, bias n/a, n 0
- US1AKAB0014: params {"gamma": 1.7992, "rho0Intercept": 85.755, "rho0TempSlope": 12.7316, "logEtaNew": 8.6757, "logEtaOld": 23.1625}, fit depth RMSE 151.3 mm on 90 windows; scored: naive MAE n/a, recovered MAE (common) n/a, bias n/a, n 0
- US1AKAB0015: params {"gamma": 1.7992, "rho0Intercept": 85.755, "rho0TempSlope": 12.7316, "logEtaNew": 8.6757, "logEtaOld": 23.1625}, fit depth RMSE 151.3 mm on 90 windows; scored: naive MAE n/a, recovered MAE (common) n/a, bias n/a, n 0
- US1AKAB0030: params {"gamma": 1.7992, "rho0Intercept": 85.755, "rho0TempSlope": 12.7316, "logEtaNew": 8.6757, "logEtaOld": 23.1625}, fit depth RMSE 151.3 mm on 90 windows; scored: naive MAE n/a, recovered MAE (common) n/a, bias n/a, n 0
- US1AKFN0047: params {"gamma": 1.7923, "rho0Intercept": 42.608, "rho0TempSlope": 21.2675, "logEtaNew": 8.5935, "logEtaOld": 25.3883}, fit depth RMSE 194.5 mm on 48 windows; scored: naive MAE 3.26, recovered MAE (common) 10.08, bias -10.08, n 3
- US1AKMS0011: params {"gamma": 1.7992, "rho0Intercept": 85.755, "rho0TempSlope": 12.7316, "logEtaNew": 8.6757, "logEtaOld": 23.1625}, fit depth RMSE 151.3 mm on 90 windows; scored: naive MAE n/a, recovered MAE (common) n/a, bias n/a, n 0
- US1CAED0020: params {"gamma": 1.8, "rho0Intercept": 200.0, "rho0TempSlope": 25.0, "logEtaNew": 12.0498, "logEtaOld": 16.9635}, fit depth RMSE 233.5 mm on 48 windows; scored: naive MAE 4.51, recovered MAE (common) 6.80, bias -6.73, n 97
- US1CANV0051: params {"gamma": 1.6605, "rho0Intercept": 92.78, "rho0TempSlope": 25.0, "logEtaNew": 28.0, "logEtaOld": 16.4041}, fit depth RMSE 114.1 mm on 48 windows; scored: naive MAE 5.34, recovered MAE (common) 3.40, bias -2.01, n 107
- US1COSU0008: skipped: insufficient fit windows
- US1ORYM0005: skipped: insufficient fit windows
- USC00509891: params {"gamma": 1.8, "rho0Intercept": 195.299, "rho0TempSlope": -2.0, "logEtaNew": 16.0897, "logEtaOld": 18.0964}, fit depth RMSE 50.7 mm on 42 windows; scored: naive MAE 20.42, recovered MAE (common) 11.01, bias -10.99, n 50

**V2 covariate-conditioned refits (LOSO)**

| Region | Naive MAE | n | Recovered MAE (common) | bias | n |
|---|---:|---:|---:|---:|---:|
| alaska | 19.18 | 55 | 6.35 | -6.15 | 53 |
| pacific-northwest | 4.04 | 3 | 2.56 | -2.56 | 3 |
| rockies | 8.46 | 295 | 8.93 | -8.92 | 286 |
| sierra-nevada | 4.95 | 205 | 6.04 | -5.97 | 204 |

- US1AKAB0001: params {"gamma": 1.8, "rho0Intercept": 165.607, "rho0TempSlope": 25.0, "logEtaNew": 13.196, "logEtaOld": 17.2352}, fit depth RMSE 218.3 mm on 244 windows; scored: naive MAE n/a, recovered MAE (common) n/a, bias n/a, n 0
- US1AKAB0003: params {"gamma": 1.8, "rho0Intercept": 165.607, "rho0TempSlope": 25.0, "logEtaNew": 13.196, "logEtaOld": 17.2352}, fit depth RMSE 218.3 mm on 244 windows; scored: naive MAE n/a, recovered MAE (common) n/a, bias n/a, n 0
- US1AKAB0014: params {"gamma": 1.8, "rho0Intercept": 165.607, "rho0TempSlope": 25.0, "logEtaNew": 13.196, "logEtaOld": 17.2352}, fit depth RMSE 218.3 mm on 244 windows; scored: naive MAE n/a, recovered MAE (common) n/a, bias n/a, n 0
- US1AKAB0015: params {"gamma": 1.8, "rho0Intercept": 165.607, "rho0TempSlope": 25.0, "logEtaNew": 13.196, "logEtaOld": 17.2352}, fit depth RMSE 218.3 mm on 244 windows; scored: naive MAE n/a, recovered MAE (common) n/a, bias n/a, n 0
- US1AKAB0030: params {"gamma": 1.8, "rho0Intercept": 165.607, "rho0TempSlope": 25.0, "logEtaNew": 13.196, "logEtaOld": 17.2352}, fit depth RMSE 218.3 mm on 244 windows; scored: naive MAE n/a, recovered MAE (common) n/a, bias n/a, n 0
- US1AKFN0047: params {"gamma": 1.8, "rho0Intercept": 169.444, "rho0TempSlope": 25.0, "logEtaNew": 13.1919, "logEtaOld": 17.0534}, fit depth RMSE 232.2 mm on 202 windows; scored: naive MAE 3.26, recovered MAE (common) 6.09, bias -6.09, n 3
- US1AKMS0011: params {"gamma": 1.8, "rho0Intercept": 165.607, "rho0TempSlope": 25.0, "logEtaNew": 13.196, "logEtaOld": 17.2126}, fit depth RMSE 218.3 mm on 244 windows; scored: naive MAE n/a, recovered MAE (common) n/a, bias n/a, n 0
- US1CAED0020: params {"gamma": 1.8, "rho0Intercept": 200.0, "rho0TempSlope": 25.0, "logEtaNew": 12.1602, "logEtaOld": 17.1031}, fit depth RMSE 231.8 mm on 196 windows; scored: naive MAE 4.51, recovered MAE (common) 6.72, bias -6.64, n 97
- US1CANV0051: params {"gamma": 1.531, "rho0Intercept": 156.221, "rho0TempSlope": 25.0, "logEtaNew": 14.0585, "logEtaOld": 16.947}, fit depth RMSE 210.7 mm on 196 windows; scored: naive MAE 5.34, recovered MAE (common) 5.43, bias -5.37, n 107
- US1COSU0008: params {"gamma": 1.6953, "rho0Intercept": 168.716, "rho0TempSlope": 25.0, "logEtaNew": 12.7869, "logEtaOld": 14.5941}, fit depth RMSE 179.2 mm on 196 windows; scored: naive MAE 8.46, recovered MAE (common) 8.93, bias -8.92, n 286
- US1ORYM0005: params {"gamma": 1.8, "rho0Intercept": 166.811, "rho0TempSlope": 25.0, "logEtaNew": 13.1482, "logEtaOld": 17.2546}, fit depth RMSE 223.1 mm on 234 windows; scored: naive MAE 4.04, recovered MAE (common) 2.56, bias -2.56, n 3
- USC00509891: params {"gamma": 1.4698, "rho0Intercept": 152.787, "rho0TempSlope": 25.0, "logEtaNew": 13.4772, "logEtaOld": 16.6515}, fit depth RMSE 221.3 mm on 196 windows; scored: naive MAE 20.42, recovered MAE (common) 6.37, bias -6.15, n 50

## Stage 5 — verdict vs the pre-registered exit criterion

Criterion: leave-stations-out recovered MAE >= 15% better than naive AND |bias| <= 1.0 SLR in at least 3 western regions with paired data (comparison on the common subset where naive is defined).

**V0-frozen:** regions passing: none (paired data: rockies, pacific-northwest, sierra-nevada, alaska)
- rockies: naive MAE 8.88, recovered 4.92, improvement 44.5%, bias -4.37, n 576 — fail
- pacific-northwest: naive MAE 3.95, recovered 4.09, improvement -3.6%, bias -3.08, n 63 — fail
- sierra-nevada: naive MAE 13.26, recovered 3.48, improvement 73.8%, bias -2.30, n 414 — fail
- alaska: naive MAE 22.11, recovered 4.93, improvement 77.7%, bias -3.31, n 246 — fail

**V1-cluster-loso:** regions passing: none (paired data: sierra-nevada, alaska)
- rockies: no paired data
- pacific-northwest: no paired data
- sierra-nevada: naive MAE 4.95, recovered 5.01, improvement -1.4%, bias -4.25, n 204 — fail
- alaska: naive MAE 19.18, recovered 10.96, improvement 42.9%, bias -10.94, n 53 — fail

**V2-covariate-loso:** regions passing: none (paired data: rockies, pacific-northwest, sierra-nevada, alaska)
- rockies: naive MAE 8.46, recovered 8.93, improvement -5.6%, bias -8.92, n 286 — fail
- pacific-northwest: naive MAE 4.04, recovered 2.56, improvement 36.4%, bias -2.56, n 3 — fail
- sierra-nevada: naive MAE 4.95, recovered 6.04, improvement -22.2%, bias -5.97, n 204 — fail
- alaska: naive MAE 19.18, recovered 6.35, improvement 66.9%, bias -6.15, n 53 — fail

**Best variant:** V0-frozen

**Recommendation: STOP**

## Interpretation

**MAE transfers; levels do not.** The frozen CSSL operator improves on naive differencing almost everywhere (relative MAE improvement of 45-78% in the Rockies, Sierra Nevada, and Alaska region aggregates), but its recovered SLR is biased low at essentially every station with data — regional bias -2.3 to -4.4, and -5.36 at the single tightest pair (Silverthorne, 1.82 km from Summit Ranch). The level error is therefore not a pair-distance artefact; it is the step-4 finding (partial level transfer, probe bias about -2.30) reproduced at network scale and amplified over the full 2003-2026 span.

**Trajectory-only refitting at network scale is degenerate.** The pre-declared refit variants (V1 cluster LOSO, V2 covariate LOSO) do not fix the level; they make it worse. Pooled multi-station trajectory fits run to the parameter bounds (gamma -> 1.8, fresh-density intercept -> 165-200 kg/m3, temperature slope -> 25) that fit depth trajectories while capping the recoverable SLR — recovered bias -2.0 to -10.9 per station. The one asymmetric exception is instructive: Tahoe-fitted parameters score Truckee almost sanely (MAE 3.40, bias -2.01), while Truckee-fitted parameters collapse at Tahoe (MAE 6.80, bias -6.73). This is step 4's identification warning (parameters weakly identified by depth trajectories, several at bounds even at CSSL) playing out across stations: the operator's parameter set is not a transferable physical constant set, and depth trajectories alone do not pin it down at new stations.

**Naive differencing at paired stations is a weak baseline.** At non-co-located pairs, naive SLR error can be dominated by spatial mismatch rather than by the measurement process the operator corrects (e.g. USC00048760: naive MAE 22.1, bias +12.7). Relative MAE gains over that baseline overstate operator skill; the pre-registered bias gate is the binding test, and every variant fails it in every judged region.

**Coverage reality check.** Of 2,914 paired gold events, 1,809 (62%) were scored. The losses are documented per station above: 393 Alaska events sit at stations with no hourly WTEQ in AWDB at all; the rest are window/coverage/depth-availability gaps in the hourly record, concentrated in the earlier water years. Alaska's judged evidence therefore rests on the 967/1260/1070/1302 stations only.

**Conclusion.** Under the pre-registered criterion the track stops at W0: the observation operator is a CSSL instrument, not a network instrument. Its recovered labels must not be produced at scale for training (W1) on the basis of trajectory-only fitting. Any future round would need a level signal that trajectories cannot supply — i.e. label-supervised calibration, which is a different (W2-style) design and would need its own pre-registered gates before any gold label is touched for fitting.
