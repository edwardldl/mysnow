from __future__ import annotations

import csv
import json
import math
from datetime import date
from pathlib import Path
from statistics import mean, median
from typing import Any

from .evaluation import apply_artifact
from .trainer import TrainingConfig, train_artifact


def _metrics(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    errors = [float(row[key]) - float(row["observed_slr"]) for row in rows]
    absolute = [abs(error) for error in errors]
    return {
        "bias": mean(errors),
        "mae": mean(absolute),
        "medianAbsoluteError": median(absolute),
        "rmse": math.sqrt(mean(error * error for error in errors)),
    }


def _elevation_band(value: float) -> str:
    if value < 1400:
        return "below_1400m"
    if value < 1700:
        return "1400_to_1699m"
    return "1700m_and_above"


def _slice_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row[key]), []).append(row)
    result: dict[str, Any] = {}
    for label, group in sorted(grouped.items()):
        baseline = _metrics(group, "baseline_prediction")
        calibrated = _metrics(group, "calibrated_prediction")
        result[label] = {
            "eventCount": len(group),
            "stationCount": len({row["station_id"] for row in group}),
            "baseline": baseline,
            "calibrated": calibrated,
            "maeImprovement": baseline["mae"] - calibrated["mae"],
            "medianAbsoluteErrorImprovement": baseline["medianAbsoluteError"] - calibrated["medianAbsoluteError"],
        }
    return result


