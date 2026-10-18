"""Integration tests for the NOAA monthly store feeding build_dataset.

All offline: the AWDB side is served from pre-seeded chunk cache files,
the forecast side from a synthetic noaa-monthly store, and the byte
fetcher is a tripwire that fails the test on any network access.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from slr_calibration.hrrr_dataset import STATION_COLUMNS, build_dataset
from slr_calibration.noaa_hrrr import (
    load_noaa_monthly_daily,
    load_or_build_grid_indices,
    month_cycle_days,
    monthly_parquet_path,
    write_noaa_parquet,
)
from slr_calibration.tracking_common import write_csv_rows

UT = {
    "station_triplet": "999:UT:SNTL",
    "station_name": "Test Station",
    "state": "UT",
    "latitude": 40.0,
    "longitude": -111.0,
    "elevation_m": 2500.0,
    "data_tz_offset_hours": -7.0,
    "nearest_resort": "Test Resort",
    "nearest_resort_region": "Utah",
    "nearest_resort_country": "United States",
    "distance_km": 1.0,
}
AK = dict(
    UT,
    station_triplet="998:AK:SNTL",
    station_name="Alaska Station",
    state="AK",
    latitude=61.0,
    longitude=-150.0,
    data_tz_offset_hours=-9.0,
)


def legacy_row(triplet: str, day: str, swe: float = 10.0, depth: float = 40.0, precip: float = 20.0) -> dict[str, str]:
    return {
        "station_triplet": triplet,
        "station_name": "Test Station",
        "state": "UT",
        "latitude": "40.0",
        "longitude": "-111.0",
        "elevation_m": "2500.0",
        "date": day,
        "swe_inches": str(swe),
        "snow_depth_inches": str(depth),
        "precip_accum_inches": str(precip),
        "tavg_f": "25.0",
        "tmax_f": "30.0",
        "tmin_f": "20.0",
    }


def noaa_day(triplet: str, day: str, snowfall: float = 2.4) -> dict:
    station = UT if triplet == UT["station_triplet"] else AK
    parsed = date.fromisoformat(day)
    return {
        "station_triplet": triplet,
        "station_name": station["station_name"],
        "state": station["state"],
        "latitude": station["latitude"],
        "longitude": station["longitude"],
        "elevation_m": station["elevation_m"],
        "data_tz_offset_hours": station["data_tz_offset_hours"],
        "nearest_resort": station["nearest_resort"],
        "distance_km": station["distance_km"],
        "date": day,
        "water_year": parsed.year + 1 if parsed.month >= 10 else parsed.year,
        "hrrr_hours": 24,
        "fcst_snowfall_cm": snowfall,
        "fcst_precip_mm": 24.0,
        "fcst_kuchera_slr": 12.0,
        "fcst_kuchera_hours": 24,
        "fcst_kuchera_weight_mm": 24.0,
        "fcst_t2m_mean_c": -5.0,
        "fcst_t2m_min_c": -8.0,
        "fcst_t2m_max_c": -2.0,
        "fcst_freezing_level_m": None,
        "noaa_lead_min_h": 1,
        "noaa_lead_max_h": 24,
    }


def write_manifest(dataset_dir: Path, months: dict) -> None:
    monthly = dataset_dir / "noaa-monthly"
    monthly.mkdir(parents=True, exist_ok=True)
    (monthly / "manifest.json").write_text(
        json.dumps({"kind": "noaa_monthly_manifest", "source": "noaa_grib_00z", "months": months}),
        encoding="utf-8",
    )


class MonthCycleDaysTests(unittest.TestCase):
    def test_december_2024_matches_prototype_cycles(self) -> None:
        days = month_cycle_days(2024, 12)
        self.assertEqual(days[0], date(2024, 11, 30))
        self.assertEqual(days[-1], date(2025, 1, 1))
        self.assertEqual(len(days), 33)

    def test_february_and_october_bounds(self) -> None:
        feb = month_cycle_days(2025, 2)
        self.assertEqual((feb[0], feb[-1], len(feb)), (date(2025, 1, 31), date(2025, 3, 1), 30))
        october = month_cycle_days(2026, 10)
        self.assertEqual((october[0], october[-1], len(october)), (date(2026, 9, 30), date(2026, 11, 1), 33))


class GridIndexCacheTests(unittest.TestCase):
    def test_second_call_uses_cache_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "noaa-cache"

            class ExplodingFetcher:
                def get(self, url):  # noqa: ANN001, ANN202
                    raise AssertionError("network access on grid cache hit")

                def head_size(self, url):  # noqa: ANN001, ANN202
                    raise AssertionError("network access on grid cache hit")

                def get_range(self, url, start, end):  # noqa: ANN001, ANN202
                    raise AssertionError("network access on grid cache hit")

            with mock.patch(
                "slr_calibration.noaa_hrrr.build_grid_indices",
                return_value={UT["station_triplet"]: 7},
            ) as builder:
                first = load_or_build_grid_indices(
                    ExplodingFetcher(), cache_dir, date(2024, 12, 1), 1, [UT],
                )
            self.assertEqual(first, {UT["station_triplet"]: 7})
            self.assertEqual(builder.call_count, 1)
            self.assertTrue((cache_dir / "grid_indices_v1.json").exists())
            with mock.patch(
                "slr_calibration.noaa_hrrr.build_grid_indices",
                side_effect=AssertionError("grid rebuilt despite cache"),
            ):
                second = load_or_build_grid_indices(
                    ExplodingFetcher(), cache_dir, date(2025, 1, 1), 1, [UT],
                )
            self.assertEqual(second, first)


class LoadMonthlyDailyTests(unittest.TestCase):
    def test_only_complete_months_are_fed_and_leads_renamed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dataset_dir = Path(tmp)
            rows = [noaa_day(UT["station_triplet"], "2025-01-01"), noaa_day(UT["station_triplet"], "2025-01-02")]
            write_noaa_parquet(monthly_parquet_path(dataset_dir, 2025, 1), rows)
            write_noaa_parquet(
                monthly_parquet_path(dataset_dir, 2025, 2),
                [noaa_day(UT["station_triplet"], "2025-02-01", snowfall=99.0)],
            )
            write_manifest(dataset_dir, {
                "2025-01": {"complete": True},
                "2025-02": {"complete": False},
            })
            daily, report = load_noaa_monthly_daily(dataset_dir, [UT, AK], [(2025, 1), (2025, 2), (2025, 3)])
            self.assertEqual(report["months_fed"], ["2025-01"])
            self.assertEqual(report["months_missing"], ["2025-02", "2025-03"])
            self.assertEqual(report["stations_with_data"], 1)
            agg = daily[UT["station_triplet"]]["2025-01-01"]
            self.assertEqual(agg["hrrr_hours"], 24)
            self.assertEqual(agg["fcst_snowfall_cm"], 2.4)
            self.assertEqual(agg["hrrr_lead_min_h"], 1)
            self.assertEqual(agg["hrrr_lead_max_h"], 24)
            self.assertEqual(daily[UT["station_triplet"]].get("2025-02-01"), None)
            self.assertEqual(daily[AK["station_triplet"]], {})


class BuildDatasetNoaaTests(unittest.TestCase):
    def _seed(self, root: Path) -> Path:
        dataset_dir = root / "dataset"
        (dataset_dir / "cache").mkdir(parents=True)
        write_csv_rows(dataset_dir / "stations.csv", [UT, AK], STATION_COLUMNS)
        days = [f"2025-01-{d:02d}" for d in range(1, 32)] + [f"2025-02-{d:02d}" for d in range(1, 6)]
        for month, chunk_days in (("202501", days[:31]), ("202502", days[31:])):
            rows = [
                legacy_row(triplet, day)
                for triplet in (AK["station_triplet"], UT["station_triplet"])
                for day in chunk_days
            ]
            (dataset_dir / "cache" / f"awdb_rows_b00_{month}.json").write_text(
                json.dumps({"kind": "awdb_rows", "batch_index": 0, "month": month, "rows": rows}),
                encoding="utf-8",
            )
        write_noaa_parquet(
            monthly_parquet_path(dataset_dir, 2025, 1),
            [noaa_day(UT["station_triplet"], f"2025-01-{d:02d}") for d in range(1, 6)],
        )
        write_manifest(dataset_dir, {"2025-01": {"complete": True}, "2025-02": {"complete": False}})
        return dataset_dir

    def test_noaa_is_default_source_with_provenance(self) -> None:
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset_dir = self._seed(root)
            catalog = root / "ski-areas.json"
            catalog.write_text("[]", encoding="utf-8")

            def tripwire(url: str) -> bytes:
                raise AssertionError(f"unexpected network access: {url}")

            report = build_dataset(
                ski_areas_path=catalog,
                dataset_dir=dataset_dir,
                obs_start=date(2025, 1, 1),
                obs_end=date(2025, 2, 5),
                bytes_fetcher=tripwire,
                awdb_interval_s=0.0,
                open_meteo_interval_s=0.0,
                hrrr_months=[(2025, 1), (2025, 2)],
                today=date(2026, 10, 17),
                progress=lambda _msg: None,
            )
            self.assertEqual(report["forecast_source"], "noaa")
            self.assertEqual(report["hrrr"]["months_fed"], ["2025-01"])
            self.assertEqual(report["hrrr"]["months_missing"], ["2025-02"])
            self.assertEqual(report["requests"]["open_meteo"]["logical_requests"], 0)

            table = pq.read_table(dataset_dir / "daily" / "water_year=2025" / "data.parquet")
            self.assertIn("hrrr_source", table.column_names)
            self.assertIn("hrrr_lead_min_h", table.column_names)
            rows = table.to_pylist()
            by_key = {(row["station_triplet"], row["date"]): row for row in rows}
            jan = by_key[(UT["station_triplet"], "2025-01-03")]
            self.assertEqual(jan["hrrr_status"], "ok")
            self.assertEqual(jan["hrrr_source"], "noaa_grib_00z")
            self.assertEqual(jan["hrrr_lead_min_h"], 1)
            self.assertEqual(jan["hrrr_lead_max_h"], 24)
            self.assertAlmostEqual(jan["fcst_snowfall_cm"], 2.4)
            self.assertIsNotNone(jan["swe_mm"])
            feb = by_key[(UT["station_triplet"], "2025-02-03")]
            self.assertEqual(feb["hrrr_status"], "not_requested")
            self.assertIsNone(feb["hrrr_source"])
            self.assertIsNone(feb["fcst_snowfall_cm"])
            ak = by_key[(AK["station_triplet"], "2025-01-03")]
            self.assertEqual(ak["hrrr_status"], "not_requested")
            self.assertFalse(ak["hrrr_available"])


if __name__ == "__main__":
    unittest.main()
