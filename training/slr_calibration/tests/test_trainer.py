from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from slr_calibration.io import read_rows
from slr_calibration.qc import parse_event
from slr_calibration.trainer import TrainingConfig, train_artifact

FIXTURE = Path(__file__).parent / "fixtures" / "smoke-events.csv"


class TrainerTests(unittest.TestCase):
    def config(self) -> TrainingConfig:
        return TrainingConfig(
            artifact_id="regional-slr-unvalidated-smoke-v1",
            artifact_version="unvalidated-smoke-v1",
            prior_effective_sample_size=4,
            min_effective_sample_size=2,
            source_label="synthetic test fixture; not meteorological evidence",
        )

    def test_export_is_deterministic(self) -> None:
        rows = read_rows(FIXTURE)
        first = train_artifact(rows, self.config())
        second = train_artifact(list(reversed(rows)), self.config())
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual(first["status"], "unvalidated")
        self.assertEqual(first["training"]["target"], "log(observed_slr / physical_baseline_slr)")

    def test_children_are_shrunk_and_hierarchy_is_present(self) -> None:
        artifact = train_artifact(read_rows(FIXTURE), self.config())
        method = artifact["methods"]["kuchera"]
        self.assertTrue(method["climate"])
        self.assertTrue(method["region"])
        self.assertTrue(method["local"])
        for collection in (method["climate"], method["region"], method["local"]):
            for node in collection.values():
                self.assertLess(node["shrinkageWeight"], 1)
                self.assertGreaterEqual(node["effectiveSampleSize"], 1)

    def test_qc_rejects_bad_observation(self) -> None:
        row = read_rows(FIXTURE)[0]
        event, issues = parse_event({**row, "observed_slr": "70"})
        self.assertIsNone(event)
        self.assertIn("OBSERVED_SLR_RANGE", {issue.code for issue in issues})

    def test_artifact_json_round_trip(self) -> None:
        artifact = train_artifact(read_rows(FIXTURE), self.config())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
            self.assertEqual(json.loads(path.read_text()), artifact)


if __name__ == "__main__":
    unittest.main()
