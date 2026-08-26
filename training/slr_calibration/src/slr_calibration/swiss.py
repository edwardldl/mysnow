from __future__ import annotations

import csv
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SwissImportConfig:
    min_new_snow_cm: float = 2.5
    min_fresh_swe_mm: float = 2.5
    min_observed_slr: float = 2.0
    max_observed_slr: float = 50.0


@dataclass(frozen=True)
class SwissRejectedRow:
    timestamp: str
    station_id: str
    reason: str
    hn_cm: str
    hnw_mm: str


def lv03_to_wgs84(easting_m: float, northing_m: float) -> tuple[float, float]:
    """Official swisstopo polynomial approximation for CH1903/LV03 to WGS84."""
    y = (easting_m - 600_000.0) / 1_000_000.0
    x = (northing_m - 200_000.0) / 1_000_000.0
    latitude = (
        16.9023892 + 3.238272 * x - 0.270978 * y**2 - 0.002528 * x**2
        - 0.0447 * y**2 * x - 0.0140 * x**3
    ) * 100.0 / 36.0
    longitude = (
        2.6779094 + 4.728982 * y + 0.791484 * y * x + 0.1306 * y * x**2
        - 0.0436 * y**3
    ) * 100.0 / 36.0
    return latitude, longitude


def _read_observation_table(path: Path, expected_description: str, expected_units: str) -> tuple[list[str], list[dict[str, str]]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if len(lines) < 10:
        raise ValueError(f"{path.name} is too short")
    attributes: dict[str, str] = {}
    for line in lines[:8]:
        if ":" in line:
            key, value = line.split(":", 1)
            attributes[key.strip()] = value.strip()
    if attributes.get("Description") != expected_description:
        raise ValueError(f"{path.name} description is not measured {expected_description!r}")
    if attributes.get("Units") != expected_units:
        raise ValueError(f"{path.name} units must be {expected_units}")
    rows = list(csv.DictReader(lines[8:]))
    stations = list(rows[0])[1:] if rows else []
    return stations, rows


def _read_stations(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"Station", "Easting (m)", "Northing (m)", "Altitude (m.a.s.l.)"}
    if not rows or required - set(rows[0]):
        raise ValueError("STATION-LIST.txt has an unexpected schema")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        easting = float(row["Easting (m)"])
        northing = float(row["Northing (m)"])
        elevation = float(row["Altitude (m.a.s.l.)"])
        latitude, longitude = lv03_to_wgs84(easting, northing)
        result[row["Station"]] = {
            "easting_m": easting,
            "northing_m": northing,
            "elevation_m": elevation,
            "latitude": latitude,
            "longitude": longitude,
        }
    return result


def _finite(value: str) -> float | None:
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _assign_storm_groups(rows: list[dict[str, Any]]) -> None:
    dates = sorted({date.fromisoformat(str(row["observation_date"])) for row in rows})
    groups: dict[date, str] = {}
    start: date | None = None
    previous: date | None = None
    for value in dates:
        if previous is None or (value - previous).days > 1:
            start = value
        assert start is not None
        groups[value] = f"swiss-alps-{start.isoformat()}"
        previous = value
    for row in rows:
        row["storm_group_id"] = groups[date.fromisoformat(str(row["observation_date"]))]


def import_swiss_new_snow(
    input_dir: Path,
    config: SwissImportConfig = SwissImportConfig(),
) -> tuple[list[dict[str, Any]], list[SwissRejectedRow], dict[str, Any]]:
    hn_stations, hn_rows = _read_observation_table(input_dir / "HN" / "OBS-HN.txt", "new snow depth, observed", "cm")
    hnw_stations, hnw_rows = _read_observation_table(input_dir / "HNW" / "OBS-HNW.txt", "new snow water equivalent, observed", "mm")
    stations = _read_stations(input_dir / "STATION-LIST.txt")
    if hn_stations != hnw_stations or set(hn_stations) != set(stations):
        raise ValueError("HN, HNW, and station metadata station sets/order do not match")
    if len(hn_rows) != len(hnw_rows):
        raise ValueError("HN and HNW tables have different row counts")

    accepted: list[dict[str, Any]] = []
    rejected: list[SwissRejectedRow] = []
    rejection_counts: Counter[str] = Counter()
    for hn_row, hnw_row in zip(hn_rows, hnw_rows, strict=True):
        timestamp = hn_row["Time"]
        if timestamp != hnw_row["Time"]:
            raise ValueError("HN and HNW timestamps are not exactly aligned")
        try:
            observed_at = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            raise ValueError(f"Invalid timestamp {timestamp}") from exc
        for station_id in hn_stations:
            hn_text, hnw_text = hn_row[station_id], hnw_row[station_id]
            hn, hnw = _finite(hn_text), _finite(hnw_text)
            reason: str | None = None
            if hn is None:
                reason = "HN_INVALID"
            elif hn == -9999 and hnw == -9999:
                reason = "BOTH_MISSING"
            elif hn == -9999:
                reason = "HN_MISSING"
            elif hnw is None:
                reason = "HNW_INVALID"
            elif hnw == -9999:
                reason = "HNW_MISSING"
            elif hn <= 0:
                reason = "HN_NONPOSITIVE"
            elif hnw <= 0:
                reason = "HNW_NONPOSITIVE"
            elif hn < config.min_new_snow_cm:
                reason = "HN_BELOW_MINIMUM"
            elif hnw < config.min_fresh_swe_mm:
                reason = "HNW_BELOW_MINIMUM"
            else:
                observed_slr = hn * 10.0 / hnw
                if not config.min_observed_slr <= observed_slr <= config.max_observed_slr:
                    reason = "SLR_OUTSIDE_PLAUSIBLE_RANGE"
            if reason:
                rejection_counts[reason] += 1
                rejected.append(SwissRejectedRow(timestamp, station_id, reason, hn_text, hnw_text))
                continue
            assert hn is not None and hnw is not None
            meta = stations[station_id]
            observed_slr = hn * 10.0 / hnw
            observation_date = observed_at.date().isoformat()
            accepted.append({
                "event_id": f"envidat590-{station_id}-{observation_date}",
                "storm_group_id": "",
                "station_id": station_id,
                "site_id": station_id,
                "station_name": station_id,
                "state": "CH",
                "observation_date": observation_date,
                "valid_time": f"{observation_date}T06:00:00+01:00",
                "latitude": round(meta["latitude"], 6),
                "longitude": round(meta["longitude"], 6),
                "elevation_m": meta["elevation_m"],
                "new_snow_cm": hn,
                "fresh_swe_mm": hnw,
                "observed_slr": round(observed_slr, 6),
                "baseline_method": "kuchera",
                "climate_regime": "alpine",
                "qc_passed": True,
                "qc_weight": 1.0,
                "qc_status": "source_quality_controlled_and_pair_qc_passed",
                "source_quality_tier": "official_quality_controlled_measured_daily_pair",
                "source_license": "CC BY 4.0",
                "source_doi": "10.16904/envidat.590",
                "source_version": "1.0",
                "source_window": "daily fresh-snow observation ending 06:00 CET (UTC+01:00)",
                "source_measurement": "independently observed HN and HNW; no HS/SWE differencing or modeled estimate",
            })
    _assign_storm_groups(accepted)
    dates = [row["observation_date"] for row in accepted]
    report = {
        "source": {
            "title": "Dataset on new snow water equivalent",
            "publisher": "WSL Institute for Snow and Avalanche Research SLF / EnviDat",
            "doi": "10.16904/envidat.590",
            "version": "1.0",
            "license": "CC BY 4.0",
            "qualityTier": "official quality-controlled paired manual observations",
            "measurementBasis": "HN (cm) and HNW (mm) are separately observed daily fresh quantities with the same timestamps; HS and SWE files are not read",
            "coordinateSystem": "CH1903/LV03 (EPSG:21781), converted with swisstopo approximation",
            "timeBasis": "README states all timestamps are CET UTC+01:00 and fresh-snow timestamps mark end of observation period",
        },
        "inputTimestampCount": len(hn_rows),
        "inputStationCount": len(hn_stations),
        "candidatePairCount": len(hn_rows) * len(hn_stations),
        "acceptedEventCount": len(accepted),
        "rejectedPairCount": len(rejected),
        "rejectionCounts": dict(sorted(rejection_counts.items())),
        "acceptedStationCount": len({row["station_id"] for row in accepted}),
        "acceptedDateRange": [min(dates), max(dates)] if dates else None,
        "thresholds": asdict(config),
        "stationMetadata": {
            station_id: {key: round(value, 6) for key, value in meta.items()}
            for station_id, meta in sorted(stations.items())
        },
    }
    return accepted, rejected, report


def write_swiss_import(
    events: list[dict[str, Any]],
    rejected: list[SwissRejectedRow],
    report: dict[str, Any],
    events_path: Path,
    rejections_path: Path,
    report_path: Path,
) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    if not events:
        raise ValueError("No Swiss events passed QC")
    with events_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(events[0]))
        writer.writeheader()
        writer.writerows(events)
    rejections_path.parent.mkdir(parents=True, exist_ok=True)
    with rejections_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rejected[0])) if rejected else ["timestamp", "station_id", "reason", "hn_cm", "hnw_mm"])
        writer.writeheader()
        writer.writerows(asdict(row) for row in rejected)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
