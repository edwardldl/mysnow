from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "slr_methodology_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("slr_methodology_diagnostics", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class MethodologyDiagnosticsTests(unittest.TestCase):
    def event(self, station: str, storm: str, qc_weight: float = 1.0):
        return module.Event(
            event_id=f"{station}-{storm}", station_id=station, storm_id=storm,
            valid_time="2024-01-01T00:00:00+00:00", observed=10.0, baseline=12.0,
            qc_weight=qc_weight, elevation_m=1000.0, swe_proxy_mm=10.0,
            geographic_slice="", features={"x": 1.0},
        )

    def test_balancing_normalizes_and_equalizes_requested_groups(self) -> None:
        events = [self.event("a", "x"), self.event("a", "x"), self.event("b", "y")]
        event_weights = module.balance_weights(events, "event")
        station_weights = module.balance_weights(events, "station")
        storm_weights = module.balance_weights(events, "storm")
        joint_weights = module.balance_weights(events, "joint")
        for weights in (event_weights, station_weights, storm_weights, joint_weights):
            self.assertAlmostEqual(sum(weights), 3.0)
            self.assertTrue(all(weight > 0 for weight in weights))
        self.assertAlmostEqual(sum(station_weights[:2]), station_weights[2])
        self.assertAlmostEqual(sum(storm_weights[:2]), storm_weights[2])
        self.assertLess(joint_weights[0], station_weights[0])

    def test_finite_order_quantile_uses_finite_sample_rank(self) -> None:
        self.assertEqual(module.finite_sample_order_quantile([1.0, 4.0, 2.0, 3.0, math.inf], 0.8), 4.0)
        with self.assertRaises(ValueError):
            module.finite_sample_order_quantile([math.inf, math.nan], 0.8)

    def test_linear_solver(self) -> None:
        solution = module.solve_linear([[2.0, 1.0], [1.0, 3.0]], [1.0, 2.0])
        self.assertAlmostEqual(solution[0], 0.2)
        self.assertAlmostEqual(solution[1], 0.6)

    def test_log_intercept_is_station_balanced(self) -> None:
        events = [self.event("a", "x"), self.event("a", "y"), self.event("b", "z")]
        events = [module.Event(**{**event.__dict__, "observed": event.baseline * (2.0 if event.station_id == "a" else 0.5)}) for event in events]
        model = module.fit_log_intercept(events)
        self.assertAlmostEqual(model["logOffset"], 0.0)
        self.assertAlmostEqual(model["multiplier"], 1.0)

    def test_feature_extraction_uses_real_thermal_fields_only(self) -> None:
        profile = {"hourly": [{
            "temperature2mC": -2.0, "freezingLevelHeightM": 900.0,
            "precipitationMm": 2.0, "snowfallCm": 1.0,
            "levels": [{"pressureHpa": 850, "temperatureC": -5.0, "geopotentialHeightM": 1500.0}],
        }]}
        row = {"elevation_m": "1200", "baseline_slr": "11", "model_frozen_swe_proxy_weight_mm": "8"}
        features = module.extract_features(profile, row)
        self.assertEqual(features["temperature2m_c_mean"], -2.0)
        self.assertEqual(features["temperature_850hpa_c_mean"], -5.0)
        self.assertEqual(features["geopotential_850hpa_m_mean"], 1500.0)
        self.assertEqual(features["precipitation_mm_sum"], 2.0)
        self.assertFalse(any("humidity" in name or "wind" in name for name in features))
        event = module.Event(
            event_id="x", station_id="s", storm_id="t", valid_time="2024-01-01T00:00:00+00:00",
            observed=10.0, baseline=11.0, qc_weight=1.0, elevation_m=1200.0,
            swe_proxy_mm=8.0, geographic_slice="", features=features,
        )
        thermal = module.usable_feature_names([event], "thermal")
        full = module.usable_feature_names([event], "full")
        self.assertIn("temperature_850hpa_c_mean", thermal)
        self.assertNotIn("baseline_slr", thermal)
        self.assertIn("baseline_slr", full)

    def test_grouped_cv_folds_never_split_groups(self) -> None:
        events = [
            self.event("a", "storm-1"), self.event("a", "storm-1"),
            self.event("b", "storm-2"), self.event("c", "storm-3"),
        ]
        for family, field in (("unseen_station", "station_id"), ("contiguous_storm", "storm_id")):
            folds = module.grouped_cv_folds(events, family)
            self.assertEqual(sum(len(held) for _, held in folds), len(events))
            for fit, held in folds:
                self.assertFalse({getattr(event, field) for event in fit} & {getattr(event, field) for event in held})

    def test_ridge_fits_finite_predictions(self) -> None:
        events = []
        for index in range(6):
            events.append(module.Event(
                event_id=str(index), station_id=f"s{index % 2}", storm_id=f"t{index // 2}",
                valid_time="2024-01-01T00:00:00+00:00", observed=10.0 + index,
                baseline=10.0, qc_weight=1.0, elevation_m=1000.0, swe_proxy_mm=1.0,
                geographic_slice="", features={"x": float(index), "sometimes_missing": None if index == 0 else float(index)},
            ))
        model = module.fit_ridge(events, "event", 1.0)
        predictions = module.predict_ridge(model, events)
        self.assertEqual(len(predictions), len(events))
        self.assertTrue(all(math.isfinite(value) for value in predictions))


if __name__ == "__main__":
    unittest.main()
