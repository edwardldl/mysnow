from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any

from .evaluation import apply_artifact
from .sierra import SIERRA_STATION_REGIONS
from .trainer import TrainingConfig, train_artifact


def _water_year(row: dict[str, Any]) -> int:
    year, month, _ = (int(value) for value in str(row["valid_time"])[:10].split("-"))
    return year + 1 if month >= 10 else year


def _elevation_band(elevation_m: float) -> str:
    if elevation_m < 1500:
        return "below_1500_m"
    if elevation_m < 2000:
        return "1500_to_1999_m"
    return "2000_m_and_above"


def _metrics(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    errors = [float(row[key]) - float(row["observed_slr"]) for row in rows]
    absolute = [abs(value) for value in errors]
    return {
        "bias": mean(errors),
        "mae": mean(absolute),
        "medianAbsoluteError": median(absolute),
        "rmse": math.sqrt(mean(value * value for value in errors)),
    }


def _slice_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row[key]), []).append(row)
    output: dict[str, Any] = {}
    for label, group in sorted(grouped.items()):
        baseline = _metrics(group, "baseline_prediction")
        calibrated = _metrics(group, "calibrated_prediction")
        covered = sum(bool(row["interval_hit"]) for row in group)
        baseline_covered = sum(bool(row["baseline_interval_hit"]) for row in group)
        output[label] = {
            "eventCount": len(group),
            "stationCount": len({str(row["station_id"]) for row in group}),
            "baseline": baseline,
            "calibrated": calibrated,
            "maeImprovement": baseline["mae"] - calibrated["mae"],
            "medianAbsoluteErrorImprovement": baseline["medianAbsoluteError"] - calibrated["medianAbsoluteError"],
            "p10P90Coverage": {"baseline": baseline_covered / len(group), "calibrated": covered / len(group)},
            "coveredEventCount": {"baseline": baseline_covered, "calibrated": covered},
        }
    return output


