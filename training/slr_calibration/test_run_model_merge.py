import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0] / "src"))

import tempfile  # noqa: E402

import run_model_merge as rmm  # noqa: E402
from run_model_merge import (  # noqa: E402
    MergeError,
    adopt_full_frontend,
    arm_counts,
    classify_outcome,
    cycle_ceiling_hours,
    deraw_frontend,
    finalise_frontend,
    frontend_record,
    install_and_queue,
    load_state,
    merge_frontend,
    publish_pending_locked,
    read_pending,
    reconcile_locked,
    recompute_cycles,
    save_state,
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


class AdoptGuardTest(unittest.TestCase):
    def test_stale_candidate_is_rejected(self) -> None:
        state = frontend()
        state["seedCycles"] = {"ecmwf": "2026-11-16T12:00:00Z", "hrrr": "2026-11-16T12:00:00Z"}
        with self.assertRaises(MergeError):
            adopt_full_frontend(state, frontend())  # candidate cycles are 00Z

    def test_reset_may_adopt_stale_with_allow_stale(self) -> None:
        state = frontend()
        state["seedCycles"] = {"ecmwf": "2026-11-16T12:00:00Z", "hrrr": "2026-11-16T12:00:00Z"}
        new_state, _stats = adopt_full_frontend(state, frontend(), allow_stale=True)
        self.assertEqual(new_state["seedCycles"]["ecmwf"], C0)

    def test_candidate_seed_cycles_are_preserved(self) -> None:
        candidate = frontend()
        candidate["seedCycles"] = {"ecmwf": "2026-11-15T12:00:00Z", "hrrr": "2026-11-15T12:00:00Z"}
        new_state, _stats = adopt_full_frontend(None, candidate)
        self.assertEqual(new_state["seedCycles"]["ecmwf"], "2026-11-15T12:00:00Z")

    def test_retention_requires_matching_window_identity(self) -> None:
        state = frontend()
        # A newer standing record whose window START differs from the
        # candidate's record for the same end must not be retained.
        odd = record("2026-11-17T15:00:00Z", "2026-11-16T22:00:00Z", slr=15.0, lead=1,
                     start="2026-11-16T14:00:00Z")
        state["points"][0]["forecasts"]["hrrr"][0] = odd
        candidate = frontend()
        for point in candidate["points"]:
            for rows in point["forecasts"].values():
                for rec in rows:
                    rec["cycleUtc"] = "2026-11-16T12:00:00Z"
        candidate["cycles"] = {"ecmwf": "2026-11-16T12:00:00Z", "hrrr": "2026-11-16T12:00:00Z"}
        new_state, stats = adopt_full_frontend(state, candidate)
        self.assertEqual(stats["retainedNewer"], 0)
        self.assertEqual(new_state["points"][0]["forecasts"]["hrrr"][0]["modelSlr"], 10.0)


class OutcomeTest(unittest.TestCase):
    def test_taxonomy(self) -> None:
        self.assertEqual(classify_outcome(10, 8, 5, 2), "merged")
        self.assertEqual(classify_outcome(10, 8, 5, 0), "already-current")
        self.assertEqual(classify_outcome(10, 8, 0, 0), "no-covered-windows")
        self.assertEqual(classify_outcome(10, 0, 0, 0), "no-usable-data")
        self.assertEqual(classify_outcome(0, 0, 0, 0), "no-eligible-windows")

    def test_arm_counts(self) -> None:
        flat = [
            {"source": "hrrr", "missing": False},
            {"source": "hrrr", "missing": True},
            {"source": "ecmwf", "missing": False},
        ]
        counts = arm_counts(flat)
        self.assertEqual(counts["hrrr"], {"built": 2, "usable": 1})
        self.assertEqual(counts["ecmwf"], {"built": 1, "usable": 1})


class GenerationLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name) / "merge-state"
        self.state_dir.mkdir(parents=True)
        self.frontend_out = Path(self.tmp.name) / "model-forecasts.json"
        state = stamp_record_cycles(deraw_frontend(frontend()))
        state["seedCycles"] = {"ecmwf": C0, "hrrr": C0}
        state["generation"] = 0
        state["publishedGeneration"] = 0
        state["artifactSha256"] = None
        self.state = state

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_install_increments_generation_and_stamps_frontend(self) -> None:
        installed, _report = install_and_queue(self.state_dir, self.state, self.frontend_out)
        self.assertEqual(installed["servingGeneration"], 1)
        self.assertEqual(self.state["generation"], 1)
        pending = read_pending(self.state_dir)
        self.assertEqual(pending["generation"], 1)
        self.assertEqual(pending["sha256"], self.state["artifactSha256"])

    def test_reconcile_repairs_install_and_requeues(self) -> None:
        install_and_queue(self.state_dir, self.state, self.frontend_out)
        # Simulate a crash after the state write: corrupt the install
        # and drop the pending record.
        self.frontend_out.write_text("{}")
        (self.state_dir / "pending-publish.json").unlink()
        changed = reconcile_locked(self.state_dir, self.state, self.frontend_out)
        self.assertTrue(changed)
        import hashlib as hl
        self.assertEqual(hl.sha256(self.frontend_out.read_bytes()).hexdigest(),
                         self.state["artifactSha256"])
        self.assertIsNotNone(read_pending(self.state_dir))

    def test_publish_marks_generation_and_clears_pending(self) -> None:
        install_and_queue(self.state_dir, self.state, self.frontend_out)
        original = rmm.publish_artifact
        rmm.publish_artifact = lambda repo, artifact: {"ok": True}
        try:
            receipt, error = publish_pending_locked(Path(self.tmp.name), self.state_dir, self.state)
        finally:
            rmm.publish_artifact = original
        self.assertIsNone(error)
        self.assertEqual(receipt, {"ok": True})
        self.assertEqual(self.state["publishedGeneration"], 1)
        self.assertIsNone(read_pending(self.state_dir))
        saved = load_state(self.state_dir)
        self.assertEqual(saved["publishedGeneration"], 1)

    def test_failed_publish_keeps_pending(self) -> None:
        install_and_queue(self.state_dir, self.state, self.frontend_out)
        original = rmm.publish_artifact

        def boom(repo, artifact):
            raise MergeError("blob down")

        rmm.publish_artifact = boom
        try:
            receipt, error = publish_pending_locked(Path(self.tmp.name), self.state_dir, self.state)
        finally:
            rmm.publish_artifact = original
        self.assertIsNone(receipt)
        self.assertIn("blob down", error)
        self.assertIsNotNone(read_pending(self.state_dir))
        self.assertEqual(self.state["publishedGeneration"], 0)


