from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from slr_calibration.gold_profiles import FEATURE_COLUMNS
from slr_calibration.hrrr_transfer import (
    _points_cache_path,
    champion_verdict,
    feature_parity,
    merge_champion_rows,
    plan_champion_transfer,
    purge_new_station_files,
    seed_champion_cache,
)

UTC = timezone.utc


def _frame(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"event_id": event_id, "valid_time": valid, "station_id": station, "region": "rockies"}
            for event_id, valid, station in rows
        ]
    )


class PlanChampionTransferTests(unittest.TestCase):
    def test_partition(self) -> None:
        step2 = _frame([
            ("s2:a", "2022-01-01T00:00:00+00:00", "S1"),
            ("s2:b", "2023-01-01T00:00:00+00:00", "S2"),
        ])
        v2 = _frame([
            ("e_old", "2021-06-01T00:00:00+00:00", "S1"),
            ("e_ok", "2022-06-01T00:00:00+00:00", "S1"),
            ("e_nb", "2022-07-01T00:00:00+00:00", "S1"),
            ("e_fm", "2022-08-01T00:00:00+00:00", "S1"),
            ("e_ip", "2022-09-01T00:00:00+00:00", "S2"),
            ("e_new", "2023-02-01T00:00:00+00:00", "S9"),
            ("e_new2", "2023-03-01T00:00:00+00:00", "S1"),
        ])
        step5 = pd.DataFrame([
            {"event_id": "e_ok", "profile_status": "ok"},
            {"event_id": "e_nb", "profile_status": "no_baseline"},
            {"event_id": "e_fm", "profile_status": "fetch_missing"},
            {"event_id": "e_ip", "profile_status": "incomplete_profile"},
        ])
        plan = plan_champion_transfer(v2, step2, step5, {"S1", "S2"})
        self.assertTrue(plan["windowFloor"].startswith("2022-01-01"))
        self.assertEqual(plan["v2ModelingRows"], 7)
        self.assertEqual(plan["inWindowRows"], 6)
        self.assertEqual(plan["reusedOk"], ["e_ok"])
        self.assertEqual(plan["carriedNonOk"], ["e_nb"])
        self.assertEqual(plan["retry"], ["e_fm", "e_ip"])
        self.assertEqual(plan["fresh"], ["e_new", "e_new2"])
        self.assertEqual(plan["build"], ["e_fm", "e_ip", "e_new", "e_new2"])
        self.assertEqual(plan["newStationIds"], ["S9"])
        self.assertEqual(plan["newStationEventIds"], ["e_new"])
        self.assertEqual(plan["step5StationCount"], 2)


class SeedAndPurgeTests(unittest.TestCase):
    def test_seed_skips_grid_indices_and_existing(self) -> None:
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            dest = Path(tmp) / "dst"
            (source / "conus" / "20240115").mkdir(parents=True)
            (source / "conus" / "20240115" / "t12z_f01.json.gz").write_bytes(b"x")
            (source / "grid_indices_conus.json").write_text("{}", encoding="utf-8")
            (dest / "conus" / "20240115").mkdir(parents=True)
            (dest / "conus" / "20240115" / "t12z_f01.json.gz").write_bytes(b"y")
            (source / "conus" / "20240115" / "t13z_f01.json.gz").write_bytes(b"z")
            stats = seed_champion_cache(source, dest)
            self.assertEqual(stats["skippedExisting"], 1)
            self.assertEqual(stats["linked"] + stats["copied"], 1)
            self.assertFalse((dest / "grid_indices_conus.json").exists())
            self.assertEqual(
                (dest / "conus" / "20240115" / "t12z_f01.json.gz").read_bytes(), b"y"
            )

    def test_purge_removes_only_planned_files(self) -> None:
        event = {
            "event_id": "test:1",
            "region": "rockies",
            "station_id": "NEW0001",
            "valid_time": datetime(2024, 1, 15, 13, 0, tzinfo=UTC),
        }
        with TemporaryDirectory() as tmp:
            cache = Path(tmp)
            planned = _points_cache_path(
                cache, "conus", datetime(2024, 1, 15, 12, 0, tzinfo=UTC), 1, False
            )
            planned.parent.mkdir(parents=True)
            planned.write_bytes(b"x")
            other = cache / "conus" / "19990101" / "t00z_f01.json.gz"
            other.parent.mkdir(parents=True)
            other.write_bytes(b"x")
            removed = purge_new_station_files(cache, [event])
            self.assertGreaterEqual(removed, 1)
            self.assertFalse(planned.exists())
            self.assertTrue(other.exists())


