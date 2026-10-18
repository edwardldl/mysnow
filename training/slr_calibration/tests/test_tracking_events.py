from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from slr_calibration.ghcn import GhcnRockiesConfig, import_ghcn_rockies
from slr_calibration.ghcn_track import GHCN_DAILY_COLUMNS
from slr_calibration.tracking_events import build_direct_events, evaluate_weak_candidate

RESORT = {
    "resort_slug": "alta",
    "timezone": "America/Denver",
    "climate_regime": "continental_mountain",
    "snotel_station": {
        "station_triplet": "555:UT:SNTL",
        "station_name": "Brighton",
        "state": "UT",
        "latitude": 40.59,
        "longitude": -111.61,
        "elevation_m": 2682.3,
    },
}


def day(date: str, swe: str, depth: str, tavg: str, tmax: str, precip: str) -> dict[str, str]:
    return {
        "station_triplet": "555:UT:SNTL",
        "station_name": "Brighton",
        "state": "UT",
        "latitude": "40.59",
        "longitude": "-111.61",
        "elevation_m": "2682.3",
        "date": date,
        "swe_inches": swe,
        "snow_depth_inches": depth,
        "precip_accum_inches": precip,
        "tavg_f": tavg,
        "tmax_f": tmax,
        "tmin_f": "20.0",
    }


class WeakSnotelEventTests(unittest.TestCase):
    def test_accepts_clean_cold_storm_day(self) -> None:
        previous = day("2026-01-10", "10.0", "20.0", "28.0", "30.0", "5.0")
        current = day("2026-01-11", "10.4", "21.0", "28.0", "30.0", "5.4")
        event, reason = evaluate_weak_candidate(previous, current, RESORT)
        self.assertIsNone(reason)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["label_quality"], "weak_snotel_diff")
        self.assertEqual(event["qc_weight"], 0.25)
        self.assertAlmostEqual(event["fresh_swe_mm"], 10.16, places=2)
        self.assertAlmostEqual(event["fresh_snow_mm"], 25.4, places=2)
        self.assertAlmostEqual(event["observed_slr"], 2.5, places=4)
        self.assertIn("NOT a snow-board", event["source"])

    def test_rejects_melt_day(self) -> None:
        previous = day("2026-03-10", "10.0", "20.0", "28.0", "30.0", "5.0")
        current = day("2026-03-11", "10.4", "19.5", "28.0", "30.0", "5.4")
        event, reason = evaluate_weak_candidate(previous, current, RESORT)
        self.assertIsNone(event)
        self.assertEqual(reason, "DEPTH_DECREASED_MELT_OR_SETTLEMENT")

    def test_rejects_settlement_only_day(self) -> None:
        # Depth ticks up slightly but SWE gain is far below the 5 mm gate:
        # this is wind redistribution / rounding, not a measurable storm.
        previous = day("2026-01-10", "10.0", "20.0", "28.0", "30.0", "5.0")
        current = day("2026-01-11", "10.05", "20.2", "28.0", "30.0", "5.05")
        event, reason = evaluate_weak_candidate(previous, current, RESORT)
        self.assertIsNone(event)
        self.assertEqual(reason, "INSUFFICIENT_SWE_GAIN")

    def test_rejects_warm_day(self) -> None:
        previous = day("2026-01-10", "10.0", "20.0", "28.0", "30.0", "5.0")
        current = day("2026-01-11", "10.4", "21.0", "40.0", "42.0", "5.4")
        event, reason = evaluate_weak_candidate(previous, current, RESORT)
        self.assertIsNone(event)
        self.assertEqual(reason, "WARM_DAY")

    def test_rejects_rain_phase_tmax(self) -> None:
        previous = day("2026-01-10", "10.0", "20.0", "28.0", "30.0", "5.0")
        current = day("2026-01-11", "10.4", "21.0", "30.0", "40.0", "5.4")
        event, reason = evaluate_weak_candidate(previous, current, RESORT)
        self.assertIsNone(event)
        self.assertEqual(reason, "RAIN_PHASE_SUSPECTED_TMAX")

    def test_rejects_non_consecutive_days(self) -> None:
        previous = day("2026-01-10", "10.0", "20.0", "28.0", "30.0", "5.0")
        current = day("2026-01-12", "10.4", "21.0", "28.0", "30.0", "5.4")
        event, reason = evaluate_weak_candidate(previous, current, RESORT)
        self.assertIsNone(event)
        self.assertEqual(reason, "NON_CONSECUTIVE_DAYS")


INTERMEDIATE_ROW = {
    "station_id": "US1VTC0001",
    "station_name": "STOWE 1.0 N",
    "state": "VT",
    "latitude": "44.54",
    "longitude": "-72.79",
    "elevation_m": "450",
    "date": "2026-01-15",
    "snow_mm": "100",
    "wesf_mm": "10",
    "slr": "10",
    "obs_time": "0700",
    "is_accumulated_snow": "False",
    "is_accumulated_wesf": "False",
    "qc_weight": "1.0",
    "qc_status": "passed",
    "source_flag": "N",
}

STOWE_REGISTRY = {
    "resorts": [{
        "resort_slug": "stowe",
        "timezone": "America/New_York",
        "climate_regime": "northeast_cold_orographic",
        "snotel_station": None,
        "ghcn_stations": [{
            "station_id": "US1VTC0001", "station_name": "STOWE 1.0 N", "state": "VT",
            "latitude": 44.54, "longitude": -72.79, "elevation_m": 450.0,
            "paired_records": 25, "network": "CoCoRaHS", "distance_km": 1.2,
        }],
        "forecast_points": [],
    }],
}


class DirectGhcnParityTests(unittest.TestCase):
    def _write_intermediate(self, root: Path) -> Path:
        path = root / "ghcn_daily.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=GHCN_DAILY_COLUMNS)
            writer.writeheader()
            writer.writerow(INTERMEDIATE_ROW)
        return path

    def test_generalized_importer_accepts_non_rockies_station(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_intermediate(Path(tmp))
            # Default config: the Rockies mask rejects a Vermont station.
            accepted_default, _, _ = import_ghcn_rockies(path, GhcnRockiesConfig())
            self.assertEqual(accepted_default, [])
            # Generalized config: same logic, mask off, resort tz/regime.
            config = GhcnRockiesConfig(
                min_elevation_m=0.0,
                apply_domain_mask=False,
                timezone_name="America/New_York",
                climate_regime="northeast_cold_orographic",
                storm_group_prefix="tracking-stowe",
            )
            accepted, rejected, _ = import_ghcn_rockies(path, config)
            self.assertEqual(len(accepted), 1)
            self.assertEqual(rejected, [])
            event = accepted[0]
            self.assertEqual(event["climate_regime"], "northeast_cold_orographic")
            self.assertEqual(event["observed_slr"], 10.0)
            # January in Vermont: EST (UTC-5).
            self.assertTrue(str(event["valid_time"]).endswith("-05:00"))
            self.assertTrue(str(event["storm_group_id"]).startswith("tracking-stowe-"))

    def test_build_direct_events_parity_with_importer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_intermediate(Path(tmp))
            events, report = build_direct_events(path, STOWE_REGISTRY)
            config = GhcnRockiesConfig(
                min_elevation_m=0.0,
                apply_domain_mask=False,
                timezone_name="America/New_York",
                climate_regime="northeast_cold_orographic",
                storm_group_prefix="tracking-stowe",
            )
            expected, _, _ = import_ghcn_rockies(path, config)
        self.assertEqual(report["acceptedEventCount"], 1)
        self.assertEqual(len(events), 1)
        for key in expected[0]:
            self.assertEqual(events[0][key], expected[0][key], key)
        self.assertEqual(events[0]["label_quality"], "direct")


if __name__ == "__main__":
    unittest.main()
