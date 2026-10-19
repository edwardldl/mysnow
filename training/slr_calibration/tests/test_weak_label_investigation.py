"""Tests for the weak-label investigation's level-surgery application path.

The investigation's conclusions rest on ``predict_variant`` being a faithful
replication of ``evaluation.apply_artifact`` when no surgery is requested,
and on ``walk_terms`` honouring the same ESS break rule.
"""
from __future__ import annotations

import math
import unittest

import h3

from slr_calibration.evaluation import apply_artifact
from slr_calibration.weak_label_investigation import predict_variant, walk_terms


def _node(log_bias: float, ess: float) -> dict:
    return {
        "logBias": log_bias,
        "rawLogBias": log_bias,
        "residualStdLog": 0.3,
        "rawSampleCount": 100,
        "effectiveSampleSize": ess,
        "shrinkageWeight": 0.9,
    }


def _artifact() -> dict:
    cell4 = h3.latlng_to_cell(39.0, -106.0, 4)
    cell2 = h3.latlng_to_cell(39.0, -106.0, 2)
    return {
        "regionIndex": {"system": "h3", "resolution": 4},
        "climateIndex": {"system": "h3", "resolution": 2, "assignments": {cell2: "continental_mountain"}},
        "bounds": {"minCorrectionFactor": 0.5, "maxCorrectionFactor": 2.0, "minSlr": 3.0, "maxSlr": 30.0},
        "training": {"minEffectiveSampleSize": 5.0},
        "methods": {
            "kuchera": {
                "global": _node(-0.90, 500.0),
                "climate": {"continental_mountain": _node(-0.05, 200.0)},
                "region": {cell4: _node(-0.02, 50.0)},
                "local": {"SITE1": _node(0.01, 20.0)},
            }
        },
    }


def _row(**overrides) -> dict:
    row = {
        "baseline_method": "kuchera",
        "baseline_slr": 20.0,
        "observed_slr": 10.0,
        "latitude": 39.0,
        "longitude": -106.0,
        "site_id": "SITE1",
    }
    row.update(overrides)
    return row


class WeakLabelInvestigationTests(unittest.TestCase):
    def test_predict_variant_matches_apply_artifact(self) -> None:
        artifact = _artifact()
        row = _row()
        expected = apply_artifact(artifact, row).calibrated_slr
        self.assertAlmostEqual(predict_variant(artifact, row), expected, places=9)

    def test_walk_breaks_below_minimum_ess(self) -> None:
        artifact = _artifact()
        artifact["methods"]["kuchera"]["climate"]["continental_mountain"]["effectiveSampleSize"] = 1.0
        levels = [level for level, _ in walk_terms(artifact, _row())]
        self.assertEqual(levels, ["global"])

    def test_drop_global_keeps_deeper_terms(self) -> None:
        artifact = _artifact()
        predicted = predict_variant(artifact, _row(), drop_levels=frozenset({"global"}))
        expected = max(3.0, min(30.0, 20.0 * math.exp(-0.05 - 0.02 + 0.01)))
        self.assertAlmostEqual(predicted, expected, places=9)

    def test_factor_floor_clamp_applies(self) -> None:
        artifact = _artifact()
        # total log bias -0.96 < log(0.5): prediction must sit at the 0.5 floor
        self.assertAlmostEqual(predict_variant(artifact, _row()), 20.0 * 0.5, places=9)


if __name__ == "__main__":
    unittest.main()
