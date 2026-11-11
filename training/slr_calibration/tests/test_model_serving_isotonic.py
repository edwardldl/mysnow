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
    ISOTONIC_SLR_LAYER,
    ISOTONIC_SLR_LAYER_VERSION,
    apply_isotonic_slr_layer,
    apply_isotonic_slr_layer_gated,
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


class IsotonicGateTest(unittest.TestCase):
    """Dormancy gate (user decision 2026-11-10): the layer is opt-in.

    A default serving run must be dormant and bit-identical to the
    pre-layer behaviour; an explicitly enabled run must reproduce the
    direct layer exactly. The run manifest records the layer state
    either way via the returned report.
    """

    def _violating_frontend(self) -> dict:
        return _frontend([
            ("resort-a", "ecmwf", [14.0, 10.0, 6.0], 10.0),
            ("resort-a", "hrrr", [6.0, 10.0, 14.0], 10.0),
        ])

    def test_disabled_is_dormant_and_bit_identical(self):
        frontend = self._violating_frontend()
        snapshot = copy.deepcopy(frontend)
        out, report = apply_isotonic_slr_layer_gated(frontend, enabled=False)
        # Output is the pre-layer document, value-for-value: no adjusted
        # values, no provenance fields, no consistency block.
        self.assertEqual(out, snapshot)
        self.assertNotIn("consistencyAdjustment", out)
        for point in out["points"]:
            for rows in point["forecasts"].values():
                for row in rows:
                    self.assertNotIn("preIsotonicSlr", row)
                    self.assertNotIn("preIsotonicSnowfallCm", row)
        # Input not mutated either.
        self.assertEqual(frontend, snapshot)
        # Manifest state: disabled, with identity and version.
        self.assertEqual(report["layer"], ISOTONIC_SLR_LAYER)
        self.assertEqual(report["version"], ISOTONIC_SLR_LAYER_VERSION)
        self.assertIs(report["enabled"], False)
        self.assertEqual(report["adjusted_windows"], 0)
        self.assertEqual(report["records_changed"], 0)

    def test_enabled_matches_direct_layer_exactly(self):
        frontend = self._violating_frontend()
        out_gated, report = apply_isotonic_slr_layer_gated(frontend, enabled=True)
        out_direct, direct_report = apply_isotonic_slr_layer(frontend)
        self.assertEqual(out_gated, out_direct)
        self.assertIn("consistencyAdjustment", out_gated)
        self.assertIs(report["enabled"], True)
        self.assertEqual(report["layer"], ISOTONIC_SLR_LAYER)
        self.assertEqual(report["version"], ISOTONIC_SLR_LAYER_VERSION)
        self.assertEqual(report["adjusted_windows"],
                         direct_report["adjusted_windows"])
        self.assertEqual(report["records_changed"],
                         direct_report["records_changed"])
        self.assertGreater(report["records_changed"], 0)


