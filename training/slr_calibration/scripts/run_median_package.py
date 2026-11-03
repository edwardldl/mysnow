#!/usr/bin/env python3
"""Packaging for median experiment M (queue item 3), run ONLY when the
pre-registered adoption rule passes (see model-v3-median/DESIGN.md
section 7).  Mirrors the v3 champion package layout so the package
loads through champion.ChampionModel; the GBM component's EB artifact
is the median hierarchy.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "training/slr_calibration/src"))

from slr_calibration import gold_model  # noqa: E402
from slr_calibration.champion import ChampionModel  # noqa: E402
from slr_calibration.prediction_cache import REGIONS  # noqa: E402

BASE = REPO / "data/slr-training-data/screened-v2/model-v1"
FEATURES = BASE / "model-v3-final/features_v3.parquet"
COLUMNS_JSON = BASE / "model-v3/screens_v2/final_combination.json"
OUT = BASE / "model-v3-median"
CANDIDATE_DIR = OUT / "candidate"


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def _eb_nodes(artifact: dict[str, Any]) -> dict[str, int]:
    method = artifact["methods"]["kuchera"]
    return {
        "climate": len(method["climate"]),
        "regionH3Res4": len(method["region"]),
        "localSite": len(method["local"]),
    }


def stage_package(results: dict[str, Any]) -> dict[str, Any]:
    import joblib

    from slr_calibration.median_model import AbsoluteErrorGBMModel

    columns = json.loads(COLUMNS_JSON.read_text(encoding="utf-8"))["columns"]
    frame, _acc = gold_model.load_modeling_frame(FEATURES)
    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)

    log("package: fitting seed-0 full-pool components")
    rf_model = gold_model.RFEBModel(0, columns=columns).fit(frame)
    med_model = AbsoluteErrorGBMModel(0, columns=columns).fit(frame)
    joblib.dump(rf_model.rf, CANDIDATE_DIR / "rf.joblib")
    joblib.dump(med_model.gbm, CANDIDATE_DIR / "gbm.joblib")

    direct_rf = rf_model.predict(frame)
    direct_gbm = med_model.predict(frame)
    direct_ens = (direct_rf + direct_gbm) / 2.0

    if "water_year" in frame.columns:
        water_years = sorted(int(y) for y in frame["water_year"].unique())
    else:
        times = pd.to_datetime(frame["valid_time"])
        water_years = sorted({int(y + (1 if m >= 10 else 0)) for y, m in zip(times.dt.year, times.dt.month)})

    inc = results["incumbent"]
    cand = results["candidate_m"]
    sidecar: dict[str, Any] = {
        "packageFormat": "mysnow-ens-eb-champion",
        "packageVersion": 1,
        "model": "ENS-EB-M (median experiment M candidate)",
        "definition": (
            "Candidate M SLR = simple mean of RF+EB (incumbent component, "
            "unchanged) and a median GBM component: "
            "HistGradientBoostingRegressor(loss='absolute_error') whose "
            "hierarchical EB log-residual layer uses weighted-median node "
            "statistics (median_eb.train_artifact_median)."
        ),
        "features": columns,
        "regions": list(REGIONS),
        "combination": {
            "rule": "simple_mean",
            "components": ["rf_eb", "gbm_eb"],
            "determinism": (
                "ChampionModel pins the random forest's predict to a single "
                "job (n_jobs=1) for bit-reproducibility; see the v3 champion "
                "sidecar for the measured rationale."
            ),
        },
        "unseenRegionFallback": (
            "The EB hierarchy is keyed on latitude/longitude (H3) and "
            "site_id, not on the named region. A level whose node is absent "
            "from a component's EB artifact (or below the minimum effective "
            "sample size of 5) ends the hierarchy walk, so an event in "
            "entirely unseen geography is corrected by that component's "
            "global node alone, and if no node applied at all the factor "
            "would be 1.0 (no correction). The tree prediction itself is "
            "unaffected."
        ),
        "components": {
            "rf_eb": {
                "modelFile": "rf.joblib",
                "model": "RandomForestRegressor(n_estimators=500, random_state=0, sklearn defaults otherwise), fitted on the full v3 modeling pool",
                "predictionClamp": [3.0, 30.0],
                "eb": "Hierarchical empirical-Bayes log-residual layer (trainer.train_artifact) fitted on station-grouped out-of-fold component predictions",
                "ebArtifact": rf_model.eb_artifact,
                "ebNodes": _eb_nodes(rf_model.eb_artifact),
            },
            "gbm_eb": {
                "modelFile": "gbm.joblib",
                "model": "HistGradientBoostingRegressor(loss='absolute_error', random_state=0, sklearn defaults otherwise), fitted on the full v3 modeling pool",
                "predictionClamp": [3.0, 30.0],
                "eb": "Hierarchical empirical-Bayes log-residual layer with weighted-median node statistics (median_eb.train_artifact_median) fitted on station-grouped out-of-fold component predictions",
                "ebArtifact": med_model.eb_median_artifact,
                "ebNodes": _eb_nodes(med_model.eb_median_artifact),
            },
        },
        "training": {
            "profileSource": "era5-nsf-ncar-aws",
            "featuresFile": "features_v3.parquet",
            "trainingRows": int(len(frame)),
            "waterYears": water_years,
            "rowsByRegion": {str(k): int(v) for k, v in frame.groupby("region").size().items()},
            "ebFit": "RFEBModel (gold_model) and AbsoluteErrorGBMModel (median_model) with seed 0 on the full v3 modeling frame with the final 64 columns; EB artifacts fitted by this packaging run as part of the component fits.",
        },
        "judgedScores": {
            "protocol": "queue item 3 median experiment M: frozen v3 manifests, queue-1 cache, paired event-by-event vs the ENS-EB incumbent",
            "resultsFile": "model-v3-median/results.json",
            "headline": {
                "incumbentFourFamilyMae": inc["four_family_mean"],
                "candidateFourFamilyMae": cand["four_family_mean"],
                "fourFamilyGain": results["four_family_gain"],
                "familyMaeIncumbent": inc["family"],
                "familyMaeCandidate": cand["family"],
                "loroByRegionIncumbent": inc["loro_regions"],
                "loroByRegionCandidate": cand["loro_regions"],
            },
            "adoptionRule": "model-v3-median/DESIGN.md section 6 (four-family gain >= 0.03; pooled LORO >= 0.02 and no region gain < -0.05; temporal/stations gains >= 0; randomised >= -0.02; slice guard 0.20; masked-tclw pooled diff <= +0.03)",
            "verdict": "adopt",
        },
        "transferCheck": None,
        "transferNote": "No HRRR/ECMWF transfer check has been run for candidate M; the v3 champion's transfer status does not carry over.",
        "candidate": {
            "name": "median-M",
            "status": "CANDIDATE — NOT SERVED",
            "design": "data/slr-training-data/screened-v2/model-v1/model-v3-median/DESIGN.md",
            "experimentDir": "data/slr-training-data/screened-v2/model-v1/model-v3-median",
        },
        "buildVerification": {},
        "note": "Candidate package from queue item 3. NOT wired into serving; production calibration gates remain OFF.",
    }

    # --- Round-trip verification (mirrors the v3 champion's). ---
    values = frame[columns].to_numpy(dtype=float)
    finite = np.isfinite(values).all(axis=1)
    finite_frame = frame.loc[finite]
    # The sidecar must exist before ChampionModel.load; write it now and
    # rewrite it below with the verification block filled in.
    (CANDIDATE_DIR / "champion_sidecar.json").write_text(
        json.dumps(sidecar, indent=2), encoding="utf-8"
    )
    packaged_model = ChampionModel.load(CANDIDATE_DIR)
    packaged_finite = packaged_model.predict(finite_frame)
    loader_diff = float(np.max(np.abs(packaged_finite - direct_ens[finite])))
    repeat = packaged_model.predict(finite_frame)
    repeat_diff = float(np.max(np.abs(repeat - packaged_finite)))

    # Contents check on ALL rows (joblibs + sidecar EB artifacts through
    # gold_model._apply_eb, bypassing the loader's finite-input guard).
    rf_loaded = joblib.load(CANDIDATE_DIR / "rf.joblib")
    gbm_loaded = joblib.load(CANDIDATE_DIR / "gbm.joblib")
    rf_frame = frame.copy()
    rf_frame["rf_pred"] = np.clip(rf_loaded.predict(values), 3.0, 30.0)
    contents_rf = gold_model._apply_eb(rf_model.eb_artifact, rf_frame, baseline_column="rf_pred")
    gbm_frame = frame.copy()
    gbm_frame["gbm_pred"] = np.clip(gbm_loaded.predict(values), 3.0, 30.0)
    contents_gbm = gold_model._apply_eb(med_model.eb_median_artifact, gbm_frame, baseline_column="gbm_pred")
    contents_ens = (contents_rf + contents_gbm) / 2.0
    contents_diff = float(np.max(np.abs(contents_ens - direct_ens)))

    observed = frame["observed_slr"].to_numpy(dtype=float)
    verification = {
        "packagedLoaderFiniteRows": int(finite.sum()),
        "nonFiniteFeatureRows": int((~finite).sum()),
        "packagedVsDirectMaxAbsPredDiffFiniteRows": loader_diff,
        "packageContentsVsDirectMaxAbsPredDiffAllRows": contents_diff,
        "packagedRepeatMaxAbsPredDiff": repeat_diff,
        "inSampleMaeDirect": float(np.mean(np.abs(direct_ens - observed))),
        "inSampleMaePackaged": float(np.mean(np.abs(packaged_finite - observed[finite]))),
        "rows": int(len(frame)),
        "roundTripFingerprint": (
            "Two-part check mirroring the v3 champion: (1) ChampionModel "
            "loaded from candidate/ alone reproduces the packaging fits' "
            "ensemble predictions on the modelling rows whose 64 features "
            "are all finite; (2) the package contents reproduce the fits' "
            "predictions on all rows. The loader refuses non-finite inputs "
            "by design."
        ),
    }
    sidecar["buildVerification"] = verification
    (CANDIDATE_DIR / "champion_sidecar.json").write_text(
        json.dumps(sidecar, indent=2), encoding="utf-8"
    )
    (OUT / "package_verification.json").write_text(
        json.dumps(verification, indent=2), encoding="utf-8"
    )

    artifact_md = f"""# ENS-EB-M candidate — packaged artifact (median experiment M)

