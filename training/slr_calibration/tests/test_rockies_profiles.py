from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from slr_calibration.profiles import HOURLY_FIELDS, backfill_openmeteo_multistation_profiles


class RockiesProfileTests(unittest.TestCase):
    def test_exact_reported_local_observation_window_from_cached_station_year(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events_path = root / "events.csv"
            fields = [
                "event_id", "storm_group_id", "station_id", "site_id", "station_name", "state",
                "observation_date", "valid_time", "latitude", "longitude", "elevation_m",
                "observed_slr", "climate_regime", "qc_weight", "source_license", "source_doi",
            ]
            event = {
                "event_id": "US1COX-2024-01-02", "storm_group_id": "rockies-2024-01-02",
                "station_id": "US1COX", "site_id": "US1COX", "station_name": "Mountain", "state": "CO",
                "observation_date": "2024-01-02", "valid_time": "2024-01-02T07:00:00-07:00",
                "latitude": "39", "longitude": "-106", "elevation_m": "2500",
                "observed_slr": "10", "climate_regime": "continental_mountain", "qc_weight": "1",
                "source_license": "public-domain", "source_doi": "",
            }
            with events_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerow(event)
            cache_path = root / "cache" / "US1COX" / "wy2024.json"
            cache_path.parent.mkdir(parents=True)
            start = datetime(2024, 1, 1, 13)
            times = [(start + timedelta(hours=index)).isoformat(timespec="minutes") for index in range(26)]
            hourly: dict[str, list[float | str | None]] = {"time": times}
            for field in HOURLY_FIELDS:
                if field == "snowfall":
                    hourly[field] = [1.0 if index == 10 else 0.0 for index in range(26)]
                elif field.startswith("geopotential_height"):
                    hourly[field] = [3000.0] * 26
                elif field.startswith("temperature"):
                    hourly[field] = [-5.0] * 26
                else:
                    hourly[field] = [1.0] * 26
            cache_path.write_text(json.dumps({"hourly": hourly, "timezone": "GMT", "utc_offset_seconds": 0}) + "\n")
            output_path = root / "profiles.jsonl"
            report_path = root / "report.json"
            report = backfill_openmeteo_multistation_profiles(
                events_path, root / "cache", output_path, report_path,
                date(2023, 10, 1), date(2024, 9, 30), {"US1COX"}, 0,
            )
            record = json.loads(output_path.read_text().strip())
        self.assertEqual(report["alignedEventCount"], 1)
        self.assertEqual(report["stationWaterYearRequestCount"], 1)
        self.assertEqual(report["reusedCacheRequestCount"], 1)
        self.assertEqual(record["windowStartUtc"], "2024-01-01T14:00:00Z")
        self.assertEqual(record["windowEndUtc"], "2024-01-02T14:00:00Z")
        self.assertEqual(record["contextHourCount"], 25)
        self.assertEqual(len(record["hourly"]), 25)


if __name__ == "__main__":
    unittest.main()
