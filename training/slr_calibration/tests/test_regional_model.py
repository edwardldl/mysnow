from __future__ import annotations

import math
import unittest

from slr_calibration.evaluation import apply_artifact
from slr_calibration.regional_model import (
    FEATURE_NAMES,
    RidgeModel,
    apply_candidate_b,
    build_features,
    fit_candidate_b,
    select_lambda,
)
from slr_calibration.trainer import TrainingConfig, _digest_rows


def _event(index: int, station: str = "1000:CO", bias: float = 1.2) -> dict:
    day = 1 + (index % 28)
    month = 1 + (index // 28) % 3
    date_text = f"2024-{month:02d}-{day:02d}"
    baseline = 10.0 + (index % 5)
    observed = max(3.0, min(30.0, baseline * bias + (index % 3) * 0.1))
    return {
        "event_id": f"{station}-{date_text}-{index}",
        "storm_group_id": f"sg-{station}-2024-{month:02d}-01",
        "station_id": station,
        "site_id": station,
        "valid_time": f"{date_text}T00:00:00Z",
        "observation_date": date_text,
        "latitude": 39.0 + (index % 4) * 0.1,
        "longitude": -106.0,
        "elevation_m": 3000.0 + (index % 10) * 40,
        "observed_slr": observed,
        "baseline_slr": baseline,
        "baseline_method": "kuchera",
        "climate_regime": "continental_mountain",
        "qc_passed": True,
        "qc_weight": 0.25,
        "water_year": 2024,
        "fcst_precip_mm": 6.0 + (index % 7),
        "fcst_snowfall_cm": 99.0,  # ASNOW diagnostic: must never enter the features
        "fcst_t2m_mean_c": -6.0 + (index % 5),
        "fcst_t2m_min_c": -9.0,
        "fcst_t2m_max_c": -3.0,
        "hrrr_lead_min_h": 1,
        "hrrr_lead_max_h": 24,
    }


class FeatureTests(unittest.TestCase):
    def test_portable_contract_only(self) -> None:
        features = build_features(_event(0))
        self.assertIsNotNone(features)
        self.assertEqual(set(features), set(FEATURE_NAMES))
        self.assertNotIn("fcst_snowfall_cm", features)

    def test_missing_feature_returns_none(self) -> None:
        row = _event(0)
        row["fcst_t2m_mean_c"] = None
        self.assertIsNone(build_features(row))

    def test_constant_lead_feature_is_dropped(self) -> None:
        rows = [_event(i) for i in range(60)]
        samples = [build_features(row) for row in rows]
        targets = [math.log(row["observed_slr"] / row["baseline_slr"]) for row in rows]
        model = RidgeModel(FEATURE_NAMES).fit(samples, targets, [1.0] * len(rows), 1.0)
        self.assertIn("lead_max_h", model.dropped_features)
        self.assertEqual(model.coefficients["lead_max_h"], 0.0)


class RidgeTests(unittest.TestCase):
    def test_recovers_linear_signal(self) -> None:
        samples = []
        targets = []
        for i in range(200):
            features = {name: 0.0 for name in FEATURE_NAMES}
            features["baseline_slr"] = 8.0 + (i % 13)
            features["elevation_m"] = 2000.0 + (i % 40) * 50
            features["log_fcst_precip_mm"] = 1.0 + (i % 7) * 0.3
            samples.append(features)
            targets.append(0.02 * features["baseline_slr"] + 0.1)
        model = RidgeModel(FEATURE_NAMES).fit(samples, targets, [1.0] * len(samples), 0.1)
        prediction, out = model.predict(samples[3])
        self.assertFalse(out)
        self.assertAlmostEqual(prediction, targets[3], delta=0.05)

    def test_lambda_selection_returns_grid_value(self) -> None:
        rows = [_event(i) for i in range(80)]
        samples = [build_features(row) for row in rows]
        targets = [math.log(row["observed_slr"] / row["baseline_slr"]) for row in rows]
        lam, scores = select_lambda(rows, samples, targets)
        self.assertIn(lam, (0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 1000.0))
        self.assertEqual(len(scores), 10)


class CandidateBTests(unittest.TestCase):
    def _config(self) -> TrainingConfig:
        return TrainingConfig(
            artifact_id="regional-slr-noaa-test-kuchera-v1",
            artifact_version="noaa-test-b-v1",
            status="unvalidated",
            source_label="unit test",
            leakage_group="storm_group_id",
        )

    def test_fit_and_apply_roundtrip(self) -> None:
        rows = [_event(i, station=f"100{i % 4}:CO") for i in range(160)]
        artifact, info = fit_candidate_b(rows, self._config())
        self.assertEqual(artifact["training"]["candidate"], "B")
        self.assertEqual(artifact["training"]["sourceDigestSha256"], _digest_rows(rows))
        block = artifact["covariateModel"]
        self.assertEqual(block["kind"], "ridge-log-residual")
        self.assertEqual(set(block["coefficients"]), set(FEATURE_NAMES))
        self.assertIn("featureEnvelope", block)
        self.assertGreater(info["completeRows"], 100)

        row = _event(500, station="1001:CO")
        prediction = apply_candidate_b(artifact, row)
        self.assertGreaterEqual(prediction.calibrated_slr, 3.0)
        self.assertLessEqual(prediction.calibrated_slr, 30.0)
        self.assertGreaterEqual(prediction.correction_factor, 0.5)
        self.assertLessEqual(prediction.correction_factor, 2.0)

    def test_out_of_envelope_application_is_clamped(self) -> None:
        rows = [_event(i, station=f"100{i % 4}:CO") for i in range(160)]
        artifact, _ = fit_candidate_b(rows, self._config())
        wild = _event(501, station="1001:CO")
        wild["elevation_m"] = 9000.0  # far outside the training envelope
        prediction = apply_candidate_b(artifact, wild)
        self.assertGreaterEqual(prediction.calibrated_slr, 3.0)
        self.assertLessEqual(prediction.calibrated_slr, 30.0)
        # The EB-only path on the same artifact still works too.
        eb_only = apply_artifact(artifact, wild)
        self.assertGreaterEqual(eb_only.calibrated_slr, 3.0)


if __name__ == "__main__":
    unittest.main()
