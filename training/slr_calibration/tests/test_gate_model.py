from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from slr_calibration.gate_model import (
    BLEND_WEIGHTS,
    GATE_PHYSICAL_FEATURES,
    GateApplicator,
    KucheraGate,
    advantage_target,
    choose_leaf_weight,
    gate_inputs,
)


def _frame(n: int, **overrides) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    data = {name: rng.normal(0.0, 1.0, n) for name in GATE_PHYSICAL_FEATURES}
    data.update(overrides)
    return pd.DataFrame(data)


def _synthetic_gate_data(n_per: int = 600):
    """Warm-nose events favour Kuchera; cold events favour ENS-EB."""
    rng = np.random.default_rng(11)
    warm = np.repeat([1.0, -1.0], n_per)
    frame = _frame(2 * n_per, tmax_warmnose_pwt=warm + rng.normal(0, 0.05, 2 * n_per))
    obs = np.full(2 * n_per, 10.0)
    ens = np.where(warm > 0, 14.0, 10.5) + rng.normal(0, 0.05, 2 * n_per)
    kuchera = np.where(warm > 0, 10.5, 14.0) + rng.normal(0, 0.05, 2 * n_per)
    return frame, ens, kuchera, obs


class GateInputTests(unittest.TestCase):
    def test_columns_and_ratio_guard(self) -> None:
        frame = _frame(3)
        ens = np.array([10.0, 20.0, 5.0])
        kuchera = np.array([8.0, 0.0, 5.0])
        X = gate_inputs(frame, ens, kuchera)
        self.assertEqual(X.shape, (3, 10))
        self.assertTrue(np.allclose(X[:, 8], [2.0, 20.0, 0.0]))
        # Ratio denominator floored at 1.0.
        self.assertTrue(np.allclose(X[:, 9], [1.25, 20.0, 1.0]))

    def test_advantage_target_sign(self) -> None:
        obs = np.array([10.0, 10.0])
        ens = np.array([9.0, 12.0])
        kuchera = np.array([12.0, 9.0])
        adv = advantage_target(obs, ens, kuchera)
        self.assertLess(adv[0], 0)  # ens better on event 0
        self.assertGreater(adv[1], 0)  # kuchera better on event 1


class LeafWeightTests(unittest.TestCase):
    def test_extremes_and_ties(self) -> None:
        obs = np.array([10.0, 12.0, 8.0])
        ens = obs.copy()
        kuchera = obs + 3.0
        w, table = choose_leaf_weight(ens, kuchera, obs)
        self.assertEqual(w, 0.0)
        self.assertEqual(set(table), {repr(v) for v in BLEND_WEIGHTS})
        w2, _ = choose_leaf_weight(kuchera, ens, obs)  # second predictor perfect
        self.assertEqual(w2, 1.0)
        # Identical predictors: every weight ties -> smallest w wins.
        w3, _ = choose_leaf_weight(ens, ens, obs)
        self.assertEqual(w3, 0.0)


class GateFitTests(unittest.TestCase):
    def test_learns_feature_dependent_switch(self) -> None:
        frame, ens, kuchera, obs = _synthetic_gate_data()
        gate = KucheraGate().fit(frame, ens, kuchera, obs, seed=3)
        pred, w = gate.predict(frame, ens, kuchera)
        warm_mask = frame["tmax_warmnose_pwt"].to_numpy() > 0
        self.assertGreater(np.mean(w[warm_mask]), 0.5)
        self.assertLess(np.mean(w[~warm_mask]), 0.25)
        mae = lambda p: float(np.mean(np.abs(p - obs)))  # noqa: E731
        self.assertLess(mae(pred), mae(ens))
        self.assertLess(mae(pred), mae(kuchera))
        self.assertTrue(set(gate.leaf_weights_.values()) <= set(BLEND_WEIGHTS))

    def test_export_roundtrip_reproduces_predictions(self) -> None:
        frame, ens, kuchera, obs = _synthetic_gate_data(300)
        gate = KucheraGate().fit(frame, ens, kuchera, obs, seed=5)
        pred, w = gate.predict(frame, ens, kuchera)
        applicator = GateApplicator(gate.to_dict())
        pred2, w2 = applicator.predict(frame, ens, kuchera)
        self.assertTrue(np.array_equal(w, w2))
        self.assertEqual(float(np.max(np.abs(pred - pred2))), 0.0)

    def test_unfitted_raises(self) -> None:
        gate = KucheraGate()
        frame, ens, kuchera, _obs = _synthetic_gate_data(10)
        with self.assertRaises(RuntimeError):
            gate.predict(frame, ens, kuchera)


if __name__ == "__main__":
    unittest.main()
