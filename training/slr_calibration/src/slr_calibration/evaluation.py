from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any

import h3

from .trainer import TrainingConfig, train_artifact

NORMAL_P10_Z = 1.2815515655446004


@dataclass(frozen=True)
class Prediction:
    calibrated_slr: float
    lower_slr: float
    upper_slr: float
    correction_factor: float
    hierarchy: tuple[str, ...]


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def apply_artifact(artifact: dict[str, Any], row: dict[str, Any]) -> Prediction:
    method = str(row["baseline_method"])
    model = artifact["methods"][method]
    minimum = float(artifact["training"]["minEffectiveSampleSize"])
    latitude = float(row["latitude"])
    longitude = float(row["longitude"])
    region_cell = h3.latlng_to_cell(latitude, longitude, artifact["regionIndex"]["resolution"])
    climate_cell = h3.latlng_to_cell(latitude, longitude, artifact["climateIndex"]["resolution"])
    climate = artifact["climateIndex"]["assignments"].get(climate_cell)
    candidates = [
        ("global", model["global"]),
        ("climate", model["climate"].get(climate) if climate else None),
        ("region", model["region"].get(region_cell)),
        ("local", model["local"].get(str(row.get("site_id") or ""))),
    ]
    used: list[tuple[str, dict[str, Any]]] = []
    for level, node in candidates:
        if node is None or float(node["effectiveSampleSize"]) < minimum:
            break
        used.append((level, node))
    if not used:
        baseline = float(row["baseline_slr"])
        return Prediction(baseline, baseline, baseline, 1.0, ())
    log_residual = sum(float(node["logBias"]) for _, node in used)
    bounds = artifact["bounds"]
    log_residual = _clamp(
        log_residual,
        math.log(float(bounds["minCorrectionFactor"])),
        math.log(float(bounds["maxCorrectionFactor"])),
    )
    factor = math.exp(log_residual)
    baseline = float(row["baseline_slr"])
    calibrated = _clamp(baseline * factor, float(bounds["minSlr"]), float(bounds["maxSlr"]))
    sigma = float(used[-1][1]["residualStdLog"])
    lower = _clamp(baseline * math.exp(log_residual - NORMAL_P10_Z * sigma), float(bounds["minSlr"]), float(bounds["maxSlr"]))
    upper = _clamp(baseline * math.exp(log_residual + NORMAL_P10_Z * sigma), float(bounds["minSlr"]), float(bounds["maxSlr"]))
    return Prediction(calibrated, min(lower, calibrated), max(upper, calibrated), factor, tuple(level for level, _ in used))


def _water_year(row: dict[str, Any]) -> int:
    value = str(row["valid_time"])[:10]
    year, month, _ = (int(part) for part in value.split("-"))
    return year + 1 if month >= 10 else year


def _metrics(rows: list[dict[str, Any]], prediction_key: str) -> dict[str, float]:
    errors = [float(row[prediction_key]) - float(row["observed_slr"]) for row in rows]
    absolute = [abs(value) for value in errors]
    return {
        "bias": mean(errors),
        "mae": mean(absolute),
        "medianAbsoluteError": median(absolute),
        "rmse": math.sqrt(mean(value * value for value in errors)),
    }


