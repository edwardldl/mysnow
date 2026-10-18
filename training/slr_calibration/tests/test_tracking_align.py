from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from slr_calibration.profiles import HOURLY_FIELDS, PRESSURE_LEVELS_HPA
from slr_calibration.tracking_align import align_events, compute_baseline_slr, kuchera_slr
from slr_calibration.tracking_common import write_csv_rows
from slr_calibration.tracking_events import EVENT_COLUMNS

UTC = timezone.utc


def make_payload(start: datetime, hours: int, temperature_c: float = -2.0) -> dict:
    hourly: dict[str, list] = {"time": []}
    for field in HOURLY_FIELDS:
        if field != "time":
            hourly[field] = []
    for offset in range(hours):
        moment = start + timedelta(hours=offset)
        hourly["time"].append(moment.strftime("%Y-%m-%dT%H:%M"))
        hourly["temperature_2m"].append(temperature_c)
        hourly["precipitation"].append(1.0)
        hourly["snowfall"].append(1.0)
        hourly["freezing_level_height"].append(3000.0)
        for level in PRESSURE_LEVELS_HPA:
            hourly[f"temperature_{level}hPa"].append(temperature_c)
            hourly[f"geopotential_height_{level}hPa"].append(3000.0 + level)
    return {"latitude": 44.54, "longitude": -72.79, "elevation": 450.0, "timezone": "UTC", "hourly": hourly}