def evaluate_swiss_station_chronological_holdout(
    input_path: Path,
    artifact_output: Path,
    report_output: Path,
    artifact_id: str,
    artifact_version: str,
    held_out_stations: set[str],
    cutoff_date: date = date(2022, 1, 1),
    prior_effective_sample_size: float = 30.0,
    min_effective_sample_size: float = 5.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        rows: list[dict[str, Any]] = list(csv.DictReader(handle))
    if len(held_out_stations) < 6:
        raise ValueError("Swiss holdout requires at least six entirely unseen stations")
    selected = {str(row["station_id"]) for row in rows}
    missing = held_out_stations - selected
    if missing:
        raise ValueError(f"Held-out stations lack aligned baseline events: {sorted(missing)}")
    train_rows = [
        row for row in rows
        if date.fromisoformat(str(row["valid_time"])[:10]) < cutoff_date
        and row["station_id"] not in held_out_stations
    ]
    test_rows = [
        row for row in rows
        if date.fromisoformat(str(row["valid_time"])[:10]) >= cutoff_date
        and row["station_id"] in held_out_stations
    ]
    if len(train_rows) < 50 or len(test_rows) < 30:
        raise ValueError(f"Insufficient Swiss split: train={len(train_rows)}, test={len(test_rows)}")
    train_stations = {str(row["station_id"]) for row in train_rows}
    test_stations = {str(row["station_id"]) for row in test_rows}
    if train_stations & test_stations:
        raise AssertionError("Stations leaked across Swiss holdout")
    train_groups = {str(row["storm_group_id"]) for row in train_rows}
    test_groups = {str(row["storm_group_id"]) for row in test_rows}
    if train_groups & test_groups:
        raise AssertionError("Storm groups leaked across chronological split")
    train_dates = [str(row["valid_time"])[:10] for row in train_rows]
    test_dates = [str(row["valid_time"])[:10] for row in test_rows]

    config = TrainingConfig(
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        status="unvalidated",
        prior_effective_sample_size=prior_effective_sample_size,
        min_effective_sample_size=min_effective_sample_size,
        source_label="WSL-SLF/EnviDat 590 measured same-window HN+HNW paired to Open-Meteo Historical Forecast profiles; research-only",
        leakage_group="entirely unseen station + contiguous-date regional storm group + chronological cutoff",
        geographic_note="Actual support is the Swiss Alps EnviDat station network only. It is not validation for all European Alps, Europe, or worldwide use.",
    )
    artifact = train_artifact(train_rows, config)
    evaluated: list[dict[str, Any]] = []
    interval_hits = 0
    hierarchy_counts: dict[str, int] = {}
    for row in test_rows:
        prediction = apply_artifact(artifact, row)
        observed = float(row["observed_slr"])
        interval_hits += int(prediction.lower_slr <= observed <= prediction.upper_slr)
        hierarchy = ">".join(prediction.hierarchy) or "identity"
        hierarchy_counts[hierarchy] = hierarchy_counts.get(hierarchy, 0) + 1
        evaluated.append({
            **row,
            "elevation_band": _elevation_band(float(row["elevation_m"])),
            "baseline_prediction": float(row["baseline_slr"]),
            "calibrated_prediction": prediction.calibrated_slr,
            "lower_prediction": prediction.lower_slr,
            "upper_prediction": prediction.upper_slr,
            "correction_factor": prediction.correction_factor,
            "hierarchy": hierarchy,
        })

    baseline = _metrics(evaluated, "baseline_prediction")
    calibrated = _metrics(evaluated, "calibrated_prediction")
    by_station = _slice_metrics(evaluated, "station_id")
    by_elevation = _slice_metrics(evaluated, "elevation_band")
    eligible_stations = [value for value in by_station.values() if value["eventCount"] >= 3]
    improved_stations = sum(value["maeImprovement"] > 0 for value in eligible_stations)
    eligible_bands = [value for value in by_elevation.values() if value["eventCount"] >= 5]
    improved_bands = sum(value["maeImprovement"] > 0 for value in eligible_bands)
    meaningful = (
        baseline["mae"] - calibrated["mae"] >= 0.25
        and calibrated["mae"] < baseline["mae"]
        and calibrated["medianAbsoluteError"] <= baseline["medianAbsoluteError"]
        and improved_stations >= math.ceil(len(eligible_stations) / 2)
        and improved_bands >= math.ceil(len(eligible_bands) / 2)
        and len(test_stations) >= 6
        and len(by_elevation) >= 3
    )
    report = {
        "status": "research-only-unvalidated",
        "artifactId": artifact_id,
        "artifactVersion": artifact_version,
        "input": str(input_path),
        "split": {
            "method": "chronological cutoff plus entirely unseen-station holdout; contiguous-date regional storm groups are disjoint",
            "cutoffDate": cutoff_date.isoformat(),
            "trainDateRange": [min(train_dates), max(train_dates)],
            "testDateRange": [min(test_dates), max(test_dates)],
            "trainEventCount": len(train_rows),
            "testEventCount": len(test_rows),
            "trainStormCount": len(train_groups),
            "testStormCount": len(test_groups),
            "trainStationCount": len(train_stations),
            "testStationCount": len(test_stations),
            "trainStations": sorted(train_stations),
            "testStations": sorted(test_stations),
            "heldOutStationsRequested": sorted(held_out_stations),
            "testElevationBands": sorted(by_elevation),
        },
        "baseline": baseline,
        "calibrated": calibrated,
        "improvement": {
            "biasReductionAbsolute": abs(baseline["bias"]) - abs(calibrated["bias"]),
            "mae": baseline["mae"] - calibrated["mae"],
            "medianAbsoluteError": baseline["medianAbsoluteError"] - calibrated["medianAbsoluteError"],
            "rmse": baseline["rmse"] - calibrated["rmse"],
            "meaningfulRobustUnseenStationImprovement": meaningful,
            "eligibleStationSliceCount": len(eligible_stations),
            "improvedStationSliceCount": improved_stations,
            "eligibleElevationBandCount": len(eligible_bands),
            "improvedElevationBandCount": improved_bands,
        },
        "uncertainty": {
            "nominalInterval": "P10-P90 log-normal approximation from highest-specificity training residual sigma",
            "empiricalCoverage": interval_hits / len(evaluated),
            "coveredEventCount": interval_hits,
            "testEventCount": len(evaluated),
        },
        "byStation": by_station,
        "byElevationBand": by_elevation,
        "hierarchyCounts": hierarchy_counts,
        "decision": {
            "commitResearchArtifact": meaningful,
            "productionEnablement": False,
            "runtimeAcceptance": False,
            "reason": "Retain only if the strict multi-station, multi-elevation, later-time holdout criteria pass; artifact remains unvalidated and runtime-rejected.",
        },
        "geographicSupport": "Swiss Alps EnviDat stations actually represented in the training data; not all European Alps",
        "limitations": [
            "Training and evaluation use only the 2021-11 through 2022-04 slice supported by exact pressure-level Historical Forecast fields.",
            "Open-Meteo best-match does not expose the exact contributing model and hosted commercial-use terms remain uncleared.",
            "The 06:00 CET source window is a fixed UTC+01:00 daily measurement window even during civil daylight-saving time, per the dataset README.",
            "Only current MySnow Kuchera is evaluated; the requested profile lacks inputs for true Cobb.",
            "This is Swiss-station spatial transfer evidence, not independent European-country or production validation.",
        ],
    }
    if meaningful:
        artifact_output.parent.mkdir(parents=True, exist_ok=True)
        artifact_output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif artifact_output.exists():
        artifact_output.unlink()
    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact, report
