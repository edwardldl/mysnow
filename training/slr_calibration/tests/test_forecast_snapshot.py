from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from slr_calibration.forecast_snapshot import read_index, run_snapshots
from slr_calibration.profiles import HOURLY_FIELDS

NOW = datetime(2026, 10, 17, 12, 30, tzinfo=timezone.utc)


def make_payload() -> bytes:
    hours = 24
    hourly = {"time": [f"2026-10-17T{hour:02d}:00" for hour in range(hours)]}
    for field in HOURLY_FIELDS:
        if field == "time":
            continue
        hourly[field] = [1.0] * hours
    payload = {"latitude": 37.64, "longitude": -119.02, "elevation": 2913.0, "timezone": "UTC", "hourly": hourly}
    return json.dumps(payload).encode("utf-8")


class ForecastSnapshotTests(unittest.TestCase):
    def _registry(self, root: Path) -> Path:
        registry = {
            "resorts": [{
                "resort_slug": "mammoth-mountain",
                "forecast_points": [{
                    "point_id": "mammoth-mountain-mid_mountain",
                    "role": "mid_mountain",
                    "latitude": 37.64013,
                    "longitude": -119.019104,
                    "elevation_m": 2913.0,
                }],
            }],
        }
        path = root / "registry.json"
        path.write_text(json.dumps(registry), encoding="utf-8")
        return path

    def test_snapshot_idempotent_and_index_integrity(self) -> None:
        payload = make_payload()
        calls: list[str] = []

        def fetcher(url: str) -> bytes:
            calls.append(url)
            return payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_path = self._registry(root)
            snapshots_dir = root / "snapshots"
            first = run_snapshots(registry_path, snapshots_dir, fetcher=fetcher, now=NOW)
            self.assertEqual(first["storedCount"], 2)
            self.assertEqual(first["failedCount"], 0)
            second = run_snapshots(registry_path, snapshots_dir, fetcher=fetcher, now=NOW)
            self.assertEqual(second["storedCount"], 0)
            self.assertEqual(second["skippedCount"], 2)
            self.assertEqual(len(calls), 2, "second run must not refetch")

            index = read_index(snapshots_dir / "index.jsonl")
            self.assertEqual(len(index), 2)
            self.assertEqual({row["model"] for row in index}, {"ecmwf_ifs", "gfs_seamless"})
            for row in index:
                stored = (snapshots_dir / row["path"]).read_bytes()
                self.assertEqual(row["sha256"], hashlib.sha256(stored).hexdigest())
                self.assertEqual(row["bytes"], len(stored))
                self.assertEqual(gzip.decompress(stored), payload)
                self.assertEqual(row["forecast_days"], 7)
                self.assertTrue(row["issued_at_utc"].startswith("2026-10-17"))

            forced = run_snapshots(registry_path, snapshots_dir, fetcher=fetcher, now=NOW, force=True)
            self.assertEqual(forced["storedCount"], 2)
            self.assertEqual(len(read_index(snapshots_dir / "index.jsonl")), 2)

    def test_invalid_payload_is_reported_not_stored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_path = self._registry(root)
            report = run_snapshots(
                registry_path, root / "snapshots",
                fetcher=lambda url: b"{}",
                now=NOW,
            )
            self.assertEqual(report["storedCount"], 0)
            self.assertEqual(report["failedCount"], 2)


if __name__ == "__main__":
    unittest.main()
