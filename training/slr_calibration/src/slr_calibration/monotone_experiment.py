"""Queue item 7 — monotone-constrained refit of the ENS-EB champion.

Pre-registration: data/slr-training-data/screened-v2/model-v1/
model-v3-monotone/{DESIGN,REGISTRATION}.md (incl. Amendment A1).
Pure helpers for the constrained GBM seat, the registered screen kill
clause, the empirical non-inferiority verdict, the paired physical
endpoint (elevation-ordering inversions on the frozen NA batch), and
the pre-fit direction audit.  All rule thresholds live here exactly as
registered so the driver and the tests share one definition.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# The six directional modelling priors (DESIGN.md section 2, as amended
# by A1): sign = direction the GBM prediction must not decrease in,
# ceteris paribus.  All other columns are unconstrained (0).
CONSTRAINT_SIGNS: dict[str, int] = {
    "elevation_m": +1,
    "tmax_column_pwt": -1,
    "tmax_warmnose_pwt": -1,
    "freezing_level_m_agl_pwt": -1,
    "warm_layer_depth_m_pwt": -1,
    "subcloud_warm_depth_m": -1,
}

# Estimator string bound into the prediction-cache key for the
# constrained seat (prediction_cache.MODEL_SPECS["gbm_eb_mono"]).
GBM_MONO_SPEC = (
    "HistGradientBoostingRegressor(sklearn_defaults;"
    "monotonic_cst over the 64-col v3 order:"
    "elevation_m:+1,tmax_column_pwt:-1,tmax_warmnose_pwt:-1,"
    "freezing_level_m_agl_pwt:-1,warm_layer_depth_m_pwt:-1,"
    "subcloud_warm_depth_m:-1;all other columns 0)"
)

# Registered screen kill clause (REGISTRATION.md section 2): a harm
# clause, replacing the improvement-calibrated built-in screen as the
# gate for this item.
SCREEN_KILL_LORO_GAIN_MIN = -0.030
SCREEN_KILL_REGION_GAIN_MIN = -0.05
SCREEN_KILL_SAMPLED_GAIN_MIN = -0.10

# Registered empirical non-inferiority margins (REGISTRATION.md
# section 3, as amended by A1): minimum gains (incumbent - candidate).
NI_FOUR_FAMILY_GAIN_MIN = -0.010
NI_LORO_GAIN_MIN = -0.010
NI_REGION_GAIN_MIN = -0.05
NI_TEMPORAL_GAIN_MIN = -0.020
NI_STATIONS_GAIN_MIN = -0.020
NI_RANDOM_GAIN_MIN = -0.020
NI_SLICE_MIN_EVENTS = 200
NI_SLICE_MAX_WORSENING = 0.20

# Physical endpoint (REGISTRATION.md section 4, as amended by A1).
INV_TOL = 1e-6
PHYS_MAX_VIOLATING_FRACTION = 0.10
PHYS_MAX_MAGNITUDE_FRACTION = 0.10
PHYS_MAX_WINDOW_INVERSION = 1.0

ROLES = ("base", "mid_mountain", "summit")
ROLE_SUFFIXES = ("-base", "-mid_mountain", "-summit")


def monotonic_cst_vector(columns: list[str]) -> list[int]:
    """The registered constraint vector aligned to `columns`."""
    return [CONSTRAINT_SIGNS.get(col, 0) for col in columns]


def screen_kill(screen: dict) -> tuple[bool, list[str]]:
    """Apply the registered kill clause to Judge.screen statistics."""
    clauses: list[str] = []
    if screen["pooled_loro_gain"] < SCREEN_KILL_LORO_GAIN_MIN:
        clauses.append(
            f"pooled LORO gain {screen['pooled_loro_gain']:.4f} "
            f"< {SCREEN_KILL_LORO_GAIN_MIN}"
        )
    worst_region = min(screen["loro_region_gain"].values())
    if worst_region < SCREEN_KILL_REGION_GAIN_MIN:
        clauses.append(
            f"worst region gain {worst_region:.4f} < {SCREEN_KILL_REGION_GAIN_MIN}"
        )
    for name, gain in screen["sampled_family_gain"].items():
        if gain < SCREEN_KILL_SAMPLED_GAIN_MIN:
            clauses.append(
                f"sampled {name} gain {gain:.4f} < {SCREEN_KILL_SAMPLED_GAIN_MIN}"
            )
    return bool(clauses), clauses


def noninferiority_verdict(comp: dict) -> tuple[bool, list[str]]:
    """Apply the registered non-inferiority rule to a Judge.gains result
    augmented with the judge's slice report (key "slices")."""
    fg = comp["family_gain"]
    clauses: list[str] = []
    if comp["four_family_gain"] < NI_FOUR_FAMILY_GAIN_MIN:
        clauses.append(
            f"four-family gain {comp['four_family_gain']:.4f} "
            f"< {NI_FOUR_FAMILY_GAIN_MIN}"
        )
    if fg["loro"] < NI_LORO_GAIN_MIN:
        clauses.append(f"pooled LORO gain {fg['loro']:.4f} < {NI_LORO_GAIN_MIN}")
    worst_region = min(comp["loro_region_gain"].values())
    if worst_region < NI_REGION_GAIN_MIN:
        clauses.append(f"worst region gain {worst_region:.4f} < {NI_REGION_GAIN_MIN}")
    if fg["temporal"] < NI_TEMPORAL_GAIN_MIN:
        clauses.append(f"temporal gain {fg['temporal']:.4f} < {NI_TEMPORAL_GAIN_MIN}")
    if fg["stations"] < NI_STATIONS_GAIN_MIN:
        clauses.append(f"stations gain {fg['stations']:.4f} < {NI_STATIONS_GAIN_MIN}")
    if fg["randomised"] < NI_RANDOM_GAIN_MIN:
        clauses.append(
            f"randomised gain {fg['randomised']:.4f} < {NI_RANDOM_GAIN_MIN}"
        )
    for entry in comp.get("slices", []):
        if entry["n"] >= NI_SLICE_MIN_EVENTS and entry["mae_diff"] > NI_SLICE_MAX_WORSENING:
            clauses.append(
                f"slice {entry['family']}/{entry['slice']} worsens "
                f"{entry['mae_diff']:.4f} > {NI_SLICE_MAX_WORSENING} (n={entry['n']})"
            )
    return not clauses, clauses


