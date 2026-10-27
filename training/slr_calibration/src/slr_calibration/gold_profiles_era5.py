"""Step 2 (gold-only model): ERA5 profile fetching from AWS Open Data.

SOURCE (verified 2026-10-20 from this workspace, anonymous unsigned S3):

* Bucket ``nsf-ncar-era5`` -- NSF NCAR's curated copy of ECMWF ERA5 on
  the AWS Open Data registry.  ListBucket and GetObject both succeed
  unsigned.  The Intertrust bucket ``era5-pds`` was probed the same day
  and returns HTTP 403 for both listing and object GET, so it is not
  usable without credentials; the NCAR mirror carries the identical
  ECMWF ERA5 reanalysis and is the source of record here.  Every output
  labels the source ``era5-nsf-ncar-aws`` (see ``ERA5_SPEC``).
* Verified layout (all 0.25 deg, hourly):
  - pressure levels: ``e5.oper.an.pl/{yyyymm}/`` with one file per
    variable per DAY, dims (time=24, level=37, lat=721, lon=1440),
    HDF5 chunks of one full global hour -- any read pulls whole hour
    chunks, so extraction is batched per calendar day across every
    station that needs that day.  Variables used: 128_130_t (K),
    128_157_r (%), 128_131_u / 128_132_v (m/s, ``ll025uv`` files),
    128_129_z (geopotential, m^2 s^-2).
  - 2 m temperature: ``e5.oper.an.sfc`` 128_167_2t, one file per
    variable per MONTH, chunks (27 h, 139, 277) -- cheap range reads.
  - accumulations: ``e5.oper.fc.sfc.accumu`` 128_144_sf (snowfall, m of
    water equivalent), 128_142_lsp and 128_143_cp (m); half-month files
    spanning the 06 UTC forecast cycles, dims (init, forecast_hour,
    lat, lon).  There is NO total-precipitation file in this mirror;
    precipitation is the ECMWF identity tp = lsp + cp, computed here
    explicitly -- never a silent substitution.
* Coverage verified by listing: pressure-level months 1940-01 through
  2026-07 (2026-08 empty).  The screened pool spans 1998-06-17 to
  2026-07-27, so every event is inside coverage; eligibility is still
  computed mechanically from ``ERA5_SPEC``.

EXTRACTION SEMANTICS (documented choices):
* Spatial: nearest ERA5 grid point to the station (0.25 deg grid).  The
  best-match archive responses this replaces are likewise single grid
  cells of comparable-resolution models, so nearest-point is the
  consistent choice; no bilinear smoothing is introduced.
* The cache contract is identical to the best-match fetcher: one
  gzip JSON per planned chunk under ``raw_cache_era5/<station_id>/``
  in the Open-Meteo hourly shape, so ``build-gold-features
  --profile-source era5`` and ``evaluate-gold-models`` run unchanged.
* Conversions: temperature K -> deg C; geopotential m^2/s^2 -> m with
  g = 9.80665; wind speed = hypot(u, v) m/s; snowfall m -> cm (WATER
  EQUIVALENT -- unlike Open-Meteo's snow-depth cm; it feeds only the
  Kuchera interval gate/weight in compute_baseline_slr, where the
  precipitation weight is preferred whenever positive); precipitation
  (lsp + cp) m -> mm.
* Accumulation hours: the value stored at instant t is the
  accumulation over (t-1h, t], differenced from consecutive forecast
  steps of the most recent 06/18 UTC cycle -- the same interval
  convention the baseline code assumes.
* No raw file is ever written to disk: all reads are HDF5 range reads
  over anonymous S3 (h5py + s3fs), batched per file and discarded.
  Concurrency is bounded (default 6 day-workers).
"""

from __future__ import annotations

import gzip
import json
import math
import multiprocessing
import pickle
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .gold_profiles import (
    ERA5_SPEC,
    HOURLY_FIELDS,
    PRESSURE_LEVELS_HPA,
    _cache_valid,
    _chunk_path,
    load_screened_events,
    plan_chunks,
)

