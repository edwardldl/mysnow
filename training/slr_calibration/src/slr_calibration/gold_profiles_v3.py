"""Phase 3 companion fetch: vertical velocity + split precipitation.

The Phase 3 physics feature rebuild needs two things the ERA5 profile
cache never retained:

* ``w`` (vertical velocity, Pa/s) on the cache's 13 pressure levels --
  the raw material for the DGZ-ascent / Cobb-overlap bundle.
* the hourly split of precipitation into ``lsp`` and ``cp`` -- the
  profile cache retained only the combined ``precipitation`` (tp) and
  ``snowfall`` (sf) hourly fields, so the convective fraction
  (cp / tp) and the large-scale share were unrecoverable from cache.
  ``sf`` and ``tp`` themselves are NOT refetched: they are read back
  from the profile cache at assembly time and cross-checked against
  lsp + cp.

ECMWF-DERIVABILITY GATE (audited 2026-10-29 against ECMWF's live IFS
open-data index files for the 2026-10-28 00z 0.25 deg oper stream,
steps 6h and 24h, plus Open-Meteo's ecmwf feed): every variable kept
here must be derivable from an ECMWF IFS forecast, not merely from
ERA5.

* ``w`` -- PASS: pressure-level parameter in the open-data set
  (levelist 1000..10 hPa).  Open-Meteo also serves
  ``vertical_velocity_<level>hPa`` at the IFS levels.
* ``clwc`` / ``ciwc`` -- FAIL, NOT FETCHED: absent from the open-data
  pressure-level parameter set and the open-data stream carries no
  model-level rows at all; only the column integrals ``tclw``/``tciw``
  exist (surface level).  The riming bundle therefore falls back to
  its RH + ascent proxy design.
* ``sf`` / ``cp`` -- PASS: surface parameters in the open-data set
  (note: Open-Meteo's ecmwf endpoint does not expose convective
  precipitation, so the cp split needs the raw GRIB route).
* ``lsp`` -- PASS BY DERIVATION: not a standalone open-data forecast
  parameter; at IFS serving time lsp = tp - cp, with tp and cp both
  present.  On the ERA5 training side lsp is read directly (128_142)
  and tp = lsp + cp is the derived quantity -- the derivation
  direction reverses, the identity is the same.

Source of record is unchanged: bucket ``nsf-ncar-era5`` (anonymous
unsigned S3), ``e5.oper.an.pl`` daily files for w (128_135_w, HDF5
``W``) and ``e5.oper.fc.sfc.accumu`` half-month files for lsp/cp,
extracted with the same nearest-grid-point, ``read_points`` batching
and (t-1h, t] differencing conventions as ``gold_profiles_era5``.

CACHE CONTRACT (all under ``model-v1/model-v3-features/``):

* PL parts: ``pl_parts/{yyyymmdd}/{station_id}.json.gz`` -- the
  station's required hours that day: ``{"time": [...],
  "w_<level>hPa": [Pa/s, ...]}`` for the 13 levels.
* Accumulation parts: ``accumu_parts/{yyyymm}/{station_id}.json.gz``
  -- ``{"time": [...], "lsp_mm": [...], "cp_mm": [...]}`` hourly
  differenced values for the station's required hours that month.
* PL chunks: ``pl_chunks/{station_id}/{start}_{end}.json.gz`` --
  assembled with the same chunk plan and naming as the profile cache.
* Accumulation chunks: ``accumu_chunks/{station_id}/{start}_{end}.
  json.gz`` -- hourly ``sf_mm`` (from the profile cache's snowfall,
  cm of water equivalent x 10), ``lsp_mm``, ``cp_mm``, ``tp_mm``
  (= lsp + cp) and ``tp_profile_cache_mm`` for the cross-check.

CONCURRENCY NOTE (observed 2026-10-29): with 4 worker threads the
fetch made no progress in 10 minutes (all threads futex-waiting;
s3fs through the workspace proxy), while 1-2 workers fetch steadily.
The default is therefore 2 workers; the fetch is resumable, so a
stalled run can simply be killed and relaunched.

SCOPE: the modelling pool only -- the 57,376 events whose v2 profile
build status is ``ok``, with the same required-hours definition as the
surface-pressure fetch (``gold_profiles_sp.required_hours``).
"""

