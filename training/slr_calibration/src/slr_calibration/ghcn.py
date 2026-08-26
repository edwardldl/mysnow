from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

MOUNTAIN_LOCAL_TIME = ZoneInfo("America/Denver")
ROCKIES_STATES = frozenset({"CO", "WY", "MT", "UT", "ID", "NM"})


@dataclass(frozen=True)
class GhcnRockiesConfig:
    min_elevation_m: float = 1200.0
    min_snow_mm: float = 25.0
    min_wesf_mm: float = 2.5
    min_observed_slr: float = 2.0
    max_observed_slr: float = 50.0


@dataclass(frozen=True)
class GhcnRejectedRow:
    row_number: int
    station_id: str
    observation_date: str
    reason: str
    detail: str


def _finite(value: Any) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_date(value: str) -> date | None:
    text = value.strip()
    for pattern in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    return None


def _parse_obs_time(value: str) -> tuple[int, int] | None:
    text = value.strip().zfill(4)
    if len(text) != 4 or not text.isdigit():
        return None
    hour, minute = int(text[:2]), int(text[2:])
    if hour == 24 and minute == 0:
        return 0, 0
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def is_rockies_station(state: str, latitude: float, longitude: float, elevation_m: float, minimum_elevation_m: float = 1200.0) -> bool:
    """Explicit reproducible U.S. Rockies/foothill study mask.

    The state/elevation/bounds mask intentionally excludes low-elevation Idaho,
    eastern Colorado plains, eastern Montana plains, and southern New Mexico.
    It is a research domain, not a claim that political boundaries define the
    physiographic Rocky Mountains.
    """
    if state not in ROCKIES_STATES or elevation_m < minimum_elevation_m:
        return False
    if state == "CO":
        return longitude <= -104.5
    if state == "WY":
        return longitude <= -104.5
    if state == "MT":
        return longitude <= -108.0
    if state == "UT":
        return longitude >= -113.5
    if state == "ID":
        return longitude >= -116.5
    if state == "NM":
        return latitude >= 35.5 and -107.5 <= longitude <= -104.5
    return False


def _assign_storm_groups(rows: list[dict[str, Any]]) -> None:
    unique_dates = sorted({date.fromisoformat(str(row["observation_date"])) for row in rows})
    group_by_date: dict[date, str] = {}
    start: date | None = None
    previous: date | None = None
    for value in unique_dates:
        if previous is None or (value - previous).days > 1:
            start = value
        assert start is not None
        group_by_date[value] = f"rockies-{start.isoformat()}"
        previous = value
    for row in rows:
        row["storm_group_id"] = group_by_date[date.fromisoformat(str(row["observation_date"]))]


