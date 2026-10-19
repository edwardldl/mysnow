"""Candidate B (covariate ridge + EB hierarchy) and diagnostic models.

Design reference: mysnow-regional-slr-predictor-design.md sections 5, 5.1, 6.2.

Candidate B predicts the log-residual ``log(observed_slr / baseline_slr)``
with a ridge regression over the portable feature contract only (no
``fcst_snowfall_cm`` -- it is HRRR's native ASNOW diagnostic with no ECMWF
equivalent), then fits the existing hierarchical EB trainer on the ridge's
remaining residuals by training on rows whose baseline has been pre-scaled
by ``exp(ridge_prediction)``.  The artifact keeps schemaVersion 1 and gains
an additive top-level ``covariateModel`` block; the Python apply path here
applies ridge-then-EB and clamps the *total* correction factor to the
artifact bounds.  The TypeScript runtime does not yet apply the covariate
block -- that lockstep update is recorded as a requirement in the regional
evaluation report, and no B artifact may be bundled until it happens.

The ridge is implemented in pure Python with feature standardization, an
unpenalized intercept, and lambda selected by storm-grouped K-fold
cross-validation inside the training rows.  Gram matrices are computed once
per fit so the lambda sweep is a series of 10x10 Cholesky solves; this
keeps the package dependency-light and fully deterministic.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable

from .evaluation import Prediction, apply_artifact
from .trainer import TrainingConfig, _digest_rows, train_artifact

FEATURE_NAMES = (
    "baseline_slr",
    "log_fcst_precip_mm",
    "fcst_t2m_mean_c",
    "fcst_t2m_min_c",
    "fcst_t2m_max_c",
    "elevation_m",
    "latitude",
    "day_of_year_sin",
    "day_of_year_cos",
    "lead_max_h",
)
LAMBDA_GRID = (0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 1000.0)


def build_features(row: dict[str, Any]) -> dict[str, float] | None:
    """Portable-contract features for one event row; None when incomplete."""
    try:
        baseline = float(row["baseline_slr"])
        precip = float(row["fcst_precip_mm"])
        t2m_mean = float(row["fcst_t2m_mean_c"])
        t2m_min = float(row["fcst_t2m_min_c"])
        t2m_max = float(row["fcst_t2m_max_c"])
        elevation = float(row["elevation_m"])
        latitude = float(row["latitude"])
        lead = float(row["hrrr_lead_max_h"])
        day = date.fromisoformat(str(row["observation_date"])[:10]).timetuple().tm_yday
    except (KeyError, TypeError, ValueError):
        return None
    angle = 2.0 * math.pi * day / 365.25
    return {
        "baseline_slr": baseline,
        "log_fcst_precip_mm": math.log(max(precip, 0.1)),
        "fcst_t2m_mean_c": t2m_mean,
        "fcst_t2m_min_c": t2m_min,
        "fcst_t2m_max_c": t2m_max,
        "elevation_m": elevation,
        "latitude": latitude,
        "day_of_year_sin": math.sin(angle),
        "day_of_year_cos": math.cos(angle),
        "lead_max_h": lead,
    }


def _solve_spd(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Solve a symmetric positive-definite system via Cholesky factorization."""
    n = len(vector)
    lower = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            total = matrix[i][j] - sum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                if total <= 0:
                    raise ValueError("Normal-equations matrix is not positive definite")
                lower[i][j] = math.sqrt(total)
            else:
                lower[i][j] = total / lower[j][j]
    forward = [0.0] * n
    for i in range(n):
        forward[i] = (vector[i] - sum(lower[i][k] * forward[k] for k in range(i))) / lower[i][i]
    result = [0.0] * n
    for i in range(n - 1, -1, -1):
        result[i] = (forward[i] - sum(lower[k][i] * result[k] for k in range(i + 1, n))) / lower[i][i]
    return result


