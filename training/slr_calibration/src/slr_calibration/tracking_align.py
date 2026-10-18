from __future__ import annotations

import gzip
import json
import math
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .forecast_snapshot import read_index
from .profiles import _response_hours, backfill_openmeteo_multistation_profiles
from .tracking_common import haversine_km, read_csv_rows, write_csv_rows, write_json
from .tracking_events import EVENT_COLUMNS
from .tracking_registry import load_registry

# Trainer-ready schema: identical to
# data/slr-training-data/normalized/baseline/ghcn_rockies_kuchera.csv plus a
# trailing ``baseline_source`` column recording which forecast source fed
# each row (local snapshot vs historical-forecast backfill).
BASELINE_COLUMNS = [
    "event_id", "storm_group_id", "station_id", "site_id", "station_name", "state",
    "valid_time", "latitude", "longitude", "elevation_m", "observed_slr",
    "baseline_slr", "baseline_method", "climate_regime", "qc_passed", "qc_weight",
    "evaluated_profile_hours", "model_frozen_swe_proxy_weight_mm", "profile_source",
    "profile_model", "profile_alignment", "source_license", "source_doi",
    "baseline_source",
]

SNAPSHOT_MODEL_PREFERENCE = ("ecmwf_ifs", "gfs_seamless")


def _js_round(value: float, digits: int) -> float:
    """JavaScript Math.round semantics (half up) at the given precision."""
    factor = 10 ** digits
    return math.floor(value * factor + 0.5) / factor


