"""Tests for the pooled bias-correction layer (bias_correction.py).

Protocol (design SS3/SS6 of the 2026-10-31 bias-correction round):
offsets are shrunk pooled cell means of out-of-fold residuals
(observed - predicted), k = 200, families backfitted; no region
identity anywhere; corrected predictions clipped to [3, 30].
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slr_calibration.bias_correction import (  # noqa: E402
    ALTITUDE_BANDS,
    SEASONS,
    altitude_band,
    apply_isotonic,
    correct_predictions,
    declared_cells,
    evaluate_adoption,
    fit_correction,
    fit_isotonic,
    fit_offsets,
    max_abs_band_bias,
    offset_vector,
    pool_band_bias,
    pool_cells,
    predicted_band_bias,
    primary_bias_metric,
    season_of_month,
)


class TestStratifiers(unittest.TestCase):
    def test_season_mapping_all_months(self) -> None:
        expected = {
            1: "winter", 2: "winter", 3: "spring", 4: "spring",
            5: "spring", 6: None, 7: None, 8: None, 9: None,
            10: "autumn", 11: "autumn", 12: "winter",
        }
        for month, season in expected.items():
            self.assertEqual(season_of_month(month), season)

    def test_altitude_band_boundaries(self) -> None:
        self.assertEqual(altitude_band(1.8), "lt500")
        self.assertEqual(altitude_band(499.99), "lt500")
        self.assertEqual(altitude_band(500.0), "500_1000")
        self.assertEqual(altitude_band(999.99), "500_1000")
        self.assertEqual(altitude_band(1000.0), "1000_1500")
        self.assertEqual(altitude_band(1500.0), "1500_2000")
        self.assertEqual(altitude_band(1999.99), "1500_2000")
        self.assertEqual(altitude_band(2000.0), "gte2000")
        self.assertEqual(altitude_band(3168.1), "gte2000")


class TestOffsetFitting(unittest.TestCase):
    def test_shrunk_cell_mean_single_family(self) -> None:
        # 200 winter events with residual exactly +1.0, k=200:
        # offset = 1.0 * 200/400 = 0.5 exactly.
        residuals = np.full(200, 1.0)
        months = np.full(200, 1)
        elevations = np.full(200, 100.0)
        season_offsets, altitude_offsets = fit_offsets(
            residuals, months, elevations, families=("season",)
        )
        self.assertAlmostEqual(season_offsets["winter"], 0.5, places=12)
        self.assertEqual(season_offsets["autumn"], 0.0)
        self.assertEqual(season_offsets["spring"], 0.0)
        for band in ALTITUDE_BANDS:
            self.assertEqual(altitude_offsets[band], 0.0)

    def test_shrinkage_scales_with_n(self) -> None:
        # Small cell: 20 events, mean residual 2.0 -> 2.0 * 20/220.
        residuals = np.full(20, 2.0)
        months = np.full(20, 10)
        elevations = np.full(20, 2500.0)
        season_offsets, _ = fit_offsets(
            residuals, months, elevations, families=("season",)
        )
        self.assertAlmostEqual(
            season_offsets["autumn"], 2.0 * 20.0 / 220.0, places=12
        )

    def test_backfitting_recovers_additive_effects(self) -> None:
        # Balanced design: every (season, band) pair equally present,
        # residual = season_effect + band_effect, no noise.  The two
        # backfit cycles must attribute the additive structure to the
        # two families (right sign, shrunk magnitude) and materially
        # shrink the residuals.
        season_effect = {"winter": 0.6, "autumn": -0.2, "spring": 0.3}
        band_effect = {
            "lt500": -0.4, "500_1000": -0.7, "1000_1500": 0.0,
            "1500_2000": 0.15, "gte2000": 0.35,
        }
        month_of = {"winter": 1, "autumn": 10, "spring": 4}
        elev_of = {
            "lt500": 100.0, "500_1000": 750.0, "1000_1500": 1250.0,
            "1500_2000": 1750.0, "gte2000": 2500.0,
        }
        rows = []
        for season, s_eff in season_effect.items():
            for band, b_eff in band_effect.items():
                for _ in range(400):
                    rows.append((month_of[season], elev_of[band], s_eff + b_eff))
        months = np.array([r[0] for r in rows])
        elevations = np.array([r[1] for r in rows])
        residuals = np.array([r[2] for r in rows])
        season_offsets, altitude_offsets = fit_offsets(residuals, months, elevations)
        correction = {
            "seasonOffsets": season_offsets,
            "altitudeOffsets": altitude_offsets,
            "isotonic": None,
        }
        applied = offset_vector(months, elevations, correction)
        # Shrinkage (k=200 against cells of n=1200-2000) deliberately
        # pulls the backfitted offsets toward zero: the design property
        # is that each cell's applied offset has the true effect's sign
        # and recovers 50-100% of it -- never the full unshrunk effect
        # and never an overshoot.
        for i, (_, _, true_effect) in enumerate(rows):
            if abs(true_effect) < 0.25:
                continue
            self.assertEqual(np.sign(applied[i]), np.sign(true_effect))
            ratio = applied[i] / true_effect
            self.assertGreaterEqual(ratio, 0.5)
            self.assertLessEqual(ratio, 1.0)
        corrected_residuals = residuals - applied
        self.assertLess(
            np.abs(corrected_residuals).mean(), np.abs(residuals).mean() * 0.35
        )

    def test_summer_events_get_no_season_offset(self) -> None:
        residuals = np.array([1.0, 1.0, 1.0])
        months = np.array([7, 8, 1])  # two summer, one winter
        elevations = np.array([100.0, 100.0, 100.0])
        season_offsets, altitude_offsets = fit_offsets(
            residuals, months, elevations, families=("season",)
        )
        vector = offset_vector(
            months,
            elevations,
            {"seasonOffsets": season_offsets, "altitudeOffsets": altitude_offsets},
        )
        self.assertEqual(vector[0], 0.0)
        self.assertEqual(vector[1], 0.0)
        self.assertGreater(vector[2], 0.0)

    def test_empty_families_zero_offsets(self) -> None:
        correction = fit_correction(
            np.full(50, 10.0), np.full(50, 12.0),
            np.full(50, 1), np.full(50, 100.0), families=(),
        )
        for season in SEASONS:
            self.assertEqual(correction["seasonOffsets"][season], 0.0)
        for band in ALTITUDE_BANDS:
            self.assertEqual(correction["altitudeOffsets"][band], 0.0)
        self.assertIsNone(correction["isotonic"])


class TestIsotonic(unittest.TestCase):
    def test_apply_matches_sklearn_predict(self) -> None:
        from sklearn.isotonic import IsotonicRegression

        rng = np.random.default_rng(7)
        x = np.sort(rng.uniform(3, 30, 800))
        y = np.clip(0.6 * x + rng.normal(0, 2.5, 800) + 5, 2, 45)
        model = IsotonicRegression(out_of_bounds="clip").fit(x, y)
        table = fit_isotonic(x, y)
        grid = np.linspace(0, 35, 1401)
        expected = model.predict(grid)
        got = apply_isotonic(table, grid)
        self.assertEqual(float(np.max(np.abs(expected - got))), 0.0)

    def test_apply_clips_out_of_bounds(self) -> None:
        table = {"xThresholds": [5.0, 20.0], "yThresholds": [6.0, 18.0]}
        out = apply_isotonic(table, np.array([0.0, 5.0, 12.5, 20.0, 99.0]))
        self.assertTrue(np.allclose(out, [6.0, 6.0, 12.0, 18.0, 18.0]))

    def test_isotonic_fitted_on_corrected_oof(self) -> None:
        # fit_correction(include_isotonic=True) must fit the map on the
        # offset-corrected OOF predictions: with a pure winter offset,
        # the map's input range shifts by that offset.
        n = 600
        months = np.full(n, 1)
        elevations = np.full(n, 100.0)
        predicted = np.linspace(5, 25, n)
        observed = predicted + 1.0  # pure +1 residual -> positive offset
        correction = fit_correction(
            predicted, observed, months, elevations, include_isotonic=True
        )
        self.assertGreater(correction["seasonOffsets"]["winter"], 0.5)
        self.assertIsNotNone(correction["isotonic"])
        thresholds = correction["isotonic"]["xThresholds"]
        self.assertGreater(thresholds[0], 5.0)  # shifted up by the offset


class TestApplyAndMetrics(unittest.TestCase):
    def test_correct_predictions_clips_to_contract(self) -> None:
        correction = {
            "seasonOffsets": {"winter": 5.0, "autumn": 0.0, "spring": 0.0},
            "altitudeOffsets": {band: 0.0 for band in ALTITUDE_BANDS},
            "isotonic": None,
        }
        out = correct_predictions(
            np.array([29.0, 4.0, 10.0]),
            np.array([1, 7, 7]),
            np.array([100.0, 100.0, 100.0]),
            correction,
        )
        self.assertTrue(np.allclose(out, [30.0, 4.0, 10.0]))

    def test_declared_cells_and_primary_metric(self) -> None:
        # 100 winter/low events with bias +1 (pred-obs), 300 spring/high
        # events with bias -0.5.  Season cells: winter +1 (n=100),
        # spring -0.5 (n=300), autumn empty.  Altitude cells: lt500 +1,
        # gte2000 -0.5.  Primary metric = (100*1 + 300*0.5 + 100*1 +
        # 300*0.5) / 800 = 0.625.
        observed = np.concatenate([np.full(100, 10.0), np.full(300, 10.0)])
        predicted = observed + np.concatenate([np.full(100, 1.0), np.full(300, -0.5)])
        months = np.concatenate([np.full(100, 1), np.full(300, 4)])
        elevations = np.concatenate([np.full(100, 100.0), np.full(300, 2500.0)])
        cells = declared_cells(observed, predicted, months, elevations)
        self.assertEqual(len(cells), 8)
        by_key = {(c["family"], c["cell"]): c for c in cells}
        self.assertEqual(by_key[("season", "winter")]["bias"], 1.0)
        self.assertEqual(by_key[("season", "spring")]["bias"], -0.5)
        self.assertEqual(by_key[("season", "autumn")]["n"], 0)
        self.assertIsNone(by_key[("season", "autumn")]["bias"])
        self.assertEqual(by_key[("altitude", "gte2000")]["n"], 300)
        self.assertAlmostEqual(primary_bias_metric(cells), 0.625, places=9)

    def test_pool_cells_recovers_pooled_bias(self) -> None:
        def table(bias_winter: float, n_winter: int) -> list[dict]:
            cells = []
            for season in ("autumn", "winter", "spring"):
                cells.append({
                    "family": "season", "cell": season,
                    "n": n_winter if season == "winter" else 0,
                    "bias": bias_winter if season == "winter" else None,
                })
            for band in ALTITUDE_BANDS:
                cells.append({"family": "altitude", "cell": band, "n": 0, "bias": None})
            return cells

        pooled = pool_cells([table(1.0, 100), table(-0.5, 300)])
        by_key = {(c["family"], c["cell"]): c for c in pooled}
        self.assertEqual(by_key[("season", "winter")]["n"], 400)
        self.assertAlmostEqual(by_key[("season", "winter")]["bias"], -0.125, places=9)

    def test_predicted_band_bias_uses_predicted_value(self) -> None:
        observed = np.array([8.0, 12.0, 18.0, 25.0])
        predicted = np.array([9.0, 11.0, 22.0, 28.0])
        bands = predicted_band_bias(observed, predicted)
        by_band = {b["band"]: b for b in bands}
        self.assertEqual(by_band["5-10"]["bias"], 1.0)
        self.assertEqual(by_band["10-15"]["bias"], -1.0)
        self.assertEqual(by_band["15-20"]["n"], 0)
        # 22.0 and 28.0 both fall in the top band (closed at 30).
        self.assertEqual(by_band["20-30"]["n"], 2)
        self.assertEqual(by_band["20-30"]["bias"], 3.5)
        self.assertEqual(max_abs_band_bias(bands), 3.5)

    def test_pool_band_bias(self) -> None:
        def table(bias: float, n: int) -> list[dict]:
            return [{
                "band": "5-10", "lo": 5.0, "hi": 10.0, "n": n, "bias": bias,
            }]

        pooled = pool_band_bias([table(2.0, 10), table(-1.0, 30)])
        self.assertEqual(pooled[0]["n"], 40)
        self.assertAlmostEqual(pooled[0]["bias"], -0.25, places=9)


def _passing_summary() -> dict:
    regions = ["alaska", "great-lakes", "northeast", "pacific-northwest", "rockies", "sierra-nevada"]
    region_bias = {
        split: {
            region: {"A0": -0.6, "A3": -0.3, "A5": -0.25} for region in regions
        }
        for split in ("temporal", "loro", "randomised", "stations")
    }
    return {
        "primaryBias": {
            "temporal": {"A0": 0.60, "A3": 0.40, "A5": 0.38},
            "loro": {"A0": 0.55, "A3": 0.50, "A5": 0.49},
            "randomised": {"A0": 0.50, "A3": 0.42, "A5": 0.41},
        },
        "mae": {
            "randomised": {"A0": 2.90, "A3": 2.91, "A5": 2.90},
            "temporal": {"A0": 2.94, "A3": 2.93, "A5": 2.92},
            "stations": {"A0": 3.07, "A3": 3.08, "A5": 3.07},
        },
        "loroRegionMeanMae": {"A0": 3.25, "A3": 3.28, "A5": 3.27},
        "regionBias": region_bias,
        "tailsRandomisedRep0": {
            "tailHigh": {"A0": 8.48, "A3": 8.50, "A5": 8.49},
            "tailLow": {"A0": 3.33, "A3": 3.35, "A5": 3.34},
        },
        "predBandMaxAbsBiasTemporal": {"A0": 1.00, "A5": 0.50},
    }


class TestAdoptionRule(unittest.TestCase):
    def test_all_clauses_pass_and_a5_gate_passes(self) -> None:
        verdict = evaluate_adoption(_passing_summary())
        self.assertTrue(verdict["a3"]["adopted"])
        self.assertTrue(all(c["passed"] for c in verdict["a3"]["clauses"]))
        self.assertTrue(verdict["a5"]["evaluated"])
        self.assertTrue(verdict["a5"]["adopted"])
        self.assertEqual(verdict["shippedArm"], "A5")

    def test_primary_bias_shortfall_fails_a3_and_blocks_a5(self) -> None:
        summary = _passing_summary()
        summary["primaryBias"]["temporal"]["A3"] = 0.50  # only 16.7% fall
        verdict = evaluate_adoption(summary)
        self.assertFalse(verdict["a3"]["adopted"])
        failed = [c["id"] for c in verdict["a3"]["clauses"] if not c["passed"]]
        self.assertEqual(failed, ["bias_reduction_temporal_ge_25pct"])
        self.assertFalse(verdict["a5"]["evaluated"])
        self.assertIsNone(verdict["shippedArm"])

    def test_alaska_worsening_fails(self) -> None:
        summary = _passing_summary()
        summary["regionBias"]["stations"]["alaska"]["A3"] = -0.9
        verdict = evaluate_adoption(summary)
        self.assertFalse(verdict["a3"]["adopted"])
        failed = [c["id"] for c in verdict["a3"]["clauses"] if not c["passed"]]
        self.assertIn("alaska_no_worsen_stations", failed)

    def test_a5_band_shortfall_keeps_a3(self) -> None:
        summary = _passing_summary()
        summary["predBandMaxAbsBiasTemporal"]["A5"] = 0.80  # only 20% fall
        verdict = evaluate_adoption(summary)
        self.assertTrue(verdict["a3"]["adopted"])
        self.assertTrue(verdict["a5"]["evaluated"])
        self.assertFalse(verdict["a5"]["adopted"])
        self.assertEqual(verdict["shippedArm"], "A3")


if __name__ == "__main__":
    unittest.main()
