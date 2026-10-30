from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from slr_calibration.gold_profiles import PRESSURE_LEVELS_HPA
from slr_calibration.gold_profiles_v3 import (
    _acc_part_path,
    _acc_part_valid,
    _level_field,
    _pl_part_path,
    _pl_part_valid,
    assemble_v3_accumu,
    assemble_v3_pl,
    day_needs,
    fetch_v3_accumu,
    fetch_v3_pl,
    v3_pl_key,
)
from slr_calibration.gold_profiles_sp import _write_gzip_json, plan_sp_chunks

LOCAL = timezone(timedelta(hours=-8))


def _event(valid_time: datetime, station_id: str = "TEST0001") -> dict:
    return {
        "event_id": f"ghcn:{station_id}:{valid_time.date().isoformat()}",
        "region": "rockies",
        "station_id": station_id,
        "site_id": station_id,
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


class FakePlReader:
    """Returns a constant w field; records the days it was asked for."""

    def __init__(self, value: float = -0.25) -> None:
        self.value = value
        self.days: list[date] = []

    def read_day(self, day, stations, hours_by_station):
        self.days.append(day)
        return {
            (sid, instant): {level: self.value for level in PRESSURE_LEVELS_HPA}
            for sid, hours in hours_by_station.items()
            for instant in hours
        }


class FakeAccumuReader:
    def __init__(self, lsp: float = 0.4, cp: float = 0.1) -> None:
        self.lsp = lsp
        self.cp = cp
        self.months: list[tuple[int, int]] = []

    def read_month(self, year, month, stations, hours_by_station):
        self.months.append((year, month))
        return {
            (sid, instant): (self.lsp, self.cp)
            for sid, hours in hours_by_station.items()
            for instant in hours
        }


class V3PlKeyTests(unittest.TestCase):
    def test_key_layout_matches_an_pl_family(self) -> None:
        self.assertEqual(
            v3_pl_key(date(2024, 1, 15)),
            "e5.oper.an.pl/202401/e5.oper.an.pl.128_135_w.ll025sc.2024011500_2024011523.nc",
        )


class DayNeedsTests(unittest.TestCase):
    def test_window_spanning_midnight_utc_splits_across_days(self) -> None:
        # 07:00 local (UTC-8) = 15:00 UTC; window hours run 16:00 UTC of
        # the previous day through 15:00 UTC of the valid day.
        event = _event(datetime(2021, 1, 15, 7, 0, tzinfo=LOCAL))
        needs = day_needs(plan_sp_chunks([event]))
        self.assertEqual(sorted(needs), [date(2021, 1, 14), date(2021, 1, 15)])
        self.assertEqual(len(needs[date(2021, 1, 14)]["TEST0001"]), 8)
        self.assertEqual(len(needs[date(2021, 1, 15)]["TEST0001"]), 16)


class PlPartValidityTests(unittest.TestCase):
    def test_roundtrip_and_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parts = Path(tmp)
            hours = ["2021-01-15T00:00", "2021-01-15T01:00"]
            payload = {"time": hours}
            for level in PRESSURE_LEVELS_HPA:
                payload[_level_field(level)] = [0.1, -0.2]
            path = _pl_part_path(parts, date(2021, 1, 15), "TEST0001")
            _write_gzip_json(path, payload)
            self.assertTrue(_pl_part_valid(path, hours))
            self.assertFalse(_pl_part_valid(path, ["2021-01-15T00:00"]))
            payload[_level_field(850)] = [None, -0.2]
            _write_gzip_json(path, payload)
            self.assertFalse(_pl_part_valid(path, hours))


class PlFetchAndAssembleTests(unittest.TestCase):
    def test_fetch_writes_parts_and_assembly_covers_event(self) -> None:
        event = _event(datetime(2021, 1, 15, 7, 0, tzinfo=LOCAL))
        with tempfile.TemporaryDirectory() as tmp:
            v3_dir = Path(tmp)
            reader = FakePlReader()
            summary = fetch_v3_pl([event], v3_dir, reader=reader, max_workers=2)
            self.assertEqual(summary["daysFailed"], [])
            self.assertEqual(summary["partsWritten"], 2)  # two UTC days
            # Second run is a no-op: every part already valid.
            summary2 = fetch_v3_pl([event], v3_dir, reader=reader, max_workers=2)
            self.assertEqual(summary2["partsWritten"], 0)
            self.assertEqual(summary2["partsSkippedValid"], 2)
            coverage = assemble_v3_pl([event], v3_dir)
            self.assertEqual(coverage["eventsCovered"], 1)
            self.assertEqual(coverage["chunksWritten"], 1)
            chunk_path = next((v3_dir / "pl_chunks" / "TEST0001").glob("*.json.gz"))
            with gzip.open(chunk_path, "rt") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["variable"], "w")
            self.assertEqual(payload["hourly"][_level_field(700)], [-0.25] * 24)


