import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_model_serving_blob import describe_frontend, regressed_arms


def record(end: str) -> dict:
    return {"windowEndUtc": end}


SAMPLE = {
    "cycles": {"ecmwf": "2026-11-15T12:00:00Z", "hrrr": "2026-11-15T12:00:00Z"},
    "points": [
        {"forecasts": {"ecmwf": [record("2026-11-16T15:00:00Z"), record("2026-11-17T15:00:00Z")],
                       "hrrr": [record("2026-11-16T15:00:00Z")]}},
        {"forecasts": {"ecmwf": [record("2026-11-25T15:00:00Z")], "hrrr": []}},
    ],
}


class DescribeFrontendTest(unittest.TestCase):
    def test_counts_records_across_points_and_arms(self) -> None:
        cycle, count, start, end = describe_frontend(SAMPLE)
        self.assertEqual(cycle, "2026-11-15T12:00:00Z")
        self.assertEqual(count, 4)
        self.assertEqual(start, "2026-11-16T15:00:00Z")
        self.assertEqual(end, "2026-11-25T15:00:00Z")

    def test_missing_cycle_fails(self) -> None:
        with self.assertRaises(SystemExit):
            describe_frontend({"cycles": {}, "points": SAMPLE["points"]})

    def test_no_records_fails(self) -> None:
        with self.assertRaises(SystemExit):
            describe_frontend({"cycles": SAMPLE["cycles"], "points": [{"forecasts": {"ecmwf": []}}]})


class RegressedArmsTest(unittest.TestCase):
    def test_equal_cycles_do_not_regress(self) -> None:
        cycles = {"ecmwf": "2026-11-16T00:00:00Z", "hrrr": "2026-11-16T00:00:00Z"}
        self.assertEqual(regressed_arms(cycles, cycles), [])

    def test_advancing_arms_do_not_regress(self) -> None:
        existing = {"ecmwf": "2026-11-16T00:00:00Z", "hrrr": "2026-11-16T00:00:00Z"}
        candidate = {"ecmwf": "2026-11-16T06:00:00Z", "hrrr": "2026-11-16T13:00:00Z"}
        self.assertEqual(regressed_arms(existing, candidate), [])

    def test_crossed_arms_regress_the_stale_arm(self) -> None:
        # The Codex-gate case: a stale ECMWF arm behind a fresh HRRR
        # cycle passes a scalar-maximum check but must be refused.
        existing = {"ecmwf": "2026-11-16T12:00:00Z", "hrrr": "2026-11-16T12:00:00Z"}
        candidate = {"ecmwf": "2026-11-16T06:00:00Z", "hrrr": "2026-11-16T13:00:00Z"}
        self.assertEqual(regressed_arms(existing, candidate), ["ecmwf"])

    def test_dropped_arm_regresses(self) -> None:
        existing = {"ecmwf": "2026-11-16T00:00:00Z", "hrrr": "2026-11-16T00:00:00Z"}
        self.assertEqual(regressed_arms(existing, {"ecmwf": "2026-11-16T06:00:00Z"}), ["hrrr"])


if __name__ == "__main__":
    unittest.main()
