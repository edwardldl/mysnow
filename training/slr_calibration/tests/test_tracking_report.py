from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from slr_calibration.tracking_common import write_csv_rows
from slr_calibration.tracking_events import EVENT_COLUMNS
from slr_calibration.tracking_report import build_status
from slr_calibration.snotel_track import SNOTEL_CSV_COLUMNS

TODAY = date(2026, 10, 17)

COEFFICIENTS = """
export const DEFAULT_SLR_CALIBRATION = {
  modifiersEnabled: false,
  regionalBiasEnabled: false,
  allowUnvalidatedRegionalArtifact: false,
};
"""


class TrackingReportTests(unittest.TestCase):
    def test_status_report_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tracking = root / "data" / "tracking"
            (tracking / "observations").mkdir(parents=True)
            (tracking / "events").mkdir(parents=True)
            (tracking / "snapshots").mkdir(parents=True)
            (tracking / "artifacts").mkdir(parents=True)
            (tracking / "alignment").mkdir(parents=True)

            registry = {
                "resorts": [{
                    "pilot_name": "Alta", "resort_slug": "alta",
                    "forecast_points": [{"point_id": "alta-base"}, {"point_id": "alta-mid_mountain"}, {"point_id": "alta-summit"}],
                    "snotel_station": {"station_triplet": "555:UT:SNTL"},
                    "ghcn_stations": [{"station_id": "US1UTSL0002"}],
                }],
            }
            (tracking / "registry.json").write_text(json.dumps(registry), encoding="utf-8")

            write_csv_rows(tracking / "observations" / "snotel_daily.csv", [{
                "station_triplet": "555:UT:SNTL", "station_name": "Brighton", "state": "UT",
                "latitude": "40.59", "longitude": "-111.61", "elevation_m": "2682.3",
                "date": TODAY.isoformat(), "swe_inches": "1.0", "snow_depth_inches": "2.0",
                "precip_accum_inches": "1.0", "tavg_f": "30.0", "tmax_f": "32.0", "tmin_f": "28.0",
            }], SNOTEL_CSV_COLUMNS)
            (tracking / "observations" / "ghcn_daily.csv").write_text(
                "station_id,date\nUS1UTSL0002,2026-09-01\n", encoding="utf-8")

            event = {column: "" for column in EVENT_COLUMNS}
            event.update({
                "event_id": "ghcn:US1UTSL0002:2026-10-16",
                "station_id": "US1UTSL0002",
                "observation_date": "2026-10-16",
                "climate_regime": "continental_mountain",
                "source": "NOAA GHCN-Daily direct SNOW+WESF",
                "label_quality": "direct",
            })
            write_csv_rows(tracking / "events" / "events.csv", [event], EVENT_COLUMNS)

            index_row = {
                "issued_at_utc": "2026-10-17T12:30:00Z", "point_id": "alta-base",
                "model": "ecmwf_ifs", "sha256": "x", "bytes": 10, "forecast_days": 7,
                "path": "2026/10/17/alta-base__ecmwf_ifs.json.gz",
            }
            (tracking / "snapshots" / "index.jsonl").write_text(json.dumps(index_row) + "\n", encoding="utf-8")
            (tracking / "alignment" / "trainer_rows.csv").write_text("event_id\n", encoding="utf-8")
            (tracking / "artifacts" / "training_report.json").write_text(json.dumps({
                "status": "unvalidated", "holdoutComputable": False,
                "holdoutNote": "No aligned trainer rows exist yet; nothing was trained.",
                "baseline": None, "calibrated": None, "productionEnablement": False,
            }), encoding="utf-8")

            coefficients = root / "coefficients.ts"
            coefficients.write_text(COEFFICIENTS, encoding="utf-8")

            status = build_status(
                tracking, coefficients,
                tracking / "reports" / "status.json",
                tracking / "reports" / "status.md",
                today=TODAY,
            )

            self.assertEqual(status["registry"]["resortCount"], 1)
            self.assertEqual(status["registry"]["forecastPointCount"], 3)
            self.assertEqual(status["events"]["eventCount"], 1)
            self.assertEqual(status["events"]["byLabelQuality"], {"direct": 1})
            self.assertEqual(status["snapshots"]["dayCount"], 1)
            recent = status["observations"]["recentStations"]
            self.assertEqual([item["station"] for item in recent["snotel"]], ["555:UT:SNTL"])
            self.assertEqual(recent["ghcn"], [])
            gates = status["productionGates"]
            self.assertEqual(gates["modifiersEnabled"], False)
            self.assertEqual(gates["regionalBiasEnabled"], False)
            self.assertEqual(gates["productionCalibrationEnabled"], False)

            markdown = (tracking / "reports" / "status.md").read_text(encoding="utf-8")
            self.assertIn("Production calibration remains DISABLED", markdown)
            self.assertIn("label_quality=direct: 1", markdown)


if __name__ == "__main__":
    unittest.main()
