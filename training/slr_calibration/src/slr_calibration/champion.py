"""Packaged ENS-EB champion for step-6 shadow scoring.

The champion designated on 2026-10-28 (see ``CHAMPION.md`` beside the
era5-expanded-v2 outputs) is ENS-EB: the simple average of RF+EB and
GBM+EB predictions, each component being ``gold_model.RFEBModel`` /
``GBMEBModel`` fitted on the full era5-expanded-v2 modeling frame
(57,376 gold events, ERA5 profiles from the NSF NCAR AWS mirror).

The judged runs shipped only the *inner* sklearn models
(``rf_full.joblib`` / ``gbm_full.joblib``); the EB residual artifacts
lived inside the evaluation processes and were never persisted.  This
module closes that gap:

* ``package-champion`` refits both components with the judged
  construction (seed 0, full pool) purely to recover their EB
  artifacts, verifies the refit inner models reproduce the shipped
  joblibs, then writes a self-contained ``champion/`` directory:
  copied component joblibs + ``champion_sidecar.json`` (feature spec,
  both EB artifacts verbatim, the averaging rule, judged scores,
  transfer headline, fallback semantics) + ``ARTIFACT.md``.
* ``predict-champion`` (and :class:`ChampionModel`) loads ONLY that
  directory and scores new events through the identical code path the
  harness used (``gold_model._apply_eb`` over the persisted EB
  artifacts), so packaged predictions are the judged model's
  predictions, not a reimplementation.

Research artifact only.  Production calibration gates remain OFF; this
package exists for step-6 shadow-scoring evaluation and is not wired
into ``src/``.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PACKAGE_FORMAT = "mysnow-ens-eb-champion"
PACKAGE_VERSION = 1
SIDECAR_NAME = "champion_sidecar.json"
RF_FILE = "rf.joblib"
GBM_FILE = "gbm.joblib"
ARTIFACT_MD_NAME = "ARTIFACT.md"

# Columns a predict input may carry besides the 27 features + region.
OPTIONAL_INPUT_COLUMNS = ("site_id", "event_id", "liquid_mm", "fresh_swe_mm")
LIQUID_COLUMNS = ("liquid_mm", "fresh_swe_mm")


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def validate_event_frame(
    frame: pd.DataFrame, features: list[str], *, strict: bool = False
) -> None:
    """Raise ValueError with an explicit message when a predict input
    does not match the champion's contract.

    Required: every feature column (in the sidecar's order) plus
    ``region``.  In strict mode (file inputs to ``predict-champion``)
    any column outside features + region + the documented optional
    columns is also an error.  Feature values must be finite.
    """
    missing = [column for column in features if column not in frame.columns]
    if "region" not in frame.columns:
        missing.append("region")
    if missing:
        raise ValueError(
            "predict input is missing required column(s): " + ", ".join(missing)
        )
    if strict:
        allowed = set(features) | {"region", *OPTIONAL_INPUT_COLUMNS}
        extra = [column for column in frame.columns if column not in allowed]
        if extra:
            raise ValueError(
                "predict input has unexpected column(s): " + ", ".join(sorted(extra))
            )
    values = frame[list(features)].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        bad = int((~np.isfinite(values)).sum())
        raise ValueError(
            f"predict input has {bad} non-finite feature value(s); "
            "the champion does not impute missing profile features"
        )


# ---------------------------------------------------------------------------
# The packaged model
# ---------------------------------------------------------------------------


class ChampionModel:
    """Loadable ENS-EB champion: mean of the two EB-corrected parts.

    Geography for the EB hierarchy comes from the latitude/longitude
    features (H3 cells + the artifact's climate assignments), exactly
    as in the harness; the ``region`` column is carried for validation
    and reporting and does not drive the correction.  An event whose
    geography is absent from a component's EB hierarchy therefore
    falls back level by level -- climate, then H3 region cell, then
    site -- and an entirely unseen area receives the component's
    global-node correction only (the harness's ``apply_artifact``
    semantics); if even that node were missing the correction factor
    would be 1.0 (no correction).
    """

    def __init__(
        self,
        champion_dir: Path,
        sidecar: dict[str, Any],
        rf: Any,
        gbm: Any,
    ) -> None:
        self.champion_dir = Path(champion_dir)
        self.sidecar = sidecar
        self.features: list[str] = list(sidecar["features"])
        self.regions: list[str] = list(sidecar["regions"])
        self.rf = rf
        self.gbm = gbm
        # Bit-reproducibility: sklearn's parallel forest predict
        # accumulates tree votes in thread-scheduling order, so two
        # identical calls with the shipped n_jobs=-1 differ by up to
        # ~1e-14 (measured 1.07e-14 on the v2 frame).  Pinning the
        # packaged forest to a single predict job makes packaged
        # predictions bit-identical across calls and processes; it
        # changes values by at most ~1e-14.  The GBM predict is
        # already deterministic as shipped (measured 0.0).
        if hasattr(self.rf, "n_jobs"):
            self.rf.n_jobs = 1
        components = sidecar["components"]
        self.rf_eb_artifact: dict[str, Any] = components["rf_eb"]["ebArtifact"]
        self.gbm_eb_artifact: dict[str, Any] = components["gbm_eb"]["ebArtifact"]

    @classmethod
    def load(cls, champion_dir: Path) -> "ChampionModel":
        import joblib

        champion_dir = Path(champion_dir)
        sidecar_path = champion_dir / SIDECAR_NAME
        if not sidecar_path.exists():
            raise FileNotFoundError(f"no {SIDECAR_NAME} in {champion_dir}")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        if sidecar.get("packageFormat") != PACKAGE_FORMAT:
            raise ValueError(
                f"{sidecar_path} is not a {PACKAGE_FORMAT} package "
                f"(packageFormat={sidecar.get('packageFormat')!r})"
            )
        rf = joblib.load(champion_dir / sidecar["components"]["rf_eb"]["modelFile"])
        gbm = joblib.load(champion_dir / sidecar["components"]["gbm_eb"]["modelFile"])
        return cls(champion_dir, sidecar, rf, gbm)

    def _prepare(self, frame: pd.DataFrame) -> pd.DataFrame:
        validate_event_frame(frame, self.features)
        prepared = frame.reset_index(drop=True).copy()
        if "site_id" not in prepared.columns:
            prepared["site_id"] = ""
        return prepared

    def predict_parts(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """(RF+EB prediction, GBM+EB prediction) for an event frame,
        composed exactly as ``gold_model``'s ``predict_parts`` do."""
        from .gold_model import MAX_SLR, MIN_SLR, _apply_eb

        prepared = self._prepare(frame)
        X = prepared[self.features].to_numpy(dtype=float)

        rf_frame = prepared.copy()
        rf_frame["rf_pred"] = np.clip(self.rf.predict(X), MIN_SLR, MAX_SLR)
        rf_eb = _apply_eb(self.rf_eb_artifact, rf_frame, baseline_column="rf_pred")

        gbm_frame = prepared.copy()
        gbm_frame["gbm_pred"] = np.clip(self.gbm.predict(X), MIN_SLR, MAX_SLR)
        gbm_eb = _apply_eb(self.gbm_eb_artifact, gbm_frame, baseline_column="gbm_pred")
        return rf_eb, gbm_eb

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        """ENS-EB SLR per event: the simple mean of the two parts."""
        rf_eb, gbm_eb = self.predict_parts(frame)
        return (rf_eb + gbm_eb) / 2.0


# ---------------------------------------------------------------------------
# File-level prediction (the predict-champion entry point)
# ---------------------------------------------------------------------------


def read_event_input(path: Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("events")
        if not isinstance(payload, list):
            raise ValueError(
                "JSON predict input must be a list of event objects "
                "or an object with an 'events' list"
            )
        return pd.DataFrame(payload)
    raise ValueError(f"unsupported predict input format: {path.suffix} (use .csv or .json)")


def predict_file(champion_dir: Path, input_path: Path, output_path: Path) -> dict[str, Any]:
    """Score an event file with the packaged champion; write a CSV.

    Output columns: ``event_id`` (when supplied), ``region``,
    ``ens_eb_slr``, ``rf_eb_slr``, ``gbm_eb_slr`` and, when the input
    carries a liquid column (``liquid_mm`` or ``fresh_swe_mm``, mm of
    water), ``snowfall_mm`` = ENS-EB SLR x liquid.
    """
    model = ChampionModel.load(champion_dir)
    frame = read_event_input(input_path)
    validate_event_frame(frame, model.features, strict=True)
    rf_eb, gbm_eb = model.predict_parts(frame)
    ens = (rf_eb + gbm_eb) / 2.0

    out = pd.DataFrame({"region": frame["region"].astype(str)})
    if "event_id" in frame.columns:
        out.insert(0, "event_id", frame["event_id"].astype(str))
    out["ens_eb_slr"] = ens
    out["rf_eb_slr"] = rf_eb
    out["gbm_eb_slr"] = gbm_eb
    liquid_column = next((c for c in LIQUID_COLUMNS if c in frame.columns), None)
    if liquid_column is not None:
        out["snowfall_mm"] = ens * frame[liquid_column].to_numpy(dtype=float)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    return {
        "events": int(len(out)),
        "championDir": str(champion_dir),
        "input": str(input_path),
        "output": str(output_path),
        "snowfallColumn": liquid_column is not None,
    }


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------


def _nested(data: dict[str, Any], *keys: str) -> Any:
    node: Any = data
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _extract_judged_scores(source_dir: Path) -> dict[str, Any]:
    """ENS-EB judged scores from the v2 variant benchmark outputs."""
    path = source_dir / "results_variants.json"
    if not path.exists():
        return {}
    results = json.loads(path.read_text(encoding="utf-8"))
    splits = results.get("splits", {})
    aggregated = _nested(splits, "random60_40", "aggregated", "ens_eb") or {}
    loro_split = splits.get("leaveOneRegionOut", {})
    loro = {
        str(region): _nested(split, "models", "ens_eb", "overall", "mae")
        for region, split in loro_split.items()
    }
    loro = {region: mae for region, mae in loro.items() if mae is not None}
    rep0 = aggregated.get("rep0", {})
    return {
        "source": "results_variants.json (evaluate-gold-model-variants, identical splits/seeds)",
        "randomisedMaeMean": aggregated.get("maeMean"),
        "randomisedMaeStd": aggregated.get("maeStd"),
        "loroMaeByRegion": loro,
        "loroMaeMean": (
            round(sum(loro.values()) / len(loro), 4) if loro else None
        ),
        "temporalMae": _nested(splits, "temporalWY2026", "models", "ens_eb", "overall", "mae"),
        "unseenStationMae": _nested(splits, "unseenStations", "models", "ens_eb", "overall", "mae"),
        "rep0TailHighSlrGt20Mae": _nested(rep0, "tailHigh_slr_gt_20", "mae"),
        "rep0TailLowSlrLt5Mae": _nested(rep0, "tailLow_slr_lt_5", "mae"),
    }


def _extract_transfer_headline(source_dir: Path) -> dict[str, Any]:
    """Champion HRRR transfer headline + the round-trip fingerprint."""
    path = source_dir / "transfer-hrrr" / "transfer_results.json"
    if not path.exists():
        return {}
    results = json.loads(path.read_text(encoding="utf-8"))
    metrics = results.get("metrics", {})
    verdict = results.get("verdict", {})
    return {
        "source": "transfer-hrrr/transfer_results.json (evaluate-gold-transfer-hrrr-champion)",
        "coveredEvents": _nested(results, "accounting", "coveredRows"),
        "ensEbHrrrMae": _nested(metrics, "hrrr", "ens_eb", "overall", "mae"),
        "kucheraHrrrMae": _nested(metrics, "hrrr", "kuchera", "overall", "mae"),
        "regionsWonVsKuchera": verdict.get("regionsWonVsKuchera"),
        "regionsTotal": 6,
        "ensEbEra5InSampleMae": _nested(metrics, "era5", "ens_eb", "overall", "mae"),
        "ensEbEra5InSampleBias": _nested(metrics, "era5", "ens_eb", "overall", "bias"),
        "alaskaHrrrBias": _nested(metrics, "hrrr", "ens_eb", "perRegion", "alaska", "bias"),
        "tailLowHrrrMaeEnsEb": _nested(metrics, "hrrr", "ens_eb", "tailLow_slr_lt_5", "mae"),
        "tailLowHrrrMaeKuchera": _nested(metrics, "hrrr", "kuchera", "tailLow_slr_lt_5", "mae"),
    }


def build_champion_package(
    features_path: Path,
    source_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Fit the EB layers (seed 0, full pool) and write ``champion/``.

    The inner sklearn models are NOT refit for the package: the
    shipped ``rf_full.joblib`` / ``gbm_full.joblib`` are copied
    verbatim.  The refit performed here exists to recover the two EB
    artifacts with the harness's exact procedure (station-grouped OOF
    residuals -> hierarchical trainer); before anything is written,
    the refit inner models must reproduce the shipped joblibs'
    predictions on the full frame (max abs diff recorded in the
    sidecar), which is what ties the recovered EB layers to the
    shipped components.
    """
    import joblib

    from .gold_model import GBMEBModel, RFEBModel, _frame_profile_source, load_modeling_frame
    from .gold_profiles import FEATURE_COLUMNS

    features_path = Path(features_path)
    source_dir = Path(source_dir)
    output_dir = Path(output_dir)

    frame, accounting = load_modeling_frame(features_path)
    profile_source = _frame_profile_source(frame)
    X = frame[list(FEATURE_COLUMNS)].to_numpy()

    rf_eb = RFEBModel(seed=0).fit(frame)
    gbm_eb = GBMEBModel(seed=0).fit(frame)

    shipped_rf = joblib.load(source_dir / "rf_full.joblib")
    shipped_gbm = joblib.load(source_dir / "gbm_full.joblib")
    rf_diff = float(np.max(np.abs(rf_eb.rf.predict(X) - shipped_rf.predict(X))))
    gbm_diff = float(np.max(np.abs(gbm_eb.gbm.predict(X) - shipped_gbm.predict(X))))
    del shipped_rf, shipped_gbm
    if rf_diff > 1e-6 or gbm_diff > 1e-6:
        raise RuntimeError(
            "refit inner models do not reproduce the shipped full-pool joblibs "
            f"(rf max abs diff {rf_diff:.3g}, gbm {gbm_diff:.3g}); refusing to "
            "package EB layers recovered against different components"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_dir / "rf_full.joblib", output_dir / RF_FILE)
    shutil.copyfile(source_dir / "gbm_full.joblib", output_dir / GBM_FILE)

    judged = _extract_judged_scores(source_dir)
    transfer = _extract_transfer_headline(source_dir)
    regions = sorted(str(region) for region in frame["region"].unique())

    def component_block(model_file: str, description: str, eb_artifact: dict[str, Any]) -> dict[str, Any]:
        method = eb_artifact["methods"]["kuchera"]
        return {
            "modelFile": model_file,
            "model": description,
            "predictionClamp": [3.0, 30.0],
            "eb": (
                "Hierarchical empirical-Bayes log-residual layer (trainer.train_artifact) "
                "fitted on station-grouped out-of-fold component predictions: "
                "global -> climate regime -> H3 region cell (res 4) -> site nodes, each "
                "shrunk toward its parent with prior effective sample size 30; the summed "
                "log bias is clamped to a correction factor in [0.5, 2.0] and the "
                "calibrated SLR to [3, 30]. At predict time the walk stops at the first "
                "missing or under-supported level, so unseen geography degrades to the "
                "broader levels and an entirely unseen area receives the global-node "
                "correction only."
            ),
            "ebArtifact": eb_artifact,
            "ebNodes": {
                "climate": len(method["climate"]),
                "regionH3Res4": len(method["region"]),
                "localSite": len(method["local"]),
            },
        }

    sidecar: dict[str, Any] = {
        "packageFormat": PACKAGE_FORMAT,
        "packageVersion": PACKAGE_VERSION,
        "model": "ENS-EB",
        "definition": (
            "ENS-EB SLR = simple mean of the RF+EB and GBM+EB predictions "
            "(each component's clipped tree prediction passed through its own "
            "EB residual layer)."
        ),
        "features": list(FEATURE_COLUMNS),
        "regions": regions,
        "combination": {
            "rule": "simple_mean",
            "components": ["rf_eb", "gbm_eb"],
            "determinism": (
                "ChampionModel pins the random forest's predict to a single job "
                "(n_jobs=1): sklearn's parallel forest predict accumulates tree "
                "votes in thread-scheduling order (identical calls measured to "
                "differ by up to 1.07e-14), while single-job predict is "
                "bit-reproducible and differs from the parallel result by at "
                "most ~1e-14."
            ),
        },
        "unseenRegionFallback": (
            "The EB hierarchy is keyed on latitude/longitude (H3) and site_id, not on "
            "the named region. A level whose node is absent from a component's EB "
            "artifact (or below the minimum effective sample size of 5) ends the "
            "hierarchy walk, so an event in entirely unseen geography is corrected by "
            "that component's global node alone -- its global-mean log-residual "
            "correction -- and if no node applied at all the factor would be 1.0 "
            "(no correction). The tree prediction itself is unaffected."
        ),
        "components": {
            "rf_eb": component_block(
                RF_FILE,
                "RandomForestRegressor(n_estimators=500, random_state=0, sklearn defaults otherwise), "
                "copied verbatim from the judged run's rf_full.joblib",
                rf_eb.eb_artifact,
            ),
            "gbm_eb": component_block(
                GBM_FILE,
                "HistGradientBoostingRegressor(random_state=0, sklearn defaults), "
                "copied verbatim from the judged run's gbm_full.joblib",
                gbm_eb.eb_artifact,
            ),
        },
        "training": {
            "profileSource": profile_source,
            "featuresFile": features_path.name,
            "trainingRows": int(len(frame)),
            "waterYears": accounting["modelingWaterYears"],
            "rowsByRegion": accounting["modelingByRegion"],
            "ebFit": (
                "RFEBModel / GBMEBModel (gold_model) with seed 0 on the full modeling "
                "frame; EB artifacts recovered by this refit -- the judged runs did not "
                "persist them -- after verifying the refit inner models reproduce the "
                "shipped joblibs (see buildVerification)."
            ),
        },
        "judgedScores": judged,
        "transferCheck": transfer,
        "buildVerification": {
            "innerRfMaxAbsPredDiffVsRfFull": rf_diff,
            "innerGbmMaxAbsPredDiffVsGbmFull": gbm_diff,
            "roundTripFingerprint": (
                "ENS-EB@ERA5 MAE on the HRRR-transfer covered subset (in-sample "
                "reference) must reproduce transferCheck.ensEbEra5InSampleMae "
                "when the packaged champion scores those events' ERA5 features."
            ),
        },
        "note": (
            "Research artifact only. Production calibration gates remain OFF; this "
            "package exists for step-6 shadow-scoring evaluation and is not wired "
            "into src/."
        ),
    }
    # The EB artifacts must survive JSON exactly (they are the package's
    # correction tables); fail loudly rather than write a lossy sidecar.
    sidecar_text = json.dumps(sidecar, indent=2, allow_nan=False)
    (output_dir / SIDECAR_NAME).write_text(sidecar_text, encoding="utf-8")
    (output_dir / ARTIFACT_MD_NAME).write_text(_render_artifact_md(sidecar), encoding="utf-8")
    return sidecar


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _render_artifact_md(sidecar: dict[str, Any]) -> str:
    judged = sidecar.get("judgedScores") or {}
    transfer = sidecar.get("transferCheck") or {}
    training = sidecar["training"]
    loro = judged.get("loroMaeByRegion") or {}
    lines = [
        "# ENS-EB champion — packaged artifact",
        "",
        "Designated by the user on 2026-10-28 (see `../CHAMPION.md`). ENS-EB is the",
        "simple average of RF+EB and GBM+EB: a pooled random forest and a gradient-",
        "boosted tree model, each followed by its hierarchical empirical-Bayes",
        "log-residual correction layer, trained on the era5-expanded-v2 gold pool",
        f"({training['trainingRows']} events, {training['profileSource']} profiles,",
        "27 AGL features).",
        "",
        "## Package contents",
        "",
        f"- `{RF_FILE}` / `{GBM_FILE}` — the judged runs' full-pool component models,",
        "  copied verbatim from `rf_full.joblib` / `gbm_full.joblib`.",
        f"- `{SIDECAR_NAME}` — feature spec (27 features, in order), both components'",
        "  EB artifacts verbatim (the correction tables + shrinkage parameters), the",
        "  averaging rule, the unseen-geography fallback, provenance, judged scores.",
        f"- `{ARTIFACT_MD_NAME}` — this file.",
        "",
        "## How to call it",
        "",
        "```bash",
        "mysnow-slr-calibration predict-champion \\",
        "    --champion-dir data/slr-training-data/screened-v2/model-v1/era5-expanded-v2/champion \\",
        "    --input events.csv --output predictions.csv",
        "```",
        "",
        "Input (CSV or JSON): the 27 feature columns named in the sidecar, in any",
        "column order, plus `region`; optional `site_id` (enables the EB local-site",
        "node), `event_id` (passed through), and a liquid column (`liquid_mm` or",
        "`fresh_swe_mm`, mm of water) which adds `snowfall_mm = ens_eb_slr x liquid`",
        "to the output. Output columns: `event_id` (if supplied), `region`,",
        "`ens_eb_slr`, `rf_eb_slr`, `gbm_eb_slr`, `snowfall_mm` (if liquid supplied).",
        "From Python: `ChampionModel.load(champion_dir).predict(frame)` on a",
        "DataFrame with the same columns. Packaged predictions are",
        "bit-reproducible across calls and processes (the loader pins the random",
        "forest's predict to a single job; sklearn's parallel forest predict",
        "otherwise accumulates tree votes in thread order, ~1e-14 of noise).",
        "",
        "## Judged scores (held-out protocol, era5-expanded-v2)",
        "",
        f"- Randomised 60/40 (20 repeats): MAE {_fmt(judged.get('randomisedMaeMean'))}",
        f"- Leave-one-region-out mean: {_fmt(judged.get('loroMaeMean'))} "
        + "("
        + ", ".join(f"{region} {_fmt(mae)}" for region, mae in sorted(loro.items()))
        + ")",
        f"- Temporal (train < WY2026, test WY2026): {_fmt(judged.get('temporalMae'))}",
        f"- Unseen-station: {_fmt(judged.get('unseenStationMae'))}",
        f"- Tails (rep 0): SLR > 20 MAE {_fmt(judged.get('rep0TailHighSlrGt20Mae'))}; "
        f"SLR < 5 MAE {_fmt(judged.get('rep0TailLowSlrLt5Mae'))}",
        "",
        "## HRRR transfer check",
        "",
        f"On the {transfer.get('coveredEvents', 'n/a')}-event covered subset, ENS-EB fed",
        f"HRRR forecast profiles scores MAE {_fmt(transfer.get('ensEbHrrrMae'))} vs",
        f"Kuchera-on-HRRR {_fmt(transfer.get('kucheraHrrrMae'))}, winning",
        f"{transfer.get('regionsWonVsKuchera', 'n/a')} of {transfer.get('regionsTotal', 6)} regions.",
        "The model was trained on ERA5 reanalysis features; HRRR use is supported by",
        "this transfer check, not by training on HRRR.",
        "",
        "## Known limitations",
        "",
        f"- Alaska bias on HRRR features: {_fmt(transfer.get('alaskaHrrrBias'), 2)} SLR "
        "(under-prediction persists after transfer).",
        f"- On HRRR features the SLR < 5 slice still favours raw Kuchera "
        f"({_fmt(transfer.get('tailLowHrrrMaeKuchera'))}) over ENS-EB "
        f"({_fmt(transfer.get('tailLowHrrrMaeEnsEb'))}).",
        "- The high-SLR tail is the weakest slice (randomised rep-0 MAE "
        f"{_fmt(judged.get('rep0TailHighSlrGt20Mae'))} on SLR > 20 events).",
        "- EB corrections are bounded (factor in [0.5, 2.0], SLR in [3, 30]) and the",
        "  hierarchy degrades gracefully, but geography far outside the training",
        "  footprint receives only the global correction.",
        "- Predictions are point estimates of SLR; snowfall output additionally",
        "  assumes the supplied liquid amount is exact.",
        "",
        "## Status",
        "",
        "**Production calibration gates remain OFF. This artifact exists for step-6",
        "shadow-scoring evaluation only and is not wired into `src/`.**",
        "",
    ]
    return "\n".join(lines)
