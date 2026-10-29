"""Surface-pressure (sp) companion cache for the pressure-level model.

The ERA5 profile cache (``gold_profiles_era5``) never fetched surface
pressure: its variable set was z, t, u, v, r on pressure levels plus the
2 m temperature and the accumulated surface fields.  The pressure-level
feature builder (``pressure_features``) needs the *real* ERA5 surface
pressure -- the validity of a fixed pressure level at a mountain station
is decided by comparing the level against the actual surface pressure,
not against an elevation approximation -- so this module fetches it
from the same source of record:

* Bucket ``nsf-ncar-era5`` (anonymous unsigned S3), analysis surface
  files ``e5.oper.an.sfc/{yyyymm}/e5.oper.an.sfc.128_134_sp.ll025sc.
  {yyyymm}0100_{yyyymm}{last}23.nc`` -- one file per variable per MONTH,
  HDF5 variable ``SP``, units Pa, 0.25 deg hourly grid.  Key layout,
  variable name and units verified against a live listing + file read
  on 2026-10-29 (the ``an.sfc`` family is also where 128_167_2t lives;
  ``sp`` is ECMWF param 134).
* Extraction mirrors ``Era5Reader.read_2t`` exactly: nearest 0.25 deg
  grid point to the station, HDF5 range reads via the same
  ``read_points`` chunk batching.  No raw file is written to disk.

CACHE CONTRACT (parallel tree, never touches ``raw_cache_era5``):

* Parts: ``raw_cache_era5_sp/_parts/{yyyymm}/{station_id}.json.gz`` --
  the station's required hours inside that month:
  ``{"time": [...], "surface_pressure": [Pa, ...]}`` (naive UTC ISO
  hour strings, the same convention as the profile chunks).
* Chunks: ``raw_cache_era5_sp/{station_id}/{start}_{end}.json.gz`` --
  assembled with the same chunk plan (``plan_chunks``, ERA5 spec) and
  the same file naming as the profile cache, restricted to the
  modelling pool.  The payload carries exactly the chunk's *required
  hours*: for every event in the chunk, every whole hour from
  floor(window_start + 1h) to ceil(window_end) -- i.e. the window
  sample instants plus the bracketing whole hours the offset builder
  interpolates between.

SCOPE: the fetch covers the modelling pool only -- the 57,376 events
whose v2 profile build status is ``ok``.  Events outside that pool
(the pre-2006 Rockies incompletes, the July-2026 Alaska chunk, the
missing-metadata stations) are out of scope by construction and are
reported, not silently included.
"""

from __future__ import annotations

import calendar
import gzip
import json
import math
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .gold_profiles import ERA5_SPEC, _chunk_path, event_window, load_screened_events, plan_chunks
from .gold_profiles_era5 import BUCKET, Era5Reader, hours_since_1900, read_points

SP_CACHE_DIR_NAME = "raw_cache_era5_sp"
SP_HDF5_NAME = "SP"
SP_CODE = "128_134"
SP_TOKEN = "sp"
_HOUR_FORMAT = "%Y-%m-%dT%H:%M"


# ---------------------------------------------------------------------------
# Object key (verified against live listing, 2026-10-29)
# ---------------------------------------------------------------------------

def sp_key(year: int, month: int) -> str:
    """Bucket key of the monthly an.sfc surface-pressure file."""
    last_day = calendar.monthrange(year, month)[1]
    return (
        f"e5.oper.an.sfc/{year:04d}{month:02d}/e5.oper.an.sfc.{SP_CODE}_{SP_TOKEN}.ll025sc."
        f"{year:04d}{month:02d}0100_{year:04d}{month:02d}{last_day:02d}23.nc"
    )


# ---------------------------------------------------------------------------
# Required hours and planning
# ---------------------------------------------------------------------------

def required_hours(event: dict[str, Any]) -> list[datetime]:
    """Whole UTC hours the builder needs for one event's window.

    The window sample instants are start+1h .. end (see
    ``gold_profiles._accumulate_agl``); sub-hourly valid_times resolve
    each instant through the two bracketing whole-hour records (see
    ``gold_profiles.record_at``), so the required set runs from
    floor(first instant) to ceil(end), inclusive.
    """
    start, end = event_window(event)
    first = (start + timedelta(hours=1)).astimezone(timezone.utc)
    lo = first.replace(minute=0, second=0, microsecond=0)
    end_utc = end.astimezone(timezone.utc)
    hi = end_utc.replace(minute=0, second=0, microsecond=0)
    if hi < end_utc:
        hi += timedelta(hours=1)
    hours: list[datetime] = []
    instant = lo
    while instant <= hi:
        hours.append(instant)
        instant += timedelta(hours=1)
    return hours


def _hour_text(instant: datetime) -> str:
    return instant.astimezone(timezone.utc).strftime(_HOUR_FORMAT)


def _parse_hour(text: str) -> datetime:
    return datetime.strptime(text, _HOUR_FORMAT).replace(tzinfo=timezone.utc)


def plan_sp_chunks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``plan_chunks`` (ERA5 spec) plus each chunk's required hour list."""
    by_id = {event["event_id"]: event for event in events}
    chunks = plan_chunks(events, spec=ERA5_SPEC)
    for chunk in chunks:
        required = {
            _hour_text(hour)
            for event_id in chunk["event_ids"]
            for hour in required_hours(by_id[event_id])
        }
        chunk["required_hours"] = sorted(required)
    return chunks


