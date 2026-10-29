from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq

from slr_calibration.gold_profiles import (
    AGL_TARGETS_M,
    BEST_MATCH_SPEC,
    PRESSURE_LEVELS_HPA,
    _has_subhour_offset,
    _merge_recovered_rows,
    build_event_features,
    build_event_features_offset,
    load_screened_events,
    record_at,
    run_build_features,
    run_build_features_v2,
)

STANDARD_HEIGHTS = {
    1000: 110.0, 975: 320.0, 950: 500.0, 925: 760.0, 900: 990.0,
    850: 1450.0, 800: 1950.0, 750: 2460.0, 700: 3010.0, 650: 3590.0,
    600: 4200.0, 550: 4860.0, 500: 5570.0,
}
TZ = timezone(timedelta(hours=-6))


def _event(valid_time: datetime, event_id: str = "test:1") -> dict:
    return {
        "event_id": event_id,
        "region": "rockies",
        "station_id": "TEST0001",
        "site_id": "TEST0001",
        "station_name": "Test",
        "state": "CO",
        "valid_time": valid_time,
        "observation_date": valid_time.date().isoformat(),
        "water_year": 2023,
        "month": 1,
        "latitude": 39.5,
        "longitude": -105.5,
        "elevation_m": 1000.0,
        "observed_slr": 15.0,
        "fresh_snow_mm": 150.0,
        "fresh_swe_mm": 10.0,
        "climate_regime": "continental_mountain",
        "source_tier": "ghcn-coop",
    }


def _record(temp: float, t2m: float | None = None) -> dict:
    return {
        "temperature2mC": temp if t2m is None else t2m,
        "precipitationMm": 1.0,
        "snowfallCm": 1.5,
        "levels": [
            (float(p), temp, 80.0, 6.0, STANDARD_HEIGHTS[p])
            for p in PRESSURE_LEVELS_HPA
        ],
    }


def _hours(end: datetime, temp_fn, span_before: int = 30, span_after: int = 3) -> dict:
    """Whole-hour-keyed records covering the span around ``end``."""
    hours = {}
    start = (end - timedelta(hours=span_before)).replace(minute=0, second=0, microsecond=0)
    stop = end + timedelta(hours=span_after)
    instant = start
    while instant <= stop:
        hours[instant.astimezone(timezone.utc)] = _record(temp_fn(instant))
        instant += timedelta(hours=1)
    return hours


class RecordAtTest(unittest.TestCase):
    def test_whole_hour_returns_stored_record(self) -> None:
        instant = datetime(2023, 1, 15, 7, 0, tzinfo=TZ)
        record = _record(-5.0)
        hours = {instant.astimezone(timezone.utc): record}
        self.assertIs(record_at(hours, instant), record)

    def test_half_hour_interpolates(self) -> None:
        base = datetime(2023, 1, 15, 7, 0, tzinfo=TZ)
        hours = {
            base.astimezone(timezone.utc): _record(0.0),
            (base + timedelta(hours=1)).astimezone(timezone.utc): _record(2.0),
        }
        mid = record_at(hours, base + timedelta(minutes=30))
        self.assertIsNotNone(mid)
        self.assertEqual(mid["temperature2mC"], 1.0)
        self.assertEqual(mid["levels"][0][1], 1.0)
        quarter = record_at(hours, base + timedelta(minutes=15))
        self.assertEqual(quarter["temperature2mC"], 0.5)

    def test_missing_bracket_and_none_propagation(self) -> None:
        base = datetime(2023, 1, 15, 7, 0, tzinfo=TZ)
        hours = {base.astimezone(timezone.utc): _record(0.0)}
        self.assertIsNone(record_at(hours, base + timedelta(minutes=30)))
        hi = _record(2.0)
        hi["levels"][0] = (1000.0, None, 80.0, 6.0, STANDARD_HEIGHTS[1000])
        hours[(base + timedelta(hours=1)).astimezone(timezone.utc)] = hi
        mid = record_at(hours, base + timedelta(minutes=30))
        self.assertIsNone(mid["levels"][0][1])
        self.assertEqual(mid["levels"][1][1], 1.0)


class OffsetBuilderTest(unittest.TestCase):
    def test_degenerates_to_whole_hour_builder(self) -> None:
        end = datetime(2023, 1, 15, 7, 0, tzinfo=TZ)
        hours = _hours(end, lambda _i: -5.0)
        event = _event(end)
        expected = build_event_features(event, hours)
        actual = build_event_features_offset(event, hours)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["profile_status"], "ok")
        self.assertEqual(actual["baseline_slr"], 15.0)

    def test_linear_in_time_shift(self) -> None:
        end = datetime(2023, 1, 15, 7, 0, tzinfo=TZ)
        slope = 0.25  # degC per hour, isothermal at each instant

        def temp_fn(instant: datetime) -> float:
            hours_since = (instant - end).total_seconds() / 3600.0
            return -5.0 + slope * hours_since

        hours = _hours(end, temp_fn, span_before=30, span_after=30)
        whole = build_event_features_offset(_event(end), hours)
        shifted = build_event_features_offset(_event(end + timedelta(minutes=30), "test:2"), hours)
        self.assertEqual(whole["profile_status"], "ok")
        self.assertEqual(shifted["profile_status"], "ok")
        self.assertEqual(shifted["valid_hours"], 24)
        for height in AGL_TARGETS_M:
            self.assertAlmostEqual(shifted[f"t_{height}"] - whole[f"t_{height}"], slope * 0.5, places=3)
            self.assertEqual(shifted[f"rh_{height}"], whole[f"rh_{height}"])

    def test_offset_event_invisible_to_whole_hour_builder(self) -> None:
        end = datetime(2023, 1, 15, 7, 45, tzinfo=TZ)
        hours = _hours(end, lambda _i: -5.0)
        row = build_event_features(_event(end), hours)
        self.assertEqual(row["profile_status"], "incomplete_profile")
        self.assertEqual(row["valid_hours"], 0)
        recovered = build_event_features_offset(_event(end), hours)
        self.assertEqual(recovered["profile_status"], "ok")
        self.assertEqual(recovered["t_300"], -5.0)
        self.assertEqual(recovered["baseline_slr"], 15.0)


