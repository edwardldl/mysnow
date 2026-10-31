"""Pooled bias-correction layer for the v3 ENS-EB champion.

Implements the pre-registered bias-correction round of 2026-10-31
(design: ``bias-correction-round-design.md`` in the snowfall goal's
files): an additive offset layer applied AFTER the full ENS-EB pipeline
(``gold_model`` RFEBModel/GBMEBModel, whose own EB residual layers are
left exactly as they are), with two pooled, region-agnostic stratifier
families -- season (from the event month) and altitude band (from
station elevation) -- plus an optional monotone (isotonic) calibration
map on the corrected prediction.

Conventions
-----------
* Residuals in this module are ALWAYS ``observed - predicted`` (the
  amount to ADD to a prediction).  A stratum whose report bias
  (predicted - observed) is -0.54 has mean residual +0.54 and receives
  a positive offset.
* Offsets are shrunk cell means: ``offset = mean_residual * n / (n + k)``
  with ``k = 200`` (ratified).  The two families are fitted by
  backfitting -- season pass, altitude pass on season-corrected
  residuals, then one repeat -- where each pass recomputes that
  family's offsets from the original residuals minus the OTHER
  family's current offsets, so the families do not double-count shared
  structure and each family's final offsets remain shrunk cell means
  of the other-family-corrected residuals.
* Every corrected prediction is clipped to the model's output
  contract ``[MIN_SLR, MAX_SLR]`` = [3, 30], the same clamp the EB
  layer applies.
* No region identity is used anywhere in fitting or applying: the
  layer is portable to stations outside the six training regions by
  construction (user direction 2026-10-31: region bias is not
  corrected; regions appear only in no-harm guardrail measurement,
  which lives in the round's summariser, not in this layer).

The shipped correction is a plain JSON dict::

    {"version": 1, "k": 200.0,
     "seasonOffsets": {"autumn": ..., "winter": ..., "spring": ...},
     "altitudeOffsets": {"lt500": ..., "500_1000": ..., ...},
     "isotonic": {"xThresholds": [...], "yThresholds": [...]} | None}

so a package sidecar can carry it verbatim and
:class:`CorrectedChampionModel` can reproduce the correction from the
package alone.  The isotonic map is applied with ``np.interp`` over
the stored thresholds (input clipped to the threshold range), which
is bit-identical to ``sklearn.isotonic.IsotonicRegression.predict``
with ``out_of_bounds="clip"`` (verified against sklearn 1.9.1; the
unit tests pin the equivalence).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .gold_model import MAX_SLR, MIN_SLR

SHRINKAGE_K = 200.0

SEASON_AUTUMN = "autumn"  # Oct-Nov
SEASON_WINTER = "winter"  # Dec-Feb
SEASON_SPRING = "spring"  # Mar-May
SEASONS: tuple[str, ...] = (SEASON_AUTUMN, SEASON_WINTER, SEASON_SPRING)

# Altitude band keys, in elevation order.  Boundaries are the report's:
# <500, 500-1000, 1000-1500, 1500-2000, >=2000 m.
ALTITUDE_BANDS: tuple[str, ...] = (
    "lt500",
    "500_1000",
    "1000_1500",
    "1500_2000",
    "gte2000",
)
ALTITUDE_BAND_LABELS: dict[str, str] = {
    "lt500": "<500 m",
    "500_1000": "500-1000 m",
    "1000_1500": "1000-1500 m",
    "1500_2000": "1500-2000 m",
    "gte2000": ">=2000 m",
}

# Predicted-SLR bands for the calibration-drift record (design SS6):
# bias is measured within bands of the PREDICTED value.
PRED_BANDS: tuple[tuple[float, float], ...] = (
    (5.0, 10.0),
    (10.0, 15.0),
    (15.0, 20.0),
    (20.0, 30.0),
)

CORRECTION_FORMAT_VERSION = 1


# ---------------------------------------------------------------------------
# Stratifiers
# ---------------------------------------------------------------------------


def season_of_month(month: Any) -> str | None:
    """Season key for a calendar month; None for Jun-Sep (no offset)."""
    m = int(month)
    if m in (10, 11):
        return SEASON_AUTUMN
    if m in (12, 1, 2):
        return SEASON_WINTER
    if m in (3, 4, 5):
        return SEASON_SPRING
    return None


def altitude_band(elevation_m: Any) -> str:
    """Altitude band key for a station elevation in metres."""
    e = float(elevation_m)
    if e < 500.0:
        return "lt500"
    if e < 1000.0:
        return "500_1000"
    if e < 1500.0:
        return "1000_1500"
    if e < 2000.0:
        return "1500_2000"
    return "gte2000"


def season_keys(months: Any) -> list[str | None]:
    return [season_of_month(m) for m in np.asarray(months)]


def altitude_keys(elevations: Any) -> list[str]:
    return [altitude_band(e) for e in np.asarray(elevations, dtype=float)]


# ---------------------------------------------------------------------------
# Offset fitting (shrunk pooled cell means, backfitted)
# ---------------------------------------------------------------------------


def _shrunk_cell_means(
    residuals: np.ndarray, keys: list[str | None], k: float
) -> dict[str, float]:
    """{cell: mean(residuals in cell) * n / (n + k)}; None keys excluded."""
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for key, residual in zip(keys, residuals):
        if key is None:
            continue
        totals[key] = totals.get(key, 0.0) + float(residual)
        counts[key] = counts.get(key, 0) + 1
    return {
        key: (totals[key] / counts[key]) * counts[key] / (counts[key] + k)
        for key in totals
    }


def _keys_offset_vector(keys: list[str | None], offsets: dict[str, float]) -> np.ndarray:
    return np.array(
        [offsets.get(key, 0.0) if key is not None else 0.0 for key in keys],
        dtype=float,
    )


def fit_offsets(
    residuals: Any,
    months: Any,
    elevations: Any,
    *,
    k: float = SHRINKAGE_K,
    families: tuple[str, ...] = ("season", "altitude"),
    cycles: int = 2,
) -> tuple[dict[str, float], dict[str, float]]:
    """Fit the (season, altitude) offset dicts on OOF residuals.

    ``residuals`` are ``observed - oof_predicted``.  Backfitting: each
    pass recomputes a family's offsets as shrunk cell means of the
    original residuals minus the other family's current offsets;
    ``cycles=2`` is the design's "S pass, A pass, then one repeat".
    Families not requested stay all-zero.  Every declared cell key is
    present in the returned dicts (0.0 when a family is off or a cell
    has no support).
    """
    residuals = np.asarray(residuals, dtype=float)
    s_keys = season_keys(months)
    a_keys = altitude_keys(elevations)
    season_offsets = {season: 0.0 for season in SEASONS}
    altitude_offsets = {band: 0.0 for band in ALTITUDE_BANDS}
    for _ in range(cycles):
        if "season" in families:
            adjusted = residuals - _keys_offset_vector(a_keys, altitude_offsets)
            fitted = _shrunk_cell_means(adjusted, s_keys, k)
            season_offsets = {season: fitted.get(season, 0.0) for season in SEASONS}
        if "altitude" in families:
            adjusted = residuals - _keys_offset_vector(s_keys, season_offsets)
            fitted = _shrunk_cell_means(adjusted, a_keys, k)
            altitude_offsets = {band: fitted.get(band, 0.0) for band in ALTITUDE_BANDS}
    return season_offsets, altitude_offsets


# ---------------------------------------------------------------------------
# Isotonic calibration
# ---------------------------------------------------------------------------


def fit_isotonic(predicted: Any, observed: Any) -> dict[str, Any]:
    """Fit sklearn IsotonicRegression (out-of-bounds clip); return the
    JSON-serialisable threshold table."""
    from sklearn.isotonic import IsotonicRegression

    model = IsotonicRegression(out_of_bounds="clip")
    model.fit(
        np.asarray(predicted, dtype=float), np.asarray(observed, dtype=float)
    )
    return {
        "xThresholds": [float(v) for v in model.X_thresholds_],
        "yThresholds": [float(v) for v in model.y_thresholds_],
    }


def apply_isotonic(isotonic: dict[str, Any], predicted: Any) -> np.ndarray:
    """Apply a stored isotonic table.  Bit-identical to
    ``IsotonicRegression.predict`` with ``out_of_bounds="clip"``:
    sklearn's predict is linear interpolation over the thresholds
    with the input clipped to the threshold range (unit-tested)."""
    x = np.asarray(isotonic["xThresholds"], dtype=float)
    y = np.asarray(isotonic["yThresholds"], dtype=float)
    values = np.clip(np.asarray(predicted, dtype=float), x[0], x[-1])
    return np.interp(values, x, y)


# ---------------------------------------------------------------------------
# Correction dict: build and apply (single code path everywhere)
# ---------------------------------------------------------------------------


def fit_correction(
    oof_predicted: Any,
    observed: Any,
    months: Any,
    elevations: Any,
    *,
    families: tuple[str, ...] = ("season", "altitude"),
    include_isotonic: bool = False,
    k: float = SHRINKAGE_K,
) -> dict[str, Any]:
    """Fit a correction dict on out-of-fold ENS-EB predictions.

    The isotonic stage (when requested) is fitted on the
    offset-corrected, clipped OOF predictions -- at serving time it is
    likewise applied after the offsets, so fit and apply see the same
    quantity.  With ``families=()`` and ``include_isotonic=True`` this
    fits the calibration-only arm.
    """
    oof_predicted = np.asarray(oof_predicted, dtype=float)
    observed = np.asarray(observed, dtype=float)
    season_offsets, altitude_offsets = fit_offsets(
        observed - oof_predicted, months, elevations, k=k, families=families
    )
    correction: dict[str, Any] = {
        "version": CORRECTION_FORMAT_VERSION,
        "k": float(k),
        "seasonOffsets": season_offsets,
        "altitudeOffsets": altitude_offsets,
        "isotonic": None,
    }
    if include_isotonic:
        corrected_oof = correct_predictions(
            oof_predicted, months, elevations, correction
        )
        correction["isotonic"] = fit_isotonic(corrected_oof, observed)
    return correction


def offset_vector(months: Any, elevations: Any, correction: dict[str, Any]) -> np.ndarray:
    """Total additive offset per event from a correction dict."""
    total = _keys_offset_vector(
        season_keys(months), correction.get("seasonOffsets") or {}
    )
    total = total + _keys_offset_vector(
        altitude_keys(elevations), correction.get("altitudeOffsets") or {}
    )
    return total


def clip_predictions(predicted: Any) -> np.ndarray:
    """The model's output contract: SLR clipped to [MIN_SLR, MAX_SLR]."""
    return np.clip(np.asarray(predicted, dtype=float), MIN_SLR, MAX_SLR)