def import_ghcn_rockies(
    input_path: Path,
    config: GhcnRockiesConfig = GhcnRockiesConfig(),
) -> tuple[list[dict[str, Any]], list[GhcnRejectedRow], dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[GhcnRejectedRow] = []
    input_rows = 0

    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "station_id", "station_name", "state", "latitude", "longitude", "elevation_m",
            "date", "snow_mm", "wesf_mm", "slr", "obs_time", "is_accumulated_snow",
            "is_accumulated_wesf", "qc_weight", "qc_status",
        }
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"{input_path.name} missing columns: {', '.join(missing)}")
        for row_number, row in enumerate(reader, start=2):
            input_rows += 1
            station_id = str(row.get("station_id") or "").strip()
            date_text = str(row.get("date") or "").strip()
            observation_date = _parse_date(date_text)
            latitude = _finite(row.get("latitude"))
            longitude = _finite(row.get("longitude"))
            elevation_m = _finite(row.get("elevation_m"))
            state = str(row.get("state") or "").strip().upper()

            def reject(reason: str, detail: str) -> None:
                rejected.append(GhcnRejectedRow(row_number, station_id, date_text, reason, detail))

            if not station_id or observation_date is None or latitude is None or longitude is None or elevation_m is None:
                reject("INVALID_METADATA", "station/date/coordinate/elevation is missing or invalid")
                continue
            if not is_rockies_station(state, latitude, longitude, elevation_m, config.min_elevation_m):
                reject("OUTSIDE_ROCKIES_DOMAIN", f"state={state}; lat={latitude}; lon={longitude}; elev_m={elevation_m}")
                continue
            if str(row.get("qc_status") or "").strip().lower() != "passed":
                reject("SOURCE_QC_FAILED", str(row.get("qc_status") or ""))
                continue
            if str(row.get("is_accumulated_snow") or "").strip().lower() == "true" or str(row.get("is_accumulated_wesf") or "").strip().lower() == "true":
                reject("ACCUMULATED_INTERVAL", "SNOW or WESF spans multiple days")
                continue
            snow_mm = _finite(row.get("snow_mm"))
            wesf_mm = _finite(row.get("wesf_mm"))
            observed_slr = _finite(row.get("slr"))
            qc_weight = _finite(row.get("qc_weight"))
            if snow_mm is None or wesf_mm is None or observed_slr is None or qc_weight is None:
                reject("INVALID_DIRECT_PAIR", "SNOW/WESF/SLR/qc_weight missing or non-finite")
                continue
            if snow_mm < config.min_snow_mm:
                reject("INSUFFICIENT_NEW_SNOW", f"snow_mm={snow_mm}")
                continue
            if wesf_mm < config.min_wesf_mm:
                reject("INSUFFICIENT_FRESH_SWE", f"wesf_mm={wesf_mm}")
                continue
            computed_slr = snow_mm / wesf_mm
            if abs(computed_slr - observed_slr) > 0.02:
                reject("SLR_MISMATCH", f"published={observed_slr}; computed={computed_slr}")
                continue
            if not (config.min_observed_slr < computed_slr < config.max_observed_slr):
                reject("SLR_OUT_OF_RANGE", f"slr={computed_slr}")
                continue
            obs_time = _parse_obs_time(str(row.get("obs_time") or ""))
            if obs_time is None:
                reject("INVALID_OBSERVATION_TIME", str(row.get("obs_time") or ""))
                continue
            hour, minute = obs_time
            valid_local = datetime(observation_date.year, observation_date.month, observation_date.day, hour, minute, tzinfo=MOUNTAIN_LOCAL_TIME)
            accepted.append({
                "event_id": f"ghcn:{station_id}:{observation_date.isoformat()}",
                "storm_group_id": "pending",
                "station_id": station_id,
                "site_id": station_id,
                "station_name": str(row.get("station_name") or "").strip(),
                "state": state,
                "observation_date": observation_date.isoformat(),
                "valid_time": valid_local.isoformat(),
                "observation_time_local_standard": f"{hour:02d}:{minute:02d}",
                "observation_window_hours": 24,
                "latitude": latitude,
                "longitude": longitude,
                "elevation_m": elevation_m,
                "fresh_snow_mm": snow_mm,
                "fresh_swe_mm": wesf_mm,
                "observed_slr": round(computed_slr, 6),
                "baseline_method": "kuchera",
                "climate_regime": "continental_mountain",
                "qc_passed": True,
                "qc_weight": qc_weight,
                "source": "NOAA GHCN-Daily direct SNOW+WESF",
                "source_license": "NOAA/NCEI public data; station-source attribution retained",
                "source_flag": str(row.get("source_flag") or "").strip(),
            })

    accepted.sort(key=lambda row: (str(row["observation_date"]), str(row["station_id"])))
    _assign_storm_groups(accepted)
    counts = Counter(item.reason for item in rejected)
    by_state: dict[str, dict[str, Any]] = {}
    state_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in accepted:
        state_rows[str(row["state"])].append(row)
    for state, rows in sorted(state_rows.items()):
        by_state[state] = {
            "eventCount": len(rows),
            "stationCount": len({row["station_id"] for row in rows}),
            "startDate": min(str(row["observation_date"]) for row in rows),
            "endDate": max(str(row["observation_date"]) for row in rows),
        }
    report = {
        "source": str(input_path),
        "inputRowCount": input_rows,
        "acceptedEventCount": len(accepted),
        "rejectedRowCount": len(rejected),
        "rejectionCounts": dict(sorted(counts.items())),
        "stationCount": len({row["station_id"] for row in accepted}),
        "stormGroupCount": len({row["storm_group_id"] for row in accepted}),
        "startDate": min((str(row["observation_date"]) for row in accepted), default=None),
        "endDate": max((str(row["observation_date"]) for row in accepted), default=None),
        "byState": by_state,
        "domain": {
            "statesConsidered": sorted(ROCKIES_STATES),
            "minimumElevationM": config.min_elevation_m,
            "bounds": {
                "CO": "longitude <= -104.5",
                "WY": "longitude <= -104.5",
                "MT": "longitude <= -108.0",
                "UT": "longitude >= -113.5",
                "ID": "longitude >= -116.5",
                "NM": "latitude >= 35.5 and -107.5 <= longitude <= -104.5",
            },
            "note": "Explicit research mask for elevated Rockies/foothill stations; not an official physiographic polygon.",
        },
        "label": "same-date GHCN-Daily SNOW / WESF only; no SNWD/WESD or total-pack differencing",
        "timezone": "GHCN observation time interpreted as America/Denver local station clock time, including DST.",
    }
    return accepted, rejected, report


def write_ghcn_import(
    events: list[dict[str, Any]],
    rejected: list[GhcnRejectedRow],
    report: dict[str, Any],
    events_path: Path,
    rejections_path: Path,
    report_path: Path,
) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    if events:
        with events_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(events[0]))
            writer.writeheader()
            writer.writerows(events)
    rejections_path.parent.mkdir(parents=True, exist_ok=True)
    with rejections_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[field.name for field in GhcnRejectedRow.__dataclass_fields__.values()])
        writer.writeheader()
        writer.writerows(asdict(row) for row in rejected)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