class MergeTest(unittest.TestCase):
    def test_merge_preserves_order_and_rows(self) -> None:
        source = [{"event_id": "a", "v": 1}, {"event_id": "b", "v": 2}, {"event_id": "c", "v": 3}]
        merged = _merge_recovered_rows(source, {"b": {"event_id": "b", "v": 20}})
        self.assertEqual([row["event_id"] for row in merged], ["a", "b", "c"])
        self.assertIs(merged[0], source[0])
        self.assertEqual(merged[1]["v"], 20)
        self.assertIs(merged[2], source[2])

    def test_subhour_offset_detection(self) -> None:
        self.assertTrue(_has_subhour_offset("2023-01-15T07:45:00-06:00"))
        self.assertTrue(_has_subhour_offset("2023-01-15T07:00:30-06:00"))
        self.assertFalse(_has_subhour_offset("2023-01-15T07:00:00-06:00"))


def _write_pool(pool_dir: Path) -> None:
    header = (
        "event_id,station_id,site_id,station_name,state,valid_time,observation_date,"
        "latitude,longitude,elevation_m,observed_slr,fresh_snow_mm,fresh_swe_mm,"
        "climate_regime,source_tier\n"
    )
    rows = [
        "ghcn:TEST0001:2023-01-15,TEST0001,TEST0001,Test,CO,2023-01-15T07:00:00-06:00,2023-01-15,39.5,-105.5,1000.0,15.0,150.0,10.0,continental_mountain,ghcn-coop",
        "ghcn:TEST0001:2023-01-16,TEST0001,TEST0001,Test,CO,2023-01-16T07:45:00-06:00,2023-01-16,39.5,-105.5,1000.0,15.0,150.0,10.0,continental_mountain,ghcn-coop",
    ]
    (pool_dir / "events_rockies.csv").write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


def _write_chunk(cache_dir: Path) -> None:
    start = datetime(2023, 1, 13, 0, 0, tzinfo=TZ)
    times = [(start + timedelta(hours=i)).isoformat() for i in range(5 * 24)]
    n = len(times)
    hourly: dict[str, list] = {
        "time": times,
        "temperature_2m": [-5.0] * n,
        "precipitation": [1.0] * n,
        "snowfall": [1.5] * n,
    }
    for level in PRESSURE_LEVELS_HPA:
        hourly[f"temperature_{level}hPa"] = [-5.0] * n
        hourly[f"relative_humidity_{level}hPa"] = [80.0] * n
        hourly[f"wind_speed_{level}hPa"] = [6.0] * n
        hourly[f"geopotential_height_{level}hPa"] = [STANDARD_HEIGHTS[level]] * n
    path = cache_dir / "TEST0001" / "2023-01-13_2023-01-17.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"hourly": hourly}, handle)


class RunBuildFeaturesV2Test(unittest.TestCase):
    def test_recovers_offset_event_and_carries_other_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pool_dir = root / "pool"
            source_dir = root / "source"
            output_dir = root / "v2"
            pool_dir.mkdir()
            _write_pool(pool_dir)
            _write_chunk(source_dir / "raw_cache")

            run_build_features(pool_dir, source_dir, BEST_MATCH_SPEC)
            source_rows = pq.read_table(source_dir / "features.parquet").to_pylist()
            by_id = {row["event_id"]: row for row in source_rows}
            self.assertEqual(by_id["ghcn:TEST0001:2023-01-15"]["profile_status"], "ok")
            self.assertEqual(by_id["ghcn:TEST0001:2023-01-16"]["profile_status"], "incomplete_profile")

            recovery = run_build_features_v2(pool_dir, source_dir, output_dir, spec=BEST_MATCH_SPEC)
            self.assertEqual(recovery["targetEvents"], 1)
            self.assertEqual(recovery["recoveredEvents"], 1)
            self.assertEqual(recovery["stillFailingEvents"], 0)
            self.assertEqual(recovery["modelingRowsBefore"], 1)
            self.assertEqual(recovery["modelingRowsAfter"], 2)

            v2_rows = pq.read_table(output_dir / "features.parquet").to_pylist()
            self.assertEqual([row["event_id"] for row in v2_rows], [row["event_id"] for row in source_rows])
            v2_by_id = {row["event_id"]: row for row in v2_rows}
            # The untouched whole-hour row is carried over exactly.
            self.assertEqual(v2_by_id["ghcn:TEST0001:2023-01-15"], by_id["ghcn:TEST0001:2023-01-15"])
            recovered = v2_by_id["ghcn:TEST0001:2023-01-16"]
            self.assertEqual(recovered["profile_status"], "ok")
            self.assertEqual(recovered["valid_hours"], 24)
            self.assertEqual(recovered["t_300"], -5.0)
            self.assertEqual(recovered["baseline_slr"], 15.0)

            coverage = json.loads((output_dir / "feature_coverage.json").read_text(encoding="utf-8"))
            self.assertEqual(coverage["statusCounts"].get("ok"), 2)
            events = load_screened_events(pool_dir)
            self.assertEqual(len(events), 2)


if __name__ == "__main__":
    unittest.main()
