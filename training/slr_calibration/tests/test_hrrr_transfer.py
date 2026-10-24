from __future__ import annotations

import gzip
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from slr_calibration.gold_profiles import PRESSURE_LEVELS_HPA
from slr_calibration.hrrr_transfer import (
    TRANSFER_SOURCE_ID,
    WANTED_VARIABLES,
    PooledRangeFetcher,
    _points_cache_path,
    _read_points_cache,
    assemble_event_hours,
    build_transfer_features,
    cycle_for_instant,
    extract_cycle_points,
    file_url,
    hourly_accumulation,
    plan_transfer_files,
    station_domain,
    station_instant_record,
)

UTC = timezone.utc
STANDARD_HEIGHTS = {
    1000: 110.0, 975: 320.0, 950: 500.0, 925: 760.0, 900: 990.0,
    850: 1450.0, 800: 1950.0, 750: 2460.0, 700: 3010.0, 650: 3590.0,
    600: 4200.0, 550: 4860.0, 500: 5570.0,
}


def _event(region: str = "rockies", valid: datetime | None = None) -> dict:
    return {
        "event_id": "test:1",
        "region": region,
        "station_id": "TEST0001",
        "site_id": "TEST0001",
        "station_name": "Test",
        "state": "CO",
        "valid_time": valid or datetime(2024, 1, 15, 13, 0, tzinfo=UTC),
        "observation_date": "2024-01-15",
        "water_year": 2024,
        "month": 1,
        "latitude": 39.5,
        "longitude": -105.5,
        "elevation_m": 1000.0,
        "observed_slr": 15.0,
        "fresh_snow_mm": 150.0,
        "fresh_swe_mm": 10.0,
        "climate_regime": "continental_mountain",
        "source_tier": "ghcn",
    }


def _points_row(apcp: float = 2.0, asnow: float = 0.02) -> dict:
    row: dict[str, float | None] = {"t2m_k": 268.15, "apcp_mm": apcp, "asnow_m": asnow}
    for level in PRESSURE_LEVELS_HPA:
        row[f"t_{level}"] = 273.15 - (1000 - level) * 0.02
        row[f"rh_{level}"] = 80.0
        row[f"u_{level}"] = 3.0
        row[f"v_{level}"] = 4.0
        row[f"hgt_{level}"] = STANDARD_HEIGHTS[level]
    return row


class CycleConventionTests(unittest.TestCase):
    def test_conus_uniform_f01(self) -> None:
        cycle, fxx = cycle_for_instant(datetime(2024, 1, 15, 13, 0, tzinfo=UTC), "conus")
        self.assertEqual(cycle, datetime(2024, 1, 15, 12, 0, tzinfo=UTC))
        self.assertEqual(fxx, 1)

    def test_conus_midnight_wraps_to_previous_day(self) -> None:
        cycle, fxx = cycle_for_instant(datetime(2024, 1, 15, 0, 0, tzinfo=UTC), "conus")
        self.assertEqual(cycle, datetime(2024, 1, 14, 23, 0, tzinfo=UTC))
        self.assertEqual(fxx, 1)

    def test_alaska_three_hourly(self) -> None:
        cases = [
            (datetime(2024, 1, 15, 13, 0, tzinfo=UTC), datetime(2024, 1, 15, 12, 0, tzinfo=UTC), 1),
            (datetime(2024, 1, 15, 14, 0, tzinfo=UTC), datetime(2024, 1, 15, 12, 0, tzinfo=UTC), 2),
            (datetime(2024, 1, 15, 15, 0, tzinfo=UTC), datetime(2024, 1, 15, 12, 0, tzinfo=UTC), 3),
            (datetime(2024, 1, 15, 16, 0, tzinfo=UTC), datetime(2024, 1, 15, 15, 0, tzinfo=UTC), 1),
        ]
        for instant, want_cycle, want_fxx in cases:
            cycle, fxx = cycle_for_instant(instant, "alaska")
            self.assertEqual((cycle, fxx), (want_cycle, want_fxx))

    def test_domains(self) -> None:
        self.assertEqual(station_domain(_event("alaska")), "alaska")
        self.assertEqual(station_domain(_event("northeast")), "conus")

    def test_file_urls(self) -> None:
        cycle = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
        self.assertEqual(
            file_url("conus", cycle, 1),
            "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.20240115/conus/hrrr.t12z.wrfprsf01.grib2",
        )
        self.assertEqual(
            file_url("alaska", cycle, 3),
            "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.20240115/alaska/hrrr.t12z.wrfprsf03.ak.grib2",
        )


