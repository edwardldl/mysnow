from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from slr_calibration.hrrr_transfer_v3 import (
    _decode_points,
    _fetch_messages,
    _points_path_utc,
    cycle_for_instant_utc,
    file_url_utc,
    plan_transfer_files_utc,
)

UTC = timezone.utc


class UtcCycleTests(unittest.TestCase):
    def test_conus_cycle_is_utc_floored(self) -> None:
        inst = datetime(2021, 1, 5, 7, 0, tzinfo=ZoneInfo("America/Denver"))
        cycle, fxx = cycle_for_instant_utc(inst, "conus")
        self.assertEqual(cycle, datetime(2021, 1, 5, 13, tzinfo=UTC))
        self.assertEqual(fxx, 1)

    def test_same_instant_same_cycle_any_representation(self) -> None:
        # The tzbug regression: one UTC instant, two local representations.
        a = datetime(2021, 1, 5, 7, 0, tzinfo=ZoneInfo("America/Denver"))
        b = datetime(2021, 1, 5, 6, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        self.assertEqual(cycle_for_instant_utc(a, "conus"), cycle_for_instant_utc(b, "conus"))

    def test_alaska_three_hourly_in_utc(self) -> None:
        inst = datetime(2021, 6, 1, 12, 0, tzinfo=ZoneInfo("America/Anchorage"))
        cycle, fxx = cycle_for_instant_utc(inst, "alaska")
        self.assertEqual(cycle, datetime(2021, 6, 1, 18, tzinfo=UTC))
        self.assertEqual(fxx, 2)
        # 16:00 AKDT = 00:00Z next day; base 23:00Z -> cycle 21:00Z, fxx 3.
        inst2 = datetime(2021, 6, 1, 16, 0, tzinfo=ZoneInfo("America/Anchorage"))
        cycle2, fxx2 = cycle_for_instant_utc(inst2, "alaska")
        self.assertEqual((cycle2, fxx2), (datetime(2021, 6, 1, 21, tzinfo=UTC), 3))

    def test_url_and_path_use_utc(self) -> None:
        cycle = datetime(2021, 1, 5, 13, tzinfo=UTC)
        self.assertTrue(
            file_url_utc("conus", cycle, 1).endswith(
                "hrrr.20210105/conus/hrrr.t13z.wrfprsf01.grib2"
            )
        )
        self.assertTrue(
            file_url_utc("alaska", cycle, 3).endswith(
                "hrrr.20210105/alaska/hrrr.t13z.wrfprsf03.ak.grib2"
            )
        )
        path = _points_path_utc(Path("/tmp/x"), "conus", cycle, 1, True)
        self.assertTrue(str(path).endswith("conus/20210105/t13z_f01_acc.json.gz"))

    def test_plan_keys_are_utc(self) -> None:
        event = {
            "event_id": "ghcn:X:2021-01-05", "region": "rockies",
            "station_id": "X", "site_id": "X", "station_name": "", "state": "CO",
            "valid_time": datetime(2021, 1, 5, 7, 0, tzinfo=ZoneInfo("America/Denver")),
            "observation_date": "2021-01-05", "water_year": 2021, "month": 1,
            "latitude": 39.0, "longitude": -106.0, "elevation_m": 2000.0,
            "observed_slr": 10.0, "fresh_snow_mm": 100.0, "fresh_swe_mm": 10.0,
            "climate_regime": "continental", "source_tier": "test",
        }
        full, acc = plan_transfer_files_utc([event])
        self.assertTrue(all(c.tzinfo == UTC for (_d, c, _f) in full))
        self.assertEqual(len(full), 25)
        self.assertEqual(acc, {})


class FakeFetcher:
    """Minimal RangeFetcher: one idx, one 64-byte dummy file body."""

    IDX = (
        "1:0:d=2021010513:APCP:surface:0-1 hour acc fcst:\n"
        "2:32:d=2021010513:TMP:850 mb:1 hour fcst:\n"
    )

    def get(self, url: str) -> bytes:
        assert url.endswith(".idx")
        return self.IDX.encode()

    def head_size(self, url: str) -> int:
        return 64

    def get_range(self, url: str, start: int, end: int) -> bytes:
        return bytes(range(64))[start:end + 1]


class FetchToleranceTests(unittest.TestCase):
    def test_missing_variable_is_tolerated(self) -> None:
        wanted = (
            ("apcp_mm", "APCP", "surface"),
            ("clmr_850", "CLMR", "850 mb"),   # absent from the idx
            ("t_850", "TMP", "850 mb"),
        )
        messages = _fetch_messages(FakeFetcher(), "https://x/y.grib2", wanted, 1,
                                   verify_accumulations=True)
        self.assertEqual(set(messages), {"apcp_mm", "t_850"})

    def test_decode_missing_keys_become_none(self) -> None:
        wanted = (("apcp_mm", "APCP", "surface"), ("t_850", "TMP", "850 mb"))
        points = _decode_points({}, wanted, {"S1": 0})
        self.assertEqual(points, {"S1": {"apcp_mm": None, "t_850": None}})


if __name__ == "__main__":
    unittest.main()
