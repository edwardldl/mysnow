"""Queue item 7 — monotone-constrained refit helpers and GBM support."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from slr_calibration.gold_model import GBMEBModel
from slr_calibration.monotone_experiment import (
    CONSTRAINT_SIGNS,
    GBM_MONO_SPEC,
    build_triples,
    direction_audit,
    inversion_stats,
    monotonic_cst_vector,
    noninferiority_verdict,
    physical_success,
    screen_kill,
    window_inversions,
)
from slr_calibration.prediction_cache import MODEL_SPECS

COLUMNS = [
    "t_300", "t_600", "t_900", "t_1200", "t_1500", "t_1800", "t_2100", "t_2400",
    "ws_300", "ws_600", "ws_900", "ws_1200", "ws_1500", "ws_1800", "ws_2100", "ws_2400",
    "rh_300", "rh_600", "rh_900", "rh_1200", "rh_1500", "rh_1800", "rh_2100", "rh_2400",
    "latitude", "longitude", "elevation_m", "fcst_liquid_mm", "precip_rate_mean",
    "precip_rate_max", "precip_rate_p90", "precip_hours_gt_1mm", "precip_first_half_frac",
    "agg_layer_depth_m", "fall_distance_below_dgz_m", "tmax_column_pwt",
    "tmax_warmnose_pwt", "freezing_level_m_agl_pwt", "warm_layer_depth_m_pwt",
    "sfc_wetbulb_c_pwt", "sfc_wetbulb_d2m_c_pwt", "sfc_dewpoint_depression_c_pwt",
    "tclw_pwt", "tclw_max", "ws10m_pwt", "ws10m_max", "skt_c_pwt", "dgz_t_pwt",
    "dgz_dist_optimum_pwt", "dgz_depth_m_pwt", "dgz_precip_frac", "habit_frac_dendrite",
    "habit_frac_plate", "habit_frac_other", "riming_rh_pwt", "riming_omega_pwt",
    "riming_index_pwt", "omega_dgz_pwt", "omega_dgz_min", "cobb_overlap",
    "cobb_overlap_m_pwt", "subcloud_dewp_depression", "subcloud_unsat_depth_m",
    "subcloud_warm_depth_m",
]


class ConstraintVectorTest(unittest.TestCase):
    def test_canonical_64_alignment(self) -> None:
        self.assertEqual(len(COLUMNS), 64)
        vec = monotonic_cst_vector(COLUMNS)
        self.assertEqual(len(vec), 64)
        nonzero = {c: v for c, v in zip(COLUMNS, vec) if v != 0}
        self.assertEqual(nonzero, CONSTRAINT_SIGNS)
        self.assertEqual(vec[COLUMNS.index("elevation_m")], 1)
        self.assertEqual(vec[COLUMNS.index("subcloud_warm_depth_m")], -1)

    def test_spec_string_matches_cache_spec(self) -> None:
        self.assertEqual(MODEL_SPECS["gbm_eb_mono"]["estimator"], GBM_MONO_SPEC)
        self.assertEqual(
            MODEL_SPECS["ens_eb_mono"]["derived_from"], ("rf_eb", "gbm_eb_mono")
        )
        for col, sign in CONSTRAINT_SIGNS.items():
            self.assertIn(f"{col}:{'+' if sign > 0 else '-'}{abs(sign)}", GBM_MONO_SPEC)


def _screen(**over):
    base = {
        "pooled_loro_gain": 0.0,
        "loro_region_gain": {r: 0.0 for r in ("alaska", "rockies")},
        "sampled_family_gain": {"randomised_rep0": 0.0, "temporal": 0.0, "stations": 0.0},
    }
    base.update(over)
    return base


class ScreenKillTest(unittest.TestCase):
    def test_passes_at_zero(self) -> None:
        killed, clauses = screen_kill(_screen())
        self.assertFalse(killed)
        self.assertEqual(clauses, [])

    def test_kills_on_loro(self) -> None:
        killed, clauses = screen_kill(_screen(pooled_loro_gain=-0.031))
        self.assertTrue(killed)
        self.assertEqual(len(clauses), 1)

    def test_kills_on_region_and_sampled(self) -> None:
        killed, clauses = screen_kill(_screen(
            loro_region_gain={"alaska": -0.06, "rockies": 0.0},
            sampled_family_gain={"randomised_rep0": 0.0, "temporal": -0.11, "stations": 0.0},
        ))
        self.assertTrue(killed)
        self.assertEqual(len(clauses), 2)


def _comp(**over):
    comp = {
        "four_family_gain": 0.0,
        "family_gain": {"loro": 0.0, "temporal": 0.0, "stations": 0.0, "randomised": 0.0},
        "loro_region_gain": {"alaska": 0.0, "rockies": 0.0},
        "slices": [],
    }
    comp.update(over)
    return comp


class NoninferiorityTest(unittest.TestCase):
    def test_passes_at_zero(self) -> None:
        ok, clauses = noninferiority_verdict(_comp())
        self.assertTrue(ok)
        self.assertEqual(clauses, [])

    def test_margin_boundaries(self) -> None:
        ok, _ = noninferiority_verdict(_comp(four_family_gain=-0.010))
        self.assertTrue(ok)
        ok, clauses = noninferiority_verdict(_comp(four_family_gain=-0.011))
        self.assertFalse(ok)
        self.assertEqual(len(clauses), 1)

    def test_slice_guard(self) -> None:
        ok, clauses = noninferiority_verdict(_comp(slices=[
            {"family": "temporal", "slice": "slr_lt_5", "n": 250, "mae_diff": 0.21},
        ]))
        self.assertFalse(ok)
        ok, _ = noninferiority_verdict(_comp(slices=[
            {"family": "temporal", "slice": "slr_lt_5", "n": 199, "mae_diff": 0.21},
        ]))
        self.assertTrue(ok)


class InversionStatsTest(unittest.TestCase):
    def test_window_inversions(self) -> None:
        self.assertEqual(window_inversions((10.0, 9.0, 11.0)), (1.0, 0.0, 1.0))
        self.assertEqual(window_inversions((10.0, 11.0, 10.5)), (0.0, 0.5, 0.5))
        self.assertEqual(window_inversions((10.0, 11.0, 12.0)), (0.0, 0.0, 0.0))

    def test_stats_and_tolerance(self) -> None:
        stats = inversion_stats([(10.0, 9.0, 11.0), (10.0, 10.0, 10.0),
                                 (10.0, 10.0 - 5e-7, 12.0)])
        self.assertEqual(stats["windows"], 3)
        self.assertEqual(stats["violating"], 1)  # 5e-7 < tol 1e-6
        self.assertAlmostEqual(stats["total_magnitude"], 1.0, places=5)
        self.assertEqual(stats["max_single_step"], 1.0)
        self.assertEqual(stats["windows_with_tie"], 2)

    def test_physical_success_boundaries(self) -> None:
        champ = {"violating": 611, "total_magnitude": 100.0, "max_window_magnitude": 3.0}
        ok, _ = physical_success(champ, {"violating": 61, "total_magnitude": 10.0,
                                         "max_window_magnitude": 1.0})
        self.assertTrue(ok)
        ok, clauses = physical_success(champ, {"violating": 62, "total_magnitude": 10.0,
                                               "max_window_magnitude": 1.0})
        self.assertFalse(ok)
        self.assertEqual(len(clauses), 1)
        ok, _ = physical_success(champ, {"violating": 61, "total_magnitude": 10.0,
                                         "max_window_magnitude": 1.01})
        self.assertFalse(ok)


def _rows_frame(values: dict[str, float]) -> pd.DataFrame:
    rows = []
    for role in ("base", "mid_mountain", "summit"):
        row = {"point_id": f"resort-{role}", "source": "ecmwf",
               "window_end_local_date": "2026-11-18", "slr": values[role]}
        for col, sign in CONSTRAINT_SIGNS.items():
            row[col] = 0.0
        rows.append(row)
    return pd.DataFrame(rows)


class TriplesAndAuditTest(unittest.TestCase):
    def test_build_triples(self) -> None:
        frame = _rows_frame({"base": 10.0, "mid_mountain": 11.0, "summit": 12.0})
        triples = build_triples(frame, "slr")
        self.assertEqual(list(triples.values()), [(10.0, 11.0, 12.0)])

    def test_build_triples_duplicate_raises(self) -> None:
        frame = _rows_frame({"base": 10.0, "mid_mountain": 11.0, "summit": 12.0})
        frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
        with self.assertRaises(ValueError):
            build_triples(frame, "slr")

    def test_direction_audit(self) -> None:
        frame = _rows_frame({"base": 10.0, "mid_mountain": 11.0, "summit": 12.0})
        # elevation rises; warmth features fall; one NaN transition.
        frame["elevation_m"] = [1000.0, 2000.0, 3000.0]
        frame["tmax_column_pwt"] = [-2.0, -4.0, -6.0]
        frame.loc[1, "warm_layer_depth_m_pwt"] = np.nan
        frame["subcloud_warm_depth_m"] = [0.0, 50.0, 0.0]  # rises base->mid: fail
        audit = direction_audit(frame)
        self.assertEqual(audit["elevation_m"]["direction_fail"], 0)
        self.assertEqual(audit["tmax_column_pwt"]["direction_fail"], 0)
        self.assertEqual(audit["warm_layer_depth_m_pwt"]["nan_transition"], 2)
        self.assertEqual(audit["subcloud_warm_depth_m"]["direction_fail"], 1)


def _train_frame(n: int = 240) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    elev = rng.uniform(500, 3500, n)
    tmax = rng.uniform(-18, 2, n)
    y = np.clip(8 + 0.002 * elev - 0.5 * tmax + rng.normal(0, 0.3, n), 3.5, 29)
    rows = []
    for i in range(n):
        rows.append({
            "event_id": f"e{i}", "station_id": f"st{i % 24}", "site_id": f"st{i % 24}",
            "valid_time": "2024-01-15T07:00:00-06:00", "latitude": 40.0,
            "longitude": -106.0, "observed_slr": float(y[i]), "baseline_slr": 11.0,
            "climate_regime": "continental_mountain",
            "elevation_m": float(elev[i]), "tmax_column_pwt": float(tmax[i]),
        })
    return pd.DataFrame(rows)


class GBMMonotonicTest(unittest.TestCase):
    def test_vector_validation(self) -> None:
        with self.assertRaises(ValueError):
            GBMEBModel(seed=0, columns=["elevation_m"], monotonic_cst=[1, -1])
        with self.assertRaises(ValueError):
            GBMEBModel(seed=0, columns=["elevation_m"], monotonic_cst=[2])

    def test_default_is_unconstrained(self) -> None:
        model = GBMEBModel(seed=0, columns=["elevation_m"])
        self.assertIsNone(model.monotonic_cst)

    def test_constrained_fit_respects_direction(self) -> None:
        frame = _train_frame()
        cols = ["elevation_m", "tmax_column_pwt"]
        model = GBMEBModel(seed=0, columns=cols, monotonic_cst=[1, -1]).fit(frame)
        probe = pd.concat([frame.iloc[[0]], frame.iloc[[0]]], ignore_index=True)
        probe.loc[0, "elevation_m"] = 500.0
        probe.loc[1, "elevation_m"] = 3500.0
        raw, _ = model.predict_parts(probe)
        self.assertGreaterEqual(raw[1] + 1e-9, raw[0])
        self.assertEqual(list(model.gbm.get_params()["monotonic_cst"]), [1, -1])


if __name__ == "__main__":
    unittest.main()