class PlanningTests(unittest.TestCase):
    def test_wanted_variables_cover_the_feature_spec(self) -> None:
        keys = {key for key, _var, _level in WANTED_VARIABLES}
        for level in PRESSURE_LEVELS_HPA:
            for prefix in ("t", "rh", "u", "v", "hgt"):
                self.assertIn(f"{prefix}_{level}", keys)
        self.assertTrue({"t2m_k", "apcp_mm", "asnow_m"} <= keys)
        self.assertEqual(len(WANTED_VARIABLES), 5 * len(PRESSURE_LEVELS_HPA) + 3)

    def test_plan_files_conus_event(self) -> None:
        full, acc_prev = plan_transfer_files([_event()])
        self.assertEqual(len(full), 25)  # one f01 file per window endpoint
        self.assertTrue(all(fxx == 1 for _d, _c, fxx in full))
        self.assertEqual(acc_prev, {})

    def test_plan_files_alaska_has_acc_prevs(self) -> None:
        full, acc_prev = plan_transfer_files([_event("alaska")])
        self.assertTrue(any(fxx > 1 for _d, _c, fxx in full))
        self.assertTrue(acc_prev)  # differencing support files planned


class AccumulationTests(unittest.TestCase):
    def test_f01_is_the_hourly_value(self) -> None:
        self.assertEqual(hourly_accumulation(2.5, None, 1), 2.5)

    def test_difference_and_clamp(self) -> None:
        self.assertEqual(hourly_accumulation(5.0, 3.5, 3), 1.5)
        self.assertEqual(hourly_accumulation(3.0, 3.5, 2), 0.0)

    def test_missing_poisons(self) -> None:
        self.assertIsNone(hourly_accumulation(None, 1.0, 2))
        self.assertIsNone(hourly_accumulation(2.0, None, 2))


class AssemblyTests(unittest.TestCase):
    def test_instant_record_conversions(self) -> None:
        record = station_instant_record(_points_row(), None, 1)
        assert record is not None
        self.assertAlmostEqual(record["temperature2mC"], -5.0)
        self.assertAlmostEqual(record["precipitationMm"], 2.0)
        self.assertAlmostEqual(record["snowfallCm"], 2.0)
        levels = record["levels"]
        self.assertEqual(len(levels), len(PRESSURE_LEVELS_HPA))
        _p, temp, rh, wind, height = levels[0]
        self.assertAlmostEqual(temp, 0.0)          # 1000 hPa at 273.15 K
        self.assertAlmostEqual(wind, 5.0)          # hypot(3, 4)
        self.assertAlmostEqual(height, 110.0)

    def test_instant_record_missing_row(self) -> None:
        self.assertIsNone(station_instant_record(None, None, 1))

    def test_full_event_builds_ok_from_cache(self) -> None:
        event = _event()
        row = _points_row()
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            start = event["valid_time"] - timedelta(hours=24)
            for step in range(25):
                instant = start + timedelta(hours=step)
                cycle, fxx = cycle_for_instant(instant, "conus")
                path = _points_cache_path(cache_dir, "conus", cycle, fxx, False)
                path.parent.mkdir(parents=True, exist_ok=True)
                payload = {"kind": "hrrr_transfer_points", "points": {event["station_id"]: row}}
                with gzip.open(path, "wt", encoding="utf-8") as handle:
                    json.dump(payload, handle)
            rows, coverage = build_transfer_features([event], cache_dir)
        self.assertEqual(coverage["profileSource"], TRANSFER_SOURCE_ID)
        self.assertEqual(rows[0]["profile_status"], "ok")
        self.assertIsNotNone(rows[0]["baseline_slr"])
        for column in ("t_300", "ws_2400", "rh_1500"):
            self.assertIsNotNone(rows[0][column])

    def test_missing_cache_marks_fetch_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            rows, _coverage = build_transfer_features([_event()], Path(tmp))
        self.assertEqual(rows[0]["profile_status"], "fetch_missing")

    def test_extract_cache_only_roundtrip(self) -> None:
        event = _event()
        cycle = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            path = _points_cache_path(cache_dir, "conus", cycle, 1, False)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"kind": "hrrr_transfer_points", "points": {event["station_id"]: _points_row()}}
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                json.dump(payload, handle)
            points, status = extract_cycle_points(None, cache_dir, "conus", cycle, 1, {})
            self.assertEqual(status, "cached")
            self.assertIsNotNone(points)
            points2, status2 = extract_cycle_points(
                None, cache_dir, "conus", datetime(2024, 1, 16, 12, 0, tzinfo=UTC), 1, {},
            )
            self.assertEqual((points2, status2), (None, "missing"))
            self.assertIsNotNone(_read_points_cache(path))


class PooledFetcherTests(unittest.TestCase):
    def test_get_range_unpacks_request_tuple(self) -> None:
        # Regression: _request returns (body, headers); get_range must
        # compare the BODY length, not the tuple length (a tuple has
        # len 2, which masqueraded as a "2-byte" range response).
        class Stub(PooledRangeFetcher):
            def _request(self, url, headers, method="GET"):  # noqa: ANN001, ANN202
                return b"x" * 1001, {"Content-Length": "1001"}

        fetcher = Stub()
        self.assertEqual(len(fetcher.get_range("https://example.invalid/f", 1000, 2000)), 1001)
        self.assertEqual(fetcher.get("https://example.invalid/f"), b"x" * 1001)
        self.assertEqual(fetcher.head_size("https://example.invalid/f"), 1001)


if __name__ == "__main__":
    unittest.main()