def _read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def evaluate_temporal_holdout(
    input_path: Path,
    artifact_output: Path,
    report_output: Path,
    artifact_id: str,
    artifact_version: str,
    test_water_year: int | None = None,
    prior_effective_sample_size: float = 30.0,
    min_effective_sample_size: float = 5.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = _read_rows(input_path)
    if len(rows) < 10:
        raise ValueError("At least 10 aligned events are required for a temporal holdout")
    counts: dict[int, int] = {}
    for row in rows:
        value = _water_year(row)
        counts[value] = counts.get(value, 0) + 1
    if test_water_year is None:
        eligible = [year for year, count in counts.items() if count >= 5]
        if not eligible:
            raise ValueError("No water year has at least five aligned events for holdout")
        test_water_year = max(eligible)

    test_groups = {
        str(row.get("storm_group_id") or row["event_id"])
        for row in rows if _water_year(row) == test_water_year
    }
    train_rows = [row for row in rows if str(row.get("storm_group_id") or row["event_id"]) not in test_groups and _water_year(row) < test_water_year]
    test_rows = [row for row in rows if str(row.get("storm_group_id") or row["event_id"]) in test_groups]
    if len(train_rows) < 5 or len(test_rows) < 5:
        raise ValueError(f"Insufficient split: train={len(train_rows)}, test={len(test_rows)}")
    if {row.get("storm_group_id") for row in train_rows} & {row.get("storm_group_id") for row in test_rows}:
        raise AssertionError("Storm groups leaked across the temporal split")

    config = TrainingConfig(
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        status="unvalidated",
        prior_effective_sample_size=prior_effective_sample_size,
        min_effective_sample_size=min_effective_sample_size,
        source_label="CSSL CC0 direct fresh-snow labels paired to Open-Meteo Historical Forecast profiles; research-only",
        leakage_group="storm_group_id",
        geographic_note="One fixed CSSL site at 39.3257,-120.3681; temporal holdout only; not Sierra-regional or worldwide validation.",
    )
    artifact = train_artifact(train_rows, config)
    evaluated: list[dict[str, Any]] = []
    hierarchy_counts: dict[str, int] = {}
    interval_hits = 0
    for row in test_rows:
        result = apply_artifact(artifact, row)
        hierarchy = ">".join(result.hierarchy) or "identity"
        hierarchy_counts[hierarchy] = hierarchy_counts.get(hierarchy, 0) + 1
        observed = float(row["observed_slr"])
        if result.lower_slr <= observed <= result.upper_slr:
            interval_hits += 1
        evaluated.append({
            **row,
            "baseline_prediction": float(row["baseline_slr"]),
            "calibrated_prediction": result.calibrated_slr,
            "lower_prediction": result.lower_slr,
            "upper_prediction": result.upper_slr,
            "correction_factor": result.correction_factor,
            "hierarchy": hierarchy,
        })

    baseline_metrics = _metrics(evaluated, "baseline_prediction")
    calibrated_metrics = _metrics(evaluated, "calibrated_prediction")
    mae_improvement = baseline_metrics["mae"] - calibrated_metrics["mae"]
    median_improvement = baseline_metrics["medianAbsoluteError"] - calibrated_metrics["medianAbsoluteError"]
    improved = mae_improvement > 0 and median_improvement >= 0
    report = {
        "status": "research-only-unvalidated",
        "artifactId": artifact_id,
        "artifactVersion": artifact_version,
        "input": str(input_path),
        "availableWaterYears": dict(sorted(counts.items())),
        "split": {
            "method": "chronological water-year holdout with complete storm groups",
            "leakageGroup": "storm_group_id",
            "trainWaterYears": sorted({_water_year(row) for row in train_rows}),
            "testWaterYear": test_water_year,
            "trainEventCount": len(train_rows),
            "testEventCount": len(test_rows),
            "trainStormCount": len({row.get("storm_group_id") for row in train_rows}),
            "testStormCount": len({row.get("storm_group_id") for row in test_rows}),
            "stationCount": 1,
        },
        "baseline": baseline_metrics,
        "calibrated": calibrated_metrics,
        "improvement": {
            "mae": mae_improvement,
            "medianAbsoluteError": median_improvement,
            "heldOutImprovement": improved,
        },
        "uncertainty": {
            "nominalInterval": "p10-p90 log-normal approximation from highest-specificity residual sigma",
            "empiricalCoverage": interval_hits / len(evaluated),
            "coveredEventCount": interval_hits,
            "testEventCount": len(evaluated),
        },
        "hierarchyCounts": hierarchy_counts,
        "decision": {
            "commitResearchArtifact": improved,
            "productionEnablement": False,
            "reason": "Single-site temporal evidence cannot establish Sierra-regional or worldwide generalization.",
        },
        "limitations": [
            "One CSSL site only; this evaluates repeat-time behavior at one location, not spatial transfer.",
            "The profile source is a recent best-match forecast archive whose exact contributing model is not exposed.",
            "CSSL precipitation timing is not documented precisely enough for exact hourly matching; a fixed-PST calendar-day proxy is used.",
            "Only MySnow Kuchera is evaluated because the profile request lacks dew point and vertical velocity required by Cobb.",
        ],
    }
    artifact_output.parent.mkdir(parents=True, exist_ok=True)
    artifact_output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact, report


def _slice_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(key) or "unknown"), []).append(row)
    result: dict[str, Any] = {}
    for name, group in sorted(grouped.items()):
        baseline = _metrics(group, "baseline_prediction")
        calibrated = _metrics(group, "calibrated_prediction")
        result[name] = {
            "eventCount": len(group),
            "baseline": baseline,
            "calibrated": calibrated,
            "maeImprovement": baseline["mae"] - calibrated["mae"],
            "medianAbsoluteErrorImprovement": baseline["medianAbsoluteError"] - calibrated["medianAbsoluteError"],
        }
    return result


