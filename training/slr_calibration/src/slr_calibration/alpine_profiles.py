"""ERA5 (CDS) pairing for the alpine benchmark — queue item 4.

Fetches ERA5 through the project's existing CDS route and assembles,
for each benchmark station, the same five series the v3 feature
builders consume (``model_serving.build_point_window_features``), so
benchmark rows are byte-conventional with the gold pool's CDS-built
rows:

* pressure levels: t / r / u / v / z **plus w and q** (the CDS gold
  fetcher never needed them; the benchmark fetches them in the same
  request so the omega / humidity bundles are real, not absent);
* single levels, instantaneous: t2m, d2m, tclw, u10, v10, skt;
* single levels, accumulated (00/12 UTC forecast-step cumulatives):
  lsp, cp, sf — differenced exactly like the gold CDS path
  (``hourly_accumulation_cds``).

Differences from the gold CDS path, all forced by benchmark shape:
requests are batched per (year, month) with explicit day lists
instead of per event-day, and the NetCDF loader here is
member-aware — each variable keeps its own member's time axis
(the gold loader's first-member-wins merge is only safe for the
single-day requests it was written for).  A coverage assertion
(every needed instant present for every variable) triggers an
automatic split of the batching unit (month -> halves -> days).

Nothing is fetched until the pair lists exist; the fetcher is
resumable (extracted per-unit JSON is the completion marker) and
serial (the container's threading pathology rules out wide pools).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import time
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .gold_profiles import PRESSURE_LEVELS_HPA
from .gold_profiles_cds import (
    _FATAL_MARKERS,
    FatalCdsError,
    accumulation_init_cds,
    hourly_accumulation_cds,
)
from .gold_profiles_era5 import _clean, _nearest_index

UTC = timezone.utc

PL_VARIABLES = (
    "temperature", "relative_humidity", "u_component_of_wind",
    "v_component_of_wind", "geopotential", "vertical_velocity",
    "specific_humidity",
)
PL_SHORT = {
    "temperature": "t", "relative_humidity": "r",
    "u_component_of_wind": "u", "v_component_of_wind": "v",
    "geopotential": "z", "vertical_velocity": "w",
    "specific_humidity": "q",
}
SL_VARIABLES = (
    "2m_temperature", "2m_dewpoint_temperature",
    "total_column_liquid_water", "10m_u_component_of_wind",
    "10m_v_component_of_wind", "skin_temperature",
    "large_scale_precipitation", "convective_precipitation", "snowfall",
)
SL_SHORT = {
    "2m_temperature": "t2m", "2m_dewpoint_temperature": "d2m",
    "total_column_liquid_water": "tclw",
    "10m_u_component_of_wind": "u10", "10m_v_component_of_wind": "v10",
    "skin_temperature": "skt", "large_scale_precipitation": "lsp",
    "convective_precipitation": "cp", "snowfall": "sf",
}
SOURCE_ID = "era5_ncar_alpine"


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


def source_box(source: str, stations: list[dict[str, Any]]) -> list[float]:
    """CDS area [north, west, south, east], 0.75 deg pad snapped out."""
    lats = [float(s["latitude"]) for s in stations]
    lons = [float(s["longitude"]) for s in stations]
    if source == "alta":
        north = math.ceil((max(lats) + 0.75) * 4) / 4
        south = math.floor((min(lats) - 0.75) * 4) / 4
        west = math.floor((min(lons) - 0.75) * 4) / 4
        east = math.ceil((max(lons) + 0.75) * 4) / 4
        return [north, west, south, east]
    # SLF: single box over all stations (~2.5 x 4.25 deg of Alps).
    north = math.ceil((max(lats) + 0.75) * 4) / 4
    south = math.floor((min(lats) - 0.75) * 4) / 4
    west = math.floor((min(lons) - 0.75) * 4) / 4
    east = math.ceil((max(lons) + 0.75) * 4) / 4
    return [north, west, south, east]


def _request(area: list[float], variables: tuple[str, ...], year: int,
             month: int, days: list[int], pressure: bool) -> dict[str, Any]:
    request: dict[str, Any] = {
        "product_type": ["reanalysis"],
        "variable": list(variables),
        "year": [str(year)],
        "month": [f"{month:02d}"],
        "day": [f"{day:02d}" for day in days],
        "time": [f"{hour:02d}:00" for hour in range(24)],
        "grid": [0.25, 0.25],
        "area": list(area),
        "data_format": "netcdf",
        "download_format": "zip",
    }
    if pressure:
        request["pressure_level"] = [str(level) for level in PRESSURE_LEVELS_HPA]
    return request


# ---------------------------------------------------------------------------
# Member-aware NetCDF loading (each variable keeps its own time axis)
# ---------------------------------------------------------------------------


def _member_paths(path: Path) -> list[Path]:
    if zipfile.is_zipfile(path):
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="alpine-nc-"))
        with zipfile.ZipFile(path) as archive:
            archive.extractall(tmp)
        return sorted(tmp.rglob("*.nc"))
    return [path]


def _load_member(path: Path, wanted: set[str]) -> dict[str, dict[str, Any]]:
    """Return {short: {times, levels, lats, lons, values}} for one file."""
    import numpy as np
    import pandas as pd
    import xarray as xr

    try:
        dataset = xr.open_dataset(path, engine="h5netcdf", decode_times=True)
    except Exception:
        dataset = xr.open_dataset(path, decode_times=True)
    with dataset:
        coords = set(dataset.coords) | set(dataset.variables)
        time_name = "valid_time" if "valid_time" in coords else "time"
        lat_name = "latitude" if "latitude" in coords else "lat"
        lon_name = "longitude" if "longitude" in coords else "lon"
        level_name = next(
            (n for n in ("pressure_level", "isobaricInhPa", "level") if n in coords),
            None,
        )
        times = [t.replace(tzinfo=UTC) for t in
                 pd.to_datetime(dataset[time_name].values).to_pydatetime()]
        lats = [float(v) for v in dataset[lat_name].values]
        lons = [float(v) for v in dataset[lon_name].values]
        out: dict[str, dict[str, Any]] = {}
        core = {time_name, lat_name, lon_name, level_name}
        for name in dataset.data_vars:
            if name not in wanted:
                continue
            variable = dataset[name]
            if time_name not in variable.dims or lat_name not in variable.dims \
                    or lon_name not in variable.dims:
                continue
            usable = True
            for dim in list(variable.dims):
                if dim not in core:
                    if variable.sizes[dim] != 1:
                        usable = False
                        break
                    variable = variable.isel({dim: 0})
            if not usable:
                continue
            order = [time_name] + ([level_name] if level_name in variable.dims else []) \
                + [lat_name, lon_name]
            values = np.asarray(variable.transpose(*order).values, dtype="float64")
            levels = None
            if level_name in variable.dims:
                levels = [int(round(float(v))) for v in dataset[level_name].values]
            out[name] = {"times": times, "levels": levels, "lats": lats,
                         "lons": lons, "values": values}
        return out


def load_unit_variables(paths: Path | list[Path],
                        wanted: set[str]) -> dict[str, dict[str, Any]]:
    """Load all members of all files; merge variables by time axis."""
    import numpy as np

    if isinstance(paths, (str, Path)):
        paths = [Path(paths)]
    parts: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        for member in _member_paths(Path(path)):
            for name, payload in _load_member(member, wanted).items():
                parts.setdefault(name, []).append(payload)
    merged: dict[str, dict[str, Any]] = {}
    for name, payloads in parts.items():
        first = payloads[0]
        for other in payloads[1:]:
            if other["lats"] != first["lats"] or other["lons"] != first["lons"] \
                    or other["levels"] != first["levels"]:
                raise ValueError(f"{path}: member axes disagree for {name}")
        if len(payloads) == 1:
            merged[name] = first
            continue
        all_times = [t for p in payloads for t in p["times"]]
        if len(set(all_times)) != len(all_times):
            raise ValueError(f"{path}: duplicate instants for {name}")
        order = sorted(range(len(all_times)), key=lambda i: all_times[i])
        values = np.concatenate([p["values"] for p in payloads], axis=0)[order]
        merged[name] = {**first, "times": sorted(all_times), "values": values}
    return merged


# ---------------------------------------------------------------------------
# Station extraction
# ---------------------------------------------------------------------------


class _VarView:
    """Time-indexed nearest-point accessor for one loaded variable."""

    def __init__(self, payload: dict[str, Any], latitude: float, longitude: float):
        self.times = payload["times"]
        self.index = {t: i for i, t in enumerate(self.times)}
        self.levels = payload["levels"]
        self.values = payload["values"]
        self.lat_i = _nearest_index(payload["lats"], latitude)
        self.lon_i = _nearest_index(payload["lons"], longitude)

    def at(self, instant: datetime, level: int | None = None) -> float | None:
        i = self.index.get(instant)
        if i is None:
            return None
        if self.levels is not None:
            if level not in self.levels:
                return None
            value = self.values[i, self.levels.index(level), self.lat_i, self.lon_i]
        else:
            value = self.values[i, self.lat_i, self.lon_i]
        return _clean(float(value))


def extract_station_hours(
    pl_vars: dict[str, dict[str, Any]],
    sl_vars: dict[str, dict[str, Any]],
    latitude: float,
    longitude: float,
) -> dict[str, dict[str, Any]]:
    """Raw per-instant station record (cumulatives still cumulative)."""
    pl = {short: _VarView(pl_vars[long], latitude, longitude)
          for long, short in PL_SHORT.items() if long in pl_vars}
    sl = {short: _VarView(sl_vars[long], latitude, longitude)
          for long, short in SL_SHORT.items() if long in sl_vars}
    instants = sorted({t for view in (*pl.values(), *sl.values()) for t in view.times})
    hours: dict[str, dict[str, Any]] = {}
    for instant in instants:
        levels: dict[str, list[float | None]] = {}
        for level in PRESSURE_LEVELS_HPA:
            row: list[float | None] = []
            for short in ("t", "r", "u", "v", "z", "w", "q"):
                view = pl.get(short)
                row.append(view.at(instant, level) if view else None)
            t_k, rh, u, v, z, w, q = row
            levels[str(level)] = [
                None if t_k is None else t_k - 273.15,
                rh,
                None if u is None or v is None else math.hypot(u, v),
                None if z is None else z / 9.80665,
                w, q,
            ]
        t2m = sl["t2m"].at(instant) if "t2m" in sl else None
        record = {
            "levels": levels,
            "t2m_c": None if t2m is None else t2m - 273.15,
            "d2m_k": sl["d2m"].at(instant) if "d2m" in sl else None,
            "tclw": sl["tclw"].at(instant) if "tclw" in sl else None,
            "u10": sl["u10"].at(instant) if "u10" in sl else None,
            "v10": sl["v10"].at(instant) if "v10" in sl else None,
            "skt_k": sl["skt"].at(instant) if "skt" in sl else None,
            "lsp_cum_m": sl["lsp"].at(instant) if "lsp" in sl else None,
            "cp_cum_m": sl["cp"].at(instant) if "cp" in sl else None,
            "sf_cum_m": sl["sf"].at(instant) if "sf" in sl else None,
        }
        hours[instant.isoformat()] = record
    _difference_cds_amounts(hours, sl)
    return hours


def _difference_cds_amounts(hours: dict[str, dict[str, Any]],
                            sl: dict[str, _VarView]) -> None:
    """Convert CDS cumulative fields to (t-1h, t] amounts in place.

    Adds ``lsp_mm`` / ``cp_mm`` / ``sf_mm`` (mm of water) to every
    record and drops the cumulative fields, using the gold CDS
    path's own differencing (00/12 UTC init convention).
    """
    cumulative: dict[str, dict[tuple[str, datetime, int], float | None]] = {
        var: {} for var in ("lsp", "cp", "sf")
    }
    for iso in hours:
        instant = datetime.fromisoformat(iso)
        init, forecast_hour = accumulation_init_cds(instant)
        for var in ("lsp", "cp", "sf"):
            view = sl.get(var)
            cumulative[var][("bench", init, forecast_hour)] = (
                view.at(instant) if view else None)
    for iso, record in hours.items():
        instant = datetime.fromisoformat(iso)
        for var, field in (("lsp", "lsp_mm"), ("cp", "cp_mm"), ("sf", "sf_mm")):
            amount_m = hourly_accumulation_cds(cumulative[var], "bench", instant)
            record[field] = None if amount_m is None else amount_m * 1000.0
        for field in ("lsp_cum_m", "cp_cum_m", "sf_cum_m"):
            record.pop(field, None)


# ---------------------------------------------------------------------------
# Series assembly (raw hours -> the builders' five series)
# ---------------------------------------------------------------------------


def assemble_series(hours: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Shape raw station hours into the builders' five series.

    Raw records already carry hourly amounts (``lsp_mm`` / ``cp_mm``
    / ``sf_mm``, mm of water over (t-1h, t]) — each route's extractor
    differences its own accumulation convention (CDS 00/12 UTC
    inits; NCAR 06/18 UTC inits) with the gold code's own arithmetic,
    so a benchmark hour's precipitation is computed exactly as a
    gold hour's was.
    """
    profile_hours: dict[datetime, dict[str, Any]] = {}
    w_series: dict[datetime, dict[int, float]] = {}
    q_series: dict[datetime, dict[int, float]] = {}
    accumu_series: dict[datetime, dict[str, float]] = {}
    sfc_series: dict[datetime, dict[str, Any]] = {}

    for iso in sorted(hours):
        instant = datetime.fromisoformat(iso)
        record = hours[iso]
        lsp_mm = record["lsp_mm"]
        cp_mm = record["cp_mm"]
        sf_mm = record["sf_mm"]
        precip_mm = None if lsp_mm is None or cp_mm is None else lsp_mm + cp_mm
        snowfall_cm = None if sf_mm is None else sf_mm / 10.0
        levels: list[tuple[int, float, float, float, float]] = []
        w_map: dict[int, float] = {}
        q_map: dict[int, float] = {}
        for level in PRESSURE_LEVELS_HPA:
            t_c, rh, ws, z_m, w, q = record["levels"][str(level)]
            if None not in (t_c, rh, ws, z_m):
                levels.append((level, t_c, rh, ws, z_m))
            if w is not None:
                w_map[level] = w
            if q is not None:
                q_map[level] = q
        profile_hours[instant] = {
            "temperature2mC": record["t2m_c"],
            "precipitationMm": precip_mm,
            "snowfallCm": snowfall_cm,
            "levels": levels,
        }
        if len(w_map) == len(PRESSURE_LEVELS_HPA):
            w_series[instant] = w_map
        if len(q_map) == len(PRESSURE_LEVELS_HPA):
            q_series[instant] = q_map
        if precip_mm is not None and cp_mm is not None and lsp_mm is not None:
            accumu_series[instant] = {
                "tp_mm": precip_mm, "cp_mm": cp_mm, "lsp_mm": lsp_mm}
        sfc_series[instant] = {
            "dewpoint_2m": record["d2m_k"],
            "total_column_liquid_water": record["tclw"],
            "wind_u_10m": record["u10"],
            "wind_v_10m": record["v10"],
            "skin_temperature": record["skt_k"],
        }
    return {
        "profile": profile_hours, "w": w_series, "q": q_series,
        "accumu": accumu_series, "sfc": sfc_series,
    }