def stamped_frontend(cycle: str) -> dict:
    doc = frontend()
    for point in doc["points"]:
        for rows in point["forecasts"].values():
            for rec in rows:
                rec["cycleUtc"] = cycle
    doc["cycles"] = {"ecmwf": cycle, "hrrr": cycle}
    return doc


class CommandLifecycleTest(unittest.TestCase):
    """The command-level invariants the v3 gate found untested:
    successive full commits, generation carry-over, reset recovery."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "state").mkdir()
        self.original_publish = rmm.publish_artifact
        rmm.publish_artifact = lambda repo, artifact: {"ok": True}

    def tearDown(self) -> None:
        rmm.publish_artifact = self.original_publish
        self.tmp.cleanup()

    def args(self, **kw):
        from types import SimpleNamespace
        base = {"repo": str(self.root), "state_dir": "state",
                "frontend_out": "out.json"}
        base.update(kw)
        return SimpleNamespace(**base)

    def write_candidate(self, name: str, cycle: str) -> str:
        import json as _json
        (self.root / name).write_text(_json.dumps(stamped_frontend(cycle)))
        return name

    def test_successive_full_commits_advance_the_generation(self) -> None:
        first = rmm.cmd_commit_full(self.args(
            candidate=self.write_candidate("a.json", "2026-11-16T12:00:00Z")))
        self.assertEqual(first["generation"], 1)
        self.assertEqual(first["publishStatus"], "published")
        second = rmm.cmd_commit_full(self.args(
            candidate=self.write_candidate("b.json", "2026-11-17T00:00:00Z")))
        self.assertEqual(second["generation"], 2)
        self.assertEqual(second["publishStatus"], "published")
        state = load_state(self.root / "state")
        self.assertEqual(state["publishedGeneration"], 2)

    def test_full_merge_full_keeps_lineage_and_retention(self) -> None:
        rmm.cmd_commit_full(self.args(
            candidate=self.write_candidate("a.json", "2026-11-16T12:00:00Z")))
        # An hourly refresh lands while the next batch is scoring
        # (generation 2 via a merge install).
        state = load_state(self.root / "state")
        refreshed = record("2026-11-17T15:00:00Z", "2026-11-17T06:00:00Z", slr=15.0, lead=1)
        merged, _ = merge_frontend(state, {("pt-a", "hrrr"): [refreshed]})
        install_and_queue(self.root / "state", merged, self.root / "out.json")
        self.assertEqual(merged["generation"], 2)
        # The next full batch (00Z, scored before the 06Z refresh
        # landed) adopts, retains the 06Z record, and continues the
        # generation lineage.
        receipt = rmm.cmd_commit_full(self.args(
            candidate=self.write_candidate("b.json", "2026-11-17T00:00:00Z")))
        self.assertEqual(receipt["generation"], 3)
        self.assertEqual(receipt["retainedNewer"], 1)
        state = load_state(self.root / "state")
        kept = state["points"][0]["forecasts"]["hrrr"][0]
        self.assertEqual(kept["modelSlr"], 15.0)

    def test_older_seed_with_newer_cycles_is_rejected(self) -> None:
        rmm.cmd_commit_full(self.args(
            candidate=self.write_candidate("a.json", "2026-11-16T12:00:00Z")))
        sneaky = stamped_frontend("2026-11-16T18:00:00Z")
        sneaky["seedCycles"] = {"ecmwf": "2026-11-16T00:00:00Z", "hrrr": "2026-11-16T00:00:00Z"}
        import json as _json
        (self.root / "sneaky.json").write_text(_json.dumps(sneaky))
        with self.assertRaises(MergeError):
            rmm.cmd_commit_full(self.args(candidate="sneaky.json"))

    def test_reset_reconcile_publishes_at_a_fresh_generation(self) -> None:
        rmm.cmd_commit_full(self.args(
            candidate=self.write_candidate("a.json", "2026-11-16T12:00:00Z")))
        import json as _json
        (self.root / "reseed.json").write_text(_json.dumps(stamped_frontend("2026-11-16T12:00:00Z")))
        rmm.cmd_reset(self.args(frontend="reseed.json"))
        state = load_state(self.root / "state")
        self.assertEqual(state["generation"], 2)  # fresh, not reused 1
        self.assertEqual(state["publishedGeneration"], 0)
        changed = reconcile_locked(self.root / "state", state, self.root / "out.json")
        self.assertTrue(changed)
        installed = _json.loads((self.root / "out.json").read_text())
        self.assertEqual(installed["servingGeneration"], 2)


class PublishFailureModesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name) / "merge-state"
        self.state_dir.mkdir(parents=True)
        self.frontend_out = Path(self.tmp.name) / "model-forecasts.json"
        state = stamp_record_cycles(deraw_frontend(frontend()))
        state["seedCycles"] = {"ecmwf": C0, "hrrr": C0}
        state["generation"] = 0
        state["publishedGeneration"] = 0
        state["artifactSha256"] = None
        self.state = state
        install_and_queue(self.state_dir, self.state, self.frontend_out)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_publisher_timeout_becomes_a_recorded_failure(self) -> None:
        import subprocess as sp
        original_run = rmm.subprocess.run

        def timing_out(*a, **kw):
            raise sp.TimeoutExpired(cmd="publisher", timeout=900)

        rmm.subprocess.run = timing_out
        try:
            with self.assertRaises(MergeError):
                rmm.publish_artifact(Path(self.tmp.name), self.frontend_out)
            receipt, error = publish_pending_locked(
                Path(self.tmp.name), self.state_dir, self.state)
        finally:
            rmm.subprocess.run = original_run
        self.assertIsNone(receipt)
        self.assertIn("did not complete", error)
        self.assertIsNotNone(read_pending(self.state_dir))


if __name__ == "__main__":
    unittest.main()