def window_inversions(triple: tuple[float, float, float]) -> tuple[float, float, float]:
    """(inv1, inv2, window magnitude) for an ordered (base, mid, summit)."""
    b, m, s = triple
    inv1 = max(0.0, b - m)
    inv2 = max(0.0, m - s)
    return inv1, inv2, inv1 + inv2


def inversion_stats(triples: list[tuple[float, float, float]]) -> dict:
    """Registered physical-endpoint statistics over ordered triples."""
    mags = np.array([window_inversions(t)[2] for t in triples], dtype=float)
    steps = np.array(
        [max(window_inversions(t)[:2]) if len(t) else 0.0 for t in triples],
        dtype=float,
    )
    violating = mags > INV_TOL
    ties = 0
    for b, m, s in triples:
        if abs(b - m) <= INV_TOL or abs(m - s) <= INV_TOL:
            ties += 1
    return {
        "windows": int(len(triples)),
        "violating": int(violating.sum()),
        "total_magnitude": float(mags.sum()),
        "mean_magnitude_per_violating": (
            float(mags[violating].mean()) if violating.any() else 0.0
        ),
        "max_window_magnitude": float(mags.max()) if len(mags) else 0.0,
        "max_single_step": float(steps.max()) if len(steps) else 0.0,
        "windows_with_tie": int(ties),
    }


