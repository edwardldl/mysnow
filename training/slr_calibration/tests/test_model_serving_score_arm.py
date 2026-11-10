"""Regression tests for score_arm's HRRR domain-exclusion handling.

The serving runner (scripts/run_model_serving.py) marks domain-excluded
points with a series-level domain_missing_reason; score_arm must report
that reason even for leads whose windows would ALSO fail the horizon
check (the domain exclusion is the persistent reason). Codex review
2026-11-10.
"""
import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "run_model_serving", ROOT / "scripts" / "run_model_serving.py")
run_model_serving = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_model_serving)

from slr_calibration.model_serving import (  # noqa: E402
    HRRR_POLICY_REASON,
    SOURCE_ID_HRRR,
)

CYCLE = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)


def _point(point_id="tremblant-base"):
    return {
        "point_id": point_id, "resort_slug": "tremblant",
        "resort_name": "Tremblant", "role": "base",
        "point_name": "Base estimate", "latitude": 46.21,
        "longitude": -74.57, "elevation_m": 227.0,
        "timezone": "America/Toronto", "geometry_source": "estimated",
        "region": "northeast",
    }


def _marker_series(reason):
    # first/last deliberately far from the scored windows, so the
    # horizon branch would fire if the domain marker did not win.
    return {
        "domain_missing_reason": reason,
        "profile_hours": {}, "w": {}, "q": {}, "accumu": {}, "sfc": {},
        "first": CYCLE + timedelta(days=10),
        "last": CYCLE + timedelta(days=11),
    }


class ScoreArmDomainExclusionTest(unittest.TestCase):
    def test_domain_reason_wins_over_horizon(self):
        point = _point()
        series = {point["point_id"]: _marker_series(HRRR_POLICY_REASON)}
        records, features = run_model_serving.score_arm(
            "hrrr", SOURCE_ID_HRRR, [1, 2], CYCLE, [point], series,
            model=None, sidecar_features=[])
        self.assertEqual(features, [])
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertTrue(record["missing"])
            self.assertEqual(record["missing_reason"], HRRR_POLICY_REASON)


if __name__ == "__main__":
    unittest.main()
