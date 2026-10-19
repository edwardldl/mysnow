"""Adapter: data/hrrr-dataset (NOAA HRRR + SNOTEL) -> normalized storm events.

Design reference: mysnow-regional-slr-predictor-design.md section 3.3.

Emits the trainer's normalized event schema from the daily Parquet
partitions, carrying the modeling extras (forecast features, observed SWE /
depth changes, region assignment) alongside the normalized columns so the
regional evaluation can consume one file.  Only QC-passed station-days in
water years 2024-2026 are emitted (WY2027 is a partial season and becomes
forward shadow data per design section 9).  Every emitted row is flagged
``trainer_eligible`` when it satisfies the existing trainer QC bounds
(observed SLR in (2, 50), baseline SLR in [3, 30]); the trainer's own
``parse_event`` remains the final arbiter.

Storm groups follow the ``ghcn.py::_assign_storm_groups`` pattern --
contiguous calendar-date runs -- applied per station within each water
year (design section 3.3: "contiguous-date grouping per station").  The
per-water-year scope guarantees a group can never straddle the
water-year holdout boundary.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .qc import parse_event
from .regions import load_station_regimes

SOURCE_LABEL = "snotel-differenced-noaa-hrrr"
WATER_YEARS = (2024, 2025, 2026)

EVENT_FIELDS = [
    "event_id", "storm_group_id", "station_id", "site_id", "station_name", "state",
    "valid_time", "observation_date", "latitude", "longitude", "elevation_m",
    "observed_slr", "baseline_slr", "baseline_method", "climate_regime",
    "qc_passed", "qc_weight", "source", "forecast_source", "label_tier",
    "region_id", "range_name", "water_year",
    # modeling extras (not part of the normalized trainer schema)
    "fcst_precip_mm", "fcst_snowfall_cm", "fcst_t2m_mean_c", "fcst_t2m_min_c",
    "fcst_t2m_max_c", "delta_swe_mm", "delta_depth_mm",
    "hrrr_lead_min_h", "hrrr_lead_max_h", "data_tz_offset_hours",
    "trainer_eligible",
]

_STRING_FIELDS = {
    "event_id", "storm_group_id", "station_id", "site_id", "station_name", "state",
    "valid_time", "observation_date", "baseline_method", "climate_regime", "source",
    "forecast_source", "label_tier", "region_id", "range_name",
}
_BOOL_FIELDS = {"qc_passed", "trainer_eligible"}
_INT_FIELDS = {"water_year", "hrrr_lead_min_h", "hrrr_lead_max_h"}


def _storm_groups(rows: list[dict[str, Any]]) -> None:
    """Assign contiguous-date storm groups per (station, water year), in place."""
    by_station_year: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_station_year[(row["station_id"], int(row["water_year"]))].append(row)
    for (station_id, _wy), group_rows in by_station_year.items():
        group_rows.sort(key=lambda row: row["observation_date"])
        start: date | None = None
        previous: date | None = None
        for row in group_rows:
            current = date.fromisoformat(row["observation_date"])
            if previous is None or (current - previous) > timedelta(days=1):
                start = current
            assert start is not None
            row["storm_group_id"] = f"sg-{station_id}-{start.isoformat()}"
            previous = current


def _output_schema() -> pa.Schema:
    fields = []
    for field in EVENT_FIELDS:
        if field in _STRING_FIELDS:
            fields.append((field, pa.string()))
        elif field in _BOOL_FIELDS:
            fields.append((field, pa.bool_()))
        elif field in _INT_FIELDS:
            fields.append((field, pa.int64()))
        else:
            fields.append((field, pa.float64()))
    return pa.schema(fields)


def adapt_hrrr_events(
    dataset_dir: Path,
    regimes_path: Path,
    output_parquet: Path,
    output_csv: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    regimes = load_station_regimes(regimes_path)
    raw_rows: list[dict[str, Any]] = []
    partition_counts: Counter[str] = Counter()
    for water_year in WATER_YEARS:
        partition = dataset_dir / "daily" / f"water_year={water_year}" / "data.parquet"
        if not partition.exists():
            continue
        table = pq.read_table(partition)
        for row in table.to_pylist():
            partition_counts[f"water_year={water_year}"] += 1
            if row["weak_qc_passed"]:
                raw_rows.append(row)

    events: list[dict[str, Any]] = []
    missing_regime: Counter[str] = Counter()
    for row in raw_rows:
        triplet = str(row["station_triplet"])
        assignment = regimes.get(triplet)
        if assignment is None:
            missing_regime[triplet] += 1
            continue
        date_text = str(row["date"])[:10]
        events.append({
            "event_id": f"{triplet}-{date_text}",
            "storm_group_id": "",  # filled by _storm_groups
            "station_id": triplet,
            "site_id": triplet,
            "station_name": str(row.get("station_name") or ""),
            "state": str(row.get("state") or ""),
            "valid_time": f"{date_text}T00:00:00Z",
            "observation_date": date_text,
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "elevation_m": row["elevation_m"],
            "observed_slr": row["observed_slr_diff"],
            "baseline_slr": row["fcst_kuchera_slr"],
            "baseline_method": "kuchera",
            "climate_regime": assignment["climate_regime"],
            "qc_passed": True,
            "qc_weight": row["weak_qc_weight"],
            "source": SOURCE_LABEL,
            "forecast_source": str(row.get("hrrr_source") or "noaa-hrrr-00z"),
            "label_tier": "weak",
            "region_id": assignment["region_id"],
            "range_name": assignment["range_name"],
            "water_year": int(row["water_year"]),
            "fcst_precip_mm": row["fcst_precip_mm"],
            "fcst_snowfall_cm": row["fcst_snowfall_cm"],
            "fcst_t2m_mean_c": row["fcst_t2m_mean_c"],
            "fcst_t2m_min_c": row["fcst_t2m_min_c"],
            "fcst_t2m_max_c": row["fcst_t2m_max_c"],
            "delta_swe_mm": row["delta_swe_mm"],
            "delta_depth_mm": row["delta_depth_mm"],
            "hrrr_lead_min_h": row["hrrr_lead_min_h"],
            "hrrr_lead_max_h": row["hrrr_lead_max_h"],
            "data_tz_offset_hours": row["data_tz_offset_hours"],
            "trainer_eligible": False,
        })
    if missing_regime:
        raise ValueError(f"Stations missing from regime map: {dict(missing_regime)}")

    _storm_groups(events)

    eligible = 0
    rejection_counts: Counter[str] = Counter()
    for event in events:
        parsed, issues = parse_event(event)
        event["trainer_eligible"] = parsed is not None
        if parsed is not None:
            eligible += 1
        else:
            rejection_counts.update(issue.code for issue in issues)

    events.sort(key=lambda row: (row["station_id"], row["observation_date"]))
    output_parquet.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(events, schema=_output_schema()), output_parquet)
    if output_csv is not None:
        with output_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=EVENT_FIELDS)
            writer.writeheader()
            writer.writerows(events)

    by_year: Counter[str] = Counter()
    by_state: Counter[str] = Counter()
    by_region: Counter[str] = Counter()
    for event in events:
        if event["trainer_eligible"]:
            by_year[str(event["water_year"])] += 1
            by_state[event["state"]] += 1
            by_region[event["region_id"] or "(none)"] += 1
    report = {
        "source": str(dataset_dir),
        "regimeMap": str(regimes_path),
        "partitionRowCounts": dict(sorted(partition_counts.items())),
        "qcPassedRows": len(raw_rows),
        "emittedEvents": len(events),
        "trainerEligibleEvents": eligible,
        "trainerRejectionCounts": dict(sorted(rejection_counts.items())),
        "eligibleByWaterYear": dict(sorted(by_year.items())),
        "eligibleByState": dict(sorted(by_state.items())),
        "eligibleByRegion": dict(sorted(by_region.items())),
        "stormGroupCount": len({event["storm_group_id"] for event in events if event["trainer_eligible"]}),
        "stationCount": len({event["station_id"] for event in events if event["trainer_eligible"]}),
        "sourceLabel": SOURCE_LABEL,
        "labelTier": "weak (SNOTEL pack differencing, qc_weight 0.25)",
        "note": "Baseline is the Python-replicated Kuchera over NOAA HRRR profiles at fixed 00Z leads 1-24 h; not interchangeable with the previous Open-Meteo/TS-Kuchera slices.",
    }
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
