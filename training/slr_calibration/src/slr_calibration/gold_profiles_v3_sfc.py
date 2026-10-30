"""Phase 3 companion fetch: single-level surface fields (2d, tclw, 10u, 10v, skt).

The Phase 3 physics feature rebuild needs five single-level fields the
ERA5 profile cache never retained:

* ``2d`` (2 m dewpoint, K) -- direct surface wet-bulb and sub-cloud
  saturation-deficit signal (near 0 degC the wet-bulb is the melting
  referee), instead of deriving surface moisture from the lowest
  profile level.
* ``tclw`` (total column liquid water, kg/m^2) -- the column-integral
  riming-environment proxy.  The level-resolved ``clwc`` failed the
  ECMWF-derivability gate (see ``gold_profiles_v3``); its column
  integral is the servable single-level alternative.
* ``10u`` / ``10v`` (10 m wind, m/s) -- surface wind: dendrite
  stripping and blowing/redistribution where the snow lands and is
  measured.  The profile cache's wind starts at 975 hPa; the 10 m
  value is a different physical quantity.
* ``skt`` (skin temperature, K) -- the ground temperature the snow
  lands on (post-landing melt, Cobb-style melting treatment).

CACHE AUDIT (before fetching, 2026-10-30): all 39,429 profile-cache
chunk files were read (``model-v3-features/cache_key_audit_sfc.json``):
56 distinct hourly keys -- the 13-level t/r/ws/z profiles,
``precipitation``, ``snowfall``, ``temperature_2m`` and ``time``.
NONE of the five target fields is present in any chunk (0 / 39,429),
so all five are fetched.  (Same audit-first pattern as the sp fetch
and the v3 accumulations audit, which found tp/sf fully held and only
the lsp/cp split missing.)

ECMWF-DERIVABILITY GATE (audited 2026-10-30, BEFORE fetching, same
method as the w/clwc audit): ECMWF's live IFS open-data index files
for the 2026-10-30 00z 0.25 deg oper stream, retrieved from
data.ecmwf.int at step 0 AND step 24 (step 0 alone can carry
analysis-only fields, so both were checked):

* ``2d``   -- PASS: present at levtype ``sfc`` in both index files.
* ``tclw`` -- PASS: present at levtype ``sfc`` in both index files.
* ``10u``  -- PASS: present at levtype ``sfc`` in both index files.
* ``10v``  -- PASS: present at levtype ``sfc`` in both index files.
* ``skt``  -- PASS: present at levtype ``sfc`` in both index files.

SOURCE: bucket ``nsf-ncar-era5`` (anonymous unsigned S3), analysis
surface files ``e5.oper.an.sfc/{yyyymm}/e5.oper.an.sfc.{code}_{token}.
ll025sc.{yyyymm}0100_{yyyymm}{last}23.nc`` -- one file per variable
per MONTH, HDF5 datasets (verified by live file reads on 2026-10-30;
shape (hours, 721, 1440), chunks (27, 139, 277), the same cheap
range-read family as the sp file):

* ``2d``   128_168 -> ``VAR_2D``   (K)
* ``tclw`` 128_078 -> ``TCLW``     (kg/m^2)
* ``10u``  128_165 -> ``VAR_10U``  (m/s)
* ``10v``  128_166 -> ``VAR_10V``  (m/s)
* ``skt``  128_235 -> ``SKT``      (K)

Extraction mirrors ``gold_profiles_sp.SpReader`` exactly: nearest
0.25 deg grid point to the station, HDF5 range reads via
``read_points`` chunk batching.  No raw file is written to disk.

CACHE CONTRACT (all under ``model-v1/model-v3-features/``; separate
trees, so an sfc run never touches the w / q / accumu trees or
``coverage_v3.json``):

* Parts: ``sfc_parts/{yyyymm}/{station_id}.json.gz`` -- the station's
  required hours inside that month: ``{"time": [...],
  "dewpoint_2m": [...], "total_column_liquid_water": [...],
  "wind_u_10m": [...], "wind_v_10m": [...],
  "skin_temperature": [...]}``.
* Chunks: ``sfc_chunks/{station_id}/{start}_{end}.json.gz`` --
  assembled with the same chunk plan and naming as the profile cache.
* Coverage: ``coverage_v3_sfc.json``; per-shard summaries
  ``v3_fetch_summary_sfc_shard{i}.json``.

SCOPE: the modelling pool only -- the 57,376 events whose v2 profile
build status is ``ok``, with the same required-hours definition as
the surface-pressure fetch (``gold_profiles_sp.required_hours``).
"""

