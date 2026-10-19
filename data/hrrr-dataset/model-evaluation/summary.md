# Region-first SLR predictor -- evaluation summary

Eligible events: 20922 (train WY[2024, 2025], holdout WY2026). All artifacts are research-only and unvalidated; production gates untouched.

| Region | Events (train/test) | Storms (train) | Kuchera MAE (split1) | A split1 / split2 | B split1 / split2 | Climatology split1 | Scratch ridge (diagnostic) split1 | Verdict |
|---|---|---|---|---|---|---|---|---|
| Colorado Ranges | 3634/1078 | 2841 | 11.4901 | 3.8595 / 4.1271 | 3.8339 / 4.1262 | 3.4473 | 3.3556 | demoted-direct-label-check |
| Utah (Wasatch-Uinta) | 2564/667 | 1931 | 9.7163 | 2.7997 / 3.4528 | 2.8104 / 3.4562 | 2.8768 | 2.5817 | retained-A |
| Wyoming Ranges | 2055/744 | 1565 | 9.4142 | 3.4542 / 3.8857 | 3.4675 / 3.8807 | 3.5886 | 3.4736 | retained-A |
| Washington Cascades | 1879/669 | 1332 | 6.4554 | 2.8419 / 2.705 | 2.7963 / 2.6362 | 2.7775 | 2.7343 | retained-B |
| Idaho Ranges | 1933/545 | 1332 | 7.2345 | 2.7659 / 3.1084 | 2.7442 / 3.0504 | 2.8457 | 2.6908 | none |
| Montana Ranges | 1428/617 | 1096 | 9.27 | 3.179 / 3.5817 | 3.1785 / 3.5536 | 3.2602 | 3.0613 | retained-B |
| Oregon Cascades | 1107/277 | 818 | 7.0537 | 2.776 / 2.6291 | 2.7691 / 2.5903 | 2.8743 | 2.8056 | retained-B |
| Sierra Nevada | 573/216 | 436 | 10.3935 | 2.8149 / 3.2961 | 2.8149 / 3.2961 | 2.3955 | 2.4007 | demoted-direct-label-check |
| Southwest (NM + AZ) | 455/128 | 384 | 10.389 | 3.1442 / 3.7132 | 3.1138 / 3.7132 | 3.2525 | 2.8346 | retained-B |
| Nevada Ranges | 253/68 | 193 | 10.8438 | 2.9157 / 3.2703 | 2.9157 / 3.2984 | 2.2101 | 2.1602 | retained-A |
| Cascades (WA + OR merged) | 2986/946 | 2150 | 6.6306 | 2.8221 / 2.6898 | 2.794 / 2.6377 | 2.8154 | 2.7608 | none-merged-not-selected |

## Cascades decision

**separate-cascades-retained** -- merged split-1 MAE 2.794 worse than event-weighted separate MAE 2.788335517970402

## Direct-label bias check

- rockies-ghcn: {'path': 'data/slr-training-data/normalized/baseline/ghcn_rockies_kuchera.csv', 'events': 124, 'candidateBApplicable': False, 'candidateBNote': 'stored direct rows lack portable forecast features (fcst_precip_mm, fcst_t2m_*); only Candidate A can be applied', 'routedByRegion': {'colorado-ranges': 120, 'wyoming-ranges': 4}}
- sierra-ghcn: {'path': 'data/sierra-slr-research/normalized/sierra_kuchera.csv', 'events': 225, 'candidateBApplicable': False, 'candidateBNote': 'stored direct rows lack portable forecast features (fcst_precip_mm, fcst_t2m_*); only Candidate A can be applied', 'routedByRegion': {'sierra-nevada': 225}}
- cssl: {'path': 'data/cssl/v13/research/cssl-aligned-baselines.csv', 'events': 35, 'candidateBApplicable': False, 'candidateBNote': 'stored direct rows lack portable forecast features (fcst_precip_mm, fcst_t2m_*); only Candidate A can be applied', 'routedByRegion': {'sierra-nevada': 35}}
- colorado-ranges: {'directEvents': 120, 'kucheraMae': 4.5132, 'artifactAMae': 4.9695, 'verdict': 'demote'}
- sierra-nevada: {'directEvents': 260, 'kucheraMae': 4.3502, 'artifactAMae': 5.556, 'verdict': 'demote'}
- wyoming-ranges: {'directEvents': 4, 'kucheraMae': 6.1765, 'artifactAMae': 5.1171, 'verdict': 'pass'}
- Demotions: ['colorado-ranges', 'sierra-nevada']

## Notes

- All artifacts carry status 'unvalidated'; production calibration gates in src/lib/snow/calibration/coefficients.ts were not touched and no artifact was bundled into src/.
- Candidate B artifacts must not be bundled until the TypeScript runtime applies the covariateModel block with semantics identical to regional_model.apply_candidate_b.
- Weak-label artifacts for regions without a direct-label check carry labelTier 'weak-only' and a lower promotion ceiling (design section 7).
- Context for the headline MAEs: differenced SNOTEL labels are noisy, so raw-Kuchera MAE on weak labels (6.5-13.2) is far above its direct-label MAE (4.4-4.5 where measured). Region x month climatology -- printed in the summary table -- captures much of the same variance reduction and beats both candidates in Colorado, Sierra Nevada, and Nevada split 1; the section 6.3 gate compares candidates against raw Kuchera only, so retention stands as designed, but climatology is the honest bar any promotion decision should also clear.
- Regime sensitivity: refitting retained regions with the climate level disabled changes split-1 MAE by at most 0.004 -- regions are regime-homogeneous by construction, so the climate level is nearly redundant inside a region (it still differentiates the WA/OR/CA interior stations).

## ECMWF transfer test (design section 8) -- addendum

Status: **blocked-profile-levels-unavailable** (full detail in
transfer_report.json). All 28 sampled stations (4 per retained region,
677 WY2026 holdout events) were fetched from the Open-Meteo
historical-forecast API with explicit models=ecmwf_ifs. Surface fields
are populated, but every pressure-level variable
(temperature_*hPa / geopotential_height_*hPa) is null with
hourly_units 'undefined', so the Python Kuchera baseline -- which needs
the temperature/geopotential profile -- cannot be computed for any
event (677 attempted, 0 baselines). Levels ARE fully populated in the
same API's best-match archive (verified 192/192 non-null on a probe),
but the contributing model is not exposed there, so best-match profiles
cannot honestly be labelled ECMWF IFS and were not substituted. The
transfer question (does the HRRR-trained correction carry to ECMWF
features?) remains open pending a profile source with attributable
ECMWF output; the per-station API responses are cached on disk under
data/hrrr-dataset/model-evaluation/ecmwf-cache/ (git-ignored).
