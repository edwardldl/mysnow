from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from slr_calibration.pool62_experiment import (
    MASK_COLUMNS,
    adoption_verdict,
    mask_frame,
    reduced_columns,
)
from slr_calibration.prediction_cache import MODEL_SPECS, PredictionCache


class ReducedColumnsTests(unittest.TestCase):
    def test_drops_exactly_the_pair_order_preserved(self) -> None:
        columns = ["a", "tclw_pwt", "b", "tclw_max", "c"]
        self.assertEqual(reduced_columns(columns), ["a", "b", "c"])

    def test_missing_pair_raises(self) -> None:
        with self.assertRaises(ValueError):
            reduced_columns(["a", "tclw_pwt", "b"])


class MaskFrameTests(unittest.TestCase):
    def test_masks_pair_only_and_does_not_mutate(self) -> None:
        frame = pd.DataFrame(
            {
                "tclw_pwt": [1.0, 2.0],
                "tclw_max": [3.0, 4.0],
                "other": [5.0, 6.0],
            }
        )
        masked = mask_frame(frame)
        self.assertTrue(masked["tclw_pwt"].isna().all())
        self.assertTrue(masked["tclw_max"].isna().all())
        self.assertEqual(masked["other"].tolist(), [5.0, 6.0])
        self.assertEqual(frame["tclw_pwt"].tolist(), [1.0, 2.0])


def _full_result(**overrides) -> dict:
    result = {
        "four_family_gain": 0.05,
        "family_gain": {
            "randomised": 0.04,
            "loro": 0.05,
            "temporal": 0.01,
            "stations": 0.02,
        },
        "loro_region_gain": {
            "alaska": 0.05,
            "great-lakes": 0.04,
            "northeast": 0.06,
            "pacific-northwest": 0.05,
            "rockies": 0.05,
            "sierra-nevada": 0.03,
        },
        "slices": [
            {"family": "temporal", "slice": "slr_lt_5", "n": 500, "mae_diff": 0.05},
            {"family": "stations", "slice": "slr_gt_20", "n": 300, "mae_diff": -0.02},
        ],
        "adopted": True,
        "failed_clauses": [],
    }
    for key, value in overrides.items():
        if key in ("family_gain", "loro_region_gain"):
            result[key].update(value)
        else:
            result[key] = value
    return result


class AdoptionVerdictTests(unittest.TestCase):
    def test_all_clauses_pass(self) -> None:
        verdict = adoption_verdict(_full_result())
        self.assertTrue(verdict["adopted"])
        self.assertEqual(verdict["failed_clauses"], [])

    def test_loro_bar_is_003_not_002(self) -> None:
        # 0.025 clears the general rule's 0.02 bar but not item 6's 0.03.
        verdict = adoption_verdict(_full_result(family_gain={"loro": 0.025}))
        self.assertFalse(verdict["adopted"])
        self.assertTrue(any("LORO" in c for c in verdict["failed_clauses"]))
        self.assertTrue(verdict["standard_rule_adopted"])

    def test_region_guard(self) -> None:
        verdict = adoption_verdict(
            _full_result(loro_region_gain={"alaska": -0.06})
        )
        self.assertFalse(verdict["adopted"])

    def test_temporal_and_stations_must_not_worsen(self) -> None:
        self.assertFalse(
            adoption_verdict(_full_result(family_gain={"temporal": -0.001}))["adopted"]
        )
        self.assertFalse(
            adoption_verdict(_full_result(family_gain={"stations": -0.001}))["adopted"]
        )

    def test_randomised_floor(self) -> None:
        self.assertFalse(
            adoption_verdict(_full_result(family_gain={"randomised": -0.021}))["adopted"]
        )
        self.assertTrue(
            adoption_verdict(_full_result(family_gain={"randomised": -0.02}))["adopted"]
        )

    def test_slice_guard_needs_200_events_and_strictly_over_020(self) -> None:
        big = [{"family": "temporal", "slice": "slr_lt_5", "n": 200, "mae_diff": 0.21}]
        self.assertFalse(adoption_verdict(_full_result(slices=big))["adopted"])
        small_n = [{"family": "temporal", "slice": "slr_lt_5", "n": 199, "mae_diff": 0.30}]
        self.assertTrue(adoption_verdict(_full_result(slices=small_n))["adopted"])
        at_bar = [{"family": "temporal", "slice": "slr_lt_5", "n": 200, "mae_diff": 0.20}]
        self.assertTrue(adoption_verdict(_full_result(slices=at_bar))["adopted"])


class MaskedSpecTests(unittest.TestCase):
    def test_specs_registered(self) -> None:
        self.assertEqual(MODEL_SPECS["ens_eb_masked62"]["kind"], "derived")
        self.assertEqual(
            MODEL_SPECS["ens_eb_masked62"]["derived_from"],
            ("rf_eb_masked62", "gbm_eb_masked62"),
        )
        for name in ("rf_eb_masked62", "gbm_eb_masked62"):
            self.assertEqual(MODEL_SPECS[name]["kind"], "fitted")
            self.assertIn("mask", MODEL_SPECS[name]["estimator"])

    def test_masked_keys_never_collide_with_unmasked(self) -> None:
        cache = PredictionCache(
            Path("/tmp/pool62-test-cache"),
            cohort="c", features="f", manifests="m", libraries={},
        )
        self.assertNotEqual(
            cache.key_for("rf_eb", "loro_rockies", 2000),
            cache.key_for("rf_eb_masked62", "loro_rockies", 2000),
        )
        self.assertNotEqual(
            cache.key_for("gbm_eb", "loro_rockies", 2000),
            cache.key_for("gbm_eb_masked62", "loro_rockies", 2000),
        )

    def test_mask_columns_are_the_pair(self) -> None:
        self.assertEqual(MASK_COLUMNS, ("tclw_pwt", "tclw_max"))


if __name__ == "__main__":
    unittest.main()