from __future__ import annotations

import calendar
import gzip
import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .gold_profiles import ERA5_SPEC, _chunk_path
from .gold_profiles_era5 import Era5Reader, hours_since_1900, read_points
from .gold_profiles_sp import (
    _hour_text,
    _parse_hour,
    _write_gzip_json,
    load_modelling_events,
    month_needs,
    plan_sp_chunks,
    required_hours,
    station_coordinates,
)
from .gold_profiles_v3 import V3_DIR_NAME

# token -> (GRIB code prefix, filename token, HDF5 dataset, payload field, units, round digits)
V3_SFC_VARIABLES: dict[str, tuple[str, str, str, str, str, int]] = {
    "2d": ("128_168", "2d", "VAR_2D", "dewpoint_2m", "K", 3),
    "tclw": ("128_078", "tclw", "TCLW", "total_column_liquid_water", "kg/m^2", 6),
    "10u": ("128_165", "10u", "VAR_10U", "wind_u_10m", "m/s", 3),
    "10v": ("128_166", "10v", "VAR_10V", "wind_v_10m", "m/s", 3),
    "skt": ("128_235", "skt", "SKT", "skin_temperature", "K", 3),
}

SFC_FIELDS: list[str] = [spec[3] for spec in V3_SFC_VARIABLES.values()]
SFC_UNITS: dict[str, str] = {spec[3]: spec[4] for spec in V3_SFC_VARIABLES.values()}
SFC_DIGITS: dict[str, int] = {spec[3]: spec[5] for spec in V3_SFC_VARIABLES.values()}


def v3_sfc_key(year: int, month: int, variable: str) -> str:
    """Bucket key of the monthly an.sfc file for one sfc variable."""
    code, token, _hdf5_name, _field, _units, _digits = V3_SFC_VARIABLES[variable]
    last_day = calendar.monthrange(year, month)[1]
    return (
        f"e5.oper.an.sfc/{year:04d}{month:02d}/e5.oper.an.sfc.{code}_{token}.ll025sc."
        f"{year:04d}{month:02d}0100_{year:04d}{month:02d}{last_day:02d}23.nc"
    )


# ---------------------------------------------------------------------------
# Reader (mirrors gold_profiles_sp.SpReader for the five an.sfc variables)
# ---------------------------------------------------------------------------

class V3SfcReader:
    """Hourly reads of the five sfc variables from the monthly files."""

    def __init__(self, reader: Era5Reader | None = None) -> None:
        self._reader = reader or Era5Reader()

    def read_month(
        self,
        year: int,
        month: int,
        stations: list[tuple[str, float, float]],
        hours_by_station: dict[str, list[datetime]],
    ) -> dict[tuple[str, datetime], dict[str, float | None]]:
        """{(station_id, instant): {token: value}} for one month."""
        out: dict[tuple[str, datetime], dict[str, float | None]] = {}
        position = {station_id: i for i, (station_id, _lat, _lon) in enumerate(stations)}
        for token, (_code, _tok, hdf5_name, _field, _units, _digits) in V3_SFC_VARIABLES.items():
            key = v3_sfc_key(year, month, token)
            handle = self._reader._open(key)
            coords = self._reader._coords(key)
            dataset = handle[hdf5_name]
            time0 = int(coords["time"][0])
            lat_idx, lon_idx = self._reader._grid_indices(key, stations)
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
            for slot, value in zip(slots, read_points(dataset, points)):
                out.setdefault(slot, {})[token] = value
        return out


