"""Step 2 (gold-only model): benchmark of SLR models on screened direct labels.

Models compared on identical splits, all trained ONLY on the step-1
screened direct (gold) labels with profile features from
``gold_profiles`` (source: open-meteo-historical-forecast-best-match
-- NOT ERA5; see that module's docstring for the provenance decision):

(a) ``rf``            scikit-learn RandomForestRegressor, Pletcher et
                      al. (2026) feature spec (27 features, direct SLR
                      regression).  n_estimators=500; all other
                      hyperparameters are sklearn defaults because the
                      paper does not fully specify them.
(b) ``gbm``           sklearn HistGradientBoostingRegressor, defaults.
                      No published SLR head-to-head exists; this is it.
(c) ``intercept``     per-region direct-intercept calibration of the
                      Kuchera baseline (the salvage winner of the
                      weak-label investigation): baseline x exp(mean
                      log residual) per region, factor clamped to
                      [0.5, 2.0], SLR clamped to [3, 30].
(d) ``eb_gold``       Candidate A: the existing EB hierarchical
                      trainer (trainer.train_artifact) fitted per
                      region on the gold events; applied with
                      evaluation.apply_artifact.
(e) ``kuchera``       raw Kuchera baseline from the same profiles.
(f) ``climatology``   region x month mean observed SLR (fallbacks:
                      region mean, then global mean).
(g) ``rf_eb``         RF + regional EB residual layer: the EB trainer
                      fitted on log(observed / RF) with the RF's
                      station-grouped out-of-fold predictions as the
                      baseline for training rows.

Splits: (1) randomized 60/40, 20 repeats; (2) leave-one-region-out;
(3) water-year temporal holdout (train < WY2026, test = WY2026);
(4) unseen-station holdout (seeded 30% of stations).

This is a research benchmark.  Nothing here writes app artifacts;
model pickles land beside the report under the data tree and are
research files only.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .evaluation import apply_artifact
from .gold_profiles import ERA5_PROFILE_SOURCE, FEATURE_COLUMNS, PROFILE_SOURCE
from .trainer import TrainingConfig, train_artifact

MODELS = ("rf", "gbm", "intercept", "eb_gold", "kuchera", "climatology", "rf_eb")
MIN_SLR, MAX_SLR = 3.0, 30.0
MIN_FACTOR, MAX_FACTOR = 0.5, 2.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _frame_profile_source(frame: pd.DataFrame) -> str:
    """Provenance of a features frame: its own uniform profile_source.

    Falls back to the module default (best-match) for frames that lack
    the column, so pre-ERA5 feature files keep their original labels.
    """
    if "profile_source" in frame.columns:
        values = {str(value) for value in frame["profile_source"].dropna().unique()}
        if len(values) == 1:
            return values.pop()
    return PROFILE_SOURCE


# ---------------------------------------------------------------------------
# Frame loading
# ---------------------------------------------------------------------------

def load_modeling_frame(features_path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = pd.read_parquet(features_path)
    total = len(frame)
    modeling = frame[(frame["profile_status"] == "ok") & frame["baseline_slr"].notna()].copy()
    modeling = modeling.reset_index(drop=True)
    accounting = {
        "eventsTotal": int(total),
        "modelingRows": int(len(modeling)),
        "excludedByStatus": {
            str(k): int(v) for k, v in frame["profile_status"].value_counts().items()
        },
        "modelingByRegion": {str(k): int(v) for k, v in modeling["region"].value_counts().items()},
        "modelingWaterYears": sorted(int(y) for y in modeling["water_year"].unique()),
    }
    return modeling, accounting


# ---------------------------------------------------------------------------
# Simple models
# ---------------------------------------------------------------------------

class InterceptModel:
    """Per-region multiplicative intercept on the Kuchera baseline."""

    def fit(self, train: pd.DataFrame) -> "InterceptModel":
        residuals = np.log(train["observed_slr"] / train["baseline_slr"])
        self.global_log = float(residuals.mean())
        self.by_region = {
            str(region): float(group.mean())
            for region, group in residuals.groupby(train["region"])
        }
        return self

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        logs = test["region"].map(self.by_region).fillna(self.global_log).to_numpy()
        factors = np.exp(np.clip(logs, math.log(MIN_FACTOR), math.log(MAX_FACTOR)))
        return np.clip(test["baseline_slr"].to_numpy() * factors, MIN_SLR, MAX_SLR)


class ClimatologyModel:
    """Region x month mean observed SLR with region/global fallbacks."""

    def fit(self, train: pd.DataFrame) -> "ClimatologyModel":
        self.global_mean = float(train["observed_slr"].mean())
        self.by_region = {str(k): float(v) for k, v in train.groupby("region")["observed_slr"].mean().items()}
        self.by_region_month = {
            (str(r), int(m)): float(v)
            for (r, m), v in train.groupby(["region", "month"])["observed_slr"].mean().items()
        }
        self.by_month = {int(k): float(v) for k, v in train.groupby("month")["observed_slr"].mean().items()}
        return self

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        out = []
        for region, month in zip(test["region"], test["month"]):
            value = self.by_region_month.get((str(region), int(month)))
            if value is None:
                value = self.by_region.get(str(region))
            if value is None:
                value = self.by_month.get(int(month), self.global_mean)
            out.append(value)
        return np.array(out)


# ---------------------------------------------------------------------------
# EB (Candidate A) helpers on top of the existing trainer
# ---------------------------------------------------------------------------

def _trainer_rows(frame: pd.DataFrame, baseline_column: str = "baseline_slr") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in frame.itertuples(index=False):
        rows.append({
            "event_id": record.event_id,
            "station_id": record.station_id,
            "site_id": record.site_id,
            "valid_time": str(record.valid_time),
            "latitude": float(record.latitude),
            "longitude": float(record.longitude),
            "observed_slr": float(record.observed_slr),
            "baseline_slr": float(getattr(record, baseline_column)),
            "baseline_method": "kuchera",
            "climate_regime": record.climate_regime,
            "qc_passed": True,
            "qc_weight": 1.0,
        })
    return rows


def _apply_eb(artifact: dict[str, Any], frame: pd.DataFrame, baseline_column: str = "baseline_slr") -> np.ndarray:
    predictions = []
    for record in frame.itertuples(index=False):
        row = {
            "baseline_method": "kuchera",
            "latitude": float(record.latitude),
            "longitude": float(record.longitude),
            "site_id": record.site_id,
            "baseline_slr": float(getattr(record, baseline_column)),
        }
        predictions.append(apply_artifact(artifact, row).calibrated_slr)
    return np.array(predictions)


class EBPerRegionModel:
    """Candidate A fitted per region; unseen regions use a global fit."""

    def fit(self, train: pd.DataFrame) -> "EBPerRegionModel":
        self.global_artifact = train_artifact(
            _trainer_rows(train),
            TrainingConfig(artifact_id="gold-eb-global", source_label="screened-v2 gold events (step 2 benchmark)"),
        )
        self.by_region: dict[str, dict[str, Any]] = {}
        for region, group in train.groupby("region"):
            if len(group) >= 30:
                self.by_region[str(region)] = train_artifact(
                    _trainer_rows(group),
                    TrainingConfig(artifact_id=f"gold-eb-{region}", source_label="screened-v2 gold events (step 2 benchmark)"),
                )
        return self

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        test = test.reset_index(drop=True)
        out = np.empty(len(test))
        for region, group in test.groupby("region"):
            artifact = self.by_region.get(str(region), self.global_artifact)
            out[group.index.to_numpy()] = _apply_eb(artifact, group)
        return out


def _station_grouped_folds(train: pd.DataFrame, seed: int) -> np.ndarray:
    """Station-grouped 5-fold assignment for out-of-fold predictions.

    Stations are shuffled with the split seed and dealt round-robin, so
    every station's rows share one fold and no station leaks between the
    OOF fit and its own residual.
    """
    stations = np.array(sorted(train["station_id"].unique()))
    rng = np.random.default_rng(seed)
    rng.shuffle(stations)
    fold_of = {station: i % 5 for i, station in enumerate(stations)}
    return train["station_id"].map(fold_of).to_numpy()


class RFEBModel:
    """RF plus an EB residual layer fitted on out-of-fold RF predictions."""

    def __init__(self, seed: int, columns: Any | None = None) -> None:
        self.seed = seed
        self.columns = _feature_columns(columns)

    def fit(
        self, train: pd.DataFrame, sample_weight: np.ndarray | None = None
    ) -> "RFEBModel":
        from sklearn.ensemble import RandomForestRegressor

        X = train[self.columns].to_numpy()
        y = train["observed_slr"].to_numpy()
        # Station-grouped 5-fold out-of-fold predictions for honest
        # residual fitting (no in-sample RF residuals).  The optional
        # sample weights apply to the RF fits only (OOF and full); the
        # EB residual layer keeps the harness convention of unweighted
        # rows (the trainer consumes qc_weight, fixed at 1.0 here).
        folds = _station_grouped_folds(train, self.seed)
        oof = np.full(len(train), np.nan)
        for fold in range(5):
            mask = folds == fold
            if mask.sum() == 0 or (~mask).sum() == 0:
                continue
            model = RandomForestRegressor(n_estimators=500, random_state=self.seed, n_jobs=-1)
            model.fit(
                X[~mask],
                y[~mask],
                sample_weight=None if sample_weight is None else sample_weight[~mask],
            )
            oof[mask] = model.predict(X[mask])
        if np.isnan(oof).any():
            oof[np.isnan(oof)] = train["baseline_slr"].to_numpy()[np.isnan(oof)]
        self.rf = RandomForestRegressor(n_estimators=500, random_state=self.seed, n_jobs=-1)
        self.rf.fit(X, y, sample_weight=sample_weight)
        residual_frame = train.copy()
        residual_frame["rf_oof"] = np.clip(oof, MIN_SLR, MAX_SLR)
        self.eb_artifact = train_artifact(
            _trainer_rows(residual_frame, baseline_column="rf_oof"),
            TrainingConfig(artifact_id="gold-rf-eb-residual", source_label="screened-v2 gold events (step 2 benchmark)"),
        )
        return self

    def predict_parts(self, test: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """(clipped RF prediction, EB-calibrated prediction) for a frame."""
        X = test[self.columns].to_numpy()
        rf_pred = np.clip(self.rf.predict(X), MIN_SLR, MAX_SLR)
        frame = test.copy()
        frame["rf_pred"] = rf_pred
        return rf_pred, _apply_eb(self.eb_artifact, frame, baseline_column="rf_pred")

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        return self.predict_parts(test)[1]


class GBMEBModel:
    """GBM plus the same EB residual layer treatment as RFEBModel."""

    def __init__(
        self,
        seed: int,
        columns: Any | None = None,
        monotonic_cst: Any | None = None,
    ) -> None:
        self.seed = seed
        self.columns = _feature_columns(columns)
        # Queue item 7 (monotone-constrained refit): optional per-column
        # monotonicity constraints, aligned to self.columns, passed to
        # every HistGradientBoostingRegressor construction (the five
        # station-grouped OOF fits and the full fit).  Default None ->
        # the incumbent estimator, bit-identical behaviour.
        if monotonic_cst is not None:
            monotonic_cst = [int(v) for v in monotonic_cst]
            if len(monotonic_cst) != len(self.columns):
                raise ValueError(
                    "monotonic_cst must align to columns "
                    f"({len(monotonic_cst)} != {len(self.columns)})"
                )
            if any(v not in (-1, 0, 1) for v in monotonic_cst):
                raise ValueError("monotonic_cst entries must be in {-1, 0, 1}")
        self.monotonic_cst = monotonic_cst

    def fit(
        self, train: pd.DataFrame, sample_weight: np.ndarray | None = None
    ) -> "GBMEBModel":
        from sklearn.ensemble import HistGradientBoostingRegressor

        X = train[self.columns].to_numpy()
        y = train["observed_slr"].to_numpy()
        folds = _station_grouped_folds(train, self.seed)
        oof = np.full(len(train), np.nan)
        for fold in range(5):
            mask = folds == fold
            if mask.sum() == 0 or (~mask).sum() == 0:
                continue
            model = HistGradientBoostingRegressor(
                random_state=self.seed, monotonic_cst=self.monotonic_cst
            )
            model.fit(
                X[~mask],
                y[~mask],
                sample_weight=None if sample_weight is None else sample_weight[~mask],
            )
            oof[mask] = model.predict(X[mask])
        if np.isnan(oof).any():
            oof[np.isnan(oof)] = train["baseline_slr"].to_numpy()[np.isnan(oof)]
        self.gbm = HistGradientBoostingRegressor(
            random_state=self.seed, monotonic_cst=self.monotonic_cst
        )
        self.gbm.fit(X, y, sample_weight=sample_weight)
        residual_frame = train.copy()
        residual_frame["gbm_oof"] = np.clip(oof, MIN_SLR, MAX_SLR)
        self.eb_artifact = train_artifact(
            _trainer_rows(residual_frame, baseline_column="gbm_oof"),
            TrainingConfig(artifact_id="gold-gbm-eb-residual", source_label="screened-v2 gold events (step 2 benchmark)"),
        )
        return self

    def predict_parts(self, test: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """(clipped GBM prediction, EB-calibrated prediction) for a frame."""
        X = test[self.columns].to_numpy()
        gbm_pred = np.clip(self.gbm.predict(X), MIN_SLR, MAX_SLR)
        frame = test.copy()
        frame["gbm_pred"] = gbm_pred
        return gbm_pred, _apply_eb(self.eb_artifact, frame, baseline_column="gbm_pred")

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        return self.predict_parts(test)[1]


# ---------------------------------------------------------------------------
# Median-objective GBM and its median-residual EB layer (bundle item 3)
# ---------------------------------------------------------------------------

MEDIAN_EB_PRIOR_ESS = 30.0  # the EB trainer's prior_effective_sample_size


def _fit_gbm_quantile(train: pd.DataFrame, seed: int):
    from sklearn.ensemble import HistGradientBoostingRegressor

    model = HistGradientBoostingRegressor(loss="quantile", quantile=0.5, random_state=seed)
    model.fit(train[list(FEATURE_COLUMNS)].to_numpy(), train["observed_slr"].to_numpy())
    return model


def _median_eb_corrections(
    residuals: np.ndarray,
    regions: np.ndarray,
    prior_ess: float = MEDIAN_EB_PRIOR_ESS,
) -> tuple[float, dict[str, float]]:
    """Per-region median log-residual corrections, EB shrinkage form.

    Mirrors the trainer's _fit_node shrinkage (effective sample size /
    (ESS + prior ESS), prior 30) but with medians in place of weighted
    means: the global correction is the shrunk overall median, and each
    region's correction is the global value plus its shrunk median
    deviation from it.  Returns (global_log_bias, {region: log_bias}).
    """
    residuals = np.asarray(residuals, dtype=float)
    regions = np.asarray(regions)
    n = len(residuals)
    global_log = float(np.median(residuals)) * (n / (n + prior_ess)) if n else 0.0
    by_region: dict[str, float] = {}
    for region in sorted(set(regions.tolist())):
        mask = regions == region
        count = int(mask.sum())
        deviation = float(np.median(residuals[mask] - global_log))
        by_region[str(region)] = global_log + deviation * (count / (count + prior_ess))
    return global_log, by_region


class MedianEBGBMModel:
    """Quantile (median) GBM plus a per-region median-residual EB layer.

    The residual layer is fitted on station-grouped out-of-fold median
    predictions, exactly as RFEBModel fits its mean-residual EB layer,
    but the corrections are per-region medians of the log residuals
    with the trainer's shrinkage form (see _median_eb_corrections).
    The correction factor is clamped to [0.5, 2.0] and the calibrated
    SLR to [3, 30], matching the EB artifact bounds.
    """

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def fit(self, train: pd.DataFrame) -> "MedianEBGBMModel":
        X = train[list(FEATURE_COLUMNS)].to_numpy()
        y = train["observed_slr"].to_numpy()
        folds = _station_grouped_folds(train, self.seed)
        oof = np.full(len(train), np.nan)
        for fold in range(5):
            mask = folds == fold
            if mask.sum() == 0 or (~mask).sum() == 0:
                continue
            model = _fit_gbm_quantile(train[~mask], self.seed)
            oof[mask] = model.predict(X[mask])
        if np.isnan(oof).any():
            oof[np.isnan(oof)] = train["baseline_slr"].to_numpy()[np.isnan(oof)]
        residuals = np.log(y / np.clip(oof, MIN_SLR, MAX_SLR))
        self.global_log, self.by_region = _median_eb_corrections(
            residuals, train["region"].to_numpy()
        )
        self.model = _fit_gbm_quantile(train, self.seed)
        return self

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        X = test[list(FEATURE_COLUMNS)].to_numpy()
        base = np.clip(self.model.predict(X), MIN_SLR, MAX_SLR)
        logs = test["region"].map(self.by_region).fillna(self.global_log).to_numpy()
        factors = np.exp(np.clip(logs, math.log(MIN_FACTOR), math.log(MAX_FACTOR)))
        return np.clip(base * factors, MIN_SLR, MAX_SLR)


def tail_shrink_predictions(rf_pred: np.ndarray, eb_pred: np.ndarray) -> np.ndarray:
    """RF+EB with the EB correction faded out at high predictions.

    The effective multiplicative EB correction (eb / rf) is shrunk
    toward 1 by weight clip(15 / prediction, 0, 1) when the RF
    prediction exceeds 15 (full correction at or below 15), per the
    pre-declared bundle item-4 form.
    """
    rf = np.asarray(rf_pred, dtype=float)
    eb = np.asarray(eb_pred, dtype=float)
    factor = eb / rf
    weight = np.where(rf > 15.0, np.clip(15.0 / rf, 0.0, 1.0), 1.0)
    return np.clip(rf * (1.0 + (factor - 1.0) * weight), MIN_SLR, MAX_SLR)


# ---------------------------------------------------------------------------
# Tree models
# ---------------------------------------------------------------------------

def _feature_columns(columns: Any | None) -> list[str]:
    """Resolve an optional feature-column override (default: AGL spec)."""
    return list(FEATURE_COLUMNS) if columns is None else list(columns)


def _fit_rf(train: pd.DataFrame, seed: int, columns: Any | None = None):
    from sklearn.ensemble import RandomForestRegressor

    model = RandomForestRegressor(n_estimators=500, random_state=seed, n_jobs=-1)
    model.fit(train[_feature_columns(columns)].to_numpy(), train["observed_slr"].to_numpy())
    return model


def _fit_gbm(train: pd.DataFrame, seed: int, columns: Any | None = None):
    from sklearn.ensemble import HistGradientBoostingRegressor

    model = HistGradientBoostingRegressor(random_state=seed)
    model.fit(train[_feature_columns(columns)].to_numpy(), train["observed_slr"].to_numpy())
    return model


def fit_predict(
    model_name: str,
    train: pd.DataFrame,
    test: pd.DataFrame,
    seed: int,
    columns: Any | None = None,
) -> np.ndarray:
    cols = _feature_columns(columns)
    if model_name == "rf":
        return _fit_rf(train, seed, cols).predict(test[cols].to_numpy())
    if model_name == "gbm":
        return _fit_gbm(train, seed, cols).predict(test[cols].to_numpy())
    if model_name == "intercept":
        return InterceptModel().fit(train).predict(test)
    if model_name == "eb_gold":
        return EBPerRegionModel().fit(train).predict(test)
    if model_name == "kuchera":
        return test["baseline_slr"].to_numpy()
    if model_name == "climatology":
        return ClimatologyModel().fit(train).predict(test)
    if model_name == "rf_eb":
        return RFEBModel(seed, columns=cols).fit(train).predict(test)
    if model_name == "gbm_eb":
        return GBMEBModel(seed, columns=cols).fit(train).predict(test)
    raise ValueError(f"unknown model {model_name}")


# ---------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------

def split_random(frame: pd.DataFrame, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(frame))
    cut = int(round(0.6 * len(frame)))
    return frame.iloc[order[:cut]].copy(), frame.iloc[order[cut:]].copy()


def split_loro(frame: pd.DataFrame, region: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    return frame[frame["region"] != region].copy(), frame[frame["region"] == region].copy()


def split_temporal(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    train = frame[frame["water_year"] < 2026].copy()
    test = frame[frame["water_year"] == 2026].copy()
    return train, test


def split_stations(frame: pd.DataFrame, seed: int, fraction: float = 0.30) -> tuple[pd.DataFrame, pd.DataFrame]:
    stations = np.array(sorted(frame["station_id"].unique()))
    rng = np.random.default_rng(seed)
    rng.shuffle(stations)
    held = set(stations[: int(round(fraction * len(stations)))].tolist())
    return frame[~frame["station_id"].isin(held)].copy(), frame[frame["station_id"].isin(held)].copy()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _metrics(observed: np.ndarray, predicted: np.ndarray) -> dict[str, Any]:
    errors = predicted - observed
    return {
        "n": int(len(observed)),
        "mae": round(float(np.mean(np.abs(errors))), 4) if len(observed) else None,
        "bias": round(float(np.mean(errors)), 4) if len(observed) else None,
        "rmse": round(float(np.sqrt(np.mean(errors ** 2))), 4) if len(observed) else None,
    }


def evaluate_predictions(frame: pd.DataFrame, predicted: np.ndarray) -> dict[str, Any]:
    observed = frame["observed_slr"].to_numpy()
    result: dict[str, Any] = {"overall": _metrics(observed, predicted)}
    per_region: dict[str, Any] = {}
    for region, group in frame.groupby("region"):
        mask = (frame["region"] == region).to_numpy()
        per_region[str(region)] = _metrics(observed[mask], predicted[mask])
    result["perRegion"] = per_region
    for label, mask in (("tailHigh_slr_gt_20", observed > 20), ("tailLow_slr_lt_5", observed < 5)):
        result[label] = _metrics(observed[mask], predicted[mask]) if mask.any() else {"n": 0, "mae": None, "bias": None, "rmse": None}
    snow_error = predicted * frame["fresh_swe_mm"].to_numpy() - frame["fresh_snow_mm"].to_numpy()
    result["snowfall"] = {
        "n": int(len(frame)),
        "maeMm": round(float(np.mean(np.abs(snow_error))), 4),
        "biasMm": round(float(np.mean(snow_error)), 4),
    }
    return result


# ---------------------------------------------------------------------------
# Benchmark driver
# ---------------------------------------------------------------------------

def _run_split(
    name: str,
    train: pd.DataFrame,
    test: pd.DataFrame,
    seed: int,
    models: tuple[str, ...] = MODELS,
    columns: Any | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"trainN": int(len(train)), "testN": int(len(test)), "models": {}}
    for model_name in models:
        predicted = fit_predict(model_name, train, test, seed, columns=columns)
        out["models"][model_name] = evaluate_predictions(test, predicted)
    return out


def run_benchmark(features_path: Path, output_dir: Path, random_repeats: int = 20) -> dict[str, Any]:
    frame, accounting = load_modeling_frame(features_path)
    profile_source = _frame_profile_source(frame)
    results: dict[str, Any] = {
        "profileSource": profile_source,
        "accounting": accounting,
        "splits": {},
    }

    # Split 1: randomized 60/40 repeats (headline, Pletcher-comparable).
    repeats: list[dict[str, Any]] = []
    for rep in range(random_repeats):
        train, test = split_random(frame, seed=1000 + rep)
        repeats.append(_run_split(f"random60_40_rep{rep}", train, test, seed=1000 + rep))
    aggregated: dict[str, Any] = {}
    for model_name in MODELS:
        maes = [r["models"][model_name]["overall"]["mae"] for r in repeats]
        aggregated[model_name] = {
            "maeMean": round(float(np.mean(maes)), 4),
            "maeStd": round(float(np.std(maes)), 4),
            "maeMin": round(float(np.min(maes)), 4),
            "maeMax": round(float(np.max(maes)), 4),
            "rep0": repeats[0]["models"][model_name],
        }
    results["splits"]["random60_40"] = {
        "repeats": random_repeats,
        "trainN": repeats[0]["trainN"],
        "testN": repeats[0]["testN"],
        "aggregated": aggregated,
    }

    # Permutation importance on repeat 0's split.
    from sklearn.inspection import permutation_importance

    train0, test0 = split_random(frame, seed=1000)
    rf0 = _fit_rf(train0, seed=1000)
    importance = permutation_importance(
        rf0, test0[list(FEATURE_COLUMNS)].to_numpy(), test0["observed_slr"].to_numpy(),
        n_repeats=5, random_state=0, n_jobs=-1,
    )
    order = np.argsort(importance.importances_mean)[::-1]
    results["permutationImportance"] = [
        {"feature": FEATURE_COLUMNS[i], "importanceMean": round(float(importance.importances_mean[i]), 4),
         "importanceStd": round(float(importance.importances_std[i]), 4)}
        for i in order
    ]

    # Split 2: leave-one-region-out.
    loro: dict[str, Any] = {}
    for region in sorted(frame["region"].unique()):
        train, test = split_loro(frame, str(region))
        loro[str(region)] = _run_split(f"loro_{region}", train, test, seed=2000)
    results["splits"]["leaveOneRegionOut"] = loro

    # Split 3: temporal holdout (WY2026).
    train, test = split_temporal(frame)
    results["splits"]["temporalWY2026"] = _run_split("temporal", train, test, seed=3000)

    # Split 4: unseen stations.
    train, test = split_stations(frame, seed=4000)
    results["splits"]["unseenStations"] = _run_split("stations", train, test, seed=4000)

    # Final research artifacts: RF + GBM on all modeling rows.
    import joblib

    output_dir.mkdir(parents=True, exist_ok=True)
    final_rf = _fit_rf(frame, seed=0)
    final_gbm = _fit_gbm(frame, seed=0)
    joblib.dump(final_rf, output_dir / "rf_full.joblib")
    joblib.dump(final_gbm, output_dir / "gbm_full.joblib")
    (output_dir / "model_sidecar.json").write_text(json.dumps({
        "profileSource": profile_source,
        "features": list(FEATURE_COLUMNS),
        "trainingRows": int(len(frame)),
        "models": {
            "rf": "RandomForestRegressor(n_estimators=500, sklearn defaults otherwise)",
            "gbm": "HistGradientBoostingRegressor(sklearn defaults)",
        },
        "note": "Research artifacts only. Not app artifacts; do not bundle into src/.",
    }, indent=2), encoding="utf-8")

    (output_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


# ---------------------------------------------------------------------------
# Cheap-wins bundle variants (items 3-4): ensembles, median objective,
# tail-shrunk EB.  Evaluated on the identical splits and seeds as
# run_benchmark, with component models fitted once per split and the
# ensemble / tail predictions derived from the shared components.
# ---------------------------------------------------------------------------

VARIANT_MODELS = ("gbm_eb", "ens_rg", "ens_eb", "med_gbm", "med_gbm_eb", "tail_rf_eb")


def _run_variant_split(
    train: pd.DataFrame,
    test: pd.DataFrame,
    seed: int,
) -> dict[str, Any]:
    X_test = test[list(FEATURE_COLUMNS)].to_numpy()
    rf_eb = RFEBModel(seed).fit(train)
    rf_pred, rf_eb_pred = rf_eb.predict_parts(test)
    gbm_pred = _fit_gbm(train, seed).predict(X_test)
    gbm_eb_pred = GBMEBModel(seed).fit(train).predict(test)
    med_pred = _fit_gbm_quantile(train, seed).predict(X_test)
    med_eb_pred = MedianEBGBMModel(seed).fit(train).predict(test)
    predictions = {
        "gbm_eb": gbm_eb_pred,
        "ens_rg": (rf_eb_pred + gbm_pred) / 2.0,
        "ens_eb": (rf_eb_pred + gbm_eb_pred) / 2.0,
        "med_gbm": med_pred,
        "med_gbm_eb": med_eb_pred,
        "tail_rf_eb": tail_shrink_predictions(rf_pred, rf_eb_pred),
    }
    out: dict[str, Any] = {"trainN": int(len(train)), "testN": int(len(test)), "models": {}}
    for model_name in VARIANT_MODELS:
        out["models"][model_name] = evaluate_predictions(test, predictions[model_name])
    return out


def run_variant_benchmark(
    features_path: Path,
    output_dir: Path,
    random_repeats: int = 20,
) -> dict[str, Any]:
    """Bundle items 3-4 benchmark; same splits/seeds as run_benchmark."""
    frame, accounting = load_modeling_frame(features_path)
    profile_source = _frame_profile_source(frame)
    results: dict[str, Any] = {
        "profileSource": profile_source,
        "accounting": accounting,
        "variantModels": list(VARIANT_MODELS),
        "splits": {},
    }

    repeats: list[dict[str, Any]] = []
    for rep in range(random_repeats):
        train, test = split_random(frame, seed=1000 + rep)
        repeats.append(_run_variant_split(train, test, seed=1000 + rep))
    aggregated: dict[str, Any] = {}
    for model_name in VARIANT_MODELS:
        maes = [r["models"][model_name]["overall"]["mae"] for r in repeats]
        aggregated[model_name] = {
            "maeMean": round(float(np.mean(maes)), 4),
            "maeStd": round(float(np.std(maes)), 4),
            "maeMin": round(float(np.min(maes)), 4),
            "maeMax": round(float(np.max(maes)), 4),
            "rep0": repeats[0]["models"][model_name],
        }
    results["splits"]["random60_40"] = {
        "repeats": random_repeats,
        "trainN": repeats[0]["trainN"],
        "testN": repeats[0]["testN"],
        "aggregated": aggregated,
    }

    loro: dict[str, Any] = {}
    for region in sorted(frame["region"].unique()):
        train, test = split_loro(frame, str(region))
        loro[str(region)] = _run_variant_split(train, test, seed=2000)
    results["splits"]["leaveOneRegionOut"] = loro

    train, test = split_temporal(frame)
    results["splits"]["temporalWY2026"] = _run_variant_split(train, test, seed=3000)

    train, test = split_stations(frame, seed=4000)
    results["splits"]["unseenStations"] = _run_variant_split(train, test, seed=4000)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results_variants.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (output_dir / "model_sidecar_variants.json").write_text(json.dumps({
        "profileSource": profile_source,
        "trainingRows": int(len(frame)),
        "models": {
            "gbm_eb": "HistGradientBoostingRegressor (sklearn defaults) + the RFEBModel EB residual layer (hierarchical trainer on log residuals of station-grouped OOF predictions)",
            "ens_rg": "mean of rf_eb and raw gbm predictions",
            "ens_eb": "mean of rf_eb and gbm_eb predictions",
            "med_gbm": "HistGradientBoostingRegressor(loss='quantile', quantile=0.5) (median objective)",
            "med_gbm_eb": "med_gbm + per-region median log-residual layer (trainer shrinkage form, prior ESS 30; factor clamp [0.5, 2.0]; SLR clamp [3, 30]; fitted on station-grouped OOF residuals)",
            "tail_rf_eb": "rf_eb with the effective EB correction shrunk toward 1 by clip(15 / rf_prediction, 0, 1) when the RF prediction exceeds 15",
        },
        "note": "Research artifacts only. Not app artifacts; do not bundle into src/.",
    }, indent=2), encoding="utf-8")
    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt(value: Any) -> str:
    return "—" if value is None else f"{value:.3f}"


def _loro_winners(results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    winners: dict[str, dict[str, Any]] = {}
    for region, split in results["splits"]["leaveOneRegionOut"].items():
        maes = {m: split["models"][m]["overall"]["mae"] for m in MODELS}
        best = min(maes, key=lambda m: maes[m])
        winners[region] = {
            "winner": best,
            "winnerMae": maes[best],
            "rfMae": maes["rf"],
            "kucheraMae": maes["kuchera"],
            "interceptMae": maes["intercept"],
            "rfVsKuchera": round(maes["kuchera"] - maes["rf"], 4),
        }
    return winners


def write_report(results: dict[str, Any], coverage: dict[str, Any] | None, path: Path) -> None:
    acc = results["accounting"]
    agg = results["splits"]["random60_40"]["aggregated"]
    winners = _loro_winners(results)
    lines: list[str] = []
    lines.append("# Step 2 — pooled gold-only profile-feature model (v1 benchmark)")
    lines.append("")
    lines.append("## Provenance — read first")
    lines.append("")
    if results.get("profileSource") == ERA5_PROFILE_SOURCE:
        lines.append(
            "**Profile source: `era5-nsf-ncar-aws` — true ECMWF ERA5 reanalysis**, fetched "
            "from the NSF NCAR mirror of ERA5 on AWS Open Data (bucket `nsf-ncar-era5`, "
            "anonymous read), with native relative humidity at the 13 pressure levels "
            "1000–500 hPa. This is the same reanalysis Pletcher et al. (2026) trained on. "
            "Verified coverage: pressure-level months 1940-01 through 2026-07. Features and "
            "Kuchera baselines are computed from the same ERA5 profiles, so they share "
            "provenance. (The step-2 v1 benchmark used the Open-Meteo best-match forecast "
            "archive instead; its report describes that source. Numbers here are ERA5 "
            "numbers and are not pooled with best-match numbers anywhere.)"
        )
        lines.append("")
        lines.append("Deviations from the Pletcher et al. spec: 13 of ERA5's 37 pressure "
                     "levels for the AGL interpolation (the finest low-level set, shared "
                     "with the step-2 spec); 24-h means over each event's observation "
                     "window (24 h ending at the stored local-standard valid_time) rather "
                     "than a calendar day; RF n_estimators=500 with sklearn defaults "
                     "otherwise (paper does not fully specify hyperparameters); GBM at "
                     "sklearn defaults.")
    else:
        lines.append(
            "**Profile source: `open-meteo-historical-forecast-best-match` — the Open-Meteo "
            "historical-forecast best-match archive, NOT ERA5.** Pletcher et al. (2026) trained on "
            "ERA5, but ERA5 pressure levels are unreachable from this workspace: the Open-Meteo "
            "archive (ERA5) API returns every pressure-level field null with `hourly_units` "
            "\"undefined\" (its published variable list contains no pressure-level variables at "
            "all), and Copernicus CDS requires a personal API key this project does not hold. "
            "The best-match archive is the fallback sanctioned for this case; it is also the "
            "source of every previous baseline in this project, so features and Kuchera "
            "baselines share provenance. The archive's coverage starts mid-2021, so the "
            "modeling pool is the 2021-06+ subset of the screened pool. Treat all numbers "
            "below as best-match-archive numbers, not ERA5 numbers."
        )
        lines.append("")
        lines.append("Deviations from the Pletcher et al. spec: profile source (above); 13 API "
                     "pressure levels instead of ERA5's 37 for the AGL interpolation; wind from "
                     "the API wind_speed field; 24-h means over each event's observation window "
                     "(24 h ending at the stored local-standard valid_time) rather than a "
                     "calendar day; RF n_estimators=500 with sklearn defaults otherwise (paper "
                     "does not fully specify hyperparameters); GBM at sklearn defaults.")
    lines.append("")
    lines.append("## Feature coverage")
    lines.append("")
    if coverage:
        lines.append(f"Profile source: `{coverage['profileSource']}`. Total screened events: "
                     f"{coverage['eventsTotal']}. Status counts: "
                     + ", ".join(f"{k}={v}" for k, v in sorted(coverage["statusCounts"].items())) + ".")
        lines.append("")
        lines.append("| Region | ok | incomplete_profile | no_baseline | outside_archive_coverage | fetch_missing |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for region, counts in sorted(coverage["byRegion"].items()):
            lines.append(
                f"| {region} | {counts.get('ok', 0)} | {counts.get('incomplete_profile', 0)} | "
                f"{counts.get('no_baseline', 0)} | {counts.get('outside_archive_coverage', 0)} | "
                f"{counts.get('fetch_missing', 0)} |"
            )
        lines.append("")
    lines.append(f"Modeling rows (complete features + Kuchera baseline): **{acc['modelingRows']}** "
                 f"of {acc['eventsTotal']} screened events. By region: "
                 + ", ".join(f"{k}={v}" for k, v in sorted(acc["modelingByRegion"].items()))
                 + f". Water years: {acc['modelingWaterYears']}.")
    lines.append("")
    lines.append("## Headline — randomized 60/40 (20 repeats, Pletcher-comparable)")
    lines.append("")
    lines.append("| Model | MAE mean | MAE std | rep0 bias | rep0 RMSE | rep0 tail>20 MAE (n) | rep0 tail<5 MAE (n) | rep0 snowfall MAE (mm) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for model in MODELS:
        rep0 = agg[model]["rep0"]
        lines.append(
            f"| {model} | {agg[model]['maeMean']:.3f} | {agg[model]['maeStd']:.3f} | "
            f"{_fmt(rep0['overall']['bias'])} | {_fmt(rep0['overall']['rmse'])} | "
            f"{_fmt(rep0['tailHigh_slr_gt_20']['mae'])} ({rep0['tailHigh_slr_gt_20']['n']}) | "
            f"{_fmt(rep0['tailLow_slr_lt_5']['mae'])} ({rep0['tailLow_slr_lt_5']['n']}) | "
            f"{_fmt(rep0['snowfall']['maeMm'])} |"
        )
    lines.append("")
    lines.append("Published reference (different labels/profiles, NOT directly comparable): "
                 "Pletcher et al. RF MAE 2.92 CONUS; operational methods 3.51–4.76.")
    lines.append("")
    lines.append("## Leave-one-region-out (decisive transfer test)")
    lines.append("")
    lines.append("| Held-out region | test n | winner | winner MAE | RF MAE | GBM MAE | intercept MAE | EB-gold MAE | RF+EB MAE | climatology MAE | Kuchera MAE | RF − Kuchera |")
    lines.append("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for region, split in sorted(results["splits"]["leaveOneRegionOut"].items()):
        m = split["models"]
        w = winners[region]
        lines.append(
            f"| {region} | {split['testN']} | {w['winner']} | {w['winnerMae']:.3f} | "
            f"{m['rf']['overall']['mae']:.3f} | {m['gbm']['overall']['mae']:.3f} | "
            f"{m['intercept']['overall']['mae']:.3f} | {m['eb_gold']['overall']['mae']:.3f} | "
            f"{m['rf_eb']['overall']['mae']:.3f} | {m['climatology']['overall']['mae']:.3f} | "
            f"{m['kuchera']['overall']['mae']:.3f} | {w['rfVsKuchera']:+.3f} |"
        )
    lines.append("")
    lines.append("Per-region verdicts: "
                 + "; ".join(
                     f"{region}: **{w['winner']}** wins (MAE {w['winnerMae']:.3f}); RF "
                     f"{'beats' if w['rfVsKuchera'] > 0 else 'loses to'} Kuchera by "
                     f"{abs(w['rfVsKuchera']):.3f}"
                     for region, w in sorted(winners.items())
                 ) + ".")
    rf_loro_wins = sum(1 for w in winners.values() if w["rfVsKuchera"] > 0)
    lines.append("")
    lines.append(f"RF beats raw Kuchera under LORO in {rf_loro_wins} of {len(winners)} regions. "
                 "The randomized-split RF advantage "
                 + ("survives" if rf_loro_wins >= len(winners) - 1 else "does NOT fully survive")
                 + " the transfer test; see per-region rows above.")
    lines.append("")
    for split_name, title in (("temporalWY2026", "Temporal holdout (train < WY2026, test WY2026)"),
                              ("unseenStations", "Unseen-station holdout (30% of stations)")):
        split = results["splits"][split_name]
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"Train n={split['trainN']}, test n={split['testN']}.")
        lines.append("")
        lines.append("| Model | MAE | bias | RMSE | tail>20 MAE (n) | tail<5 MAE (n) | snowfall MAE (mm) |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for model in MODELS:
            r = split["models"][model]
            lines.append(
                f"| {model} | {r['overall']['mae']:.3f} | {_fmt(r['overall']['bias'])} | "
                f"{_fmt(r['overall']['rmse'])} | {_fmt(r['tailHigh_slr_gt_20']['mae'])} "
                f"({r['tailHigh_slr_gt_20']['n']}) | {_fmt(r['tailLow_slr_lt_5']['mae'])} "
                f"({r['tailLow_slr_lt_5']['n']}) | {_fmt(r['snowfall']['maeMm'])} |"
            )
        lines.append("")
    lines.append("## RF permutation importance (randomized split, rep 0)")
    lines.append("")
    for entry in results["permutationImportance"][:10]:
        lines.append(f"- {entry['feature']}: {entry['importanceMean']:.3f} ± {entry['importanceStd']:.3f}")
    top = results["permutationImportance"][:5]
    dgx = [e["feature"] for e in top if e["feature"].startswith("t_") and e["feature"] in
           ("t_1200", "t_1500", "t_1800", "t_2100", "t_2400")]
    lines.append("")
    lines.append(
        "DGZ check: "
        + (f"{len(dgx)} of the top 5 features are temperatures at 1200–2400 m AGL "
           f"({', '.join(dgx)}), replicating Pletcher et al.'s finding that growth-layer "
           "temperatures dominate." if len(dgx) >= 3 else
           f"only {len(dgx)} of the top 5 are 1200–2400 m temperatures — the DGZ "
           "concentration Pletcher et al. reported is weaker here.")
    )
    lines.append("")
    lines.append("## Verdicts")
    lines.append("")
    rf_mae, gbm_mae = agg["rf"]["maeMean"], agg["gbm"]["maeMean"]
    lines.append(f"- **RF vs GBM (randomized):** RF {rf_mae:.3f} vs GBM {gbm_mae:.3f} — "
                 f"{'RF' if rf_mae < gbm_mae else 'GBM'} wins by {abs(rf_mae - gbm_mae):.3f} MAE.")
    loro_rf = float(np.mean([results['splits']['leaveOneRegionOut'][r]['models']['rf']['overall']['mae'] for r in winners]))
    loro_gbm = float(np.mean([results['splits']['leaveOneRegionOut'][r]['models']['gbm']['overall']['mae'] for r in winners]))
    lines.append(f"- **RF vs GBM (LORO mean):** RF {loro_rf:.3f} vs GBM {loro_gbm:.3f}.")
    rfeb = agg["rf_eb"]["maeMean"]
    lines.append(f"- **RF+EB residual vs RF (randomized):** {rfeb:.3f} vs {rf_mae:.3f} — "
                 f"the regional EB residual layer {'helps' if rfeb < rf_mae else 'does not help'} "
                 f"by {abs(rfeb - rf_mae):.3f} MAE on randomized splits; LORO means: RF+EB "
                 f"{float(np.mean([results['splits']['leaveOneRegionOut'][r]['models']['rf_eb']['overall']['mae'] for r in winners])):.3f} "
                 f"vs RF {loro_rf:.3f}.")
    lines.append(f"- **Our correction approaches vs direct regression:** intercept MAE "
                 f"{agg['intercept']['maeMean']:.3f}, EB-gold {agg['eb_gold']['maeMean']:.3f} "
                 f"(randomized) vs RF {rf_mae:.3f}; raw Kuchera {agg['kuchera']['maeMean']:.3f}; "
                 f"climatology {agg['climatology']['maeMean']:.3f}.")
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append("- Labels are the step-1 screened pool: the CoCoRaHS tier is behaviour-screened, "
                 "not provenance-proven (81% pass on survey-confirmed samplers; no confirmed-copier "
                 "reference exists). Northeast and Great Lakes stations are almost all below "
                 "900 m elevation; Alaska's screen validation was indirect only.")
    if results.get("profileSource") == ERA5_PROFILE_SOURCE:
        lines.append("- Profiles are ERA5 reanalysis (NSF NCAR mirror), not a forecast "
                     "blend: no lead-time error of the kind the best-match archive carries. "
                     "ERA5's own resolution and assimilation limits still apply, and the "
                     "13-level subset is coarser than the full 37-level ERA5 grid.")
        lines.append("- The screened pool spans water years 1999–2026; modeling rows are "
                     "concentrated in 2006–2026 (the year-expansion target pool) with a "
                     "small number of earlier rows whose fetch chunks were shared with "
                     "in-pool events. ERA5 coverage ends 2026-07, so late-WY2026 events "
                     "without complete profiles are excluded by the completeness screen, "
                     "and the temporal holdout is a single water year (WY2026).")
    else:
        lines.append("- Profiles are a best-match forecast-archive blend, not reanalysis: the "
                     "contributing model varies by date, and forecast (not analysis) fields carry "
                     "lead-time error that ERA5 would not. Numbers are therefore a lower bound "
                     "on what the same spec would do on true ERA5 profiles.")
        lines.append("- Events before 2021-06 are excluded by archive coverage; the temporal "
                     "holdout is a single water year (WY2026).")
    lines.append("- Tail subsets are small in some splits; read tail MAEs with their n.")
    lines.append("- Model pickles beside this report are research artifacts only — not app "
                 "artifacts, never bundled into src/, and the production calibration gates "
                 "are untouched.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_evaluate_gold_models(features_path: Path, output_dir: Path, random_repeats: int = 20) -> dict[str, Any]:
    results = run_benchmark(features_path, output_dir, random_repeats=random_repeats)
    coverage = None
    coverage_path = output_dir / "feature_coverage.json"
    if coverage_path.exists():
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    write_report(results, coverage, output_dir / "MODEL_REPORT.md")
    return results


# ---------------------------------------------------------------------------
# Cheap-wins bundle verdicts and report sections (items 1-4)
# ---------------------------------------------------------------------------

_HEADLINE_SPLITS = ("randomised", "loroMean", "temporal", "stations")


def headline_metrics(results: dict[str, Any], model: str) -> dict[str, Any]:
    """The four headline split MAEs + rep0 tails for one model."""
    agg = results["splits"]["random60_40"]["aggregated"][model]
    loro = results["splits"]["leaveOneRegionOut"]
    loro_maes = [split["models"][model]["overall"]["mae"] for split in loro.values()]
    return {
        "randomised": agg["maeMean"],
        "randomisedStd": agg["maeStd"],
        "loroMean": round(float(np.mean(loro_maes)), 4),
        "temporal": results["splits"]["temporalWY2026"]["models"][model]["overall"]["mae"],
        "stations": results["splits"]["unseenStations"]["models"][model]["overall"]["mae"],
        "tailHigh": agg["rep0"]["tailHigh_slr_gt_20"]["mae"],
        "tailHighN": agg["rep0"]["tailHigh_slr_gt_20"]["n"],
        "tailLow": agg["rep0"]["tailLow_slr_lt_5"]["mae"],
        "tailLowN": agg["rep0"]["tailLow_slr_lt_5"]["n"],
    }


def judge_candidate(candidate: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    """Pre-declared materiality rule for the bundle.

    Material gain: >= 0.03 MAE improvement on at least one headline
    split, with no regression > 0.05 on any other headline split and
    no regression > 0.20 on the high tail (randomised rep 0).  A
    candidate breaching a regression limit is harmful; anything else
    is no material gain.  Deltas are reference - candidate (positive =
    improvement).
    """
    deltas = {key: round(float(reference[key] - candidate[key]), 4) for key in _HEADLINE_SPLITS}
    tail_high_delta = round(float(reference["tailHigh"] - candidate["tailHigh"]), 4)
    tail_low_delta = round(float(reference["tailLow"] - candidate["tailLow"]), 4)
    breach = any(deltas[key] < -0.05 for key in _HEADLINE_SPLITS) or tail_high_delta < -0.20
    gain = max(deltas.values())
    if gain >= 0.03 and not breach:
        verdict = "adopt"
    elif breach:
        verdict = "harmful"
    else:
        verdict = "no material gain"
    return {
        "verdict": verdict,
        "deltas": deltas,
        "tailHighDelta": tail_high_delta,
        "tailLowDelta": tail_low_delta,
    }


def _headline_table(results: dict[str, Any], variants: dict[str, Any]) -> list[str]:
    lines = ["| Model | randomised | LORO mean | temporal | unseen-station | tail>20 (rep0) | tail<5 (rep0) |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for model in VARIANT_MODELS:
        h = headline_metrics(variants, model)
        lines.append(
            f"| {model} | {h['randomised']:.3f} ±{h['randomisedStd']:.3f} | {h['loroMean']:.3f} | "
            f"{h['temporal']:.3f} | {h['stations']:.3f} | {_fmt(h['tailHigh'])} (n={h['tailHighN']}) | "
            f"{_fmt(h['tailLow'])} (n={h['tailLowN']}) |"
        )
    return lines


def append_bundle_report(output_dir: Path, baseline_results_path: Path) -> dict[str, Any]:
    """Append the bundle sections (items 1-4 + verdicts) to MODEL_REPORT.md."""
    results = json.loads((output_dir / "results.json").read_text(encoding="utf-8"))
    variants = json.loads((output_dir / "results_variants.json").read_text(encoding="utf-8"))
    recovery = json.loads((output_dir / "recovery_report.json").read_text(encoding="utf-8"))
    baseline = json.loads(baseline_results_path.read_text(encoding="utf-8"))

    verdicts: dict[str, Any] = {}
    ref_v2 = headline_metrics(results, "rf_eb")
    base_v1 = headline_metrics(baseline, "rf_eb")
    verdicts["item1_data_recovery"] = judge_candidate(ref_v2, base_v1)
    for model in VARIANT_MODELS:
        verdicts[model] = judge_candidate(headline_metrics(variants, model), ref_v2)
    verdicts["gbm_eb_vs_gbm"] = judge_candidate(
        headline_metrics(variants, "gbm_eb"), headline_metrics(results, "gbm"))
    verdicts["med_gbm_vs_gbm"] = judge_candidate(
        headline_metrics(variants, "med_gbm"), headline_metrics(results, "gbm"))
    verdicts["med_gbm_eb_vs_gbm"] = judge_candidate(
        headline_metrics(variants, "med_gbm_eb"), headline_metrics(results, "gbm"))

    lines: list[str] = []
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Cheap-wins bundle — era5-expanded-v2")
    lines.append("")
    lines.append("Four pre-declared items, each judged against the era5-expanded baseline "
                 "(v1 pool, 54,880 rows) and against the item-2 ablation on this v2 pool. "
                 "Materiality rule (pre-declared): a candidate is **adopt** when it gains "
                 ">= 0.03 MAE on at least one headline split with no regression > 0.05 on "
                 "any other headline split and no regression > 0.20 on the high tail "
                 "(SLR > 20, randomised rep 0); a candidate breaching a regression limit "
                 "is **harmful**; otherwise **no material gain**. Deltas below are "
                 "reference - candidate, so positive is an improvement.")
    lines.append("")
    lines.append("### Item 1 — minute-offset event recovery (additive)")
    lines.append("")
    lines.append(
        f"Target events (source build marked incomplete_profile with a sub-hourly "
        f"valid_time): **{recovery['targetEvents']}**, of which "
        f"{recovery.get('targetsInExtendedPool', '—')} are in the WY2006+ extended pool. "
        f"Recovered to status ok (complete interpolated profiles + Kuchera baseline): "
        f"**{recovery['recoveredEvents']}**; still failing: {recovery['stillFailingEvents']} "
        f"(per-event reasons in recovery_report.json). Modeling rows: "
        f"{recovery['modelingRowsBefore']} -> {recovery['modelingRowsAfter']}. "
        "Recovered by region: "
        + ", ".join(f"{k}={v}" for k, v in sorted(recovery["recoveredByRegion"].items()))
        + ". Every non-recovered row is byte-carried from the source build (additive "
        "merge), so all v1 features for previously ok events are bit-identical in v2."
    )
    lines.append("")
    v1 = base_v1
    lines.append(
        f"Item 1 verdict (v2-pool RF+EB vs v1-pool RF+EB; pools differ by design — this "
        f"is the data effect, isolated by item 2 below): randomised {v1['randomised']:.3f} "
        f"-> {ref_v2['randomised']:.3f}, LORO mean {v1['loroMean']:.3f} -> "
        f"{ref_v2['loroMean']:.3f}, temporal {v1['temporal']:.3f} -> {ref_v2['temporal']:.3f}, "
        f"unseen-station {v1['stations']:.3f} -> {ref_v2['stations']:.3f}: "
        f"**{verdicts['item1_data_recovery']['verdict']}**."
    )
    lines.append("")
    lines.append("### Item 2 — ablation baseline (standard models on the v2 pool)")
    lines.append("")
    lines.append("The standard seven-model benchmark above (headline, LORO, temporal and "
                 "unseen-station tables in the main report body) was re-run unchanged on "
                 "the v2 pool; it is the reference for items 3–4, so model effects are "
                 "never confounded with the item-1 data effect.")
    lines.append("")
    lines.append("### Items 3–4 — variant headline results (v2 pool)")
    lines.append("")
    lines.extend(_headline_table(results, variants))
    lines.append("")
    lines.append(f"Reference (v2 pool): rf_eb randomised {ref_v2['randomised']:.3f}, LORO mean "
                 f"{ref_v2['loroMean']:.3f}, temporal {ref_v2['temporal']:.3f}, unseen-station "
                 f"{ref_v2['stations']:.3f}, tail>20 {_fmt(ref_v2['tailHigh'])}, tail<5 "
                 f"{_fmt(ref_v2['tailLow'])}; gbm randomised "
                 f"{headline_metrics(results, 'gbm')['randomised']:.3f}.")
    lines.append("")
    lines.append("Variant definitions: **gbm_eb** = GBM + the same hierarchical EB residual "
                 "layer as RF+EB (fitted on station-grouped OOF log residuals). "
                 "**ens_rg** = mean(rf_eb, raw gbm). **ens_eb** = mean(rf_eb, gbm_eb). "
                 "**med_gbm** = HistGradientBoosting with quantile loss (alpha 0.5). "
                 "**med_gbm_eb** = med_gbm + a per-region **median** log-residual layer: "
                 "corrections are medians (not the trainer's weighted means) of the "
                 "station-grouped OOF log residuals per region, shrunk with the trainer's "
                 "own form (ESS / (ESS + 30)) toward the shrunk global median, factor "
                 "clamped to [0.5, 2.0], SLR clamped to [3, 30] — the documented median "
                 "analogue of the EB layer. **tail_rf_eb** (item 4) = rf_eb with the "
                 "effective EB correction shrunk toward 1 by clip(15 / rf_prediction, 0, 1) "
                 "whenever the RF prediction exceeds 15 (full correction at <= 15).")
    lines.append("")
    lines.append("Per-region LORO MAE (variants; rf_eb on the v2 pool as reference):")
    lines.append("")
    regions = sorted(variants["splits"]["leaveOneRegionOut"].keys())
    lines.append("| Held-out region | rf_eb (v2) | " + " | ".join(VARIANT_MODELS) + " |")
    lines.append("|---|---:|" + "---:|" * (1 + len(VARIANT_MODELS)))
    for region in regions:
        row = [f"| {region} | {results['splits']['leaveOneRegionOut'][region]['models']['rf_eb']['overall']['mae']:.3f} |"]
        for model in VARIANT_MODELS:
            row.append(f"{variants['splits']['leaveOneRegionOut'][region]['models'][model]['overall']['mae']:.3f} |")
        lines.append(" ".join(row))
    lines.append("")
    lines.append("Tails for every model (items 2–4), randomised rep 0:")
    lines.append("")
    lines.append("| Model | tail>20 MAE (n) | tail<5 MAE (n) |")
    lines.append("|---|---:|---:|")
    for model in MODELS:
        h = headline_metrics(results, model)
        lines.append(f"| {model} | {_fmt(h['tailHigh'])} ({h['tailHighN']}) | {_fmt(h['tailLow'])} ({h['tailLowN']}) |")
    for model in VARIANT_MODELS:
        h = headline_metrics(variants, model)
        lines.append(f"| {model} | {_fmt(h['tailHigh'])} ({h['tailHighN']}) | {_fmt(h['tailLow'])} ({h['tailLowN']}) |")
    lines.append("")
    for split_key, title in (("temporalWY2026", "Temporal WY2026"), ("unseenStations", "Unseen-station")):
        lines.append(f"Tails, {title} split:")
        lines.append("")
        lines.append("| Model | tail>20 MAE (n) | tail<5 MAE (n) |")
        lines.append("|---|---:|---:|")
        for model in MODELS:
            r = results["splits"][split_key]["models"][model]
            lines.append(f"| {model} | {_fmt(r['tailHigh_slr_gt_20']['mae'])} ({r['tailHigh_slr_gt_20']['n']}) | "
                         f"{_fmt(r['tailLow_slr_lt_5']['mae'])} ({r['tailLow_slr_lt_5']['n']}) |")
        for model in VARIANT_MODELS:
            r = variants["splits"][split_key]["models"][model]
            lines.append(f"| {model} | {_fmt(r['tailHigh_slr_gt_20']['mae'])} ({r['tailHigh_slr_gt_20']['n']}) | "
                         f"{_fmt(r['tailLow_slr_lt_5']['mae'])} ({r['tailLow_slr_lt_5']['n']}) |")
        lines.append("")
    lines.append("### Verdicts")
    lines.append("")
    labels = {
        "gbm_eb": "Item 3 — GBM+EB",
        "ens_rg": "Item 3 — ENS-RG = mean(rf_eb, gbm)",
        "ens_eb": "Item 3 — ENS-EB = mean(rf_eb, gbm_eb)",
        "med_gbm": "Item 3 — MED (quantile GBM)",
        "med_gbm_eb": "Item 3 — MED+EB (median residuals)",
        "tail_rf_eb": "Item 4 — TAIL (magnitude-shrunk EB)",
    }
    for model in VARIANT_MODELS:
        judge = verdicts[model]
        d = judge["deltas"]
        lines.append(
            f"- **{labels[model]}: {judge['verdict']}** (vs rf_eb on the v2 pool: "
            f"randomised {d['randomised']:+.3f}, LORO {d['loroMean']:+.3f}, temporal "
            f"{d['temporal']:+.3f}, unseen-station {d['stations']:+.3f}, tail>20 "
            f"{judge['tailHighDelta']:+.3f}, tail<5 {judge['tailLowDelta']:+.3f})."
        )
    for key, label in (("gbm_eb_vs_gbm", "GBM+EB vs raw GBM"), ("med_gbm_vs_gbm", "MED vs raw GBM"),
                       ("med_gbm_eb_vs_gbm", "MED+EB vs raw GBM")):
        judge = verdicts[key]
        d = judge["deltas"]
        lines.append(
            f"- {label}: **{judge['verdict']}** (randomised {d['randomised']:+.3f}, LORO "
            f"{d['loroMean']:+.3f}, temporal {d['temporal']:+.3f}, unseen-station "
            f"{d['stations']:+.3f}, tail>20 {judge['tailHighDelta']:+.3f})."
        )
    lines.append("")
    lines.append("Production calibration gates are untouched; all v2 artifacts are research "
                 "files under the data tree, like v1.")
    lines.append("")

    report_path = output_dir / "MODEL_REPORT.md"
    existing = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    report_path.write_text(existing + "\n".join(lines), encoding="utf-8")
    (output_dir / "bundle_verdicts.json").write_text(json.dumps(verdicts, indent=2), encoding="utf-8")
    return verdicts


# ---------------------------------------------------------------------------
# Pressure-level model judging (model-v2-pressure).  Same labels, splits,
# seeds, model hyperparameters and EB machinery as run_benchmark /
# run_variant_benchmark -- only the feature representation changes, from
# AGL bands to native ERA5 pressure levels (pressure_features.py), so the
# scores are directly comparable to the era5-expanded-v2 judging.  The
# adoption rule applied to the results is the pre-registered one in
# mysnow-pressure-level-model-plan.md section 7.
# ---------------------------------------------------------------------------

PRESSURE_MODELS = ("rf", "gbm", "rf_eb", "gbm_eb", "ens_eb", "kuchera")

PRESSURE_REPRESENTATION = (
    "ERA5 profiles in native pressure coordinates: window means of t / r / ws "
    "at the 13 pressure levels 1000-500 hPa from the NSF NCAR ERA5 mirror; "
    "surface pressure is the window mean of fetched ERA5 an.sfc sp "
    "(128_134_sp), Pa converted to hPa"
)


def add_obs_hour_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add obs_end_hour_sin / obs_end_hour_cos from the LOCAL valid_time.

    ``valid_time`` is a local ISO string carrying its UTC offset, e.g.
    "2007-11-12T07:00:00-09:00".  The wall-clock time in the string IS
    the local observation-end time; it is parsed exactly as written and
    never converted to UTC (a UTC conversion would shift the dominant
    07:00-local peak to 12-13 UTC and silently corrupt the feature --
    data-accuracy audit Check 7).
    """
    import re

    pattern = re.compile(r"T(\d{2}):(\d{2})")

    def _hour_angle(value: Any) -> float:
        match = pattern.search(str(value))
        if match is None:
            raise ValueError(f"valid_time lacks a local time component: {value!r}")
        hour = int(match.group(1)) + int(match.group(2)) / 60.0
        return 2.0 * math.pi * hour / 24.0

    out = frame.copy()
    angles = out["valid_time"].map(_hour_angle).to_numpy()
    out["obs_end_hour_sin"] = np.sin(angles)
    out["obs_end_hour_cos"] = np.cos(angles)
    return out


