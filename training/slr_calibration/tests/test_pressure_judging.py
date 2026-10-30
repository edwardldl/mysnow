"""Tests for the pressure-level judging entry point (gold_model pressure
section): feature-column threading, obs-end-hour derivation, the shared
pressure split runner, and the mechanical plan-section-7 verdict."""

from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from slr_calibration.gold_model import (
    GBMEBModel,
    PRESSURE_MODELS,
    RFEBModel,
    _feature_columns,
    _fit_gbm,
    _fit_rf,
    _run_pressure_split,
    add_obs_hour_features,
    fit_predict,
    pressure_route_verdict,
)
from slr_calibration.gold_profiles import FEATURE_COLUMNS

PRESSURE_COLS = ["t_850", "rh_850", "ws_850", "surface_pressure_hpa"]


def _frame(n_per_region: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    rows = []
    regions = ["rockies", "northeast", "alaska", "pacific-northwest"]
    for r_idx, region in enumerate(regions):
        for i in range(n_per_region):
            station = f"{region}-st{i % 8}"
            observed = float(9 + r_idx + rng.normal(0, 2))
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
                "observed_slr": max(2.5, observed),
                "fresh_snow_mm": 100.0,
                "fresh_swe_mm": 10.0,
                "climate_regime": "continental_mountain",
                "source_tier": "ghcn-coop",
                "baseline_slr": 12.0,
                "profile_status": "ok",
                "latitude": 39.0 + r_idx,
                "longitude": -105.0 - r_idx,
                "elevation_m": 2500.0,
            }
            for j, column in enumerate(FEATURE_COLUMNS):
                row[column] = float(j) + r_idx
            for j, column in enumerate(PRESSURE_COLS):
                row[column] = 5.0 * j + r_idx + rng.normal(0, 0.5)
            rows.append(row)
    return pd.DataFrame(rows)


class FeatureColumnsTest(unittest.TestCase):
    def test_default_is_agl_spec(self) -> None:
        self.assertEqual(_feature_columns(None), list(FEATURE_COLUMNS))

    def test_override_respected(self) -> None:
        self.assertEqual(_feature_columns(PRESSURE_COLS), PRESSURE_COLS)


class ColumnsThreadingTest(unittest.TestCase):
    def test_fit_rf_and_gbm_use_given_columns(self) -> None:
        frame = _frame(10)
        rf = _fit_rf(frame, seed=3, columns=PRESSURE_COLS)
        gbm = _fit_gbm(frame, seed=3, columns=PRESSURE_COLS)
        self.assertEqual(rf.n_features_in_, len(PRESSURE_COLS))
        self.assertEqual(gbm.n_features_in_, len(PRESSURE_COLS))

    def test_fit_predict_threads_columns(self) -> None:
        frame = _frame(10)
        pred = fit_predict("rf", frame, frame, seed=3, columns=PRESSURE_COLS)
        self.assertEqual(len(pred), len(frame))
        self.assertTrue(np.isfinite(pred).all())

    def test_eb_models_store_columns(self) -> None:
        self.assertEqual(RFEBModel(0, columns=PRESSURE_COLS).columns, PRESSURE_COLS)
        self.assertEqual(GBMEBModel(0, columns=PRESSURE_COLS).columns, PRESSURE_COLS)
        self.assertEqual(RFEBModel(0).columns, list(FEATURE_COLUMNS))


class ObsHourFeaturesTest(unittest.TestCase):
    def test_local_hour_parsed_as_written(self) -> None:
        frame = pd.DataFrame({"valid_time": ["2007-11-12T07:00:00-09:00"]})
        out = add_obs_hour_features(frame)
        angle = 2 * math.pi * 7 / 24
        self.assertAlmostEqual(out["obs_end_hour_sin"].iloc[0], math.sin(angle))
        self.assertAlmostEqual(out["obs_end_hour_cos"].iloc[0], math.cos(angle))

    def test_no_utc_conversion(self) -> None:
        # 07:30 at +14:00 is 17:30 UTC the previous day; the feature must
        # still encode 7.5, the local wall-clock hour as written.
        frame = pd.DataFrame({"valid_time": ["2024-06-01T07:30:00+14:00"]})
        out = add_obs_hour_features(frame)
        angle = 2 * math.pi * 7.5 / 24
        self.assertAlmostEqual(out["obs_end_hour_sin"].iloc[0], math.sin(angle))
        self.assertAlmostEqual(out["obs_end_hour_cos"].iloc[0], math.cos(angle))

    def test_missing_time_component_raises(self) -> None:
        frame = pd.DataFrame({"valid_time": ["2007-11-12"]})
        with self.assertRaises(ValueError):
            add_obs_hour_features(frame)


