from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from slr_calibration.ghcn import GhcnRockiesConfig, import_ghcn_rockies, is_rockies_station


class GhcnRockiesImportTests(unittest.TestCase):
    def test_domain_excludes_low_elevation_and_eastern_plains(self) -> None:
        self.assertTrue(is_rockies_station("CO", 38.96, -106.99, 2928.0))
        self.assertFalse(is_rockies_station("CO", 39.0, -103.2, 1800.0))
        self.assertFalse(is_rockies_station("ID", 44.0, -114.0, 750.0))
        self.assertTrue(is_rockies_station("NM", 35.7, -106.1, 1985.0))
        self.assertFalse(is_rockies_station("NM", 32.95, -105.7, 2700.0))

    def test_import_uses_direct_pair_and_local_station_time(self) -> None:
        columns = [
            "station_id", "station_name", "state", "latitude", "longitude", "elevation_m",
            "date", "snow_mm", "wesf_mm", "slr", "obs_time", "is_accumulated_snow",
            "is_accumulated_wesf", "qc_weight", "qc_status", "source_flag",
        ]
        rows = [
            ["US1COX", "Mountain", "CO", "39.0", "-106.0", "2500", "2024-01-02", "100", "10", "10", "0700", "False", "False", "1", "passed", "N"],
            ["US1IDX", "Low", "ID", "44.0", "-114.0", "750", "2024-01-02", "100", "10", "10", "0700", "False", "False", "1", "passed", "N"],
            ["US1COY", "Accum", "CO", "39.0", "-106.0", "2500", "2024-01-03", "100", "10", "10", "0700", "True", "False", "1", "passed", "N"],
            ["US1COZ", "Summer", "CO", "39.0", "-106.0", "2500", "2024-07-02", "100", "10", "10", "0700", "False", "False", "1", "passed", "N"],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(columns)
                writer.writerows(rows)
            accepted, rejected, report = import_ghcn_rockies(path, GhcnRockiesConfig())
        self.assertEqual(len(accepted), 2)
        by_station = {row["station_id"]: row for row in accepted}
        self.assertEqual(by_station["US1COX"]["observed_slr"], 10.0)
        self.assertEqual(by_station["US1COX"]["valid_time"], "2024-01-02T07:00:00-07:00")
        self.assertEqual(by_station["US1COZ"]["valid_time"], "2024-07-02T07:00:00-06:00")
        self.assertEqual(by_station["US1COX"]["storm_group_id"], "rockies-2024-01-02")
        self.assertEqual(report["acceptedEventCount"], 2)
        self.assertEqual({item.reason for item in rejected}, {"OUTSIDE_ROCKIES_DOMAIN", "ACCUMULATED_INTERVAL"})


if __name__ == "__main__":
    unittest.main()