def slice_series(
    series: dict[str, dict[datetime, Any]], start: datetime, end: datetime
) -> dict[str, dict[datetime, Any]]:
    lo, hi = start - timedelta(hours=3), end + timedelta(hours=3)
    return {
        name: {t: v for t, v in values.items() if lo <= t <= hi}
        for name, values in series.items()
    }


# ---------------------------------------------------------------------------
# Fetch driver (serial, resumable, auto-splitting units)
# ---------------------------------------------------------------------------


def needed_days_by_month(pairs: list[dict[str, Any]]) -> dict[tuple[int, int], set]:
    """Map (year, month) -> set of UTC dates touched by pair windows."""
    units: dict[tuple[int, int], set] = {}
    for pair in pairs:
        start = datetime.fromisoformat(pair["window_start_utc"]) - timedelta(hours=12)
        end = datetime.fromisoformat(pair["window_end_utc"])
        day = start.date()
        while day <= end.date():
            units.setdefault((day.year, day.month), set()).add(day)
            day += timedelta(days=1)
    return units


def unit_key(source: str, year: int, month: int, days: list) -> str:
    digest = hashlib.sha256(
        ",".join(d.isoformat() for d in days).encode()).hexdigest()[:10]
    return f"{source}-{year}{month:02d}-{digest}"


