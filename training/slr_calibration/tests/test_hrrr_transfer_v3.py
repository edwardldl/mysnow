from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from slr_calibration.gold_profiles import PRESSURE_LEVELS_HPA
from slr_calibration.hrrr_transfer import WANTED_VARIABLES, cycle_for_instant
from slr_calibration.hrrr_transfer_v3 import (
    NEW_VARIABLES,
    V3_WANTED_VARIABLES,
    _complete_level_dict,
    _tclw_for_row,
    assemble_event_series,
    decision_outcome,
    eligible_events,
    lead_band,
    paired_block,
)
from slr_calibration.model_serving import tclw_from_hydrometeors

UTC = timezone.utc


def _event(valid: datetime, region: str = "rockies") -> dict:
    return {
        "event_id": "ghcn:TEST1:2024-01-10",
        "region": region,
        "station_id": "TEST1",
        "site_id": "TEST1",
        "station_name": "Test",
        "state": "CO",
        "valid_time": valid,
        "observation_date": valid.date().isoformat(),
        "water_year": 2024,
        "month": valid.month,
        "latitude": 39.0,
        "longitude": -106.0,
        "elevation_m": 1500.0,
        "observed_slr": 10.0,
        "fresh_snow_mm": 100.0,
        "fresh_swe_mm": 10.0,
        "climate_regime": "continental",
        "source_tier": "test",
    }


class VariableSpecTests(unittest.TestCase):
    def test_new_variables_complete(self) -> None:
        keys = [k for k, _v, _l in NEW_VARIABLES]
        self.assertEqual(len(keys), 56)
        self.assertEqual(len(set(keys)), 56)
        for prefix, var in (("w", "VVEL"), ("q", "SPFH"), ("clmr", "CLMR"), ("rwmr", "RWMR")):
            for level in PRESSURE_LEVELS_HPA:
                self.assertIn((f"{prefix}_{level}", var, f"{level} mb"), NEW_VARIABLES)
        self.assertIn(("dpt2m_k", "DPT", "2 m above ground"), NEW_VARIABLES)
        self.assertIn(("u10", "UGRD", "10 m above ground"), NEW_VARIABLES)
        self.assertIn(("v10", "VGRD", "10 m above ground"), NEW_VARIABLES)
        self.assertIn(("skt_k", "TMP", "surface"), NEW_VARIABLES)

    def test_v3_wanted_is_old_plus_new(self) -> None:
        self.assertEqual(len(V3_WANTED_VARIABLES), len(WANTED_VARIABLES) + 56)
        old_keys = {k for k, _v, _l in WANTED_VARIABLES}
        self.assertTrue(old_keys.issubset({k for k, _v, _l in V3_WANTED_VARIABLES}))


class EligibilityTests(unittest.TestCase):
    def test_cutoff(self) -> None:
        before = _event(datetime(2020, 12, 3, 12, tzinfo=UTC))  # start Dec 2 12Z
        after = _event(datetime(2020, 12, 4, 0, tzinfo=UTC))     # start Dec 3 00Z
        old = _event(datetime(2019, 1, 10, 12, tzinfo=UTC))
        kept = eligible_events([before, after, old])
        self.assertEqual(kept, [after])

    def test_lead_band(self) -> None:
        self.assertEqual(lead_band(_event(datetime(2024, 1, 10, tzinfo=UTC), "alaska")), "1-3h")
        self.assertEqual(lead_band(_event(datetime(2024, 1, 10, tzinfo=UTC), "rockies")), "1h")


class LevelDictTests(unittest.TestCase):
    def test_complete_and_incomplete(self) -> None:
        row = {f"w_{lvl}": 0.1 for lvl in PRESSURE_LEVELS_HPA}
        out = _complete_level_dict(row, "w")
        self.assertIsNotNone(out)
        self.assertEqual(len(out), 13)
        row["w_500"] = None
        self.assertIsNone(_complete_level_dict(row, "w"))
        del row["w_700"]
        row["w_500"] = 0.2
        self.assertIsNone(_complete_level_dict(row, "w"))


class TclwTests(unittest.TestCase):
    def _row(self) -> dict:
        row = {}
        for i, lvl in enumerate(PRESSURE_LEVELS_HPA):
            row[f"clmr_{lvl}"] = 0.001
            row[f"rwmr_{lvl}"] = 0.0005
            row[f"hgt_{lvl}"] = 100.0 + i * 400.0
        return row

    def test_matches_serving_function(self) -> None:
        row = self._row()
        levels_desc = sorted(PRESSURE_LEVELS_HPA, reverse=True)
        expected = tclw_from_hydrometeors(
            levels_desc,
            [row[f"clmr_{p}"] + row[f"rwmr_{p}"] for p in levels_desc],
            [row[f"hgt_{p}"] for p in levels_desc],
            200.0,
        )
        self.assertAlmostEqual(_tclw_for_row(row, 200.0), expected)
        self.assertGreater(expected, 0.0)

    def test_missing_level_is_none(self) -> None:
        row = self._row()
        row["clmr_850"] = None
        self.assertIsNone(_tclw_for_row(row, 200.0))


