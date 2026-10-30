from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from slr_calibration.gold_profiles_sp import _write_gzip_json
from slr_calibration.gold_profiles_v3_sfc import (
    SFC_FIELDS,
    V3_SFC_VARIABLES,
    _sfc_part_path,
    _sfc_part_valid,
    assemble_v3_sfc,
    fetch_v3_sfc,
    v3_sfc_key,
)

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


class FakeSfcReader:
    """Constant values per token; records the months it was asked for."""

    def __init__(self, values: dict | None = None) -> None:
        self.values = values or {
            "2d": 268.0,
            "tclw": 0.25,
            "10u": 3.0,
            "10v": -1.5,
            "skt": 271.0,
        }
        self.months: list[tuple[int, int]] = []

    def read_month(self, year, month, stations, hours_by_station):
        self.months.append((year, month))
        return {
            (sid, instant): dict(self.values)
            for sid, hours in hours_by_station.items()
            for instant in hours
        }


class V3SfcKeyTests(unittest.TestCase):
    def test_key_layout_matches_an_sfc_family(self) -> None:
        self.assertEqual(
            v3_sfc_key(2024, 1, "2d"),
            "e5.oper.an.sfc/202401/e5.oper.an.sfc.128_168_2d.ll025sc.2024010100_2024013123.nc",
        )
        self.assertEqual(
            v3_sfc_key(2024, 1, "tclw"),
            "e5.oper.an.sfc/202401/e5.oper.an.sfc.128_078_tclw.ll025sc.2024010100_2024013123.nc",
        )
        self.assertEqual(
            v3_sfc_key(2024, 1, "10u"),
            "e5.oper.an.sfc/202401/e5.oper.an.sfc.128_165_10u.ll025sc.2024010100_2024013123.nc",
        )
        self.assertEqual(
            v3_sfc_key(2024, 1, "10v"),
            "e5.oper.an.sfc/202401/e5.oper.an.sfc.128_166_10v.ll025sc.2024010100_2024013123.nc",
        )
        self.assertEqual(
            v3_sfc_key(2024, 1, "skt"),
            "e5.oper.an.sfc/202401/e5.oper.an.sfc.128_235_skt.ll025sc.2024010100_2024013123.nc",
        )

    def test_key_last_day_handles_february(self) -> None:
        self.assertIn("2020020100_2020022923", v3_sfc_key(2020, 2, "skt"))  # leap
        self.assertIn("2021020100_2021022823", v3_sfc_key(2021, 2, "skt"))  # non-leap

    def test_all_five_variables_have_distinct_fields(self) -> None:
        self.assertEqual(len(V3_SFC_VARIABLES), 5)
        self.assertEqual(len(set(SFC_FIELDS)), 5)


class SfcPartValidityTests(unittest.TestCase):
    def test_roundtrip_and_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parts = Path(tmp)
            hours = ["2021-01-15T00:00", "2021-01-15T01:00"]
            payload = {"time": hours}
            for field in SFC_FIELDS:
                payload[field] = [1.0, 2.0]
            path = _sfc_part_path(parts, 2021, 1, "TEST0001")
            _write_gzip_json(path, payload)
            self.assertTrue(_sfc_part_valid(path, hours))
            self.assertFalse(_sfc_part_valid(path, ["2021-01-15T00:00"]))
            payload["dewpoint_2m"] = [None, 2.0]
            _write_gzip_json(path, payload)
            self.assertFalse(_sfc_part_valid(path, hours))
            payload["dewpoint_2m"] = [1.0, 2.0]
            del payload["skin_temperature"]
            _write_gzip_json(path, payload)
            self.assertFalse(_sfc_part_valid(path, hours))


class SfcFetchAndAssembleTests(unittest.TestCase):
    def test_fetch_writes_parts_and_assembly_covers_event(self) -> None:
        event = _event(datetime(2021, 1, 15, 7, 0, tzinfo=LOCAL))
        with tempfile.TemporaryDirectory() as tmp:
            v3_dir = Path(tmp)
            reader = FakeSfcReader()
            summary = fetch_v3_sfc([event], v3_dir, reader=reader)
            self.assertEqual(summary["monthsFailed"], [])
            # All required hours fall inside January 2021 (UTC).
            self.assertEqual(summary["partsWritten"], 1)
            self.assertEqual(reader.months, [(2021, 1)])
            part = _sfc_part_path(v3_dir / "sfc_parts", 2021, 1, "TEST0001")
            with gzip.open(part, "rt") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["dewpoint_2m"], [268.0] * len(payload["time"]))
            self.assertEqual(payload["total_column_liquid_water"], [0.25] * len(payload["time"]))
            # Second run is a no-op: the part is already valid.
            summary2 = fetch_v3_sfc([event], v3_dir, reader=FakeSfcReader())
            self.assertEqual(summary2["partsWritten"], 0)
            self.assertEqual(summary2["partsSkippedValid"], 1)
            coverage = assemble_v3_sfc([event], v3_dir)
            self.assertEqual(coverage["eventsCovered"], 1)
            self.assertEqual(coverage["chunksWritten"], 1)
            chunk_path = next((v3_dir / "sfc_chunks" / "TEST0001").glob("*.json.gz"))
            with gzip.open(chunk_path, "rt") as handle:
                chunk = json.load(handle)
            hourly = chunk["hourly"]
            self.assertEqual(hourly["skin_temperature"], [271.0] * len(hourly["time"]))
            self.assertEqual(hourly["wind_u_10m"], [3.0] * len(hourly["time"]))
            self.assertEqual(hourly["wind_v_10m"], [-1.5] * len(hourly["time"]))
            self.assertEqual(chunk["units"]["total_column_liquid_water"], "kg/m^2")

    def test_failed_month_is_reported_not_written(self) -> None:
        class BrokenReader:
            def read_month(self, year, month, stations, hours_by_station):
                raise OSError("boom")

        event = _event(datetime(2021, 1, 15, 7, 0, tzinfo=LOCAL))
        with tempfile.TemporaryDirectory() as tmp:
            v3_dir = Path(tmp)
            summary = fetch_v3_sfc([event], v3_dir, reader=BrokenReader())
            self.assertEqual(summary["partsWritten"], 0)
            self.assertEqual(len(summary["monthsFailed"]), 1)
            self.assertEqual(summary["monthsFailed"][0]["month"], "202101")
            coverage = assemble_v3_sfc([event], v3_dir)
            self.assertEqual(coverage["eventsCovered"], 0)
            self.assertEqual(coverage["chunksWritten"], 0)


if __name__ == "__main__":
    unittest.main()