class PressureSplitSmokeTest(unittest.TestCase):
    def test_run_pressure_split_returns_all_models(self) -> None:
        frame = _frame(40)
        out = _run_pressure_split(frame, frame, seed=5, columns=PRESSURE_COLS)
        self.assertEqual(set(out["models"]), set(PRESSURE_MODELS))
        for model, metrics in out["models"].items():
            self.assertIsNotNone(metrics["overall"]["mae"], model)
            self.assertTrue(np.isfinite(metrics["overall"]["mae"]), model)


def _results(randomised: float, loro: dict[str, tuple[float, float]],
             temporal: float, stations: float, tail_high: float) -> dict:
    """Minimal results dict shaped like the benchmark outputs, for ens_eb."""
    def split_block(mae: float) -> dict:
        return {"models": {"ens_eb": {"overall": {"mae": mae, "bias": 0.0, "n": 10}}}}

    return {
        "splits": {
            "random60_40": {"aggregated": {"ens_eb": {
                "maeMean": randomised, "maeStd": 0.1,
                "rep0": {
                    "overall": {"mae": randomised, "bias": 0.0, "n": 10},
                    "tailHigh_slr_gt_20": {"mae": tail_high, "n": 5},
                    "tailLow_slr_lt_5": {"mae": 3.0, "n": 5},
                },
            }}},
            "leaveOneRegionOut": {
                region: {"models": {"ens_eb": {"overall": {"mae": mae, "bias": bias, "n": 10}}}}
                for region, (mae, bias) in loro.items()
            },
            "temporalWY2026": split_block(temporal),
            "unseenStations": split_block(stations),
        }
    }


_REFERENCE_LORO = {
    "alaska": (3.990, -2.424),
    "pacific-northwest": (3.379, 0.583),
    "rockies": (3.427, -0.243),
}


def _reference() -> dict:
    return _results(3.011, _REFERENCE_LORO, 3.156, 3.224, 9.004)


class PressureRouteVerdictTest(unittest.TestCase):
    def test_route_a_pass(self) -> None:
        loro = {r: (mae - 0.15, bias) for r, (mae, bias) in _REFERENCE_LORO.items()}
        candidate = _results(2.95, loro, 3.10, 3.18, 8.90)
        # LORO mean improves by 0.15 with everything else inside limits.
        verdict = pressure_route_verdict(candidate, _reference())
        self.assertTrue(verdict["routeA"]["passed"])
        self.assertTrue(verdict["adopted"])

    def test_route_a_loro_gain_too_small_and_route_b_fail(self) -> None:
        candidate = _results(2.95, _REFERENCE_LORO, 3.10, 3.18, 8.90)
        # Shrink only the LORO gain below 0.03 by worsening every region a bit.
        loro = {r: (mae + 0.15, bias) for r, (mae, bias) in _REFERENCE_LORO.items()}
        candidate = _results(2.95, loro, 3.10, 3.18, 8.90)
        verdict = pressure_route_verdict(candidate, _reference())
        self.assertFalse(verdict["routeA"]["passed"])
        self.assertFalse(verdict["adopted"])

    def test_route_b_pass_on_alaska_bias(self) -> None:
        loro = dict(_REFERENCE_LORO)
        loro["alaska"] = (3.950, -1.800)  # |bias| gain 0.624, MAE improves too
        candidate = _results(3.05, loro, 3.20, 3.26, 9.10)
        verdict = pressure_route_verdict(candidate, _reference())
        self.assertTrue(verdict["routeB"]["regions"]["alaska"]["passed"])
        self.assertTrue(verdict["adopted"])

    def test_route_b_blocked_by_region_mae_regression(self) -> None:
        loro = dict(_REFERENCE_LORO)
        loro["alaska"] = (4.100, -1.800)  # bias gain 0.624 but MAE regresses
        candidate = _results(3.05, loro, 3.20, 3.26, 9.10)
        verdict = pressure_route_verdict(candidate, _reference())
        self.assertFalse(verdict["routeB"]["regions"]["alaska"]["passed"])
        self.assertFalse(verdict["adopted"])

    def test_headline_breach_blocks_route_a(self) -> None:
        loro = {r: (mae - 0.10, bias) for r, (mae, bias) in _REFERENCE_LORO.items()}
        candidate = _results(3.30, loro, 3.10, 3.18, 8.90)  # randomised regresses 0.289
        verdict = pressure_route_verdict(candidate, _reference())
        self.assertFalse(verdict["routeA"]["headlineOk"])
        self.assertFalse(verdict["routeA"]["passed"])


if __name__ == "__main__":
    unittest.main()