def evaluate_rockies_station_temporal_holdout(
    input_path: Path,
    artifact_output: Path,
    report_output: Path,
    artifact_id: str,
    artifact_version: str,
    held_out_stations: set[str],
    train_water_year: int = 2024,
    test_water_year: int = 2025,
    prior_effective_sample_size: float = 30.0,
    min_effective_sample_size: float = 5.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = _read_rows(input_path)
    if not held_out_stations:
        raise ValueError("At least one held-out station is required")
    train_rows = [row for row in rows if _water_year(row) == train_water_year and row["station_id"] not in held_out_stations]
    test_rows = [row for row in rows if _water_year(row) == test_water_year and row["station_id"] in held_out_stations]
    if len(train_rows) < 20 or len(test_rows) < 10:
        raise ValueError(f"Insufficient Rockies split: train={len(train_rows)}, test={len(test_rows)}")
    train_stations = {str(row["station_id"]) for row in train_rows}
    test_stations = {str(row["station_id"]) for row in test_rows}
    if train_stations & test_stations:
        raise AssertionError("Stations leaked across the Rockies split")
    train_groups = {str(row.get("storm_group_id") or row["event_id"]) for row in train_rows}
    test_groups = {str(row.get("storm_group_id") or row["event_id"]) for row in test_rows}
    if train_groups & test_groups:
        raise AssertionError("Storm groups leaked across the Rockies split")

    config = TrainingConfig(
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        status="unvalidated",
        prior_effective_sample_size=prior_effective_sample_size,
        min_effective_sample_size=min_effective_sample_size,
        source_label="NOAA GHCN-Daily same-window SNOW+WESF labels paired to Open-Meteo Historical Forecast profiles; research-only",
        leakage_group="station_id + regional storm_group_id + water_year",
        geographic_note="U.S. Rockies research mask; trained on selected Colorado stations in WY2024 and evaluated on previously unseen CO/WY/NM stations in WY2025. Not production, Canadian Rockies, PNW, or worldwide validation.",
    )
    artifact = train_artifact(train_rows, config)
    evaluated: list[dict[str, Any]] = []
    hierarchy_counts: dict[str, int] = {}
    interval_hits = 0
    for row in test_rows:
        result = apply_artifact(artifact, row)
        hierarchy = ">".join(result.hierarchy) or "identity"
        hierarchy_counts[hierarchy] = hierarchy_counts.get(hierarchy, 0) + 1
        observed = float(row["observed_slr"])
        if result.lower_slr <= observed <= result.upper_slr:
            interval_hits += 1
        evaluated.append({
            **row,
            "baseline_prediction": float(row["baseline_slr"]),
            "calibrated_prediction": result.calibrated_slr,
            "lower_prediction": result.lower_slr,
            "upper_prediction": result.upper_slr,
            "correction_factor": result.correction_factor,
            "hierarchy": hierarchy,
        })

    baseline_metrics = _metrics(evaluated, "baseline_prediction")
    calibrated_metrics = _metrics(evaluated, "calibrated_prediction")
    by_state = _slice_metrics(evaluated, "state")
    by_station = _slice_metrics(evaluated, "station_id")
    out_of_co = [row for row in evaluated if str(row.get("state")) != "CO"]
    out_of_co_metrics = None
    out_of_co_improved = True
    if out_of_co:
        out_baseline = _metrics(out_of_co, "baseline_prediction")
        out_calibrated = _metrics(out_of_co, "calibrated_prediction")
        out_of_co_metrics = {
            "eventCount": len(out_of_co),
            "stationCount": len({row["station_id"] for row in out_of_co}),
            "states": sorted({str(row.get("state")) for row in out_of_co}),
            "baseline": out_baseline,
            "calibrated": out_calibrated,
            "maeImprovement": out_baseline["mae"] - out_calibrated["mae"],
        }
        out_of_co_improved = len(out_of_co) < 3 or out_calibrated["mae"] <= out_baseline["mae"]
    eligible_station_slices = [value for value in by_station.values() if value["eventCount"] >= 2]
    improved_station_slices = sum(value["maeImprovement"] > 0 for value in eligible_station_slices)
    overall_improved = (
        calibrated_metrics["mae"] < baseline_metrics["mae"]
        and calibrated_metrics["medianAbsoluteError"] <= baseline_metrics["medianAbsoluteError"]
        and out_of_co_improved
        and improved_station_slices >= max(1, math.ceil(len(eligible_station_slices) / 2))
    )
    report = {
        "status": "research-only-unvalidated",
        "artifactId": artifact_id,
        "artifactVersion": artifact_version,
        "input": str(input_path),
        "split": {
            "method": "combined station and chronological water-year holdout; regional contiguous-date storm groups kept intact",
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
            "trainStates": sorted({str(row.get("state")) for row in train_rows}),
            "testStates": sorted({str(row.get("state")) for row in test_rows}),
        },
        "baseline": baseline_metrics,
        "calibrated": calibrated_metrics,
        "improvement": {
            "mae": baseline_metrics["mae"] - calibrated_metrics["mae"],
            "medianAbsoluteError": baseline_metrics["medianAbsoluteError"] - calibrated_metrics["medianAbsoluteError"],
            "heldOutImprovement": overall_improved,
            "eligibleStationSliceCount": len(eligible_station_slices),
            "improvedStationSliceCount": improved_station_slices,
        },
        "outOfColoradoHoldout": out_of_co_metrics,
        "byState": by_state,
        "byStation": by_station,
        "uncertainty": {
            "nominalInterval": "p10-p90 log-normal approximation from highest-specificity residual sigma",
            "empiricalCoverage": interval_hits / len(evaluated),
            "coveredEventCount": interval_hits,
            "testEventCount": len(evaluated),
        },
        "hierarchyCounts": hierarchy_counts,
        "decision": {
            "commitResearchArtifact": overall_improved,
            "productionEnablement": False,
            "reason": "A narrow station+time holdout can justify retaining an unvalidated research artifact, but cannot authorize production or broad Rockies generalization.",
        },
        "limitations": [
            "The source dataset is dominated by Colorado volunteer stations and sparse outside Colorado.",
            "Trainer effective sample sizes are row-level rather than independent-storm counts, so support is overstated when stations share a storm.",
            "The explicit state/elevation/coordinate mask is reproducible but is not an official physiographic Rockies polygon.",
            "The profile source is a recent best-match forecast archive whose exact contributing model is not exposed.",
            "GHCN daily labels are matched to a 24-hour window ending at reported America/Denver local observation time; source timing metadata can still contain observer error.",
            "Only MySnow Kuchera is evaluated because the profile request lacks dew point and vertical velocity required by Cobb.",
            f"The test set has only {len(test_rows)} events at {len(test_stations)} unseen stations; just {len(out_of_co)} events are outside Colorado, and sparse or absent state slices are not independent validation.",
            f"The nominal P10-P90 approximation covered {interval_hits}/{len(evaluated)} events ({interval_hits / len(evaluated):.1%}), so uncertainty is materially undercalibrated.",
        ],
    }
    artifact_output.parent.mkdir(parents=True, exist_ok=True)
    artifact_output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact, report
