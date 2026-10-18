from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from slr_calibration.noaa_hrrr import (
    aggregate_station_days,
    assemble_station_hours,
    compare_daily_rows,
    hourly_increments,
    nearest_grid_indices,
    paired_stats,
    parse_accumulation_span,
    parse_idx,
    plan_ranges,
)

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

IDX_TEXT = """\
1:0:d=2024121500:PRATE:surface:0-1 hour acc fcst:
2:1000:d=2024121500:TMP:2 m above ground:1 hour fcst:
3:2500:d=2024121500:ASNOW:surface:0-1 hour acc fcst:
"""

STATION = {
    "station_triplet": "999:UT:SNTL",
    "station_name": "Test Station",
    "state": "UT",
    "latitude": 40.0,
    "longitude": -111.0,
    "elevation_m": 2000.0,
    "data_tz_offset_hours": 0.0,
    "nearest_resort": "Test Resort",
    "nearest_resort_region": "Utah",
    "nearest_resort_country": "United States",
    "distance_km": 1.0,
}


class ParseIdxTests(unittest.TestCase):
    def test_entries_spans_and_forecast(self) -> None:
        entries = parse_idx(IDX_TEXT)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["var"], "PRATE")
        self.assertEqual(entries[0]["end"], 999)
        self.assertEqual(entries[1]["level"], "2 m above ground")
        self.assertEqual(entries[1]["end"], 2499)
        self.assertIsNone(entries[2]["end"])
        self.assertEqual(entries[2]["forecast"], "0-1 hour acc fcst:")


class PlanRangesTests(unittest.TestCase):
    def test_adjacent_ranges_merge_and_last_entry_uses_file_size(self) -> None:
        entries = parse_idx(IDX_TEXT)
        ranges = plan_ranges(
            entries,
            [("t2m", "TMP", "2 m above ground"), ("asnow", "ASNOW", "surface")],
            file_size=4000,
        )
        self.assertEqual(len(ranges), 1)
        self.assertEqual((ranges[0]["start"], ranges[0]["end"]), (1000, 3999))
        self.assertEqual(ranges[0]["keys"], ["t2m", "asnow"])
        self.assertEqual(ranges[0]["spans"], [(1000, 2499, "t2m"), (2500, 3999, "asnow")])

    def test_non_adjacent_ranges_stay_separate(self) -> None:
        entries = parse_idx(IDX_TEXT)
        ranges = plan_ranges(
            entries,
            [("prate", "PRATE", "surface"), ("asnow", "ASNOW", "surface")],
            file_size=4000,
        )
        self.assertEqual(len(ranges), 2)
        self.assertEqual((ranges[0]["start"], ranges[0]["end"]), (0, 999))

    def test_missing_variable_raises(self) -> None:
        entries = parse_idx(IDX_TEXT)
        with self.assertRaises(ValueError):
            plan_ranges(entries, [("x", "NOPE", "surface")], file_size=4000)


class AccumulationSpanTests(unittest.TestCase):
    def test_run_accumulated_descriptor(self) -> None:
        self.assertEqual(parse_accumulation_span("0-7 hour acc fcst:"), (0, 7))
        self.assertEqual(parse_accumulation_span("0-1 hour acc fcst"), (0, 1))

    def test_instantaneous_descriptor(self) -> None:
        self.assertIsNone(parse_accumulation_span("7 hour fcst:"))
        self.assertIsNone(parse_accumulation_span("entire atmosphere: 7 hour fcst"))


class HourlyIncrementsTests(unittest.TestCase):
    def test_differencing_missing_propagation_and_clamp(self) -> None:
        series = {1: 0.5, 2: 1.5, 3: None, 4: 3.0, 5: 2.9}
        increments, clamps = hourly_increments(series)
        self.assertEqual(increments[1], 0.5)  # against the run's zero baseline
        self.assertEqual(increments[2], 1.0)
        self.assertIsNone(increments[3])
        self.assertIsNone(increments[4])  # previous value missing: would span a gap
        self.assertEqual(increments[5], 0.0)  # small negative clamped
        self.assertEqual(clamps, 1)