def _run_pressure_split(
    train: pd.DataFrame,
    test: pd.DataFrame,
    seed: int,
    columns: Any,
) -> dict[str, Any]:
    """One split of the pressure ladder; components fitted once, shared.

    rf / gbm are the raw base learners exactly as run_benchmark scores
    them; rf_eb / gbm_eb come from the same EB-residual models the
    variant benchmark used, and ens_eb is their mean -- identical
    construction to results_variants.json's ens_eb.
    """
    cols = _feature_columns(columns)
    X_test = test[cols].to_numpy()
    rf_pred = _fit_rf(train, seed, cols).predict(X_test)
    gbm_pred = _fit_gbm(train, seed, cols).predict(X_test)
    _, rf_eb_pred = RFEBModel(seed, columns=cols).fit(train).predict_parts(test)
    _, gbm_eb_pred = GBMEBModel(seed, columns=cols).fit(train).predict_parts(test)
    predictions = {
        "rf": rf_pred,
        "gbm": gbm_pred,
        "rf_eb": rf_eb_pred,
        "gbm_eb": gbm_eb_pred,
        "ens_eb": (rf_eb_pred + gbm_eb_pred) / 2.0,
        "kuchera": test["baseline_slr"].to_numpy(),
    }
    out: dict[str, Any] = {"trainN": int(len(train)), "testN": int(len(test)), "models": {}}
    for model_name in PRESSURE_MODELS:
        out["models"][model_name] = evaluate_predictions(test, predictions[model_name])
    return out


