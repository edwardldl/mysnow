from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from slr_calibration.cssl import CsslImportConfig, import_cssl_directory
from slr_calibration.profiles import _response_hours

FIXTURE = Path(__file__).parent / "fixtures" / "cssl-wy2024-sample.csv"


class CsslImporterTests(unittest.TestCase):
    def test_real_cc0_rows_create_direct_fresh_slr_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "WY2024.csv"
            source.write_bytes(FIXTURE.read_bytes())
            accepted, rejected, report = import_cssl_directory(Path(directory), CsslImportConfig())
        self.assertEqual(len(accepted), 5)
        self.assertEqual({row["storm_group_id"] for row in accepted}, {"CSSL-STORM-0001"})
        self.assertAlmostEqual(float(accepted[1]["observed_slr"]), 52.5 * 10 / 58)
        self.assertEqual(accepted[1]["fresh_swe_mm"], 58.0)
        self.assertFalse(report["qc"]["publishedRatioAvailable"])
        reasons = {item.reason for item in rejected}
        self.assertIn("MIXED_OR_UNCERTAIN_PHASE", reasons)
        self.assertIn("BELOW_FRESH_SWE_THRESHOLD", reasons)
        self.assertIn("UNSAFE_MEASUREMENT_REMARK", reasons)
        self.assertTrue(all(row["source_license"] == "CC0-1.0" for row in accepted))

    def test_total_pack_fields_never_affect_label(self) -> None:
        with FIXTURE.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        rows[0]["Snowpack depth (cm)"] = "99999"
        rows[0]["Snow Water Equivalent (cm)"] = "-99999"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "WY2024.csv"
            with source.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            accepted, _, _ = import_cssl_directory(Path(directory))
        self.assertAlmostEqual(float(accepted[0]["observed_slr"]), 48.5 * 10 / 26)


class ProfileParserTests(unittest.TestCase):
    def test_openmeteo_hourly_shape_is_preserved(self) -> None:
        times = ["2024-03-02T00:00", "2024-03-02T01:00"]
        hourly: dict[str, list[float | str | None]] = {
            "time": times,
            "temperature_2m": [-3.0, -4.0],
            "precipitation": [1.0, 2.0],
            "snowfall": [0.7, 1.4],
            "freezing_level_height": [1500.0, 1400.0],
        }
        for level in (1000, 975, 950, 925, 900, 850, 800, 700, 600, 500):
            hourly[f"temperature_{level}hPa"] = [-5.0, -6.0]
            hourly[f"geopotential_height_{level}hPa"] = [2200.0, 2210.0]
        hours = _response_hours({"hourly": hourly})
        self.assertEqual(len(hours), 2)
        self.assertEqual(len(hours[0]["levels"]), 10)
        self.assertEqual(hours[0]["localPstDate"], "2024-03-01")


if __name__ == "__main__":
    unittest.main()