ERA5_PROFILE_SOURCE = ERA5_SPEC.source_id
BUCKET = "nsf-ncar-era5"
GRAVITY_M_S2 = 9.80665
EPOCH_1900 = datetime(1900, 1, 1, tzinfo=timezone.utc)
FILL_LIMIT = 9.9e19

# variable short name -> (GRIB code prefix, filename token, grid suffix, HDF5 variable)
PL_VARIABLES = {
    "z": ("128_129", "z", "ll025sc", "Z"),
    "t": ("128_130", "t", "ll025sc", "T"),
    "u": ("128_131", "u", "ll025uv", "U"),
    "v": ("128_132", "v", "ll025uv", "V"),
    "r": ("128_157", "r", "ll025sc", "R"),
}
ACCUMU_VARIABLES = {
    "lsp": ("128_142", "lsp", "LSP"),
    "cp": ("128_143", "cp", "CP"),
    "sf": ("128_144", "sf", "SF"),
}


# ---------------------------------------------------------------------------
# Object keys (verified against live listings, 2026-10-20)
# ---------------------------------------------------------------------------

def pl_key(day: date, variable: str) -> str:
    code, token, grid, _hdf5_name = PL_VARIABLES[variable]
    return (
        f"e5.oper.an.pl/{day:%Y%m}/e5.oper.an.pl.{code}_{token}.{grid}."
        f"{day:%Y%m%d}00_{day:%Y%m%d}23.nc"
    )


def sfc_2t_key(year: int, month: int) -> str:
    import calendar

    last_day = calendar.monthrange(year, month)[1]
    return (
        f"e5.oper.an.sfc/{year:04d}{month:02d}/e5.oper.an.sfc.128_167_2t.ll025sc."
        f"{year:04d}{month:02d}0100_{year:04d}{month:02d}{last_day:02d}23.nc"
    )


def accumu_span_start(init: datetime) -> datetime:
    """Start instant of the half-month accumulation file holding ``init``.

    Files span [1st 06Z, 16th 06Z) and [16th 06Z, next month 1st 06Z);
    forecast inits are the 06/18 UTC cycles.
    """
    boundary = init.replace(day=16, hour=6, minute=0, second=0, microsecond=0)
    if init >= boundary:
        return boundary
    return init.replace(day=1, hour=6, minute=0, second=0, microsecond=0)


def _next_span_start(span_start: datetime) -> datetime:
    if span_start.day == 1:
        return span_start.replace(day=16)
    if span_start.month == 12:
        return span_start.replace(year=span_start.year + 1, month=1, day=1)
    return span_start.replace(month=span_start.month + 1, day=1)


def accumu_key(span_start: datetime, variable: str) -> str:
    code, token, _hdf5_name = ACCUMU_VARIABLES[variable]
    span_end = _next_span_start(span_start)
    return (
        f"e5.oper.fc.sfc.accumu/{span_start:%Y%m}/e5.oper.fc.sfc.accumu.{code}_{token}.ll025sc."
        f"{span_start:%Y%m%d%H}_{span_end:%Y%m%d%H}.nc"
    )


# ---------------------------------------------------------------------------
# Time / grid helpers
# ---------------------------------------------------------------------------

