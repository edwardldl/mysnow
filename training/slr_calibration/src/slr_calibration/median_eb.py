"""Median variant of the hierarchical log-residual EB trainer.

Queue item 3 (median experiment M; see
data/.../model-v3-median/DESIGN.md).  ``train_artifact_median`` is
``trainer.train_artifact`` with exactly one change: each hierarchy
node's statistic is the **weighted median** of
``residual - parent_prediction`` in place of the weighted mean in
``trainer._fit_node``.  Everything else — row QC via
``qc.parse_event``, the dedupe check, ESS, the shrinkage
``ESS / (ESS + prior ESS)``, the hierarchy
global → climate regime → H3 region cell (res 4) → site, the climate
assignments, the artifact schema, and the residual-std construction
(which does not enter the point prediction) — is the trainer's,
reused or mirrored line for line so the two artifacts differ only in
the node statistic and the recorded ``modelKind``.

The weighted median convention: sort by value; take the value at
which cumulative weight first reaches half the total weight; if
cumulative weight equals exactly half at a point, average that value
with the next.  With unit weights this reduces exactly to
``numpy.median`` (the convention of gold_model._median_eb_corrections).
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Callable

import h3
import numpy as np

from .io import canonical_rows
from .qc import StormEvent, parse_event
from .trainer import (
    TrainingConfig,
    WeightedResidual,
    _digest_rows,
    _effective_sample_size,
    _group,
    _weighted_mean,
    _weighted_std,
)

MEDIAN_MODEL_KIND = "hierarchical-log-residual-median-eb"


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    """Weighted median as defined in this module's docstring."""
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    if len(values) == 0:
        return 0.0
    order = np.argsort(values, kind="stable")
    sorted_values = values[order]
    cumulative = np.cumsum(weights[order])
    total = float(cumulative[-1])
    if total == 0:
        return 0.0
    half = 0.5 * total
    idx = int(np.searchsorted(cumulative, half, side="left"))
    if cumulative[idx] == half and idx + 1 < len(sorted_values):
        return float(0.5 * (sorted_values[idx] + sorted_values[idx + 1]))
    return float(sorted_values[idx])


def _fit_node_median(
    records: list[WeightedResidual],
    parent_prediction: Callable[[WeightedResidual], float],
    config: TrainingConfig,
) -> dict[str, Any]:
    """trainer._fit_node with a weighted-median node statistic."""
    raw = np.array(
        [record.residual - parent_prediction(record) for record in records],
        dtype=float,
    )
    weights = np.array([record.weight for record in records], dtype=float)
    raw_stat = weighted_median(raw, weights)
    effective = _effective_sample_size(records)
    shrinkage = effective / (effective + config.prior_effective_sample_size)
    log_bias = raw_stat * shrinkage
    final_residuals = lambda record: record.residual - parent_prediction(record) - log_bias
    final_mean = _weighted_mean(records, final_residuals)
    residual_std = max(0.05, _weighted_std(records, final_residuals, final_mean))
    return {
        "logBias": round(log_bias, 8),
        "rawLogBias": round(raw_stat, 8),
        "residualStdLog": round(residual_std, 8),
        "rawSampleCount": len(records),
        "effectiveSampleSize": round(effective, 4),
        "shrinkageWeight": round(shrinkage, 8),
    }


def train_artifact_median(rows: list[dict[str, Any]], config: TrainingConfig) -> dict[str, Any]:
    """trainer.train_artifact with median hierarchy nodes.

    Identical control flow to the mean trainer; see the module
    docstring for the single deliberate difference.
    """
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
        global_node = _fit_node_median(residuals, lambda _: 0.0, config)
        global_prediction = lambda _: float(global_node["logBias"])

        climate_nodes: dict[str, Any] = {}
        for regime, group_records in _group(residuals, lambda record: record.event.climate_regime).items():
            climate_nodes[regime] = _fit_node_median(group_records, global_prediction, config)

        def climate_prediction(record: WeightedResidual) -> float:
            node = climate_nodes.get(record.event.climate_regime)
            return global_prediction(record) + (float(node["logBias"]) if node else 0.0)

        region_nodes: dict[str, Any] = {}
        for cell, group_records in _group(residuals, lambda record: h3.latlng_to_cell(record.event.latitude, record.event.longitude, config.region_resolution)).items():
            region_nodes[cell] = _fit_node_median(group_records, climate_prediction, config)

        def region_prediction(record: WeightedResidual) -> float:
            cell = h3.latlng_to_cell(record.event.latitude, record.event.longitude, config.region_resolution)
            node = region_nodes.get(cell)
            return climate_prediction(record) + (float(node["logBias"]) if node else 0.0)

        local_nodes: dict[str, Any] = {}
        local_records = [record for record in residuals if record.event.site_id]
        for site_id, group_records in _group(local_records, lambda record: record.event.site_id or "").items():
            local_nodes[site_id] = _fit_node_median(group_records, region_prediction, config)

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
        "modelKind": MEDIAN_MODEL_KIND,
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
            "leakageGroup": config.leakage_group,
            "target": "log(observed_slr / physical_baseline_slr)",
        },
        "coverage": {
            "qualityTier": "unvalidated" if config.status == "unvalidated" else "limited",
            "geographicNote": config.geographic_note,
            "supportedMethods": sorted(methods),
        },
        "methods": methods,
    }