def run_pressure_benchmark(
    features_path: Path,
    output_dir: Path,
    *,
    columns: Any,
    variant: str,
    random_repeats: int = 20,
    splits: tuple[str, ...] = ("random", "loro", "temporal", "stations"),
    add_obs_hour: bool = False,
    permutation_importance_flag: bool = False,
) -> dict[str, Any]:
    """Judge one pressure-level variant on the champion's splits/seeds."""
    frame, accounting = load_modeling_frame(features_path)
    if add_obs_hour:
        frame = add_obs_hour_features(frame)
    cols = _feature_columns(columns)
    missing = [c for c in cols if c not in frame.columns]
    if missing:
        raise ValueError(f"features frame is missing columns: {missing}")
    results: dict[str, Any] = {
        "profileSource": _frame_profile_source(frame),
        "featureRepresentation": PRESSURE_REPRESENTATION,
        "variant": variant,
        "features": cols,
        "accounting": accounting,
        "splits": {},
    }

    if "random" in splits:
        repeats: list[dict[str, Any]] = []
        for rep in range(random_repeats):
            train, test = split_random(frame, seed=1000 + rep)
            repeats.append(_run_pressure_split(train, test, seed=1000 + rep, columns=cols))
        aggregated: dict[str, Any] = {}
        for model_name in PRESSURE_MODELS:
            maes = [r["models"][model_name]["overall"]["mae"] for r in repeats]
            aggregated[model_name] = {
                "maeMean": round(float(np.mean(maes)), 4),
                "maeStd": round(float(np.std(maes)), 4),
                "maeMin": round(float(np.min(maes)), 4),
                "maeMax": round(float(np.max(maes)), 4),
                "rep0": repeats[0]["models"][model_name],
            }
        results["splits"]["random60_40"] = {
            "repeats": random_repeats,
            "trainN": repeats[0]["trainN"],
            "testN": repeats[0]["testN"],
            "aggregated": aggregated,
        }

    if permutation_importance_flag:
        from sklearn.inspection import permutation_importance

        train0, test0 = split_random(frame, seed=1000)
        rf0 = _fit_rf(train0, 1000, cols)
        importance = permutation_importance(
            rf0, test0[cols].to_numpy(), test0["observed_slr"].to_numpy(),
            n_repeats=5, random_state=0, n_jobs=-1,
        )
        order = np.argsort(importance.importances_mean)[::-1]
        results["permutationImportance"] = [
            {"feature": cols[i], "importanceMean": round(float(importance.importances_mean[i]), 4),
             "importanceStd": round(float(importance.importances_std[i]), 4)}
            for i in order
        ]

    if "loro" in splits:
        loro: dict[str, Any] = {}
        for region in sorted(frame["region"].unique()):
            train, test = split_loro(frame, str(region))
            loro[str(region)] = _run_pressure_split(train, test, seed=2000, columns=cols)
        results["splits"]["leaveOneRegionOut"] = loro

    if "temporal" in splits:
        train, test = split_temporal(frame)
        results["splits"]["temporalWY2026"] = _run_pressure_split(
            train, test, seed=3000, columns=cols)

    if "stations" in splits:
        train, test = split_stations(frame, seed=4000)
        results["splits"]["unseenStations"] = _run_pressure_split(
            train, test, seed=4000, columns=cols)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"results_pressure_{variant}.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")
    return results


