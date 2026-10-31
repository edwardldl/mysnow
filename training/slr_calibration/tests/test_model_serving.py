"""Unit tests for the model-serving adapters (pure functions only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from slr_calibration.model_serving import (
    expand_hourly,
    hourly_amounts,
    interp_level_series,
    load_catalogue,
    tclw_from_hydrometeors,
    window_ends,
)


class WindowEndsTest(unittest.TestCase):
    def test_dst_transition(self):
        cycle = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
        ends = window_ends(cycle, "America/Los_Angeles", [1, 2, 3])
        # DST ends 2026-11-01 02:00 local: 07:00 on Nov 1 is PST (UTC-8).
        self.assertEqual(ends[0], datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc))
        self.assertEqual(ends[1], datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc))
        self.assertEqual(ends[2], datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc))
        ends_ny = window_ends(cycle, "America/New_York", [1, 2])
        self.assertEqual(ends_ny[0], datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(ends_ny[1], datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc))


class InterpLevelSeriesTest(unittest.TestCase):
    def test_hits_and_midpoints(self):
        src = {1000: 0.0, 500: 10.0}
        out = interp_level_series(src, targets=[1000, 850, 500, 300])
        self.assertEqual(out[1000], 0.0)
        self.assertEqual(out[500], 10.0)
        self.assertTrue(0.0 < out[850] < 10.0)
        self.assertEqual(out[300], 10.0)  # clamped outside source range


class HourlyAmountsTest(unittest.TestCase):
    def test_amounts(self):
        t0 = datetime(2026, 1, 1, 0, tzinfo=timezone.utc)
        acc = {t0: 3.0, t0 + timedelta(hours=1): 7.5, t0 + timedelta(hours=2): 7.5}
        amounts = hourly_amounts(acc)
        self.assertEqual(amounts[t0], 3.0)
        self.assertEqual(amounts[t0 + timedelta(hours=1)], 4.5)
        self.assertEqual(amounts[t0 + timedelta(hours=2)], 0.0)


class ExpandHourlyTest(unittest.TestCase):
    def test_lerps(self):
        t0 = datetime(2026, 1, 1, 0, tzinfo=timezone.utc)
        stamps = [t0, t0 + timedelta(hours=3)]
        out = expand_hourly(stamps, [0.0, 6.0])
        self.assertEqual(out[t0 + timedelta(hours=1)], 2.0)
        self.assertEqual(out[t0 + timedelta(hours=2)], 4.0)


class TclwTest(unittest.TestCase):
    def test_constant_profile(self):
        levels = [1000, 850, 700, 500]  # descending pressure
        liquid = [0.001] * 4
        heights = [100.0, 1500.0, 3000.0, 5500.0]
        expected = 0.001 * 50000.0 / 9.80665
        got = tclw_from_hydrometeors(levels, liquid, heights, elevation_m=0.0)
        self.assertAlmostEqual(got, expected, places=9)

    def test_high_surface_excludes_bottom_segment(self):
        levels = [1000, 850, 700, 500]
        liquid = [0.001] * 4
        heights = [100.0, 1500.0, 3000.0, 5500.0]
        got = tclw_from_hydrometeors(levels, liquid, heights, elevation_m=2000.0)
        # Segments 850->700 (midpoint 2250 m) and 700->500 (4250 m) qualify:
        # 350 hPa of the 500 hPa column.
        self.assertAlmostEqual(got, 0.001 * 35000.0 / 9.80665, places=9)


class LoadCatalogueTest(unittest.TestCase):
    def test_registry_shape(self):
        registry = {
            "resorts": [{
                "resort_slug": "alta",
                "pilot_name": "Alta",
                "timezone": "America/Denver",
                "state": "UT",
                "climate_regime": "continental_mountain",
                "forecast_points": [{
                    "point_id": "alta-base", "name": "Base estimate", "role": "base",
                    "latitude": 40.58, "longitude": -111.62, "elevation_m": 2605.0,
                    "geometry_source": "estimated",
                }],
            }]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(registry))
            points = load_catalogue(path)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["region"], "rockies")
        self.assertEqual(points[0]["point_id"], "alta-base")


if __name__ == "__main__":
    unittest.main()
