from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from slr_calibration.evaluation import evaluate_temporal_holdout


class EvaluationTests(unittest.TestCase):
    def test_temporal_storm_holdout_reports_real_improvement(self) -> None:
        fields = [
            "event_id", "storm_group_id", "station_id", "site_id", "valid_time",
            "latitude", "longitude", "observed_slr", "baseline_slr",
            "baseline_method", "climate_regime", "qc_passed", "qc_weight",
        ]
        rows = []
        for index in range(12):
            rows.append({
                "event_id": f"train-{index}", "storm_group_id": f"train-storm-{index // 2}",
                "station_id": "CSSL", "site_id": "CSSL", "valid_time": f"2023-02-{index + 1:02d}T16:00:00-08:00",
                "latitude": 39.3257, "longitude": -120.3681, "observed_slr": 8,
                "baseline_slr": 10, "baseline_method": "kuchera", "climate_regime": "maritime_mountain",
                "qc_passed": True, "qc_weight": 1,
            })
        for index in range(6):
            rows.append({
                "event_id": f"test-{index}", "storm_group_id": f"test-storm-{index // 2}",
                "station_id": "CSSL", "site_id": "CSSL", "valid_time": f"2024-02-{index + 1:02d}T16:00:00-08:00",
                "latitude": 39.3257, "longitude": -120.3681, "observed_slr": 8,
                "baseline_slr": 10, "baseline_method": "kuchera", "climate_regime": "maritime_mountain",
                "qc_passed": True, "qc_weight": 1,
            })
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "events.csv"
            with input_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            _, report = evaluate_temporal_holdout(
                input_path,
                root / "artifact.json",
                root / "report.json",
                "test-artifact",
                "test-v1",
                test_water_year=2024,
                prior_effective_sample_size=4,
                min_effective_sample_size=2,
            )
        self.assertEqual(report["split"]["trainEventCount"], 12)
        self.assertEqual(report["split"]["testEventCount"], 6)
        self.assertTrue(report["improvement"]["heldOutImprovement"])
        self.assertLess(report["calibrated"]["mae"], report["baseline"]["mae"])
        self.assertFalse(report["decision"]["productionEnablement"])


if __name__ == "__main__":
    unittest.main()