def loro_region_metrics(results: dict[str, Any], model: str) -> dict[str, Any]:
    """Per-region MAE/bias/n of one model under LORO (held-out region rows)."""
    out: dict[str, Any] = {}
    for region, split in results["splits"]["leaveOneRegionOut"].items():
        overall = split["models"][model]["overall"]
        out[str(region)] = {"mae": overall["mae"], "bias": overall["bias"], "n": overall["n"]}
    return out


def split_maes(results: dict[str, Any], model: str) -> dict[str, Any]:
    """Headline MAEs for the splits a results dict actually contains."""
    out: dict[str, Any] = {}
    splits = results.get("splits", {})
    if "random60_40" in splits:
        out["randomised"] = splits["random60_40"]["aggregated"][model]["maeMean"]
    if "leaveOneRegionOut" in splits:
        maes = [s["models"][model]["overall"]["mae"] for s in splits["leaveOneRegionOut"].values()]
        out["loroMean"] = round(float(np.mean(maes)), 4)
    if "temporalWY2026" in splits:
        out["temporal"] = splits["temporalWY2026"]["models"][model]["overall"]["mae"]
    if "unseenStations" in splits:
        out["stations"] = splits["unseenStations"]["models"][model]["overall"]["mae"]
    return out


def pressure_route_verdict(
    candidate_results: dict[str, Any],
    reference_variants: dict[str, Any],
) -> dict[str, Any]:
    """Plan section 7, applied mechanically to ENS-EB.

    Route A (overall): LORO improves >= 0.03, no headline split regresses
    > 0.05, high tail (randomised rep0, SLR > 20) regresses <= 0.20.
    Route B (regional bias): Alaska or PNW LORO |bias| improves >= 0.5
    with no MAE regression in that region.  Deltas are reference -
    candidate (positive = improvement).
    """
    cand = headline_metrics(candidate_results, "ens_eb")
    ref = headline_metrics(reference_variants, "ens_eb")
    deltas = {key: round(float(ref[key] - cand[key]), 4) for key in _HEADLINE_SPLITS}
    tail_high_delta = round(float(ref["tailHigh"] - cand["tailHigh"]), 4)
    route_a = {
        "loroGain": deltas["loroMean"],
        "loroGainOk": deltas["loroMean"] >= 0.03,
        "headlineDeltas": deltas,
        "headlineOk": all(value >= -0.05 for value in deltas.values()),
        "tailHighDelta": tail_high_delta,
        "tailHighOk": tail_high_delta >= -0.20,
    }
    route_a["passed"] = bool(
        route_a["loroGainOk"] and route_a["headlineOk"] and route_a["tailHighOk"])

    cand_loro = loro_region_metrics(candidate_results, "ens_eb")
    ref_loro = loro_region_metrics(reference_variants, "ens_eb")
    route_b: dict[str, Any] = {"regions": {}}
    route_b_passed = False
    for region in ("alaska", "pacific-northwest"):
        bias_gain = round(abs(ref_loro[region]["bias"]) - abs(cand_loro[region]["bias"]), 4)
        mae_delta = round(float(ref_loro[region]["mae"] - cand_loro[region]["mae"]), 4)
        passed = bool(bias_gain >= 0.5 and mae_delta >= 0.0)
        route_b["regions"][region] = {
            "referenceBias": ref_loro[region]["bias"],
            "candidateBias": cand_loro[region]["bias"],
            "absBiasGain": bias_gain,
            "referenceMae": ref_loro[region]["mae"],
            "candidateMae": cand_loro[region]["mae"],
            "maeDelta": mae_delta,
            "passed": passed,
        }
        route_b_passed = route_b_passed or passed
    route_b["passed"] = bool(route_b_passed)

    return {
        "adopted": bool(route_a["passed"] or route_b["passed"]),
        "routeA": route_a,
        "routeB": route_b,
        "candidateHeadline": cand,
        "referenceHeadline": ref,
        "candidateLoroRegions": cand_loro,
        "referenceLoroRegions": ref_loro,
    }


