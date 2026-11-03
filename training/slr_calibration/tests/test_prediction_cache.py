"""Tests for the frozen manifests + keyed prediction cache (queue item 1)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slr_calibration import gold_model  # noqa: E402
from slr_calibration.prediction_cache import (  # noqa: E402
    PredictionCache,
    build_manifests,
    manifest_hash,
    split_instances,
)


def tiny_frame(n_per_region: int = 20) -> pd.DataFrame:
    rows = []
    for region in ("alaska", "rockies"):
        for i in range(n_per_region):
            rows.append({
                "event_id": f"{region}-{i}",
                "region": region,
                "station_id": f"{region}-s{i % 5}",
                "water_year": 2026 if i % 4 == 0 else 2024,
                "observed_slr": 10.0,
                "baseline_slr": 8.0,
            })
    return pd.DataFrame(rows)


class SplitInstancesTest(unittest.TestCase):
    def test_default_table_has_28_instances(self):
        instances = split_instances()
        self.assertEqual(len(instances), 28)
        families = [i["family"] for i in instances]
        self.assertEqual(families.count("randomised"), 20)
        self.assertEqual(families.count("loro"), 6)

    def test_fit_seeds_match_judging(self):
        by_id = {i["split_id"]: i for i in split_instances()}
        self.assertEqual(by_id["random60_40_rep0"]["fit_seed"], 1000)
        self.assertEqual(by_id["random60_40_rep19"]["fit_seed"], 1019)
        self.assertEqual(by_id["loro_alaska"]["fit_seed"], 2000)
        self.assertEqual(by_id["temporalWY2026"]["fit_seed"], 3000)
        self.assertEqual(by_id["unseenStations"]["fit_seed"], 4000)


class ManifestsTest(unittest.TestCase):
    def test_manifests_on_tiny_frame(self):
        frame = tiny_frame()
        instances = split_instances(regions=("alaska", "rockies"), random_reps=2)
        manifests = build_manifests(frame, instances)
        self.assertEqual(manifests["loro_alaska"]["test_n"], 20)
        self.assertEqual(
            set(manifests["loro_alaska"]["test_event_ids"]),
            {f"alaska-{i}" for i in range(20)},
        )
        temporal = manifests["temporalWY2026"]
        self.assertEqual(temporal["test_n"], 10)  # every 4th row is WY2026
        for entry in manifests.values():
            self.assertEqual(entry["train_n"] + entry["test_n"], len(frame))
        # Hash is stable across rebuilds on the same frame.
        again = build_manifests(frame, instances)
        self.assertEqual(manifest_hash(manifests), manifest_hash(again))


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = PredictionCache(
            Path(self.tmp.name), cohort="c" * 64, features="f" * 64,
            manifests="m" * 64, libraries={"sklearn": "test"},
        )
        self.ids = ["e1", "e2", "e3"]
        self.values = np.array([10.0, 11.0, 12.0])

    def test_round_trip(self):
        self.cache.put("rf", "loro_alaska", 2000, self.ids, self.values)
        got = self.cache.get("rf", "loro_alaska", 2000, self.ids)
        np.testing.assert_array_equal(got, self.values)

    def test_miss_on_wrong_seed_or_split(self):
        self.cache.put("rf", "loro_alaska", 2000, self.ids, self.values)
        self.assertIsNone(self.cache.get("rf", "loro_alaska", 2001, self.ids))
        self.assertIsNone(self.cache.get("rf", "loro_rockies", 2000, self.ids))
        self.assertIsNone(self.cache.get("gbm", "loro_alaska", 2000, self.ids))

    def test_miss_on_event_order_mismatch(self):
        self.cache.put("rf", "loro_alaska", 2000, self.ids, self.values)
        self.assertIsNone(self.cache.get("rf", "loro_alaska", 2000, ["e2", "e1", "e3"]))

    def test_miss_on_tampered_key_record(self):
        key = self.cache.put("rf", "loro_alaska", 2000, self.ids, self.values)
        json_path = Path(self.tmp.name) / key[:2] / f"{key}.json"
        record = json.loads(json_path.read_text())
        record["payload"]["seed"] = 9999  # payload no longer hashes to key
        json_path.write_text(json.dumps(record))
        self.assertIsNone(self.cache.get("rf", "loro_alaska", 2000, self.ids))

    def test_key_depends_on_cohort_and_features(self):
        other = PredictionCache(
            Path(self.tmp.name), cohort="d" * 64, features="f" * 64,
            manifests="m" * 64, libraries={"sklearn": "test"},
        )
        self.assertNotEqual(
            self.cache.key_for("rf", "loro_alaska", 2000),
            other.key_for("rf", "loro_alaska", 2000),
        )

    def test_derived_mean_resolution(self):
        self.cache.put("rf_eb", "loro_alaska", 2000, self.ids, self.values)
        self.cache.put("gbm_eb", "loro_alaska", 2000, self.ids, self.values + 2.0)
        got = self.cache.get("ens_eb", "loro_alaska", 2000, self.ids)
        np.testing.assert_array_equal(got, self.values + 1.0)

    def test_derived_tail_shrink_matches_gold_model(self):
        rf = np.array([10.0, 20.0, 25.0])
        eb = np.array([12.0, 30.0, 30.0])
        self.cache.put("rf", "loro_alaska", 2000, self.ids, rf)
        self.cache.put("rf_eb", "loro_alaska", 2000, self.ids, eb)
        got = self.cache.get("tail_rf_eb", "loro_alaska", 2000, self.ids)
        np.testing.assert_allclose(got, gold_model.tail_shrink_predictions(rf, eb))

    def test_derived_miss_when_component_missing(self):
        self.cache.put("rf_eb", "loro_alaska", 2000, self.ids, self.values)
        self.assertIsNone(self.cache.get("ens_eb", "loro_alaska", 2000, self.ids))


if __name__ == "__main__":
    unittest.main()
