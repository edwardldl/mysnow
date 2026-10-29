from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from slr_calibration.gold_model import (
    GBMEBModel,
    MedianEBGBMModel,
    RFEBModel,
    _median_eb_corrections,
    headline_metrics,
    judge_candidate,
    tail_shrink_predictions,
)
from slr_calibration.gold_profiles import FEATURE_COLUMNS


def _frame(n_per_region: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    regions = ["rockies", "northeast", "alaska"]
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


class MedianEbCorrectionsTest(unittest.TestCase):
    def test_shrinkage_form(self) -> None:
        residuals = np.array([0.4] * 10 + [0.0] * 10)
        regions = np.array(["a"] * 10 + ["b"] * 10)
        global_log, by_region = _median_eb_corrections(residuals, regions)
        # Global: median 0.2 shrunk by 20 / (20 + 30) = 0.4 -> 0.08.
        self.assertAlmostEqual(global_log, 0.08)
        # Region a: deviation median 0.32 shrunk by 10 / 40 = 0.25 -> 0.08 + 0.08.
        self.assertAlmostEqual(by_region["a"], 0.16)
        # Region b: deviation median -0.08 shrunk by 0.25 -> 0.08 - 0.02.
        self.assertAlmostEqual(by_region["b"], 0.06)

    def test_empty(self) -> None:
        global_log, by_region = _median_eb_corrections(np.array([]), np.array([]))
        self.assertEqual(global_log, 0.0)
        self.assertEqual(by_region, {})


class TailShrinkTest(unittest.TestCase):
    def test_full_correction_at_and_below_15(self) -> None:
        rf = np.array([10.0, 15.0])
        eb = np.array([12.0, 18.0])
        out = tail_shrink_predictions(rf, eb)
        np.testing.assert_allclose(out, eb)

    def test_fades_with_magnitude(self) -> None:
        # rf 20 -> weight 15/20 = 0.75; factor 1.2 -> 20 * (1 + 0.2 * 0.75) = 23.
        out = tail_shrink_predictions(np.array([20.0]), np.array([24.0]))
        np.testing.assert_allclose(out, [23.0])
        # Downward corrections shrink too: factor 0.8 -> 20 * (1 - 0.2 * 0.75) = 17.
        out = tail_shrink_predictions(np.array([20.0]), np.array([16.0]))
        np.testing.assert_allclose(out, [17.0])

    def test_output_stays_in_bounds(self) -> None:
        out = tail_shrink_predictions(np.array([30.0]), np.array([30.0]))
        np.testing.assert_allclose(out, [30.0])


class JudgeCandidateTest(unittest.TestCase):
    def _metrics(self, randomised=3.0, loro=3.5, temporal=3.2, stations=3.1, tail=8.5):
        return {
            "randomised": randomised, "loroMean": loro, "temporal": temporal,
            "stations": stations, "tailHigh": tail, "tailLow": 3.5,
        }

    def test_adopt(self) -> None:
        verdict = judge_candidate(self._metrics(randomised=2.95), self._metrics())
        self.assertEqual(verdict["verdict"], "adopt")

    def test_boundary_values_adopt(self) -> None:
        candidate = self._metrics(randomised=2.97, temporal=3.25)  # gain .03, regression .05
        verdict = judge_candidate(candidate, self._metrics())
        self.assertEqual(verdict["verdict"], "adopt")

    def test_no_material_gain(self) -> None:
        verdict = judge_candidate(self._metrics(randomised=2.99, loro=3.49), self._metrics())
        self.assertEqual(verdict["verdict"], "no material gain")

    def test_harmful_split_regression(self) -> None:
        candidate = self._metrics(randomised=2.90, temporal=3.26)
        self.assertEqual(judge_candidate(candidate, self._metrics())["verdict"], "harmful")

    def test_harmful_tail_regression(self) -> None:
        candidate = self._metrics(randomised=2.90, tail=8.71)
        self.assertEqual(judge_candidate(candidate, self._metrics())["verdict"], "harmful")


class HeadlineMetricsTest(unittest.TestCase):
    def test_loro_mean_is_unweighted_region_mean(self) -> None:
        def split(mae: float) -> dict:
            return {"models": {"m": {"overall": {"mae": mae}}}}

        results = {
            "splits": {
                "random60_40": {"aggregated": {"m": {
                    "maeMean": 2.5, "maeStd": 0.1,
                    "rep0": {"tailHigh_slr_gt_20": {"mae": 8.0, "n": 10},
                             "tailLow_slr_lt_5": {"mae": 3.0, "n": 5}},
                }}},
                "leaveOneRegionOut": {"r1": split(3.0), "r2": split(4.0)},
                "temporalWY2026": split(3.2),
                "unseenStations": split(3.1),
            }
        }
        h = headline_metrics(results, "m")
        self.assertEqual(h["randomised"], 2.5)
        self.assertEqual(h["loroMean"], 3.5)
        self.assertEqual(h["temporal"], 3.2)
        self.assertEqual(h["stations"], 3.1)
        self.assertEqual(h["tailHigh"], 8.0)
        self.assertEqual(h["tailLowN"], 5)


class VariantModelsSmokeTest(unittest.TestCase):
    def test_gbm_eb_predict(self) -> None:
        frame = _frame()
        model = GBMEBModel(seed=3).fit(frame)
        predicted = model.predict(frame)
        self.assertEqual(len(predicted), len(frame))
        self.assertTrue(np.isfinite(predicted).all())
        base, calibrated = model.predict_parts(frame)
        self.assertTrue(((base >= 3.0) & (base <= 30.0)).all())
        np.testing.assert_allclose(calibrated, predicted)

    def test_median_eb_predict(self) -> None:
        frame = _frame()
        model = MedianEBGBMModel(seed=3).fit(frame)
        predicted = model.predict(frame)
        self.assertEqual(len(predicted), len(frame))
        self.assertTrue(np.isfinite(predicted).all())
        self.assertTrue(((predicted >= 3.0) & (predicted <= 30.0)).all())
        self.assertIn("rockies", model.by_region)

    def test_rf_eb_predict_parts_consistency(self) -> None:
        frame = _frame(n_per_region=24)
        model = RFEBModel(seed=3).fit(frame)
        base, calibrated = model.predict_parts(frame)
        np.testing.assert_allclose(calibrated, model.predict(frame))
        self.assertTrue(((base >= 3.0) & (base <= 30.0)).all())


if __name__ == "__main__":
    unittest.main()