def physical_success(champion: dict, challenger: dict) -> tuple[bool, list[str]]:
    """The three registered physical-success clauses (REGISTRATION S4)."""
    clauses: list[str] = []
    max_violating = PHYS_MAX_VIOLATING_FRACTION * champion["violating"]
    if challenger["violating"] > max_violating:
        clauses.append(
            f"violating windows {challenger['violating']} > {max_violating:.1f} "
            f"(10% of champion {champion['violating']})"
        )
    max_magnitude = PHYS_MAX_MAGNITUDE_FRACTION * champion["total_magnitude"]
    if challenger["total_magnitude"] > max_magnitude:
        clauses.append(
            f"total magnitude {challenger['total_magnitude']:.4f} > "
            f"{max_magnitude:.4f} (10% of champion)"
        )
    if challenger["max_window_magnitude"] > PHYS_MAX_WINDOW_INVERSION:
        clauses.append(
            f"max window inversion {challenger['max_window_magnitude']:.4f} "
            f"> {PHYS_MAX_WINDOW_INVERSION}"
        )
    return not clauses, clauses


def resort_key(point_id: str) -> str | None:
    """Resort key = point id minus its exact role suffix (A1 S3)."""
    for suffix in ROLE_SUFFIXES:
        if point_id.endswith(suffix):
            return point_id[: -len(suffix)]
    return None


def role_of(point_id: str) -> str | None:
    for suffix, role in zip(ROLE_SUFFIXES, ROLES):
        if point_id.endswith(suffix):
            return role
    return None


def build_triples(frame: pd.DataFrame, value_col: str) -> dict[tuple, tuple[float, float, float]]:
    """Group a row frame into {(resort, source, window): (b, m, s)}.

    `frame` needs columns point_id, source, window_end_local_date and
    `value_col`.  Raises on any window without exactly one row per role
    (the population is frozen; nothing is silently dropped).
    """
    out: dict[tuple, dict[str, float]] = {}
    for rec in frame.itertuples(index=False):
        role = role_of(rec.point_id)
        resort = resort_key(rec.point_id)
        if role is None or resort is None:
            raise ValueError(f"unparsable point_id {rec.point_id!r}")
        key = (resort, rec.source, str(rec.window_end_local_date))
        slot = out.setdefault(key, {})
        if role in slot:
            raise ValueError(f"duplicate role {role} in window {key}")
        slot[role] = float(getattr(rec, value_col))
    triples: dict[tuple, tuple[float, float, float]] = {}
    for key, slot in out.items():
        if set(slot) != set(ROLES):
            raise ValueError(f"window {key} lacks exactly one row per role: {sorted(slot)}")
        triples[key] = (slot["base"], slot["mid_mountain"], slot["summit"])
    return triples


def direction_audit(frame: pd.DataFrame) -> dict:
    """Pre-fit audit of the six claimed directions (A1): for every
    adjacent pair in every window, elevation_m must strictly increase
    and each warmth feature must not increase; NaN transitions counted.
    `frame` needs the 64 feature columns plus point_id/source/
    window_end_local_date."""
    warmth = [c for c, s in CONSTRAINT_SIGNS.items() if s == -1]
    counts: dict[str, dict[str, int]] = {
        c: {"pairs": 0, "direction_fail": 0, "nan_transition": 0}
        for c in CONSTRAINT_SIGNS
    }
    by_window: dict[tuple, dict[str, dict[str, float]]] = {}
    for rec in frame.itertuples(index=False):
        role = role_of(rec.point_id)
        resort = resort_key(rec.point_id)
        key = (resort, rec.source, str(rec.window_end_local_date))
        by_window.setdefault(key, {})[role] = {
            c: float(getattr(rec, c)) for c in CONSTRAINT_SIGNS
        }
    for key, slot in by_window.items():
        if set(slot) != set(ROLES):
            raise ValueError(f"window {key} lacks exactly one row per role")
        for lo, hi in (("base", "mid_mountain"), ("mid_mountain", "summit")):
            for col in CONSTRAINT_SIGNS:
                a, b = slot[lo][col], slot[hi][col]
                counts[col]["pairs"] += 1
                if np.isnan(a) or np.isnan(b):
                    counts[col]["nan_transition"] += 1
                    continue
                if col == "elevation_m":
                    if not b > a:
                        counts[col]["direction_fail"] += 1
                else:
                    if b > a:
                        counts[col]["direction_fail"] += 1
    return counts