def _points_row(apcp: float) -> dict:
    row: dict = {"t2m_k": 270.0, "apcp_mm": apcp, "asnow_m": 0.01}
    for i, lvl in enumerate(PRESSURE_LEVELS_HPA):
        row[f"t_{lvl}"] = 260.0 + i
        row[f"rh_{lvl}"] = 80.0
        row[f"u_{lvl}"] = 3.0
        row[f"v_{lvl}"] = 4.0
        row[f"hgt_{lvl}"] = 100.0 + i * 400.0
        row[f"w_{lvl}"] = -0.5
        row[f"q_{lvl}"] = 0.002
        row[f"clmr_{lvl}"] = 0.001
        row[f"rwmr_{lvl}"] = 0.0
    row["dpt2m_k"] = 268.0
    row["u10"] = 2.0
    row["v10"] = 1.0
    row["skt_k"] = 269.0
    return row


class AssemblyTests(unittest.TestCase):
    def test_conus_event_series(self) -> None:
        event = _event(datetime(2024, 1, 10, 12, tzinfo=UTC))

        def load_points(domain, cycle, fxx, reduced):
            self.assertEqual(domain, "conus")
            self.assertEqual(fxx, 1)
            return {"TEST1": _points_row(2.5)}

        hours, w, q, acc, sfc, max_fxx = assemble_event_series(event, load_points)
        self.assertEqual(len(hours), 25)
        self.assertEqual(len(w), 25)
        self.assertEqual(len(q), 25)
        self.assertEqual(max_fxx, 1)
        first = acc[datetime(2024, 1, 9, 12, tzinfo=UTC)]
        self.assertEqual(first["tp_mm"], 2.5)
        self.assertIsNone(first["cp_mm"])
        self.assertIsNone(first["lsp_mm"])
        sfc_row = sfc[datetime(2024, 1, 9, 12, tzinfo=UTC)]
        self.assertEqual(sfc_row["dewpoint_2m"], 268.0)
        self.assertIsNotNone(sfc_row["total_column_liquid_water"])

    def test_missing_files_yield_empty_series(self) -> None:
        event = _event(datetime(2024, 1, 10, 12, tzinfo=UTC))
        hours, w, q, acc, sfc, _ = assemble_event_series(event, lambda *a: None)
        self.assertEqual((len(hours), len(w), len(q), len(acc), len(sfc)), (0, 0, 0, 0, 0))

    def test_partial_w_levels_drop_the_instant(self) -> None:
        event = _event(datetime(2024, 1, 10, 12, tzinfo=UTC))

        def load_points(domain, cycle, fxx, reduced):
            row = _points_row(1.0)
            row["w_700"] = None
            return {"TEST1": row}

        hours, w, q, acc, sfc, _ = assemble_event_series(event, load_points)
        self.assertEqual(len(hours), 25)   # profile unaffected
        self.assertEqual(len(w), 0)        # w dropped whole, ERA5-chunk semantics
        self.assertEqual(len(q), 25)


class MetricsTests(unittest.TestCase):
    def test_paired_block(self) -> None:
        rows = [
            {"observed": 10.0, "era5": 9.0, "hrrr": 8.0},
            {"observed": 20.0, "era5": 22.0, "hrrr": 21.0},
        ]
        block = paired_block(rows)
        self.assertEqual(block["n"], 2)
        self.assertAlmostEqual(block["mae_era5"], 1.5)
        self.assertAlmostEqual(block["mae_hrrr"], 1.5)
        self.assertAlmostEqual(block["penalty"], 0.0)
        self.assertEqual(paired_block([])["penalty"], None)

    def test_decision_branches(self) -> None:
        ok = decision_outcome(0.05, {"rockies": 0.10}, {"1h": 0.05, "1-3h": 0.149})
        self.assertEqual(ok["outcome"], "transfer_accepted")
        big = decision_outcome(0.10, {"rockies": 0.0}, {"1h": 0.0})
        self.assertEqual(big["outcome"], "correction_study_warranted")
        conc = decision_outcome(0.05, {"alaska": 0.15}, {"1h": 0.0, "1-3h": 0.15})
        self.assertEqual(conc["outcome"], "accepted_with_concentration_flag")
        conc_band = decision_outcome(0.05, {"alaska": 0.10}, {"1h": 0.0, "1-3h": 0.20})
        self.assertEqual(conc_band["outcome"], "accepted_with_concentration_flag")


if __name__ == "__main__":
    unittest.main()