Adopted as a CANDIDATE under the pre-registered adoption rule in
`model-v3-median/DESIGN.md` section 6 (see `model-v3-median/REPORT.md`
for the clause-by-clause verdict). **CANDIDATE — NOT SERVED.** Nothing
is wired into serving; production calibration gates remain OFF.

Candidate M is the ENS-EB incumbent with only its GBM component
changed: the tree is refit with `loss="absolute_error"` (conditional
median, matching the MAE judging metric) and its hierarchical EB
log-residual layer uses weighted-median node statistics in place of
weighted means. The RF+EB component is the incumbent's, unchanged.

## Package contents

- `rf.joblib` / `gbm.joblib` — seed-0 full-pool component fits of the
  judged construction (57,376 events, 64 features).
- `champion_sidecar.json` — feature spec, both components' EB
  artifacts verbatim (the GBM component's is the median hierarchy),
  the averaging rule, fallback semantics, judged scores, build
  verification.
- `ARTIFACT.md` — this file. The package loads through the existing
  `champion.ChampionModel` (same package format).

## Judged scores (frozen v3 folds, paired vs incumbent)

- Randomised: incumbent {inc['family']['randomised']:.4f} → M {cand['family']['randomised']:.4f}
- LORO mean: incumbent {inc['family']['loro']:.4f} → M {cand['family']['loro']:.4f}
- Temporal: incumbent {inc['family']['temporal']:.4f} → M {cand['family']['temporal']:.4f}
- Unseen-station: incumbent {inc['family']['stations']:.4f} → M {cand['family']['stations']:.4f}
- Four-family gain: {results['four_family_gain']:+.4f}

## Build verification

- Loader round-trip on {verification['packagedLoaderFiniteRows']} finite rows: max abs diff {verification['packagedVsDirectMaxAbsPredDiffFiniteRows']:.3e}
- Contents round-trip on all {verification['rows']} rows: max abs diff {verification['packageContentsVsDirectMaxAbsPredDiffAllRows']:.3e}
- Repeat predict: max abs diff {verification['packagedRepeatMaxAbsPredDiff']:.3e}
- In-sample MAE (direct fits): {verification['inSampleMaeDirect']:.4f}
"""
    (CANDIDATE_DIR / "ARTIFACT.md").write_text(artifact_md, encoding="utf-8")

    results["package"] = {
        "dir": str(CANDIDATE_DIR.relative_to(REPO)),
        "verification": verification,
    }
    (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    with open(OUT / "REPORT.md", "a", encoding="utf-8") as handle:
        handle.write(
            "\n## Candidate package\n\n"
            f"Built at `model-v3-median/candidate/` (CANDIDATE — NOT SERVED). "
            f"Round-trip: loader max abs diff "
            f"{verification['packagedVsDirectMaxAbsPredDiffFiniteRows']:.3e} on "
            f"{verification['packagedLoaderFiniteRows']} finite rows; contents "
            f"max abs diff {verification['packageContentsVsDirectMaxAbsPredDiffAllRows']:.3e} "
            f"on all rows; repeat {verification['packagedRepeatMaxAbsPredDiff']:.3e}.\n"
        )
    log(f"package: built at {CANDIDATE_DIR}, loader diff {loader_diff:.3e}")
    return results