class TooLargeError(RuntimeError):
    """CDS rejected the selection size; split the day list and retry."""


def _retrieve(client: Any, dataset: str, request: dict[str, Any], target: Path) -> None:
    last: Exception | None = None
    queued_waits = 0
    attempt = 0
    while True:
        try:
            client.retrieve(dataset, request, str(target))
            return
        except Exception as exc:  # noqa: BLE001 - classified below
            message = f"{type(exc).__name__}: {exc}".lower()
            if any(marker in message for marker in _FATAL_MARKERS):
                raise FatalCdsError(str(exc)) from exc
            if "cost limits" in message or "too large" in message:
                raise TooLargeError(str(exc)) from exc
            if "maximum number queued" in message:
                # CDS caps queued jobs per dataset for this account
                # and instructs sequential requesting: wait for the
                # queue to drain instead of dying (observed
                # 2026-11-03; parallel submitters were rejected).
                queued_waits += 1
                if queued_waits > 40:
                    raise RuntimeError(
                        f"CDS queue cap persisted after 40 waits: {exc}") from exc
                print(f"[alpine-fetch] CDS queue cap; waiting 240s "
                      f"({queued_waits})", flush=True)
                time.sleep(240)
                continue
            last = exc
            attempt += 1
            if attempt >= 3:
                raise RuntimeError(
                    f"CDS retrieve failed after retries: {last}") from exc
            time.sleep(20 * attempt)


