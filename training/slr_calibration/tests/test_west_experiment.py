from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from slr_calibration.gold_model import GBMEBModel, RFEBModel
from slr_calibration.gold_profiles import FEATURE_COLUMNS
from slr_calibration.west_experiment import (
    EAST_REGIONS,
    EAST_WEIGHT,
    WEST_REGIONS,
    arm_training,
    judge_west_arm,
)


def _frame(n_per_region: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    rows = []
    regions = list(WEST_REGIONS) + list(EAST_REGIONS)
    for r_idx, region in enumerate(regions):
        for i in range(n_per_region):
            station = f"{region}-st{i % 8}"
            observed = float(10 + r_idx * 2 + rng.normal(0, 1.5))
            row = {
                "event_id": f"{region}:{i}",
                "region": region,
                "station_id": station,
                "site_id": station,
                "station_name": "synthetic",
                "state": "CO",
                "valid_time": "2024-01-15T07:00:00-06:00",
                "observation_date": "2024-01-15",
                "water_year": 2024,
                "month": 1,
                "observed_slr": max(3.5, observed),
                "fresh_snow_mm": 100.0,
                "fresh_swe_mm": 10.0,
                "climate_regime": "continental_mountain",
                "source_tier": "ghcn-coop",
                "baseline_slr": 11.0,
                "profile_status": "ok",
            }
            for j, column in enumerate(FEATURE_COLUMNS):
                row[column] = float(j) + r_idx + (i % 5) * 0.1
            rows.append(row)
    return pd.DataFrame(rows)


class ArmTrainingTest(unittest.TestCase):
    def test_pooled_returns_full_frame_no_weights(self) -> None:
        frame = _frame()
        train, weights = arm_training(frame, "pooled")
        self.assertEqual(len(train), len(frame))
        self.assertIsNone(weights)

    def test_west_only_filters_to_western_regions(self) -> None:
        frame = _frame(n_per_region=10)
        train, weights = arm_training(frame, "west_only")
        self.assertEqual(set(train["region"].unique()), set(WEST_REGIONS))
        self.assertEqual(len(train), 10 * len(WEST_REGIONS))
        self.assertIsNone(weights)

    def test_weighted_weights_align_with_rows(self) -> None:
        frame = _frame(n_per_region=10)
        train, weights = arm_training(frame, "weighted")
        self.assertEqual(len(train), len(frame))
        self.assertIsNotNone(weights)
        assert weights is not None
        self.assertEqual(len(weights), len(frame))
        east_mask = frame["region"].isin(list(EAST_REGIONS)).to_numpy()
        self.assertTrue(np.all(weights[east_mask] == EAST_WEIGHT))
        self.assertTrue(np.all(weights[~east_mask] == 1.0))

    def test_unknown_arm_raises(self) -> None:
        with self.assertRaises(ValueError):
            arm_training(_frame(n_per_region=2), "east_only")


def _block(loro_mean: float, region_maes: dict[str, float], randomised: float) -> dict:
    return {
        "loroMean": loro_mean,
        "loroByRegion": region_maes,
        "randomised": randomised,
    }


_BASE_REGIONS = {region: 3.5 for region in WEST_REGIONS}


class JudgeWestArmTest(unittest.TestCase):
    def test_clear_gain_helps(self) -> None:
        control = _block(3.55, dict(_BASE_REGIONS), 3.00)
        candidate = _block(3.45, {r: 3.45 for r in WEST_REGIONS}, 2.98)
        verdict = judge_west_arm(control, candidate)
        self.assertTrue(verdict["helps"])
        self.assertAlmostEqual(verdict["loroMeanDelta"], 0.10)

    def test_gain_below_threshold_does_not_help(self) -> None:
        control = _block(3.55, dict(_BASE_REGIONS), 3.00)
        candidate = _block(3.53, {r: 3.48 for r in WEST_REGIONS}, 2.99)
        self.assertFalse(judge_west_arm(control, candidate)["helps"])

    def test_gain_exactly_at_threshold_helps(self) -> None:
        control = _block(3.55, dict(_BASE_REGIONS), 3.00)
        candidate = _block(3.52, {r: 3.49 for r in WEST_REGIONS}, 3.00)
        self.assertTrue(judge_west_arm(control, candidate)["helps"])

    def test_region_regression_beyond_limit_blocks(self) -> None:
        control = _block(3.55, dict(_BASE_REGIONS), 3.00)
        regions = {r: 3.30 for r in WEST_REGIONS}
        regions["alaska"] = 3.65  # delta -0.15 < -0.10
        candidate = _block(3.38, regions, 2.95)
        verdict = judge_west_arm(control, candidate)
        self.assertFalse(verdict["helps"])
        self.assertAlmostEqual(verdict["worstRegionDelta"], -0.15)

    def test_region_regression_exactly_at_limit_passes(self) -> None:
        control = _block(3.55, dict(_BASE_REGIONS), 3.00)
        regions = {r: 3.30 for r in WEST_REGIONS}
        regions["alaska"] = 3.60  # delta exactly -0.10
        candidate = _block(3.38, regions, 2.95)
        self.assertTrue(judge_west_arm(control, candidate)["helps"])

    def test_randomised_regression_beyond_limit_blocks(self) -> None:
        control = _block(3.55, dict(_BASE_REGIONS), 3.00)
        candidate = _block(3.45, {r: 3.45 for r in WEST_REGIONS}, 3.06)
        self.assertFalse(judge_west_arm(control, candidate)["helps"])


class WeightedFitSmokeTest(unittest.TestCase):
    """RFEBModel / GBMEBModel accept sample weights and predict finitely."""

    def test_weighted_fits_predict(self) -> None:
        frame = _frame(n_per_region=40)
        train, weights = arm_training(frame, "weighted")
        test = frame.iloc[:12].copy()
        for model in (RFEBModel(3), GBMEBModel(3)):
            fitted = model.fit(train, sample_weight=weights)
            _, eb_pred = fitted.predict_parts(test)
            self.assertEqual(len(eb_pred), len(test))
            self.assertTrue(np.all(np.isfinite(eb_pred)))

    def test_unweighted_fit_still_works(self) -> None:
        frame = _frame(n_per_region=40)
        fitted = GBMEBModel(3).fit(frame)
        _, eb_pred = fitted.predict_parts(frame.iloc[:6].copy())
        self.assertTrue(np.all(np.isfinite(eb_pred)))


if __name__ == "__main__":
    unittest.main()