class AssembleStationHoursTests(unittest.TestCase):
    def test_increments_units_levels_and_lead(self) -> None:
        triplet = STATION["station_triplet"]
        file_points = {
            ("2024-12-01", 1, "wrfsfc"): {triplet: {"asnow_m": 0.01, "apcp_mm": 1.0, "t2m_k": 270.0}},
            ("2024-12-01", 2, "wrfsfc"): {triplet: {"asnow_m": 0.035, "apcp_mm": 3.5, "t2m_k": 268.0}},
            ("2024-12-01", 1, "wrfprs"): {triplet: {"t_850": 265.0, "hgt_850": 1500.0}},
            ("2024-12-01", 2, "wrfprs"): {triplet: {"t_850": 264.0, "hgt_850": 1501.0}},
        }
        hours, stats = assemble_station_hours(file_points, [date(2024, 12, 1)], triplet)
        self.assertEqual(len(hours), 2)
        self.assertEqual(stats["missing_hours"], 22)
        first = hours[datetime(2024, 12, 1, 1, tzinfo=timezone.utc)]
        self.assertAlmostEqual(first["snowfallCm"], 1.0)
        self.assertAlmostEqual(first["precipitationMm"], 1.0)
        self.assertAlmostEqual(first["temperature2mC"], 270.0 - 273.15)
        self.assertEqual(first["lead_h"], 1)
        self.assertEqual(len(first["levels"]), 10)  # profiles.PRESSURE_LEVELS_HPA subset
        level850 = next(level for level in first["levels"] if level["pressureHpa"] == 850)
        self.assertAlmostEqual(level850["temperatureC"], 265.0 - 273.15)
        self.assertEqual(level850["geopotentialHeightM"], 1500.0)
        second = hours[datetime(2024, 12, 1, 2, tzinfo=timezone.utc)]
        self.assertAlmostEqual(second["snowfallCm"], 2.5)
        self.assertAlmostEqual(second["precipitationMm"], 2.5)


def _hour(instant: datetime, lead: int = 1) -> dict:
    return {
        "time": instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snowfallCm": 2.0,
        "precipitationMm": 2.0,
        "temperature2mC": -5.0,
        "levels": [
            {"pressureHpa": 700, "temperatureC": -8.0, "geopotentialHeightM": 3000.0},
            {"pressureHpa": 500, "temperatureC": -20.0, "geopotentialHeightM": 5500.0},
        ],
        "lead_h": lead,
    }


class AggregateStationDaysTests(unittest.TestCase):
    def test_daily_sums_t2m_and_kuchera(self) -> None:
        start = datetime(2024, 12, 1, tzinfo=timezone.utc)
        hours = {start + timedelta(hours=k): _hour(start + timedelta(hours=k), lead=(k % 24) or 24) for k in range(25)}
        aggregated = aggregate_station_days(hours, STATION, [date(2024, 12, 1)])
        day = aggregated["2024-12-01"]
        self.assertEqual(day["hrrr_hours"], 24)
        self.assertAlmostEqual(day["fcst_snowfall_cm"], 48.0)
        self.assertAlmostEqual(day["fcst_precip_mm"], 48.0)
        self.assertAlmostEqual(day["fcst_t2m_mean_c"], -5.0)
        self.assertAlmostEqual(day["fcst_t2m_min_c"], -5.0)
        self.assertAlmostEqual(day["fcst_t2m_max_c"], -5.0)
        # Max above-ground layer temperature -8 C -> Kuchera 12 + (271.16 - 265.15) -> 18.0
        self.assertAlmostEqual(day["fcst_kuchera_slr"], 18.0)
        self.assertEqual(day["fcst_kuchera_hours"], 24)
        self.assertAlmostEqual(day["fcst_kuchera_weight_mm"], 48.0)
        self.assertIsNone(day["fcst_freezing_level_m"])

    def test_local_day_window_uses_station_offset(self) -> None:
        station = dict(STATION, data_tz_offset_hours=-8.0)
        # Local Dec 1 at UTC-8 spans 08:00Z Dec 1 .. 08:00Z Dec 2.
        start = datetime(2024, 12, 1, 8, tzinfo=timezone.utc)
        hours = {start + timedelta(hours=k): _hour(start + timedelta(hours=k)) for k in range(25)}
        aggregated = aggregate_station_days(hours, station, [date(2024, 12, 1)])
        self.assertEqual(aggregated["2024-12-01"]["hrrr_hours"], 24)
        # The same instants are not a full UTC day: only the 17 endpoints
        # from 08:00Z Dec 1 through 00:00Z Dec 2 fall in the UTC window.
        aggregated_utc = aggregate_station_days(hours, STATION, [date(2024, 12, 1)])
        self.assertEqual(aggregated_utc["2024-12-01"]["hrrr_hours"], 17)


class PairedStatsTests(unittest.TestCase):
    def test_bias_mae_correlation(self) -> None:
        stats = paired_stats([(1.0, 1.0), (2.0, 2.0), (4.0, 3.0)])
        self.assertEqual(stats["n"], 3)
        self.assertAlmostEqual(stats["mean_bias"], 1.0 / 3.0, places=4)
        self.assertAlmostEqual(stats["mae"], 1.0 / 3.0, places=4)
        self.assertAlmostEqual(stats["correlation"], 0.982, places=3)

    def test_empty(self) -> None:
        self.assertEqual(paired_stats([]), {"n": 0, "mean_bias": None, "mae": None, "correlation": None})


class CompareDailyRowsTests(unittest.TestCase):
    def test_head_to_head_and_vs_observations(self) -> None:
        triplet = STATION["station_triplet"]
        om_rows = [
            {"station_triplet": triplet, "date": "2024-12-01", "fcst_snowfall_cm": 8.0,
             "fcst_precip_mm": 10.0, "fcst_kuchera_slr": None, "weak_qc_passed": True, "delta_depth_mm": 100.0},
            {"station_triplet": triplet, "date": "2024-12-02", "fcst_snowfall_cm": 0.0,
             "fcst_precip_mm": 0.0, "fcst_kuchera_slr": None, "weak_qc_passed": False, "delta_depth_mm": None},
            {"station_triplet": triplet, "date": "2024-12-03", "fcst_snowfall_cm": 20.0,
             "fcst_precip_mm": 25.0, "fcst_kuchera_slr": 15.0, "weak_qc_passed": True, "delta_depth_mm": 200.0},
        ]
        noaa_rows = [
            {"station_triplet": triplet, "date": "2024-12-01", "fcst_snowfall_cm": 12.0,
             "fcst_precip_mm": 12.0, "fcst_kuchera_slr": 14.0},
            {"station_triplet": triplet, "date": "2024-12-02", "fcst_snowfall_cm": 1.0,
             "fcst_precip_mm": 1.0, "fcst_kuchera_slr": None},
            {"station_triplet": triplet, "date": "2024-12-03", "fcst_snowfall_cm": 18.0,
             "fcst_precip_mm": 22.0, "fcst_kuchera_slr": 16.0},
        ]
        report = compare_daily_rows(noaa_rows, om_rows)
        self.assertEqual(report["shared_station_days"], 3)
        snowfall = report["noaa_vs_open_meteo"]["snowfall_cm"]
        self.assertEqual(snowfall["n"], 3)
        self.assertAlmostEqual(snowfall["mean_bias"], 1.0)
        self.assertAlmostEqual(snowfall["mae"], 7.0 / 3.0, places=4)
        kuchera = report["noaa_vs_open_meteo"]["kuchera_slr"]
        self.assertEqual(kuchera["n"], 1)
        self.assertAlmostEqual(kuchera["mean_bias"], 1.0)
        noaa_obs = report["vs_snotel_observations_qc_passed"]["noaa"]
        self.assertEqual(noaa_obs["n"], 2)
        self.assertAlmostEqual(noaa_obs["mean_bias"], 0.0)
        self.assertAlmostEqual(noaa_obs["mae"], 2.0)
        om_obs = report["vs_snotel_observations_qc_passed"]["open_meteo"]
        self.assertEqual(om_obs["n"], 2)
        self.assertAlmostEqual(om_obs["mean_bias"], -1.0)
        self.assertAlmostEqual(om_obs["mae"], 1.0)


@unittest.skipUnless(np is not None, "numpy not installed")
class NearestGridIndicesTests(unittest.TestCase):
    def test_nearest_with_0360_longitudes(self) -> None:
        latitudes = np.array([40.0, 40.0, 41.0, 41.0])
        longitudes = np.array([250.0, 251.0, 250.0, 251.0])
        station = dict(STATION, latitude=40.9, longitude=-109.2)  # 250.8 in 0-360
        indices = nearest_grid_indices(latitudes, longitudes, [station])
        self.assertEqual(indices[STATION["station_triplet"]], 3)


if __name__ == "__main__":
    unittest.main()
