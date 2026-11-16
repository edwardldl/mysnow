import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0] / "src"))

from run_model_merge import (  # noqa: E402
    MergeError,
    adopt_full_frontend,
    cycle_ceiling_hours,
    deraw_frontend,
    finalise_frontend,
    frontend_record,
    merge_frontend,
    recompute_cycles,
    stamp_record_cycles,
)

C0 = "2026-11-16T00:00:00Z"


def record(end: str, cycle: str, slr: float = 10.0, lead: int = 2,
           start: str = "2026-11-15T15:00:00Z") -> dict:
    return {
        "leadDay": lead,
        "windowStartUtc": start,
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
    return {
        "generatedAtUtc": "2026-11-16T09:00:00+00:00",
        "cycles": {"ecmwf": C0, "hrrr": C0},
        "points": [
            {
                "pointId": "pt-a",
                "forecasts": {
                    "ecmwf": [record("2026-11-17T15:00:00Z", C0), record("2026-11-25T15:00:00Z", C0, lead=10)],
                    "hrrr": [record("2026-11-17T15:00:00Z", C0, lead=1)],
                },
            },
            {
                "pointId": "pt-b",
                "forecasts": {
                    "ecmwf": [record("2026-11-17T12:00:00Z", C0, start="2026-11-16T12:00:00Z")],
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


class CeilingTest(unittest.TestCase):
    def test_cycle_specific_ceilings(self) -> None:
        self.assertEqual(cycle_ceiling_hours("ecmwf", "2026-11-16T00:00:00Z"), 240)
        self.assertEqual(cycle_ceiling_hours("ecmwf", "2026-11-16T12:00:00Z"), 240)
        self.assertEqual(cycle_ceiling_hours("ecmwf", "2026-11-16T06:00:00Z"), 144)
        self.assertEqual(cycle_ceiling_hours("ecmwf", "2026-11-16T18:00:00Z"), 144)
        self.assertEqual(cycle_ceiling_hours("hrrr", "2026-11-16T06:00:00Z"), 48)
        self.assertEqual(cycle_ceiling_hours("hrrr", "2026-11-16T07:00:00Z"), 18)


class MergeTest(unittest.TestCase):
    def test_replaces_only_strictly_newer(self) -> None:
        state = frontend()
        newer = record("2026-11-17T15:00:00Z", "2026-11-16T06:00:00Z", slr=12.0)
        same = record("2026-11-17T15:00:00Z", C0, slr=13.0)
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

    def test_window_identity_mismatch_is_rejected(self) -> None:
        state = frontend()
        wrong_start = record("2026-11-17T15:00:00Z", "2026-11-16T06:00:00Z",
                             start="2026-11-15T14:00:00Z")
        with self.assertRaises(MergeError):
            merge_frontend(state, {("pt-a", "ecmwf"): [wrong_start]})

    def test_duplicate_scored_records_are_rejected(self) -> None:
        state = frontend()
        dup = record("2026-11-17T15:00:00Z", "2026-11-16T06:00:00Z")
        with self.assertRaises(MergeError):
            merge_frontend(state, {("pt-a", "ecmwf"): [dup, dict(dup)]})

    def test_off_cycle_ceiling_is_enforced(self) -> None:
        state = frontend()
        # The lead-10 ECMWF window ends 225 h after a 06Z cycle: within
        # the 240 h synoptic ceiling but beyond the 144 h off-cycle one.
        late = record("2026-11-25T15:00:00Z", "2026-11-16T06:00:00Z", lead=9)
        with self.assertRaises(MergeError):
            merge_frontend(state, {("pt-a", "ecmwf"): [late]})

    def test_ordinary_hour_hrrr_ceiling_is_enforced(self) -> None:
        state = frontend()
        # Window ends 33 h after a 07Z (18 h) HRRR cycle.
        late = record("2026-11-17T15:00:00Z", "2026-11-16T07:00:00Z", lead=1)
        state["points"][0]["forecasts"]["hrrr"][0]["cycleUtc"] = "2026-11-16T06:00:00Z"
        with self.assertRaises(MergeError):
            merge_frontend(state, {("pt-a", "hrrr"): [late]})


class AdoptTest(unittest.TestCase):
    def test_retains_records_newer_than_the_candidate(self) -> None:
        state = frontend()
        # An hourly refresh landed while the batch was scoring.
        # A 22Z hourly run can reach this window (17 h out, inside its
        # 18 h horizon) and lands while the 12Z batch is still scoring.
        refreshed = record("2026-11-17T15:00:00Z", "2026-11-16T22:00:00Z", slr=15.0, lead=1)
        merge_frontend(state, {("pt-a", "hrrr"): [refreshed]})
        candidate = frontend()  # the 12Z batch's view: hrrr records at 12Z
        for point in candidate["points"]:
            for rows in point["forecasts"].values():
                for rec in rows:
                    rec["cycleUtc"] = "2026-11-16T12:00:00Z"
        candidate["cycles"] = {"ecmwf": "2026-11-16T12:00:00Z", "hrrr": "2026-11-16T12:00:00Z"}
        new_state, stats = adopt_full_frontend(state, candidate)
        kept = new_state["points"][0]["forecasts"]["hrrr"][0]
        self.assertEqual(kept["modelSlr"], 15.0)
        self.assertEqual(kept["cycleUtc"], "2026-11-16T22:00:00Z")
        self.assertEqual(stats["retainedNewer"], 1)
        self.assertEqual(new_state["seedCycles"]["ecmwf"], "2026-11-16T12:00:00Z")

    def test_adopt_without_prior_state(self) -> None:
        new_state, stats = adopt_full_frontend(None, frontend())
        self.assertEqual(stats["adopted"], 4)
        self.assertEqual(new_state["seedCycles"]["ecmwf"], C0)


class FinaliseTest(unittest.TestCase):
    def test_cycles_recomputed_as_per_arm_max(self) -> None:
        state = frontend()
        newer = record("2026-11-17T15:00:00Z", "2026-11-16T06:00:00Z", slr=12.0)
        merged, _ = merge_frontend(state, {("pt-a", "ecmwf"): [newer]})
        recompute_cycles(merged)
        self.assertEqual(merged["cycles"]["ecmwf"], "2026-11-16T06:00:00Z")
        self.assertEqual(merged["cycles"]["hrrr"], C0)

    def test_finalise_applies_isotonic_and_stamps_seed_cycles(self) -> None:
        state = stamp_record_cycles(deraw_frontend(frontend()))
        state["seedCycles"] = {"ecmwf": C0, "hrrr": C0}
        installed, report = finalise_frontend(state)
        self.assertTrue(report["enabled"])
        self.assertEqual(installed["seedCycles"]["ecmwf"], C0)


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