def _dataset_files(client: Any, dataset: str, variables: tuple[str, ...],
                   area: list[float], source: str, year: int, month: int,
                   days: list, pressure: bool, cache_dir: Path) -> list[Path]:
    """Download (or reuse) the file(s) covering exactly ``days``.

    Splits the day list recursively when CDS rejects the selection
    size; single-day pressure-level requests are the proven floor
    (the whole gold CDS campaign ran at that granularity).
    """
    key = unit_key(source, year, month, days)
    target = cache_dir / f"{key}_{'pl' if pressure else 'sl'}.zip"
    if target.exists():
        return [target]
    request = _request(area, variables, year, month,
                       [d.day for d in days], pressure)
    try:
        _retrieve(client, dataset, request, target)
        return [target]
    except TooLargeError:
        pass
    # CDS also answers transient quota pressure with the same
    # "cost limits" 403 (observed 2026-11-03: an identical 2-day
    # selection was rejected once and accepted minutes later).
    # Retry the same selection before concluding it is truly size.
    for _attempt in range(2):
        time.sleep(60)
        try:
            _retrieve(client, dataset, request, target)
            return [target]
        except TooLargeError:
            continue
    if len(days) <= 1:
        raise TooLargeError(f"single-day selection rejected: {target.name}")
    mid = len(days) // 2
    return (
        _dataset_files(client, dataset, variables, area, source,
                       year, month, days[:mid], pressure, cache_dir)
        + _dataset_files(client, dataset, variables, area, source,
                         year, month, days[mid:], pressure, cache_dir)
    )