def _ablation_summary(
    base_results: dict[str, Any], ablated_results: dict[str, Any]
) -> dict[str, Any]:
    """ENS-EB MAE deltas of an ablation vs its base (positive = ablation worse)."""
    base = split_maes(base_results, "ens_eb")
    ablated = split_maes(ablated_results, "ens_eb")
    return {
        key: round(float(ablated[key] - base[key]), 4)
        for key in ("randomised", "loroMean", "temporal", "stations")
        if key in base and key in ablated
    }


def write_pressure_report_bundle(pressure_dir: Path, reference_dir: Path) -> dict[str, Any]:
    """Aggregate the per-variant pressure results, apply plan section 7,
    and write verdict_pressure.json + PRESSURE_MODEL_REPORT.md."""
    ref_base = json.loads((reference_dir / "results.json").read_text(encoding="utf-8"))
    ref_variants = json.loads((reference_dir / "results_variants.json").read_text(encoding="utf-8"))

    variant_results: dict[str, Any] = {}
    for path in sorted(pressure_dir.glob("results_pressure_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        variant_results[str(payload["variant"])] = payload
    if not variant_results:
        raise FileNotFoundError(f"no results_pressure_*.json under {pressure_dir}")

    full_variants = [v for v, r in variant_results.items() if "unseenStations" in r["splits"]]
    if not full_variants:
        raise ValueError("no fully-judged pressure variant (unseenStations split missing)")

    def _loro_mean(variant: str) -> float:
        return float(split_maes(variant_results[variant], "ens_eb")["loroMean"])

    better = sorted(full_variants, key=lambda v: (_loro_mean(v), v != "p_core", v))[0]
    verdict = pressure_route_verdict(variant_results[better], ref_variants)
    verdict["betterVariant"] = better
    verdict["fullVariants"] = {v: split_maes(variant_results[v], "ens_eb") for v in full_variants}

    ablations: dict[str, Any] = {}
    if "p_core" in variant_results:
        if "p_core_notime" in variant_results:
            ablations["noTime"] = _ablation_summary(
                variant_results["p_core"], variant_results["p_core_notime"])
        if "p_core_obshour" in variant_results:
            ablations["obsHour"] = _ablation_summary(
                variant_results["p_core"], variant_results["p_core_obshour"])
    verdict["ablations"] = ablations

    ref_ens = headline_metrics(ref_variants, "ens_eb")
    ref_kuchera = headline_metrics(ref_base, "kuchera")
    cand_loro = loro_region_metrics(variant_results[better], "ens_eb")
    ref_loro = loro_region_metrics(ref_variants, "ens_eb")

    disp = {"rf": "RF", "gbm": "GBM", "rf_eb": "RF+EB", "gbm_eb": "GBM+EB",
            "ens_eb": "ENS-EB", "kuchera": "Kuchera"}
    lines: list[str] = []
    lines.append("# Pressure-level model judging (model-v2-pressure)")
    lines.append("")
    lines.append("## Provenance — read first")
    lines.append("")
    lines.append(
        "**Profile representation: native ERA5 pressure levels** — window means of "
        "temperature, relative humidity and wind speed at the 13 pressure levels "
        "1000–500 hPa, taken from the same NSF NCAR ERA5 profiles "
        "(`era5-nsf-ncar-aws`) the AGL champion was judged on, *without* the "
        "interpolation into 300–2400 m AGL bands. Surface pressure is the window "
        "mean of the real ERA5 an.sfc surface-pressure field (128_134_sp), "
        "fetched for this plan and validated in the data-accuracy audit. Labels, "
        "the 57,376-event modelling pool, split constructions, seeds, model "
        "hyperparameters and the hierarchical EB layer are identical to the "
        "era5-expanded-v2 judging (gold_model harness), so every score below is "
        "directly comparable to the AGL champion's. The AGL champion and its "
        "tree are untouched; production calibration gates are untouched."
    )
    lines.append("")
    lines.append("Variants judged: " + ", ".join(
        f"{v} ({len(r['features'])} features)" for v, r in sorted(variant_results.items())) + ".")
    lines.append("")
    lines.append("The P-geometry ablation (per-level height-above-ground features) was "
                 "**skipped**: the built pressure parquets persist only t/rh/ws level "
                 "means, so per-level geopotential heights would require a new builder "
                 "pass over the raw profile cache — new extraction plumbing, outside "
                 "this judging run.")
    lines.append("")
    lines.append("## Headline comparison (ENS-EB, MAE)")
    lines.append("")
    lines.append("| Model | randomised | LORO mean | temporal | unseen-station | tail>20 (rep0) | tail<5 (rep0) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for variant in full_variants:
        h = headline_metrics(variant_results[variant], "ens_eb")
        marker = " ← better pressure variant" if variant == better else ""
        lines.append(
            f"| {variant} ENS-EB{marker} | {h['randomised']:.3f} ±{h['randomisedStd']:.3f} | "
            f"{h['loroMean']:.3f} | {h['temporal']:.3f} | {h['stations']:.3f} | "
            f"{_fmt(h['tailHigh'])} (n={h['tailHighN']}) | {_fmt(h['tailLow'])} (n={h['tailLowN']}) |"
        )
    lines.append(
        f"| AGL champion ENS-EB | {ref_ens['randomised']:.3f} ±{ref_ens['randomisedStd']:.3f} | "
        f"{ref_ens['loroMean']:.3f} | {ref_ens['temporal']:.3f} | {ref_ens['stations']:.3f} | "
        f"{_fmt(ref_ens['tailHigh'])} (n={ref_ens['tailHighN']}) | "
        f"{_fmt(ref_ens['tailLow'])} (n={ref_ens['tailLowN']}) |"
    )
    lines.append(
        f"| Kuchera (same events) | {ref_kuchera['randomised']:.3f} | {ref_kuchera['loroMean']:.3f} | "
        f"{ref_kuchera['temporal']:.3f} | {ref_kuchera['stations']:.3f} | "
        f"{_fmt(ref_kuchera['tailHigh'])} (n={ref_kuchera['tailHighN']}) | "
        f"{_fmt(ref_kuchera['tailLow'])} (n={ref_kuchera['tailLowN']}) |"
    )
    lines.append("")

    for variant in full_variants:
        results = variant_results[variant]
        agg = results["splits"]["random60_40"]["aggregated"]
        lines.append(f"## {variant} — full ladder")
        lines.append("")
        lines.append("Randomised 60/40 (20 repeats):")
        lines.append("")
        lines.append("| Model | MAE mean | MAE std | rep0 bias | rep0 tail>20 MAE (n) | rep0 tail<5 MAE (n) |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for model in PRESSURE_MODELS:
            rep0 = agg[model]["rep0"]
            lines.append(
                f"| {disp[model]} | {agg[model]['maeMean']:.3f} | {agg[model]['maeStd']:.3f} | "
                f"{_fmt(rep0['overall']['bias'])} | {_fmt(rep0['tailHigh_slr_gt_20']['mae'])} "
                f"({rep0['tailHigh_slr_gt_20']['n']}) | {_fmt(rep0['tailLow_slr_lt_5']['mae'])} "
                f"({rep0['tailLow_slr_lt_5']['n']}) |"
            )
        lines.append("")
        lines.append("Leave-one-region-out (ENS-EB per held-out region; other models' MAE):")
        lines.append("")
        lines.append("| Held-out region | n | ENS-EB MAE | ENS-EB bias | RF | GBM | RF+EB | GBM+EB | Kuchera |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for region, split in sorted(results["splits"]["leaveOneRegionOut"].items()):
            m = split["models"]
            ens = m["ens_eb"]["overall"]
            lines.append(
                f"| {region} | {split['testN']} | {ens['mae']:.3f} | {_fmt(ens['bias'])} | "
                f"{m['rf']['overall']['mae']:.3f} | {m['gbm']['overall']['mae']:.3f} | "
                f"{m['rf_eb']['overall']['mae']:.3f} | {m['gbm_eb']['overall']['mae']:.3f} | "
                f"{m['kuchera']['overall']['mae']:.3f} |"
            )
        lines.append("")
        for split_name, title in (("temporalWY2026", "Temporal holdout (train < WY2026, test WY2026)"),
                                  ("unseenStations", "Unseen-station holdout (30% of stations)")):
            split = results["splits"][split_name]
            lines.append(f"{title} — train n={split['trainN']}, test n={split['testN']}:")
            lines.append("")
            lines.append("| Model | MAE | bias | tail>20 MAE (n) | tail<5 MAE (n) |")
            lines.append("|---|---:|---:|---:|---:|")
            for model in PRESSURE_MODELS:
                r = split["models"][model]
                lines.append(
                    f"| {disp[model]} | {r['overall']['mae']:.3f} | {_fmt(r['overall']['bias'])} | "
                    f"{_fmt(r['tailHigh_slr_gt_20']['mae'])} ({r['tailHigh_slr_gt_20']['n']}) | "
                    f"{_fmt(r['tailLow_slr_lt_5']['mae'])} ({r['tailLow_slr_lt_5']['n']}) |"
                )
            lines.append("")

    lines.append(f"## Better pressure variant ({better}) vs AGL champion — LORO per region (ENS-EB)")
    lines.append("")
    lines.append("| Region | n | champion MAE | pressure MAE | ΔMAE | champion bias | pressure bias | Δ|bias| |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for region in sorted(cand_loro):
        c, r = cand_loro[region], ref_loro[region]
        lines.append(
            f"| {region} | {c['n']} | {r['mae']:.3f} | {c['mae']:.3f} | "
            f"{r['mae'] - c['mae']:+.3f} | {_fmt(r['bias'])} | {_fmt(c['bias'])} | "
            f"{abs(r['bias']) - abs(c['bias']):+.3f} |"
        )
    lines.append("")

    if ablations:
        lines.append("## Ablations (ENS-EB MAE deltas: ablated variant minus P-core; positive = the ablated variant is worse)")
        lines.append("")
        if "noTime" in ablations:
            d = ablations["noTime"]
            lines.append(
                "- **Time features (month, doy_sin, doy_cos), removed:** "
                + ", ".join(f"{k} {v:+.3f}" for k, v in sorted(d.items()))
                + ". Positive deltas mean removing the time features hurt, i.e. "
                "P-core (with time features) is better."
            )
        if "obsHour" in ablations:
            d = ablations["obsHour"]
            lines.append(
                "- **Obs-end-hour features added to P-core:** deltas are "
                "P-core+hour minus P-core, so negative values are improvements "
                "from the hour features: "
                + ", ".join(f"{k} {v:+.3f}" for k, v in sorted(d.items()))
                + ". Per plan §2.6 the hour features count as helping only if the "
                "LORO and/or temporal delta is ≤ −0.030 (an improvement of "
                "≥ 0.03); a randomised-only gain does not count."
            )
        lines.append("")

    if "permutationImportance" in variant_results.get("p_core", {}):
        importance = variant_results["p_core"]["permutationImportance"]
        lines.append("## RF permutation importance (P-core, randomised split rep 0; R² drop, 5 repeats)")
        lines.append("")
        for entry in importance[:15]:
            lines.append(f"- {entry['feature']}: {entry['importanceMean']:.3f} ± {entry['importanceStd']:.3f}")
        lines.append("")
        groups: dict[str, float] = {}
        for entry in importance:
            name = entry["feature"]
            group = name.split("_")[0] if name.startswith(("t_", "rh_", "ws_")) else name
            groups[group] = groups.get(group, 0.0) + entry["importanceMean"]
        lines.append("Grouped importance sums: " + ", ".join(
            f"{k} {v:.3f}" for k, v in sorted(groups.items(), key=lambda kv: -kv[1])) + ".")
        lines.append("")

    route_a, route_b = verdict["routeA"], verdict["routeB"]
    lines.append("## Adoption verdict (plan §7, applied mechanically)")
    lines.append("")
    lines.append(f"Better pressure variant: **{better}** (by ENS-EB LORO mean).")
    lines.append("")
    lines.append(
        f"- **Route A (overall): {'PASS' if route_a['passed'] else 'FAIL'}** — LORO gain "
        f"{route_a['loroGain']:+.3f} (needs ≥ +0.030: {'ok' if route_a['loroGainOk'] else 'no'}); "
        "headline deltas "
        + ", ".join(f"{k} {v:+.3f}" for k, v in route_a["headlineDeltas"].items())
        + f" (none may be < −0.050: {'ok' if route_a['headlineOk'] else 'breached'}); "
        f"high-tail delta {route_a['tailHighDelta']:+.3f} "
        f"(must be ≥ −0.200: {'ok' if route_a['tailHighOk'] else 'breached'})."
    )
    for region, entry in sorted(route_b["regions"].items()):
        lines.append(
            f"- **Route B ({region}): {'PASS' if entry['passed'] else 'FAIL'}** — |bias| "
            f"{abs(entry['referenceBias']):.3f} → {abs(entry['candidateBias']):.3f} "
            f"(gain {entry['absBiasGain']:+.3f}, needs ≥ +0.500); region MAE delta "
            f"{entry['maeDelta']:+.3f} (must be ≥ 0.000)."
        )
    lines.append("")
    if verdict["adopted"]:
        lines.append(
            f"**ADOPTED — {better} ENS-EB becomes the research champion.** Production "
            "calibration gates are unchanged; promotion into production remains a "
            "separate step-6 decision."
        )
    else:
        lines.append(
            "**NOT ADOPTED — the AGL champion stands.** The pressure-level tree is "
            "archived as a judged experiment; its scores above are the record."
        )
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append("- Tail subsets are small in some splits; read tail MAEs with their n.")
    lines.append("- The temporal holdout is a single water year (WY2026).")
    lines.append("- Below-ground pressure levels are represented per the builder's "
                 "validity/fill rules (fetched-sp majority rule); P-mask exposes those "
                 "flags to the model, P-core does not.")
    lines.append("- All artifacts here are research files under the data tree — not app "
                 "artifacts, never bundled into src/.")
    lines.append("")

    (pressure_dir / "PRESSURE_MODEL_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    (pressure_dir / "verdict_pressure.json").write_text(
        json.dumps(verdict, indent=2), encoding="utf-8")
    return verdict