def evaluate_sierra_station_water_year_holdout(
    input_path: Path,
    artifact_output: Path,
    report_output: Path,
    artifact_id: str,
    artifact_version: str,
    held_out_stations: set[str],
    train_water_year: int = 2023,
    test_water_year: int = 2024,
    prior_effective_sample_size: float = 30.0,
    min_effective_sample_size: float = 5.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        rows: list[dict[str, Any]] = list(csv.DictReader(handle))
    if len(held_out_stations) < 3:
        raise ValueError("Sierra holdout requires at least three entirely unseen stations")
    missing = held_out_stations - {str(row["station_id"]) for row in rows}
    if missing:
        raise ValueError(f"Held-out stations lack aligned baseline events: {sorted(missing)}")
    train_rows = [
        row for row in rows
        if _water_year(row) == train_water_year and str(row["station_id"]) not in held_out_stations
    ]
    test_rows = [
        row for row in rows
        if _water_year(row) == test_water_year and str(row["station_id"]) in held_out_stations
    ]
    if len(train_rows) < 50 or len(test_rows) < 15:
        raise ValueError(f"Insufficient Sierra split: train={len(train_rows)}, test={len(test_rows)}")
    train_stations = {str(row["station_id"]) for row in train_rows}
    test_stations = {str(row["station_id"]) for row in test_rows}
    if train_stations & test_stations:
        raise AssertionError("Stations leaked across Sierra holdout")
    train_groups = {str(row["storm_group_id"]) for row in train_rows}
    test_groups = {str(row["storm_group_id"]) for row in test_rows}
    if train_groups & test_groups:
        raise AssertionError("Regional storm groups leaked across Sierra split")
    if max(str(row["valid_time"]) for row in train_rows) >= min(str(row["valid_time"]) for row in test_rows):
        raise AssertionError("Sierra train and test periods are not chronological")

    config = TrainingConfig(
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        status="unvalidated",
        prior_effective_sample_size=prior_effective_sample_size,
        min_effective_sample_size=min_effective_sample_size,
        source_label="NOAA GHCN-Daily direct same-window SNOW+WESF paired to Open-Meteo Historical Forecast profiles; Sierra research only",
        leakage_group="entirely unseen station + regional contiguous-date storm group + chronological water year",
        geographic_note="Audited Sierra/Tahoe/eastern-Sierra mountain-station whitelist only; excludes adjacent cities and basins. Not statewide, production, or worldwide validation.",
    )
    artifact = train_artifact(train_rows, config)
    evaluated: list[dict[str, Any]] = []
    global_sigma = float(artifact["methods"]["kuchera"]["global"]["residualStdLog"])
    p10_p90_z = 1.2815515655446004
    hierarchy_counts: dict[str, int] = {}
    for row in test_rows:
        prediction = apply_artifact(artifact, row)
        hierarchy = ">".join(prediction.hierarchy) or "identity"
        hierarchy_counts[hierarchy] = hierarchy_counts.get(hierarchy, 0) + 1
        observed = float(row["observed_slr"])
        baseline_prediction = float(row["baseline_slr"])
        baseline_lower = max(3.0, baseline_prediction * math.exp(-p10_p90_z * global_sigma))
        baseline_upper = min(30.0, baseline_prediction * math.exp(p10_p90_z * global_sigma))
        station_id = str(row["station_id"])
        evaluated.append({
            **row,
            "geographic_slice": SIERRA_STATION_REGIONS.get(station_id, "unknown"),
            "elevation_band": _elevation_band(float(row["elevation_m"])),
            "baseline_prediction": baseline_prediction,
            "baseline_lower_prediction": baseline_lower,
            "baseline_upper_prediction": baseline_upper,
            "baseline_interval_hit": baseline_lower <= observed <= baseline_upper,
            "calibrated_prediction": prediction.calibrated_slr,
            "lower_prediction": prediction.lower_slr,
            "upper_prediction": prediction.upper_slr,
            "interval_hit": prediction.lower_slr <= observed <= prediction.upper_slr,
            "correction_factor": prediction.correction_factor,
            "hierarchy": hierarchy,
        })

    baseline = _metrics(evaluated, "baseline_prediction")
    calibrated = _metrics(evaluated, "calibrated_prediction")
    by_station = _slice_metrics(evaluated, "station_id")
    by_geography = _slice_metrics(evaluated, "geographic_slice")
    by_elevation = _slice_metrics(evaluated, "elevation_band")
    covered = sum(bool(row["interval_hit"]) for row in evaluated)
    baseline_covered = sum(bool(row["baseline_interval_hit"]) for row in evaluated)

    eligible_station = [value for value in by_station.values() if value["eventCount"] >= 3]
    eligible_geography = [value for value in by_geography.values() if value["eventCount"] >= 3]
    eligible_elevation = [value for value in by_elevation.values() if value["eventCount"] >= 3]
    improved_station = sum(value["maeImprovement"] > 0 for value in eligible_station)
    improved_geography = sum(value["maeImprovement"] > 0 for value in eligible_geography)
    improved_elevation = sum(value["maeImprovement"] > 0 for value in eligible_elevation)
    mae_improvement = baseline["mae"] - calibrated["mae"]
    relative_mae_improvement = mae_improvement / baseline["mae"] if baseline["mae"] else 0.0
    robust_improvement = (
        mae_improvement >= 0.25
        and relative_mae_improvement >= 0.05
        and calibrated["medianAbsoluteError"] <= baseline["medianAbsoluteError"]
        and improved_station >= math.ceil(len(eligible_station) / 2)
        and improved_geography >= math.ceil(len(eligible_geography) / 2)
        and improved_elevation >= math.ceil(len(eligible_elevation) / 2)
    )

    report = {
        "status": "research-only-unvalidated",
        "artifactId": artifact_id,
        "artifactVersion": artifact_version,
        "input": str(input_path),
        "split": {
            "method": "entirely unseen stations plus chronological adjacent water years; regional contiguous-date storm groups disjoint",
            "leakageGroup": "station_id + storm_group_id + water_year",
            "trainWaterYear": train_water_year,
            "testWaterYear": test_water_year,
            "trainEventCount": len(train_rows),
            "testEventCount": len(test_rows),
            "trainStormCount": len(train_groups),
            "testStormCount": len(test_groups),
            "trainStationCount": len(train_stations),
            "testStationCount": len(test_stations),
            "trainStations": sorted(train_stations),
            "testStations": sorted(test_stations),
            "heldOutStationsRequested": sorted(held_out_stations),
            "trainStart": min(str(row["valid_time"]) for row in train_rows),
            "trainEnd": max(str(row["valid_time"]) for row in train_rows),
            "testStart": min(str(row["valid_time"]) for row in test_rows),
            "testEnd": max(str(row["valid_time"]) for row in test_rows),
        },
        "baseline": baseline,
        "calibrated": calibrated,
        "improvement": {
            "mae": mae_improvement,
            "relativeMae": relative_mae_improvement,
            "medianAbsoluteError": baseline["medianAbsoluteError"] - calibrated["medianAbsoluteError"],
            "heldOutImprovement": robust_improvement,
            "eligibleStationSliceCount": len(eligible_station),
            "improvedStationSliceCount": improved_station,
            "eligibleGeographicSliceCount": len(eligible_geography),
            "improvedGeographicSliceCount": improved_geography,
            "eligibleElevationSliceCount": len(eligible_elevation),
            "improvedElevationSliceCount": improved_elevation,
        },
        "byStation": by_station,
        "byGeographicSlice": by_geography,
        "byElevationBand": by_elevation,
        "uncertainty": {
            "nominalInterval": "p10-p90 log-normal approximation from highest-specificity retained residual sigma",
            "baselineEmpiricalCoverage": baseline_covered / len(evaluated),
            "baselineCoveredEventCount": baseline_covered,
            "calibratedEmpiricalCoverage": covered / len(evaluated),
            "empiricalCoverage": covered / len(evaluated),
            "coveredEventCount": covered,
            "testEventCount": len(evaluated),
        },
        "hierarchyCounts": hierarchy_counts,
        "decision": {
            "commitResearchArtifact": robust_improvement,
            "productionEnablement": False,
            "gate": "MAE improvement >=0.25 and >=5%; median AE non-worsening; MAE improves in at least half of eligible station, geography, and elevation slices.",
            "reason": "Passing retains only an unvalidated Sierra-whitelist research artifact. It cannot authorize runtime registration or production enablement.",
        },
        "limitations": [
            "GHCN WESF may be measured or estimated and does not expose a separate estimation flag; all available measurement, quality, source, date, and time fields were required to pass.",
            "The source is dominated by source-N volunteer observations and does not establish professional study-plot equivalence.",
            "The station whitelist is conservative and reproducible but not an official Sierra physiographic polygon.",
            "The profile source is Open-Meteo's recent best-match historical forecast archive; the exact contributing model is not exposed.",
            "Only exact current MySnow Kuchera is evaluated because requested fields do not provide Cobb dew point and vertical velocity.",
            "P10-P90 uncertainty is an empirical check, not a calibrated production interval.",
            f"The holdout has {len(test_rows)} events at {len(test_stations)} unseen stations; individual slices remain small.",
        ],
    }
    if robust_improvement:
        artifact_output.parent.mkdir(parents=True, exist_ok=True)
        artifact_output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif artifact_output.exists():
        artifact_output.unlink()
    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact, report
