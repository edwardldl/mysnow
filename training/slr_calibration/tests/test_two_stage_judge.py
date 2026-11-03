"""Tests for the two-stage judge rules (queue item 1, DESIGN.md SS3/SS5)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slr_calibration.two_stage_judge import Judge, spearman  # noqa: E402

REGIONS = ["alaska", "great-lakes", "northeast", "pacific-northwest", "rockies", "sierra-nevada"]


def build_world():
    """60 events (10/region, observed 10.0) and a fabricated manifest set."""
    events = [f"{region}-{i}" for region in REGIONS for i in range(10)]
    metadata = pd.DataFrame(
        {"observed_slr": [10.0] * len(events),
         "region": [e.rsplit("-", 1)[0] for e in events]},
        index=events,
    )
    manifests = {}
    for region in REGIONS:
        ids = [f"{region}-{i}" for i in range(10)]
        manifests[f"loro_{region}"] = {"family": "loro", "test_event_ids": ids}
    manifests["random60_40_rep0"] = {"family": "randomised", "test_event_ids": events[:24]}
    manifests["random60_40_rep1"] = {"family": "randomised", "test_event_ids": events[24:48]}
    manifests["temporalWY2026"] = {"family": "temporal", "test_event_ids": events[::5]}
    manifests["unseenStations"] = {"family": "stations", "test_event_ids": events[1::5]}
    return metadata, manifests


def preds_with_offsets(manifests, offsets: dict[str, float], base: float = 10.0):
    """Predictions = observed + per-split constant offset (MAE = |offset|)."""
    out = {}
    for split_id, entry in manifests.items():
        ids = entry["test_event_ids"]
        out[split_id] = pd.Series([base + offsets.get(split_id, 0.0)] * len(ids), index=ids)
    return out


class JudgeTest(unittest.TestCase):
    def setUp(self):
        self.metadata, self.manifests = build_world()
        self.judge = Judge(self.metadata, self.manifests)
        # Incumbent: MAE exactly 1.0 on every split.
        self.incumbent = preds_with_offsets(self.manifests, {}, base=11.0)

    def test_family_maes_definitions(self):
        stats = self.judge.family_maes(self.incumbent)
        self.assertAlmostEqual(stats["family"]["loro"], 1.0)
        self.assertAlmostEqual(stats["family"]["randomised"], 1.0)
        self.assertAlmostEqual(stats["four_family_mean"], 1.0)
        # LORO is the unweighted mean of fold MAEs even with unequal folds:
        cand = preds_with_offsets(self.manifests, {"loro_alaska": 1.0}, base=11.0)
        stats = self.judge.family_maes(cand)
        self.assertAlmostEqual(stats["loro_regions"]["alaska"], 2.0)  # pred 12 vs obs 10
        self.assertAlmostEqual(stats["family"]["loro"], (2.0 + 5 * 1.0) / 6)

    def test_screen_kills_identical_candidate(self):
        screen = self.judge.screen(self.incumbent, self.incumbent)
        self.assertTrue(screen["killed"])
        self.assertTrue(any("LORO" in c for c in screen["kill_clauses"]))

    def test_screen_passes_uniformly_better_candidate(self):
        candidate = preds_with_offsets(self.manifests, {}, base=10.9)
        screen = self.judge.screen(self.incumbent, candidate)
        self.assertFalse(screen["killed"])
        self.assertAlmostEqual(screen["screen_scalar"], 0.1)

    def test_screen_region_clause(self):
        offsets = {split: -0.1 for split in self.manifests}  # better everywhere
        offsets["loro_alaska"] = 0.2  # ...except Alaska, worse by 0.2 net
        candidate = preds_with_offsets(self.manifests, offsets, base=11.0)
        screen = self.judge.screen(self.incumbent, candidate)
        self.assertTrue(screen["killed"])
        self.assertTrue(any("region" in c for c in screen["kill_clauses"]))

    def test_full_adoption_uniform_gain(self):
        candidate = preds_with_offsets(self.manifests, {}, base=10.9)
        full = self.judge.full(self.incumbent, candidate)
        self.assertTrue(full["adopted"], full["failed_clauses"])

    def test_full_rejects_small_gain(self):
        candidate = preds_with_offsets(self.manifests, {}, base=10.99)
        full = self.judge.full(self.incumbent, candidate)
        self.assertFalse(full["adopted"])

    def test_qualification_pass_and_fail(self):
        offsets = {"temporalWY2026": 0.02, "unseenStations": 0.03,
                   "random60_40_rep0": 0.01, "random60_40_rep1": 0.01}
        for region in REGIONS:
            offsets[f"loro_{region}"] = -0.01
        candidate = preds_with_offsets(self.manifests, offsets, base=11.0)
        result = self.judge.qualification(self.incumbent, candidate)
        self.assertTrue(result["qualified"], result["failed_clauses"])
        offsets["unseenStations"] = 0.04
        candidate = preds_with_offsets(self.manifests, offsets, base=11.0)
        result = self.judge.qualification(self.incumbent, candidate)
        self.assertFalse(result["qualified"])
        self.assertTrue(any("stations" in c for c in result["failed_clauses"]))

    def test_qualification_region_clause(self):
        offsets = {f"loro_{r}": 0.0 for r in REGIONS}
        offsets["loro_rockies"] = 0.06
        candidate = preds_with_offsets(self.manifests, offsets, base=11.0)
        result = self.judge.qualification(self.incumbent, candidate)
        self.assertFalse(result["qualified"])
        self.assertTrue(any("rockies" in c for c in result["failed_clauses"]))


class SpearmanTest(unittest.TestCase):
    def test_perfect_and_reversed(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)

    def test_ties(self):
        rho = spearman([1, 1, 2, 3], [1, 2, 3, 4])
        self.assertGreater(rho, 0.8)


if __name__ == "__main__":
    unittest.main()
