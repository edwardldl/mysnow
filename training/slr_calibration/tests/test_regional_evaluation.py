from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from slr_calibration.regional_evaluation import _gate, direct_label_check, evaluate_region, station_holdout
from slr_calibration.trainer import TrainingConfig, train_artifact


def _row(station: str, water_year: int, index: int, observed: float, baseline: float = 10.0) -> dict:
    month = 1 + (index // 10) % 3
    day = 1 + index % 10
    date_text = f"{water_year - 1 if month >= 10 else water_year}-{month:02d}-{day:02d}"
    # Simple WY-consistent dates: Nov of previous calendar year through Mar.
    if month in (11, 12):
        date_text = f"{water_year - 1}-{month:02d}-{day:02d}"
    else:
        date_text = f"{water_year}-{month:02d}-{day:02d}"
    return {
        "event_id": f"{station}-{date_text}-{index}",
        "storm_group_id": f"sg-{station}-{date_text}",
        "station_id": station,
        "site_id": station,
        "state": "CO",
        "valid_time": f"{date_text}T00:00:00Z",
        "observation_date": date_text,
        "latitude": 39.5,
        "longitude": -106.0,
        "elevation_m": 3100.0,
        "observed_slr": observed,
        "baseline_slr": baseline,
        "baseline_method": "kuchera",
        "climate_regime": "continental_mountain",
        "qc_passed": True,
        "qc_weight": 0.25,
        "water_year": water_year,
        "region_id": "colorado-ranges",
        "fcst_precip_mm": 8.0,
        "fcst_snowfall_cm": 11.0,
        "fcst_t2m_mean_c": -5.0,
        "fcst_t2m_min_c": -8.0,
        "fcst_t2m_max_c": -2.0,
        "delta_swe_mm": 20.0,
        "delta_depth_mm": observed * 20.0,
        "hrrr_lead_min_h": 1,
        "hrrr_lead_max_h": 24,
    }


def _region_rows() -> list[dict]:
    import math

    rows = []
    stations = [f"200{i}:CO" for i in range(6)]
    for station_number, station in enumerate(stations):
        for water_year in (2024, 2025, 2026):
            for index in range(40):
                # Kuchera biased low by ~15%; log-residual noise is an
                # arcsine spread (amplitude 0.17) whose std makes the
                # trainer's P10-P90 interval cover ~72% of holdout events,
                # inside the [0.65, 0.95] gate band.
                noise = 0.17 * math.sin(index * 12.9898 + station_number * 78.233)
                observed = 11.5 * math.exp(noise)
                rows.append(_row(station, water_year, index, observed=observed))
    return rows


def _split(mae_kuchera: float, mae_candidate: float, coverage: float = 0.8, degraded: bool = False) -> dict:
    slices = {"byStation": {}, "byElevationBand": {}, "byTemperatureBand": {}}
    if degraded:
        slices["byStation"]["2000:CO"] = {"events": 25, "kucheraMae": 2.0, "candidateMae": 2.2, "relativeMaeChange": 0.1}
    return {
        "kuchera": {"mae": mae_kuchera},
        "candidateA": {"mae": mae_candidate, "intervalCoverage": coverage, "slices": slices},
    }


class SplitTests(unittest.TestCase):
    def test_station_holdout_is_deterministic(self) -> None:
        rows = _region_rows()
        first = station_holdout(rows)
        second = station_holdout(list(reversed(rows)))
        self.assertEqual(first, second)
        self.assertTrue(1 <= len(first) <= 3)
        self.assertTrue(first.issubset({row["station_id"] for row in rows}))


class GateTests(unittest.TestCase):
    support = {"trainStations": 6, "trainStormGroups": 60, "trainWaterYears": 2}

    def test_gate_passes_when_all_checks_hold(self) -> None:
        gate = _gate("A", _split(4.0, 3.0), _split(4.0, 3.2), self.support)
        self.assertTrue(gate["passed"], gate["checks"])

    def test_gate_fails_on_absolute_improvement(self) -> None:
        gate = _gate("A", _split(4.0, 3.9), _split(4.0, 3.0), self.support)
        self.assertFalse(gate["passed"])

    def test_gate_fails_on_slice_degradation(self) -> None:
        gate = _gate("A", _split(4.0, 3.0, degraded=True), _split(4.0, 3.0), self.support)
        self.assertFalse(gate["passed"])

    def test_gate_fails_on_coverage(self) -> None:
        gate = _gate("A", _split(4.0, 3.0, coverage=0.5), _split(4.0, 3.0), self.support)
        self.assertFalse(gate["passed"])

    def test_gate_fails_on_support(self) -> None:
        gate = _gate("A", _split(4.0, 3.0), _split(4.0, 3.0), {"trainStations": 2, "trainStormGroups": 60, "trainWaterYears": 2})
        self.assertFalse(gate["passed"])


class EvaluateRegionTests(unittest.TestCase):
    def test_biased_region_is_retained_with_numbers(self) -> None:
        result = evaluate_region(_region_rows(), "colorado-ranges", "Colorado Ranges")
        self.assertIn(result["verdict"], ("retained-A", "retained-B"))
        self.assertLess(result["split1"]["candidateA"]["mae"], result["split1"]["kuchera"]["mae"])
        self.assertEqual(result["split1"]["testEvents"], 240)
        self.assertTrue(result["gates"]["A"]["checks"])
        self.assertIn("cvStabilityMae", result)

    def test_thin_region_is_not_evaluated(self) -> None:
        rows = [_row("2000:CO", 2024, i, 11.5) for i in range(10)]
        result = evaluate_region(rows, "colorado-ranges", "Colorado Ranges")
        self.assertEqual(result["verdict"], "not-evaluated-insufficient-support")


class DirectLabelCheckTests(unittest.TestCase):
    def _region_results(self) -> dict:
        train_rows = [_row(f"200{i}:CO", 2024, j, 11.5) for i in range(3) for j in range(30)]
        config = TrainingConfig(artifact_id="t", artifact_version="v", status="unvalidated", source_label="test", leakage_group="storm_group_id")
        artifact = train_artifact(train_rows, config)
        return {"colorado-ranges": {"_artifacts": {"A": artifact}, "selectedCandidate": "A"}}

    def _write_direct(self, path: Path, observed: float, baseline: float = 10.0, n: int = 12) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["event_id", "storm_group_id", "station_id", "site_id", "state", "valid_time", "latitude", "longitude", "observed_slr", "baseline_slr", "baseline_method", "climate_regime", "qc_passed", "qc_weight"])
            writer.writeheader()
            for i in range(n):
                writer.writerow({
                    "event_id": f"d{i}", "storm_group_id": f"g{i}", "station_id": f"USC0005{i:04d}", "site_id": f"USC0005{i:04d}",
                    "state": "CO", "valid_time": "2025-01-10T00:00:00Z", "latitude": 39.5, "longitude": -106.0,
                    "observed_slr": observed, "baseline_slr": baseline, "baseline_method": "kuchera",
                    "climate_regime": "continental_mountain", "qc_passed": "True", "qc_weight": 1.0,
                })

    def test_direct_check_pass_and_demote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.csv"
            self._write_direct(good, observed=11.5)
            report = direct_label_check(self._region_results(), {"rockies": (good, None)})
            self.assertEqual(report["byRegion"]["colorado-ranges"]["verdict"], "pass")
            self.assertEqual(report["demotions"], [])
            self.assertFalse(report["sets"]["rockies"]["candidateBApplicable"])

            bad = Path(tmp) / "bad.csv"
            self._write_direct(bad, observed=10.0)  # Kuchera is exactly right here; the biased artifact is worse.
            report = direct_label_check(self._region_results(), {"rockies": (bad, None)})
            self.assertEqual(report["byRegion"]["colorado-ranges"]["verdict"], "demote")
            self.assertEqual(report["demotions"], ["colorado-ranges"])


if __name__ == "__main__":
    unittest.main()
