#!/usr/bin/env python3
"""Deterministic, pure-stdlib methodology diagnostics for regional SLR calibration.

This script is research-only. It writes diagnostics, never a production artifact, and
never changes the application runtime or production calibration configuration.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

REGIONS = {
    "rockies": {
        "baseline": "data/slr-training-data/normalized/baseline/ghcn_rockies_kuchera.csv",
        "profiles": "data/slr-training-data/normalized/profiles/ghcn_rockies_openmeteo_profiles.jsonl",
        "report": "data/slr-training-data/reports/rockies-station-temporal-holdout.json",
        "temporal": {"kind": "water_year", "train": 2024, "test": 2025},
        "counts": {"train": 103, "test": 21},
    },
    "sierra": {
        "baseline": "data/sierra-slr-research/normalized/sierra_kuchera.csv",
        "profiles": "data/sierra-slr-research/normalized/sierra_profiles.jsonl",
        "report": "data/sierra-slr-research/reports/evaluation.json",
        "temporal": {"kind": "water_year", "train": 2023, "test": 2024},
        "counts": {"train": 206, "test": 19},
    },
    "swiss": {
        "baseline": "data/swiss-alps-slr/baseline.csv",
        "profiles": "data/swiss-alps-slr/profiles.jsonl",
        "report": "data/swiss-alps-slr/reports/evaluation.json",
        "temporal": {
            "kind": "date_range",
            "train": ["2021-11-02", "2021-12-30"],
            "test": ["2022-01-05", "2022-04-30"],
        },
        "counts": {"train": 165, "test": 125},
    },
    "northeast": {
        "baseline": "data/northeast-slr-research/normalized/baseline.csv",
        "profiles": "data/northeast-slr-research/normalized/profiles.jsonl",
        "report": "data/northeast-slr-research/reports/evaluation.json",
        "temporal": {"kind": "water_year", "train": 2024, "test": 2025},
        "counts": {"train": 60, "test": 80},
    },
}
SELECTION_REGIONS = ("rockies", "sierra", "swiss")
US_LABEL_PROVENANCE = {
    "rockies": {
        "selectedSplit": "data/slr-training-data/normalized/observations/ghcn_rockies_selected_split.csv",
        "expectedSourceFlags": {"N": 148},
    },
    "sierra": {
        "selectedSplit": "data/sierra-slr-research/normalized/sierra_selected_split.csv",
        "expectedSourceFlags": {"SNOW:N;WESF:N": 197, "SNOW:7;WESF:N": 48},
    },
    "northeast": {
        "selectedSplit": "data/northeast-slr-research/normalized/selected-split.csv",
        "expectedSourceFlags": {"SNOW:N;WESF:N": 179},
    },
}
BALANCE_MODES = ("event", "station", "storm", "joint")
SHRINKAGES = (0.0, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0)
RIDGE_ALPHAS = (0.01, 0.1, 1.0, 10.0, 100.0, 1000.0)
PRESSURE_LEVELS = (1000, 975, 950, 925, 900, 850, 800, 700, 600, 500)
RIDGE_FEATURE_SETS = ("thermal", "full")
EPSILON = 1e-12


@dataclass(frozen=True)
class Event:
    event_id: str
    station_id: str
    storm_id: str
    valid_time: str
    observed: float
    baseline: float
    qc_weight: float
    elevation_m: float
    swe_proxy_mm: float
    geographic_slice: str
    features: dict[str, float | None]


def as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def weighted_mean(values: Iterable[tuple[float | None, float]]) -> float | None:
    kept = [(value, weight) for value, weight in values if value is not None and weight > 0]
    if not kept:
        return None
    total = sum(weight for _, weight in kept)
    return sum(float(value) * weight for value, weight in kept) / total


def extract_features(profile: dict[str, Any], baseline_row: dict[str, str]) -> dict[str, float | None]:
    """Extract only predictors physically present in retained inputs."""
    hours = profile.get("hourly") or []
    hour_weights = []
    for hour in hours:
        precip = as_float(hour.get("precipitationMm")) or 0.0
        snowfall = as_float(hour.get("snowfallCm")) or 0.0
        hour_weights.append(max(precip, snowfall * 0.1, 0.0))
    if not any(weight > 0 for weight in hour_weights):
        hour_weights = [1.0] * len(hours)

    result: dict[str, float | None] = {
        "elevation_m": as_float(baseline_row.get("elevation_m")),
        "baseline_slr": as_float(baseline_row.get("baseline_slr")),
        "swe_proxy_mm": as_float(baseline_row.get("model_frozen_swe_proxy_weight_mm")),
        "profile_hour_count": float(len(hours)),
        "temperature2m_c_mean": weighted_mean(
            (as_float(hour.get("temperature2mC")), weight) for hour, weight in zip(hours, hour_weights)
        ),
        "freezing_level_m_mean": weighted_mean(
            (as_float(hour.get("freezingLevelHeightM")), weight) for hour, weight in zip(hours, hour_weights)
        ),
        "precipitation_mm_sum": sum(as_float(hour.get("precipitationMm")) or 0.0 for hour in hours),
        "snowfall_cm_sum": sum(as_float(hour.get("snowfallCm")) or 0.0 for hour in hours),
    }
    temperatures = [as_float(hour.get("temperature2mC")) for hour in hours]
    temperatures = [value for value in temperatures if value is not None]
    freezing = [as_float(hour.get("freezingLevelHeightM")) for hour in hours]
    freezing = [value for value in freezing if value is not None]
    result["temperature2m_c_min"] = min(temperatures) if temperatures else None
    result["temperature2m_c_max"] = max(temperatures) if temperatures else None
    result["freezing_level_m_min"] = min(freezing) if freezing else None
    result["freezing_level_m_max"] = max(freezing) if freezing else None

    for pressure in PRESSURE_LEVELS:
        temperatures_at_level: list[tuple[float | None, float]] = []
        heights_at_level: list[tuple[float | None, float]] = []
        for hour, weight in zip(hours, hour_weights):
            by_pressure = {
                int(level.get("pressureHpa")): level
                for level in (hour.get("levels") or [])
                if as_float(level.get("pressureHpa")) is not None
            }
            level = by_pressure.get(pressure, {})
            temperatures_at_level.append((as_float(level.get("temperatureC")), weight))
            heights_at_level.append((as_float(level.get("geopotentialHeightM")), weight))
        result[f"temperature_{pressure}hpa_c_mean"] = weighted_mean(temperatures_at_level)
        result[f"geopotential_{pressure}hpa_m_mean"] = weighted_mean(heights_at_level)
    return result


def water_year(valid_time: str) -> int:
    day = datetime.fromisoformat(valid_time.replace("Z", "+00:00")).date()
    return day.year + (1 if day.month >= 10 else 0)


def local_date(valid_time: str) -> date:
    return datetime.fromisoformat(valid_time.replace("Z", "+00:00")).date()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_region(root: Path, name: str) -> tuple[list[Event], list[Event], dict[str, Any]]:
    config = REGIONS[name]
    baseline_path = root / config["baseline"]
    profile_path = root / config["profiles"]
    report_path = root / config["report"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    split = report["split"]

    expected = config["counts"]
    assert int(split["trainEventCount"]) == expected["train"], (name, split["trainEventCount"])
    assert int(split["testEventCount"]) == expected["test"], (name, split["testEventCount"])
    temporal = config["temporal"]
    if temporal["kind"] == "water_year":
        assert int(split["trainWaterYear"]) == temporal["train"]
        assert int(split["testWaterYear"]) == temporal["test"]
    else:
        assert split["trainDateRange"] == temporal["train"]
        assert split["testDateRange"] == temporal["test"]

    profiles: dict[str, dict[str, Any]] = {}
    with profile_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                profile = json.loads(line)
                profiles[profile["event_id"]] = profile

    with baseline_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    def make_event(row: dict[str, str]) -> Event:
        profile = profiles.get(row["event_id"])
        if profile is None:
            raise AssertionError(f"{name}: missing retained profile for {row['event_id']}")
        observed = as_float(row.get("observed_slr"))
        baseline = as_float(row.get("baseline_slr"))
        if observed is None or baseline is None:
            raise AssertionError(f"{name}: non-finite target/baseline for {row['event_id']}")
        return Event(
            event_id=row["event_id"], station_id=row["station_id"], storm_id=row["storm_group_id"],
            valid_time=row["valid_time"], observed=observed, baseline=baseline,
            qc_weight=as_float(row.get("qc_weight")) or 1.0,
            elevation_m=as_float(row.get("elevation_m")) or 0.0,
            swe_proxy_mm=as_float(row.get("model_frozen_swe_proxy_weight_mm")) or 0.0,
            geographic_slice=row.get("geographic_slice", ""),
            features=extract_features(profile, row),
        )

    train_stations = set(split["trainStations"])
    test_stations = set(split["testStations"])

    def matches(row: dict[str, str], subset: str) -> bool:
        stations = train_stations if subset == "train" else test_stations
        if row["station_id"] not in stations:
            return False
        if temporal["kind"] == "water_year":
            return water_year(row["valid_time"]) == temporal[subset]
        start, end = map(date.fromisoformat, temporal[subset])
        return start <= local_date(row["valid_time"]) <= end

    train = [make_event(row) for row in rows if matches(row, "train")]
    test = [make_event(row) for row in rows if matches(row, "test")]
    assert len(train) == expected["train"], f"{name}: train count {len(train)} != {expected['train']}"
    assert len(test) == expected["test"], f"{name}: test count {len(test)} != {expected['test']}"
    assert {event.station_id for event in train} == train_stations
    assert {event.station_id for event in test} == set(split["testStations"])
    assert not ({event.event_id for event in train} & {event.event_id for event in test})
    metadata = {
        "baselinePath": config["baseline"], "profilesPath": config["profiles"], "reportPath": config["report"],
        "baselineSha256": sha256(baseline_path), "profilesSha256": sha256(profile_path), "reportSha256": sha256(report_path),
        "trainStations": sorted(train_stations), "testStations": sorted(test_stations),
        "temporalConstraint": temporal, "trainEventCount": len(train), "testEventCount": len(test),
        "trainStormCount": len({event.storm_id for event in train}), "testStormCount": len({event.storm_id for event in test}),
        "existingEvaluationHierarchyCounts": report.get("hierarchyCounts"),
    }
    return train, test, metadata


def inspect_us_label_provenance(root: Path) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for region, config in US_LABEL_PROVENANCE.items():
        path = root / config["selectedSplit"]
        with path.open(encoding="utf-8", newline="") as handle:
            counts = Counter(row["source_flag"] for row in csv.DictReader(handle))
        expected = Counter(config["expectedSourceFlags"])
        assert counts == expected, f"{region}: source-flag counts {dict(counts)} != {dict(expected)}"
        details[region] = {
            "selectedSplitPath": config["selectedSplit"],
            "selectedSplitSha256": sha256(path),
            "selectedLabelCount": sum(counts.values()),
            "sourceFlagCounts": dict(sorted(counts.items())),
        }
    return {
        "status": "authorization-unresolved",
        "warning": (
            "All selected U.S. labels are source-N/CoCoRaHS: Rockies 148/148 N; "
            "Sierra 197 SNOW:N/WESF:N plus 48 SNOW:7/WESF:N; Northeast 179/179 N. "
            "NOAA documentation defines source N as CoCoRaHS. The later strict broad-U.S. "
            "audit excludes source N because underlying reuse and commercial-training "
            "authorization was not positively established. This does not assert illegality; "
            "authorization is unresolved. U.S. results therefore remain research-only even "
            "where physical pairs pass non-circular quality control."
        ),
        "regions": details,
        "strictBroadUsAudit": "data/slr-training-data/reports/broad-us-regional-audit/broad-us-regional-audit.json",
    }


def balance_weights(events: Sequence[Event], mode: str) -> list[float]:
    if mode not in BALANCE_MODES:
        raise ValueError(f"unknown balancing mode: {mode}")
    station_counts = Counter(event.station_id for event in events)
    storm_counts = Counter(event.storm_id for event in events)
    raw = []
    for event in events:
        weight = event.qc_weight
        if mode == "station":
            weight /= station_counts[event.station_id]
        elif mode == "storm":
            weight /= storm_counts[event.storm_id]
        elif mode == "joint":
            weight /= station_counts[event.station_id] * storm_counts[event.storm_id]
        raw.append(weight)
    scale = len(raw) / sum(raw) if raw else 1.0
    return [weight * scale for weight in raw]


def effective_sample_size(weights: Sequence[float]) -> float:
    total = sum(weights)
    return total * total / sum(weight * weight for weight in weights)


def fit_intercept(events: Sequence[Event], balance: str, shrinkage: float) -> dict[str, Any]:
    weights = balance_weights(events, balance)
    mean_residual = sum(w * (e.observed - e.baseline) for e, w in zip(events, weights)) / sum(weights)
    n_eff = effective_sample_size(weights)
    return {"offset": mean_residual * n_eff / (n_eff + shrinkage), "effectiveSampleSize": n_eff}


def predict_intercept(model: dict[str, Any], events: Sequence[Event]) -> list[float]:
    return [event.baseline + model["offset"] for event in events]


def fit_log_intercept(events: Sequence[Event]) -> dict[str, float]:
    """Station-balanced multiplicative correction in log(SLR) residual space."""
    weights = balance_weights(events, "station")
    offset = sum(
        weight * math.log(event.observed / event.baseline)
        for event, weight in zip(events, weights)
    ) / sum(weights)
    return {"logOffset": offset, "multiplier": math.exp(offset), "effectiveSampleSize": effective_sample_size(weights)}


def predict_log_intercept(model: dict[str, float], events: Sequence[Event]) -> list[float]:
    return [event.baseline * model["multiplier"] for event in events]


def log_residual_summary(events: Sequence[Event]) -> dict[str, Any]:
    values = [math.log(event.observed / event.baseline) for event in events]
    by_station: dict[str, list[float]] = defaultdict(list)
    for event, value in zip(events, values):
        by_station[event.station_id].append(value)
    station_means = {station: statistics.fmean(items) for station, items in sorted(by_station.items())}
    return {
        "mean": statistics.fmean(values), "median": statistics.median(values),
        "stationMeans": station_means,
        "stationMeanRange": [min(station_means.values()), max(station_means.values())],
    }


def solve_linear(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    """Solve Ax=b with deterministic partial-pivot Gaussian elimination."""
    n = len(vector)
    augmented = [list(map(float, matrix[row])) + [float(vector[row])] for row in range(n)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda row: (abs(augmented[row][column]), -row))
        if abs(augmented[pivot][column]) < EPSILON:
            raise ValueError("singular linear system")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        for index in range(column, n + 1):
            augmented[column][index] /= divisor
        for row in range(n):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0:
                continue
            for index in range(column, n + 1):
                augmented[row][index] -= factor * augmented[column][index]
    return [augmented[row][n] for row in range(n)]


def usable_feature_names(events: Sequence[Event], feature_set: str = "full") -> list[str]:
    if feature_set not in RIDGE_FEATURE_SETS:
        raise ValueError(f"unknown ridge feature set: {feature_set}")
    names = sorted({name for event in events for name in event.features})
    if feature_set == "thermal":
        names = [
            name for name in names
            if name.startswith("temperature_")
            or name.startswith("temperature2m_")
            or name.startswith("freezing_level_")
            or name.startswith("geopotential_")
        ]
    return [name for name in names if any(event.features.get(name) is not None for event in events)]


def fit_ridge(events: Sequence[Event], balance: str, alpha: float, feature_set: str = "full") -> dict[str, Any]:
    names = usable_feature_names(events, feature_set)
    medians: dict[str, float] = {}
    means: dict[str, float] = {}
    scales: dict[str, float] = {}
    for name in names:
        present = [float(event.features[name]) for event in events if event.features.get(name) is not None]
        median = statistics.median(present)
        filled = [float(event.features.get(name)) if event.features.get(name) is not None else median for event in events]
        mean = statistics.fmean(filled)
        scale = math.sqrt(statistics.fmean((value - mean) ** 2 for value in filled))
        medians[name], means[name], scales[name] = median, mean, scale if scale > EPSILON else 1.0
    rows = [[1.0] + [((float(event.features.get(name)) if event.features.get(name) is not None else medians[name]) - means[name]) / scales[name] for name in names] for event in events]
    targets = [event.observed - event.baseline for event in events]
    weights = balance_weights(events, balance)
    size = len(names) + 1
    gram = [[0.0] * size for _ in range(size)]
    rhs = [0.0] * size
    for row, target, weight in zip(rows, targets, weights):
        for i in range(size):
            rhs[i] += weight * row[i] * target
            for j in range(i, size):
                gram[i][j] += weight * row[i] * row[j]
    for i in range(size):
        for j in range(i):
            gram[i][j] = gram[j][i]
        if i > 0:
            gram[i][i] += alpha
    coefficients = solve_linear(gram, rhs)
    return {
        "featureNames": names, "medians": medians, "means": means, "scales": scales,
        "coefficients": coefficients, "alpha": alpha, "featureSet": feature_set,
        "effectiveSampleSize": effective_sample_size(weights),
    }


def predict_ridge(model: dict[str, Any], events: Sequence[Event]) -> list[float]:
    predictions = []
    for event in events:
        residual = model["coefficients"][0]
        for index, name in enumerate(model["featureNames"], start=1):
            value = event.features.get(name)
            if value is None:
                value = model["medians"][name]
            residual += model["coefficients"][index] * (float(value) - model["means"][name]) / model["scales"][name]
        predictions.append(event.baseline + residual)
    return predictions


def metrics(events: Sequence[Event], predictions: Sequence[float]) -> dict[str, float]:
    errors = [prediction - event.observed for event, prediction in zip(events, predictions)]
    return {
        "mae": statistics.fmean(abs(error) for error in errors),
        "bias": statistics.fmean(errors),
        "rmse": math.sqrt(statistics.fmean(error * error for error in errors)),
    }


def grouped_cv_folds(events: Sequence[Event], family: str) -> list[tuple[list[Event], list[Event]]]:
    if family == "unseen_station":
        groups = [[station] for station in sorted({event.station_id for event in events})]
        field = "station_id"
    elif family == "contiguous_storm":
        field = "storm_id"
        first_time = {
            storm: min(local_date(event.valid_time) for event in events if event.storm_id == storm)
            for storm in {event.storm_id for event in events}
        }
        ordered = sorted(first_time, key=lambda storm: (first_time[storm], storm))
        fold_count = min(5, len(ordered))
        base, remainder = divmod(len(ordered), fold_count)
        groups = []
        cursor = 0
        for index in range(fold_count):
            size = base + (1 if index < remainder else 0)
            groups.append(ordered[cursor:cursor + size])
            cursor += size
    else:
        raise ValueError(f"unknown CV family: {family}")
    folds = []
    for held_groups in groups:
        held_set = set(held_groups)
        train = [event for event in events if getattr(event, field) not in held_set]
        validation = [event for event in events if getattr(event, field) in held_set]
        if train and validation:
            folds.append((train, validation))
    assert sum(len(validation) for _, validation in folds) == len(events)
    return folds


def grouped_cv_score(
    events: Sequence[Event], method: str, balance: str, hyperparameter: float,
    feature_set: str = "full",
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for family in ("unseen_station", "contiguous_storm"):
        held_events: list[Event] = []
        predictions: list[float] = []
        folds = grouped_cv_folds(events, family)
        for fit_events, validation in folds:
            if method == "intercept":
                values = predict_intercept(fit_intercept(fit_events, balance, hyperparameter), validation)
            elif method == "ridge":
                values = predict_ridge(fit_ridge(fit_events, balance, hyperparameter, feature_set), validation)
            else:
                raise ValueError(f"unknown CV method: {method}")
            held_events.extend(validation)
            predictions.extend(values)
        output[family] = {
            "foldCount": len(folds), "eventCount": len(held_events),
            "metrics": metrics(held_events, predictions),
        }
    output["meanFamilyMae"] = statistics.fmean(
        output[family]["metrics"]["mae"] for family in ("unseen_station", "contiguous_storm")
    )
    return output


def elevation_band(region: str, elevation_m: float) -> str:
    if region == "northeast":
        if elevation_m < 400:
            return "below_400_m"
        if elevation_m < 500:
            return "400_to_499_m"
        return "500_m_and_above"
    if region == "sierra":
        if elevation_m < 1500:
            return "below_1500_m"
        if elevation_m < 2000:
            return "1500_to_1999_m"
        return "2000_m_and_above"
    if region == "swiss":
        if elevation_m < 1400:
            return "below_1400_m"
        if elevation_m < 1700:
            return "1400_to_1699_m"
        return "1700_m_and_above"
    if elevation_m < 2000:
        return "below_2000_m"
    if elevation_m < 2500:
        return "2000_to_2499_m"
    return "2500_m_and_above"


def fixed_holdout_slice_metrics(
    region: str, events: Sequence[Event], predictions: dict[str, Sequence[float]],
) -> dict[str, Any]:
    dimensions: dict[str, dict[str, list[int]]] = {
        "station": defaultdict(list), "storm": defaultdict(list),
        "elevationBand": defaultdict(list),
    }
    if any(event.geographic_slice for event in events):
        dimensions["geographicSlice"] = defaultdict(list)
    for index, event in enumerate(events):
        dimensions["station"][event.station_id].append(index)
        dimensions["storm"][event.storm_id].append(index)
        dimensions["elevationBand"][elevation_band(region, event.elevation_m)].append(index)
        if event.geographic_slice:
            dimensions["geographicSlice"][event.geographic_slice].append(index)
    slices: dict[str, Any] = {}
    improved: dict[str, Any] = {}
    candidate_names = [name for name in predictions if name != "baseline"]
    for dimension, groups in dimensions.items():
        slices[dimension] = {}
        for label, indices in sorted(groups.items()):
            subset = [events[index] for index in indices]
            slices[dimension][label] = {
                "eventCount": len(indices),
                "metrics": {
                    method: metrics(subset, [values[index] for index in indices])
                    for method, values in predictions.items()
                },
            }
        improved[dimension] = {}
        for method in candidate_names:
            count = sum(
                item["metrics"][method]["mae"] < item["metrics"]["baseline"]["mae"]
                for item in slices[dimension].values()
            )
            improved[dimension][method] = {"improved": count, "total": len(slices[dimension])}
    return {"byDimension": slices, "improvedSliceCounts": improved}


def finite_sample_order_quantile(scores: Sequence[float], coverage: float = 0.8) -> float:
    finite = sorted(float(score) for score in scores if math.isfinite(float(score)))
    if not finite:
        raise ValueError("no finite empirical scores")
    rank = min(len(finite), math.ceil((len(finite) + 1) * coverage))
    return finite[rank - 1]


def cluster_bootstrap(
    events: Sequence[Event], predictions: dict[str, Sequence[float]], cluster_field: str,
    repetitions: int = 2000, seed: int = 20260826,
) -> dict[str, Any]:
    clusters: dict[str, list[int]] = defaultdict(list)
    for index, event in enumerate(events):
        clusters[getattr(event, cluster_field)].append(index)
    keys = sorted(clusters)
    rng = random.Random(seed)
    samples: dict[str, list[float]] = defaultdict(list)
    for _ in range(repetitions):
        selected = [rng.choice(keys) for _ in keys]
        indices = [index for key in selected for index in clusters[key]]
        for model_name, values in predictions.items():
            samples[f"{model_name}.mae"].append(statistics.fmean(abs(values[i] - events[i].observed) for i in indices))
            if model_name != "baseline":
                samples[f"{model_name}.maeDeltaVsBaseline"].append(
                    statistics.fmean(abs(values[i] - events[i].observed) - abs(predictions["baseline"][i] - events[i].observed) for i in indices)
                )
    def percentile(values: Sequence[float], probability: float) -> float:
        ordered = sorted(values)
        position = probability * (len(ordered) - 1)
        lower = int(math.floor(position)); upper = int(math.ceil(position))
        if lower == upper:
            return ordered[lower]
        return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)
    return {
        "clusterCount": len(keys), "repetitions": repetitions,
        "intervals95": {name: [percentile(values, 0.025), percentile(values, 0.975)] for name, values in sorted(samples.items())},
    }


def leave_group_out_scores(
    train: Sequence[Event], method: str, group_field: str, balance: str, hyperparameter: float,
    feature_set: str = "full",
) -> list[float]:
    groups = sorted({getattr(event, group_field) for event in train})
    scores = []
    for group in groups:
        fit = [event for event in train if getattr(event, group_field) != group]
        held = [event for event in train if getattr(event, group_field) == group]
        if not fit or not held:
            continue
        if method == "intercept":
            prediction = predict_intercept(fit_intercept(fit, balance, hyperparameter), held)
        else:
            prediction = predict_ridge(fit_ridge(fit, balance, hyperparameter, feature_set), held)
        scores.extend(abs(value - event.observed) for event, value in zip(held, prediction))
    return scores


def empirical_crossfit_interval_report(
    train: Sequence[Event], test: Sequence[Event], predictions: dict[str, Sequence[float]],
    selected: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "status": "empirical-heuristic-no-finite-sample-guarantee",
        "warning": (
            "Pooled event residuals from grouped cross-fits are used only as an empirical "
            "interval heuristic. They do not establish event exchangeability, cluster-level "
            "exchangeability, group-conditional coverage, or a finite-sample conformal guarantee."
        ),
        "methods": {},
    }
    for method_name in ("intercept", "ridgeThermal", "ridgeFull"):
        method = "intercept" if method_name == "intercept" else "ridge"
        feature_set = "thermal" if method_name == "ridgeThermal" else "full"
        output["methods"][method_name] = {}
        balance = selected[method_name]["balance"]
        hyper = selected[method_name]["shrinkage" if method == "intercept" else "alpha"]
        for label, field in (("leaveStationOut", "station_id"), ("leaveStormOut", "storm_id")):
            scores = leave_group_out_scores(train, method, field, balance, hyper, feature_set)
            radius = finite_sample_order_quantile(scores, 0.8)
            values = predictions[method_name]
            covered = [abs(value - event.observed) <= radius for event, value in zip(test, values)]
            output["methods"][method_name][label] = {
                "heuristicCoverageTarget": 0.8, "crossfitResidualCount": len(scores),
                "empiricalRadius": radius, "empiricalTestCoverage": sum(covered) / len(covered),
                "meanIntervalWidth": 2.0 * radius,
            }
    return output


def round_floats(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {key: round_floats(item) for key, item in value.items()}
    if isinstance(value, list):
        return [round_floats(item) for item in value]
    return value


def run(root: Path, output_dir: Path) -> dict[str, Any]:
    datasets: dict[str, tuple[list[Event], list[Event]]] = {}
    provenance: dict[str, Any] = {}
    for name in REGIONS:
        train, test, metadata = load_region(root, name)
        datasets[name] = (train, test)
        provenance[name] = metadata

    # Nested selection uses training partitions only. Fixed holdouts are not read by any
    # candidate scorer and are evaluated only after all shared choices are frozen.
    selection: dict[str, list[dict[str, Any]]] = {
        "intercept": [], "ridgeThermal": [], "ridgeFull": [],
    }
    for balance in BALANCE_MODES:
        for shrinkage in SHRINKAGES:
            cv_by_region = {
                name: grouped_cv_score(datasets[name][0], "intercept", balance, shrinkage)
                for name in SELECTION_REGIONS
            }
            objective = statistics.fmean(cv_by_region[name]["meanFamilyMae"] for name in SELECTION_REGIONS)
            selection["intercept"].append({
                "balance": balance, "shrinkage": shrinkage,
                "macroCvMae": objective, "cvByRegion": cv_by_region,
            })
        for feature_set, key in (("thermal", "ridgeThermal"), ("full", "ridgeFull")):
            for alpha in RIDGE_ALPHAS:
                cv_by_region = {
                    name: grouped_cv_score(datasets[name][0], "ridge", balance, alpha, feature_set)
                    for name in SELECTION_REGIONS
                }
                objective = statistics.fmean(cv_by_region[name]["meanFamilyMae"] for name in SELECTION_REGIONS)
                selection[key].append({
                    "balance": balance, "alpha": alpha, "featureSet": feature_set,
                    "macroCvMae": objective, "cvByRegion": cv_by_region,
                })

    selected_intercept = min(
        selection["intercept"],
        key=lambda row: (row["macroCvMae"], BALANCE_MODES.index(row["balance"]), row["shrinkage"]),
    )
    selected_thermal = min(
        selection["ridgeThermal"],
        key=lambda row: (row["macroCvMae"], BALANCE_MODES.index(row["balance"]), row["alpha"]),
    )
    selected_full = min(
        selection["ridgeFull"],
        key=lambda row: (row["macroCvMae"], BALANCE_MODES.index(row["balance"]), row["alpha"]),
    )
    selected = {
        "intercept": selected_intercept,
        "ridgeThermal": selected_thermal,
        "ridgeFull": selected_full,
    }

    # Freeze the cross-region prior models before touching any fixed holdout.
    global_prior_models = {
        target: fit_log_intercept([
            event for source in SELECTION_REGIONS if source != target
            for event in datasets[source][0]
        ])
        for target in SELECTION_REGIONS
    }
    global_prior_models["northeast"] = fit_log_intercept([
        event for source in SELECTION_REGIONS for event in datasets[source][0]
    ])

    # Single post-selection pass over each fixed holdout. All reported holdout metrics and
    # cached predictions originate here; no candidate grid consults these outcomes.
    holdout_results: dict[str, Any] = {}
    holdout_predictions: dict[str, dict[str, list[float]]] = {}
    fitted_models: dict[str, dict[str, Any]] = {}
    for name in REGIONS:
        train, test = datasets[name]
        intercept_model = fit_intercept(train, selected_intercept["balance"], selected_intercept["shrinkage"])
        thermal_model = fit_ridge(train, selected_thermal["balance"], selected_thermal["alpha"], "thermal")
        full_model = fit_ridge(train, selected_full["balance"], selected_full["alpha"], "full")
        predictions = {
            "baseline": [event.baseline for event in test],
            "intercept": predict_intercept(intercept_model, test),
            "ridgeThermal": predict_ridge(thermal_model, test),
            "ridgeFull": predict_ridge(full_model, test),
            "globalPrior": predict_log_intercept(global_prior_models[name], test),
        }
        holdout_predictions[name] = predictions
        region_index = list(REGIONS).index(name)
        holdout_results[name] = {
            "evaluationCount": 1,
            "metrics": {method: metrics(test, values) for method, values in predictions.items()},
            "slices": fixed_holdout_slice_metrics(name, test, predictions),
            "clusterBootstrap95": {
                "station": cluster_bootstrap(
                    test, predictions, "station_id", seed=20260826 + region_index * 10
                ),
                "storm": cluster_bootstrap(
                    test, predictions, "storm_id", seed=20260827 + region_index * 10
                ),
            },
        }
        fitted_models[name] = {
            "intercept": intercept_model,
            "ridgeThermal": thermal_model,
            "ridgeFull": full_model,
        }

    global_prior: dict[str, Any] = {
        "method": "station-balanced mean log(observed_slr / baseline_slr) on other development-region training events",
        "decision": "no-global-fallback",
        "developmentLeaveOneRegionOut": {},
    }
    for target in SELECTION_REGIONS:
        model = global_prior_models[target]
        target_metrics = holdout_results[target]["metrics"]
        global_prior["developmentLeaveOneRegionOut"][target] = {
            "fitRegions": [name for name in SELECTION_REGIONS if name != target],
            "logOffset": model["logOffset"], "multiplier": model["multiplier"],
            "baseline": target_metrics["baseline"], "globalPrior": target_metrics["globalPrior"],
        }
    northeast_global_model = global_prior_models["northeast"]
    northeast_holdout_metrics = holdout_results["northeast"]["metrics"]
    global_prior["northeastFinal"] = {
        "fitRegions": list(SELECTION_REGIONS),
        "logOffset": northeast_global_model["logOffset"],
        "multiplier": northeast_global_model["multiplier"],
        "baseline": northeast_holdout_metrics["baseline"],
        "globalPrior": northeast_holdout_metrics["globalPrior"],
        "conclusion": "The prior transfers on development-region holdouts but fails on untouched Northeast; do not use a global fallback.",
    }

    northeast_train, northeast_test = datasets["northeast"]
    northeast_predictions = {
        key: value for key, value in holdout_predictions["northeast"].items()
        if key != "globalPrior"
    }
    train_log_summary = log_residual_summary(northeast_train)
    test_log_summary = log_residual_summary(northeast_test)
    hierarchy_counts = provenance["northeast"]["existingEvaluationHierarchyCounts"]
    assert hierarchy_counts == {"global>climate": 62, "global>climate>region": 18}, hierarchy_counts
    northeast_root_cause = {
        "trainLogObservedOverBaseline": train_log_summary,
        "testLogObservedOverBaseline": test_log_summary,
        "existingEvaluationHierarchyCounts": hierarchy_counts,
        "interpretation": (
            "The aggregate log-residual changes sign from training to test. Training-station "
            "means occupy a comparatively narrow negative-to-near-zero range, while held-out "
            "station means span both negative and strongly positive values. The existing "
            "hierarchy therefore applies a near-uniform regional correction with the wrong "
            "aggregate sign and cannot represent held-out-station heterogeneity."
        ),
    }
    northeast = {
        "evaluationPolicy": "single post-selection evaluation after training-only grouped-CV choices were frozen",
        "evaluationCount": 1,
        "metrics": {key: value for key, value in northeast_holdout_metrics.items() if key != "globalPrior"},
        "stationClusterBootstrap": holdout_results["northeast"]["clusterBootstrap95"]["station"],
        "stormClusterBootstrap": holdout_results["northeast"]["clusterBootstrap95"]["storm"],
        "empiricalGroupedCrossfitIntervals80": empirical_crossfit_interval_report(
            northeast_train, northeast_test, northeast_predictions, selected
        ),
        "rootCauseDiagnostic": northeast_root_cause,
        "models": {
            "intercept": {
                "offset": fitted_models["northeast"]["intercept"]["offset"],
                "effectiveSampleSize": fitted_models["northeast"]["intercept"]["effectiveSampleSize"],
            },
            "ridgeThermal": {
                "featureNames": fitted_models["northeast"]["ridgeThermal"]["featureNames"],
                "featureCount": len(fitted_models["northeast"]["ridgeThermal"]["featureNames"]),
                "effectiveSampleSize": fitted_models["northeast"]["ridgeThermal"]["effectiveSampleSize"],
            },
            "ridgeFull": {
                "featureNames": fitted_models["northeast"]["ridgeFull"]["featureNames"],
                "featureCount": len(fitted_models["northeast"]["ridgeFull"]["featureNames"]),
                "effectiveSampleSize": fitted_models["northeast"]["ridgeFull"]["effectiveSampleSize"],
            },
        },
    }

    feature_names = sorted({name for region in datasets.values() for event in region[0] for name in event.features})
    forbidden = [name for name in feature_names if "humidity" in name.lower() or "wind" in name.lower()]
    assert not forbidden, forbidden
    result = {
        "schemaVersion": 2,
        "status": "research-only-unvalidated",
        "productionEnablement": False,
        "artifactProduced": False,
        "purpose": "methodology diagnostic only; no production artifact or runtime modification",
        "determinism": {
            "stdlibOnly": True,
            "bootstrapSeedPolicy": "station=20260826+10*regionIndex; storm=20260827+10*regionIndex in REGIONS order",
            "selectionRegions": list(SELECTION_REGIONS), "untouchedRegion": "northeast",
        },
        "balancingDefinitions": {
            "event": "qc_weight per event",
            "station": "qc_weight divided by the station event count",
            "storm": "qc_weight divided by the storm event count",
            "joint": "qc_weight divided by station event count times storm event count",
            "normalization": "all schemes rescaled to sum to event count",
        },
        "predictors": {
            "allAvailableFeatureNames": feature_names,
            "thermalAblationDefinition": "temperature2m, freezing-level, and pressure-level temperature/geopotential summaries only",
            "fullFeatureDefinition": "thermal profile plus retained precipitation, snowfall, elevation, baseline SLR, SWE proxy, and profile-hour count",
            "note": "Only retained temperature2mC, freezingLevelHeightM, pressure-level temperature/geopotential, precipitationMm, snowfallCm, elevation, baseline SLR and SWE proxy fields are used; no humidity or wind is synthesized.",
        },
        "provenanceAndSplitAssertions": provenance,
        "usLabelProvenanceWarning": inspect_us_label_provenance(root),
        "globalPriorLeaveOneRegionOut": global_prior,
        "trainingGroupedCvSelection": {
            "holdoutIsolation": "fixed regional holdouts were excluded from all candidate scoring",
            "foldFamilies": {
                "unseen_station": "deterministic leave-one-station-out folds",
                "contiguous_storm": "up to five deterministic contiguous chronological blocks of complete storm groups; no storm crosses folds",
            },
            "objective": "equal-weight macro mean across regions; each region score is the equal-weight mean of unseen-station and contiguous-storm event MAE",
            "selected": selected,
            "candidateGridScores": selection,
        },
        "fixedHoldoutEvaluation": {
            "policy": "one post-selection evaluation per fixed regional holdout",
            "results": holdout_results,
        },
        "northeastUntouchedEvaluation": northeast,
        "limitations": [
            "Retrospective diagnostic on four retained regional datasets; not a production validation.",
            "Hyperparameters are shared, but coefficients are fitted separately within each region.",
            "Contiguous storm CV holds out complete chronological storm blocks but is not forward-chaining.",
            "Cluster bootstrap intervals describe sampling variability across the respective observed station or storm clusters within each fixed holdout only.",
            "Grouped-crossfit intervals are empirical heuristics without finite-sample, exchangeability, or group-conditional coverage guarantees.",
            "No artifact is emitted and production calibration remains disabled.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    rounded = round_floats(result)
    (output_dir / "diagnostics.json").write_text(json.dumps(rounded, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(output_dir / "README.md", rounded)
    return rounded


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    selected = report["trainingGroupedCvSelection"]["selected"]
    lines = [
        "# Regional SLR methodology diagnostics", "",
        "> **Research only · unvalidated · no artifact produced · production remains disabled.**", "",
        "This deterministic pure-stdlib diagnostic compares the retained Kuchera baseline, a shrunk residual intercept, a thermal-profile ridge, and a full-available-feature ridge. Shared hyperparameters are selected exclusively inside the Rockies, Sierra, and Swiss training partitions using grouped CV. Every fixed regional holdout is evaluated once only after choices are frozen; Northeast is excluded from all selection.", "",
        "## Exact retained splits", "",
        "| Region | Train constraint | Train events | Test constraint | Test events |",
        "|---|---:|---:|---:|---:|",
    ]
    for name in REGIONS:
        item = report["provenanceAndSplitAssertions"][name]
        temporal = item["temporalConstraint"]
        if temporal["kind"] == "water_year":
            train_constraint, test_constraint = f"WY{temporal['train']}", f"WY{temporal['test']}"
        else:
            train_constraint = " to ".join(temporal["train"]); test_constraint = " to ".join(temporal["test"])
        lines.append(f"| {name.title()} | {train_constraint} + exact stations | {item['trainEventCount']} | {test_constraint} + exact stations | {item['testEventCount']} |")

    provenance_warning = report["usLabelProvenanceWarning"]
    lines += ["", "## U.S. label provenance warning", "",
        f"> **Authorization unresolved.** {provenance_warning['warning']}", "",
        "| Region | Selected labels | Source-flag counts |", "|---|---:|---|",
    ]
    for region, item in provenance_warning["regions"].items():
        counts = ", ".join(f"{key}: {value}" for key, value in item["sourceFlagCounts"].items())
        lines.append(f"| {region.title()} | {item['selectedLabelCount']} | {counts} |")

    lines += ["", "## Training-only grouped-CV selection", "",
        "Candidate scores use no fixed-holdout events. Each region combines deterministic leave-one-station-out CV with up to five contiguous chronological blocks of complete storms; regions and fold families receive equal weight.", "",
        "| Candidate | Balance | Hyperparameter | Macro CV MAE |",
        "|---|---|---:|---:|",
        f"| Shrunk intercept | {selected['intercept']['balance']} | shrinkage {selected['intercept']['shrinkage']} | {selected['intercept']['macroCvMae']:.3f} |",
        f"| Thermal-profile ridge | {selected['ridgeThermal']['balance']} | alpha {selected['ridgeThermal']['alpha']} | {selected['ridgeThermal']['macroCvMae']:.3f} |",
        f"| Full-feature ridge | {selected['ridgeFull']['balance']} | alpha {selected['ridgeFull']['alpha']} | {selected['ridgeFull']['macroCvMae']:.3f} |", "",
        "Full candidate-grid grouped-CV scores are stored separately from fixed-holdout outcomes in `diagnostics.json`.",
    ]

    lines += ["", "## Leave-one-region-out global-prior check", "",
        "A station-balanced log-residual intercept was fitted on the other development-region training sets and applied in the single post-selection holdout pass. Northeast was excluded from every fit and selection decision.", "",
        "| Target | Log offset | Baseline MAE | Global-prior MAE | Baseline RMSE | Global-prior RMSE |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    global_prior = report["globalPriorLeaveOneRegionOut"]
    for region, item in global_prior["developmentLeaveOneRegionOut"].items():
        lines.append(f"| {region.title()} | {item['logOffset']:.4f} | {item['baseline']['mae']:.3f} | {item['globalPrior']['mae']:.3f} | {item['baseline']['rmse']:.3f} | {item['globalPrior']['rmse']:.3f} |")
    item = global_prior["northeastFinal"]
    lines.append(f"| Northeast (final) | {item['logOffset']:.4f} | {item['baseline']['mae']:.3f} | {item['globalPrior']['mae']:.3f} | {item['baseline']['rmse']:.3f} | {item['globalPrior']['rmse']:.3f} |")
    lines += ["", f"**Decision: no global fallback.** {item['conclusion']}", "", "## Fixed-holdout outcomes", "",
        "Each row below comes from the one post-selection evaluation of that regional holdout.", "",
        "| Region | Method | MAE | Bias | RMSE |", "|---|---|---:|---:|---:|",
    ]
    results = report["fixedHoldoutEvaluation"]["results"]
    for region, result in results.items():
        for method in ("baseline", "intercept", "ridgeThermal", "ridgeFull"):
            metric = result["metrics"][method]
            lines.append(f"| {region.title()} | {method} | {metric['mae']:.3f} | {metric['bias']:.3f} | {metric['rmse']:.3f} |")
    lines += ["", "### Improved fixed-holdout slices vs baseline", "",
        "Counts cover station, complete storm, available geographic-slice, and elevation-band slices. Per-slice metrics are in `diagnostics.json`.", "",
        "| Region | Method | Station | Storm | Geographic | Elevation |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for region, result in results.items():
        counts = result["slices"]["improvedSliceCounts"]
        for method in ("intercept", "ridgeThermal", "ridgeFull", "globalPrior"):
            def shown(dimension: str) -> str:
                item = counts.get(dimension, {}).get(method)
                return f"{item['improved']}/{item['total']}" if item else "n/a"
            lines.append(f"| {region.title()} | {method} | {shown('station')} | {shown('storm')} | {shown('geographicSlice')} | {shown('elevationBand')} |")

    ne = report["northeastUntouchedEvaluation"]
    root_cause = ne["rootCauseDiagnostic"]
    train_log = root_cause["trainLogObservedOverBaseline"]
    test_log = root_cause["testLogObservedOverBaseline"]
    held_out_means = ", ".join(f"`{station}` {value:+.3f}" for station, value in test_log["stationMeans"].items())
    hierarchy = root_cause["existingEvaluationHierarchyCounts"]
    lines += ["", "## Northeast root-cause diagnostic", "",
        f"- Train log(obs/baseline): mean `{train_log['mean']:+.4f}`, median `{train_log['median']:+.4f}`; station-mean range `{train_log['stationMeanRange'][0]:+.3f}` to `{train_log['stationMeanRange'][1]:+.3f}`.",
        f"- Test log(obs/baseline): mean `{test_log['mean']:+.4f}`, median `{test_log['median']:+.4f}`; held-out station means: {held_out_means}.",
        f"- Existing evaluation hierarchy paths: `global>climate` for {hierarchy['global>climate']}/80 events and `global>climate>region` for {hierarchy['global>climate>region']}/80.",
        f"- {root_cause['interpretation']}",
        "", "## Northeast uncertainty diagnostics", "",
        "The JSON report also contains deterministic station- and storm-cluster bootstrap 95% intervals for every fixed-holdout method and MAE delta in Rockies, Sierra, Swiss, and Northeast.", "",
    ]
    for cluster in ("stationClusterBootstrap", "stormClusterBootstrap"):
        label = "Station" if cluster.startswith("station") else "Storm"
        lines.append(f"### {label}-cluster bootstrap (95%, {ne[cluster]['repetitions']} replicates)")
        lines.append("")
        for key, interval in ne[cluster]["intervals95"].items():
            lines.append(f"- `{key}`: [{interval[0]:.3f}, {interval[1]:.3f}]")
        lines.append("")

    heuristic = ne["empiricalGroupedCrossfitIntervals80"]
    lines += ["### Empirical grouped-crossfit interval heuristics (80% target)", "",
        f"> **No conformal guarantee.** {heuristic['warning']}", "",
    ]
    for method, groups in heuristic["methods"].items():
        for group, item in groups.items():
            lines.append(f"- `{method}` / `{group}`: empirical radius {item['empiricalRadius']:.3f}, Northeast coverage {item['empiricalTestCoverage']:.3f}, width {item['meanIntervalWidth']:.3f}.")
    lines += ["", "## Predictor discipline", "", report["predictors"]["note"], "",
        f"- Thermal ablation: {report['predictors']['thermalAblationDefinition']}.",
        f"- Full ridge: {report['predictors']['fullFeatureDefinition']}.",
        "", "## Limitations", "",
    ]
    lines.extend(f"- {item}" for item in report["limitations"])
    lines += [
        "",
        "Full candidate-grid CV scores, fixed-holdout outcomes, source hashes, exact station lists, bootstrap intervals, and empirical interval details are in [`diagnostics.json`](diagnostics.json).",
        "The regional failure taxonomy, literature review, and implications are in [`RESEARCH.md`](RESEARCH.md).",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output = args.output_dir or args.repo_root / "data/slr-methodology-diagnostics"
    report = run(args.repo_root.resolve(), output.resolve())
    print(json.dumps({
        "status": report["status"], "output": str(output.resolve()),
        "selected": report["trainingGroupedCvSelection"]["selected"],
        "northeast": report["northeastUntouchedEvaluation"]["metrics"],
    }, indent=2))


if __name__ == "__main__":
    main()
