"""HRRR correction study: registered adapter candidates C1-C3 and judging.

Implements the frozen protocol of model-v3-hrrr-correction/REGISTRATION.md
(round-14 certified): a strongly regularised log-scale adapter on the
complete ENS-EB prediction, fitted against observed SLR on HRRR inputs
under nested whole-pipeline validation.

Candidates (all act on the complete ENS-EB prediction yhat):
  yhat_c = clamp(exp(log yhat + Delta), 3, 30)
  C1: Delta = alpha + beta * (log yhat - log 10)
  C2: C1 + gamma * log(1 + L / 5mm), L = HRRR-input fcst_liquid_mm
  C3: C2 (stage A) + per-region shifts delta_r (stage B:
      estimate -> shrink ESS_r/(ESS_r + 30) -> clip [-0.3, 0.3];
      ESS_r < 100 or region absent -> delta_r = 0)

Shared parameters minimise J(theta) = mean original-scale MAE
+ lam * sum(theta^2) (parameters in frozen natural units, gamma
penalised) via scipy Nelder-Mead under the options frozen in
REGISTRATION.md section R7(a), scipy 1.18.1 pinned.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import scipy
from scipy.optimize import minimize

SCIPY_PINNED = "1.18.1"
LAMBDAS: tuple[float, ...] = (3.0, 10.0, 30.0, 100.0)
SLR_MIN, SLR_MAX = 3.0, 30.0
SHARED_BOXES: dict[str, list[tuple[float, float]]] = {
    "C1": [(-0.35, 0.35), (-0.5, 0.5)],
    "C2": [(-0.35, 0.35), (-0.5, 0.5), (-0.5, 0.5)],
    "C3": [(-0.35, 0.35), (-0.5, 0.5), (-0.5, 0.5)],
}
CANDIDATES: tuple[str, ...] = ("C1", "C2", "C3")
DELTA_BOX = (-0.6, 0.6)
DELTA_CLIP = 0.3
ESS_MIN = 100
SHRINK_CONSTANT = 30.0
SOLVER_OPTIONS: dict[str, Any] = {
    "xatol": 1e-8,
    "fatol": 1e-8,
    "maxiter": 2000,
    "maxfev": 2000,
    "adaptive": False,
}
BOOTSTRAP_B = 2000
BOOTSTRAP_SEED_STATION = 20261105
BOOTSTRAP_SEED_DATE = 20261106
MIN_VALID_REPLICATES = 1000


class AdapterFitFailed(Exception):
    """A registered adapter optimisation failed (REGISTRATION.md R7(a))."""


def verify_scipy_version() -> str:
    """Environment gate: abort unless the imported scipy is the pinned one."""
    version = scipy.__version__
    if version != SCIPY_PINNED:
        raise RuntimeError(
            f"scipy version gate failed: imported {version}, "
            f"registration pins {SCIPY_PINNED}; aborting before any fit"
        )
    return version


def apply_delta(yhat: np.ndarray, delta: np.ndarray | float) -> np.ndarray:
    """yhat_c = clamp(exp(log yhat + delta), 3, 30)."""
    return np.clip(np.exp(np.log(yhat) + delta), SLR_MIN, SLR_MAX)


def _design(candidate: str, yhat: np.ndarray, liquid: np.ndarray) -> list[np.ndarray]:
    """Non-intercept regressors of the shared stage, in parameter order."""
    u = np.log(yhat) - np.log(10.0)
    if candidate == "C1":
        return [u]
    g = np.log1p(np.asarray(liquid, dtype=float) / 5.0)
    return [u, g]


def shared_objective(
    theta: np.ndarray,
    candidate: str,
    yhat: np.ndarray,
    y: np.ndarray,
    liquid: np.ndarray,
    lam: float,
) -> float:
    """J(theta) = mean original-scale MAE + lam * sum(theta^2)."""
    cols = _design(candidate, yhat, liquid)
    delta = float(theta[0]) + sum(float(theta[k + 1]) * cols[k] for k in range(len(cols)))
    corrected = apply_delta(yhat, delta)
    return float(np.mean(np.abs(corrected - y)) + lam * float(np.sum(theta**2)))


def _solve(objective, x0: np.ndarray, bounds: list[tuple[float, float]]) -> np.ndarray:
    result = minimize(
        objective,
        x0,
        method="Nelder-Mead",
        bounds=bounds,
        options=dict(SOLVER_OPTIONS),
    )
    if not result.success or not np.isfinite(result.fun):
        raise AdapterFitFailed(
            f"Nelder-Mead did not converge (success={result.success}, "
            f"fun={result.fun!r}, message={result.message!r})"
        )
    return np.asarray(result.x, dtype=float)


def fit_shared(
    candidate: str,
    yhat: np.ndarray,
    y: np.ndarray,
    liquid: np.ndarray,
    lam: float,
) -> tuple[np.ndarray, float]:
    """Fit the shared stage (C1 / C2 / C3 stage A). Returns (theta, J)."""
    boxes = SHARED_BOXES[candidate]
    theta = _solve(
        lambda th: shared_objective(th, candidate, yhat, y, liquid, lam),
        np.zeros(len(boxes)),
        boxes,
    )
    return theta, shared_objective(theta, candidate, yhat, y, liquid, lam)


def shared_delta(
    candidate: str, theta: np.ndarray, yhat: np.ndarray, liquid: np.ndarray
) -> np.ndarray:
    cols = _design(candidate, yhat, liquid)
    return float(theta[0]) + sum(
        float(theta[k + 1]) * cols[k] for k in range(len(cols))
    )


def fit_stage_b_delta(yhat_stage_a: np.ndarray, y: np.ndarray) -> float:
    """C3 stage B estimate for one region: unpenalised 1-D fit of delta.

    Minimises the mean original-scale MAE of the stage-A prediction
    shifted by delta over the region's rows, delta in [-0.6, 0.6],
    starting from 0 (REGISTRATION.md R2 / R6(iii), solver per R7(a)).
    """
    def objective(delta: np.ndarray) -> float:
        corrected = apply_delta(yhat_stage_a, float(delta[0]))
        return float(np.mean(np.abs(corrected - y)))

    x = _solve(objective, np.zeros(1), [DELTA_BOX])
    return float(x[0])


@dataclass
class Adapter:
    candidate: str
    lam: float
    theta: np.ndarray
    deltas: dict[str, float] = field(default_factory=dict)

    def predict(
        self, yhat: np.ndarray, liquid: np.ndarray, regions: np.ndarray
    ) -> np.ndarray:
        delta = shared_delta(self.candidate, self.theta, yhat, liquid)
        if self.candidate == "C3":
            shift = np.array(
                [self.deltas.get(str(r), 0.0) for r in regions], dtype=float
            )
            delta = delta + shift
        return apply_delta(yhat, delta)


def fit_adapter(
    candidate: str,
    yhat: np.ndarray,
    y: np.ndarray,
    liquid: np.ndarray,
    regions: np.ndarray,
    lam: float,
) -> tuple[Adapter, float]:
    """Fit one candidate at one lambda on one adapter row set.

    For C3 the two stages together are one adapter fit (R6(i)).
    Raises AdapterFitFailed on any solver failure (R7(a)).
    Returns (adapter, shared-stage objective J at the fitted theta).
    """
    yhat = np.asarray(yhat, dtype=float)
    y = np.asarray(y, dtype=float)
    liquid = np.asarray(liquid, dtype=float)
    regions = np.asarray(regions).astype(str)
    theta, objective = fit_shared(candidate, yhat, y, liquid, lam)
    deltas: dict[str, float] = {}
    if candidate == "C3":
        stage_a = apply_delta(yhat, shared_delta(candidate, theta, yhat, liquid))
        for region in sorted(set(regions.tolist())):
            mask = regions == region
            ess = int(mask.sum())
            if ess < ESS_MIN:
                deltas[region] = 0.0
                continue
            delta_hat = fit_stage_b_delta(stage_a[mask], y[mask])
            shrunk = ess / (ess + SHRINK_CONSTANT) * delta_hat
            deltas[region] = float(np.clip(shrunk, -DELTA_CLIP, DELTA_CLIP))
    return Adapter(candidate, float(lam), theta, deltas), objective


def select_lambda(scores: dict[float, float | None]) -> float | None:
    """Registered lambda selection (R6(iv)).

    scores maps lambda -> pooled validation MAE, or None when that
    lambda is ineligible (any failed tuning fit).  Ties within 1e-6
    of the minimum break to the largest lambda.  None when no lambda
    is eligible (the candidate/outer instance is then fit-failed).
    """
    eligible = {lam: s for lam, s in scores.items() if s is not None}
    if not eligible:
        return None
    best = min(eligible.values())
    tied = [lam for lam, s in eligible.items() if s <= best + 1e-6]
    return float(max(tied))


# ---------------------------------------------------------------------------
# Judging machinery (REGISTRATION.md R3 / R4 / R6(v))
# ---------------------------------------------------------------------------

def slr_band(yhat_c0: np.ndarray) -> np.ndarray:
    """Predicted-SLR bands from the appearance's C0 prediction.

    Labels '<8', '8-14', '>14' read literally: 8 and 14 fall in the
    middle band (the outer labels exclude them)."""
    out = np.full(len(yhat_c0), "8_14", dtype=object)
    out[yhat_c0 < 8.0] = "lt8"
    out[yhat_c0 > 14.0] = "gt14"
    return out


def liquid_band(liquid: np.ndarray) -> np.ndarray:
    """Liquid bands '<3', '3-10', '>10' mm, read literally."""
    out = np.full(len(liquid), "3_10", dtype=object)
    out[liquid < 3.0] = "lt3"
    out[liquid > 10.0] = "gt10"
    return out


def elevation_band(elevation_m: np.ndarray) -> np.ndarray:
    """Elevation bands per the round-12 closure: <1000,
    1000 <= e < 1500, >=1500."""
    out = np.full(len(elevation_m), "1000_1500", dtype=object)
    out[elevation_m < 1000.0] = "lt1000"
    out[elevation_m >= 1500.0] = "ge1500"
    return out


def gain_from_errors(err0: np.ndarray, errc: np.ndarray, w: np.ndarray) -> float:
    """Weighted gain MAE_C0 - MAE_C; NaN when total weight is 0."""
    den = float(np.sum(w))
    if den <= 0.0:
        return float("nan")
    return float(np.sum(w * err0) / den - np.sum(w * errc) / den)


def bootstrap_gains(
    err0: np.ndarray,
    errc: np.ndarray,
    cluster_idx: np.ndarray,
    n_clusters: int,
    stat_masks: dict[str, np.ndarray],
    *,
    seed: int,
    b: int = BOOTSTRAP_B,
) -> dict[str, np.ndarray]:
    """One clustering's bootstrap replicate gains for named row subsets.

    Per R6(v): each replicate draws cluster identities with
    replacement (rng.integers(0, K, size=K), sequentially from one
    PCG64 generator); a row's weight is its cluster's draw
    multiplicity; gains are recomputed from the weighted errors.
    Returns {stat: array of per-replicate gains, NaN where the
    replicate's resampled row set has total weight 0}.
    """
    rng = np.random.default_rng(seed)
    out: dict[str, list[float]] = {key: [] for key in stat_masks}
    masks = {key: np.asarray(mask, dtype=bool) for key, mask in stat_masks.items()}
    sub_idx = {key: np.flatnonzero(mask) for key, mask in masks.items()}
    for _ in range(b):
        draw = rng.integers(0, n_clusters, size=n_clusters)
        mult = np.bincount(draw, minlength=n_clusters).astype(float)
        w_all = mult[cluster_idx]
        for key, idx in sub_idx.items():
            w = w_all[idx]
            out[key].append(gain_from_errors(err0[idx], errc[idx], w))
    return {key: np.array(vals, dtype=float) for key, vals in out.items()}


def percentile_interval(replicates: np.ndarray) -> tuple[float, float, int] | None:
    """95% percentile interval over valid replicates (R6(v)).

    Returns (lo, hi, n_valid), or None when the interval is
    undefined (< 1000 valid replicates)."""
    valid = replicates[np.isfinite(replicates)]
    if len(valid) < MIN_VALID_REPLICATES:
        return None
    lo, hi = np.percentile(valid, [2.5, 97.5], method="linear")
    return float(lo), float(hi), int(len(valid))


def clause_status(
    estimate: float,
    *,
    fail_below: float,
    straddle: tuple[float, float],
    intervals: list[tuple[float, float, int] | None],
    support_ok: bool,
) -> str:
    """One clause's status under R3/R6(v): 'passed' | 'failed' | 'inconclusive'.

    A clause is inconclusive when its support minima are unmet, when
    either clustering's interval is undefined, or when either
    interval contains both straddle endpoints (the clause's bar and
    0, in the clause-specific form frozen in R3/R6(v)).  Otherwise
    it fails when the point estimate is below fail_below, else it
    passes.  The caller applies candidate-level precedence:
    inconclusive outranks failed (R3).
    """
    if not support_ok:
        return "inconclusive"
    lo_s, hi_s = min(straddle), max(straddle)
    for interval in intervals:
        if interval is None:
            return "inconclusive"
        lo, hi, _ = interval
        if lo <= lo_s and hi >= hi_s:
            return "inconclusive"
    return "failed" if estimate < fail_below else "passed"
