"""Queue item 5: feature-based Kuchera gate (gated ENS-EB / Kuchera blend).

Pre-registration: data/.../model-v3-gate/DESIGN.md.  A depth-2 tree
partitions events by atmospheric state and model disagreement; each
leaf carries a blend weight from a small fixed set, chosen on
gate-training (inner out-of-fold) events to minimise absolute error.
The gate uses no region, station, or geographic input of any kind.

    pred = (1 - w) * ens_eb + w * kuchera
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeRegressor

# The eight physical gate inputs, all members of the frozen 64-feature
# set (DESIGN section 2): warm layer, surface wet-bulb, wind, DGZ class.
GATE_PHYSICAL_FEATURES: tuple[str, ...] = (
    "tmax_warmnose_pwt",
    "warm_layer_depth_m_pwt",
    "sfc_wetbulb_c_pwt",
    "freezing_level_m_agl_pwt",
    "ws10m_pwt",
    "dgz_t_pwt",
    "dgz_precip_frac",
    "habit_frac_dendrite",
)
GATE_INPUT_NAMES: tuple[str, ...] = GATE_PHYSICAL_FEATURES + (
    "disagree_abs",
    "disagree_ratio",
)
BLEND_WEIGHTS: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
TREE_MAX_DEPTH = 2
TREE_MIN_SAMPLES_LEAF = 200
# Feature-defined stratum used by the descriptive behaviour analysis.
LOW_PREDICTED_SLR = 10.0


def gate_inputs(
    frame: pd.DataFrame, ens: np.ndarray, kuchera: np.ndarray
) -> np.ndarray:
    """Assemble the (n, 10) gate-input matrix for a frame.

    `ens` is the applicable ENS-EB prediction (inner OOF when fitting,
    cached fold prediction when applying); `kuchera` is the baseline
    SLR for the same events.  Disagreement columns follow DESIGN
    section 2 exactly (ratio denominator floored at 1.0).
    """
    ens = np.asarray(ens, dtype=float)
    kuchera = np.asarray(kuchera, dtype=float)
    physical = frame[list(GATE_PHYSICAL_FEATURES)].to_numpy(dtype=float)
    disagree_abs = np.abs(ens - kuchera)
    disagree_ratio = ens / np.maximum(kuchera, 1.0)
    return np.column_stack([physical, disagree_abs, disagree_ratio])


def advantage_target(
    observed: np.ndarray, ens: np.ndarray, kuchera: np.ndarray
) -> np.ndarray:
    """|obs - ens| - |obs - kuchera|; positive means Kuchera was better."""
    observed = np.asarray(observed, dtype=float)
    return np.abs(observed - np.asarray(ens, dtype=float)) - np.abs(
        observed - np.asarray(kuchera, dtype=float)
    )


def choose_leaf_weight(
    ens: np.ndarray, kuchera: np.ndarray, observed: np.ndarray
) -> tuple[float, dict[str, float]]:
    """Blend weight in BLEND_WEIGHTS minimising MAE; ties -> smaller w.

    Returns (weight, {weight: mae}) with the full table for reporting.
    """
    ens = np.asarray(ens, dtype=float)
    kuchera = np.asarray(kuchera, dtype=float)
    observed = np.asarray(observed, dtype=float)
    table: dict[str, float] = {}
    best_w, best_mae = 0.0, np.inf
    for w in BLEND_WEIGHTS:
        mae = float(np.mean(np.abs((1.0 - w) * ens + w * kuchera - observed)))
        table[repr(w)] = mae
        if mae < best_mae:  # strict: ascending order keeps the smaller w on ties
            best_w, best_mae = w, mae
    return best_w, table


class KucheraGate:
    """A fitted depth-2 partition with per-leaf blend weights."""

    def __init__(self) -> None:
        self.tree_: DecisionTreeRegressor | None = None
        self.leaf_weights_: dict[int, float] = {}
        self.leaf_tables_: dict[int, dict[str, float]] = {}
        self.leaf_sizes_: dict[int, int] = {}
        self.global_weight_: float = 0.0
        self.global_table_: dict[str, float] = {}

    # -- fitting ---------------------------------------------------------
    def fit(
        self,
        frame: pd.DataFrame,
        ens_oof: np.ndarray,
        kuchera: np.ndarray,
        observed: np.ndarray,
        *,
        seed: int,
    ) -> "KucheraGate":
        X = gate_inputs(frame, ens_oof, kuchera)
        target = advantage_target(observed, ens_oof, kuchera)
        self.tree_ = DecisionTreeRegressor(
            max_depth=TREE_MAX_DEPTH,
            min_samples_leaf=TREE_MIN_SAMPLES_LEAF,
            random_state=seed,
        ).fit(X, target)
        leaf = self.tree_.apply(X)
        self.leaf_weights_ = {}
        self.leaf_tables_ = {}
        self.leaf_sizes_ = {}
        for node in np.unique(leaf):
            mask = leaf == node
            w, table = choose_leaf_weight(
                np.asarray(ens_oof)[mask],
                np.asarray(kuchera)[mask],
                np.asarray(observed)[mask],
            )
            self.leaf_weights_[int(node)] = w
            self.leaf_tables_[int(node)] = table
            self.leaf_sizes_[int(node)] = int(mask.sum())
        self.global_weight_, self.global_table_ = choose_leaf_weight(
            ens_oof, kuchera, observed
        )
        return self

    # -- application -------------------------------------------------------
    def weights_for(
        self, frame: pd.DataFrame, ens: np.ndarray, kuchera: np.ndarray
    ) -> np.ndarray:
        if self.tree_ is None:
            raise RuntimeError("gate is not fitted")
        X = gate_inputs(frame, ens, kuchera)
        leaf = self.tree_.apply(X)
        return np.array(
            [self.leaf_weights_.get(int(node), self.global_weight_) for node in leaf],
            dtype=float,
        )

    def predict(
        self, frame: pd.DataFrame, ens: np.ndarray, kuchera: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """(gated prediction, per-event weight) for a frame."""
        ens = np.asarray(ens, dtype=float)
        kuchera = np.asarray(kuchera, dtype=float)
        w = self.weights_for(frame, ens, kuchera)
        return (1.0 - w) * ens + w * kuchera, w

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """JSON-able export: tree structure + leaf weights (DESIGN 3)."""
        if self.tree_ is None:
            raise RuntimeError("gate is not fitted")
        tree = self.tree_.tree_
        return {
            "input_names": list(GATE_INPUT_NAMES),
            "blend_weights": list(BLEND_WEIGHTS),
            "tree": {
                "children_left": [int(v) for v in tree.children_left],
                "children_right": [int(v) for v in tree.children_right],
                "feature": [int(v) for v in tree.feature],
                "threshold": [float(v) for v in tree.threshold],
            },
            "leaf_weights": {str(k): v for k, v in self.leaf_weights_.items()},
            "leaf_sizes": {str(k): v for k, v in self.leaf_sizes_.items()},
            "leaf_mae_tables": {
                str(k): v for k, v in self.leaf_tables_.items()
            },
            "global_weight": self.global_weight_,
            "global_mae_table": self.global_table_,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GateApplicator":
        return GateApplicator(payload)


class GateApplicator:
    """Re-applies an exported gate (to_dict payload) without sklearn.

    Walks the exported node arrays directly, so judged predictions can
    be reproduced from the JSON export alone (DESIGN section 5).
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        tree = payload["tree"]
        self.children_left = tree["children_left"]
        self.children_right = tree["children_right"]
        self.feature = tree["feature"]
        self.threshold = tree["threshold"]
        self.leaf_weights = {int(k): v for k, v in payload["leaf_weights"].items()}
        self.global_weight = float(payload["global_weight"])

    def _leaf_of(self, row: np.ndarray) -> int:
        node = 0
        while self.children_left[node] != -1:
            if row[self.feature[node]] <= self.threshold[node]:
                node = self.children_left[node]
            else:
                node = self.children_right[node]
        return node

    def weights_for(
        self, frame: pd.DataFrame, ens: np.ndarray, kuchera: np.ndarray
    ) -> np.ndarray:
        X = gate_inputs(frame, ens, kuchera)
        return np.array(
            [self.leaf_weights.get(self._leaf_of(row), self.global_weight) for row in X],
            dtype=float,
        )

    def predict(
        self, frame: pd.DataFrame, ens: np.ndarray, kuchera: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        ens = np.asarray(ens, dtype=float)
        kuchera = np.asarray(kuchera, dtype=float)
        w = self.weights_for(frame, ens, kuchera)
        return (1.0 - w) * ens + w * kuchera, w