def correct_predictions(
    predicted: Any,
    months: Any,
    elevations: Any,
    correction: dict[str, Any],
) -> np.ndarray:
    """Apply a correction dict: offsets, clip, isotonic (if any), clip."""
    out = clip_predictions(
        np.asarray(predicted, dtype=float)
        + offset_vector(months, elevations, correction)
    )
    isotonic = correction.get("isotonic")
    if isotonic:
        out = clip_predictions(apply_isotonic(isotonic, out))
    return out


# ---------------------------------------------------------------------------
# Stratified bias records (declared cells, predicted bands)
# ---------------------------------------------------------------------------


def declared_cells(
    observed: Any, predicted: Any, months: Any, elevations: Any
) -> list[dict[str, Any]]:
    """Bias (predicted - observed) and n for the 8 declared pooled
    cells: 3 season cells + 5 altitude cells, in declared order."""
    observed = np.asarray(observed, dtype=float)
    errors = np.asarray(predicted, dtype=float) - observed
    s_keys = season_keys(months)
    a_keys = altitude_keys(elevations)
    cells: list[dict[str, Any]] = []
    for family, keys, order in (
        ("season", s_keys, SEASONS),
        ("altitude", a_keys, ALTITUDE_BANDS),
    ):
        for cell in order:
            mask = np.array([key == cell for key in keys])
            n = int(mask.sum())
            cells.append(
                {
                    "family": family,
                    "cell": cell,
                    "n": n,
                    "bias": round(float(errors[mask].mean()), 4) if n else None,
                }
            )
    return cells


