"""Unit tests for the isotonic SLR elevation-ordering serving layer.

The layer (model-v3-isotonic-serving study; user decision 2026-11-10)
enforces the physical ordering SLR base <= mid_mountain <= summit on
served three-point resort windows by construction: unweighted PAVA on
``modelSlr`` within each eligible resort x arm x window triple, liquid
preserved bit-exactly, snowfall recomputed on changed records only,
raw values preserved per record in ``preIsotonicSlr``. It is a
consistency repair, not an accuracy change.
"""

from __future__ import annotations

import copy
import unittest

from slr_calibration.model_serving import (
    apply_isotonic_slr_layer,
    assert_frontend_within_lead_cap,
    frontend_arm_uniformity,
    isotonic_frontend_stats,
    pava_non_decreasing,
)


def _point(slug: str, role: str, elevation: float, rows_by_arm: dict) -> dict:
    return {
        "pointId": f"{slug}-{role}",
        "resortSlug": slug,
        "resortName": slug,
        "pointName": role,
        "role": role,
        "latitude": 39.0,
        "longitude": -120.0,
        "elevationM": elevation,
        "timezone": "America/Los_Angeles",
        "forecasts": rows_by_arm,
    }


def _row(slr: float, liquid: float = 10.0, lead: int = 5,
         start: str = "2026-11-15T15:00:00Z", end: str = "2026-11-16T15:00:00Z",
         date: str = "2026-11-16") -> dict:
    return {
        "leadDay": lead,
        "windowStartUtc": start,
        "windowEndUtc": end,
        "windowEndLocalDate": date,
        "modelSlr": slr,
        "modelSnowfallCm": slr * liquid / 10.0,
        "kucheraSlr": None,
        "kucheraSnowfallCm": None,
        "liquidMm": liquid,
    }


def _frontend(triples: list[tuple[str, str, list[float], float]]) -> dict:
    """Build a frontend from (slug, arm, [base, mid, summit] SLRs, liquid)."""
    points: dict[str, dict] = {}
    for slug, arm, slrs, liquid in triples:
        for role, elev, slr in zip(
                ("base", "mid_mountain", "summit"), (1000.0, 1500.0, 2000.0), slrs):
            pid = f"{slug}-{role}"
            if pid not in points:
                points[pid] = _point(slug, role, elev, {})
            points[pid]["forecasts"].setdefault(arm, []).append(_row(slr, liquid))
    return {"cycles": {"ecmwf": "2026-11-11T00:00:00Z"},
            "points": list(points.values())}


class PavaTest(unittest.TestCase):
    def test_mid_dip_pools_the_dip(self):
        self.assertEqual(pava_non_decreasing([12.0, 8.0, 13.0]), [10.0, 10.0, 13.0])

    def test_mid_peak_pools_the_peak(self):
        self.assertEqual(pava_non_decreasing([8.0, 14.0, 10.0]), [8.0, 12.0, 12.0])

    def test_monotone_down_pools_all(self):
        self.assertEqual(pava_non_decreasing([14.0, 10.0, 6.0]), [10.0, 10.0, 10.0])

    def test_iterative_pooling(self):
        self.assertEqual(pava_non_decreasing([10.0, 9.0, 9.5]), [9.5, 9.5, 9.5])

    def test_partial_pool_is_the_projection(self):
        # Codex gate-1 confirmation counterexample: (9, 9, 9) is
        # monotone with the right sum and range, but the L2 projection
        # pools only the violating pair.
        self.assertEqual(pava_non_decreasing([9.0, 8.0, 10.0]), [8.5, 8.5, 10.0])

    def test_ordered_and_flat_inputs_unchanged(self):
        self.assertEqual(pava_non_decreasing([1.0, 2.0, 3.0]), [1.0, 2.0, 3.0])
        self.assertEqual(pava_non_decreasing([5.0, 5.0, 5.0]), [5.0, 5.0, 5.0])