def hour_dicts(start: datetime, hours: int, temperature_c: float = -2.0) -> list[dict]:
    return [
        {
            "time": (start + timedelta(hours=offset)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "temperature2mC": temperature_c,
            "precipitationMm": 1.0,
            "snowfallCm": 1.0,
            "freezingLevelHeightM": 3000.0,
            "levels": [
                {"pressureHpa": level, "temperatureC": temperature_c, "geopotentialHeightM": 3000.0 + level}
                for level in PRESSURE_LEVELS_HPA
            ],
        }
        for offset in range(hours)
    ]


class KucheraReplicationTests(unittest.TestCase):
    def test_known_values(self) -> None:
        # Single above-ground level; station elevation 2500 m, level at 3000 m.
        self.assertEqual(kuchera_slr([(3000.0, -2.0)], 2500.0, None), 12.0)
        self.assertEqual(kuchera_slr([(3000.0, 0.0)], 2500.0, None), 8.0)
        self.assertEqual(kuchera_slr([(3000.0, -10.0)], 2500.0, None), 20.0)

    def test_below_ground_levels_fall_back_to_surface(self) -> None:
        # Only level is below ground (2000 m < 2500-100): surface temp is used.
        self.assertEqual(kuchera_slr([(2000.0, 5.0)], 2500.0, -5.0), 15.0)
        # No information at all: Kuchera default 10.
        self.assertEqual(kuchera_slr([(2000.0, 5.0)], 2500.0, None), 10.0)

    def test_compute_baseline_weighted_mean(self) -> None:
        start = datetime(2026, 1, 14, 12, 0, tzinfo=UTC)
        hours = hour_dicts(start, 26)
        result = compute_baseline_slr(hours, start, start + timedelta(hours=24), 450.0)
        self.assertIsNotNone(result)
        assert result is not None
        baseline, evaluated, weight = result
        self.assertEqual(baseline, 12.0)
        self.assertEqual(evaluated, 24)
        self.assertEqual(weight, 24.0)


EVENT = {
    "event_id": "ghcn:US1VTC0001:2026-01-15",
    "storm_group_id": "tracking-stowe-2026-01-15",
    "station_id": "US1VTC0001",
    "site_id": "US1VTC0001",
    "station_name": "STOWE 1.0 N",
    "state": "VT",
    "observation_date": "2026-01-15",
    "valid_time": "2026-01-15T07:00:00-05:00",
    "observation_time_local_standard": "0700",
    "observation_window_hours": "24",
    "latitude": "44.54",
    "longitude": "-72.79",
    "elevation_m": "450",
    "fresh_snow_mm": "100",
    "fresh_swe_mm": "10",
    "observed_slr": "10",
    "baseline_method": "kuchera",
    "climate_regime": "northeast_cold_orographic",
    "qc_passed": "true",
    "qc_weight": "1.0",
    "source": "NOAA GHCN-Daily direct SNOW+WESF",
    "source_license": "NOAA/NCEI public data",
    "source_flag": "N",
    "label_quality": "direct",
}

REGISTRY = {
    "resorts": [{
        "resort_slug": "stowe",
        "timezone": "America/New_York",
        "climate_regime": "northeast_cold_orographic",
        "snotel_station": None,
        "ghcn_stations": [{"station_id": "US1VTC0001"}],
        "forecast_points": [{
            "point_id": "stowe-mid_mountain", "role": "mid_mountain",
            "latitude": 44.54, "longitude": -72.79, "elevation_m": 450.0,
        }],
    }],
}


class SnapshotAlignmentTests(unittest.TestCase):
    def _setup(self, root: Path, issued_at: str) -> tuple[Path, Path, Path]:
        registry_path = root / "registry.json"
        registry_path.write_text(json.dumps(REGISTRY), encoding="utf-8")
        events_path = root / "events.csv"
        write_csv_rows(events_path, [EVENT], EVENT_COLUMNS)
        snapshots_dir = root / "snapshots"
        day_dir = snapshots_dir / "2026" / "01" / "14"
        day_dir.mkdir(parents=True)
        payload = make_payload(datetime(2026, 1, 14, 0, 0), 62)
        raw = json.dumps(payload).encode("utf-8")
        compressed = gzip.compress(raw, mtime=0)
        payload_path = day_dir / "stowe-mid_mountain__ecmwf_ifs.json.gz"
        payload_path.write_bytes(compressed)
        import hashlib

        index_row = {
            "issued_at_utc": issued_at,
            "point_id": "stowe-mid_mountain",
            "model": "ecmwf_ifs",
            "sha256": hashlib.sha256(compressed).hexdigest(),
            "bytes": len(compressed),
            "forecast_days": 7,
            "path": "2026/01/14/stowe-mid_mountain__ecmwf_ifs.json.gz",
        }
        (snapshots_dir / "index.jsonl").write_text(json.dumps(index_row) + "\n", encoding="utf-8")
        return events_path, registry_path, snapshots_dir

    def test_snapshot_preferred_when_issued_before_event_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events_path, registry_path, snapshots_dir = self._setup(root, "2026-01-14T12:00:00Z")
            output = root / "trainer_rows.csv"
            report = align_events(
                events_path, registry_path, snapshots_dir,
                output, root / "report.json", root / "cache",
            )
            self.assertEqual(report["alignedRowCount"], 1)
            self.assertEqual(report["sourceCounts"], {"snapshot": 1})
            rows = output.read_text(encoding="utf-8").splitlines()
            self.assertIn("snapshot:stowe-mid_mountain:ecmwf_ifs", rows[1])
            self.assertIn(",12.0,", rows[1])

    def test_snapshot_issued_after_event_end_is_rejected_for_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Issued 2026-01-16, after the event ended 2026-01-15T12:00Z.
            events_path, registry_path, snapshots_dir = self._setup(root, "2026-01-16T12:00:00Z")
            output = root / "trainer_rows.csv"
            with mock.patch(
                "slr_calibration.profiles._download_json",
                side_effect=RuntimeError("offline in tests"),
            ):
                report = align_events(
                    events_path, registry_path, snapshots_dir,
                    output, root / "report.json", root / "cache",
                )
            self.assertEqual(report["alignedRowCount"], 0)
            self.assertEqual(report["sourceCounts"], {})
            self.assertGreaterEqual(report["rejectionCounts"].get("BACKFILL_PROFILE_REJECTED", 0), 1)


if __name__ == "__main__":
    unittest.main()