from __future__ import annotations

import gzip
import json
import math
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .gold_profiles import ERA5_SPEC, PRESSURE_LEVELS_HPA, _chunk_path
from .gold_profiles_era5 import (
    BUCKET,
    Era5Reader,
    accumulation_init,
    hourly_accumulation,
    hours_since_1900,
    read_points,
)
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

V3_DIR_NAME = "model-v3-features"

# variable -> (GRIB code prefix, filename token, grid suffix, HDF5 name, units)
V3_PL_VARIABLES = {
    "w": ("128_135", "w", "ll025sc", "W", "Pa/s"),
}


def v3_pl_key(day: date, variable: str = "w") -> str:
    """Bucket key of the daily an.pl file for a Phase 3 PL variable."""
    code, token, grid, _hdf5_name, _units = V3_PL_VARIABLES[variable]
    return (
        f"e5.oper.an.pl/{day:%Y%m}/e5.oper.an.pl.{code}_{token}.{grid}."
        f"{day:%Y%m%d}00_{day:%Y%m%d}23.nc"
    )


def _level_field(level: int) -> str:
    return f"w_{level}hPa"


# ---------------------------------------------------------------------------
# Planning: per-day (PL) and per-month (accumulations) needs
# ---------------------------------------------------------------------------

def day_needs(chunks: list[dict[str, Any]]) -> dict[date, dict[str, list[str]]]:
    """{day: {station_id: [hour texts]}} over all chunks' required hours."""
    needs: dict[date, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for chunk in chunks:
        for text in chunk["required_hours"]:
            needs[_parse_hour(text).date()][chunk["station_id"]].add(text)
    return {
        day: {sid: sorted(texts) for sid, texts in sorted(per.items())}
        for day, per in sorted(needs.items())
    }


# ---------------------------------------------------------------------------
# PL reader (mirrors Era5Reader.read_pl_day, restricted to needed hours)
# ---------------------------------------------------------------------------

class V3PlReader:
    """Hourly w reads from the daily an.pl files."""

    def __init__(self, reader: Era5Reader | None = None) -> None:
        self._reader = reader or Era5Reader()

    def read_day(
        self,
        day: date,
        stations: list[tuple[str, float, float]],
        hours_by_station: dict[str, list[datetime]],
    ) -> dict[tuple[str, datetime], dict[int, float | None]]:
        """{(station_id, instant): {level_hpa: w in Pa/s}} for one day."""
        key = v3_pl_key(day)
        handle = self._reader._open(key)
        coords = self._reader._coords(key)
        dataset = handle[V3_PL_VARIABLES["w"][3]]
        level_values = [int(v) for v in coords["level"]]
        level_index = {level: level_values.index(level) for level in PRESSURE_LEVELS_HPA}
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        assert int(coords["time"][0]) == hours_since_1900(day_start)
        lat_idx, lon_idx = self._reader._grid_indices(key, stations)
        position = {sid: i for i, (sid, _lat, _lon) in enumerate(stations)}
        points: list[tuple[int, ...]] = []
        slots: list[tuple[str, datetime, int]] = []
        for station_id, hours in hours_by_station.items():
            pos = position[station_id]
            for instant in hours:
                for level in PRESSURE_LEVELS_HPA:
                    points.append((instant.hour, level_index[level], lat_idx[pos], lon_idx[pos]))
                    slots.append((station_id, instant, level))
        out: dict[tuple[str, datetime], dict[int, float | None]] = {}
        for (station_id, instant, level), value in zip(slots, read_points(dataset, points)):
            out.setdefault((station_id, instant), {})[level] = value
        return out


# ---------------------------------------------------------------------------
# PL parts (per station-day) -- resumable fetch units
# ---------------------------------------------------------------------------

def _pl_part_path(parts_dir: Path, day: date, station_id: str) -> Path:
    return parts_dir / f"{day:%Y%m%d}" / f"{station_id}.json.gz"


def _pl_part_valid(path: Path, expected_hours: list[str]) -> bool:
    if not path.exists():
        return False
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return False
    if payload.get("time") != expected_hours:
        return False
    for level in PRESSURE_LEVELS_HPA:
        values = payload.get(_level_field(level))
        if not isinstance(values, list) or len(values) != len(expected_hours):
            return False
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            return False
    return True


def fetch_v3_pl(
    events: list[dict[str, Any]],
    v3_dir: Path,
    shard_index: int = 0,
    shard_count: int = 1,
    max_workers: int = 2,
    reader: V3PlReader | None = None,
) -> dict[str, Any]:
    """Fetch this shard's days into per-station-day w part files.

    Resumable: a part whose stored hours exactly match the plan and are
    all finite is skipped.  A day that fails after 3 attempts is
    reported in ``daysFailed`` and left for a rerun -- a part file is
    written only from a completed day read, never before it.
    """
    chunks = plan_sp_chunks(events)
    needs = day_needs(chunks)
    coords = station_coordinates(events)
    parts_dir = v3_dir / "pl_parts"
    active = reader or V3PlReader()
    days = sorted(needs)
    mine = [day for i, day in enumerate(days) if i % shard_count == shard_index]
    summary: dict[str, Any] = {
        "shardIndex": shard_index,
        "shardCount": shard_count,
        "daysPlanned": [day.isoformat() for day in mine],
        "daysDone": [],
        "daysFailed": [],
        "partsWritten": 0,
        "partsSkippedValid": 0,
        "pointsRead": 0,
    }

    def work(day: date) -> tuple[date, int, int, int, str | None]:
        expected = needs[day]
        todo = {
            sid: hours
            for sid, hours in expected.items()
            if not _pl_part_valid(_pl_part_path(parts_dir, day, sid), hours)
        }
        skipped = len(expected) - len(todo)
        if not todo:
            return day, 0, skipped, 0, None
        stations = [(sid, coords[sid][0], coords[sid][1]) for sid in sorted(todo)]
        hours_dt = {sid: [_parse_hour(t) for t in hours] for sid, hours in todo.items()}
        values = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                values = active.read_day(day, stations, hours_dt)
                break
            except Exception as exc:  # retried: S3 throttling / transient I/O
                last_error = exc
                values = None
                time.sleep(5.0 * (attempt + 1) ** 2)
        if values is None:
            return day, 0, skipped, 0, f"{type(last_error).__name__}: {last_error}"
        written = 0
        points = 0
        for sid, hours in sorted(todo.items()):
            payload: dict[str, Any] = {"time": hours}
            for level in PRESSURE_LEVELS_HPA:
                payload[_level_field(level)] = [
                    round(values[(sid, _parse_hour(t))][level], 5)
                    if values.get((sid, _parse_hour(t)), {}).get(level) is not None
                    else None
                    for t in hours
                ]
            _write_gzip_json(_pl_part_path(parts_dir, day, sid), payload)
            written += 1
            points += len(hours) * len(PRESSURE_LEVELS_HPA)
        return day, written, skipped, points, None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(work, day): day for day in mine}
        done = 0
        for future in as_completed(futures):
            day, written, skipped, points, error = future.result()
            summary["partsWritten"] += written
            summary["partsSkippedValid"] += skipped
            summary["pointsRead"] += points
            if error is not None:
                summary["daysFailed"].append({"day": day.isoformat(), "error": error})
            else:
                summary["daysDone"].append(day.isoformat())
            done += 1
            if done % 100 == 0:
                print(f"[v3-pl] {done}/{len(mine)} days processed", flush=True)
    summary["daysDone"].sort()
    return summary


