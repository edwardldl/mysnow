from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from slr_calibration.sierra_evaluation import evaluate_sierra_station_water_year_holdout


class SierraEvaluationTests(unittest.TestCase):
    def test_unseen_station_water_year_gate_and_slices(self) -> None:
        rows = []
        train_start = date(2022, 10, 2)
        for index in range(60):
            day = train_start + timedelta(days=index * 2)
            station = f"TRAIN-{index % 3}"
            rows.append({
                "event_id": f"train-{index}", "storm_group_id": f"sierra-{day}",
                "station_id": station, "site_id": station,
                "valid_time": f"{day.isoformat()}T07:00:00-08:00",
                "latitude": 38.0 + index % 3, "longitude": -120.0,
                "elevation_m": 1600 + 300 * (index % 3), "observed_slr": 10,
                "baseline_slr": 20, "baseline_method": "kuchera",
                "climate_regime": "sierra_maritime_mountain", "qc_passed": "true",
                "qc_weight": 1, "state": "CA",
            })
        held_out = [("US1CACV0014", 1180), ("US1CAED0035", 1920), ("US1CAMN0005", 2154)]
        test_start = date(2023, 12, 2)
        for station_index, (station, elevation) in enumerate(held_out):
            for index in range(6):
                day = test_start + timedelta(days=station_index * 20 + index * 2)
                rows.append({
                    "event_id": f"test-{station}-{index}", "storm_group_id": f"sierra-{day}",
                    "station_id": station, "site_id": station,
                    "valid_time": f"{day.isoformat()}T07:00:00-08:00",
                    "latitude": 38.2 + station_index * 0.5, "longitude": -120.2 + station_index * 0.1,
                    "elevation_m": elevation, "observed_slr": 10,
                    "baseline_slr": 20, "baseline_method": "kuchera",
                    "climate_regime": "sierra_maritime_mountain", "qc_passed": "true",
                    "qc_weight": 1, "state": "CA",
                })
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "events.csv"
            with input_path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
            artifact, report = evaluate_sierra_station_water_year_holdout(
                input_path, root / "artifact.json", root / "report.json",
                "test-sierra", "test-v1", {station for station, _ in held_out}, 2023, 2024,
            )
        self.assertEqual(report["split"]["trainEventCount"], 60)
        self.assertEqual(report["split"]["testEventCount"], 18)
        self.assertEqual(report["split"]["testStationCount"], 3)
        self.assertTrue(report["decision"]["commitResearchArtifact"])
        self.assertLess(report["calibrated"]["mae"], report["baseline"]["mae"])
        self.assertEqual(set(report["byStation"]), {station for station, _ in held_out})
        self.assertEqual(set(report["byGeographicSlice"]), {"western_slope", "tahoe_high_country", "eastern_sierra"})
        self.assertEqual(artifact["status"], "unvalidated")


if __name__ == "__main__":
    unittest.main()