class IsotonicLayerTest(unittest.TestCase):
    def test_violating_window_adjusted_with_provenance(self):
        frontend = _frontend([("resort-a", "ecmwf", [12.0, 8.0, 13.0], 10.0)])
        adjusted, report = apply_isotonic_slr_layer(frontend)
        rows = {p["role"]: p["forecasts"]["ecmwf"][0] for p in adjusted["points"]}
        self.assertEqual([rows[r]["modelSlr"] for r in ("base", "mid_mountain", "summit")],
                         [10.0, 10.0, 13.0])
        # Changed records carry the raw value and a recomputed snowfall;
        # the unchanged summit record is fully untouched.
        self.assertEqual(rows["base"]["preIsotonicSlr"], 12.0)
        self.assertEqual(rows["mid_mountain"]["preIsotonicSlr"], 8.0)
        self.assertEqual(rows["base"]["preIsotonicSnowfallCm"], 12.0)
        self.assertEqual(rows["mid_mountain"]["preIsotonicSnowfallCm"], 8.0)
        self.assertNotIn("preIsotonicSlr", rows["summit"])
        self.assertNotIn("preIsotonicSnowfallCm", rows["summit"])
        self.assertEqual(rows["base"]["modelSnowfallCm"], 10.0)  # 10 * 10 / 10
        self.assertEqual(rows["summit"]["modelSnowfallCm"], 13.0)
        for row in rows.values():
            self.assertEqual(row["liquidMm"], 10.0)
        self.assertEqual(report["adjusted_windows"], 1)
        self.assertEqual(report["records_changed"], 2)
        meta = adjusted["consistencyAdjustment"]
        self.assertEqual(meta["adjustedWindows"], 1)
        self.assertEqual(meta["adjustedRecords"], 2)
        self.assertEqual(meta["populationWindows"], 1)

    def test_ordered_window_bit_identical(self):
        # Heavenly-shaped control (window ending 2026-11-18 values).
        frontend = _frontend([("heavenly", "ecmwf", [13.218, 13.500, 14.892], 1.4043)])
        adjusted, report = apply_isotonic_slr_layer(frontend)
        self.assertEqual(report["records_changed"], 0)
        self.assertEqual(report["adjusted_windows"], 0)
        before = copy.deepcopy(frontend)
        after = copy.deepcopy(adjusted)
        after.pop("consistencyAdjustment")
        self.assertEqual(after, before)

    def test_input_is_not_mutated(self):
        frontend = _frontend([("resort-a", "ecmwf", [14.0, 10.0, 6.0], 10.0)])
        snapshot = copy.deepcopy(frontend)
        apply_isotonic_slr_layer(frontend)
        self.assertEqual(frontend, snapshot)

    def test_grouping_isolation_across_arms_windows_and_resorts(self):
        frontend = _frontend([
            ("resort-a", "ecmwf", [14.0, 10.0, 6.0], 10.0),
            ("resort-a", "hrrr", [6.0, 10.0, 14.0], 10.0),
            ("resort-b", "ecmwf", [14.0, 10.0, 6.0], 10.0),
        ])
        adjusted, report = apply_isotonic_slr_layer(frontend)
        self.assertEqual(report["eligible_windows"], 3)
        self.assertEqual(report["adjusted_windows"], 2)
        stats = isotonic_frontend_stats(adjusted)
        self.assertEqual(stats["violating"], 0)
        hrrr_rows = [p for p in adjusted["points"] if p["resortSlug"] == "resort-a"]
        for p in hrrr_rows:
            self.assertNotIn("preIsotonicSlr", p["forecasts"]["hrrr"][0])

    def test_incomplete_triple_untouched(self):
        frontend = _frontend([("resort-a", "ecmwf", [14.0, 10.0, 6.0], 10.0)])
        # Drop the summit point entirely.
        frontend["points"] = [p for p in frontend["points"] if p["role"] != "summit"]
        adjusted, report = apply_isotonic_slr_layer(frontend)
        self.assertEqual(report["eligible_windows"], 0)
        self.assertEqual(report["ineligible_groups"], 1)
        self.assertEqual(report["records_changed"], 0)
        slrs = sorted((p["role"], p["forecasts"]["ecmwf"][0]["modelSlr"])
                      for p in adjusted["points"])
        self.assertEqual(slrs, [("base", 14.0), ("mid_mountain", 10.0)])

    def test_missing_value_group_untouched(self):
        frontend = _frontend([("resort-a", "ecmwf", [14.0, 10.0, 6.0], 10.0)])
        for p in frontend["points"]:
            if p["role"] == "mid_mountain":
                p["forecasts"]["ecmwf"][0]["modelSlr"] = None
        adjusted, report = apply_isotonic_slr_layer(frontend)
        self.assertEqual(report["eligible_windows"], 0)
        self.assertEqual(report["records_changed"], 0)

    def test_elevation_order_mismatch_untouched(self):
        frontend = _frontend([("resort-a", "ecmwf", [14.0, 10.0, 6.0], 10.0)])
        for p in frontend["points"]:
            if p["role"] == "mid_mountain":
                p["elevationM"] = 500.0  # below base: not an ordered triple
        adjusted, report = apply_isotonic_slr_layer(frontend)
        self.assertEqual(report["eligible_windows"], 0)
        self.assertEqual(report["records_changed"], 0)

    def test_field_disagreement_untouched(self):
        frontend = _frontend([("resort-a", "ecmwf", [14.0, 10.0, 6.0], 10.0)])
        for p in frontend["points"]:
            if p["role"] == "summit":
                p["forecasts"]["ecmwf"][0]["leadDay"] = 6
        adjusted, report = apply_isotonic_slr_layer(frontend)
        self.assertEqual(report["eligible_windows"], 0)
        self.assertEqual(report["records_changed"], 0)

    def test_clamp_bounds_preserved(self):
        frontend = _frontend([
            ("resort-a", "ecmwf", [30.0, 3.0, 3.0], 10.0),
            ("resort-b", "ecmwf", [3.0, 30.0, 29.0], 10.0),
        ])
        adjusted, _report = apply_isotonic_slr_layer(frontend)
        for p in adjusted["points"]:
            value = p["forecasts"]["ecmwf"][0]["modelSlr"]
            self.assertGreaterEqual(value, 3.0)
            self.assertLessEqual(value, 30.0)
        self.assertEqual(isotonic_frontend_stats(adjusted)["violating"], 0)

    def test_lead_cap_still_passes_after_layer(self):
        frontend = _frontend([("resort-a", "ecmwf", [14.0, 10.0, 6.0], 10.0)])
        for p in frontend["points"]:
            p["forecasts"]["ecmwf"][0]["leadDay"] = 10
        adjusted, _report = apply_isotonic_slr_layer(frontend)
        assert_frontend_within_lead_cap(adjusted)  # must not raise
        for p in adjusted["points"]:
            self.assertEqual(p["forecasts"]["ecmwf"][0]["leadDay"], 10)

    def test_already_adjusted_input_is_rejected(self):
        frontend = _frontend([("resort-a", "ecmwf", [14.0, 10.0, 6.0], 10.0)])
        adjusted, _report = apply_isotonic_slr_layer(frontend)
        with self.assertRaises(ValueError):
            apply_isotonic_slr_layer(adjusted)
        # A stray record-level marker without the batch block is also
        # rejected (replay can never stack provenance).
        frontend2 = _frontend([("resort-a", "ecmwf", [14.0, 10.0, 6.0], 10.0)])
        frontend2["points"][0]["forecasts"]["ecmwf"][0]["preIsotonicSlr"] = 1.0
        with self.assertRaises(ValueError):
            apply_isotonic_slr_layer(frontend2)

    def test_ineligible_reasons_are_enumerated(self):
        frontend = _frontend([("resort-a", "ecmwf", [14.0, 10.0, 6.0], 10.0)])
        frontend["points"] = [p for p in frontend["points"] if p["role"] != "summit"]
        _adjusted, report = apply_isotonic_slr_layer(frontend)
        self.assertEqual(report["ineligible_reasons"],
                         {"incomplete_or_duplicate_roles": 1})

    def test_arm_uniformity_audit(self):
        frontend = _frontend([
            ("resort-a", "ecmwf", [14.0, 10.0, 6.0], 10.0),
            ("resort-a", "hrrr", [6.0, 10.0, 14.0], 10.0),
        ])
        self.assertEqual(frontend_arm_uniformity(frontend), [])
        # Remove the summit point's HRRR row: its arm set now differs.
        for p in frontend["points"]:
            if p["role"] == "summit":
                del p["forecasts"]["hrrr"]
        flagged = frontend_arm_uniformity(frontend)
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["resort"], "resort-a")

    def test_stats_on_raw_frontend(self):
        frontend = _frontend([
            ("resort-a", "ecmwf", [14.0, 10.0, 6.0], 10.0),
            ("resort-a", "hrrr", [6.0, 10.0, 14.0], 10.0),
        ])
        stats = isotonic_frontend_stats(frontend)
        self.assertEqual(stats["windows"], 2)
        self.assertEqual(stats["records"], 6)
        self.assertEqual(stats["violating"], 1)
        self.assertEqual(stats["total_magnitude"], 8.0)


if __name__ == "__main__":
    unittest.main()