def _coverage_ok(pl_vars: dict, sl_vars: dict, days: list) -> bool:
    needed = {datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(hours=h)
              for d in days for h in range(24)}
    for payload in (*pl_vars.values(), *sl_vars.values()):
        if not needed.issubset(set(payload["times"])):
            return False
    return True


def fetch_source(
    source: str,
    pairs: list[dict[str, Any]],
    stations: list[dict[str, Any]],
    bench_dir: Path,
    *,
    limit_units: int | None = None,
    stage_rank: dict[str, int] | None = None,
    years: set[int] | None = None,
    cost_name: str = "pairing_cost.json",
) -> dict[str, Any]:
    """Fetch + extract all CDS units for one source (resumable).

    Units are ordered by the highest benchmark stage they serve
    (stage_rank: pair stage -> priority, lower first), then
    chronologically.  ``years`` restricts the run to units in those
    years so several processes can cover disjoint year ranges in
    parallel (each with its own ``cost_name`` log).  Returns the
    cost log accumulated so far.
    """
    import cdsapi

    bench_dir = Path(bench_dir)
    cache_dir = bench_dir / "cds_cache"
    extracted_dir = bench_dir / "cds_extracted"
    cache_dir.mkdir(parents=True, exist_ok=True)
    extracted_dir.mkdir(parents=True, exist_ok=True)
    cost_path = bench_dir / cost_name
    all_costs: dict[str, Any] = {}
    if cost_path.exists():
        all_costs = json.loads(cost_path.read_text(encoding="utf-8"))
    cost: dict[str, Any] = all_costs.setdefault(source, {"units": {}})

    units = needed_days_by_month(pairs)
    if years is not None:
        units = {key: days for key, days in units.items() if key[0] in years}
    unit_rank: dict[tuple[int, int], int] = {}
    if stage_rank:
        for pair in pairs:
            start = datetime.fromisoformat(pair["window_start_utc"])
            end = datetime.fromisoformat(pair["window_end_utc"])
            for key in {(start.year, start.month), (end.year, end.month)}:
                unit_rank[key] = min(unit_rank.get(key, 99),
                                     stage_rank.get(pair["stage"], 99))

    def _rank(item: tuple[tuple[int, int], set]) -> tuple[int, int, int]:
        (year, month), _days = item
        return (unit_rank.get((year, month), 99), year, month)

    ordered = sorted(units.items(), key=_rank)
    area = source_box(source, stations)
    client = cdsapi.Client()
    done = 0
    for (year, month), days in ordered:
        days_sorted = sorted(days)
        key = unit_key(source, year, month, days_sorted)
        extracted_path = extracted_dir / f"{key}.json.gz"
        if extracted_path.exists():
            done += 1
            continue
        if limit_units is not None and done >= limit_units:
            break
        t0 = time.time()
        _fetch_unit(client, source, area, year, month, days_sorted,
                    stations, cache_dir, extracted_path)
        seconds = time.time() - t0
        cost["units"][key] = {
            "year": year, "month": month, "days": len(days_sorted),
            "seconds": round(seconds, 1),
            "extracted_bytes": extracted_path.stat().st_size,
        }
        cost_path.write_text(json.dumps(all_costs, indent=2, sort_keys=True),
                             encoding="utf-8")
        done += 1
        print(f"[alpine-fetch] {source} {key}: {len(days_sorted)} days in "
              f"{seconds:.0f}s ({done} units this run)", flush=True)
    cost["units_done_total"] = len([p for p in extracted_dir.glob(f"{source}-*.json.gz")])
    cost["units_planned_total"] = len(ordered)
    cost_path.write_text(json.dumps(all_costs, indent=2, sort_keys=True),
                         encoding="utf-8")
    return cost


def _fetch_unit(client: Any, source: str, area: list[float], year: int,
                month: int, days: list, stations: list[dict[str, Any]],
                cache_dir: Path, extracted_path: Path) -> None:
    """One unit; datasets split independently on CDS size rejection,
    and the unit itself splits if parsed coverage is incomplete."""
    pl_files = _dataset_files(client, "reanalysis-era5-pressure-levels",
                              PL_VARIABLES, area, source, year, month,
                              days, True, cache_dir)
    sl_files = _dataset_files(client, "reanalysis-era5-single-levels",
                              SL_VARIABLES, area, source, year, month,
                              days, False, cache_dir)
    pl_vars = load_unit_variables(pl_files, set(PL_SHORT.values()))
    sl_vars = load_unit_variables(sl_files, set(SL_SHORT.values()))
    if not _coverage_ok(pl_vars, sl_vars, days):
        if len(days) <= 1:
            raise RuntimeError(
                f"coverage failure persists at single-day unit {extracted_path.stem}")
        mid = len(days) // 2
        for chunk in (days[:mid], days[mid:]):
            sub_key = unit_key(source, chunk[0].year, chunk[0].month, chunk)
            sub_path = extracted_path.parent / f"{sub_key}.json.gz"
            if not sub_path.exists():
                _fetch_unit(client, source, area, chunk[0].year, chunk[0].month,
                            chunk, stations, cache_dir, sub_path)
        # Mark the parent unit satisfied-by-split with an empty payload.
        with gzip.open(extracted_path, "wt", encoding="utf-8") as handle:
            json.dump({"split_into": True, "stations": {}}, handle)
        return
    payload: dict[str, Any] = {"split_into": False, "stations": {}}
    for station in stations:
        payload["stations"][station["station_id"]] = extract_station_hours(
            pl_vars, sl_vars, float(station["latitude"]), float(station["longitude"]))
    with gzip.open(extracted_path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)


def load_station_hours(bench_dir: Path, source: str) -> dict[str, dict[str, Any]]:
    """Merge all extracted units into {station_id: {iso: raw record}}."""
    merged: dict[str, dict[str, Any]] = {}
    for subdir in ("cds_extracted", "ncar_extracted"):
        for path in sorted((Path(bench_dir) / subdir).glob(f"{source}-*.json.gz")):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            for station_id, hours in payload.get("stations", {}).items():
                merged.setdefault(station_id, {}).update(hours)
    return merged


# ---------------------------------------------------------------------------
# NCAR route (nsf-ncar-era5 bucket, anonymous HDF5 range reads)
#
# The project's primary gold route.  CDS (above) was the
# pre-registered first choice for this benchmark, but on 2026-11-03
# CDS's post-"protection measures" throttling rejected or queued
# nearly every submission for hours (see DESIGN.md amendment 2), so
# the executed route is NCAR — the same ERA5 fields from NCAR's
# mirror, read with the gold reader's own machinery, extended with
# the two pressure-level variables (w, q) and the single-level set
# the benchmark needs.  Accumulations use the NCAR 06/18 UTC init
# convention via the gold module's own differencing.
# ---------------------------------------------------------------------------

#: Bucket file metadata for the PL variables the gold table lacks
#: (verified live against the bucket, 2026-11-03).
NCAR_PL_EXTRA = {
    "w": ("128_135", "w", "ll025sc", "W"),
    "q": ("128_133", "q", "ll025sc", "Q"),
}
#: an.sfc monthly variables: short name -> (code, token, HDF5 name).
NCAR_SFC = {
    "d2m": ("128_168", "2d", "VAR_2D"),
    "u10": ("128_165", "10u", "VAR_10U"),
    "v10": ("128_166", "10v", "VAR_10V"),
    "skt": ("128_235", "skt", "SKT"),
    "tclw": ("128_078", "tclw", "TCLW"),
}
NCAR_PL_VARIABLES = ("t", "r", "u", "v", "z", "w", "q")


def _ncar_reader() -> Any:
    from . import gold_profiles_era5

    for name, entry in NCAR_PL_EXTRA.items():
        gold_profiles_era5.PL_VARIABLES.setdefault(name, entry)
    return gold_profiles_era5.Era5Reader()


def _read_sfc_month(reader: Any, code: str, token: str, dataset_name: str,
                    instants: list[datetime],
                    stations: list[tuple[str, float, float]]
                    ) -> dict[tuple[str, datetime], float | None]:
    """Monthly an.sfc variable at the given instants (read_2t's shape)."""
    import calendar
    from collections import defaultdict

    from .gold_profiles_era5 import hours_since_1900, read_points

    by_month: dict[tuple[int, int], list[datetime]] = defaultdict(list)
    for instant in instants:
        by_month[(instant.year, instant.month)].append(instant)
    out: dict[tuple[str, datetime], float | None] = {}
    for (year, month), month_instants in sorted(by_month.items()):
        last_day = calendar.monthrange(year, month)[1]
        key = (
            f"e5.oper.an.sfc/{year:04d}{month:02d}/e5.oper.an.sfc.{code}_{token}."
            f"ll025sc.{year:04d}{month:02d}0100_{year:04d}{month:02d}{last_day:02d}23.nc"
        )
        handle = reader._open(key)
        coords = reader._coords(key)
        dataset = handle[dataset_name]
        time0 = int(coords["time"][0])
        lat_idx, lon_idx = reader._grid_indices(key, stations)
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


def extract_day_ncar(reader: Any, day: Any,
                     stations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Raw benchmark records for one UTC day and all stations."""
    from .gold_profiles_era5 import (
        ACCUMU_VARIABLES,
        accumulation_init,
        hourly_accumulation,
    )

    station_tuples = [
        (s["station_id"], float(s["latitude"]), float(s["longitude"]))
        for s in stations
    ]
    day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    instants = [day_start + timedelta(hours=h) for h in range(24)]
    pl = {var: reader.read_pl_day(day, var, station_tuples)
          for var in NCAR_PL_VARIABLES}
    t2m = reader.read_2t(instants, station_tuples)
    sfc = {
        short: _read_sfc_month(reader, code, token, dataset_name,
                               instants, station_tuples)
        for short, (code, token, dataset_name) in NCAR_SFC.items()
    }
    requests: list[tuple[datetime, int, str, float, float]] = []
    for station_id, lat, lon in station_tuples:
        for instant in instants:
            init, forecast_hour = accumulation_init(instant)
            requests.append((init, forecast_hour, station_id, lat, lon))
            if forecast_hour > 1:
                requests.append((init, forecast_hour - 1, station_id, lat, lon))
    cumulative = {var: reader.read_accumu_cumulative(var, requests)
                  for var in ACCUMU_VARIABLES}
    out: dict[str, dict[str, Any]] = {}
    for station_id, _lat, _lon in station_tuples:
        hours: dict[str, Any] = {}
        for instant in instants:
            levels: dict[str, list[float | None]] = {}
            for level in PRESSURE_LEVELS_HPA:
                t_k = pl["t"][(station_id, instant)][level]
                rh = pl["r"][(station_id, instant)][level]
                u = pl["u"][(station_id, instant)][level]
                v = pl["v"][(station_id, instant)][level]
                z = pl["z"][(station_id, instant)][level]
                levels[str(level)] = [
                    None if t_k is None else t_k - 273.15,
                    rh,
                    None if u is None or v is None else math.hypot(u, v),
                    None if z is None else z / 9.80665,
                    pl["w"][(station_id, instant)][level],
                    pl["q"][(station_id, instant)][level],
                ]
            amounts: dict[str, float | None] = {}
            for var in ("lsp", "cp", "sf"):
                amount_m = hourly_accumulation(cumulative[var], station_id, instant)
                amounts[var] = None if amount_m is None else amount_m * 1000.0
            t2m_k = t2m.get((station_id, instant))
            hours[instant.isoformat()] = {
                "levels": levels,
                "t2m_c": None if t2m_k is None else t2m_k - 273.15,
                "d2m_k": sfc["d2m"].get((station_id, instant)),
                "tclw": sfc["tclw"].get((station_id, instant)),
                "u10": sfc["u10"].get((station_id, instant)),
                "v10": sfc["v10"].get((station_id, instant)),
                "skt_k": sfc["skt"].get((station_id, instant)),
                "lsp_mm": amounts["lsp"],
                "cp_mm": amounts["cp"],
                "sf_mm": amounts["sf"],
            }
        out[station_id] = hours
    return out


def fetch_source_ncar(
    source: str,
    pairs: list[dict[str, Any]],
    stations: list[dict[str, Any]],
    bench_dir: Path,
    *,
    limit_units: int | None = None,
    stage_rank: dict[str, int] | None = None,
    years: set[int] | None = None,
    workers: int = 4,
) -> dict[str, Any]:
    """NCAR-route counterpart of fetch_source (same units/order/log)."""
    from concurrent.futures import ThreadPoolExecutor

    bench_dir = Path(bench_dir)
    extracted_dir = bench_dir / "ncar_extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)
    cost_path = bench_dir / "pairing_cost.json"
    all_costs: dict[str, Any] = {}
    if cost_path.exists():
        all_costs = json.loads(cost_path.read_text(encoding="utf-8"))
    cost: dict[str, Any] = all_costs.setdefault(source, {"units": {}})

    units = needed_days_by_month(pairs)
    if years is not None:
        units = {key: days for key, days in units.items() if key[0] in years}
    unit_rank: dict[tuple[int, int], int] = {}
    if stage_rank:
        for pair in pairs:
            start = datetime.fromisoformat(pair["window_start_utc"])
            end = datetime.fromisoformat(pair["window_end_utc"])
            for key in {(start.year, start.month), (end.year, end.month)}:
                unit_rank[key] = min(unit_rank.get(key, 99),
                                     stage_rank.get(pair["stage"], 99))
    ordered = sorted(
        units.items(),
        key=lambda item: (unit_rank.get(item[0], 99), item[0][0], item[0][1]))
    reader = _ncar_reader()
    done = 0
    for (year, month), days in ordered:
        days_sorted = sorted(days)
        key = unit_key(source, year, month, days_sorted)
        extracted_path = extracted_dir / f"{key}.json.gz"
        if extracted_path.exists():
            done += 1
            continue
        if limit_units is not None and done >= limit_units:
            break
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            per_day = list(pool.map(
                lambda d: extract_day_ncar(reader, d, stations), days_sorted))
        payload: dict[str, Any] = {"split_into": False, "stations": {}}
        for day_hours in per_day:
            for station_id, hours in day_hours.items():
                payload["stations"].setdefault(station_id, {}).update(hours)
        with gzip.open(extracted_path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle)
        seconds = time.time() - t0
        cost["units"][key] = {
            "year": year, "month": month, "days": len(days_sorted),
            "seconds": round(seconds, 1), "route": "ncar",
            "extracted_bytes": extracted_path.stat().st_size,
        }
        cost_path.write_text(json.dumps(all_costs, indent=2, sort_keys=True),
                             encoding="utf-8")
        done += 1
        print(f"[alpine-fetch-ncar] {source} {key}: {len(days_sorted)} days in "
              f"{seconds:.0f}s ({done} units this run)", flush=True)
    cost["units_done_total"] = len(
        [p for p in extracted_dir.glob(f"{source}-*.json.gz")])
    cost["units_planned_total"] = len(ordered)
    cost_path.write_text(json.dumps(all_costs, indent=2, sort_keys=True),
                         encoding="utf-8")
    return cost


# ---------------------------------------------------------------------------
# Feature building
# ---------------------------------------------------------------------------

_POINT_META = {
    "alta": {"state": "UT", "timezone": "America/Denver",
             "climate_regime": "intermountain_west", "region": "rockies"},
    "slf": {"state": "CH", "timezone": "Europe/Zurich",
            "climate_regime": "temperate_maritime", "region": "alpine-external"},
}


def build_features_for_source(
    source: str,
    pairs: list[dict[str, Any]],
    bench_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the 64-feature rows for every pair of one source."""
    from .model_serving import build_point_window_features

    station_hours = load_station_hours(bench_dir, source)
    funnel: Counter = Counter()
    rows: list[dict[str, Any]] = []
    series_cache: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        funnel["pairs"] += 1
        station_id = pair["station_id"]
        if station_id not in station_hours:
            funnel["status:no_series"] += 1
            rows.append({"pair_id": pair["pair_id"],
                         "profile_status": "no_series",
                         "baseline_slr": None})
            continue
        if station_id not in series_cache:
            series_cache[station_id] = assemble_series(station_hours[station_id])
        series = series_cache[station_id]
        start = datetime.fromisoformat(pair["window_start_utc"])
        end = datetime.fromisoformat(pair["window_end_utc"])
        sliced = slice_series(series, start, end)
        meta = _POINT_META[source]
        point = {
            "point_id": station_id.lower(),
            "resort_name": pair["station_name"],
            "point_name": "benchmark",
            "state": meta["state"],
            "timezone": meta["timezone"],
            "climate_regime": meta["climate_regime"],
            "region": meta["region"],
            "latitude": pair["latitude"],
            "longitude": pair["longitude"],
            "elevation_m": pair["elevation_m"],
        }
        row, status, counts = build_point_window_features(
            point, end, sliced["profile"], sliced["w"], sliced["q"],
            sliced["accumu"], sliced["sfc"], SOURCE_ID)
        funnel[f"status:{status}"] += 1
        row["pair_id"] = pair["pair_id"]
        rows.append(row)
    return rows, dict(funnel)
