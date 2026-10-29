from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from slr_calibration.gold_profiles_sp import (
    _part_path,
    _part_valid,
    _write_gzip_json,
    plan_sp_chunks,
    required_hours,
    sp_key,
)

LOCAL = timezone(timedelta(hours=-8))


def _event(valid_time: datetime) -> dict:
    return {
        "event_id": "ghcn:TEST0001:2021-01-15",
        "region": "rockies",
        "station_id": "TEST0001",
        "site_id": "TEST0001",
        "station_name": "Test",
        "state": "CO",
        "valid_time": valid_time,
        "observation_date": valid_time.date().isoformat(),
        "water_year": 2021,
        "month": valid_time.month,
        "latitude": 39.0,
        "longitude": -106.0,
        "elevation_m": 3000.0,
        "observed_slr": 12.0,
        "fresh_snow_mm": 120.0,
        "fresh_swe_mm": 10.0,
        "climate_regime": "continental",
        "source_tier": "ghcn",
    }


class SpKeyTests(unittest.TestCase):
    def test_key_layout_matches_an_sfc_family(self) -> None:
        self.assertEqual(
            sp_key(2024, 1),
            "e5.oper.an.sfc/202401/e5.oper.an.sfc.128_134_sp.ll025sc.2024010100_2024013123.nc",
        )

    def test_key_last_day_leap_february(self) -> None:
        self.assertTrue(sp_key(2024, 2).endswith("2024020100_2024022923.nc"))
        self.assertTrue(sp_key(2023, 2).endswith("2023020100_2023022823.nc"))


class RequiredHoursTests(unittest.TestCase):
    def test_whole_hour_event_needs_exactly_its_24_window_hours(self) -> None:
        event = _event(datetime(2021, 1, 15, 7, 0, tzinfo=LOCAL))
        hours = required_hours(event)
        end_utc = event["valid_time"].astimezone(timezone.utc)
        self.assertEqual(len(hours), 24)
        self.assertEqual(hours[-1], end_utc)
        self.assertEqual(hours[0], end_utc - timedelta(hours=23))
        self.assertTrue(all(h.minute == 0 and h.second == 0 for h in hours))

    def test_subhourly_event_needs_bracket_hours(self) -> None:
        event = _event(datetime(2021, 1, 15, 7, 45, tzinfo=LOCAL))
        hours = required_hours(event)
        start, end = event["valid_time"] - timedelta(hours=24), event["valid_time"]
        first_instant = start + timedelta(hours=1)
        self.assertEqual(len(hours), 25)
        self.assertLessEqual(hours[0], first_instant.astimezone(timezone.utc))
        self.assertGreaterEqual(hours[-1], end.astimezone(timezone.utc))
        self.assertTrue(all(h.minute == 0 for h in hours))
        self.assertEqual(
            [hours[i + 1] - hours[i] for i in range(len(hours) - 1)],
            [timedelta(hours=1)] * (len(hours) - 1),
        )


class PlanTests(unittest.TestCase):
    def test_chunk_required_hours_cover_every_event(self) -> None:
        event = _event(datetime(2021, 1, 15, 7, 45, tzinfo=LOCAL))
        chunks = plan_sp_chunks([event])
        self.assertEqual(len(chunks), 1)
        needed = {h.strftime("%Y-%m-%dT%H:%M") for h in required_hours(event)}
        self.assertEqual(set(chunks[0]["required_hours"]), needed)


class PartValidityTests(unittest.TestCase):
    def test_part_valid_roundtrip_and_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parts = Path(tmp)
            path = _part_path(parts, 2024, 1, "TEST0001")
            hours = ["2024-01-01T00:00", "2024-01-01T01:00"]
            _write_gzip_json(path, {"time": hours, "surface_pressure": [101325.0, 101300.0]})
            self.assertTrue(_part_valid(path, hours))
            self.assertFalse(_part_valid(path, ["2024-01-01T00:00"]))
            _write_gzip_json(path, {"time": hours, "surface_pressure": [101325.0, None]})
            self.assertFalse(_part_valid(path, hours))


if __name__ == "__main__":
    unittest.main()
