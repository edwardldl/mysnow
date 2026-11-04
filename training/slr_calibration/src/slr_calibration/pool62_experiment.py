"""Queue item 6 — full-pool 62-feature model for the Open-Meteo configuration.

Pre-registration: data/slr-training-data/screened-v2/model-v1/
model-v3-pool62/DESIGN.md.  This module holds the experiment's pure
pieces: the tclw-pair masking applied to the incumbent at predict time
(exactly the Open-Meteo serving adapter's behaviour — the pair arrives
as NaN) and the item-6 adoption rule, which mirrors the amended
adoption rule of two_stage_judge.Judge.full with this item's stricter
pooled-LORO bar (0.03 in place of 0.02).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

# The total-column-liquid-water pair the Open-Meteo pressure-level
# archive cannot supply; dropped for CANDIDATE-62, masked for the
# incumbent.
MASK_COLUMNS: tuple[str, str] = ("tclw_pwt", "tclw_max")

# Item-6 adoption thresholds (DESIGN.md section 6).  All but the LORO
# bar mirror two_stage_judge's full-judge constants.
ADOPT_FOUR_FAMILY_GAIN_MIN = 0.03
ADOPT_LORO_GAIN_MIN = 0.03
ADOPT_REGION_DELTA_MIN = -0.05
ADOPT_RANDOM_GAIN_MIN = -0.02
SLICE_MIN_EVENTS = 200
SLICE_MAX_WORSENING = 0.20


def reduced_columns(columns: list[str]) -> list[str]:
    """The 62-feature set: `columns` minus the tclw pair, order kept."""
    reduced = [c for c in columns if c not in MASK_COLUMNS]
    if len(reduced) != len(columns) - len(MASK_COLUMNS):
        raise ValueError("column list does not contain the full tclw pair")
    return reduced


def mask_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Copy of `frame` with the tclw pair set to NaN for every row.

    This is the predict-time masking of DESIGN.md section 2: the fold
    fits are untouched; only the inputs a fitted model sees at predict
    time lose the pair, exactly as on the Open-Meteo serving arm.
    """
    masked = frame.copy()
    for column in MASK_COLUMNS:
        masked[column] = float("nan")
    return masked


def adoption_verdict(full_result: dict[str, Any]) -> dict[str, Any]:
    """Item-6 adoption rule applied to a Judge.full() result dict.

    `full_result` is the dict returned by two_stage_judge.Judge.full
    (gains + slices + the standard-rule verdict).  The clauses mirror
    Judge.full's exactly, except the pooled-LORO bar is this item's
    0.03.  Returns the clause-by-clause record; the standard-rule
    outcome is carried through for the descriptive comparison the
    pre-registration asks for.
    """
    family_gain = full_result["family_gain"]
    region_gain = full_result["loro_region_gain"]
    clauses: list[dict[str, Any]] = []

    def record(name: str, value: float, threshold: float, passed: bool, text: str) -> None:
        clauses.append(
            {
                "clause": name,
                "value": float(value),
                "threshold": float(threshold),
                "passed": bool(passed),
                "detail": text,
            }
        )

    value = full_result["four_family_gain"]
    record(
        "four_family_gain", value, ADOPT_FOUR_FAMILY_GAIN_MIN,
        value >= ADOPT_FOUR_FAMILY_GAIN_MIN,
        f"four-family gain {value:.4f} >= {ADOPT_FOUR_FAMILY_GAIN_MIN}",
    )
    value = family_gain["loro"]
    record(
        "pooled_loro_gain", value, ADOPT_LORO_GAIN_MIN,
        value >= ADOPT_LORO_GAIN_MIN,
        f"pooled LORO gain {value:.4f} >= {ADOPT_LORO_GAIN_MIN}",
    )
    worst_region = min(region_gain.values())
    record(
        "worst_region_gain", worst_region, ADOPT_REGION_DELTA_MIN,
        worst_region >= ADOPT_REGION_DELTA_MIN,
        f"worst region gain {worst_region:.4f} >= {ADOPT_REGION_DELTA_MIN}",
    )
    for family in ("temporal", "stations"):
        value = family_gain[family]
        record(
            f"{family}_gain", value, 0.0, value >= 0.0,
            f"{family} gain {value:.4f} >= 0",
        )
    value = family_gain["randomised"]
    record(
        "randomised_gain", value, ADOPT_RANDOM_GAIN_MIN,
        value >= ADOPT_RANDOM_GAIN_MIN,
        f"randomised gain {value:.4f} >= {ADOPT_RANDOM_GAIN_MIN}",
    )
    slice_failures = [
        entry
        for entry in full_result["slices"]
        if entry["n"] >= SLICE_MIN_EVENTS and entry["mae_diff"] > SLICE_MAX_WORSENING
    ]
    record(
        "slice_guards", float(len(slice_failures)), 0.0, not slice_failures,
        "no temporal/stations slice with n>=200 worsens by > 0.20"
        if not slice_failures
        else "; ".join(
            f"slice {e['family']}/{e['slice']} worsens {e['mae_diff']:.4f} "
            f"> {SLICE_MAX_WORSENING} (n={e['n']})"
            for e in slice_failures
        ),
    )
    return {
        "adopted": all(c["passed"] for c in clauses),
        "clauses": clauses,
        "failed_clauses": [c["detail"] for c in clauses if not c["passed"]],
        "standard_rule_adopted": bool(full_result["adopted"]),
        "standard_rule_failed_clauses": list(full_result["failed_clauses"]),
    }
