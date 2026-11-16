import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0] / "src"))

from run_model_merge import (  # noqa: E402
    MergeError,
    deraw_frontend,
    finalise_frontend,
    frontend_record,
    merge_frontend,
    recompute_cycles,
    stamp_record_cycles,
)


def record(end: str, cycle: str, slr: float = 10.0, lead: int = 2) -> dict:
    return {
        "leadDay": lead,
        "windowStartUtc": "2026-11-15T15:00:00Z",
        "windowEndUtc": end,
        "windowEndLocalDate": end[:10],
        "modelSlr": slr,
        "modelSnowfallCm": slr,
        "kucheraSlr": 8.0,
        "kucheraSnowfallCm": 8.0,
        "liquidMm": 10.0,
        "cycleUtc": cycle,
    }


def frontend() -> dict:
    c0 = "2026-11-16T00:00:00Z"
    return {
        "generatedAtUtc": "2026-11-16T09:00:00+00:00",
        "cycles": {"ecmwf": c0, "hrrr": c0},
        "points": [
            {
                "pointId": "pt-a",
                "forecasts": {
                    "ecmwf": [record("2026-11-17T15:00:00Z", c0), record("2026-11-25T15:00:00Z", c0, lead=10)],
                    "hrrr": [record("2026-11-17T15:00:00Z", c0, lead=1)],
                },
            },
            {
                "pointId": "pt-b",
                "forecasts": {
                    "ecmwf": [record("2026-11-17T12:00:00Z", c0)],
                    "hrrr": [],
                },
            },
        ],
    }


class DerawTest(unittest.TestCase):
    def test_restores_pre_isotonic_values(self) -> None:
        doc = frontend()
        rec = doc["points"][0]["forecasts"]["ecmwf"][0]
        rec["preIsotonicSlr"] = rec["modelSlr"]
        rec["preIsotonicSnowfallCm"] = rec["modelSnowfallCm"]
        rec["modelSlr"] = 99.0
        rec["modelSnowfallCm"] = 99.0
        doc["consistencyAdjustment"] = {"adjustedWindows": 1}
        raw = deraw_frontend(doc)
        self.assertNotIn("consistencyAdjustment", raw)
        restored = raw["points"][0]["forecasts"]["ecmwf"][0]
        self.assertEqual(restored["modelSlr"], 10.0)
        self.assertNotIn("preIsotonicSlr", restored)


class MergeTest(unittest.TestCase):
    def test_replaces_only_strictly_newer(self) -> None:
        state = frontend()
        newer = record("2026-11-17T15:00:00Z", "2026-11-16T06:00:00Z", slr=12.0)
        same = record("2026-11-17T15:00:00Z", "2026-11-16T00:00:00Z", slr=13.0)
        merged, stats = merge_frontend(state, {("pt-a", "hrrr"): [same], ("pt-a", "ecmwf"): [newer]})
        self.assertEqual(stats["replaced"], 1)
        self.assertEqual(stats["skippedStale"], 1)
        self.assertEqual(merged["points"][0]["forecasts"]["ecmwf"][0]["modelSlr"], 12.0)
        self.assertEqual(merged["points"][0]["forecasts"]["hrrr"][0]["modelSlr"], 10.0)

    def test_absent_windows_are_skipped_never_added(self) -> None:
        state = frontend()
        ghost = record("2026-11-18T15:00:00Z", "2026-11-16T06:00:00Z")
        _merged, stats = merge_frontend(state, {("pt-a", "ecmwf"): [ghost]})
        self.assertEqual(stats["skippedAbsent"], 1)
        self.assertEqual(stats["replaced"], 0)

    def test_untouched_records_stay_identical(self) -> None:
        state = frontend()
        snapshot = copy.deepcopy(state)
        newer = record("2026-11-17T12:00:00Z", "2026-11-16T06:00:00Z", slr=11.0)
        merged, _stats = merge_frontend(state, {("pt-b", "ecmwf"): [newer]})
        self.assertEqual(merged["points"][0], snapshot["points"][0])

    def test_beyond_ceiling_record_fails_the_gate(self) -> None:
        state = frontend()
        # A standing record whose window ends 20 days after its own cycle
        # breaches the ECMWF ceiling; G4 must reject the merged document.
        state["points"][0]["forecasts"]["ecmwf"][1]["cycleUtc"] = "2026-11-05T00:00:00Z"
        newer = record("2026-11-17T15:00:00Z", "2026-11-16T06:00:00Z", slr=12.0)
        with self.assertRaises(MergeError):
            merge_frontend(state, {("pt-a", "ecmwf"): [newer]})


class FinaliseTest(unittest.TestCase):
    def test_cycles_recomputed_as_per_arm_max(self) -> None:
        state = frontend()
        newer = record("2026-11-17T15:00:00Z", "2026-11-16T06:00:00Z", slr=12.0)
        merged, _ = merge_frontend(state, {("pt-a", "ecmwf"): [newer]})
        recompute_cycles(merged)
        self.assertEqual(merged["cycles"]["ecmwf"], "2026-11-16T06:00:00Z")
        self.assertEqual(merged["cycles"]["hrrr"], "2026-11-16T00:00:00Z")

    def test_finalise_applies_isotonic_and_keeps_provenance(self) -> None:
        state = stamp_record_cycles(deraw_frontend(frontend()))
        installed, report = finalise_frontend(state)
        self.assertTrue(report["enabled"])
        self.assertIn("cycles", installed)


class FrontendRecordTest(unittest.TestCase):
    def test_maps_flat_record(self) -> None:
        flat = {
            "lead_day": 3, "window_start_utc": "2026-11-17T15:00:00Z",
            "window_end_utc": "2026-11-18T15:00:00Z", "window_end_local_date": "2026-11-18",
            "model_slr": 11.0, "model_snowfall_cm": 22.0, "kuchera_slr": 9.0,
            "kuchera_snowfall_cm": 18.0, "liquid_mm": 20.0, "cycle_utc": "2026-11-16T06:00:00Z",
        }
        rec = frontend_record(flat)
        self.assertEqual(rec["cycleUtc"], "2026-11-16T06:00:00Z")
        self.assertEqual(rec["leadDay"], 3)
        self.assertEqual(rec["liquidMm"], 20.0)


if __name__ == "__main__":
    unittest.main()
