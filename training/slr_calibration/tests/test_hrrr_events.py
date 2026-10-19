from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from slr_calibration.hrrr_events import EVENT_FIELDS, adapt_hrrr_events

STATION = "1100:CO"


def _row(date_text: str, water_year: int, **overrides) -> dict:
    row = {
        "station_triplet": STATION,
        "station_name": "Test SNOTEL",
        "state": "CO",
        "latitude": 39.5,
        "longitude": -106.0,
        "elevation_m": 3200.0,
        "data_tz_offset_hours": -7.0,
        "date": date_text,
        "water_year": water_year,
        "weak_qc_passed": True,
        "weak_qc_weight": 0.25,
        "observed_slr_diff": 12.0,
        "fcst_kuchera_slr": 10.0,
        "fcst_precip_mm": 8.0,
        "fcst_snowfall_cm": 9.0,
        "fcst_t2m_mean_c": -5.0,
        "fcst_t2m_min_c": -8.0,
        "fcst_t2m_max_c": -2.0,
        "delta_swe_mm": 20.0,
        "delta_depth_mm": 240.0,
        "hrrr_lead_min_h": 1,
        "hrrr_lead_max_h": 24,
        "hrrr_source": "noaa-hrrr-00z",
    }
    row.update(overrides)
    return row


def _write_dataset(root: Path, partitions: dict[int, list[dict]]) -> None:
    for water_year, rows in partitions.items():
        target = root / "daily" / f"water_year={water_year}"
        target.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows), target / "data.parquet")


def _write_regimes(root: Path, triplets: list[str]) -> Path:
    path = root / "station_regimes.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["station_triplet", "station_name", "state", "latitude", "longitude", "range_name", "climate_regime", "region_id", "assignment_basis"])
        for triplet in triplets:
            writer.writerow([triplet, "Test SNOTEL", "CO", 39.5, -106.0, "Colorado Rockies", "continental_mountain", "colorado-ranges", "unit test fixture"])
    return path


class AdapterTests(unittest.TestCase):
    def _run(self, partitions: dict[int, list[dict]], triplets: list[str] | None = None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        dataset = root / "dataset"
        _write_dataset(dataset, partitions)
        regimes = _write_regimes(root, [STATION] if triplets is None else triplets)
        output = root / "events.parquet"
        report = adapt_hrrr_events(dataset, regimes, output, root / "events.csv", root / "report.json")
        events = pq.read_table(output).to_pylist()
        return report, events

    def test_schema_and_provenance(self) -> None:
        report, events = self._run({2024: [_row("2023-11-02", 2024)], 2025: [], 2026: []})
        self.assertEqual(report["trainerEligibleEvents"], 1)
        event = events[0]
        for field in EVENT_FIELDS:
            self.assertIn(field, event)
        self.assertEqual(event["station_id"], STATION)
        self.assertEqual(event["site_id"], STATION)
        self.assertEqual(event["baseline_method"], "kuchera")
        self.assertEqual(event["source"], "snotel-differenced-noaa-hrrr")
        self.assertEqual(event["label_tier"], "weak")
        self.assertEqual(event["qc_weight"], 0.25)
        self.assertEqual(event["climate_regime"], "continental_mountain")
        self.assertEqual(event["region_id"], "colorado-ranges")
        self.assertTrue(event["trainer_eligible"])

    def test_eligibility_bounds(self) -> None:
        rows = [
            _row("2023-11-02", 2024),
            _row("2023-11-03", 2024, observed_slr_diff=60.0),       # observed out of (2, 50)
            _row("2023-11-04", 2024, fcst_kuchera_slr=None),        # missing baseline
            _row("2023-11-05", 2024, fcst_kuchera_slr=2.0),         # baseline below [3, 30]
            _row("2023-11-06", 2024, weak_qc_passed=False),         # not QC-passed: excluded entirely
        ]
        report, events = self._run({2024: rows, 2025: [], 2026: []})
        self.assertEqual(report["qcPassedRows"], 4)
        self.assertEqual(report["emittedEvents"], 4)
        self.assertEqual(report["trainerEligibleEvents"], 1)
        self.assertEqual(report["eligibleByWaterYear"], {"2024": 1})
        self.assertTrue(report["trainerRejectionCounts"])

    def test_storm_grouping_contiguous_per_station(self) -> None:
        rows = [
            _row("2024-01-30", 2024), _row("2024-01-31", 2024),
            _row("2024-02-01", 2024),  # contiguous across the month boundary
            _row("2024-02-03", 2024),  # gap -> new group
        ]
        _, events = self._run({2024: rows, 2025: [], 2026: []})
        groups = [event["storm_group_id"] for event in sorted(events, key=lambda e: e["observation_date"])]
        self.assertEqual(groups[0], groups[1])
        self.assertEqual(groups[1], groups[2])
        self.assertNotEqual(groups[2], groups[3])
        self.assertIn("2024-01-30", groups[0])

    def test_storm_groups_never_cross_water_years(self) -> None:
        rows = [_row("2024-09-30", 2024), _row("2024-10-01", 2025)]
        _, events = self._run({2024: rows[:1], 2025: rows[1:], 2026: []})
        self.assertEqual(len({event["storm_group_id"] for event in events}), 2)

    def test_wy2027_partition_is_not_read(self) -> None:
        report, events = self._run({2024: [_row("2023-11-02", 2024)], 2025: [], 2026: [], 2027: [_row("2026-10-02", 2027)]})
        self.assertEqual(report["emittedEvents"], 1)
        self.assertEqual({event["water_year"] for event in events}, {2024})

    def test_missing_regime_assignment_fails_loudly(self) -> None:
        with self.assertRaises(ValueError):
            self._run({2024: [_row("2023-11-02", 2024)], 2025: [], 2026: []}, triplets=[])


if __name__ == "__main__":
    unittest.main()