class RunnerIsotonicFlagTest(unittest.TestCase):
    """The serving runner's --isotonic-layer flag defaults to OFF."""

    def _parser(self):
        import importlib.util
        import sys
        from pathlib import Path as _Path
        root = _Path(__file__).resolve().parents[1]
        for sub in ("src", "scripts"):
            if str(root / sub) not in sys.path:
                sys.path.insert(0, str(root / sub))
        spec = importlib.util.spec_from_file_location(
            "run_model_serving", root / "scripts" / "run_model_serving.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.build_arg_parser()

    _REQUIRED = ["--registry", "r.json", "--champion-dir", "c",
                 "--out-dir", "o", "--tracking-records", "t.jsonl",
                 "--frontend-json", "f.json"]

    def test_flag_defaults_off(self):
        args = self._parser().parse_args(self._REQUIRED)
        self.assertIs(args.isotonic_layer, False)

    def test_flag_opts_in(self):
        args = self._parser().parse_args(self._REQUIRED + ["--isotonic-layer"])
        self.assertIs(args.isotonic_layer, True)


class RunnerPersistenceTest(unittest.TestCase):
    """Deterministic end-to-end runner test (Codex gate fix, 2026-11-10).

    Executes run_model_serving.main() with the network/arm builders
    stubbed and a frozen clock, in both CLI modes, and asserts on the
    PERSISTED artefacts: the written frontend bytes and the written
    run manifest. Disabled: the frontend is the pre-layer document and
    the manifest records the layer disabled. Enabled: the written
    frontend bytes equal the serialisation of the direct layer applied
    to the disabled document, and the manifest report equals the
    direct-layer report plus the gate state fields.
    """

    FIXED_NOW = "2026-11-11T00:00:00+00:00"

    def _load_runner(self):
        import importlib.util
        import sys
        from pathlib import Path as _Path
        root = _Path(__file__).resolve().parents[1]
        for sub in ("src", "scripts"):
            if str(root / sub) not in sys.path:
                sys.path.insert(0, str(root / sub))
        spec = importlib.util.spec_from_file_location(
            "run_model_serving", root / "scripts" / "run_model_serving.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _run_main(self, module, tmp: Path, extra_args: list[str]) -> int:
        import sys
        from unittest import mock
        argv = ["run_model_serving",
                "--registry", str(tmp / "registry.json"),
                "--champion-dir", str(tmp / "champion"),
                "--date", "20261110", "--cycle", "0",
                "--sources", "ecmwf",
                "--out-dir", str(tmp / "out"),
                "--tracking-records", str(tmp / "tracking.jsonl"),
                "--frontend-json", str(tmp / "frontend.json")] + extra_args
        with mock.patch.object(sys, "argv", argv):
            return module.main()

    def test_runner_persists_dormant_and_enabled_modes(self):
        import json
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path as _Path
        from unittest import mock

        module = self._load_runner()

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 11, 0, 0, tzinfo=timezone.utc)

        points = []
        for role, elev in (("base", 2000.0), ("mid_mountain", 2500.0),
                           ("summit", 3000.0)):
            points.append({
                "point_id": f"resort-a-{role}", "resort_slug": "resort-a",
                "resort_name": "Resort A", "point_name": role, "role": role,
                "latitude": 39.0, "longitude": -120.0, "elevation_m": elev,
                "timezone": "America/Los_Angeles",
                "geometry_source": "estimated", "region": "sierra-nevada",
            })

        def fake_score_arm(arm, source_id, leads, cycle_dt, pts, series,
                           model, features):
            records = []
            for point, slr in zip(pts, (14.0, 10.0, 6.0)):
                records.append({
                    "source": arm, "point_id": point["point_id"],
                    "resort_name": "Resort A", "point_name": point["role"],
                    "role": point["role"], "lead_day": 5,
                    "window_end_local_date": "2026-11-16",
                    "window_start_utc": "2026-11-15T15:00:00Z",
                    "window_end_utc": "2026-11-16T15:00:00Z",
                    "model_slr": slr, "model_snowfall_cm": slr * 10.0 / 10.0,
                    "kuchera_slr": None, "kuchera_snowfall_cm": None,
                    "liquid_mm": 10.0, "missing": False,
                    "missing_reason": None,
                })
            return records, []

        results = {}
        for mode, extra in (("disabled", []), ("enabled", ["--isotonic-layer"])):
            with tempfile.TemporaryDirectory() as td:
                tmp = _Path(td)
                (tmp / "champion").mkdir()
                (tmp / "champion" / "champion_sidecar.json").write_text(
                    json.dumps({"features": []}))
                with mock.patch.object(module, "load_catalogue",
                                       lambda path: points), \
                     mock.patch.object(module, "build_ecmwf_series",
                                       lambda pts, date, cycle: ({}, {})), \
                     mock.patch.object(module, "score_arm", fake_score_arm), \
                     mock.patch.object(module.ChampionModel, "load",
                                       classmethod(lambda cls, d: object())), \
                     mock.patch.object(module, "datetime", _FixedDatetime):
                    rc = self._run_main(module, tmp, extra)
                self.assertEqual(rc, 0)
                frontend_bytes = (tmp / "frontend.json").read_bytes()
                manifest = json.loads((tmp / "out" / "run_manifest.json")
                                      .read_text())
                results[mode] = (frontend_bytes, manifest)

        disabled_bytes, disabled_manifest = results["disabled"]
        enabled_bytes, enabled_manifest = results["enabled"]
        disabled_doc = json.loads(disabled_bytes)

        # Disabled: persisted frontend is the pre-layer document.
        self.assertNotIn(b"preIsotonicSlr", disabled_bytes)
        self.assertNotIn(b"consistencyAdjustment", disabled_bytes)
        slrs = [row["modelSlr"] for point in disabled_doc["points"]
                for rows in point["forecasts"].values() for row in rows]
        self.assertEqual(sorted(slrs), [6.0, 10.0, 14.0])
        # Disabled: manifest records layer identity/version/state.
        state = disabled_manifest["isotonic_layer"]
        self.assertEqual(state["layer"], ISOTONIC_SLR_LAYER)
        self.assertEqual(state["version"], ISOTONIC_SLR_LAYER_VERSION)
        self.assertIs(state["enabled"], False)
        self.assertEqual(state["records_changed"], 0)

        # Enabled: persisted bytes are exactly the serialised direct
        # layer applied to the disabled document (same frozen clock, so
        # the documents differ only by the layer).
        expected_doc, direct_report = apply_isotonic_slr_layer(disabled_doc)
        self.assertEqual(enabled_bytes,
                         json.dumps(expected_doc, indent=1).encode())
        # Enabled: manifest report = direct report + gate state fields.
        estate = enabled_manifest["isotonic_layer"]
        self.assertIs(estate["enabled"], True)
        self.assertEqual(estate["layer"], ISOTONIC_SLR_LAYER)
        self.assertEqual(estate["version"], ISOTONIC_SLR_LAYER_VERSION)
        for key, value in direct_report.items():
            self.assertEqual(estate[key], value)
        self.assertGreater(estate["records_changed"], 0)


if __name__ == "__main__":
    unittest.main()