def primary_bias_metric(cells: list[dict[str, Any]]) -> float | None:
    """The design's primary bias metric: n-weighted mean |cell bias|
    over the declared cells (cells with no support are excluded)."""
    weighted = [(c["n"], abs(c["bias"])) for c in cells if c["n"] and c["bias"] is not None]
    total_n = sum(n for n, _ in weighted)
    if not total_n:
        return None
    return float(sum(n * b for n, b in weighted) / total_n)


def pool_cells(cell_tables: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Pool per-partition declared-cell tables exactly (n-weighted
    bias), e.g. the six LORO regions into one pooled table."""
    pooled: dict[tuple[str, str], dict[str, Any]] = {}
    for table in cell_tables:
        for cell in table:
            key = (cell["family"], cell["cell"])
            entry = pooled.setdefault(
                key, {"family": cell["family"], "cell": cell["cell"], "n": 0, "_sum": 0.0}
            )
            if cell["n"] and cell["bias"] is not None:
                entry["n"] += cell["n"]
                entry["_sum"] += cell["n"] * cell["bias"]
    out: list[dict[str, Any]] = []
    for family, order in (("season", SEASONS), ("altitude", ALTITUDE_BANDS)):
        for cell in order:
            entry = pooled.get((family, cell))
            if entry is None:
                out.append({"family": family, "cell": cell, "n": 0, "bias": None})
            else:
                out.append(
                    {
                        "family": family,
                        "cell": cell,
                        "n": entry["n"],
                        "bias": round(entry["_sum"] / entry["n"], 4) if entry["n"] else None,
                    }
                )
    return out


def predicted_band_bias(observed: Any, predicted: Any) -> list[dict[str, Any]]:
    """Bias within bands of the PREDICTED value (the servable view):
    bands 5-10, 10-15, 15-20, 20-30 (the last is closed at 30, the
    model's output ceiling)."""
    observed = np.asarray(observed, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    errors = predicted - observed
    out: list[dict[str, Any]] = []
    for lo, hi in PRED_BANDS:
        if hi >= MAX_SLR:
            mask = (predicted >= lo) & (predicted <= hi)
        else:
            mask = (predicted >= lo) & (predicted < hi)
        n = int(mask.sum())
        out.append(
            {
                "band": f"{int(lo)}-{int(hi)}",
                "lo": lo,
                "hi": hi,
                "n": n,
                "bias": round(float(errors[mask].mean()), 4) if n else None,
            }
        )
    return out


def pool_band_bias(band_tables: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Pool per-partition predicted-band tables exactly (n-weighted)."""
    pooled: dict[str, dict[str, Any]] = {}
    for table in band_tables:
        for band in table:
            entry = pooled.setdefault(
                band["band"],
                {"band": band["band"], "lo": band["lo"], "hi": band["hi"], "n": 0, "_sum": 0.0},
            )
            if band["n"] and band["bias"] is not None:
                entry["n"] += band["n"]
                entry["_sum"] += band["n"] * band["bias"]
    out = []
    for entry in pooled.values():
        out.append(
            {
                "band": entry["band"],
                "lo": entry["lo"],
                "hi": entry["hi"],
                "n": entry["n"],
                "bias": round(entry["_sum"] / entry["n"], 4) if entry["n"] else None,
            }
        )
    return out


def max_abs_band_bias(bands: list[dict[str, Any]]) -> float | None:
    values = [abs(b["bias"]) for b in bands if b["n"] and b["bias"] is not None]
    return float(max(values)) if values else None


# ---------------------------------------------------------------------------
# Adoption rule (design SS6, applied mechanically)
# ---------------------------------------------------------------------------

_TOL = 1e-9


def _clause(clause_id: str, description: str, value: Any, passed: bool) -> dict[str, Any]:
    return {"id": clause_id, "description": description, "value": value, "passed": bool(passed)}


def _arm_clauses(summary: dict[str, Any], arm: str) -> list[dict[str, Any]]:
    """The SS6 guardrail clauses for one arm vs A0 (shared by A3/A5)."""
    clauses: list[dict[str, Any]] = []
    primary = summary["primaryBias"]
    base_temporal = primary["temporal"]["A0"]
    value_temporal = primary["temporal"][arm]
    reduction = (
        (base_temporal - value_temporal) / base_temporal if base_temporal else None
    )
    clauses.append(
        _clause(
            "bias_reduction_temporal_ge_25pct",
            "Primary |bias| falls by >= 25% vs A0 on the temporal split",
            {"a0": base_temporal, arm.lower(): value_temporal, "reductionFrac": reduction},
            reduction is not None and reduction >= 0.25 - _TOL,
        )
    )
    for split in ("loro", "randomised"):
        base = primary[split]["A0"]
        value = primary[split][arm]
        clauses.append(
            _clause(
                f"bias_no_rise_{split}",
                f"Primary |bias| does not rise vs A0 on the {split} split",
                {"a0": base, arm.lower(): value, "delta": value - base},
                value <= base + _TOL,
            )
        )
    for split, limit in (
        ("randomised", 0.02),
        ("temporal", 0.02),
        ("stations", 0.02),
    ):
        delta = summary["mae"][split][arm] - summary["mae"][split]["A0"]
        clauses.append(
            _clause(
                f"mae_regression_{split}_le_{limit}",
                f"Headline MAE regression vs A0 <= {limit} on the {split} split",
                {"a0": summary["mae"][split]["A0"], arm.lower(): summary["mae"][split][arm], "delta": delta},
                delta <= limit + _TOL,
            )
        )
    delta_loro = summary["loroRegionMeanMae"][arm] - summary["loroRegionMeanMae"]["A0"]
    clauses.append(
        _clause(
            "mae_regression_loro_region_mean_le_0.05",
            "Headline MAE regression vs A0 <= 0.05 on the LORO region-mean",
            {
                "a0": summary["loroRegionMeanMae"]["A0"],
                arm.lower(): summary["loroRegionMeanMae"][arm],
                "delta": delta_loro,
            },
            delta_loro <= 0.05 + _TOL,
        )
    )
    for split in ("temporal", "loro", "randomised"):
        worst_region, worst_delta = None, None
        for region, biases in summary["regionBias"][split].items():
            delta = abs(biases[arm]) - abs(biases["A0"])
            if worst_delta is None or delta > worst_delta:
                worst_region, worst_delta = region, delta
        clauses.append(
            _clause(
                f"region_guardrail_{split}_le_0.15",
                f"No region's |bias| worsens by > 0.15 vs A0 on the {split} split (measurement only)",
                {"worstRegion": worst_region, "worstDelta": worst_delta},
                worst_delta is not None and worst_delta <= 0.15 + _TOL,
            )
        )
    for split in ("temporal", "loro", "randomised", "stations"):
        base = abs(summary["regionBias"][split]["alaska"]["A0"])
        value = abs(summary["regionBias"][split]["alaska"][arm])
        clauses.append(
            _clause(
                f"alaska_no_worsen_{split}",
                f"Alaska's |bias| does not worsen vs A0 on the {split} split",
                {"a0Abs": base, arm.lower() + "Abs": value, "delta": value - base},
                value <= base + _TOL,
            )
        )
    for tail, label in (("tailHigh", "SLR>20"), ("tailLow", "SLR<5")):
        delta = (
            summary["tailsRandomisedRep0"][tail][arm]
            - summary["tailsRandomisedRep0"][tail]["A0"]
        )
        clauses.append(
            _clause(
                f"tail_regression_{tail}_rep0_le_0.10",
                f"Tail MAE ({label}, randomised rep 0 as in judging) regresses by <= 0.10 vs A0",
                {
                    "a0": summary["tailsRandomisedRep0"][tail]["A0"],
                    arm.lower(): summary["tailsRandomisedRep0"][tail][arm],
                    "delta": delta,
                },
                delta <= 0.10 + _TOL,
            )
        )
    return clauses


def evaluate_adoption(summary: dict[str, Any]) -> dict[str, Any]:
    """Apply the design SS6 adoption rule mechanically.

    ``summary`` is the round summariser's aggregate dict (see
    model-v3-correction/summarize_correction.py for its exact shape).
    A3 is judged on every clause; A5 is judged only when A3 passes,
    on the same clauses plus its predicted-band criterion, and is
    otherwise recorded as not evaluated (never promoted past a failed
    A3).
    """
    a3_clauses = _arm_clauses(summary, "A3")
    a3_adopted = all(c["passed"] for c in a3_clauses)
    result: dict[str, Any] = {
        "a3": {"adopted": a3_adopted, "clauses": a3_clauses},
        "a5": {"evaluated": False, "adopted": False, "clauses": []},
    }
    if a3_adopted:
        a5_clauses = _arm_clauses(summary, "A5")
        base_band = summary["predBandMaxAbsBiasTemporal"]["A0"]
        value_band = summary["predBandMaxAbsBiasTemporal"]["A5"]
        reduction = (
            (base_band - value_band) / base_band if base_band else None
        )
        a5_clauses.append(
            _clause(
                "pred_band_max_abs_bias_temporal_fall_ge_40pct",
                "Max |bias| across predicted-SLR bands 5-30 falls by >= 40% vs A0 on the temporal split",
                {"a0": base_band, "a5": value_band, "reductionFrac": reduction},
                reduction is not None and reduction >= 0.40 - _TOL,
            )
        )
        result["a5"] = {
            "evaluated": True,
            "adopted": all(c["passed"] for c in a5_clauses),
            "clauses": a5_clauses,
        }
    result["verdict"] = "adopt" if a3_adopted else "not_adopt"
    result["shippedArm"] = (
        "A5" if result["a5"]["adopted"] else ("A3" if a3_adopted else None)
    )
    return result


# ---------------------------------------------------------------------------
# Packaged corrected champion
# ---------------------------------------------------------------------------


class CorrectedChampionModel:
    """``ChampionModel`` plus the sidecar's pooled bias-correction layer.

    Loads a corrected champion package (the v3 champion's contents plus
    a ``correction`` block in ``champion_sidecar.json``) and reproduces
    ``base ENS-EB prediction -> correction`` from the package alone.
    Input frames follow ``ChampionModel``'s contract and must
    additionally carry ``month`` (at serving time, the forecast's valid
    month); ``elevation_m`` is one of the 64 features.  The plain
    ``predict-champion`` CLI does NOT apply this layer.
    """

    def __init__(self, base_model: Any, correction: dict[str, Any]) -> None:
        self.base = base_model
        self.correction = correction
        self.champion_dir = base_model.champion_dir
        self.sidecar = base_model.sidecar
        self.features = base_model.features
        self.regions = base_model.regions

    @classmethod
    def load(cls, champion_dir: Path) -> "CorrectedChampionModel":
        from .champion import ChampionModel

        base = ChampionModel.load(Path(champion_dir))
        correction = base.sidecar.get("correction")
        if not correction:
            raise ValueError(
                f"{champion_dir} has no 'correction' block in its sidecar; "
                "it is an uncorrected champion package"
            )
        return cls(base, correction)

    def predict_base(self, frame: pd.DataFrame) -> np.ndarray:
        """The uncorrected ENS-EB prediction (ChampionModel path)."""
        return self.base.predict(frame)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        """Base ENS-EB prediction with the correction layer applied."""
        if "month" not in frame.columns:
            raise ValueError(
                "corrected prediction requires a 'month' column "
                "(the forecast's valid month at serving time)"
            )
        base = self.base.predict(frame)
        return correct_predictions(
            base,
            frame["month"].to_numpy(),
            frame["elevation_m"].to_numpy(dtype=float),
            self.correction,
        )
