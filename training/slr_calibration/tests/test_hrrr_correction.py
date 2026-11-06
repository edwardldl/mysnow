from __future__ import annotations

import unittest
from unittest import mock

import numpy as np

from slr_calibration import hrrr_correction as hc


def _rows(n: int = 400, seed: int = 7):
    rng = np.random.default_rng(seed)
    yhat = rng.uniform(4.0, 25.0, size=n)
    liquid = rng.uniform(0.0, 20.0, size=n)
    regions = np.array(["rockies", "northeast"] * (n // 2))
    return yhat, liquid, regions


class ApplyDeltaTests(unittest.TestCase):
    def test_clamp_bounds(self) -> None:
        yhat = np.array([10.0, 10.0, 10.0])
        out = hc.apply_delta(yhat, np.array([5.0, -5.0, 0.0]))
        self.assertEqual(out[0], 30.0)
        self.assertEqual(out[1], 3.0)
        self.assertAlmostEqual(out[2], 10.0)

    def test_log_scale_shift(self) -> None:
        out = hc.apply_delta(np.array([10.0]), np.log(1.5))
        self.assertAlmostEqual(float(out[0]), 15.0, places=9)


class ObjectiveTests(unittest.TestCase):
    def test_objective_is_mean_mae_plus_penalty(self) -> None:
        yhat = np.array([10.0, 10.0])
        y = np.array([12.0, 8.0])
        liquid = np.array([5.0, 5.0])
        theta = np.array([0.1, 0.0])
        # delta = 0.1 for both rows; corrected = 10 * e^0.1
        corrected = 10.0 * np.exp(0.1)
        expected = (abs(corrected - 12.0) + abs(corrected - 8.0)) / 2.0
        expected += 3.0 * (0.1**2)
        got = hc.shared_objective(theta, "C1", yhat, y, liquid, 3.0)
        self.assertAlmostEqual(got, expected, places=9)


class FitSharedTests(unittest.TestCase):
    def test_recovers_intercept_shift(self) -> None:
        rng = np.random.default_rng(3)
        yhat = rng.uniform(5.0, 20.0, size=500)
        liquid = rng.uniform(0.0, 15.0, size=500)
        y = np.clip(yhat * np.exp(0.2), 3.0, 30.0)
        theta, _ = hc.fit_shared("C1", yhat, y, liquid, 3.0)
        self.assertAlmostEqual(float(theta[0]), 0.2, delta=0.05)
        self.assertLessEqual(abs(float(theta[0])), 0.35)
        self.assertLessEqual(abs(float(theta[1])), 0.5)

    def test_solver_failure_raises(self) -> None:
        yhat, liquid, _ = _rows(50)
        y = np.full(50, 10.0)
        fake = mock.Mock(success=False, fun=np.inf, message="nope", x=np.zeros(2))
        with mock.patch.object(hc, "minimize", return_value=fake):
            with self.assertRaises(hc.AdapterFitFailed):
                hc.fit_shared("C1", yhat, y, liquid, 3.0)


class C3StageBTests(unittest.TestCase):
    def test_thin_region_gets_zero(self) -> None:
        rng = np.random.default_rng(11)
        yhat = rng.uniform(5.0, 20.0, size=120)
        liquid = rng.uniform(0.0, 15.0, size=120)
        regions = np.array(["rockies"] * 99 + ["northeast"] * 21)
        y = np.clip(yhat * 1.1, 3.0, 30.0)
        adapter, _ = hc.fit_adapter("C3", yhat, y, liquid, regions, 10.0)
        self.assertEqual(adapter.deltas["northeast"], 0.0)

    def test_shrink_and_clip_formula(self) -> None:
        # 200 rows in one region, all with y = stage-A prediction * e^0.5:
        # delta_hat ~ 0.5 (inside [-0.6, 0.6]); ESS = 200 ->
        # delta = 200/230 * 0.5 ~ 0.4348 -> clipped to 0.3.
        yhat = np.full(200, 10.0)
        liquid = np.zeros(200)
        regions = np.array(["rockies"] * 200)
        y = np.full(200, 10.0 * np.exp(0.5))
        adapter, _ = hc.fit_adapter("C3", yhat, y, liquid, regions, 100.0)
        stage_a = hc.apply_delta(
            yhat, hc.shared_delta("C3", adapter.theta, yhat, liquid)
        )
        delta_hat = hc.fit_stage_b_delta(stage_a, y)
        expected = float(np.clip(200 / 230 * delta_hat, -0.3, 0.3))
        self.assertAlmostEqual(adapter.deltas["rockies"], expected, places=6)
        self.assertEqual(adapter.deltas["rockies"], 0.3)

    def test_absent_region_falls_back_to_zero(self) -> None:
        yhat, liquid, regions = _rows(300)
        y = np.clip(yhat * 1.05, 3.0, 30.0)
        adapter, _ = hc.fit_adapter("C3", yhat, y, liquid, regions, 10.0)
        pred = adapter.predict(
            np.array([10.0]), np.array([5.0]), np.array(["alaska"])
        )
        shared = hc.apply_delta(
            np.array([10.0]),
            hc.shared_delta("C3", adapter.theta, np.array([10.0]), np.array([5.0])),
        )
        self.assertAlmostEqual(float(pred[0]), float(shared[0]), places=9)


class SelectLambdaTests(unittest.TestCase):
    def test_min_and_tie_break_to_largest(self) -> None:
        scores = {3.0: 2.0000005, 10.0: 2.0, 30.0: 2.4, 100.0: None}
        self.assertEqual(hc.select_lambda(scores), 10.0)

    def test_all_ineligible_returns_none(self) -> None:
        self.assertIsNone(hc.select_lambda({3.0: None, 10.0: None}))

    def test_failed_lambda_excluded_even_if_best(self) -> None:
        scores = {3.0: None, 10.0: 2.5, 30.0: 2.6, 100.0: 2.7}
        self.assertEqual(hc.select_lambda(scores), 10.0)


class BandTests(unittest.TestCase):
    def test_slr_band_endpoints(self) -> None:
        bands = hc.slr_band(np.array([7.9, 8.0, 14.0, 14.1]))
        self.assertEqual(list(bands), ["lt8", "8_14", "8_14", "gt14"])

    def test_liquid_band_endpoints(self) -> None:
        bands = hc.liquid_band(np.array([2.9, 3.0, 10.0, 10.1]))
        self.assertEqual(list(bands), ["lt3", "3_10", "3_10", "gt10"])

    def test_elevation_band_endpoints(self) -> None:
        bands = hc.elevation_band(np.array([999.0, 1000.0, 1499.0, 1500.0]))
        self.assertEqual(list(bands), ["lt1000", "1000_1500", "1000_1500", "ge1500"])


class BootstrapTests(unittest.TestCase):
    def test_single_cluster_replicates_equal_point_gain(self) -> None:
        err0 = np.array([1.0, 2.0, 3.0, 4.0])
        errc = np.array([0.5, 1.5, 2.5, 3.5])
        cluster_idx = np.zeros(4, dtype=int)
        masks = {"all": np.ones(4, dtype=bool)}
        reps = hc.bootstrap_gains(err0, errc, cluster_idx, 1, masks, seed=1, b=25)
        point = hc.gain_from_errors(err0, errc, np.ones(4))
        self.assertTrue(np.allclose(reps["all"], point))

    def test_interval_undefined_below_min_valid(self) -> None:
        reps = np.full(2000, np.nan)
        self.assertIsNone(hc.percentile_interval(reps))
        reps[:1500] = 0.05
        interval = hc.percentile_interval(reps)
        self.assertIsNotNone(interval)
        lo, hi, n_valid = interval
        self.assertEqual(n_valid, 1500)
        self.assertAlmostEqual(lo, 0.05)
        self.assertAlmostEqual(hi, 0.05)


class ClauseStatusTests(unittest.TestCase):
    def test_pass_fail_inconclusive(self) -> None:
        ok_interval = [(0.04, 0.09, 2000), (0.035, 0.08, 2000)]
        self.assertEqual(
            hc.clause_status(
                0.05, fail_below=0.03, straddle=(0.0, 0.03),
                intervals=ok_interval, support_ok=True,
            ),
            "passed",
        )
        self.assertEqual(
            hc.clause_status(
                0.01, fail_below=0.03, straddle=(0.0, 0.03),
                intervals=[(0.005, 0.02, 2000), (0.004, 0.019, 2000)],
                support_ok=True,
            ),
            "failed",
        )
        # Interval straddling both the bar and 0 -> inconclusive,
        # even when the point estimate passes.
        self.assertEqual(
            hc.clause_status(
                0.05, fail_below=0.03, straddle=(0.0, 0.03),
                intervals=[(-0.01, 0.06, 2000), (0.04, 0.09, 2000)],
                support_ok=True,
            ),
            "inconclusive",
        )
        # Undefined interval -> inconclusive.
        self.assertEqual(
            hc.clause_status(
                0.05, fail_below=0.03, straddle=(0.0, 0.03),
                intervals=[None, (0.04, 0.09, 2000)], support_ok=True,
            ),
            "inconclusive",
        )
        # Support unmet -> inconclusive regardless of numbers.
        self.assertEqual(
            hc.clause_status(
                -1.0, fail_below=0.03, straddle=(0.0, 0.03),
                intervals=ok_interval, support_ok=False,
            ),
            "inconclusive",
        )

    def test_nonworsening_straddle_form(self) -> None:
        # Temporal/stations form: fail below 0; inconclusive when an
        # interval contains both >= 0 and <= -0.05.
        self.assertEqual(
            hc.clause_status(
                0.01, fail_below=0.0, straddle=(-0.05, 0.0),
                intervals=[(-0.06, 0.02, 2000), (0.0, 0.03, 2000)],
                support_ok=True,
            ),
            "inconclusive",
        )
        self.assertEqual(
            hc.clause_status(
                -0.01, fail_below=0.0, straddle=(-0.05, 0.0),
                intervals=[(-0.03, -0.005, 2000), (-0.02, -0.001, 2000)],
                support_ok=True,
            ),
            "failed",
        )


class ScipyGateTests(unittest.TestCase):
    def test_pinned_version_passes(self) -> None:
        self.assertEqual(hc.verify_scipy_version(), "1.18.1")


if __name__ == "__main__":
    unittest.main()
