from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path
from typing import Any

from .tracking_common import Fetcher, fetch_bytes, finite, write_csv_rows
from .tracking_registry import load_registry

NCEI_ACCESS_URL = "https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily/access/{station_id}.csv"

# Intermediate schema consumed by slr_calibration.ghcn.import_ghcn_rockies
# (identical to the existing bootstrap GHCN files).
GHCN_DAILY_COLUMNS = [
    "station_id", "station_name", "state", "latitude", "longitude", "elevation_m",
    "date", "snow_mm", "wesf_mm", "slr", "obs_time", "is_accumulated_snow",
    "is_accumulated_wesf", "qc_weight", "qc_status", "source_flag",
]

# The NCEI access CSV carries no observation-time column.  CoCoRaHS (the bulk
# of the paired registry) observes at 07:00 local by protocol, which is also
# the dominant obs_time in the existing bootstrap pairs; recorded here as an
# explicit assumption rather than hidden metadata.
DEFAULT_OBS_TIME = "0700"


def last_water_years_start(today: date, water_years: int = 2) -> date:
    """Start (Oct 1) of the window covering the last ``water_years`` water years."""
    current_water_year = today.year + 1 if today.month >= 10 else today.year
    return date(current_water_year - water_years, 10, 1)


def _attributes(raw: str | None) -> tuple[str, str, str]:
    parts = [part.strip() for part in (raw or "").split(",")]
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def _format(value: float | None) -> str:
    if value is None:
        return ""
    if value == int(value):
        return f"{value:.1f}"
    return repr(round(value, 4))


def parse_ncei_access_csv(text: str, station_meta: dict[str, Any], start: date) -> list[dict[str, str]]:
    """Convert one station's NCEI access CSV into intermediate-schema rows.

    Unit rules follow the package's existing GHCN conversion: SNOW is already
    millimetres; WESF is reported in tenths of a millimetre and divided by 10
    (see slr_calibration.sierra).  Rows are kept only when SNOW or WESF is
    present and the date is on/after ``start``.
    """
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for record in reader:
        day_text = str(record.get("DATE") or "").strip()
        try:
            day = date.fromisoformat(day_text)
        except ValueError:
            continue
        if day < start:
            continue
        snow_raw = finite(record.get("SNOW"))
        wesf_raw = finite(record.get("WESF"))
        if snow_raw is None and wesf_raw is None:
            continue
        snow_mm = snow_raw
        wesf_mm = wesf_raw / 10.0 if wesf_raw is not None else None
        _, snow_qflag, snow_sflag = _attributes(record.get("SNOW_ATTRIBUTES"))
        _, wesf_qflag, wesf_sflag = _attributes(record.get("WESF_ATTRIBUTES"))
        dapr = finite(record.get("DAPR"))
        mdsf_present = str(record.get("MDSF") or "").strip() not in ("",)
        accumulated_snow = bool((dapr is not None and dapr > 1) or mdsf_present)
        accumulated_wesf = bool(dapr is not None and dapr > 1)
        slr = ""
        if snow_mm is not None and wesf_mm is not None and wesf_mm > 0:
            slr = _format(round(snow_mm / wesf_mm, 6))
        qc_status = "passed" if not snow_qflag and not wesf_qflag else "failed"
        rows.append({
            "station_id": str(station_meta["station_id"]),
            "station_name": str(station_meta.get("station_name") or ""),
            "state": str(station_meta.get("state") or ""),
            "latitude": _format(finite(station_meta.get("latitude"))),
            "longitude": _format(finite(station_meta.get("longitude"))),
            "elevation_m": _format(finite(station_meta.get("elevation_m"))),
            "date": day.isoformat(),
            "snow_mm": _format(snow_mm),
            "wesf_mm": _format(wesf_mm),
            "slr": slr,
            "obs_time": DEFAULT_OBS_TIME,
            "is_accumulated_snow": str(accumulated_snow),
            "is_accumulated_wesf": str(accumulated_wesf),
            "qc_weight": "1.0",
            "qc_status": qc_status,
            "source_flag": wesf_sflag or snow_sflag,
        })
    rows.sort(key=lambda row: row["date"])
    return rows


def ingest_ghcn(
    registry_path: Path,
    output_path: Path,
    fetcher: Fetcher | None = None,
    today: date | None = None,
    water_years: int = 2,
) -> dict[str, Any]:
    fetch = fetcher or fetch_bytes
    today = today or date.today()
    start = last_water_years_start(today, water_years)
    registry = load_registry(registry_path)

    all_rows: list[dict[str, str]] = []
    station_reports: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    seen: set[tuple[str, str]] = set()
    for resort in registry.get("resorts", []):
        for station in resort.get("ghcn_stations", []):
            station_id = str(station["station_id"])
            if station_id in {item["station_id"] for item in station_reports}:
                continue
            url = NCEI_ACCESS_URL.format(station_id=station_id)
            try:
                text = fetch(url).decode("utf-8-sig")
                rows = parse_ncei_access_csv(text, station, start)
            except Exception as exc:  # one bad station must not sink the ingest
                failures[station_id] = f"{type(exc).__name__}: {exc}"
                continue
            deduped = []
            for row in rows:
                key = (row["station_id"], row["date"])
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(row)
            all_rows.extend(deduped)
            station_reports.append({"station_id": station_id, "rowCount": len(deduped)})

    all_rows.sort(key=lambda row: (row["station_id"], row["date"]))
    write_csv_rows(output_path, all_rows, GHCN_DAILY_COLUMNS)
    return {
        "output": str(output_path),
        "windowStart": start.isoformat(),
        "waterYears": water_years,
        "stationCount": len(station_reports),
        "rowCount": len(all_rows),
        "stations": station_reports,
        "failures": failures,
    }
