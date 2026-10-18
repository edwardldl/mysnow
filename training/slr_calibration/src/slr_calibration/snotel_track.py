from __future__ import annotations

import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from .tracking_common import finite, format_number, read_csv_rows, write_csv_rows
from .tracking_registry import load_registry

AWDB_DATA_URL = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data"
ELEMENTS = ("WTEQ", "SNWD", "PREC", "TAVG", "TMAX", "TMIN")
CHUNK_DAYS = 92

# Same columns as data/slr-training-data/normalized/observations/
# snotel_daily_observations.csv.  NOTE: these are total-pack measurements
# (snow water equivalent and depth of the whole pack), never fresh snow.
SNOTEL_CSV_COLUMNS = [
    "station_triplet", "station_name", "state", "latitude", "longitude", "elevation_m",
    "date", "swe_inches", "snow_depth_inches", "precip_accum_inches",
    "tavg_f", "tmax_f", "tmin_f",
]
_ELEMENT_TO_COLUMN = {
    "WTEQ": "swe_inches",
    "SNWD": "snow_depth_inches",
    "PREC": "precip_accum_inches",
    "TAVG": "tavg_f",
    "TMAX": "tmax_f",
    "TMIN": "tmin_f",
}


def awdb_data_url(triplets: list[str], start: date, end: date) -> str:
    query = urllib.parse.urlencode({
        "stationTriplets": ",".join(triplets),
        "elements": ",".join(ELEMENTS),
        "beginDate": start.isoformat(),
        "endDate": end.isoformat(),
        "duration": "DAILY",
    })
    return f"{AWDB_DATA_URL}?{query}"


def _convert_value(element: str, value: float, stored_unit: str) -> float:
    """Normalize AWDB values to the CSV's inches / degrees-F convention."""
    if element in {"TAVG", "TMAX", "TMIN"} and stored_unit == "degC":
        return value * 9.0 / 5.0 + 32.0
    return value


def parse_awdb_data(payload: Any, station_meta: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """Flatten an AWDB /data payload into SNOTEL daily CSV rows."""
    rows_by_key: dict[tuple[str, str], dict[str, str]] = {}
    if not isinstance(payload, list):
        raise ValueError("AWDB data payload is not a list")
    for station_block in payload:
        triplet = str(station_block.get("stationTriplet") or "")
        meta = station_meta.get(triplet)
        if meta is None:
            continue
        for element_block in station_block.get("data") or []:
            element_info = element_block.get("stationElement") or {}
            element = str(element_info.get("elementCode") or "")
            column = _ELEMENT_TO_COLUMN.get(element)
            if column is None:
                continue
            stored_unit = str(element_info.get("storedUnitCode") or "")
            for item in element_block.get("values") or []:
                day = str(item.get("date") or "")[:10]
                value = finite(item.get("value"))
                if not day:
                    continue
                key = (triplet, day)
                row = rows_by_key.setdefault(key, {
                    "station_triplet": triplet,
                    "station_name": str(meta.get("station_name") or ""),
                    "state": str(meta.get("state") or ""),
                    "latitude": format_number(finite(meta.get("latitude"))),
                    "longitude": format_number(finite(meta.get("longitude"))),
                    "elevation_m": format_number(finite(meta.get("elevation_m"))),
                    "date": day,
                    **{column_name: "" for column_name in SNOTEL_CSV_COLUMNS[7:]},
                })
                row[column] = "" if value is None else format_number(_convert_value(element, value, stored_unit))
    return [rows_by_key[key] for key in sorted(rows_by_key)]


def merge_snotel_rows(existing: list[dict[str, str]], new_rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Append-dedupe by (station_triplet, date); fresh values win on conflict."""
    merged: dict[tuple[str, str], dict[str, str]] = {}
    for row in existing:
        merged[(row.get("station_triplet", ""), row.get("date", ""))] = row
    added = 0
    updated = 0
    for row in new_rows:
        key = (row.get("station_triplet", ""), row.get("date", ""))
        if key in merged:
            if merged[key] != row:
                updated += 1
        else:
            added += 1
        merged[key] = row
    ordered = [merged[key] for key in sorted(merged)]
    return ordered, {"added": added, "updated": updated, "total": len(ordered)}


def registry_station_meta(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    meta: dict[str, dict[str, Any]] = {}
    for resort in registry.get("resorts", []):
        station = resort.get("snotel_station")
        if station:
            meta[str(station["station_triplet"])] = station
    return meta


def ingest_snotel(
    registry_path: Path,
    output_path: Path,
    start: date,
    end: date,
    json_fetcher: Callable[[str], Any],
) -> dict[str, Any]:
    registry = load_registry(registry_path)
    station_meta = registry_station_meta(registry)
    triplets = sorted(station_meta)
    if not triplets:
        raise ValueError("Registry has no SNOTEL stations attached")

    fetched_rows: list[dict[str, str]] = []
    requests: list[dict[str, Any]] = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end)
        url = awdb_data_url(triplets, chunk_start, chunk_end)
        payload = json_fetcher(url)
        rows = parse_awdb_data(payload, station_meta)
        fetched_rows.extend(rows)
        requests.append({"start": chunk_start.isoformat(), "end": chunk_end.isoformat(), "rowCount": len(rows)})
        chunk_start = chunk_end + timedelta(days=1)

    existing = read_csv_rows(output_path)
    merged, counts = merge_snotel_rows(existing, fetched_rows)
    write_csv_rows(output_path, merged, SNOTEL_CSV_COLUMNS)
    return {
        "output": str(output_path),
        "stationCount": len(triplets),
        "stations": triplets,
        "requestedRange": [start.isoformat(), end.isoformat()],
        "fetchedRowCount": len(fetched_rows),
        "requests": requests,
        **counts,
    }
