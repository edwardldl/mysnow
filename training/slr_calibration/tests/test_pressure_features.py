from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta, timezone

from slr_calibration.gold_profiles import PRESSURE_LEVELS_HPA, event_window
from slr_calibration.pressure_features import (
    P_CORE_FEATURES,
    P_MASK_FEATURES,
    PROFILE_FEATURES,
    build_event_pressure_features,
    sp_at,
)

LOCAL = timezone(timedelta(hours=-8))
UTC = timezone.utc


def _event() -> dict:
    return {
        "event_id": "ghcn:TEST0001:2021-01-15",
        "region": "rockies",
        "station_id": "TEST0001",
        "site_id": "TEST0001",
        "station_name": "Test",
        "state": "CO",
        "valid_time": datetime(2021, 1, 15, 7, 0, tzinfo=LOCAL),
        "observation_date": "2021-01-15",
        "water_year": 2021,
        "month": 1,
        "latitude": 39.0,
        "longitude": -106.0,
        "elevation_m": 3000.0,
        "observed_slr": 12.0,
        "fresh_snow_mm": 120.0,
        "fresh_swe_mm": 10.0,
        "climate_regime": "continental",
        "source_tier": "ghcn",
    }


def _hours(event: dict, t700: tuple[float, float] | None = None) -> dict:
    """Synthetic profile hours: t at level index j is float(j), rh 50, ws 5."""
    start, _end = event_window(event)
    hours = {}
    for step in range(1, 25):
        instant = start + timedelta(hours=step)
        levels = []
        for j, pressure in enumerate(PRESSURE_LEVELS_HPA):
            t = float(j)
            if t700 is not None and pressure == 700:
                t = t700[0] if step <= 12 else t700[1]
            levels.append((float(pressure), t, 50.0, 5.0, 100.0 * j))
        hours[instant] = {
            "temperature2mC": -5.0,
            "precipitationMm": 1.0,
            "snowfallCm": 1.0,
            "levels": levels,
        }
    return hours


def _sp_hours(event: dict, values) -> dict:
    """values: a constant Pa, or (first12, last12) tuple over window instants."""
    start, _end = event_window(event)
    out = {}
    for step in range(1, 25):
        instant = (start + timedelta(hours=step)).astimezone(UTC)
        if isinstance(values, tuple):
            out[instant] = values[0] if step <= 12 else values[1]
        else:
            out[instant] = values
    return out


class FeatureListTests(unittest.TestCase):
    def test_feature_counts_and_order(self) -> None:
        self.assertEqual(len(PROFILE_FEATURES), 39)
        self.assertEqual(len(P_CORE_FEATURES), 46)
        self.assertEqual(len(P_MASK_FEATURES), 59)
        self.assertEqual(P_CORE_FEATURES[0], "t_1000")
        self.assertEqual(P_CORE_FEATURES[13], "rh_1000")
        self.assertEqual(P_CORE_FEATURES[26], "ws_1000")
        self.assertEqual(
            P_CORE_FEATURES[39:],
            ["latitude", "longitude", "elevation_m", "surface_pressure_hpa", "month", "doy_sin", "doy_cos"],
        )
        self.assertEqual(P_MASK_FEATURES[46:], [f"valid_{p}" for p in PRESSURE_LEVELS_HPA])


class SpAtTests(unittest.TestCase):
    def test_exact_and_interpolated(self) -> None:
        base = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
        sp = {base: 100000.0, base + timedelta(hours=1): 100200.0}
        self.assertEqual(sp_at(sp, base), 100000.0)
        self.assertEqual(sp_at(sp, base + timedelta(minutes=30)), 100100.0)
        self.assertIsNone(sp_at(sp, base + timedelta(hours=2)))
        self.assertIsNone(sp_at({}, base))


class BuildEventTests(unittest.TestCase):
    def test_high_elevation_validity_and_fill(self) -> None:
        event = _event()
        row = build_event_pressure_features(
            event, _hours(event), _sp_hours(event, 68000.0), baseline=8.5
        )
        self.assertEqual(row["profile_status"], "ok")
        self.assertEqual(row["surface_pressure_hpa"], 680.0)
        self.assertEqual(row["baseline_slr"], 8.5)
        # levels with p > 680 hPa are below ground at sp = 680 hPa
        for pressure in (1000, 975, 950, 925, 900, 850, 800, 750, 700):
            self.assertEqual(row[f"valid_{pressure}"], 0, pressure)
        for pressure in (650, 600, 550, 500):
            self.assertEqual(row[f"valid_{pressure}"], 1, pressure)
        # t at level index j is j; nearest valid level to index 0 is index 9 (650 hPa)
        self.assertEqual(row["t_650"], 9.0)
        self.assertEqual(row["t_1000"], 9.0)
        self.assertEqual(row["t_500"], 12.0)
        self.assertEqual(row["rh_1000"], 50.0)
        self.assertEqual(row["ws_1000"], 5.0)
        # time features: 2021-01-15 -> doy 15
        self.assertEqual(row["month"], 1)
        angle = 2.0 * math.pi * 14 / 365.25
        self.assertAlmostEqual(row["doy_sin"], round(math.sin(angle), 6), places=6)
        self.assertAlmostEqual(row["doy_cos"], round(math.cos(angle), 6), places=6)

    def test_majority_rule_and_above_ground_only_means(self) -> None:
        event = _event()
        # sp straddles 700 hPa: 705 hPa for 12 instants (700 above ground),
        # 695 hPa for 12 (700 below ground) -> majority rule keeps it valid,
        # and its mean uses only the above-ground instants (t = 10.0 there).
        hours = _hours(event, t700=(10.0, 20.0))
        row = build_event_pressure_features(
            event, hours, _sp_hours(event, (70500.0, 69500.0)), baseline=None
        )
        self.assertEqual(row["profile_status"], "ok")
        self.assertEqual(row["valid_700"], 1)
        self.assertEqual(row["t_700"], 10.0)

    def test_missing_sp_marks_row(self) -> None:
        event = _event()
        row = build_event_pressure_features(event, _hours(event), {}, baseline=None)
        self.assertEqual(row["profile_status"], "sp_missing")
        self.assertIsNone(row["t_1000"])
        self.assertIsNone(row["surface_pressure_hpa"])
        # geography/time features are still present on incomplete rows
        self.assertEqual(row["latitude"], 39.0)
        self.assertIsNotNone(row["doy_sin"])


if __name__ == "__main__":
    unittest.main()