# ---------------------------------------------------------------------------
# Parts (per station-month) -- resumable fetch units
# ---------------------------------------------------------------------------

def _sfc_part_path(parts_dir: Path, year: int, month: int, station_id: str) -> Path:
    return parts_dir / f"{year:04d}{month:02d}" / f"{station_id}.json.gz"


def _sfc_part_valid(path: Path, expected_hours: list[str]) -> bool:
    if not path.exists():
        return False
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return False
    if payload.get("time") != expected_hours:
        return False
    for field in SFC_FIELDS:
        values = payload.get(field)
        if not isinstance(values, list) or len(values) != len(expected_hours):
            return False
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            return False
    return True


def fetch_v3_sfc(
    events: list[dict[str, Any]],
    v3_dir: Path,
    shard_index: int = 0,
    shard_count: int = 1,
    reader: V3SfcReader | None = None,
) -> dict[str, Any]:
    """Fetch this shard's months into per-station-month sfc part files.

    Resumable like the sp fetch: a part whose stored hours exactly
    match the plan and whose five fields are all finite is skipped.
    A month that fails after 3 attempts is reported in
    ``monthsFailed`` and left for a rerun; parts are written only
    from a completed month read.
    """
    chunks = plan_sp_chunks(events)
    needs = month_needs(chunks)
    coords = station_coordinates(events)
    parts_dir = v3_dir / "sfc_parts"
    active = reader or V3SfcReader()
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
            if not _sfc_part_valid(_sfc_part_path(parts_dir, year, month, station_id), hours)
        }
        summary["partsSkippedValid"] += len(expected) - len(todo)
        if not todo:
            summary["monthsDone"].append(f"{year:04d}{month:02d}")
            continue
        stations = [(sid, coords[sid][0], coords[sid][1]) for sid in sorted(todo)]
        hours_dt = {sid: [_parse_hour(text) for text in hours] for sid, hours in todo.items()}
        values: dict[tuple[str, datetime], dict[str, float | None]] | None = None
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
            payload: dict[str, Any] = {"time": hours}
            complete = True
            for token, (_c, _t, _h, field, _u, digits) in V3_SFC_VARIABLES.items():
                column: list[float | None] = []
                for text in hours:
                    value = values.get((station_id, _parse_hour(text)), {}).get(token)
                    if value is None:
                        complete = False
                    column.append(round(value, digits) if value is not None else None)
                payload[field] = column
            _write_gzip_json(_sfc_part_path(parts_dir, year, month, station_id), payload)
            summary["partsWritten"] += 1
            summary["pointsRead"] += len(hours) * len(V3_SFC_VARIABLES)
            if not complete:
                summary.setdefault("partsWithNulls", 0)
                summary["partsWithNulls"] += 1
        summary["monthsDone"].append(f"{year:04d}{month:02d}")
        print(f"[v3-sfc] month {year:04d}{month:02d} done", flush=True)
    return summary


# ---------------------------------------------------------------------------
# Assembly: parts -> chunk files + per-event coverage
# ---------------------------------------------------------------------------

def load_sfc_parts(parts_dir: Path, station_id: str) -> dict[datetime, dict[str, float]]:
    """All fetched sfc hours for one station: {instant: {field: value}}."""
    hours: dict[datetime, dict[str, float]] = {}
    if not parts_dir.exists():
        return hours
    for month_dir in sorted(parts_dir.iterdir()):
        path = month_dir / f"{station_id}.json.gz"
        if not path.exists():
            continue
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        for i, text in enumerate(payload["time"]):
            record = {
                field: payload[field][i]
                for field in SFC_FIELDS
                if payload[field][i] is not None
            }
            if len(record) == len(SFC_FIELDS):
                hours[_parse_hour(text)] = record
    return hours


def assemble_v3_sfc(events: list[dict[str, Any]], v3_dir: Path) -> dict[str, Any]:
    """Assemble sfc chunk files from parts; per-event coverage as in sp.

    A chunk is written only when every required hour is present with
    all five fields finite.  Coverage is computed per event, so a
    failed chunk does not misreport the events inside it.
    """
    chunks = plan_sp_chunks(events)
    parts_dir = v3_dir / "sfc_parts"
    chunks_dir = v3_dir / "sfc_chunks"
    station_hours: dict[str, dict[datetime, dict[str, float]]] = {}

    def hours_for(station_id: str) -> dict[datetime, dict[str, float]]:
        if station_id not in station_hours:
            station_hours[station_id] = load_sfc_parts(parts_dir, station_id)
        return station_hours[station_id]

    covered: list[str] = []
    missing: list[dict[str, Any]] = []
    for event in events:
        available = hours_for(event["station_id"])
        absent = [
            t
            for t in (_hour_text(h) for h in required_hours(event))
            if _parse_hour(t) not in available
        ]
        if absent:
            missing.append({"event_id": event["event_id"], "missingHours": len(absent)})
        else:
            covered.append(event["event_id"])

    chunks_written = 0
    chunks_failed: list[dict[str, Any]] = []
    for chunk in chunks:
        available = hours_for(chunk["station_id"])
        absent = [t for t in chunk["required_hours"] if _parse_hour(t) not in available]
        path = _chunk_path(chunks_dir, chunk)
        if absent:
            chunks_failed.append({"key": chunk["key"], "missingHours": len(absent)})
            continue
        hourly: dict[str, Any] = {"time": chunk["required_hours"]}
        for field in SFC_FIELDS:
            hourly[field] = [
                round(available[_parse_hour(t)][field], SFC_DIGITS[field])
                for t in chunk["required_hours"]
            ]
        _write_gzip_json(
            path,
            {
                "hourly": hourly,
                "variables": "2d, tclw, 10u, 10v, skt (Phase 3 sfc fetch)",
                "units": SFC_UNITS,
                "profile_source": ERA5_SPEC.source_id,
                "profile_model": "ERA5",
                "station_id": chunk["station_id"],
                "event_ids": chunk["event_ids"],
            },
        )
        chunks_written += 1
    return {
        "eventsTotal": len(events),
        "eventsCovered": len(covered),
        "eventsMissing": missing,
        "chunksTotal": len(chunks),
        "chunksWritten": chunks_written,
        "chunksFailed": chunks_failed,
    }


# ---------------------------------------------------------------------------
# Run entry point
# ---------------------------------------------------------------------------

def run_fetch_v3_sfc(
    pool_dir: Path,
    output_dir: Path,
    shard_index: int = 0,
    shard_count: int = 1,
    assemble_only: bool = False,
    no_assemble: bool = False,
) -> dict[str, Any]:
    """Fetch the sfc set for the modelling pool into ``model-v3-features``.

    ``output_dir`` is the model-v1 directory (parity with the other
    v3 entry points); the Phase 3 tree lives at
    ``output_dir/model-v3-features``.  Writes
    ``coverage_v3_sfc.json`` when assembling and a per-shard summary
    ``v3_fetch_summary_sfc_shard{i}.json``; never touches
    ``coverage_v3.json`` or the w/q/accumu trees.
    """
    events = load_modelling_events(pool_dir, output_dir / "era5-expanded-v2" / "features.parquet")
    v3_dir = output_dir / V3_DIR_NAME
    v3_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"modellingEvents": len(events), "v3Dir": str(v3_dir)}
    if not assemble_only:
        summary["fetch"] = fetch_v3_sfc(
            events, v3_dir, shard_index=shard_index, shard_count=shard_count
        )
    if assemble_only or (not no_assemble and shard_count == 1):
        summary["coverage"] = assemble_v3_sfc(events, v3_dir)
        (v3_dir / "coverage_v3_sfc.json").write_text(
            json.dumps(summary["coverage"], indent=2, sort_keys=True), encoding="utf-8"
        )
    (v3_dir / f"v3_fetch_summary_sfc_shard{shard_index}.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary
