"""Year expansion: ERA5 profile fetching through the Copernicus CDS API.

SOURCE (decided 2026-10-24): the step-2 gold pool spans 1998-2026, but
the Open-Meteo best-match archive starts 2021-06, so 18,507 of 58,322
screened events could be modelled.  ERA5 covers the whole pool.  The
NSF NCAR AWS mirror route (``gold_profiles_era5``, source
``era5-nsf-ncar-aws``) works but reads whole-global-hour HDF5 chunks
(~24 TB of range traffic for the full pool).  The Copernicus Climate
Data Store subsets server-side, so the same profiles cost ~44 GB of
packed NetCDF instead -- at the price of a queued API and the user's
personal CDS key.  Provenance here is ``era5-cds`` (see ``CDS_SPEC``).

CREDENTIALS: the ONLY credential path is the standard ``cdsapi``
client reading the user's own ``~/.cdsapirc`` (written by the user
personally; the key never transits this codebase, its logs, or its
tests).  Nothing in this module opens that file.

REQUEST DESIGN (the CDS queue is the constraint, not bandwidth):
* Work is batched by CALENDAR DAY and REGION: for each (day, region)
  that has pending chunks, exactly two requests are submitted -- one
  ``reanalysis-era5-pressure-levels`` request (temperature,
  relative_humidity, u/v wind, geopotential on the same 13 levels,
  24 hourly steps) and one ``reanalysis-era5-single-levels`` request
  (2m temperature + the accumulated lsp/cp/sf fields), each subset to
  a 0.25 deg box snapped outward around that day's stations in the
  region (median box 260 grid points; the day a storm hits, its
  stations cluster, so boxes stay small).
* A satellite day-cache (gzip JSON per day+region under
  ``cds_day_cache/`` next to the chunk cache) persists every parsed
  day, so the campaign resumes at day granularity: a completed request
  is never re-submitted, even across process restarts.
* Jobs run oldest-day-first, so the pre-2021 events (the point of the
  expansion) land first.  Concurrency is bounded (default 3 in-flight
  jobs); each worker owns its cdsapi client (clients are not shared
  across threads); transient failures retry with exponential backoff;
  licence/auth errors are classified FATAL and stop the campaign
  immediately -- re-submitting a request the API will never accept
  just burns queue goodwill.
* Totals for the full pool (measured on the 2026-10-24 plan):
  13,307 day-region jobs = 26,614 requests, ~44 GB raw value volume
  (pressure levels 41.5 GB + single levels 2.6 GB before NetCDF
  packing).  Pre-2021 subset: 9,729 jobs = 19,458 requests, ~31 GB.

EXTRACTION SEMANTICS (mirrors gold_profiles_era5 unless noted):
* Spatial: nearest ERA5 grid point to the station (0.25 deg grid).
* The cache contract is identical: one gzip JSON per planned chunk
  under ``raw_cache_cds/<station_id>/`` in the Open-Meteo hourly
  shape, so ``build-gold-features --profile-source cds`` runs
  unchanged.  Chunk payloads are assembled from the day-cache once
  every day of a chunk is present.
* Conversions: temperature K -> deg C; geopotential m^2/s^2 -> m with
  g = 9.80665; wind speed = hypot(u, v) m/s; snowfall m -> cm (WATER
  EQUIVALENT, as in the NCAR route); precipitation (lsp + cp) m -> mm.
  There is no total-precipitation variable requested: tp = lsp + cp
  is the ECMWF identity, computed explicitly, exactly as in the NCAR
  module.
* Accumulation hours: DOCUMENTED DEVIATION from the NCAR route.  The
  NCAR mirror's accumulation files derive from the 06/18 UTC forecast
  cycles; CDS ERA5 accumulations derive from the 00/12 UTC cycles.
  The stored value at instant t is in both cases differenced against
  the previous step of the SAME cycle to give the accumulation over
  (t-1h, t] -- the interval the baseline code assumes -- so the hour
  records are equivalent; only the cycle arithmetic differs
  (``accumulation_init_cds``).  Differencing happens at chunk
  assembly time from the persisted cumulative values, so every
  interior day boundary resolves exactly; the single 00:00 hour at a
  chunk's padded first day may carry null precipitation/snowfall
  (padding only -- no event window reaches it).
* Downloads land in a temp directory and are parsed in memory; the
  raw NetCDF results are not retained (the day-cache is the durable
  artefact).  The new CDS may deliver a result as a zip split by
  stepType (instant/accum members); the parser merges members.
"""

from __future__ import annotations

import gzip
import json
import math
import sys
import tempfile
import threading
import time
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .gold_profiles import (
    CDS_PROFILE_SOURCE,
    CDS_SPEC,
    PRESSURE_LEVELS_HPA,
    _cache_valid,
    _chunk_path,
    load_screened_events,
    plan_chunks,
)
from .gold_profiles_era5 import (
    GRAVITY_M_S2,
    _clean,
    _nearest_index,
    build_chunk_payload,
)

CDS_PROFILE_SOURCE_ID = CDS_PROFILE_SOURCE
PL_DATASET = "reanalysis-era5-pressure-levels"
SL_DATASET = "reanalysis-era5-single-levels"
PL_VARIABLES = (
    "geopotential",
    "relative_humidity",
    "temperature",
    "u_component_of_wind",
    "v_component_of_wind",
)
SL_VARIABLES = (
    "2m_temperature",
    "convective_precipitation",
    "large_scale_precipitation",
    "snowfall",
)
PL_SHORT_NAMES = ("t", "r", "u", "v", "z")
SL_SHORT_NAMES = ("t2m", "lsp", "cp", "sf")
TIMES = tuple(f"{hour:02d}:00" for hour in range(24))
BOX_PAD_DEG = 0.75
GRID_DEG = 0.25
CDS_ALIGNMENT = (
    "ERA5 (Copernicus CDS, era5-cds): nearest 0.25 deg grid point; "
    "24-h observation window ending at the stored valid_time"
)
ACCUMU_FIELDS = (("lsp", "lspCumM"), ("cp", "cpCumM"), ("sf", "sfCumM"))

# Errors that re-submitting will never fix (licence not accepted for
# the dataset, bad credentials, malformed request): stop at once.
_FATAL_MARKERS = (
    "licence",
    "license",
    "terms and conditions",
    "not accepted",
    "not entitled",
    "unauthorized",
    "unauthorised",
    "authentication failed",
    "invalid api key",
    # cdsapi's own wording when ~/.cdsapirc is missing/incomplete --
    # without credentials every job would fail identically.
    "configuration file",
    "cdsapirc",
    "personal access token",
)


class FatalCdsError(RuntimeError):
    """A CDS failure that retrying cannot fix (licence/auth/request)."""


# ---------------------------------------------------------------------------
# Request construction
# ---------------------------------------------------------------------------

def _snap_down(value: float) -> float:
    return math.floor(value / GRID_DEG) * GRID_DEG


def _snap_up(value: float) -> float:
    return math.ceil(value / GRID_DEG) * GRID_DEG


def region_box(stations: list[tuple[str, float, float]]) -> tuple[float, float, float, float]:
    """CDS ``area`` box [north, west, south, east] around one day's stations.

    Padded by BOX_PAD_DEG and snapped OUTWARD to the 0.25 deg ERA5
    grid, so the nearest grid point of every station is inside.
    """
    latitudes = [lat for _sid, lat, _lon in stations]
    longitudes = [lon for _sid, _lat, lon in stations]
    north = _snap_up(max(latitudes) + BOX_PAD_DEG)
    south = _snap_down(min(latitudes) - BOX_PAD_DEG)
    west = _snap_down(min(longitudes) - BOX_PAD_DEG)
    east = _snap_up(max(longitudes) + BOX_PAD_DEG)
    return (round(north, 2), round(west, 2), round(south, 2), round(east, 2))


