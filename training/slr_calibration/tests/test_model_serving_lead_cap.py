"""Unit tests for the served-lead cap guard (10-day cap, 2026-11-10).

The cap (user decision 2026-11-10: "only do 10 days for all forecast
across all surfaces") is enforced on the data side by
``frontend_lead_violations`` / ``assert_frontend_within_lead_cap`` in
``slr_calibration.model_serving``: every serving write path asserts
before publishing, and the retired Open-Meteo merge refuses a frontend
that breaches the cap, so a future batch cannot reintroduce the
leads 11-14 tail.
"""

from __future__ import annotations

import unittest

from slr_calibration.model_serving import (
    MAX_SERVED_LEAD_DAY,
    assert_frontend_within_lead_cap,
    frontend_lead_violations,
)


def _frontend(rows_by_point: dict[str, dict[str, list[int]]]) -> dict:
    return {
        "points": [
            {
                "pointId": point_id,
                "forecasts": {
                    arm: [{"leadDay": lead} for lead in leads]
                    for arm, leads in arms.items()
                },
            }
            for point_id, arms in rows_by_point.items()
        ]
    }


class FrontendLeadCapTest(unittest.TestCase):
    def test_cap_is_ten_days(self):
        self.assertEqual(MAX_SERVED_LEAD_DAY, 10)

    def test_records_at_or_below_the_cap_pass(self):
        frontend = _frontend({
            "palisades-tahoe-base": {"ecmwf": [2, 9, 10], "hrrr": [2]},
            "stowe-base": {"ecmwf": [10]},
        })
        self.assertEqual(frontend_lead_violations(frontend), [])
        assert_frontend_within_lead_cap(frontend)  # must not raise

    def test_tail_records_are_flagged_with_point_arm_and_lead(self):
        frontend = _frontend({
            "palisades-tahoe-base": {"ecmwf": [10], "ecmwf_openmeteo": [11, 14]},
            "stowe-base": {"ecmwf_openmeteo": [12]},
        })
        self.assertEqual(
            frontend_lead_violations(frontend),
            [
                ("palisades-tahoe-base", "ecmwf_openmeteo", 11),
                ("palisades-tahoe-base", "ecmwf_openmeteo", 14),
                ("stowe-base", "ecmwf_openmeteo", 12),
            ],
        )
        with self.assertRaises(ValueError):
            assert_frontend_within_lead_cap(frontend)

    def test_beyond_cap_rows_in_a_grib_arm_are_flagged_too(self):
        frontend = _frontend({"a-base": {"ecmwf": [11]}})
        self.assertEqual(frontend_lead_violations(frontend), [("a-base", "ecmwf", 11)])

    def test_empty_and_missing_sections_are_clean(self):
        self.assertEqual(frontend_lead_violations({"points": []}), [])
        self.assertEqual(frontend_lead_violations({}), [])
        assert_frontend_within_lead_cap({"points": [{"pointId": "x", "forecasts": {}}]})

    def test_a_stricter_cap_can_be_requested(self):
        frontend = _frontend({"a-base": {"ecmwf": [9, 10]}})
        self.assertEqual(
            frontend_lead_violations(frontend, max_lead_day=9),
            [("a-base", "ecmwf", 10)],
        )


if __name__ == "__main__":
    unittest.main()
