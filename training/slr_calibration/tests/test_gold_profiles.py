from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from slr_calibration.gold_profiles import (
    AGL_TARGETS_M,
    build_event_features,
    hour_agl_values,
    interpolate_agl,
    plan_chunks,
    water_year,
)

STANDARD_HEIGHTS = {
    1000: 110.0, 975: 320.0, 950: 500.0, 925: 760.0, 900: 990.0,
    850: 1450.0, 800: 1950.0, 750: 2460.0, 700: 3010.0, 650: 3590.0,
    600: 4200.0, 550: 4860.0, 500: 5570.0,
}


def _synthetic_event() -> dict:
    return {
        "event_id": "test:1",
        "region": "rockies",
        "station_id": "TEST0001",
        "site_id": "TEST0001",
        "station_name": "Test",
        "state": "CO",
        "valid_time": datetime(2023, 1, 15, 7, 0, tzinfo=timezone(timedelta(hours=-6))),
        "observation_date": "2023-01-15",
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


class InterpolateAglTest(unittest.TestCase):
    def test_linear_and_clamped(self) -> None:
        samples = [(0.0, 10.0), (1000.0, 0.0), (2000.0, -10.0)]
        values = interpolate_agl(samples, [0.0, 500.0, 1500.0, 2000.0, 3000.0, -100.0])
        self.assertEqual(values, [10.0, 5.0, -5.0, -10.0, -10.0, 10.0])

    def test_needs_two_samples(self) -> None:
        self.assertEqual(interpolate_agl([(100.0, 3.0)], [100.0, 200.0]), [None, None])
        self.assertEqual(interpolate_agl([], [100.0]), [None])


class HourAglValuesTest(unittest.TestCase):
    def test_isothermal_profile_and_span_rule(self) -> None:
        levels = [
            (float(p), -5.0, 80.0, 6.0, STANDARD_HEIGHTS[p])
            for p in sorted(STANDARD_HEIGHTS)
        ]
        result = hour_agl_values(levels, station_elevation_m=1000.0)
        self.assertIsNotNone(result)
        temps, winds, rhs = result
        self.assertEqual(temps, [-5.0] * len(AGL_TARGETS_M))
        self.assertEqual(winds, [6.0] * len(AGL_TARGETS_M))
        self.assertEqual(rhs, [80.0] * len(AGL_TARGETS_M))
        # Truncated profile that cannot reach 2100 m AGL -> invalid hour.
        shallow = [lvl for lvl in levels if lvl[0] >= 850]
        self.assertIsNone(hour_agl_values(shallow, station_elevation_m=100.0))


class BuildEventFeaturesTest(unittest.TestCase):
    def test_isothermal_profile(self) -> None:
        event = _synthetic_event()
        end = event["valid_time"]
        start = end - timedelta(hours=24)
        hours = {}
        for step in range(25):
            instant = (start + timedelta(hours=step)).astimezone(timezone.utc)
            hours[instant] = {
                "temperature2mC": -5.0,
                "precipitationMm": 1.0,
                "snowfallCm": 1.5,
                "levels": [
                    (float(p), -5.0, 80.0, 6.0, STANDARD_HEIGHTS[p])
                    for p in sorted(STANDARD_HEIGHTS)
                ],
            }
        row = build_event_features(event, hours)
        self.assertEqual(row["profile_status"], "ok")
        self.assertEqual(row["valid_hours"], 24)
        for height in AGL_TARGETS_M:
            self.assertEqual(row[f"t_{height}"], -5.0)
            self.assertEqual(row[f"ws_{height}"], 6.0)
            self.assertEqual(row[f"rh_{height}"], 80.0)
        # Isothermal -5 C column: Kuchera max temp -5 C -> 12 + (271.16 - 268.15) = 15.0.
        self.assertEqual(row["baseline_slr"], 15.0)

    def test_missing_hours(self) -> None:
        row = build_event_features(_synthetic_event(), {})
        self.assertEqual(row["profile_status"], "incomplete_profile")
        self.assertIsNone(row["baseline_slr"])
        self.assertIsNone(row["t_300"])


class PlanChunksTest(unittest.TestCase):
    def test_clusters_and_coverage_floor(self) -> None:
        def event(day: str, elevation_m: float | None = 2000.0) -> dict:
            return {
                "event_id": f"e:{day}",
                "station_id": "TEST0001",
                "valid_time": datetime.fromisoformat(f"{day}T07:00:00-06:00"),
                "observation_date": day,
                "latitude": 39.5,
                "longitude": -105.5,
                "elevation_m": elevation_m,
            }

        events = [
            event("2023-01-10"),
            event("2023-01-12"),
            event("2023-02-10"),
            event("2020-01-10"),
            event("2023-03-10", elevation_m=None),
        ]
        chunks = plan_chunks(events, gap_days=4)
        # 2020 event is below the archive coverage floor and excluded; the
        # elevation-less event has no AGL reference and is not fetched.
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["event_ids"], ["e:2023-01-10", "e:2023-01-12"])
        self.assertEqual(chunks[1]["event_ids"], ["e:2023-02-10"])

    def test_water_year(self) -> None:
        self.assertEqual(water_year(date(2025, 10, 1)), 2026)
        self.assertEqual(water_year(date(2026, 9, 30)), 2026)
        self.assertEqual(water_year(date(2026, 10, 1)), 2027)


if __name__ == "__main__":
    unittest.main()