class RidgeModel:
    """Standardized ridge with unpenalized intercept; deterministic."""

    def __init__(self, feature_names: tuple[str, ...]):
        self.feature_names = feature_names
        self.means: dict[str, float] = {}
        self.stds: dict[str, float] = {}
        self.coefficients: dict[str, float] = {}
        self.dropped_features: list[str] = []
        self.intercept = 0.0  # weighted mean of the target
        self.lam = 0.0
        self.envelope: dict[str, list[float]] = {}

    # -- internal helpers ---------------------------------------------------
    def _standardize(self, samples: list[dict[str, float]], weights: list[float]) -> list[str]:
        total_weight = sum(weights)
        active: list[str] = []
        for name in self.feature_names:
            mean = sum(w * sample[name] for w, sample in zip(weights, samples)) / total_weight
            variance = sum(w * (sample[name] - mean) ** 2 for w, sample in zip(weights, samples)) / total_weight
            std = math.sqrt(max(variance, 0.0))
            self.means[name] = mean
            self.stds[name] = std
            self.envelope[name] = [min(sample[name] for sample in samples), max(sample[name] for sample in samples)]
            if std < 1e-12:
                self.dropped_features.append(name)
                self.coefficients[name] = 0.0
            else:
                active.append(name)
        return active

    def _z(self, sample: dict[str, float], active: list[str]) -> list[float]:
        return [(sample[name] - self.means[name]) / self.stds[name] for name in active]

    @staticmethod
    def _gram(z_rows: list[list[float]], targets: list[float], weights: list[float], center: float) -> tuple[list[list[float]], list[float]]:
        size = len(z_rows[0]) if z_rows else 0
        matrix = [[0.0] * size for _ in range(size)]
        vector = [0.0] * size
        for z, target, weight in zip(z_rows, targets, weights):
            centered = target - center
            for i in range(size):
                vector[i] += weight * z[i] * centered
                zi = z[i]
                row_i = matrix[i]
                for j in range(size):
                    row_i[j] += weight * zi * z[j]
        return matrix, vector

    # -- public API ---------------------------------------------------------
    def fit(self, samples: list[dict[str, float]], targets: list[float], weights: list[float], lam: float) -> "RidgeModel":
        total_weight = sum(weights)
        self.intercept = sum(w * y for w, y in zip(weights, targets)) / total_weight
        active = self._standardize(samples, weights)
        self.lam = lam
        if active:
            z_rows = [self._z(sample, active) for sample in samples]
            matrix, vector = self._gram(z_rows, targets, weights, self.intercept)
            for i in range(len(active)):
                matrix[i][i] += lam
            solution = _solve_spd(matrix, vector)
            for name, coefficient in zip(active, solution):
                self.coefficients[name] = coefficient
        return self

    def predict(self, features: dict[str, float], clamp_envelope: bool = False) -> tuple[float, bool]:
        """Return (prediction, out_of_envelope_flag)."""
        out = False
        total = self.intercept
        for name in self.feature_names:
            if name in self.dropped_features:
                continue
            value = features[name]
            low, high = self.envelope[name]
            if value < low or value > high:
                out = True
                if clamp_envelope:
                    value = max(low, min(high, value))
            total += self.coefficients[name] * (value - self.means[name]) / self.stds[name]
        return total, out

    def to_block(self) -> dict[str, Any]:
        return {
            "version": 1,
            "kind": "ridge-log-residual",
            "featureNames": list(self.feature_names),
            "droppedFeatures": list(self.dropped_features),
            "lambda": self.lam,
            "intercept": round(self.intercept, 8),
            "standardization": {
                name: {"mean": round(self.means[name], 8), "std": round(self.stds[name], 8)}
                for name in self.feature_names
            },
            "coefficients": {name: round(self.coefficients[name], 8) for name in self.feature_names},
            "featureEnvelope": {
                name: [round(self.envelope[name][0], 6), round(self.envelope[name][1], 6)]
                for name in self.feature_names
            },
            "envelopePolicy": "clamp features to the training envelope and flag the application out-of-envelope",
        }

    @classmethod
    def from_block(cls, block: dict[str, Any]) -> "RidgeModel":
        model = cls(tuple(block["featureNames"]))
        model.intercept = float(block["intercept"])
        model.lam = float(block["lambda"])
        model.dropped_features = list(block.get("droppedFeatures", []))
        for name in model.feature_names:
            stats = block["standardization"][name]
            model.means[name] = float(stats["mean"])
            model.stds[name] = float(stats["std"])
            model.coefficients[name] = float(block["coefficients"][name])
            low, high = block["featureEnvelope"][name]
            model.envelope[name] = [float(low), float(high)]
        return model