def hours_since_1900(instant: datetime) -> int:
    delta = instant - EPOCH_1900
    return int(delta.total_seconds() // 3600)


def accumulation_init(valid: datetime) -> tuple[datetime, int]:
    """(forecast init, forecast hour) whose step difference covers (valid-1h, valid].

    ERA5 short forecasts initialise at 06 and 18 UTC with hourly steps
    1..12; exactly one cycle covers any valid hour.
    """
    hour = valid.hour
    if 7 <= hour <= 18:
        init = valid.replace(hour=6, minute=0, second=0, microsecond=0)
        return init, hour - 6
    if hour >= 19:
        init = valid.replace(hour=18, minute=0, second=0, microsecond=0)
        return init, hour - 18
    init = (valid - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    return init, hour + 6


def _nearest_index(coordinates: list[float], value: float) -> int:
    """Index of the nearest coordinate; coordinates may ascend or descend."""
    best = 0
    best_distance = abs(coordinates[0] - value)
    for index in range(1, len(coordinates)):
        distance = abs(coordinates[index] - value)
        if distance < best_distance:
            best, best_distance = index, distance
    return best


def _clean(value: float) -> float | None:
    if not math.isfinite(value) or abs(value) >= FILL_LIMIT:
        return None
    return value


def read_points(dataset: Any, points: list[tuple[int, ...]]) -> list[float | None]:
    """Read scattered cells from an HDF5 dataset, fetching each touched chunk once.

    ``dataset`` is an h5py Dataset.  Chunk-aligned block reads keep the
    S3 range traffic proportional to the number of distinct chunks the
    points touch, not to the number of points.
    """
    if not points:
        return []
    chunks = tuple(dataset.chunks) if dataset.chunks else tuple(dataset.shape)
    grouped: dict[tuple[int, ...], list[int]] = defaultdict(list)
    for index, point in enumerate(points):
        grouped[tuple(p // c for p, c in zip(point, chunks))].append(index)
    values: list[float | None] = [None] * len(points)
    for chunk_key, indices in grouped.items():
        starts = tuple(k * c for k, c in zip(chunk_key, chunks))
        stops = tuple(min(s + c, dataset.shape[d]) for d, (s, c) in enumerate(zip(starts, chunks)))
        block = dataset[tuple(slice(s, e) for s, e in zip(starts, stops))]
        for index in indices:
            point = points[index]
            local = tuple(p - s for p, s in zip(point, starts))
            values[index] = _clean(float(block[local]))
    return values


# ---------------------------------------------------------------------------
# S3 reader (thread-local filesystem, small LRU of open files)
# ---------------------------------------------------------------------------

class Era5Reader:
    """Anonymous range-read access to the nsf-ncar-era5 bucket.

    One instance is shared across worker threads; the s3fs filesystem
    and open HDF5 handles are thread-local (h5py handles are not safe
    to share across threads).
    """

    MAX_OPEN_FILES = 16

    def __init__(self) -> None:
        self._local = threading.local()

    def _fs(self) -> Any:
        import s3fs

        fs = getattr(self._local, "fs", None)
        if fs is None:
            fs = s3fs.S3FileSystem(anon=True)
            self._local.fs = fs
        return fs

    def _open(self, key: str) -> Any:
        import h5py

        files: dict[str, Any] = getattr(self._local, "files", None)
        if files is None:
            files = {}
            self._local.files = files
            self._local.coords = {}
        if key not in files:
            if len(files) >= self.MAX_OPEN_FILES:
                for handle in files.values():
                    handle.close()
                files.clear()
                self._local.coords.clear()
            files[key] = h5py.File(self._fs().open(f"{BUCKET}/{key}", "rb"), "r")
        return files[key]

    def _coords(self, key: str) -> dict[str, Any]:
        handle = self._open(key)  # also initialises the thread-local stores
        coords: dict[str, Any] = self._local.coords
        if key not in coords:
            entry: dict[str, Any] = {}
            for name in ("time", "forecast_initial_time", "forecast_hour", "level", "latitude", "longitude"):
                if name in handle:
                    entry[name] = [float(v) for v in handle[name][()]]
            coords[key] = entry
        return coords[key]

    def _grid_indices(self, key: str, stations: list[tuple[str, float, float]]) -> tuple[list[int], list[int]]:
        coords = self._coords(key)
        latitudes = coords["latitude"]
        longitudes = coords["longitude"]
        lat_idx = [_nearest_index(latitudes, lat) for _sid, lat, _lon in stations]
        lon_idx = [_nearest_index(longitudes, lon % 360.0) for _sid, _lat, lon in stations]
        return lat_idx, lon_idx

    # -- pressure levels ----------------------------------------------------

    def read_pl_day(
        self,
        day: date,
        variable: str,
        stations: list[tuple[str, float, float]],
    ) -> dict[tuple[str, datetime], dict[int, float | None]]:
        """One variable, one day: {(station_id, instant): {level_hpa: value}}."""
        key = pl_key(day, variable)
        handle = self._open(key)
        coords = self._coords(key)
        _code, _token, _grid, hdf5_name = PL_VARIABLES[variable]
        dataset = handle[hdf5_name]
        level_values = [int(v) for v in coords["level"]]
        level_index = {level: level_values.index(level) for level in PRESSURE_LEVELS_HPA}
        day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        assert int(coords["time"][0]) == hours_since_1900(day_start)  # file starts 00 UTC
        lat_idx, lon_idx = self._grid_indices(key, stations)
        points: list[tuple[int, ...]] = []
        slots: list[tuple[str, datetime, int]] = []
        for hour in range(24):
            instant = day_start + timedelta(hours=hour)
            for level in PRESSURE_LEVELS_HPA:
                for station_pos, (station_id, _lat, _lon) in enumerate(stations):
                    points.append((hour, level_index[level], lat_idx[station_pos], lon_idx[station_pos]))
                    slots.append((station_id, instant, level))
        values = read_points(dataset, points)
        out: dict[tuple[str, datetime], dict[int, float | None]] = {}
        for (station_id, instant, level), value in zip(slots, values):
            out.setdefault((station_id, instant), {})[level] = value
        return out

    # -- 2 m temperature ------------------------------------------------------

    def read_2t(
        self,
        instants: list[datetime],
        stations: list[tuple[str, float, float]],
    ) -> dict[tuple[str, datetime], float | None]:
        """2 m temperature (K) at the given instants; instants may span months."""
        by_month: dict[str, list[datetime]] = defaultdict(list)
        for instant in instants:
            by_month[f"{instant:%Y%m}"].append(instant)
        out: dict[tuple[str, datetime], float | None] = {}
        for _month, month_instants in by_month.items():
            first = month_instants[0]
            key = sfc_2t_key(first.year, first.month)
            handle = self._open(key)
            coords = self._coords(key)
            dataset = handle["VAR_2T"]
            time0 = int(coords["time"][0])
            lat_idx, lon_idx = self._grid_indices(key, stations)
            points: list[tuple[int, ...]] = []
            slots: list[tuple[str, datetime]] = []
            for instant in month_instants:
                time_index = hours_since_1900(instant) - time0
                if not 0 <= time_index < dataset.shape[0]:
                    continue
                for station_pos, (station_id, _lat, _lon) in enumerate(stations):
                    points.append((time_index, lat_idx[station_pos], lon_idx[station_pos]))
                    slots.append((station_id, instant))
            for slot, value in zip(slots, read_points(dataset, points)):
                out[slot] = value
        return out

    # -- accumulations --------------------------------------------------------

    def read_accumu_cumulative(
        self,
        variable: str,
        requests: list[tuple[datetime, int, str, float, float]],
    ) -> dict[tuple[str, datetime, int], float | None]:
        """Cumulative accumulation values for (init, forecast_hour, station) requests.

        Each request is (init, forecast_hour, station_id, latitude,
        longitude).  Returns {(station_id, init, forecast_hour): value}.
        """
        by_span: dict[datetime, list[tuple[datetime, int, str, float, float]]] = defaultdict(list)
        for request in requests:
            by_span[accumu_span_start(request[0])].append(request)
        _code, _token, hdf5_name = ACCUMU_VARIABLES[variable]
        out: dict[tuple[str, datetime, int], float | None] = {}
        for span_start, span_requests in by_span.items():
            key = accumu_key(span_start, variable)
            handle = self._open(key)
            coords = self._coords(key)
            dataset = handle[hdf5_name]
            init_index = {int(v): i for i, v in enumerate(coords["forecast_initial_time"])}
            hour_index = {int(v): i for i, v in enumerate(coords["forecast_hour"])}
            stations = sorted({(r[2], r[3], r[4]) for r in span_requests})
            lat_idx, lon_idx = self._grid_indices(key, list(stations))
            station_pos = {sid: i for i, (sid, _la, _lo) in enumerate(stations)}
            points: list[tuple[int, ...]] = []
            slots: list[tuple[str, datetime, int]] = []
            for init, forecast_hour, station_id, _lat, _lon in span_requests:
                pos = station_pos[station_id]
                points.append((init_index[hours_since_1900(init)], hour_index[forecast_hour], lat_idx[pos], lon_idx[pos]))
                slots.append((station_id, init, forecast_hour))
            for slot, value in zip(slots, read_points(dataset, points)):
                out[slot] = value
        return out


# ---------------------------------------------------------------------------
# Day assembly: raw ERA5 -> per-station hour records
# ---------------------------------------------------------------------------

def hourly_accumulation(
    cumulative: dict[tuple[str, datetime, int], float | None],
    station_id: str,
    valid: datetime,
) -> float | None:
    """Accumulation over (valid-1h, valid] from forecast-step differences."""
    init, forecast_hour = accumulation_init(valid)
    current = cumulative.get((station_id, init, forecast_hour))
    if current is None:
        return None
    if forecast_hour == 1:
        previous = 0.0
    else:
        previous = cumulative.get((station_id, init, forecast_hour - 1))
        if previous is None:
            return None
    return max(0.0, current - previous)


def read_station_day(
    reader: Era5Reader,
    day: date,
    stations: list[tuple[str, float, float]],
) -> dict[str, dict[datetime, dict[str, Any]]]:
    """All hour records for one calendar day: {station_id: {instant: record}}.

    A record mirrors the fields build-gold-features consumes:
    temperature2mC, precipitationMm, snowfallCm and per-level
    (temperature C, rh %, wind m/s, geopotential height m).
    Raises on any source-file error so the caller can retry the day.
    """
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    instants = [day_start + timedelta(hours=h) for h in range(24)]
    levels_by_var = {var: reader.read_pl_day(day, var, stations) for var in PL_VARIABLES}
    t2m = reader.read_2t(instants, stations)

    accumu_requests: list[tuple[datetime, int, str, float, float]] = []
    for station_id, lat, lon in stations:
        for instant in instants:
            init, forecast_hour = accumulation_init(instant)
            accumu_requests.append((init, forecast_hour, station_id, lat, lon))
            if forecast_hour > 1:
                accumu_requests.append((init, forecast_hour - 1, station_id, lat, lon))
    cumulative = {
        var: reader.read_accumu_cumulative(var, accumu_requests)
        for var in ACCUMU_VARIABLES
    }

    out: dict[str, dict[datetime, dict[str, Any]]] = {sid: {} for sid, _la, _lo in stations}
    for station_id, _lat, _lon in stations:
        for instant in instants:
            levels: dict[int, tuple[float | None, float | None, float | None, float | None]] = {}
            for level in PRESSURE_LEVELS_HPA:
                temp_k = levels_by_var["t"][(station_id, instant)][level]
                rh = levels_by_var["r"][(station_id, instant)][level]
                u = levels_by_var["u"][(station_id, instant)][level]
                v = levels_by_var["v"][(station_id, instant)][level]
                geopotential = levels_by_var["z"][(station_id, instant)][level]
                wind = math.hypot(u, v) if u is not None and v is not None else None
                levels[level] = (
                    temp_k - 273.15 if temp_k is not None else None,
                    rh,
                    wind,
                    geopotential / GRAVITY_M_S2 if geopotential is not None else None,
                )
            sf_m = hourly_accumulation(cumulative["sf"], station_id, instant)
            lsp_m = hourly_accumulation(cumulative["lsp"], station_id, instant)
            cp_m = hourly_accumulation(cumulative["cp"], station_id, instant)
            precip_mm = (lsp_m + cp_m) * 1000.0 if lsp_m is not None and cp_m is not None else None
            t2m_k = t2m.get((station_id, instant))
            out[station_id][instant] = {
                "temperature2mC": t2m_k - 273.15 if t2m_k is not None else None,
                "precipitationMm": precip_mm,
                "snowfallCm": sf_m * 100.0 if sf_m is not None else None,
                "levels": levels,
            }
    return out


# ---------------------------------------------------------------------------
# Chunk payload assembly (identical cache contract to the best-match fetcher)
# ---------------------------------------------------------------------------

def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


ERA5_ALIGNMENT = (
    "ERA5 (NSF NCAR AWS mirror, era5-nsf-ncar-aws): nearest 0.25 deg grid point; "
    "24-h observation window ending at the stored valid_time"
)


def build_chunk_payload(
    chunk: dict[str, Any],
    hours: dict[datetime, dict[str, Any]],
    profile_source: str = ERA5_PROFILE_SOURCE,
    profile_model: str = "ERA5",
    profile_alignment: str = ERA5_ALIGNMENT,
) -> dict[str, Any] | None:
    """Open-Meteo-shaped payload for one chunk, or None if any hour is absent."""
    fields: dict[str, list[Any]] = {name: [] for name in HOURLY_FIELDS}
    times: list[str] = []
    day = date.fromisoformat(chunk["start"])
    last = date.fromisoformat(chunk["end"])
    while day <= last:
        for hour in range(24):
            instant = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            record = hours.get(instant)
            if record is None:
                return None
            times.append(instant.strftime("%Y-%m-%dT%H:%M"))
            fields["temperature_2m"].append(_round(record["temperature2mC"]))
            fields["precipitation"].append(_round(record["precipitationMm"]))
            fields["snowfall"].append(_round(record["snowfallCm"]))
            for level in PRESSURE_LEVELS_HPA:
                temp_c, rh, wind, height = record["levels"][level]
                fields[f"temperature_{level}hPa"].append(_round(temp_c))
                fields[f"relative_humidity_{level}hPa"].append(_round(rh))
                fields[f"wind_speed_{level}hPa"].append(_round(wind))
                fields[f"geopotential_height_{level}hPa"].append(_round(height))
        day += timedelta(days=1)
    return {
        "hourly": {"time": times, **fields},
        "profile_source": profile_source,
        "profile_model": profile_model,
        "profile_alignment": profile_alignment,
    }


# ---------------------------------------------------------------------------
# Fetch orchestration: batch by calendar day, assemble planned chunks
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Process-pool day reads
# ---------------------------------------------------------------------------

_WORKER_READER: Era5Reader | None = None


def _read_day_in_worker(
    day_iso: str,
    stations: list[tuple[str, float, float]],
    reader: Era5Reader | None,
) -> tuple[str, dict[str, dict[datetime, dict[str, Any]]] | None, str | None]:
    """Read one calendar day in a worker process (process executor only).

    Returns ``(day_iso, day_data, None)`` on success, or ``(day_iso,
    None, "TypeName: message")`` after the same 3-attempt retry schedule
    with the same backoff the threaded path uses (5 s, 20 s, 45 s).
    Only the day read -- the GIL-bound s3fs/HDF5 work -- leaves the
    parent process; planning, the shared store, chunk assembly/writing,
    progress prints and the summary all stay in the parent, so process
    mode produces exactly the threaded mode's outputs.

    Reader construction: with ``reader=None`` the worker builds its own
    Era5Reader -- one per worker process, kept in the module global
    ``_WORKER_READER`` -- because the real reader's s3fs filesystem is
    built lazily per thread and must never be pickled across a process
    boundary.  An INJECTED reader (the test seam used by
    ``fetch_profiles_era5``) is passed through as the task payload
    instead: it must be picklable, which the parent checks up front and
    falls back to threaded reads if not.  The stateless fakes in the
    test suite are picklable, so the process/thread equality test runs
    the real process path.
    """
    global _WORKER_READER
    active = reader
    if active is None:
        if _WORKER_READER is None:
            _WORKER_READER = Era5Reader()
        active = _WORKER_READER
    day = date.fromisoformat(day_iso)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return day_iso, read_station_day(active, day, stations), None
        except Exception as exc:  # retried: S3 throttling / transient I/O
            last_error = exc
            time.sleep(5.0 * (attempt + 1) ** 2)
    return day_iso, None, f"{type(last_error).__name__}: {last_error}"


def fetch_profiles_era5(
    events: list[dict[str, Any]],
    cache_dir: Path,
    max_workers: int = 6,
    event_ids: set[str] | None = None,
    reader: Era5Reader | None = None,
    executor: str = "thread",
) -> dict[str, Any]:
    """Download ERA5 profiles for all eligible event windows into the chunk cache.

    Resumable exactly like the best-match fetcher: chunks whose cache
    file already validates are skipped.  Work is organised by calendar
    day (pressure-level files are whole-global-hour chunked, so a day
    is read once for every station that needs it).  A day that fails
    after retries leaves its chunks unwritten for the next run.
    ``event_ids`` restricts downloads to chunks containing at least one
    of the given events (chunk boundaries still come from the full
    pool, so pilot caches are identical to campaign caches).

    ``executor`` selects how day reads are parallelised: ``"thread"``
    (default; the historical behaviour -- worker threads in this
    process sharing ``reader``) or ``"process"`` (a ProcessPoolExecutor
    whose workers run ``_read_day_in_worker``; the parent still plans,
    merges, assembles and writes every chunk).  The threaded reader's
    throughput is bounded by the GIL at roughly one core; process mode
    exists to put the decompression/extraction work on every core.
    """
    if executor not in ("thread", "process"):
        raise ValueError(f"executor must be 'thread' or 'process', got {executor!r}")
    chunks = plan_chunks(events, spec=ERA5_SPEC)
    if event_ids is not None:
        chunks = [c for c in chunks if event_ids.intersection(c["event_ids"])]
    eligible = {eid for c in chunks for eid in c["event_ids"]}
    cache_dir.mkdir(parents=True, exist_ok=True)
    pending = [c for c in chunks if not _cache_valid(_chunk_path(cache_dir, c))]
    already = len(chunks) - len(pending)
    injected_reader = reader
    reader = reader or Era5Reader()

    station_coords: dict[str, tuple[float, float]] = {}
    for chunk in chunks:
        station_coords[chunk["station_id"]] = (chunk["latitude"], chunk["longitude"])

    chunk_days: dict[str, set[date]] = {}
    day_chunks: dict[date, set[str]] = defaultdict(set)
    for chunk in pending:
        days = set()
        day = date.fromisoformat(chunk["start"])
        last = date.fromisoformat(chunk["end"])
        while day <= last:
            days.add(day)
            day_chunks[day].add(chunk["key"])
            day += timedelta(days=1)
        chunk_days[chunk["key"]] = days

    day_stations: dict[date, list[tuple[str, float, float]]] = {}
    for needed_day in day_chunks:
        station_ids = sorted({c["station_id"] for c in pending if needed_day in chunk_days[c["key"]]})
        day_stations[needed_day] = [(sid, *station_coords[sid]) for sid in station_ids]
    by_key = {c["key"]: c for c in pending}

    store: dict[tuple[str, datetime], dict[str, Any]] = {}
    lock = threading.Lock()
    state = {"written": 0, "days_done": 0}
    failed_days: list[date] = []

    def absorb(
        day: date,
        day_data: dict[str, dict[datetime, dict[str, Any]]] | None,
        error_text: str | None,
    ) -> None:
        """Merge one finished day read (from either executor) and write
        any chunks it completes.  Runs in the parent under the lock, so
        store mutation and chunk writes stay single-threaded and the
        outputs are identical whichever executor produced the read."""
        with lock:
            if day_data is None:
                failed_days.append(day)
                print(
                    f"[era5] day {day.isoformat()} failed after retries: "
                    f"{error_text}",
                    file=sys.stderr,
                )
                return
            for station_id, hours in day_data.items():
                for instant, record in hours.items():
                    store[(station_id, instant)] = record
            state["days_done"] += 1
            completed = []
            for chunk_key in day_chunks[day]:
                chunk_days[chunk_key].discard(day)
                if not chunk_days[chunk_key]:
                    completed.append(chunk_key)
            for chunk_key in completed:
                chunk = by_key[chunk_key]
                station_hours = {
                    instant: record
                    for (sid, instant), record in store.items()
                    if sid == chunk["station_id"]
                }
                payload = build_chunk_payload(chunk, station_hours)
                if payload is None:
                    continue
                path = _chunk_path(cache_dir, chunk)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                with gzip.open(tmp, "wt", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                tmp.replace(path)
                state["written"] += 1
                for instant in list(station_hours):
                    store.pop((chunk["station_id"], instant), None)
            done_days = state["days_done"]
            if done_days % 25 == 0:
                print(
                    f"[era5] progress: {done_days}/{len(day_chunks)} days, "
                    f"{state['written']} chunks written",
                    file=sys.stderr,
                )

    def work(day: date) -> None:
        day_data: dict[str, dict[datetime, dict[str, Any]]] | None = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                day_data = read_station_day(reader, day, day_stations[day])
                break
            except Exception as exc:  # retried: S3 throttling / transient I/O
                last_error = exc
                time.sleep(5.0 * (attempt + 1) ** 2)
        absorb(
            day,
            day_data,
            None if day_data is not None else f"{type(last_error).__name__}: {last_error}",
        )

    # Process mode dispatches day reads to worker processes.  An
    # injected reader may only cross the process boundary if it is
    # picklable (the parent checks; the real Era5Reader is never
    # injected by production callers, and workers build their own when
    # the payload carries None).  An unpicklable injected reader falls
    # back to threaded reads with a logged note rather than crashing.
    use_process = executor == "process"
    worker_reader: Era5Reader | None = None
    if use_process and injected_reader is not None:
        try:
            pickle.dumps(injected_reader)
            worker_reader = injected_reader
        except Exception:
            print(
                "[era5] injected reader is not picklable; "
                "process executor falls back to threaded day reads",
                file=sys.stderr,
            )
            use_process = False

    days = sorted(day_chunks)
    if days:
        if use_process:
            context = multiprocessing.get_context("fork")
            with ProcessPoolExecutor(max_workers=max_workers, mp_context=context) as pool:
                futures = {
                    pool.submit(
                        _read_day_in_worker,
                        day.isoformat(),
                        day_stations[day],
                        worker_reader,
                    ): day
                    for day in days
                }
                for future in as_completed(futures):
                    _day_iso, day_data, error_text = future.result()
                    absorb(futures[future], day_data, error_text)
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [pool.submit(work, day) for day in days]
                for future in as_completed(futures):
                    future.result()
    cached_total = sum(1 for c in chunks if _cache_valid(_chunk_path(cache_dir, c)))
    return {
        "profileSource": ERA5_PROFILE_SOURCE,
        "eventsLoaded": len(events),
        "eventsEligible": len(eligible),
        "chunksPlanned": len(chunks),
        "chunksAlreadyCached": already,
        "chunksDownloadedThisRun": state["written"],
        "chunksFailedThisRun": len(pending) - state["written"],
        "chunksCachedTotal": cached_total,
        "daysPlanned": len(days),
        "daysFailed": len(failed_days),
        "failedDays": [d.isoformat() for d in sorted(failed_days)[:20]],
    }


def run_fetch_profiles_era5(
    pool_dir: Path,
    output_dir: Path,
    max_workers: int = 6,
    event_ids: set[str] | None = None,
    executor: str = "thread",
) -> dict[str, Any]:
    events = load_screened_events(pool_dir)
    cache_dir = output_dir / ERA5_SPEC.cache_dir_name
    return fetch_profiles_era5(
        events,
        cache_dir,
        max_workers=max_workers,
        event_ids=event_ids,
        executor=executor,
    )
