from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from slr_calibration.gold_model import (
    ClimatologyModel,
    InterceptModel,
    evaluate_predictions,
    fit_predict,
    split_loro,
    split_random,
    split_stations,
    split_temporal,
)
from slr_calibration.gold_profiles import FEATURE_COLUMNS


def _frame(n_per_region: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    regions = ["rockies", "northeast", "alaska"]
    for r_idx, region in enumerate(regions):
        for i in range(n_per_region):
            station = f"{region}-st{i % 8}"
            observed = float(8 + r_idx * 2 + rng.normal(0, 2))
            row = {
                "event_id": f"{region}:{i}",
                "region": region,
                "station_id": station,
                "site_id": station,
                "station_name": "synthetic",
                "state": "CO",
                "valid_time": "2024-01-15T07:00:00-06:00",
                "observation_date": "2024-01-15",
                "water_year": 2024 + (i % 4),
                "month": 1 + (i % 3),
                "observed_slr": max(2.5, observed),
                "fresh_snow_mm": 100.0,
                "fresh_swe_mm": 10.0,
                "climate_regime": "continental_mountain",
                "source_tier": "ghcn-coop",
                "baseline_slr": 12.0,
                "profile_status": "ok",
            }
            for j, column in enumerate(FEATURE_COLUMNS):
                row[column] = float(j) + r_idx
            rows.append(row)
    return pd.DataFrame(rows)


class SplitIntegrityTest(unittest.TestCase):
    def test_stations_no_leakage(self) -> None:
        frame = _frame()
        train, test = split_stations(frame, seed=4000)
        self.assertTrue(set(train["station_id"]).isdisjoint(set(test["station_id"])))
        self.assertEqual(len(train) + len(test), len(frame))
        self.assertTrue(0 < len(test) < len(frame))

    def test_loro_no_region_leakage(self) -> None:
        frame = _frame()
        for region in ("rockies", "northeast", "alaska"):
            train, test = split_loro(frame, region)
            self.assertEqual(set(test["region"]), {region})
            self.assertNotIn(region, set(train["region"]))
            self.assertEqual(len(train) + len(test), len(frame))

    def test_temporal(self) -> None:
        frame = _frame()
        frame.loc[frame["water_year"] == 2027, "water_year"] = 2026
        train, test = split_temporal(frame)
        self.assertLess(train["water_year"].max(), 2026)
        self.assertEqual(set(test["water_year"]), {2026})

    def test_random_is_60_40(self) -> None:
        frame = _frame(50)
        train, test = split_random(frame, seed=1)
        self.assertEqual((len(train), len(test)), (90, 60))
        self.assertTrue(set(train.index).isdisjoint(set(test.index)))


class SimpleModelsTest(unittest.TestCase):
    def test_intercept_recovers_factor_and_clamps(self) -> None:
        frame = _frame()
        frame["observed_slr"] = frame["baseline_slr"] * 0.8
        model = InterceptModel().fit(frame)
        predicted = model.predict(frame)
        self.assertTrue(np.allclose(predicted, frame["baseline_slr"] * 0.8))
        frame["observed_slr"] = frame["baseline_slr"] * 10.0
        model = InterceptModel().fit(frame)
        predicted = model.predict(frame)
        self.assertTrue(np.all(predicted <= 30.0))
        self.assertTrue(np.allclose(predicted / frame["baseline_slr"], 2.0))

    def test_climatology_fallbacks(self) -> None:
        frame = _frame()
        model = ClimatologyModel().fit(frame)
        known = model.predict(frame.iloc[:3])
        self.assertTrue(np.all(known > 0))
        unseen = frame.iloc[:1].copy()
        unseen["region"] = "great-lakes"
        unseen["month"] = 1
        value = model.predict(unseen)
        self.assertEqual(value[0], model.by_month[1])  # month fallback for unseen region


class MetricsTest(unittest.TestCase):
    def test_tails_and_snowfall(self) -> None:
        frame = _frame(10)
        frame["observed_slr"] = 12.0
        frame.loc[frame.index[:3], "observed_slr"] = 25.0
        frame.loc[frame.index[3:5], "observed_slr"] = 4.0
        frame["fresh_snow_mm"] = frame["observed_slr"] * frame["fresh_swe_mm"]
        predicted = frame["observed_slr"].to_numpy()
        result = evaluate_predictions(frame, predicted)
        self.assertEqual(result["overall"]["mae"], 0.0)
        self.assertEqual(result["tailHigh_slr_gt_20"]["n"], 3)
        self.assertEqual(result["tailLow_slr_lt_5"]["n"], 2)
        self.assertEqual(result["snowfall"]["maeMm"], 0.0)


class FitPredictSmokeTest(unittest.TestCase):
    def test_kuchera_and_rf(self) -> None:
        frame = _frame(15)
        train, test = frame.iloc[:30], frame.iloc[30:]
        self.assertTrue(np.allclose(
            fit_predict("kuchera", train, test, seed=0), test["baseline_slr"].to_numpy()))
        rf_pred = fit_predict("rf", train, test, seed=0)
        self.assertEqual(rf_pred.shape, (len(test),))
        self.assertTrue(np.all(np.isfinite(rf_pred)))


if __name__ == "__main__":
    unittest.main()