# ---------------------------------------------------------------------------
# Accumulation reader: hourly lsp/cp from the fc.sfc.accumu files
# ---------------------------------------------------------------------------

class V3AccumuReader:
    """Hourly (lsp_mm, cp_mm) via the campaign's cumulative differencing."""

    def __init__(self, reader: Era5Reader | None = None) -> None:
        self._reader = reader or Era5Reader()

    def read_month(
        self,
        year: int,
        month: int,
        stations: list[tuple[str, float, float]],
        hours_by_station: dict[str, list[datetime]],
    ) -> dict[tuple[str, datetime], tuple[float | None, float | None]]:
        """{(station_id, instant): (lsp_mm, cp_mm)} for the given hours."""
        coords = {sid: (lat, lon) for sid, lat, lon in stations}
        requests: set[tuple[datetime, int, str, float, float]] = set()
        for station_id, hours in hours_by_station.items():
            lat, lon = coords[station_id]
            for instant in hours:
                init, forecast_hour = accumulation_init(instant)
                requests.add((init, forecast_hour, station_id, lat, lon))
                if forecast_hour > 1:
                    requests.add((init, forecast_hour - 1, station_id, lat, lon))
        request_list = sorted(requests)
        cumulative = {
            var: self._reader.read_accumu_cumulative(var, request_list)
            for var in ("lsp", "cp")
        }
        out: dict[tuple[str, datetime], tuple[float | None, float | None]] = {}
        for station_id, hours in hours_by_station.items():
            for instant in hours:
                lsp_m = hourly_accumulation(cumulative["lsp"], station_id, instant)
                cp_m = hourly_accumulation(cumulative["cp"], station_id, instant)
                out[(station_id, instant)] = (
                    lsp_m * 1000.0 if lsp_m is not None else None,
                    cp_m * 1000.0 if cp_m is not None else None,
                )
        return out


# ---------------------------------------------------------------------------
# Accumulation parts (per station-month) -- resumable fetch units
# ---------------------------------------------------------------------------

def _acc_part_path(parts_dir: Path, year: int, month: int, station_id: str) -> Path:
    return parts_dir / f"{year:04d}{month:02d}" / f"{station_id}.json.gz"


def _acc_part_valid(path: Path, expected_hours: list[str]) -> bool:
    if not path.exists():
        return False
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return False
    if payload.get("time") != expected_hours:
        return False
    for field in ("lsp_mm", "cp_mm"):
        values = payload.get(field)
        if not isinstance(values, list) or len(values) != len(expected_hours):
            return False
        if not all(v is None or (isinstance(v, (int, float)) and math.isfinite(v)) for v in values):
            return False
    return True


def fetch_v3_accumu(
    events: list[dict[str, Any]],
    v3_dir: Path,
    shard_index: int = 0,
    shard_count: int = 1,
    reader: V3AccumuReader | None = None,
) -> dict[str, Any]:
    """Fetch this shard's months into per-station-month lsp/cp parts.

    Resumable like the sp fetch: a structurally valid part is skipped.
    Null hourly values are preserved and counted (``nullHours``) rather
    than hidden; a month whose reads raise after 3 attempts lands in
    ``monthsFailed`` and no parts are written for it.
    """
    chunks = plan_sp_chunks(events)
    needs = month_needs(chunks)
    coords = station_coordinates(events)
    parts_dir = v3_dir / "accumu_parts"
    active = reader or V3AccumuReader()
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
        "nullHours": 0,
    }
    for year, month in mine:
        expected = needs[(year, month)]
        todo = {
            sid: hours
            for sid, hours in expected.items()
            if not _acc_part_valid(_acc_part_path(parts_dir, year, month, sid), hours)
        }
        summary["partsSkippedValid"] += len(expected) - len(todo)
        if not todo:
            summary["monthsDone"].append(f"{year:04d}{month:02d}")
            continue
        stations = [(sid, coords[sid][0], coords[sid][1]) for sid in sorted(todo)]
        hours_dt = {sid: [_parse_hour(t) for t in hours] for sid, hours in todo.items()}
        values = None
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
        for sid, hours in sorted(todo.items()):
            lsp_values: list[float | None] = []
            cp_values: list[float | None] = []
            for text in hours:
                lsp, cp = values.get((sid, _parse_hour(text)), (None, None))
                lsp_values.append(round(lsp, 4) if lsp is not None else None)
                cp_values.append(round(cp, 4) if cp is not None else None)
                if lsp is None or cp is None:
                    summary["nullHours"] += 1
            _write_gzip_json(
                _acc_part_path(parts_dir, year, month, sid),
                {"time": hours, "lsp_mm": lsp_values, "cp_mm": cp_values},
            )
            summary["partsWritten"] += 1
            summary["pointsRead"] += len(hours)
        summary["monthsDone"].append(f"{year:04d}{month:02d}")
        print(f"[v3-accumu] month {year:04d}{month:02d} done", flush=True)
    return summary


# ---------------------------------------------------------------------------
# Assembly: parts -> chunk files + per-event coverage
# ---------------------------------------------------------------------------

def load_pl_parts(parts_dir: Path, station_id: str) -> dict[datetime, dict[int, float]]:
    """All fetched w hours for one station: {instant: {level: Pa/s}}."""
    hours: dict[datetime, dict[int, float]] = {}
    if not parts_dir.exists():
        return hours
    for day_dir in sorted(parts_dir.iterdir()):
        path = day_dir / f"{station_id}.json.gz"
        if not path.exists():
            continue
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        for i, text in enumerate(payload["time"]):
            record = {
                level: payload[_level_field(level)][i]
                for level in PRESSURE_LEVELS_HPA
                if payload[_level_field(level)][i] is not None
            }
            if len(record) == len(PRESSURE_LEVELS_HPA):
                hours[_parse_hour(text)] = record
    return hours


def assemble_v3_pl(events: list[dict[str, Any]], v3_dir: Path) -> dict[str, Any]:
    """Assemble w chunk files from parts; per-event coverage as in sp."""
    chunks = plan_sp_chunks(events)
    parts_dir = v3_dir / "pl_parts"
    chunks_dir = v3_dir / "pl_chunks"
    station_hours: dict[str, dict[datetime, dict[int, float]]] = {}

    def hours_for(station_id: str) -> dict[datetime, dict[int, float]]:
        if station_id not in station_hours:
            station_hours[station_id] = load_pl_parts(parts_dir, station_id)
        return station_hours[station_id]

    covered: list[str] = []
    missing: list[dict[str, Any]] = []
    for event in events:
        available = hours_for(event["station_id"])
        absent = [t for t in (_hour_text(h) for h in required_hours(event)) if _parse_hour(t) not in available]
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
        for level in PRESSURE_LEVELS_HPA:
            hourly[_level_field(level)] = [
                round(available[_parse_hour(t)][level], 5) for t in chunk["required_hours"]
            ]
        _write_gzip_json(
            path,
            {
                "hourly": hourly,
                "variable": "w",
                "units": "Pa/s",
                "levels_hpa": list(PRESSURE_LEVELS_HPA),
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


def load_acc_parts(parts_dir: Path, station_id: str) -> dict[datetime, tuple[float | None, float | None]]:
    """All fetched lsp/cp hours for one station: {instant: (lsp_mm, cp_mm)}."""
    hours: dict[datetime, tuple[float | None, float | None]] = {}
    if not parts_dir.exists():
        return hours
    for month_dir in sorted(parts_dir.iterdir()):
        path = month_dir / f"{station_id}.json.gz"
        if not path.exists():
            continue
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        for text, lsp, cp in zip(payload["time"], payload["lsp_mm"], payload["cp_mm"]):
            hours[_parse_hour(text)] = (lsp, cp)
    return hours


def load_profile_cache_accum(
    cache_dir: Path, station_id: str
) -> dict[str, tuple[float | None, float | None]]:
    """Profile-cache hourly accumulations: {hour text: (tp_mm, sf_cm_we)}."""
    out: dict[str, tuple[float | None, float | None]] = {}
    station_dir = cache_dir / station_id
    if not station_dir.exists():
        return out
    for path in sorted(station_dir.glob("*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        hourly = payload["hourly"]
        for text, tp, sf in zip(
            hourly["time"], hourly.get("precipitation", []), hourly.get("snowfall", [])
        ):
            out[text] = (tp, sf)
    return out


def assemble_v3_accumu(
    events: list[dict[str, Any]], v3_dir: Path, profile_cache_dir: Path
) -> dict[str, Any]:
    """Assemble accumulation chunk files (fetched lsp/cp + cached sf/tp).

    A chunk is written only when every required hour has lsp, cp and
    the profile cache's tp and sf.  Per-event coverage is computed the
    same way, so a failed chunk does not misreport its events.
    """
    chunks = plan_sp_chunks(events)
    parts_dir = v3_dir / "accumu_parts"
    chunks_dir = v3_dir / "accumu_chunks"
    acc_hours: dict[str, dict[datetime, tuple[float | None, float | None]]] = {}
    cache_hours: dict[str, dict[str, tuple[float | None, float | None]]] = {}

    def fetched(station_id: str) -> dict[datetime, tuple[float | None, float | None]]:
        if station_id not in acc_hours:
            acc_hours[station_id] = load_acc_parts(parts_dir, station_id)
        return acc_hours[station_id]

    def cached(station_id: str) -> dict[str, tuple[float | None, float | None]]:
        if station_id not in cache_hours:
            cache_hours[station_id] = load_profile_cache_accum(profile_cache_dir, station_id)
        return cache_hours[station_id]

    def hour_complete(station_id: str, text: str) -> bool:
        lsp, cp = fetched(station_id).get(_parse_hour(text), (None, None))
        tp_cache, sf_cache = cached(station_id).get(text, (None, None))
        return None not in (lsp, cp, tp_cache, sf_cache)

    covered: list[str] = []
    missing: list[dict[str, Any]] = []
    for event in events:
        absent = [
            t
            for t in (_hour_text(h) for h in required_hours(event))
            if not hour_complete(event["station_id"], t)
        ]
        if absent:
            missing.append({"event_id": event["event_id"], "missingHours": len(absent)})
        else:
            covered.append(event["event_id"])

    chunks_written = 0
    chunks_failed: list[dict[str, Any]] = []
    for chunk in chunks:
        absent = [t for t in chunk["required_hours"] if not hour_complete(chunk["station_id"], t)]
        path = _chunk_path(chunks_dir, chunk)
        if absent:
            chunks_failed.append({"key": chunk["key"], "missingHours": len(absent)})
            continue
        hourly: dict[str, Any] = {"time": chunk["required_hours"], "sf_mm": [], "lsp_mm": [], "cp_mm": [], "tp_mm": [], "tp_profile_cache_mm": []}
        for text in chunk["required_hours"]:
            lsp, cp = fetched(chunk["station_id"])[_parse_hour(text)]
            tp_cache, sf_cache = cached(chunk["station_id"])[text]
            assert lsp is not None and cp is not None and tp_cache is not None and sf_cache is not None
            hourly["sf_mm"].append(round(sf_cache * 10.0, 4))
            hourly["lsp_mm"].append(lsp)
            hourly["cp_mm"].append(cp)
            hourly["tp_mm"].append(round(lsp + cp, 4))
            hourly["tp_profile_cache_mm"].append(tp_cache)
        _write_gzip_json(
            path,
            {
                "hourly": hourly,
                "variables": "sf (profile cache), lsp + cp (Phase 3 fetch)",
                "units": "mm",
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

def run_fetch_v3(
    pool_dir: Path,
    output_dir: Path,
    shard_index: int = 0,
    shard_count: int = 1,
    assemble_only: bool = False,
    no_assemble: bool = False,
    max_workers: int = 2,
) -> dict[str, Any]:
    """Fetch w + lsp/cp for the modelling pool into ``model-v3-features``.

    ``output_dir`` is the model-v1 directory (parity with the sp fetch);
    the Phase 3 tree lives at ``output_dir/model-v3-features`` and the
    profile cache used for sf/tp at assembly is ``output_dir/
    raw_cache_era5``.
    """
    events = load_modelling_events(pool_dir, output_dir / "era5-expanded-v2" / "features.parquet")
    v3_dir = output_dir / V3_DIR_NAME
    v3_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"modellingEvents": len(events), "v3Dir": str(v3_dir)}
    if not assemble_only:
        summary["fetchPl"] = fetch_v3_pl(
            events, v3_dir, shard_index=shard_index, shard_count=shard_count, max_workers=max_workers
        )
        summary["fetchAccumu"] = fetch_v3_accumu(
            events, v3_dir, shard_index=shard_index, shard_count=shard_count
        )
    if assemble_only or (not no_assemble and shard_count == 1):
        summary["coverage"] = {
            "pl": assemble_v3_pl(events, v3_dir),
            "accumu": assemble_v3_accumu(events, v3_dir, output_dir / "raw_cache_era5"),
        }
        (v3_dir / "coverage_v3.json").write_text(
            json.dumps(summary["coverage"], indent=2, sort_keys=True), encoding="utf-8"
        )
    (v3_dir / f"v3_fetch_summary_shard{shard_index}.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary
