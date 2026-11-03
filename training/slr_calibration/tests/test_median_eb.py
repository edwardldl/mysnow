from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from slr_calibration.io import read_rows
from slr_calibration.median_eb import train_artifact_median, weighted_median
from slr_calibration.median_model import AbsoluteErrorGBMModel
from slr_calibration.prediction_cache import MODEL_SPECS, PredictionCache
from slr_calibration.trainer import TrainingConfig, train_artifact

FIXTURE = Path(__file__).parent / "fixtures" / "smoke-events.csv"


def _config() -> TrainingConfig:
    return TrainingConfig(
        artifact_id="median-eb-test",
        artifact_version="test-v1",
        prior_effective_sample_size=4,
        min_effective_sample_size=2,
        source_label="synthetic test fixture; not meteorological evidence",
    )


class WeightedMedianTests(unittest.TestCase):
    def test_unit_weights_reduce_to_numpy_median(self) -> None:
        rng = np.random.default_rng(11)
        for n in (1, 2, 3, 4, 5, 10, 11, 57):
            values = rng.normal(size=n)
            weights = np.ones(n)
            self.assertAlmostEqual(
                weighted_median(values, weights), float(np.median(values)), places=12
            )

    def test_weighted_case_hand_computed(self) -> None:
        # Sorted values 1,2,3 with weights 1,1,2: cumulative 1,2,4;
        # half of total (4) is 2, hit exactly at value 2 -> average
        # with the next value (3) by the stated convention.
        self.assertEqual(weighted_median(np.array([3.0, 1.0, 2.0]), np.array([2.0, 1.0, 1.0])), 2.5)
        # Weights 3,1,1 on values 10,20,30: cumulative 3,4,5; half 2.5
        # is crossed at value 10.
        self.assertEqual(weighted_median(np.array([10.0, 20.0, 30.0]), np.array([3.0, 1.0, 1.0])), 10.0)

    def test_empty_and_zero_weight(self) -> None:
        self.assertEqual(weighted_median(np.array([]), np.array([])), 0.0)
        self.assertEqual(weighted_median(np.array([5.0]), np.array([0.0])), 0.0)


class MedianTrainerTests(unittest.TestCase):
    def test_schema_matches_mean_trainer(self) -> None:
        rows = read_rows(FIXTURE)
        median_artifact = train_artifact_median(rows, _config())
        mean_artifact = train_artifact(rows, _config())
        self.assertEqual(set(median_artifact), set(mean_artifact))
        self.assertEqual(median_artifact["modelKind"], "hierarchical-log-residual-median-eb")
        self.assertEqual(mean_artifact["modelKind"], "hierarchical-log-residual-eb")
        method = median_artifact["methods"]["kuchera"]
        self.assertTrue(method["climate"])
        self.assertTrue(method["region"])
        self.assertTrue(method["local"])
        for node in method["climate"].values():
            self.assertEqual(
                set(node),
                {"logBias", "rawLogBias", "residualStdLog", "rawSampleCount",
                 "effectiveSampleSize", "shrinkageWeight"},
            )

    def test_deterministic_under_row_reversal(self) -> None:
        rows = read_rows(FIXTURE)
        first = train_artifact_median(rows, _config())
        second = train_artifact_median(list(reversed(rows)), _config())
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_constant_residuals_match_mean_trainer_exactly(self) -> None:
        # Every residual identical (observed = 1.5 x baseline) and one
        # climate regime: within every hierarchy node the raw values
        # are constant along each record's chain, so median and mean
        # node statistics coincide and the methods must be identical.
        rows = read_rows(FIXTURE)
        regime = rows[0]["climate_regime"]
        for row in rows:
            row["climate_regime"] = regime
            row["observed_slr"] = round(float(row["baseline_slr"]) * 1.5, 4)
        median_artifact = train_artifact_median(rows, _config())
        mean_artifact = train_artifact(rows, _config())
        self.assertEqual(median_artifact["methods"], mean_artifact["methods"])

    def test_global_node_is_shrunk_weighted_median(self) -> None:
        rows = read_rows(FIXTURE)
        regime = rows[0]["climate_regime"]
        multipliers = [0.6, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 3.0]
        rows = rows[: len(multipliers)]
        for row, mult in zip(rows, multipliers):
            row["climate_regime"] = regime
            row["observed_slr"] = round(float(row["baseline_slr"]) * mult, 4)
        config = _config()
        artifact = train_artifact_median(rows, config)
        residuals = np.array([math.log(m) for m in multipliers])
        expected_raw = float(np.median(residuals))
        ess = float(len(multipliers))
        expected_log_bias = expected_raw * ess / (ess + config.prior_effective_sample_size)
        node = artifact["methods"]["kuchera"]["global"]
        self.assertAlmostEqual(node["rawLogBias"], round(expected_raw, 8), places=7)
        self.assertAlmostEqual(node["logBias"], round(expected_log_bias, 8), places=7)
        # The skewed 3.0 multiplier pulls the mean well above the median.
        mean_artifact = train_artifact(rows, config)
        self.assertGreater(
            mean_artifact["methods"]["kuchera"]["global"]["rawLogBias"],
            node["rawLogBias"] + 0.02,
        )


def _model_frame(n_stations: int = 10, per_station: int = 14) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    columns = ["feat_a", "feat_b", "feat_c"]
    rows = []
    for s in range(n_stations):
        for i in range(per_station):
            feats = rng.normal(size=len(columns)) + s * 0.1
            observed = float(10.0 + feats[0] * 1.5 + rng.normal(0, 1.0))
            rows.append({
                "event_id": f"syn-{s}-{i}",
                "region": "rockies",
                "station_id": f"st-{s}",
                "site_id": f"st-{s}",
                "valid_time": "2024-01-15T07:00:00-06:00",
                "latitude": 39.0 + s * 0.05,
                "longitude": -106.0,
                "observed_slr": min(29.0, max(3.5, observed)),
                "baseline_slr": 11.0,
                "climate_regime": "continental_mountain",
                **{c: float(v) for c, v in zip(columns, feats)},
            })
    return pd.DataFrame(rows), columns


class AbsoluteErrorGBMModelTests(unittest.TestCase):
    def test_fit_predict_shapes_bounds_and_parts(self) -> None:
        frame, columns = _model_frame()
        model = AbsoluteErrorGBMModel(seed=5, columns=columns).fit(frame)
        base, calibrated = model.predict_parts(frame)
        self.assertEqual(len(base), len(frame))
        self.assertTrue(np.all(base >= 3.0) and np.all(base <= 30.0))
        self.assertTrue(np.all(calibrated >= 3.0) and np.all(calibrated <= 30.0))
        np.testing.assert_array_equal(model.predict(frame), calibrated)
        mean_eb = model.predict_mean_eb(frame)
        self.assertEqual(len(mean_eb), len(frame))
        self.assertEqual(
            model.eb_median_artifact["modelKind"], "hierarchical-log-residual-median-eb"
        )
        self.assertEqual(
            model.eb_mean_artifact["modelKind"], "hierarchical-log-residual-eb"
        )
        hierarchies = model.hierarchy_median(frame.head(6))
        self.assertEqual(len(hierarchies), 6)
        self.assertTrue(all(h[0] == "global" for h in hierarchies))


class MedianCacheSpecTests(unittest.TestCase):
    def test_specs_registered_and_derived_ensemble_resolves(self) -> None:
        for name in ("gbm_med", "gbm_med_eb", "gbm_med_mean_eb",
                     "ens_med_m", "ens_med_d1", "ens_med_d2"):
            self.assertIn(name, MODEL_SPECS)
        with tempfile.TemporaryDirectory() as directory:
            cache = PredictionCache(
                Path(directory), cohort="c", features="f", manifests="m",
                libraries={"sklearn": "x", "scipy": "x", "numpy": "x", "pandas": "x"},
            )
            ids = ["e1", "e2", "e3"]
            cache.put("rf_eb", "splitX", 7, ids, np.array([10.0, 12.0, 14.0]))
            cache.put("gbm_med_eb", "splitX", 7, ids, np.array([12.0, 12.0, 12.0]))
            ens = cache.get("ens_med_m", "splitX", 7, ids)
            np.testing.assert_allclose(ens, np.array([11.0, 12.0, 13.0]))


if __name__ == "__main__":
    unittest.main()
