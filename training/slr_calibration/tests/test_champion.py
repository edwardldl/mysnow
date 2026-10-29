from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from slr_calibration.champion import (
    PACKAGE_FORMAT,
    ChampionModel,
    build_champion_package,
    predict_file,
    validate_event_frame,
)
from slr_calibration.gold_model import GBMEBModel, RFEBModel, _fit_gbm, _fit_rf
from slr_calibration.gold_profiles import FEATURE_COLUMNS

_REGION_GEO = {
    "rockies": (40.0, -106.0, 3000.0),
    "northeast": (44.0, -71.0, 1200.0),
    "alaska": (64.0, -150.0, 600.0),
}


def _frame(n_per_region: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    for r_idx, (region, (lat0, lon0, elev)) in enumerate(_REGION_GEO.items()):
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
                "observed_slr": min(20.0, max(5.0, observed)),
                "fresh_snow_mm": 100.0,
                "fresh_swe_mm": 10.0,
                "climate_regime": "continental_mountain",
                "source_tier": "ghcn-coop",
                "baseline_slr": 12.0,
                "profile_status": "ok",
            }
            for j, column in enumerate(FEATURE_COLUMNS):
                row[column] = float(j) + r_idx
            station_idx = i % 8
            row["latitude"] = lat0 + station_idx * 0.05
            row["longitude"] = lon0 + station_idx * 0.05
            row["elevation_m"] = elev
            rows.append(row)
    return pd.DataFrame(rows)


def _fake_variants_results() -> dict:
    def entry(mae: float) -> dict:
        return {"models": {"ens_eb": {"overall": {"mae": mae, "bias": -0.5, "rmse": mae + 1}}}}

    return {
        "splits": {
            "random60_40": {
                "aggregated": {
                    "ens_eb": {
                        "maeMean": 3.011,
                        "maeStd": 0.02,
                        "rep0": {
                            "tailHigh_slr_gt_20": {"mae": 9.004},
                            "tailLow_slr_lt_5": {"mae": 3.773},
                        },
                    }
                }
            },
            "leaveOneRegionOut": {
                "rockies": entry(3.4),
                "northeast": entry(3.5),
                "alaska": entry(4.0),
            },
            "temporalWY2026": entry(3.156),
            "unseenStations": entry(3.224),
        }
    }


class ChampionPackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.frame = _frame()
        cls.source_dir = root / "source"
        cls.source_dir.mkdir()
        cls.features_path = cls.source_dir / "features.parquet"
        cls.frame.to_parquet(cls.features_path)
        joblib.dump(_fit_rf(cls.frame, seed=0), cls.source_dir / "rf_full.joblib")
        joblib.dump(_fit_gbm(cls.frame, seed=0), cls.source_dir / "gbm_full.joblib")
        (cls.source_dir / "results_variants.json").write_text(
            json.dumps(_fake_variants_results()), encoding="utf-8"
        )
        cls.champion_dir = root / "champion"
        cls.sidecar = build_champion_package(
            features_path=cls.features_path,
            source_dir=cls.source_dir,
            output_dir=cls.champion_dir,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_layout_and_sidecar(self) -> None:
        for name in ("rf.joblib", "gbm.joblib", "champion_sidecar.json", "ARTIFACT.md"):
            self.assertTrue((self.champion_dir / name).exists(), name)
        sidecar = json.loads((self.champion_dir / "champion_sidecar.json").read_text())
        self.assertEqual(sidecar["packageFormat"], PACKAGE_FORMAT)
        self.assertEqual(sidecar["features"], list(FEATURE_COLUMNS))
        self.assertEqual(sidecar["combination"]["rule"], "simple_mean")
        self.assertEqual(sidecar["training"]["trainingRows"], len(self.frame))
        for component in ("rf_eb", "gbm_eb"):
            artifact = sidecar["components"][component]["ebArtifact"]
            self.assertIn("kuchera", artifact["methods"])
            self.assertIn("global", artifact["methods"]["kuchera"])
        self.assertLess(
            sidecar["buildVerification"]["innerRfMaxAbsPredDiffVsRfFull"], 1e-9
        )
        artifact_md = (self.champion_dir / "ARTIFACT.md").read_text()
        self.assertIn("gates remain OFF", artifact_md)

    def test_judged_scores_extracted(self) -> None:
        judged = self.sidecar["judgedScores"]
        self.assertEqual(judged["randomisedMaeMean"], 3.011)
        self.assertAlmostEqual(judged["loroMaeMean"], (3.4 + 3.5 + 4.0) / 3, places=4)
        self.assertEqual(judged["temporalMae"], 3.156)
        self.assertEqual(judged["rep0TailHighSlrGt20Mae"], 9.004)

    def test_predict_matches_harness(self) -> None:
        expected = (
            RFEBModel(seed=0).fit(self.frame).predict(self.frame)
            + GBMEBModel(seed=0).fit(self.frame).predict(self.frame)
        ) / 2.0
        model = ChampionModel.load(self.champion_dir)
        predicted = model.predict(self.frame)
        self.assertTrue(np.isfinite(predicted).all())
        self.assertTrue(((predicted >= 3.0) & (predicted <= 30.0)).all())
        self.assertLess(float(np.max(np.abs(predicted - expected))), 1e-8)

    def test_determinism_across_loads(self) -> None:
        first = ChampionModel.load(self.champion_dir).predict(self.frame)
        second = ChampionModel.load(self.champion_dir).predict(self.frame)
        self.assertTrue(np.array_equal(first, second))

    def test_unseen_region_falls_back_to_global_node(self) -> None:
        model = ChampionModel.load(self.champion_dir)
        row = self.frame.iloc[[0]].copy()
        row["region"] = "unseen-nowhere"
        row["site_id"] = ""
        row["latitude"] = 0.0
        row["longitude"] = -150.0
        rf_eb, _ = model.predict_parts(row)
        # Manual expectation: clipped RF prediction times the RF EB
        # global-node factor alone (climate/region/local all absent
        # for open-Pacific geography, so the hierarchy walk stops).
        X = row[list(FEATURE_COLUMNS)].to_numpy(dtype=float)
        rf_pred = float(np.clip(model.rf.predict(X)[0], 3.0, 30.0))
        global_node = model.rf_eb_artifact["methods"]["kuchera"]["global"]
        self.assertGreaterEqual(global_node["effectiveSampleSize"], 5.0)
        log_bias = min(math.log(2.0), max(math.log(0.5), float(global_node["logBias"])))
        expected = min(30.0, max(3.0, rf_pred * math.exp(log_bias)))
        self.assertAlmostEqual(float(rf_eb[0]), expected, places=8)
        self.assertTrue(np.isfinite(model.predict(row)).all())

    def test_validation_errors(self) -> None:
        model = ChampionModel.load(self.champion_dir)
        missing = self.frame.drop(columns=["t_1500"])
        with self.assertRaises(ValueError) as ctx:
            model.predict(missing)
        self.assertIn("t_1500", str(ctx.exception))
        with self.assertRaises(ValueError):
            validate_event_frame(
                self.frame.assign(bogus=1.0), model.features, strict=True
            )
        nan_frame = self.frame.copy()
        nan_frame.loc[0, "rh_300"] = np.nan
        with self.assertRaises(ValueError):
            model.predict(nan_frame)

    def test_predict_file_with_snowfall(self) -> None:
        keep = [*FEATURE_COLUMNS, "region", "site_id", "event_id"]
        events = self.frame.head(6)[keep].copy()
        events["liquid_mm"] = 10.0
        input_path = Path(self._tmp.name) / "events.csv"
        output_path = Path(self._tmp.name) / "predictions.csv"
        events.to_csv(input_path, index=False)
        summary = predict_file(self.champion_dir, input_path, output_path)
        self.assertEqual(summary["events"], 6)
        out = pd.read_csv(output_path)
        self.assertIn("snowfall_mm", out.columns)
        np.testing.assert_allclose(
            out["snowfall_mm"].to_numpy(), out["ens_eb_slr"].to_numpy() * 10.0
        )
        np.testing.assert_allclose(
            out["ens_eb_slr"].to_numpy(),
            (out["rf_eb_slr"].to_numpy() + out["gbm_eb_slr"].to_numpy()) / 2.0,
        )


if __name__ == "__main__":
    unittest.main()
