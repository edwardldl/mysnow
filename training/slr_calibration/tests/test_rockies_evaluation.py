from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from slr_calibration.evaluation import evaluate_rockies_station_temporal_holdout


class RockiesEvaluationTests(unittest.TestCase):
    def test_combined_station_and_time_holdout(self) -> None:
        rows = []
        start = date(2024, 1, 1)
        for index in range(24):
            day = start + timedelta(days=index * 2)
            rows.append({
                "event_id": f"train-{index}", "storm_group_id": f"rockies-{day}",
                "station_id": f"TRAIN-{index % 3}", "site_id": f"TRAIN-{index % 3}",
                "valid_time": f"{day.isoformat()}T07:00:00-07:00", "latitude": 39.0 + index % 3,
                "longitude": -106.0, "elevation_m": 2500, "observed_slr": 10,
                "baseline_slr": 20, "baseline_method": "kuchera", "climate_regime": "continental_mountain",
                "qc_passed": "true", "qc_weight": 1, "state": "CO",
            })
        start = date(2025, 1, 1)
        for index in range(12):
            day = start + timedelta(days=index * 2)
            state = "WY" if index >= 6 else "CO"
            rows.append({
                "event_id": f"test-{index}", "storm_group_id": f"rockies-{day}",
                "station_id": "HOLD-WY" if state == "WY" else "HOLD-CO", "site_id": "HOLD-WY" if state == "WY" else "HOLD-CO",
                "valid_time": f"{day.isoformat()}T07:00:00-07:00", "latitude": 42.0 if state == "WY" else 40.0,
                "longitude": -106.0, "elevation_m": 2200, "observed_slr": 10,
                "baseline_slr": 20, "baseline_method": "kuchera", "climate_regime": "continental_mountain",
                "qc_passed": "true", "qc_weight": 1, "state": state,
            })
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "events.csv"
            with input_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader(); writer.writerows(rows)
            artifact_path = root / "artifact.json"
            report_path = root / "report.json"
            artifact, report = evaluate_rockies_station_temporal_holdout(
                input_path, artifact_path, report_path, "test", "test-v1", {"HOLD-CO", "HOLD-WY"}
            )
        self.assertEqual(report["split"]["trainEventCount"], 24)
        self.assertEqual(report["split"]["testEventCount"], 12)
        self.assertEqual(report["split"]["trainStationCount"], 3)
        self.assertEqual(report["split"]["testStationCount"], 2)
        self.assertTrue(report["improvement"]["heldOutImprovement"])
        self.assertLess(report["calibrated"]["mae"], report["baseline"]["mae"])
        self.assertEqual(report["outOfColoradoHoldout"]["eventCount"], 6)
        self.assertEqual(artifact["status"], "unvalidated")


if __name__ == "__main__":
    unittest.main()
