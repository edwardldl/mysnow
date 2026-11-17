"""Unit tests for resort-report parsing, alignment, and aggregation."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from slr_calibration.resort_reports import (
    aggregate_errors,
    comparison_row,
    parse_inches_range,
    parse_snocountry_item,
    select_forecast_vintage,
)


class ParseInchesRangeTest(unittest.TestCase):
    def test_empty_means_not_reported(self):
        self.assertEqual(parse_inches_range(""), (None, None))
        self.assertEqual(parse_inches_range(None), (None, None))
        self.assertEqual(parse_inches_range("  "), (None, None))
        self.assertEqual(parse_inches_range("n/a"), (None, None))

    def test_reported_zero_is_a_real_observation(self):
        self.assertEqual(parse_inches_range("0"), (0.0, 0.0))
        self.assertEqual(parse_inches_range(0), (0.0, 0.0))

    def test_single_and_range(self):
        self.assertEqual(parse_inches_range("10"), (10.0, 10.0))
        self.assertEqual(parse_inches_range("13-15"), (13.0, 15.0))
        self.assertEqual(parse_inches_range("15 - 13"), (13.0, 15.0))
        self.assertEqual(parse_inches_range(2.5), (2.5, 2.5))


def _item(**overrides):
    item = {
        "id": "801001",
        "resortName": "Alta",
        "reportDateTime": "2026-11-16 06:37:39",
        "newSnowMin": "4",
        "newSnowMax": "6",
        "snowLast48Hours": "13-15",
        "avgBaseDepthMin": "20",
        "avgBaseDepthMax": "30",
        "operatingStatus": "Plan to Open 11/20",
        "resortStatus": "5",
        "primarySurfaceCondition": "Packed Powder",
    }
    item.update(overrides)
    return item


def _parse(item, tz="America/Denver"):
    return parse_snocountry_item(
        item,
        resort_slug="alta",
        resort_name="Alta",
        timezone_name=tz,
        collected_at_utc=datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc),
        raw_ref="raw/2026-11-16/alta.json",
    )


class ParseSnoCountryItemTest(unittest.TestCase):
    def test_inches_convert_to_cm(self):
        record = _parse(_item())
        self.assertIsNotNone(record)
        self.assertEqual(record["new_snow_24h_cm"], {"min": 10.16, "max": 15.24, "mid": 12.7})
        self.assertEqual(record["snow_48h_cm"]["mid"], 35.56)
        self.assertEqual(record["base_depth_cm"]["mid"], 63.5)

    def test_alignment_uses_resort_local_date(self):
        record = _parse(_item())
        self.assertEqual(record["window_end_local_date"], "2026-11-16")
        # 06:37 in America/Denver (MST, UTC-7 in November).
        self.assertTrue(record["report_datetime_local"].startswith("2026-11-16T06:37:39-07:00"))

    def test_same_utc_instant_different_local_dates(self):
        # 04:30 local in Denver is still the previous evening in Los Angeles;
        # each resort aligns to its own local date.
        denver = _parse(_item(reportDateTime="2026-11-16 04:30:00"), tz="America/Denver")
        la = _parse(_item(reportDateTime="2026-11-16 04:30:00"), tz="America/Los_Angeles")
        self.assertEqual(denver["window_end_local_date"], "2026-11-16")
        self.assertEqual(la["window_end_local_date"], "2026-11-16")
        early_la = _parse(_item(reportDateTime="2026-11-15 23:30:00"), tz="America/Los_Angeles")
        self.assertEqual(early_la["window_end_local_date"], "2026-11-15")

    def test_blank_snow_fields_parse_to_none_but_record_kept(self):
        record = _parse(_item(newSnowMin="", newSnowMax="", snowLast48Hours=""))
        self.assertIsNotNone(record)
        self.assertIsNone(record["new_snow_24h_cm"]["mid"])
        self.assertIsNone(record["snow_48h_cm"]["mid"])

    def test_one_sided_range(self):
        record = _parse(_item(newSnowMin="", newSnowMax="8"))
        self.assertEqual(record["new_snow_24h_cm"]["mid"], 20.32)

    def test_missing_timestamp_returns_none(self):
        self.assertIsNone(_parse(_item(reportDateTime="")))
        self.assertIsNone(_parse(_item(reportDateTime="not a date")))


class SelectForecastVintageTest(unittest.TestCase):
    WINDOW_START = "2026-11-16T15:00:00Z"

    def _record(self, cycle, snowfall=5.0, **overrides):
        record = {
            "cycle_utc": cycle,
            "model_snowfall_cm": snowfall,
            "kuchera_snowfall_cm": 4.0,
            "missing": False,
        }
        record.update(overrides)
        return record

    def test_picks_last_cycle_before_window_start(self):
        records = [
            self._record("2026-11-15T00:00:00Z", 1.0),
            self._record("2026-11-16T12:00:00Z", 6.79),
            self._record("2026-11-15T12:00:00Z", 3.0),
        ]
        chosen = select_forecast_vintage(records, self.WINDOW_START)
        self.assertEqual(chosen["cycle_utc"], "2026-11-16T12:00:00Z")

    def test_cycle_at_or_after_window_start_never_used(self):
        records = [
            self._record("2026-11-16T15:00:00Z", 9.0),  # at window start
            self._record("2026-11-16T18:00:00Z", 9.0),  # inside the window
        ]
        self.assertIsNone(select_forecast_vintage(records, self.WINDOW_START))

    def test_missing_and_null_records_skipped(self):
        records = [
            self._record("2026-11-16T12:00:00Z", None),
            self._record("2026-11-16T06:00:00Z", 2.0, missing=True),
            self._record("2026-11-15T12:00:00Z", 3.0),
        ]
        chosen = select_forecast_vintage(records, self.WINDOW_START)
        self.assertEqual(chosen["cycle_utc"], "2026-11-15T12:00:00Z")

    def test_frontend_style_keys_accepted(self):
        records = [
            {"cycleUtc": "2026-11-16T12:00:00Z", "model_snowfall_cm": 6.79, "missing": False}
        ]
        chosen = select_forecast_vintage(records, self.WINDOW_START)
        self.assertEqual(chosen["cycleUtc"], "2026-11-16T12:00:00Z")


class ComparisonAndAggregateTest(unittest.TestCase):
    def test_comparison_row_errors(self):
        report = _parse(_item())
        forecast = {
            "cycle_utc": "2026-11-15T12:00:00Z",
            "lead_day": 2,
            "model_snowfall_cm": 10.0,
            "kuchera_snowfall_cm": 15.0,
        }
        row = comparison_row(report, forecast, role="mid_mountain", arm="ecmwf")
        self.assertEqual(row["reported_cm"], 12.7)
        self.assertEqual(row["err_ens_eb_cm"], 2.7)
        self.assertEqual(row["err_kuchera_cm"], -2.3)

    def test_aggregate_errors(self):
        rows = [
            {"err_ens_eb_cm": 2.0},
            {"err_ens_eb_cm": -4.0},
            {"err_ens_eb_cm": None},
            {"err_ens_eb_cm": 3.0},
        ]
        stats = aggregate_errors(rows, "err_ens_eb_cm")
        self.assertEqual(stats["n"], 3)
        self.assertAlmostEqual(stats["bias_cm"], 0.33, places=2)
        self.assertAlmostEqual(stats["mae_cm"], 3.0, places=2)
        self.assertEqual(aggregate_errors([], "err_ens_eb_cm")["n"], 0)


if __name__ == "__main__":
    unittest.main()