def storm_folds(rows: list[dict[str, Any]], folds: int = 5) -> dict[str, int]:
    groups = sorted({str(row["storm_group_id"]) for row in rows})
    return {group: index % folds for index, group in enumerate(groups)}


def select_lambda(rows: list[dict[str, Any]], feature_rows: list[dict[str, float]], targets: list[float]) -> tuple[float, dict[str, float]]:
    """Storm-grouped 5-fold CV over LAMBDA_GRID; returns (best_lambda, mse_by_lambda)."""
    weights = [float(row.get("qc_weight", 1.0)) for row in rows]
    fold_of = storm_folds(rows)
    fold_ids = [fold_of[str(row["storm_group_id"])] for row in rows]
    errors: dict[float, tuple[float, float]] = {lam: (0.0, 0.0) for lam in LAMBDA_GRID}
    for fold in range(5):
        train_idx = [i for i in range(len(rows)) if fold_ids[i] != fold]
        test_idx = [i for i in range(len(rows)) if fold_ids[i] == fold]
        if not train_idx or not test_idx:
            continue
        probe = RidgeModel(FEATURE_NAMES)
        train_samples = [feature_rows[i] for i in train_idx]
        train_targets = [targets[i] for i in train_idx]
        train_weights = [weights[i] for i in train_idx]
        total_weight = sum(train_weights)
        center = sum(w * y for w, y in zip(train_weights, train_targets)) / total_weight
        active = probe._standardize(train_samples, train_weights)
        if not active:
            continue
        z_train = [probe._z(sample, active) for sample in train_samples]
        matrix, vector = RidgeModel._gram(z_train, train_targets, train_weights, center)
        test_z = [probe._z(feature_rows[i], active) for i in test_idx]
        for lam in LAMBDA_GRID:
            penalized = [row[:] for row in matrix]
            for i in range(len(active)):
                penalized[i][i] += lam
            solution = _solve_spd(penalized, vector)
            squared, weight_sum = errors[lam]
            for z, i in zip(test_z, test_idx):
                prediction = center + sum(coef * value for coef, value in zip(solution, z))
                squared += weights[i] * (prediction - targets[i]) ** 2
                weight_sum += weights[i]
            errors[lam] = (squared, weight_sum)
    scores = {f"{lam:g}": (squared / weight_sum if weight_sum else float("inf")) for lam, (squared, weight_sum) in errors.items()}
    best = min(LAMBDA_GRID, key=lambda lam: scores[f"{lam:g}"])
    return best, scores