class MergeTests(unittest.TestCase):
    def test_merge_orders_and_rejects_gaps(self) -> None:
        rows = merge_champion_rows(
            ["a", "b", "c"],
            [{"event_id": "b", "profile_status": "no_baseline"}],
            [{"event_id": "a", "profile_status": "ok"}, {"event_id": "c", "profile_status": "ok"}],
        )
        self.assertEqual([row["event_id"] for row in rows], ["a", "b", "c"])
        with self.assertRaises(ValueError):
            merge_champion_rows(["a", "b"], [{"event_id": "a", "profile_status": "ok"}], [])
        with self.assertRaises(ValueError):
            merge_champion_rows(
                ["a"],
                [{"event_id": "a", "profile_status": "ok"}],
                [{"event_id": "a", "profile_status": "ok"}],
            )


def _metrics(overall: float, regions: dict[str, float], tail_low: float) -> dict:
    return {
        "overall": {"n": 100, "mae": overall, "bias": 0.1, "rmse": overall + 1.0},
        "perRegion": {
            region: {"n": 10, "mae": mae, "bias": -0.2, "rmse": mae + 1.0}
            for region, mae in regions.items()
        },
        "tailHigh_slr_gt_20": {"n": 5, "mae": 9.0, "bias": 0.0, "rmse": 10.0},
        "tailLow_slr_lt_5": {"n": 5, "mae": tail_low, "bias": 0.0, "rmse": tail_low + 1.0},
    }


REGIONS = ["alaska", "great-lakes", "northeast", "pacific-northwest", "rockies", "sierra-nevada"]


class ChampionVerdictTests(unittest.TestCase):
    def _all(self, ens_h: float, kuch_h: float, region_deltas: dict[str, float]) -> dict:
        ens_regions = {region: 3.0 + region_deltas.get(region, -0.5) for region in REGIONS}
        kuch_regions = {region: 3.0 for region in REGIONS}
        return {
            "era5": {
                "ens_eb": _metrics(2.5, {region: 2.5 for region in REGIONS}, 3.0),
                "rf_eb": _metrics(2.6, {region: 2.6 for region in REGIONS}, 3.1),
                "kuchera": _metrics(4.0, kuch_regions, 3.4),
            },
            "hrrr": {
                "ens_eb": _metrics(ens_h, ens_regions, 3.2),
                "rf_eb": _metrics(ens_h + 0.1, {region: 3.2 for region in REGIONS}, 3.3),
                "kuchera": _metrics(kuch_h, kuch_regions, 3.4),
            },
        }

    def test_transfers_when_overall_and_five_regions_win(self) -> None:
        verdict = champion_verdict(self._all(2.8, 4.0, {"alaska": 0.4}))
        self.assertTrue(verdict["transfers"])
        self.assertEqual(verdict["regionsWonVsKuchera"], 5)
        self.assertAlmostEqual(verdict["transferCostMae"], 0.3)
        self.assertTrue(verdict["ensEbBeatsRfEbOnHrrr"])
        self.assertEqual(verdict["watchItems"]["alaskaBiasEnsEbHrrr"], -0.2)
        self.assertTrue(verdict["watchItems"]["tailLowEnsEbBeatsKuchera"])

    def test_fails_when_only_four_regions_win(self) -> None:
        verdict = champion_verdict(self._all(2.8, 4.0, {"alaska": 0.4, "rockies": 0.2}))
        self.assertFalse(verdict["transfers"])
        self.assertEqual(verdict["regionsWonVsKuchera"], 4)
        self.assertTrue(verdict["ensEbBeatsKucheraOverall"])

    def test_fails_when_overall_loses(self) -> None:
        verdict = champion_verdict(self._all(4.5, 4.0, {}))
        self.assertFalse(verdict["transfers"])
        self.assertFalse(verdict["ensEbBeatsKucheraOverall"])


class FeatureParityTests(unittest.TestCase):
    def test_paired_means(self) -> None:
        era5 = pd.DataFrame({column: [1.0, 3.0] for column in FEATURE_COLUMNS})
        era5["baseline_slr"] = [10.0, 12.0]
        hrrr = pd.DataFrame({column: [2.0, 4.0] for column in FEATURE_COLUMNS})
        hrrr["baseline_slr"] = [9.0, 11.0]
        rows = feature_parity(era5, hrrr)
        self.assertEqual(len(rows), len(FEATURE_COLUMNS) + 1)
        self.assertEqual(rows[0]["era5Mean"], 2.0)
        self.assertEqual(rows[0]["hrrrMean"], 3.0)
        self.assertEqual(rows[0]["meanDiffHrrrMinusEra5"], 1.0)
        self.assertEqual(rows[-1]["feature"], "baseline_slr")
        self.assertEqual(rows[-1]["meanDiffHrrrMinusEra5"], -1.0)


if __name__ == "__main__":
    unittest.main()