class AccumuFetchAndAssembleTests(unittest.TestCase):
    def test_fetch_and_assembly_merges_cached_sf_tp(self) -> None:
        event = _event(datetime(2021, 1, 15, 7, 0, tzinfo=LOCAL))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v3_dir = root / "model-v3-features"
            reader = FakeAccumuReader()
            summary = fetch_v3_accumu([event], v3_dir, reader=reader)
            self.assertEqual(summary["monthsFailed"], [])
            self.assertEqual(summary["nullHours"], 0)
            part = _acc_part_path(v3_dir / "accumu_parts", 2021, 1, "TEST0001")
            self.assertTrue(_acc_part_valid(part, json.loads(gzip.open(part, "rt").read())["time"]))
            # Fake profile cache holding tp = 0.5 mm and sf = 0.2 cm WE
            # for exactly the event's required hours.
            from slr_calibration.gold_profiles_sp import required_hours

            hours = [h.strftime("%Y-%m-%dT%H:%M") for h in required_hours(event)]
            cache_dir = root / "raw_cache_era5"
            _write_gzip_json(
                cache_dir / "TEST0001" / "2021-01-14_2021-01-16.json.gz",
                {
                    "hourly": {
                        "time": hours,
                        "precipitation": [0.5] * len(hours),
                        "snowfall": [0.2] * len(hours),
                    }
                },
            )
            coverage = assemble_v3_accumu([event], v3_dir, cache_dir)
            self.assertEqual(coverage["eventsCovered"], 1)
            chunk_path = next((v3_dir / "accumu_chunks" / "TEST0001").glob("*.json.gz"))
            with gzip.open(chunk_path, "rt") as handle:
                payload = json.load(handle)
            hourly = payload["hourly"]
            self.assertEqual(hourly["lsp_mm"], [0.4] * 24)
            self.assertEqual(hourly["cp_mm"], [0.1] * 24)
            self.assertEqual(hourly["tp_mm"], [0.5] * 24)
            self.assertEqual(hourly["sf_mm"], [2.0] * 24)  # 0.2 cm WE -> mm
            self.assertEqual(hourly["tp_profile_cache_mm"], [0.5] * 24)


class QPlTests(unittest.TestCase):
    def test_q_key_layout(self) -> None:
        self.assertEqual(
            v3_pl_key(date(2024, 1, 15), "q"),
            "e5.oper.an.pl/202401/e5.oper.an.pl.128_133_q.ll025sc.2024011500_2024011523.nc",
        )

    def test_q_fetch_and_assemble_use_their_own_tree(self) -> None:
        event = _event(datetime(2021, 1, 15, 7, 0, tzinfo=LOCAL))
        with tempfile.TemporaryDirectory() as tmp:
            v3_dir = Path(tmp)
            reader = FakePlReader(value=0.00421)
            summary = fetch_v3_pl([event], v3_dir, reader=reader, max_workers=2, variable="q")
            self.assertEqual(summary["daysFailed"], [])
            self.assertEqual(summary["variable"], "q")
            self.assertEqual(summary["partsWritten"], 2)
            self.assertTrue((v3_dir / "pl_parts_q").exists())
            self.assertFalse((v3_dir / "pl_parts").exists())
            # Second run is a no-op: q parts are valid at q precision
            # (5-place rounding would have destroyed 0.00421's tail).
            summary2 = fetch_v3_pl([event], v3_dir, reader=reader, max_workers=2, variable="q")
            self.assertEqual(summary2["partsWritten"], 0)
            self.assertEqual(summary2["partsSkippedValid"], 2)
            coverage = assemble_v3_pl([event], v3_dir, variable="q")
            self.assertEqual(coverage["eventsCovered"], 1)
            chunk_path = next((v3_dir / "pl_chunks_q" / "TEST0001").glob("*.json.gz"))
            with gzip.open(chunk_path, "rt") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["variable"], "q")
            self.assertEqual(payload["units"], "kg/kg")
            self.assertEqual(payload["hourly"][_level_field(700, "q")], [0.00421] * 24)


if __name__ == "__main__":
    unittest.main()