def fit_candidate_b(rows: list[dict[str, Any]], config: TrainingConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fit Candidate B: ridge on the log-residual, then EB on the residuals.

    Returns (artifact, fit_info).  The artifact is the existing trainer's
    output on ridge-adjusted rows plus the additive ``covariateModel`` block.
    """
    complete: list[dict[str, Any]] = []
    feature_rows: list[dict[str, float]] = []
    for row in rows:
        features = build_features(row)
        if features is not None:
            complete.append(row)
            feature_rows.append(features)
    if len(complete) < 20:
        raise ValueError(f"Candidate B needs at least 20 feature-complete rows, got {len(complete)}")
    targets = [math.log(float(row["observed_slr"]) / float(row["baseline_slr"])) for row in complete]
    weights = [float(row.get("qc_weight", 1.0)) for row in complete]
    lam, cv_scores = select_lambda(complete, feature_rows, targets)
    ridge = RidgeModel(FEATURE_NAMES).fit(feature_rows, targets, weights, lam)

    adjusted: list[dict[str, Any]] = []
    clamped = 0
    for row, features in zip(complete, feature_rows):
        prediction, _ = ridge.predict(features)
        scaled = float(row["baseline_slr"]) * math.exp(prediction)
        bounded = max(3.0, min(30.0, scaled))
        if bounded != scaled:
            clamped += 1
        adjusted.append({**row, "baseline_slr": bounded})
    artifact = train_artifact(adjusted, config)
    # Provenance must describe the original events, not the adjusted copy.
    artifact["training"]["sourceDigestSha256"] = _digest_rows(rows)
    artifact["training"]["candidate"] = "B"
    artifact["training"]["covariateAdjustedEventCount"] = len(complete)
    artifact["training"]["featureIncompleteEventCount"] = len(rows) - len(complete)
    artifact["training"]["adjustedBaselineClampedCount"] = clamped
    artifact["covariateModel"] = ridge.to_block()
    artifact["covariateModel"]["lambdaCvWeightedMse"] = {key: round(value, 8) for key, value in cv_scores.items()}
    artifact["covariateModel"]["lambdaSelection"] = "storm-grouped 5-fold CV within training rows, weighted MSE of the log-residual"
    return artifact, {"lambda": lam, "cvScores": cv_scores, "completeRows": len(complete), "clamped": clamped}


def apply_candidate_b(artifact: dict[str, Any], row: dict[str, Any]) -> Prediction:
    """Apply a Candidate B artifact: ridge correction, then the EB walk.

    The total correction factor (ridge x EB) is clamped to the artifact
    bounds, exactly as apply_artifact clamps the EB-only factor.
    """
    block = artifact.get("covariateModel")
    if block is None:
        return apply_artifact(artifact, row)
    features = build_features(row)
    baseline = float(row["baseline_slr"])
    bounds = artifact["bounds"]
    if features is None:
        # No portable features: fall back to the EB hierarchy alone.
        return apply_artifact(artifact, row)
    ridge = RidgeModel.from_block(block)
    ridge_log, _out = ridge.predict(features, clamp_envelope=True)
    adjusted_row = {**row, "baseline_slr": baseline * math.exp(ridge_log)}
    prediction = apply_artifact(artifact, adjusted_row)
    total_factor = math.exp(ridge_log) * prediction.correction_factor
    clamped_factor = max(float(bounds["minCorrectionFactor"]), min(float(bounds["maxCorrectionFactor"]), total_factor))
    if clamped_factor == total_factor:
        return prediction
    calibrated = max(float(bounds["minSlr"]), min(float(bounds["maxSlr"]), baseline * clamped_factor))
    ratio = calibrated / prediction.calibrated_slr if prediction.calibrated_slr else 1.0
    lower = max(float(bounds["minSlr"]), min(float(bounds["maxSlr"]), prediction.lower_slr * ratio))
    upper = max(float(bounds["minSlr"]), min(float(bounds["maxSlr"]), prediction.upper_slr * ratio))
    return Prediction(calibrated, min(lower, calibrated), max(upper, calibrated), clamped_factor, prediction.hierarchy)


def apply_candidate(artifact: dict[str, Any], row: dict[str, Any]) -> Prediction:
    if artifact.get("covariateModel"):
        return apply_candidate_b(artifact, row)
    return apply_artifact(artifact, row)


# ---------------------------------------------------------------------------
# Diagnostics only: from-scratch ridge (predicts raw observed SLR) and
# regional climatology.  Neither produces an artifact.
# ---------------------------------------------------------------------------

def fit_scratch_ridge(rows: list[dict[str, Any]]) -> RidgeModel | None:
    complete: list[dict[str, float]] = []
    targets: list[float] = []
    weights: list[float] = []
    usable: list[dict[str, Any]] = []
    for row in rows:
        features = build_features(row)
        if features is not None:
            complete.append(features)
            targets.append(float(row["observed_slr"]))
            weights.append(float(row.get("qc_weight", 1.0)))
            usable.append(row)
    if len(complete) < 20:
        return None
    lam, _ = select_lambda(usable, complete, targets)
    return RidgeModel(FEATURE_NAMES).fit(complete, targets, weights, lam)


def apply_scratch(model: RidgeModel, row: dict[str, Any]) -> float | None:
    features = build_features(row)
    if features is None:
        return None
    prediction, _ = model.predict(features, clamp_envelope=True)
    return max(3.0, min(30.0, prediction))


def fit_climatology(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    by_month: dict[int, list[float]] = {}
    all_values: list[float] = []
    for row in rows:
        value = float(row["observed_slr"])
        month = int(str(row["observation_date"])[5:7])
        by_month.setdefault(month, []).append(value)
        all_values.append(value)
    return {
        "byMonth": {month: sum(values) / len(values) for month, values in by_month.items()},
        "overall": sum(all_values) / len(all_values) if all_values else 10.0,
    }


def apply_climatology(model: dict[str, Any], row: dict[str, Any]) -> float:
    month = int(str(row["observation_date"])[5:7])
    return model["byMonth"].get(month, model["overall"])