def month_needs(chunks: list[dict[str, Any]]) -> dict[tuple[int, int], dict[str, list[str]]]:
    """{(year, month): {station_id: [hour texts]}} over all chunks."""
    needs: dict[tuple[int, int], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for chunk in chunks:
        for text in chunk["required_hours"]:
            instant = _parse_hour(text)
            needs[(instant.year, instant.month)][chunk["station_id"]].add(text)
    return {
        key: {station_id: sorted(texts) for station_id, texts in sorted(per_station.items())}
        for key, per_station in sorted(needs.items())
    }


def station_coordinates(events: Iterable[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    coords: dict[str, tuple[float, float]] = {}
    for event in events:
        coords.setdefault(event["station_id"], (event["latitude"], event["longitude"]))
    return coords


# ---------------------------------------------------------------------------
# Reader (mirrors Era5Reader.read_2t for the SP variable)
# ---------------------------------------------------------------------------

class SpReader:
    """Hourly surface-pressure reads from the monthly an.sfc files."""

    def __init__(self, reader: Era5Reader | None = None) -> None:
        self._reader = reader or Era5Reader()

    def read_month(
        self,
        year: int,
        month: int,
        stations: list[tuple[str, float, float]],
        hours_by_station: dict[str, list[datetime]],
    ) -> dict[tuple[str, datetime], float | None]:
        """{(station_id, instant): surface pressure in Pa} for one month."""
        key = sp_key(year, month)
        handle = self._reader._open(key)
        coords = self._reader._coords(key)
        dataset = handle[SP_HDF5_NAME]
        time0 = int(coords["time"][0])
        lat_idx, lon_idx = self._reader._grid_indices(key, stations)
        position = {station_id: i for i, (station_id, _lat, _lon) in enumerate(stations)}
        points: list[tuple[int, ...]] = []
        slots: list[tuple[str, datetime]] = []
        for station_id, hours in hours_by_station.items():
            pos = position[station_id]
            for instant in hours:
                time_index = hours_since_1900(instant) - time0
                if not 0 <= time_index < dataset.shape[0]:
                    continue
                points.append((time_index, lat_idx[pos], lon_idx[pos]))
                slots.append((station_id, instant))
        out: dict[tuple[str, datetime], float | None] = {}
        for slot, value in zip(slots, read_points(dataset, points)):
            out[slot] = value
        return out


# ---------------------------------------------------------------------------
# Parts (per station-month) -- resumable fetch units
# ---------------------------------------------------------------------------

def _part_path(parts_dir: Path, year: int, month: int, station_id: str) -> Path:
    return parts_dir / f"{year:04d}{month:02d}" / f"{station_id}.json.gz"


def _write_gzip_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(tmp, path)


def _part_valid(path: Path, expected_hours: list[str]) -> bool:
    if not path.exists():
        return False
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return False
    if payload.get("time") != expected_hours:
        return False
    values = payload.get("surface_pressure")
    if not isinstance(values, list) or len(values) != len(expected_hours):
        return False
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def fetch_sp_months(
    events: list[dict[str, Any]],
    cache_dir: Path,
    shard_index: int = 0,
    shard_count: int = 1,
    reader: SpReader | None = None,
) -> dict[str, Any]:
    """Fetch this shard's months into per-station-month part files.

    Resumable: a part whose stored hours exactly match the plan and are
    all finite is skipped.  A month that fails after 3 attempts is
    reported in the summary (``monthsFailed``) and left for a rerun.
    """
    chunks = plan_sp_chunks(events)
    needs = month_needs(chunks)
    coords = station_coordinates(events)
    parts_dir = cache_dir / "_parts"
    active = reader or SpReader()
    months = sorted(needs)
    mine = [key for i, key in enumerate(months) if i % shard_count == shard_index]
    summary: dict[str, Any] = {
        "shardIndex": shard_index,
        "shardCount": shard_count,
        "monthsPlanned": [f"{y:04d}{m:02d}" for y, m in mine],
        "monthsDone": [],
        "monthsFailed": [],
        "partsWritten": 0,
        "partsSkippedValid": 0,
        "pointsRead": 0,
    }
    for year, month in mine:
        expected = needs[(year, month)]
        todo = {
            station_id: hours
            for station_id, hours in expected.items()
            if not _part_valid(_part_path(parts_dir, year, month, station_id), hours)
        }
        summary["partsSkippedValid"] += len(expected) - len(todo)
        if not todo:
            summary["monthsDone"].append(f"{year:04d}{month:02d}")
            continue
        stations = [(sid, coords[sid][0], coords[sid][1]) for sid in sorted(todo)]
        hours_dt = {sid: [_parse_hour(text) for text in hours] for sid, hours in todo.items()}
        values: dict[tuple[str, datetime], float | None] | None = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                values = active.read_month(year, month, stations, hours_dt)
                break
            except Exception as exc:  # retried: S3 throttling / transient I/O
                last_error = exc
                values = None
                time.sleep(5.0 * (attempt + 1) ** 2)
        if values is None:
            summary["monthsFailed"].append(
                {"month": f"{year:04d}{month:02d}", "error": f"{type(last_error).__name__}: {last_error}"}
            )
            continue
        for station_id, hours in sorted(todo.items()):
            payload = {
                "time": hours,
                "surface_pressure": [
                    round(values[(station_id, _parse_hour(text))], 1)
                    if values.get((station_id, _parse_hour(text))) is not None
                    else None
                    for text in hours
                ],
            }
            _write_gzip_json(_part_path(parts_dir, year, month, station_id), payload)
            summary["partsWritten"] += 1
            summary["pointsRead"] += len(hours)
        summary["monthsDone"].append(f"{year:04d}{month:02d}")
    return summary


# ---------------------------------------------------------------------------
# Assembly: parts -> chunk files + per-event coverage
# ---------------------------------------------------------------------------

def load_station_parts(parts_dir: Path, station_id: str) -> dict[datetime, float]:
    """All fetched sp hours for one station: {instant (UTC): Pa}."""
    hours: dict[datetime, float] = {}
    if not parts_dir.exists():
        return hours
    for month_dir in sorted(parts_dir.iterdir()):
        path = month_dir / f"{station_id}.json.gz"
        if not path.exists():
            continue
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        for text, value in zip(payload["time"], payload["surface_pressure"]):
            if value is not None:
                hours[_parse_hour(text)] = float(value)
    return hours


def assemble_sp_chunks(events: list[dict[str, Any]], cache_dir: Path) -> dict[str, Any]:
    """Assemble chunk files from parts and compute per-event coverage.

    A chunk is written only when every required hour is present and
    finite.  Coverage is computed per event (an event is covered when
    all of its own required hours are present), so a failed chunk does
    not misreport the events inside it.
    """
    chunks = plan_sp_chunks(events)
    parts_dir = cache_dir / "_parts"
    by_id = {event["event_id"]: event for event in events}
    station_hours: dict[str, dict[datetime, float]] = {}
    def hours_for(station_id: str) -> dict[datetime, float]:
        if station_id not in station_hours:
            station_hours[station_id] = load_station_parts(parts_dir, station_id)
        return station_hours[station_id]

    covered: list[str] = []
    missing: list[dict[str, Any]] = []
    for event in events:
        available = hours_for(event["station_id"])
        absent = [text for text in (_hour_text(h) for h in required_hours(event)) if _parse_hour(text) not in available]
        if absent:
            missing.append({"event_id": event["event_id"], "missingHours": len(absent)})
        else:
            covered.append(event["event_id"])

    chunks_written = 0
    chunks_failed: list[dict[str, Any]] = []
    for chunk in chunks:
        available = hours_for(chunk["station_id"])
        absent = [text for text in chunk["required_hours"] if _parse_hour(text) not in available]
        path = _chunk_path(cache_dir, chunk)
        if absent:
            chunks_failed.append({"key": chunk["key"], "missingHours": len(absent)})
            continue
        payload = {
            "hourly": {
                "time": chunk["required_hours"],
                "surface_pressure": [round(available[_parse_hour(text)], 1) for text in chunk["required_hours"]],
            },
            "variable": "sp",
            "units": "Pa",
            "profile_source": ERA5_SPEC.source_id,
            "profile_model": "ERA5",
            "station_id": chunk["station_id"],
            "event_ids": chunk["event_ids"],
        }
        _write_gzip_json(path, payload)
        chunks_written += 1
    return {
        "eventsTotal": len(events),
        "eventsCovered": len(covered),
        "eventsMissing": missing,
        "chunksTotal": len(chunks),
        "chunksWritten": chunks_written,
        "chunksFailed": chunks_failed,
    }


def load_sp_hours(cache_dir: Path, station_id: str) -> dict[datetime, float]:
    """Assembled sp chunk files for one station: {instant (UTC): Pa}."""
    hours: dict[datetime, float] = {}
    station_dir = cache_dir / station_id
    if not station_dir.exists():
        return hours
    for path in sorted(station_dir.glob("*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        hourly = payload["hourly"]
        for text, value in zip(hourly["time"], hourly["surface_pressure"]):
            if value is not None:
                hours[_parse_hour(text)] = float(value)
    return hours


# ---------------------------------------------------------------------------
# Pool selection + run entry points
# ---------------------------------------------------------------------------

def load_modelling_events(pool_dir: Path, features_parquet: Path) -> list[dict[str, Any]]:
    """Screened events whose v2 build status is ``ok`` (the modelling pool)."""
    import pyarrow.parquet as pq

    table = pq.read_table(features_parquet, columns=["event_id", "profile_status"])
    ok_ids = {
        event_id
        for event_id, status in zip(table.column("event_id").to_pylist(), table.column("profile_status").to_pylist())
        if status == "ok"
    }
    return [event for event in load_screened_events(pool_dir) if event["event_id"] in ok_ids]


def run_fetch_sp(
    pool_dir: Path,
    output_dir: Path,
    shard_index: int = 0,
    shard_count: int = 1,
    assemble_only: bool = False,
    no_assemble: bool = False,
) -> dict[str, Any]:
    """Fetch sp parts for this shard, then (single-shard or on request) assemble.

    ``output_dir`` is the model-v1 directory; the sp cache lives beside
    the profile cache at ``output_dir/raw_cache_era5_sp`` and the pool
    is read from ``output_dir/era5-expanded-v2/features.parquet``.
    """
    events = load_modelling_events(pool_dir, output_dir / "era5-expanded-v2" / "features.parquet")
    cache_dir = output_dir / SP_CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"modellingEvents": len(events), "cacheDir": str(cache_dir)}
    if not assemble_only:
        summary["fetch"] = fetch_sp_months(
            events, cache_dir, shard_index=shard_index, shard_count=shard_count
        )
    if assemble_only or (not no_assemble and shard_count == 1):
        summary["coverage"] = assemble_sp_chunks(events, cache_dir)
        (cache_dir / "sp_coverage.json").write_text(
            json.dumps(summary["coverage"], indent=2, sort_keys=True), encoding="utf-8"
        )
    (cache_dir / f"sp_fetch_summary_shard{shard_index}.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary
