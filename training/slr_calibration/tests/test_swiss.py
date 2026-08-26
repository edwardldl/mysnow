from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from slr_calibration.profiles import backfill_openmeteo_swiss_profiles
from slr_calibration.swiss import SwissImportConfig, import_swiss_new_snow, lv03_to_wgs84


class SwissImporterTest(unittest.TestCase):
    def _dataset(self, root: Path, hn_description: str = "new snow depth, observed") -> Path:
        (root / "HN").mkdir(parents=True)
        (root / "HNW").mkdir(parents=True)
        prefix = "GLOBAL FILE ATTRIBUTES\n\nDescription:                  {}\nUnits:                        {}\nDate created:                 2024-11-11T08:49:57\n\nTABLE WITH DATA\n\n"
        (root / "HN" / "OBS-HN.txt").write_text(
            prefix.format(hn_description, "cm")
            + "Time,SLF.A,SLF.B\n"
            + "2022-01-01 06:00:00,5,3\n"
            + "2022-01-02 06:00:00,-9999,0\n"
            + "2022-01-03 06:00:00,20,-9999\n",
            encoding="utf-8",
        )
        (root / "HNW" / "OBS-HNW.txt").write_text(
            prefix.format("new snow water equivalent, observed", "mm")
            + "Time,SLF.A,SLF.B\n"
            + "2022-01-01 06:00:00,5,-9999\n"
            + "2022-01-02 06:00:00,-9999,4\n"
            + "2022-01-03 06:00:00,2,5\n",
            encoding="utf-8",
        )
        (root / "STATION-LIST.txt").write_text(
            "Station,Easting (m),Northing (m),Altitude (m.a.s.l.)\n"
            "SLF.A,600000,200000,1500\n"
            "SLF.B,610000,210000,1800\n",
            encoding="utf-8",
        )
        return root

    def test_imports_only_same_timestamp_measured_hn_hnw_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events, rejected, report = import_swiss_new_snow(self._dataset(Path(temporary)), SwissImportConfig())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["observed_slr"], 10.0)
        self.assertEqual(events[0]["valid_time"], "2022-01-01T06:00:00+01:00")
        self.assertIn("no HS/SWE differencing", events[0]["source_measurement"])
        self.assertEqual(report["candidatePairCount"], 6)
        self.assertEqual(report["acceptedEventCount"], 1)
        self.assertEqual(report["rejectionCounts"], {
            "BOTH_MISSING": 1,
            "HNW_BELOW_MINIMUM": 1,
            "HNW_MISSING": 1,
            "HN_MISSING": 1,
            "HN_NONPOSITIVE": 1,
        })
        self.assertEqual(len(rejected), 5)

    def test_rejects_non_measured_hn_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dataset = self._dataset(Path(temporary), "snow depth change, modeled")
            with self.assertRaisesRegex(ValueError, "description"):
                import_swiss_new_snow(dataset)

    def test_lv03_reference_origin(self) -> None:
        latitude, longitude = lv03_to_wgs84(600000, 200000)
        self.assertAlmostEqual(latitude, 46.951081, places=5)
        self.assertAlmostEqual(longitude, 7.438637, places=5)


class SwissProfileWrapperTest(unittest.TestCase):
    def test_sets_swiss_window_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "profiles.jsonl"
            report_path = root / "report.json"

            def fake(*args, **kwargs):
                output.write_text(json.dumps({"event_id": "x", "profileAlignment": "old"}) + "\n", encoding="utf-8")
                report_path.write_text("{}\n", encoding="utf-8")
                return {"limitations": []}

            with patch("slr_calibration.profiles.backfill_openmeteo_multistation_profiles", side_effect=fake):
                report = backfill_openmeteo_swiss_profiles(
                    root / "events.csv", root / "cache", output, report_path,
                    date(2021, 9, 1), date(2022, 8, 31), sleep_seconds=0,
                )
            record = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("06:00 CET", record["profileAlignment"])
            self.assertEqual(report["observationSource"]["doi"], "10.16904/envidat.590")
            self.assertIn("commercial-use terms remain uncleared", " ".join(report["limitations"]))


if __name__ == "__main__":
    unittest.main()
