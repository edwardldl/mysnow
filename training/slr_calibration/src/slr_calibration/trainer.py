from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable

import h3

from .io import canonical_rows
from .qc import StormEvent, parse_event


@dataclass(frozen=True)
class TrainingConfig:
    artifact_id: str
    artifact_version: str = "unvalidated-v1"
    status: str = "unvalidated"
    region_resolution: int = 4
    climate_resolution: int = 2
    prior_effective_sample_size: float = 30.0
    min_effective_sample_size: float = 5.0
    min_correction_factor: float = 0.5
    max_correction_factor: float = 2.0
    min_slr: float = 3.0
    max_slr: float = 30.0
    source_label: str = "normalized storm-event observations"


@dataclass(frozen=True)
class WeightedResidual:
    event: StormEvent
    residual: float
    weight: float


def _effective_sample_size(records: list[WeightedResidual]) -> float:
    total = sum(record.weight for record in records)
    squared = sum(record.weight * record.weight for record in records)
    return 0.0 if squared == 0 else total * total / squared


def _weighted_mean(records: list[WeightedResidual], value: Callable[[WeightedResidual], float]) -> float:
    total = sum(record.weight for record in records)
    return 0.0 if total == 0 else sum(record.weight * value(record) for record in records) / total


def _weighted_std(records: list[WeightedResidual], value: Callable[[WeightedResidual], float], mean: float) -> float:
    total = sum(record.weight for record in records)
    if total == 0:
        return 0.0
    variance = sum(record.weight * (value(record) - mean) ** 2 for record in records) / total
    return math.sqrt(max(variance, 0.0))


def _fit_node(records: list[WeightedResidual], parent_prediction: Callable[[WeightedResidual], float], config: TrainingConfig) -> dict[str, Any]:
    raw_values = lambda record: record.residual - parent_prediction(record)
    raw_mean = _weighted_mean(records, raw_values)
    effective = _effective_sample_size(records)
    shrinkage = effective / (effective + config.prior_effective_sample_size)
    log_bias = raw_mean * shrinkage
    final_residuals = lambda record: record.residual - parent_prediction(record) - log_bias
    final_mean = _weighted_mean(records, final_residuals)
    residual_std = max(0.05, _weighted_std(records, final_residuals, final_mean))
    return {
        "logBias": round(log_bias, 8),
        "rawLogBias": round(raw_mean, 8),
        "residualStdLog": round(residual_std, 8),
        "rawSampleCount": len(records),
        "effectiveSampleSize": round(effective, 4),
        "shrinkageWeight": round(shrinkage, 8),
    }


def _group(records: list[WeightedResidual], key: Callable[[WeightedResidual], str]) -> dict[str, list[WeightedResidual]]:
    grouped: dict[str, list[WeightedResidual]] = defaultdict(list)
    for record in records:
        grouped[key(record)].append(record)
    return dict(sorted(grouped.items()))


def _digest_rows(rows: Iterable[dict[str, Any]]) -> str:
    payload = json.dumps(canonical_rows(rows), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def train_artifact(rows: list[dict[str, Any]], config: TrainingConfig) -> dict[str, Any]:
    if config.status not in {"unvalidated", "validated", "production"}:
        raise ValueError("status must be unvalidated, validated, or production")
    events: list[StormEvent] = []
    rejected_rows = 0
    rejection_counts: Counter[str] = Counter()
    for row in canonical_rows(rows):
        event, issues = parse_event(row)
        if event is None:
            rejected_rows += 1
            rejection_counts.update(issue.code for issue in issues)
        else:
            events.append(event)
    if not events:
        raise ValueError("No QC-passed storm events remain")

    dedupe_key = lambda event: (event.event_id, event.station_id, event.baseline_method)
    if len({dedupe_key(event) for event in events}) != len(events):
        raise ValueError("Duplicate event_id/station_id/baseline_method records")

    methods: dict[str, Any] = {}
    for method in sorted({event.baseline_method for event in events}):
        residuals = [WeightedResidual(event, math.log(event.observed_slr / event.baseline_slr), event.qc_weight) for event in events if event.baseline_method == method]
        global_node = _fit_node(residuals, lambda _: 0.0, config)
        global_prediction = lambda _: float(global_node["logBias"])

        climate_nodes: dict[str, Any] = {}
        for regime, group_records in _group(residuals, lambda record: record.event.climate_regime).items():
            climate_nodes[regime] = _fit_node(group_records, global_prediction, config)

        def climate_prediction(record: WeightedResidual) -> float:
            node = climate_nodes.get(record.event.climate_regime)
            return global_prediction(record) + (float(node["logBias"]) if node else 0.0)

        region_nodes: dict[str, Any] = {}
        for cell, group_records in _group(residuals, lambda record: h3.latlng_to_cell(record.event.latitude, record.event.longitude, config.region_resolution)).items():
            region_nodes[cell] = _fit_node(group_records, climate_prediction, config)

        def region_prediction(record: WeightedResidual) -> float:
            cell = h3.latlng_to_cell(record.event.latitude, record.event.longitude, config.region_resolution)
            node = region_nodes.get(cell)
            return climate_prediction(record) + (float(node["logBias"]) if node else 0.0)

        local_nodes: dict[str, Any] = {}
        local_records = [record for record in residuals if record.event.site_id]
        for site_id, group_records in _group(local_records, lambda record: record.event.site_id or "").items():
            local_nodes[site_id] = _fit_node(group_records, region_prediction, config)

        methods[method] = {
            "global": global_node,
            "climate": climate_nodes,
            "region": region_nodes,
            "local": local_nodes,
        }

    assignments: dict[str, str] = {}
    by_climate_cell: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        cell = h3.latlng_to_cell(event.latitude, event.longitude, config.climate_resolution)
        by_climate_cell[cell][event.climate_regime] += 1
    for cell, regimes in sorted(by_climate_cell.items()):
        assignments[cell] = sorted(regimes.items(), key=lambda item: (-item[1], item[0]))[0][0]

    trained_through = max(event.trained_date for event in events)
    stations = {event.station_id for event in events}
    return {
        "schemaVersion": 1,
        "artifactId": config.artifact_id,
        "artifactVersion": config.artifact_version,
        "status": config.status,
        "generatedAt": f"{trained_through}T00:00:00Z",
        "trainedThrough": trained_through,
        "modelKind": "hierarchical-log-residual-eb",
        "regionIndex": {"system": "h3", "resolution": config.region_resolution},
        "climateIndex": {"system": "h3", "resolution": config.climate_resolution, "assignments": assignments},
        "bounds": {
            "minCorrectionFactor": config.min_correction_factor,
            "maxCorrectionFactor": config.max_correction_factor,
            "minSlr": config.min_slr,
            "maxSlr": config.max_slr,
        },
        "training": {
            "sourceLabel": config.source_label,
            "sourceDigestSha256": _digest_rows(rows),
            "acceptedEventCount": len(events),
            "rejectedEventCount": rejected_rows,
            "rejectionCounts": dict(sorted(rejection_counts.items())),
            "stationCount": len(stations),
            "priorEffectiveSampleSize": config.prior_effective_sample_size,
            "minEffectiveSampleSize": config.min_effective_sample_size,
            "leakageGroup": "event_id",
            "target": "log(observed_slr / physical_baseline_slr)",
        },
        "coverage": {
            "qualityTier": "unvalidated" if config.status == "unvalidated" else "limited",
            "geographicNote": "Coverage is explicit by hierarchy; absent cells fall back to a broader level.",
            "supportedMethods": sorted(methods),
        },
        "methods": methods,
    }