def _interval_mean(first: float | None, second: float | None) -> float | None:
    values = [value for value in (first, second) if value is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _representative_layers(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[tuple[float | None, float | None]]:
    """Interval-representative (geopotentialHeightM, temperatureC) per level.

    Mirrors src/lib/snow/atmosphere/interval.ts intervalRepresentativeLayers
    followed by src/lib/snow/profile.ts normalizeProfile range checks.
    """
    by_pressure: dict[float, dict[str, dict[str, Any]]] = {}
    for layer in previous:
        by_pressure.setdefault(float(layer["pressureHpa"]), {})["previous"] = layer
    for layer in current:
        by_pressure.setdefault(float(layer["pressureHpa"]), {})["current"] = layer
    representative: list[tuple[float | None, float | None]] = []
    for _pressure, pair in sorted(by_pressure.items(), key=lambda item: -item[0]):
        prev = pair.get("previous") or {}
        curr = pair.get("current") or {}
        height = _interval_mean(prev.get("geopotentialHeightM"), curr.get("geopotentialHeightM"))
        temperature = _interval_mean(prev.get("temperatureC"), curr.get("temperatureC"))
        if height is not None and not (-500.0 <= height <= 30000.0):
            height = None
        if temperature is not None and not (-100.0 <= temperature <= 50.0):
            temperature = None
        representative.append((height, temperature))
    return representative


def kuchera_slr(levels: list[tuple[float | None, float | None]], station_elevation_m: float, surface_temperature_c: float | None) -> float:
    """Exact replication of src/lib/snow/slr/kuchera.ts estimateKucheraSlr.

    Calibration is disabled in the research baseline path, so the physical
    Kuchera value is the baseline (see training/slr_calibration/scripts/
    cssl-baseline.ts, which calls estimateFreshSnowSlr with enabled: false).
    """
    temperatures = [
        temperature
        for height, temperature in levels
        if height is not None and height >= station_elevation_m - 100.0 and temperature is not None
    ]
    maximum = max(temperatures) if temperatures else surface_temperature_c
    if maximum is None:
        return 10.0
    max_k = maximum + 273.15
    pivot_k = 271.16
    slr = 12.0 + 2.0 * (pivot_k - max_k) if max_k > pivot_k else 12.0 + (pivot_k - max_k)
    return _js_round(max(3.0, min(30.0, slr)), 1)


def compute_baseline_slr(
    hours: list[dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
    station_elevation_m: float,
) -> tuple[float, int, float] | None:
    """Precipitation-weighted Kuchera baseline over a (start, end] window.

    Mirrors evaluateResearchKucheraBaseline in cssl-baseline.ts.  ``hours``
    uses the profiles.py hour-dict shape.  Returns (baseline_slr,
    evaluated_hours, total_weight) or None when no valid interval exists.
    """
    ordered = sorted(hours, key=lambda hour: str(hour["time"]))
    evaluated: list[tuple[float, float]] = []
    for index in range(1, len(ordered)):
        previous = ordered[index - 1]
        current = ordered[index]
        current_time = datetime.fromisoformat(str(current["time"]).replace("Z", "+00:00"))
        if not (window_start < current_time <= window_end):
            continue
        snowfall = current.get("snowfallCm")
        if snowfall is None or snowfall <= 0:
            continue
        precipitation = current.get("precipitationMm")
        weight = precipitation if precipitation is not None and precipitation > 0 else snowfall
        layers = _representative_layers(previous.get("levels") or [], current.get("levels") or [])
        above_ground = sum(
            1 for height, _ in layers
            if height is not None and height >= station_elevation_m - 100.0
        )
        if above_ground < 1:
            continue
        surface = _interval_mean(previous.get("temperature2mC"), current.get("temperature2mC"))
        slr = kuchera_slr(layers, station_elevation_m, surface)
        evaluated.append((slr, float(weight)))
    if not evaluated:
        return None
    total_weight = sum(weight for _, weight in evaluated)
    if total_weight <= 0:
        return None
    baseline = sum(slr * weight for slr, weight in evaluated) / total_weight
    return _js_round(baseline, 6), len(evaluated), _js_round(total_weight, 6)


def _parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _snapshot_candidate(
    event: dict[str, str],
    resort: dict[str, Any],
    snapshots_dir: Path,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any] | None:
    """Freshest covering snapshot for an event, or None.

    Leakage rule: the snapshot issue time must precede the event end, and the
    payload must contain every hourly endpoint of the event window.
    """
    index_rows = read_index(snapshots_dir / "index.jsonl")
    station_lat = float(event["latitude"])
    station_lon = float(event["longitude"])
    station_elev = float(event["elevation_m"])

    def point_rank(point: dict[str, Any]) -> tuple[float, float]:
        distance = haversine_km(station_lat, station_lon, float(point["latitude"]), float(point["longitude"]))
        return (distance, abs(float(point["elevation_m"]) - station_elev))

    points = sorted(resort.get("forecast_points", []), key=point_rank)
    expected_endpoints = [window_start + timedelta(hours=offset) for offset in range(0, int((window_end - window_start).total_seconds() // 3600) + 1)]
    for point in points:
        for model in SNAPSHOT_MODEL_PREFERENCE:
            candidates = [
                row for row in index_rows
                if row.get("point_id") == point["point_id"] and row.get("model") == model
                and _parse_instant(str(row.get("issued_at_utc"))) < window_end
            ]
            candidates.sort(key=lambda row: str(row.get("issued_at_utc")), reverse=True)
            for row in candidates:
                payload_path = snapshots_dir / str(row.get("path"))
                if not payload_path.exists():
                    continue
                try:
                    payload = json.loads(gzip.decompress(payload_path.read_bytes()).decode("utf-8"))
                    hours = _response_hours(payload)
                except Exception:
                    continue
                by_time = {_parse_instant(str(hour["time"])): hour for hour in hours}
                if all(endpoint in by_time for endpoint in expected_endpoints):
                    return {
                        "point": point,
                        "model": model,
                        "issued_at_utc": row.get("issued_at_utc"),
                        "hours": [by_time[endpoint] for endpoint in sorted(by_time) if window_start - timedelta(hours=1) <= endpoint <= window_end],
                    }
    return None


def _baseline_row(event: dict[str, str], computed: tuple[float, int, float], profile_fields: dict[str, str], baseline_source: str) -> dict[str, Any]:
    baseline_slr, evaluated_hours, total_weight = computed
    return {
        "event_id": event["event_id"],
        "storm_group_id": event["storm_group_id"],
        "station_id": event["station_id"],
        "site_id": event["site_id"],
        "station_name": event["station_name"],
        "state": event["state"],
        "valid_time": event["valid_time"],
        "latitude": event["latitude"],
        "longitude": event["longitude"],
        "elevation_m": event["elevation_m"],
        "observed_slr": event["observed_slr"],
        "baseline_slr": baseline_slr,
        "baseline_method": "kuchera",
        "climate_regime": event["climate_regime"],
        "qc_passed": event["qc_passed"],
        "qc_weight": event["qc_weight"],
        "evaluated_profile_hours": evaluated_hours,
        "model_frozen_swe_proxy_weight_mm": total_weight,
        "profile_source": profile_fields["profile_source"],
        "profile_model": profile_fields["profile_model"],
        "profile_alignment": profile_fields["profile_alignment"],
        "source_license": event["source_license"],
        "source_doi": "",
        "baseline_source": baseline_source,
    }


def align_events(
    events_path: Path,
    registry_path: Path,
    snapshots_dir: Path,
    output_path: Path,
    report_path: Path,
    cache_dir: Path,
    sleep_seconds: float = 0.25,
) -> dict[str, Any]:
    events = read_csv_rows(events_path)
    registry = load_registry(registry_path)
    resorts_by_station: dict[str, dict[str, Any]] = {}
    for resort in registry.get("resorts", []):
        station = resort.get("snotel_station")
        if station:
            resorts_by_station[str(station["station_triplet"])] = resort
        for ghcn_station in resort.get("ghcn_stations", []):
            resorts_by_station[str(ghcn_station["station_id"])] = resort

    rows: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    rejection_counts: Counter[str] = Counter()
    pending_backfill: list[dict[str, str]] = []

    for event in events:
        end = _parse_instant(str(event["valid_time"]))
        window_hours = int(float(event.get("observation_window_hours") or 24))
        start = end - timedelta(hours=window_hours)
        resort = resorts_by_station.get(str(event["station_id"]))
        candidate = _snapshot_candidate(event, resort, snapshots_dir, start, end) if resort else None
        if candidate is not None:
            computed = compute_baseline_slr(candidate["hours"], start, end, float(event["elevation_m"]))
            if computed is not None:
                point = candidate["point"]
                rows.append(_baseline_row(event, computed, {
                    "profile_source": "MySnow forecast snapshot (Open-Meteo Forecast API)",
                    "profile_model": str(candidate["model"]),
                    "profile_alignment": (
                        f"snapshot {point['point_id']} issued {candidate['issued_at_utc']} (before event end); "
                        "production (t-1,t] interval-mean profile; model-precipitation weighted Kuchera; tracking"
                    ),
                }, f"snapshot:{point['point_id']}:{candidate['model']}"))
                source_counts["snapshot"] += 1
                continue
            rejection_counts["SNAPSHOT_NO_VALID_KUCHERA_INTERVALS"] += 1
        pending_backfill.append(event)

    backfill_report: dict[str, Any] | None = None
    if pending_backfill:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            staging = tmp_path / "events.csv"
            write_csv_rows(staging, pending_backfill, EVENT_COLUMNS)
            jsonl_path = tmp_path / "profiles.jsonl"
            backfill_report_path = tmp_path / "backfill-report.json"
            start_date = min(str(event["observation_date"]) for event in pending_backfill)
            end_date = max(str(event["observation_date"]) for event in pending_backfill)
            try:
                backfill_report = backfill_openmeteo_multistation_profiles(
                    staging,
                    cache_dir,
                    jsonl_path,
                    backfill_report_path,
                    datetime.fromisoformat(start_date).date(),
                    datetime.fromisoformat(end_date).date(),
                    None,
                    sleep_seconds,
                    profile_alignment_label="MySnow tracking 24-hour window ending at reported local observation time",
                )
            except ValueError:
                backfill_report = None
            if backfill_report is not None and jsonl_path.exists():
                jsonl_event_ids: set[str] = set()
                for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    jsonl_event_ids.add(str(record["event_id"]))
                    event = next(item for item in pending_backfill if item["event_id"] == record["event_id"])
                    window_start = _parse_instant(str(record["windowStartUtc"]))
                    window_end = _parse_instant(str(record["windowEndUtc"]))
                    computed = compute_baseline_slr(record["hourly"], window_start, window_end, float(event["elevation_m"]))
                    if computed is None:
                        rejection_counts["BACKFILL_NO_VALID_KUCHERA_INTERVALS"] += 1
                        continue
                    rows.append(_baseline_row(event, computed, {
                        "profile_source": str(record.get("profileSource") or "Open-Meteo Historical Forecast API"),
                        "profile_model": str(record.get("profileModel") or ""),
                        "profile_alignment": str(record.get("profileAlignment") or ""),
                    }, "open-meteo-historical-forecast"))
                    source_counts["open-meteo-historical-forecast"] += 1
                # Events the profile backfill itself rejected never reach the
                # JSONL; their detailed reasons live in backfillReport.
                for event in pending_backfill:
                    if str(event["event_id"]) not in jsonl_event_ids:
                        rejection_counts["BACKFILL_PROFILE_REJECTED"] += 1

    rows.sort(key=lambda row: str(row["event_id"]))
    # qc_passed arrives as "true"/"false" text; trainer rows keep that form.
    write_csv_rows(output_path, rows, BASELINE_COLUMNS)
    report = {
        "eventsPath": str(events_path),
        "outputPath": str(output_path),
        "inputEventCount": len(events),
        "alignedRowCount": len(rows),
        "sourceCounts": dict(sorted(source_counts.items())),
        "rejectionCounts": dict(sorted(rejection_counts.items())),
        "backfillReport": backfill_report,
        "leakageRule": "snapshot issue time must precede event end; otherwise the historical-forecast archive backfill is used",
    }
    write_json(report_path, report)
    return report