def _base_request(day: date, box: tuple[float, float, float, float]) -> dict[str, Any]:
    return {
        "product_type": ["reanalysis"],
        "year": [f"{day:%Y}"],
        "month": [f"{day:%m}"],
        "day": [f"{day:%d}"],
        "time": list(TIMES),
        "area": list(box),
        "data_format": "netcdf",
    }


def pl_request(day: date, box: tuple[float, float, float, float]) -> dict[str, Any]:
    request = _base_request(day, box)
    request["variable"] = list(PL_VARIABLES)
    request["pressure_level"] = [str(level) for level in PRESSURE_LEVELS_HPA]
    return request


def sl_request(day: date, box: tuple[float, float, float, float]) -> dict[str, Any]:
    request = _base_request(day, box)
    request["variable"] = list(SL_VARIABLES)
    return request


# ---------------------------------------------------------------------------
# Accumulation cycles (CDS ERA5: forecasts initialise 00/12 UTC)
# ---------------------------------------------------------------------------

def accumulation_init_cds(valid: datetime) -> tuple[datetime, int]:
    """(forecast init, forecast hour) whose step difference covers (valid-1h, valid].

    CDS ERA5 accumulated fields come from the 00 and 12 UTC forecast
    cycles with hourly steps 1..12; exactly one cycle covers any valid
    hour.  Same convention as gold_profiles_era5.accumulation_init,
    different cycles (06/18 there).
    """
    hour = valid.hour
    if 1 <= hour <= 12:
        init = valid.replace(hour=0, minute=0, second=0, microsecond=0)
        return init, hour
    if hour >= 13:
        init = valid.replace(hour=12, minute=0, second=0, microsecond=0)
        return init, hour - 12
    init = (valid - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    return init, 12


def hourly_accumulation_cds(
    cumulative: dict[tuple[str, datetime, int], float | None],
    station_id: str,
    valid: datetime,
) -> float | None:
    """Accumulation over (valid-1h, valid] from forecast-step differences."""
    init, forecast_hour = accumulation_init_cds(valid)
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


# ---------------------------------------------------------------------------
# CDS client access (the only credential path: cdsapi reads ~/.cdsapirc)
# ---------------------------------------------------------------------------

def cds_credentials_present() -> bool:
    """Whether cdsapi has any credential source configured.

    Existence check ONLY -- the rc file's contents are never read by
    this codebase (the cdsapi client reads them itself).  cdsapi 0.7
    constructs a client happily with no credentials and then burns
    minutes inside retrieve() on doomed anonymous submissions, so the
    fetcher checks this first and stops immediately when the user has
    not written ~/.cdsapirc yet (or set CDSAPI_KEY).
    """
    import os

    if os.environ.get("CDSAPI_KEY"):
        return True
    rc = os.environ.get("CDSAPI_RC")
    path = Path(rc) if rc else Path.home() / ".cdsapirc"
    return path.exists()


def _default_client_factory() -> Any:
    try:
        import cdsapi
    except ImportError as exc:
        raise FatalCdsError("cdsapi is not installed (declare the package's 'cds' extra)") from exc
    try:
        return cdsapi.Client(quiet=True, progress=False, timeout=120)
    except Exception as exc:
        raise FatalCdsError(
            "cdsapi client failed to initialise -- the user's ~/.cdsapirc "
            f"must exist and hold the CDS url + personal token ({type(exc).__name__}: {exc})"
        ) from exc


class _ClientPool:
    """One cdsapi client per worker thread (clients are not thread-safe)."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._local = threading.local()

    def client(self) -> Any:
        client = getattr(self._local, "client", None)
        if client is None:
            client = self._factory()
            self._local.client = client
        return client


def _is_fatal(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _FATAL_MARKERS)


def _retrieve(
    client: Any,
    dataset: str,
    request: dict[str, Any],
    target: Path,
    retries: int = 4,
    backoff_s: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """One queued CDS retrieval with bounded retries; fatal errors stop fast."""
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            client.retrieve(dataset, request, str(target))
            return
        except FatalCdsError:
            raise
        except Exception as exc:  # classified below; cdsapi raises loosely-typed errors
            if _is_fatal(exc):
                raise FatalCdsError(f"{dataset}: {type(exc).__name__}: {exc}") from exc
            last_error = exc
            if attempt + 1 < retries:
                sleep(backoff_s * (2.0 ** attempt))
    assert last_error is not None
    raise last_error


# ---------------------------------------------------------------------------
# NetCDF parsing (xarray; zip results split by stepType are merged)
# ---------------------------------------------------------------------------

def _coord_datetimes(values: Any) -> list[datetime]:
    import numpy as np

    converted = np.asarray(values).astype("datetime64[us]").astype(object)
    out = []
    for value in converted:
        instant = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        out.append(instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant)
    return out


def load_netcdf_variables(path: Path, wanted: Iterable[str]) -> dict[str, Any]:
    """Read the wanted variables of a CDS NetCDF result into plain arrays.

    Returns {"times": [datetime], "levels": [int] | None,
    "latitudes": [float], "longitudes": [float],
    "variables": {short_name: ndarray}} with PL arrays shaped
    (time, level, lat, lon) and SL arrays (time, lat, lon).  A zip
    result (the new CDS splits single-levels output by stepType) is
    unpacked and its members merged.
    """
    import numpy as np
    import xarray as xr

    wanted = list(wanted)
    members: list[Path] = []
    tmpdir: tempfile.TemporaryDirectory | None = None
    if zipfile.is_zipfile(path):
        tmpdir = tempfile.TemporaryDirectory()
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.endswith(".nc"):
                    members.append(Path(archive.extract(name, tmpdir.name)))
    else:
        members = [path]
    merged: dict[str, Any] = {
        "times": None, "levels": None, "latitudes": None, "longitudes": None,
        "variables": {},
    }
    try:
        for member in members:
            with xr.open_dataset(member, engine="h5netcdf") as dataset:
                time_name = "valid_time" if "valid_time" in dataset.coords else "time"
                times = _coord_datetimes(dataset[time_name].values)
                latitudes = [float(v) for v in dataset["latitude"].values]
                longitudes = [float(v) for v in dataset["longitude"].values]
                levels = None
                if "pressure_level" in dataset.coords:
                    levels = [int(v) for v in dataset["pressure_level"].values]
                if merged["times"] is None:
                    merged.update(times=times, levels=levels, latitudes=latitudes, longitudes=longitudes)
                for name in wanted:
                    if name in dataset.variables and name not in merged["variables"]:
                        array = dataset[name]
                        for dim in list(array.dims):
                            if dim not in (time_name, "pressure_level", "latitude", "longitude") and array.sizes[dim] == 1:
                                array = array.isel({dim: 0})
                        merged["variables"][name] = np.asarray(array.values, dtype=float)
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()
    missing = [name for name in wanted if name not in merged["variables"]]
    if missing:
        raise ValueError(f"CDS result {path.name} lacks variables: {', '.join(missing)}")
    return merged


def _nearest_lon_index(longitudes: list[float], lon: float) -> int:
    """Nearest longitude index, safe across the +/-180 wrap and 0..360 grids."""
    target = lon % 360.0
    best = 0
    best_distance = math.inf
    for index, value in enumerate(longitudes):
        distance = abs((value % 360.0) - target)
        distance = min(distance, 360.0 - distance)
        if distance < best_distance:
            best, best_distance = index, distance
    return best


def _grid_value(
    data: dict[str, Any],
    variable: str,
    time_index: int,
    level_index: int | None,
    lat_index: int,
    lon_index: int,
) -> float | None:
    array = data["variables"][variable]
    if level_index is None:
        raw = float(array[time_index, lat_index, lon_index])
    else:
        raw = float(array[time_index, level_index, lat_index, lon_index])
    return _clean(raw)


def extract_day_records(
    pl_data: dict[str, Any],
    sl_data: dict[str, Any],
    day: date,
    stations: list[tuple[str, float, float]],
) -> dict[str, dict[datetime, dict[str, Any]]]:
    """Per-station hour records for one day, in the era5 record shape plus
    the raw cumulative accumulation fields (differenced at assembly).

    Raises on any missing variable/hour so the caller can retry the job.
    """
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    instants = [day_start + timedelta(hours=h) for h in range(24)]
    pl_time_index = {instant: i for i, instant in enumerate(pl_data["times"])}
    sl_time_index = {instant: i for i, instant in enumerate(sl_data["times"])}
    pl_level_index = {level: i for i, level in enumerate(pl_data["levels"] or [])}
    out: dict[str, dict[datetime, dict[str, Any]]] = {}
    for station_id, lat, lon in stations:
        pl_lat = _nearest_index(pl_data["latitudes"], lat)
        pl_lon = _nearest_lon_index(pl_data["longitudes"], lon)
        sl_lat = _nearest_index(sl_data["latitudes"], lat)
        sl_lon = _nearest_lon_index(sl_data["longitudes"], lon)
        records: dict[datetime, dict[str, Any]] = {}
        for instant in instants:
            if instant not in pl_time_index or instant not in sl_time_index:
                raise ValueError(f"CDS result lacks hour {instant.isoformat()}")
            pti = pl_time_index[instant]
            sti = sl_time_index[instant]
            levels: dict[int, tuple[float | None, float | None, float | None, float | None]] = {}
            for level in PRESSURE_LEVELS_HPA:
                li = pl_level_index[level]
                temp_k = _grid_value(pl_data, "t", pti, li, pl_lat, pl_lon)
                rh = _grid_value(pl_data, "r", pti, li, pl_lat, pl_lon)
                u = _grid_value(pl_data, "u", pti, li, pl_lat, pl_lon)
                v = _grid_value(pl_data, "v", pti, li, pl_lat, pl_lon)
                geopotential = _grid_value(pl_data, "z", pti, li, pl_lat, pl_lon)
                wind = math.hypot(u, v) if u is not None and v is not None else None
                levels[level] = (
                    temp_k - 273.15 if temp_k is not None else None,
                    rh,
                    wind,
                    geopotential / GRAVITY_M_S2 if geopotential is not None else None,
                )
            t2m_k = _grid_value(sl_data, "t2m", sti, None, sl_lat, sl_lon)
            records[instant] = {
                "temperature2mC": t2m_k - 273.15 if t2m_k is not None else None,
                "lspCumM": _grid_value(sl_data, "lsp", sti, None, sl_lat, sl_lon),
                "cpCumM": _grid_value(sl_data, "cp", sti, None, sl_lat, sl_lon),
                "sfCumM": _grid_value(sl_data, "sf", sti, None, sl_lat, sl_lon),
                "levels": levels,
            }
        out[station_id] = records
    return out


# ---------------------------------------------------------------------------
# Day-cache: one gzip JSON per (day, region), the resumability unit
# ---------------------------------------------------------------------------

def _day_cache_path(day_cache_dir: Path, region: str, day: date) -> Path:
    return day_cache_dir / region / f"{day.isoformat()}.json.gz"


def _serialize_records(records: dict[datetime, dict[str, Any]]) -> dict[str, Any]:
    return {
        instant.isoformat(): {
            "temperature2mC": record["temperature2mC"],
            "lspCumM": record["lspCumM"],
            "cpCumM": record["cpCumM"],
            "sfCumM": record["sfCumM"],
            "levels": {str(level): list(values) for level, values in record["levels"].items()},
        }
        for instant, record in records.items()
    }


def _deserialize_records(raw: dict[str, Any]) -> dict[datetime, dict[str, Any]]:
    out: dict[datetime, dict[str, Any]] = {}
    for instant_text, record in raw.items():
        instant = datetime.fromisoformat(instant_text)
        out[instant] = {
            "temperature2mC": record["temperature2mC"],
            "lspCumM": record["lspCumM"],
            "cpCumM": record["cpCumM"],
            "sfCumM": record["sfCumM"],
            "levels": {
                int(level): tuple(values) for level, values in record["levels"].items()
            },
        }
    return out


def load_day_cache(
    day_cache_dir: Path, region: str, day: date,
) -> dict[str, dict[datetime, dict[str, Any]]] | None:
    """Station records cached for (day, region), or None if absent/invalid."""
    path = _day_cache_path(day_cache_dir, region, day)
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("day") != day.isoformat() or payload.get("region") != region:
            return None
        return {
            station_id: _deserialize_records(records)
            for station_id, records in payload["stations"].items()
        }
    except Exception:
        return None


def write_day_cache(
    day_cache_dir: Path,
    region: str,
    day: date,
    station_records: dict[str, dict[datetime, dict[str, Any]]],
) -> None:
    """Merge station records into the (day, region) cache file, atomically."""
    existing = load_day_cache(day_cache_dir, region, day) or {}
    existing.update(station_records)
    payload = {
        "day": day.isoformat(),
        "region": region,
        "profile_source": CDS_PROFILE_SOURCE_ID,
        "stations": {
            station_id: _serialize_records(records)
            for station_id, records in existing.items()
        },
    }
    path = _day_cache_path(day_cache_dir, region, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{threading.get_ident()}")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Job planning: pending chunks -> (day, region) jobs, oldest first
# ---------------------------------------------------------------------------

def plan_jobs(
    pending_chunks: list[dict[str, Any]],
    station_region: dict[str, str],
    station_coords: dict[str, tuple[float, float]],
    day_cache_dir: Path,
) -> list[dict[str, Any]]:
    """Group the days of pending chunks into (day, region) fetch jobs.

    Stations already present in a valid day-cache entry are excluded;
    a job is emitted only for the stations still missing.  Sorted by
    day ascending so pre-2021 value lands first.
    """
    needed: dict[tuple[date, str], set[str]] = defaultdict(set)
    for chunk in pending_chunks:
        region = station_region[chunk["station_id"]]
        day = date.fromisoformat(chunk["start"])
        last = date.fromisoformat(chunk["end"])
        while day <= last:
            needed[(day, region)].add(chunk["station_id"])
            day += timedelta(days=1)
    jobs: list[dict[str, Any]] = []
    for (day, region), station_ids in sorted(needed.items()):
        cached = load_day_cache(day_cache_dir, region, day) or {}
        missing = sorted(sid for sid in station_ids if sid not in cached)
        if not missing:
            continue
        stations = [(sid, *station_coords[sid]) for sid in missing]
        jobs.append({
            "day": day,
            "region": region,
            "stations": stations,
            "box": region_box(stations),
        })
    return jobs


# ---------------------------------------------------------------------------
# Chunk assembly: day-cache records -> final hour records -> chunk payload
# ---------------------------------------------------------------------------

def finalize_hours(
    hours_with_cum: dict[datetime, dict[str, Any]],
    station_id: str,
) -> dict[datetime, dict[str, Any]]:
    """Difference the cumulative fields into (t-1h, t] hour records."""
    cumulative: dict[str, dict[tuple[str, datetime, int], float | None]] = {
        var: {} for var, _field in ACCUMU_FIELDS
    }
    for instant, record in hours_with_cum.items():
        init, forecast_hour = accumulation_init_cds(instant)
        for var, field in ACCUMU_FIELDS:
            cumulative[var][(station_id, init, forecast_hour)] = record[field]
    out: dict[datetime, dict[str, Any]] = {}
    for instant, record in hours_with_cum.items():
        lsp_m = hourly_accumulation_cds(cumulative["lsp"], station_id, instant)
        cp_m = hourly_accumulation_cds(cumulative["cp"], station_id, instant)
        sf_m = hourly_accumulation_cds(cumulative["sf"], station_id, instant)
        precip_mm = (lsp_m + cp_m) * 1000.0 if lsp_m is not None and cp_m is not None else None
        out[instant] = {
            "temperature2mC": record["temperature2mC"],
            "precipitationMm": precip_mm,
            "snowfallCm": sf_m * 100.0 if sf_m is not None else None,
            "levels": record["levels"],
        }
    return out


def assemble_chunk(
    chunk: dict[str, Any],
    region: str,
    day_cache_dir: Path,
    cache_dir: Path,
) -> bool:
    """Write one chunk payload if every day of the chunk is cached."""
    station_id = chunk["station_id"]
    hours_with_cum: dict[datetime, dict[str, Any]] = {}
    day = date.fromisoformat(chunk["start"])
    last = date.fromisoformat(chunk["end"])
    while day <= last:
        entry = load_day_cache(day_cache_dir, region, day)
        if entry is None or station_id not in entry:
            return False
        hours_with_cum.update(entry[station_id])
        day += timedelta(days=1)
    payload = build_chunk_payload(
        chunk,
        finalize_hours(hours_with_cum, station_id),
        profile_source=CDS_PROFILE_SOURCE_ID,
        profile_alignment=CDS_ALIGNMENT,
    )
    if payload is None:
        return False
    path = _chunk_path(cache_dir, chunk)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{threading.get_ident()}")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)
    tmp.replace(path)
    return True


# ---------------------------------------------------------------------------
# Fetch orchestration
# ---------------------------------------------------------------------------

def fetch_profiles_cds(
    events: list[dict[str, Any]],
    cache_dir: Path,
    day_cache_dir: Path,
    max_workers: int = 3,
    event_ids: set[str] | None = None,
    client_factory: Callable[[], Any] | None = None,
    retry_backoff_s: float = 30.0,
) -> dict[str, Any]:
    """Download ERA5 profiles via CDS for all eligible event windows.

    Resumable at two levels: valid chunk payloads are never rebuilt,
    and parsed (day, region) results persist in the day-cache, so a
    later run submits only the requests still missing.  A fatal
    (licence/auth) failure aborts the run and is reported in the
    summary's ``fatalError`` instead of being retried job after job.
    """
    chunks = plan_chunks(events, spec=CDS_SPEC)
    if event_ids is not None:
        chunks = [c for c in chunks if event_ids.intersection(c["event_ids"])]
    eligible = {eid for c in chunks for eid in c["event_ids"]}
    cache_dir.mkdir(parents=True, exist_ok=True)
    day_cache_dir.mkdir(parents=True, exist_ok=True)
    pending = [c for c in chunks if not _cache_valid(_chunk_path(cache_dir, c))]
    already = len(chunks) - len(pending)

    station_region: dict[str, str] = {}
    station_coords: dict[str, tuple[float, float]] = {}
    for event in events:
        station_region.setdefault(event["station_id"], event["region"])
        station_coords.setdefault(event["station_id"], (event["latitude"], event["longitude"]))

    jobs = plan_jobs(pending, station_region, station_coords, day_cache_dir)
    if jobs and client_factory is None and not cds_credentials_present():
        return {
            "profileSource": CDS_PROFILE_SOURCE_ID,
            "eventsLoaded": len(events),
            "eventsEligible": len(eligible),
            "chunksPlanned": len(chunks),
            "chunksAlreadyCached": already,
            "chunksDownloadedThisRun": 0,
            "chunksFailedThisRun": len(pending),
            "chunksCachedTotal": already,
            "jobsPlanned": len(jobs),
            "jobsDownloadedThisRun": 0,
            "jobsFailed": 0,
            "requestsSubmitted": 0,
            "failedJobs": [],
            "fatalError": (
                "CDS credentials not configured: ~/.cdsapirc does not exist yet "
                "and CDSAPI_KEY is unset -- the user writes the rc file "
                "personally; no request was submitted"
            ),
        }
    pool = _ClientPool(client_factory or _default_client_factory)
    abort = threading.Event()
    lock = threading.Lock()
    state = {"jobs_done": 0, "jobs_failed": 0, "fatal": None}
    failed_jobs: list[str] = []

    def work(job: dict[str, Any]) -> None:
        if abort.is_set():
            return
        label = f"{job['day'].isoformat()}:{job['region']}"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                pl_path = tmp_dir / "pl.nc"
                sl_path = tmp_dir / "sl.nc"
                client = pool.client()
                _retrieve(client, PL_DATASET, pl_request(job["day"], job["box"]), pl_path,
                          backoff_s=retry_backoff_s)
                _retrieve(client, SL_DATASET, sl_request(job["day"], job["box"]), sl_path,
                          backoff_s=retry_backoff_s)
                pl_data = load_netcdf_variables(pl_path, PL_SHORT_NAMES)
                sl_data = load_netcdf_variables(sl_path, SL_SHORT_NAMES)
            records = extract_day_records(pl_data, sl_data, job["day"], job["stations"])
            write_day_cache(day_cache_dir, job["region"], job["day"], records)
            with lock:
                state["jobs_done"] += 1
                done = state["jobs_done"]
            if done % 25 == 0:
                print(f"[cds] progress: {done}/{len(jobs)} day-jobs", file=sys.stderr)
        except FatalCdsError as exc:
            with lock:
                if state["fatal"] is None:
                    state["fatal"] = str(exc)
                state["jobs_failed"] += 1
                failed_jobs.append(label)
            abort.set()
            print(f"[cds] FATAL on job {label}: {exc}", file=sys.stderr)
        except Exception as exc:  # job left uncached for the next run
            with lock:
                state["jobs_failed"] += 1
                failed_jobs.append(label)
            print(f"[cds] job {label} failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    if jobs:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(work, job) for job in jobs]
            for future in as_completed(futures):
                future.result()

    written = 0
    for chunk in pending:
        if assemble_chunk(chunk, station_region[chunk["station_id"]], day_cache_dir, cache_dir):
            written += 1
    cached_total = sum(1 for c in chunks if _cache_valid(_chunk_path(cache_dir, c)))
    return {
        "profileSource": CDS_PROFILE_SOURCE_ID,
        "eventsLoaded": len(events),
        "eventsEligible": len(eligible),
        "chunksPlanned": len(chunks),
        "chunksAlreadyCached": already,
        "chunksDownloadedThisRun": written,
        "chunksFailedThisRun": len(pending) - written,
        "chunksCachedTotal": cached_total,
        "jobsPlanned": len(jobs),
        "jobsDownloadedThisRun": state["jobs_done"],
        "jobsFailed": state["jobs_failed"],
        "requestsSubmitted": 2 * state["jobs_done"],
        "failedJobs": sorted(failed_jobs)[:20],
        "fatalError": state["fatal"],
    }


def run_fetch_profiles_cds(
    pool_dir: Path,
    output_dir: Path,
    max_workers: int = 3,
    event_ids: set[str] | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    events = load_screened_events(pool_dir)
    cache_dir = output_dir / CDS_SPEC.cache_dir_name
    day_cache_dir = output_dir / "cds_day_cache"
    return fetch_profiles_cds(
        events, cache_dir, day_cache_dir,
        max_workers=max_workers, event_ids=event_ids, client_factory=client_factory,
    )
