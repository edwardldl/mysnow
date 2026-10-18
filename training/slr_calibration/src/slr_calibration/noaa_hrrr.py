"""NOAA HRRR GRIB2 extractor prototype: AWS archive vs Open-Meteo aggregates.

Motivation: the Open-Meteo historical-forecast build (hrrr_dataset.py) is
bounded by Open-Meteo's per-IP daily quota (~66 heavy chunks/day), so the
remaining HRRR backlog takes about a week of quota resets.  NOAA publishes
the operational HRRR output itself, anonymously, in the AWS Open Data
bucket ``noaa-hrrr-bdp-pds`` with no daily call quota.  This prototype
extracts the same daily station aggregates directly from those GRIB2
files for December 2024 -- a month the Open-Meteo dataset already covers
completely -- and benchmarks the two sources head-to-head on identical
station-days plus against the SNOTEL observations in the dataset.

Source layout (verified live 2026-10-18):
``https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.YYYYMMDD/conus/hrrr.t00z.{product}f{FF}.grib2``
with products ``wrfsfc`` (surface) and ``wrfprs`` (pressure levels).  Every
GRIB2 file has a ``.grib2.idx`` sidecar listing each message's byte offset;
the extractor fetches only the byte ranges of the wanted messages (HTTP
Range) and decodes them with ecCodes -- whole files are never downloaded.

Forecast selection (fixed): the 00Z cycle, forecast hours f01-f24, cycles
2024-11-30 .. 2025-01-01, so every station-local December 2024 day is
covered.  Each UTC instant comes from exactly one (cycle, fxx) pair, so
lead times are known exactly (1-24 h) -- unlike the Open-Meteo archive,
which blends runs opaquely.  Daily lead min/max are recorded per row.

Variables (trimmed): surface TMP / DPT at 2 m, APCP, ASNOW, WEASD, PRES,
HGT; pressure TMP and HGT at 1000, 975, 950, 925, 900, 850, 800, 750, 700,
650, 600, 550, 500 hPa.  The Kuchera computation uses the subset
``profiles.PRESSURE_LEVELS_HPA`` (10 levels) so the level set is identical
to the Open-Meteo dataset's; the extra 750/650/550 hPa levels are
extracted and cached for future work.

Accumulation semantics (verified from the data): the .idx forecast
descriptor for APCP and ASNOW reads ``0-N hour acc fcst`` at forecast hour
N, i.e. both are accumulated from cycle start.  Hourly increments are
therefore differences within one run (f01 differences against the run's
zero baseline); increments are never differenced across a cycle boundary.
Small negative increments from GRIB quantization are clamped to zero and
counted.  Daily snowfall/precip sums then match the Open-Meteo semantics
(sum of hourly amounts over the station-local day, (t-1, t] attribution).

Daily aggregation mirrors hrrr_dataset.aggregate_station_month exactly:
fixed AWDB ``dataTimeZone`` offset days, the shared
``tracking_align.compute_baseline_slr`` precip-weighted Kuchera, and the
same rounding.  Known differences from the Open-Meteo aggregates:
(1) single fixed 00Z cycle (leads 1-24 h) instead of a blended archive;
(2) snowfall is HRRR's native ASNOW diagnostic differenced from run
accumulation, not Open-Meteo's hourly snowfall field;
(3) no freezing-level diagnostic in the trimmed variable set, so
``fcst_freezing_level_m`` is null in the prototype output.

Dependencies: the base package needs only pyarrow; this module's GRIB
path needs the optional ``noaa`` extra (``pip install -e ".[noaa]"``,
i.e. herbie-data / cfgrib / eccodes, which install cleanly into the
package venv on Python 3.14).  The extractor drives ecCodes directly
rather than Herbie so request and byte accounting is exact; Herbie
remains available for ad-hoc retrieval.  Pure helpers (idx parsing,
range planning, differencing, aggregation, comparison) import without
the extra.

Discipline: anonymous S3, at most 4 concurrent file extractions, retries
with exponential backoff on transient errors, and a per-file cache of
extracted station point values (gzipped JSON) under
``data/hrrr-dataset/noaa-cache/`` (git-ignored).  Raw GRIB slices are
deterministically reproducible from S3, so the resumability layer caches
the extracted values; a rerun with a complete cache performs zero
network requests.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from .hrrr_dataset import load_stations_csv, water_year
from .profiles import PRESSURE_LEVELS_HPA
from .tracking_align import compute_baseline_slr
from .tracking_common import iso_z, utc_now, write_json

S3_BASE = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
CYCLE_HOUR_UTC = 0
FORECAST_HOURS = tuple(range(1, 25))
PRODUCTS = ("wrfsfc", "wrfprs")
MAX_WORKERS = 4
MAX_RETRIES = 4
KELVIN_OFFSET = 273.15

EXTRACT_LEVELS_HPA = (1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500)
KUCHERA_LEVELS_HPA = tuple(PRESSURE_LEVELS_HPA)

# (point-value key, GRIB variable, GRIB level string)
SURFACE_VARIABLES: tuple[tuple[str, str, str], ...] = (
    ("t2m_k", "TMP", "2 m above ground"),
    ("d2m_k", "DPT", "2 m above ground"),
    ("apcp_mm", "APCP", "surface"),
    ("asnow_m", "ASNOW", "surface"),
    ("weasd_kg_m2", "WEASD", "surface"),
    ("pres_pa", "PRES", "surface"),
    ("hgt_surface_m", "HGT", "surface"),
)
PRESSURE_VARIABLES: tuple[tuple[str, str, str], ...] = tuple(
    (f"t_{level}", "TMP", f"{level} mb") for level in EXTRACT_LEVELS_HPA
) + tuple(
    (f"hgt_{level}", "HGT", f"{level} mb") for level in EXTRACT_LEVELS_HPA
)

# Remaining Open-Meteo backlog the full build still owes (hrrr_dataset's
# hrrr_month_plan minus the completed Oct-Dec 2024), as contiguous date
# blocks; used only to scale the prototype's per-cycle cost into a
# full-backlog estimate for the NOAA route.
BACKLOG_BLOCKS: tuple[tuple[date, date], ...] = (
    (date(2025, 1, 1), date(2025, 5, 31)),
    (date(2025, 10, 1), date(2026, 5, 31)),
    (date(2026, 10, 1), date(2026, 10, 31)),
    (date(2023, 10, 1), date(2024, 5, 31)),
)

NOAA_DAILY_COLUMNS = [
    "station_triplet", "station_name", "state", "latitude", "longitude",
    "elevation_m", "data_tz_offset_hours", "nearest_resort", "distance_km",
    "date", "water_year", "hrrr_hours",
    "fcst_snowfall_cm", "fcst_precip_mm", "fcst_kuchera_slr",
    "fcst_kuchera_hours", "fcst_kuchera_weight_mm",
    "fcst_t2m_mean_c", "fcst_t2m_min_c", "fcst_t2m_max_c",
    "fcst_freezing_level_m", "noaa_lead_min_h", "noaa_lead_max_h",
]


class NoaaFetchError(Exception):
    """A NOAA S3 request failed persistently after all retries."""


# ---------------------------------------------------------------------------
# .idx parsing and range planning (pure)
# ---------------------------------------------------------------------------

def parse_idx(text: str) -> list[dict[str, Any]]:
    """Parse a GRIB2 .idx sidecar into message entries with byte spans.

    Lines look like ``15:1234567:d=2024121500:TMP:500 mb:6 hour fcst:``.
    Spans are filled in from the following entry's offset; the final
    entry's span end stays None until the file size is known.
    """
    entries: list[dict[str, Any]] = []
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) < 6:
            continue
        try:
            number = int(parts[0])
            offset = int(parts[1])
        except ValueError:
            continue
        entries.append({
            "num": number,
            "offset": offset,
            "var": parts[3],
            "level": parts[4],
            "forecast": ":".join(parts[5:]),
            "end": None,
        })
    entries.sort(key=lambda entry: entry["offset"])
    for index, entry in enumerate(entries):
        if index + 1 < len(entries):
            entry["end"] = entries[index + 1]["offset"] - 1
    return entries


def plan_ranges(
    entries: list[dict[str, Any]],
    wanted: Iterable[tuple[str, str, str]],
    file_size: int,
) -> list[dict[str, Any]]:
    """Plan byte ranges covering exactly the wanted (key, var, level) messages.

    Exactly-adjacent ranges are merged.  Raises ValueError when a wanted
    variable is absent from the inventory -- a silent miss would corrupt
    the benchmark.
    """
    by_var_level: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        by_var_level.setdefault((entry["var"], entry["level"]), entry)
    planned: list[dict[str, Any]] = []
    for key, var, level in wanted:
        entry = by_var_level.get((var, level))
        if entry is None:
            raise ValueError(f"variable {var} at {level} missing from idx inventory")
        end = entry["end"] if entry["end"] is not None else file_size - 1
        planned.append({"key": key, "var": var, "level": level, "start": entry["offset"], "end": end})
    planned.sort(key=lambda item: item["start"])
    merged: list[dict[str, Any]] = []
    for item in planned:
        if merged and item["start"] == merged[-1]["end"] + 1:
            merged[-1]["end"] = item["end"]
            merged[-1]["keys"].append(item["key"])
            merged[-1]["spans"].append((item["start"], item["end"], item["key"]))
        else:
            merged.append({
                "start": item["start"], "end": item["end"],
                "keys": [item["key"]], "spans": [(item["start"], item["end"], item["key"])],
            })
    return merged


def parse_accumulation_span(forecast: str) -> tuple[int, int] | None:
    """Parse an idx forecast descriptor into an accumulation span in hours.

    ``"0-7 hour acc fcst"`` -> (0, 7); ``"7 hour fcst"`` -> None
    (instantaneous field).  Returns None for anything unrecognized.
    """
    text = forecast.strip().rstrip(":")
    if " acc " not in f" {text} " and not text.endswith(" acc fcst"):
        return None
    head = text.split(" hour ")[0]
    if "-" not in head:
        return None
    start_text, end_text = head.split("-", 1)
    try:
        return int(start_text), int(end_text)
    except ValueError:
        return None


def hourly_increments(series: dict[int, float | None]) -> tuple[dict[int, float | None], int]:
    """Difference a run-accumulated series into hourly increments.

    ``series`` maps forecast hour -> accumulated value; hour 0 is the
    run's zero baseline.  A missing value poisons its own increment and
    the next one (the following difference would span a gap).  Negative
    increments are clamped to zero; the second return value counts them.
    """
    increments: dict[int, float | None] = {}
    clamps = 0
    previous: float | None = 0.0
    for hour in sorted(series):
        value = series[hour]
        if value is None or previous is None:
            increments[hour] = None
        else:
            increment = value - previous
            if increment < 0:
                clamps += 1
                increment = 0.0
            increments[hour] = increment
        previous = value
    return increments, clamps


# ---------------------------------------------------------------------------
# Fetch layer (mockable)
# ---------------------------------------------------------------------------

class RangeFetcher(Protocol):
    def get(self, url: str) -> bytes: ...
    def head_size(self, url: str) -> int: ...
    def get_range(self, url: str, start: int, end: int) -> bytes: ...


class UrllibRangeFetcher:
    """urllib S3 fetcher with backoff retries and request/byte accounting."""

    def __init__(
        self,
        max_retries: int = MAX_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
        timeout: int = 120,
    ) -> None:
        self._max_retries = max_retries
        self._sleep = sleep
        self._timeout = timeout
        self._lock = threading.Lock()
        self.requests = 0
        self.bytes_transferred = 0
        self.retries = 0

    def _request(self, url: str, headers: dict[str, str], method: str = "GET") -> tuple[bytes, dict[str, str]]:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            with self._lock:
                self.requests += 1
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "mysnow-noaa-hrrr-prototype/0.1", **headers}, method=method,
                )
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    body = response.read() if method != "HEAD" else b""
                    response_headers = {key: value for key, value in response.headers.items()}
                with self._lock:
                    self.bytes_transferred += len(body)
                return body, response_headers
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise NoaaFetchError(f"HTTP 404 for {url}") from exc
                last_error = exc
                if attempt >= self._max_retries:
                    break
                with self._lock:
                    self.retries += 1
                self._sleep(min(60.0, 2.0 * (2 ** attempt)))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt >= self._max_retries:
                    break
                with self._lock:
                    self.retries += 1
                self._sleep(min(60.0, 2.0 * (2 ** attempt)))
        raise NoaaFetchError(f"persistent failure for {url}: {last_error}")

    def get(self, url: str) -> bytes:
        body, _ = self._request(url, {})
        return body

    def head_size(self, url: str) -> int:
        _, headers = self._request(url, {}, method="HEAD")
        return int(headers["Content-Length"])

    def get_range(self, url: str, start: int, end: int) -> bytes:
        body, _ = self._request(url, {"Range": f"bytes={start}-{end}"})
        if len(body) != end - start + 1:
            raise NoaaFetchError(f"range {start}-{end} for {url} returned {len(body)} bytes")
        return body

    def stats(self) -> dict[str, int]:
        return {"requests": self.requests, "bytes_transferred": self.bytes_transferred, "retries": self.retries}


# ---------------------------------------------------------------------------
# GRIB decoding (ecCodes; the only part that needs the `noaa` extra)
# ---------------------------------------------------------------------------

def _eccodes() -> Any:
    try:
        import eccodes
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "eccodes is required for GRIB decoding; install the noaa extra: pip install -e \".[noaa]\""
        ) from exc
    return eccodes


def decode_message_values(message: bytes) -> Any:
    """Decode one GRIB2 message to its values array (NaN where missing)."""
    import numpy as np

    ec = _eccodes()
    gid = ec.codes_new_from_message(message)
    try:
        values = np.asarray(ec.codes_get_values(gid), dtype=float)
        try:
            missing = float(ec.codes_get(gid, "missingValue"))
        except Exception:
            missing = None
        if missing is not None:
            values = np.where(values == missing, np.nan, values)
        values = np.where(np.isfinite(values) & (np.abs(values) < 1e29), values, np.nan)
        return values
    finally:
        ec.codes_release(gid)


def message_grid(message: bytes) -> tuple[Any, Any, int, int]:
    """(latitudes, longitudes, ni, nj) for one GRIB2 message."""
    import numpy as np

    ec = _eccodes()
    gid = ec.codes_new_from_message(message)
    try:
        latitudes = np.asarray(ec.codes_get_array(gid, "latitudes"), dtype=float)
        longitudes = np.asarray(ec.codes_get_array(gid, "longitudes"), dtype=float)
        return latitudes, longitudes, int(ec.codes_get(gid, "Ni")), int(ec.codes_get(gid, "Nj"))
    finally:
        ec.codes_release(gid)


def nearest_grid_indices(latitudes: Any, longitudes: Any, stations: list[dict[str, Any]]) -> dict[str, int]:
    """Flat nearest-gridpoint index per station (HRRR longitudes are 0-360)."""
    import numpy as np

    lats = np.asarray(latitudes, dtype=float)
    lons = np.asarray(longitudes, dtype=float)
    indices: dict[str, int] = {}
    for station in stations:
        target_lon = float(station["longitude"]) % 360.0
        distance = (lats - float(station["latitude"])) ** 2 + (lons - target_lon) ** 2
        indices[str(station["station_triplet"])] = int(np.argmin(distance))
    return indices


# ---------------------------------------------------------------------------
# Per-file extraction with point-value cache
# ---------------------------------------------------------------------------

def grib_url(cycle_day: date, product: str, fxx: int) -> str:
    stamp = cycle_day.strftime("%Y%m%d")
    return (
        f"{S3_BASE}/hrrr.{stamp}/conus/"
        f"hrrr.t{CYCLE_HOUR_UTC:02d}z.{product}f{fxx:02d}.grib2"
    )


def _cache_path(cache_dir: Path, cycle_day: date, product: str, fxx: int) -> Path:
    stamp = cycle_day.strftime("%Y%m%d")
    return cache_dir / stamp / f"t{CYCLE_HOUR_UTC:02d}z_{product}f{fxx:02d}.json.gz"


def _read_points_cache(path: Path) -> dict[str, dict[str, float | None]] | None:
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("kind") != "noaa_hrrr_points":
        return None
    return payload["points"]


def _write_points_cache(path: Path, cycle_day: date, product: str, fxx: int, url: str, points: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": "noaa_hrrr_points",
        "cycle": f"{cycle_day.isoformat()}T{CYCLE_HOUR_UTC:02d}:00:00Z",
        "product": product,
        "fxx": fxx,
        "url": url,
        "fetched_at_utc": iso_z(utc_now()),
        "points": points,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
    os.replace(tmp, path)


def extract_file_points(
    cycle_day: date,
    product: str,
    fxx: int,
    fetcher: RangeFetcher,
    cache_dir: Path,
    grid_indices: dict[str, int],
) -> dict[str, Any]:
    """Station point values for one (cycle, product, fxx) GRIB file.

    Returns {"points": {triplet: {key: value|None}}, "cached": bool}.
    Only the wanted messages' byte ranges cross the network.
    """
    cache_path = _cache_path(cache_dir, cycle_day, product, fxx)
    cached = _read_points_cache(cache_path)
    if cached is not None:
        return {"points": cached, "cached": True}
    url = grib_url(cycle_day, product, fxx)
    wanted = SURFACE_VARIABLES if product == "wrfsfc" else PRESSURE_VARIABLES
    idx_text = fetcher.get(url + ".idx").decode("utf-8")
    entries = parse_idx(idx_text)
    file_size = fetcher.head_size(url)
    ranges = plan_ranges(entries, wanted, file_size)
    values_by_key: dict[str, Any] = {}
    for planned in ranges:
        body = fetcher.get_range(url, planned["start"], planned["end"])
        cursor = 0
        for span_start, span_end, key in planned["spans"]:
            length = span_end - span_start + 1
            message = body[cursor:cursor + length]
            cursor += length
            values_by_key[key] = decode_message_values(message)
    points: dict[str, dict[str, float | None]] = {}
    for triplet, index in grid_indices.items():
        row: dict[str, float | None] = {}
        for key, _var, _level in wanted:
            values = values_by_key.get(key)
            value = None if values is None else float(values[index])
            row[key] = None if value is None or value != value else value
        points[triplet] = row
    _write_points_cache(cache_path, cycle_day, product, fxx, url, points)
    return {"points": points, "cached": False}


def build_grid_indices(
    fetcher: RangeFetcher,
    sample_cycle: date,
    sample_fxx: int,
    stations: list[dict[str, Any]],
) -> dict[str, int]:
    """Nearest-gridpoint indices from one sample surface file's TMP 2 m message."""
    url = grib_url(sample_cycle, "wrfsfc", sample_fxx)
    idx_text = fetcher.get(url + ".idx").decode("utf-8")
    entries = parse_idx(idx_text)
    file_size = fetcher.head_size(url)
    ranges = plan_ranges(entries, [("t2m_k", "TMP", "2 m above ground")], file_size)
    planned = ranges[0]
    body = fetcher.get_range(url, planned["start"], planned["end"])
    latitudes, longitudes, _ni, _nj = message_grid(body)
    return nearest_grid_indices(latitudes, longitudes, stations)


# ---------------------------------------------------------------------------
# Hourly assembly + daily aggregation (pure)
# ---------------------------------------------------------------------------

def assemble_station_hours(
    file_points: dict[tuple[str, int, str], dict[str, dict[str, float | None]]],
    cycle_days: list[date],
    triplet: str,
) -> tuple[dict[datetime, dict[str, Any]], dict[str, int]]:
    """Build compute_baseline_slr-shaped hourly records for one station.

    ``file_points`` maps (cycle-date ISO, fxx, product) -> points dicts.
    ASNOW / APCP are differenced within each run; hours carry their exact
    forecast lead under ``lead_h``.
    """
    hours: dict[datetime, dict[str, Any]] = {}
    stats = {"negative_clamps": 0, "missing_hours": 0}
    for cycle_day in cycle_days:
        cycle_key = cycle_day.isoformat()
        asnow_series: dict[int, float | None] = {}
        apcp_series: dict[int, float | None] = {}
        for fxx in FORECAST_HOURS:
            sfc = file_points.get((cycle_key, fxx, "wrfsfc"), {}).get(triplet, {})
            asnow_series[fxx] = sfc.get("asnow_m")
            apcp_series[fxx] = sfc.get("apcp_mm")
        asnow_inc, asnow_clamps = hourly_increments(asnow_series)
        apcp_inc, apcp_clamps = hourly_increments(apcp_series)
        stats["negative_clamps"] += asnow_clamps + apcp_clamps
        cycle_start = datetime(cycle_day.year, cycle_day.month, cycle_day.day, CYCLE_HOUR_UTC, tzinfo=timezone.utc)
        for fxx in FORECAST_HOURS:
            sfc = file_points.get((cycle_key, fxx, "wrfsfc"), {}).get(triplet, {})
            prs = file_points.get((cycle_key, fxx, "wrfprs"), {}).get(triplet, {})
            if not sfc and not prs:
                stats["missing_hours"] += 1
                continue
            instant = cycle_start + timedelta(hours=fxx)
            t2m = sfc.get("t2m_k")
            levels = []
            for level in KUCHERA_LEVELS_HPA:
                temperature = prs.get(f"t_{level}")
                height = prs.get(f"hgt_{level}")
                levels.append({
                    "pressureHpa": level,
                    "temperatureC": None if temperature is None else temperature - KELVIN_OFFSET,
                    "geopotentialHeightM": height,
                })
            snow_increment = asnow_inc[fxx]
            precip_increment = apcp_inc[fxx]
            hours[instant] = {
                "time": iso_z(instant),
                "snowfallCm": None if snow_increment is None else snow_increment * 100.0,
                "precipitationMm": precip_increment,
                "temperature2mC": None if t2m is None else t2m - KELVIN_OFFSET,
                "levels": levels,
                "lead_h": fxx,
            }
    return hours, stats


def aggregate_station_days(
    hours_by_instant: dict[datetime, dict[str, Any]],
    station: dict[str, Any],
    days: list[date],
) -> dict[str, dict[str, Any]]:
    """Aggregate hourly records to station-local days.

    Mirrors hrrr_dataset.aggregate_station_month's daily semantics
    exactly, plus NOAA lead min/max over the day's hours.
    """
    empty: dict[str, Any] = {
        "hrrr_hours": 0,
        "fcst_snowfall_cm": None,
        "fcst_precip_mm": None,
        "fcst_kuchera_slr": None,
        "fcst_kuchera_hours": None,
        "fcst_kuchera_weight_mm": None,
        "fcst_t2m_mean_c": None,
        "fcst_t2m_min_c": None,
        "fcst_t2m_max_c": None,
        "fcst_freezing_level_m": None,
        "noaa_lead_min_h": None,
        "noaa_lead_max_h": None,
    }
    offset = station.get("data_tz_offset_hours")
    offset_td = timedelta(hours=float(offset)) if offset is not None else timedelta(0)
    elevation = float(station["elevation_m"])
    aggregated: dict[str, dict[str, Any]] = {}
    for day in days:
        start_utc = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - offset_td
        end_utc = start_utc + timedelta(hours=24)
        endpoints = [start_utc + timedelta(hours=k) for k in range(25)]
        present = [(instant, hours_by_instant[instant]) for instant in endpoints if instant in hours_by_instant]
        currents = [(instant, hour) for instant, hour in present if instant > start_utc]
        agg = dict(empty)
        agg["hrrr_hours"] = len(currents)
        if currents:
            snowfall = [hour["snowfallCm"] for _, hour in currents if hour.get("snowfallCm") is not None]
            precip = [hour["precipitationMm"] for _, hour in currents if hour.get("precipitationMm") is not None]
            t2m = [hour["temperature2mC"] for _, hour in currents if hour.get("temperature2mC") is not None]
            leads = [hour["lead_h"] for _, hour in currents if hour.get("lead_h") is not None]
            if snowfall:
                agg["fcst_snowfall_cm"] = round(sum(snowfall), 4)
            if precip:
                agg["fcst_precip_mm"] = round(sum(precip), 4)
            if t2m:
                agg["fcst_t2m_mean_c"] = round(sum(t2m) / len(t2m), 4)
                agg["fcst_t2m_min_c"] = round(min(t2m), 4)
                agg["fcst_t2m_max_c"] = round(max(t2m), 4)
            if leads:
                agg["noaa_lead_min_h"] = min(leads)
                agg["noaa_lead_max_h"] = max(leads)
            day_hours = [hour for _, hour in present]
            computed = compute_baseline_slr(day_hours, start_utc, end_utc, elevation)
            if computed is not None:
                slr, evaluated_hours, total_weight = computed
                agg["fcst_kuchera_slr"] = slr
                agg["fcst_kuchera_hours"] = evaluated_hours
                agg["fcst_kuchera_weight_mm"] = total_weight
        aggregated[day.isoformat()] = agg
    return aggregated


def local_day_bounds(station: dict[str, Any], day: date) -> tuple[datetime, datetime]:
    """(start_utc, end_utc) of a station-local day under the fixed AWDB offset."""
    offset = station.get("data_tz_offset_hours")
    offset_td = timedelta(hours=float(offset)) if offset is not None else timedelta(0)
    start_utc = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - offset_td
    return start_utc, start_utc + timedelta(hours=24)


# ---------------------------------------------------------------------------
# Comparison math (pure)
# ---------------------------------------------------------------------------

def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 3:
        return None
    mean_x = sum(x for x, _ in pairs) / len(pairs)
    mean_y = sum(y for _, y in pairs) / len(pairs)
    sxx = sum((x - mean_x) ** 2 for x, _ in pairs)
    syy = sum((y - mean_y) ** 2 for _, y in pairs)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    return round(sxy / math.sqrt(sxx * syy), 4)


def paired_stats(pairs: list[tuple[float, float]]) -> dict[str, Any]:
    """Bias / MAE / correlation for (candidate, reference) value pairs."""
    if not pairs:
        return {"n": 0, "mean_bias": None, "mae": None, "correlation": None}
    biases = [candidate - reference for candidate, reference in pairs]
    return {
        "n": len(pairs),
        "mean_bias": round(sum(biases) / len(biases), 4),
        "mae": round(sum(abs(bias) for bias in biases) / len(biases), 4),
        "correlation": _pearson(pairs),
    }


def compare_daily_rows(
    noaa_rows: list[dict[str, Any]],
    om_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Head-to-head NOAA vs Open-Meteo, and each vs SNOTEL observations.

    ``om_rows`` are committed-dataset rows (they carry the observation
    columns).  Observation truth for QC-passed days is the dataset's
    differenced depth change (``delta_depth_mm`` / 10, cm) -- the same
    weak-label definition the dataset itself publishes.
    """
    noaa_by_key = {(row["station_triplet"], row["date"]): row for row in noaa_rows}
    om_by_key = {(row["station_triplet"], row["date"]): row for row in om_rows}
    shared = sorted(set(noaa_by_key) & set(om_by_key))

    def pairs_for(column: str) -> list[tuple[float, float]]:
        pairs = []
        for key in shared:
            candidate = noaa_by_key[key].get(column)
            reference = om_by_key[key].get(column)
            if candidate is not None and reference is not None:
                pairs.append((float(candidate), float(reference)))
        return pairs

    report: dict[str, Any] = {
        "shared_station_days": len(shared),
        "noaa_vs_open_meteo": {
            "snowfall_cm": paired_stats(pairs_for("fcst_snowfall_cm")),
            "precip_mm": paired_stats(pairs_for("fcst_precip_mm")),
            "kuchera_slr": paired_stats(pairs_for("fcst_kuchera_slr")),
        },
        "vs_snotel_observations_qc_passed": {},
    }
    for label, source in (("noaa", noaa_by_key), ("open_meteo", om_by_key)):
        errors: list[tuple[float, float]] = []
        for key in shared:
            om_row = om_by_key[key]
            if om_row.get("weak_qc_passed") is not True:
                continue
            observed_depth_mm = om_row.get("delta_depth_mm")
            forecast_cm = source[key].get("fcst_snowfall_cm")
            if observed_depth_mm is None or forecast_cm is None:
                continue
            errors.append((float(forecast_cm), float(observed_depth_mm) / 10.0))
        stats = paired_stats(errors)
        stats["metric"] = "daily snowfall cm vs differenced SNOTEL depth change (weak labels, qc_weight 0.25)"
        report["vs_snotel_observations_qc_passed"][label] = stats
    return report


# ---------------------------------------------------------------------------
# Parquet IO
# ---------------------------------------------------------------------------

def _noaa_parquet_schema() -> Any:
    import pyarrow as pa

    return pa.schema([
        ("station_triplet", pa.string()),
        ("station_name", pa.string()),
        ("state", pa.string()),
        ("latitude", pa.float64()),
        ("longitude", pa.float64()),
        ("elevation_m", pa.float64()),
        ("data_tz_offset_hours", pa.float64()),
        ("nearest_resort", pa.string()),
        ("distance_km", pa.float64()),
        ("date", pa.string()),
        ("water_year", pa.int32()),
        ("hrrr_hours", pa.int32()),
        ("fcst_snowfall_cm", pa.float64()),
        ("fcst_precip_mm", pa.float64()),
        ("fcst_kuchera_slr", pa.float64()),
        ("fcst_kuchera_hours", pa.int32()),
        ("fcst_kuchera_weight_mm", pa.float64()),
        ("fcst_t2m_mean_c", pa.float64()),
        ("fcst_t2m_min_c", pa.float64()),
        ("fcst_t2m_max_c", pa.float64()),
        ("fcst_freezing_level_m", pa.float64()),
        ("noaa_lead_min_h", pa.int32()),
        ("noaa_lead_max_h", pa.int32()),
    ])


def write_noaa_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=_noaa_parquet_schema())
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp)
    os.replace(tmp, path)


def read_dataset_rows(dataset_dir: Path, year: int, month: int) -> list[dict[str, Any]]:
    """Committed-dataset rows for one calendar month (from the WY partition)."""
    import pyarrow.parquet as pq

    month_start = date(year, month, 1)
    partition = dataset_dir / "daily" / f"water_year={water_year(month_start)}" / "data.parquet"
    table = pq.read_table(partition)
    rows = table.to_pylist()
    prefix = f"{year:04d}-{month:02d}"
    return [row for row in rows if str(row["date"]).startswith(prefix)]


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def backlog_cycle_estimate() -> dict[str, Any]:
    days = 0
    for start, end in BACKLOG_BLOCKS:
        days += (end - start).days + 1
    # Each contiguous block needs one trailing boundary cycle past its end.
    return {"days": days, "cycles": days + len(BACKLOG_BLOCKS), "blocks": len(BACKLOG_BLOCKS)}


def extract_noaa_hrrr(
    *,
    dataset_dir: Path,
    start_cycle: date = date(2024, 11, 30),
    end_cycle: date = date(2025, 1, 1),
    output_year: int = 2024,
    output_month: int = 12,
    max_workers: int = MAX_WORKERS,
    max_files: int | None = None,
    fetcher: RangeFetcher | None = None,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Extract one month of NOAA HRRR daily aggregates and benchmark them."""
    started = time.monotonic()
    fetcher = fetcher or UrllibRangeFetcher()
    cache_dir = dataset_dir / "noaa-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    prototype_dir = dataset_dir / "noaa-prototype"
    prototype_dir.mkdir(parents=True, exist_ok=True)

    stations = [
        station for station in load_stations_csv(dataset_dir / "stations.csv")
        if station["state"] != "AK"
    ]
    progress(f"Stations: {len(stations)} CONUS (AK excluded: CONUS HRRR domain only)")
    month_days = [date(output_year, output_month, d) for d in range(1, monthrange(output_year, output_month)[1] + 1)]

    cycle_days: list[date] = []
    cursor = start_cycle
    while cursor <= end_cycle:
        cycle_days.append(cursor)
        cursor += timedelta(days=1)
    total_planned = len(cycle_days) * len(PRODUCTS) * len(FORECAST_HOURS)
    progress(f"Planned GRIB files: {total_planned} ({len(cycle_days)} cycles x {len(PRODUCTS)} products x {len(FORECAST_HOURS)} fxx)")

    grid_indices = build_grid_indices(fetcher, cycle_days[0], FORECAST_HOURS[0], stations)
    progress("Grid indices built from sample file")

    fetched_files = 0
    cached_files = 0
    failed_files: list[dict[str, Any]] = []
    assembly_stats = {"negative_clamps": 0, "missing_hours": 0}
    budget = max_files  # remaining fetch budget across cycles (None = unlimited)
    # Per-station pending hourly records and finished day aggregates.
    pending: dict[str, dict[datetime, dict[str, Any]]] = {str(s["station_triplet"]): {} for s in stations}
    aggregated: dict[str, dict[str, dict[str, Any]]] = {str(s["station_triplet"]): {} for s in stations}
    station_by_triplet = {str(s["station_triplet"]): s for s in stations}

    def run_task(task: tuple[date, str, int]) -> tuple[tuple[str, int, str], dict[str, Any] | None, str | None]:
        cycle_day, product, fxx = task
        try:
            result = extract_file_points(cycle_day, product, fxx, fetcher, cache_dir, grid_indices)
            return (cycle_day.isoformat(), fxx, product), result, None
        except Exception as exc:  # recorded, never hot-looped
            return (cycle_day.isoformat(), fxx, product), None, f"{type(exc).__name__}: {exc}"

    for cycle_day in cycle_days:
        cycle_end = datetime(cycle_day.year, cycle_day.month, cycle_day.day, CYCLE_HOUR_UTC, tzinfo=timezone.utc) + timedelta(hours=24)
        tasks = [(cycle_day, product, fxx) for product in PRODUCTS for fxx in FORECAST_HOURS]
        if budget is not None:
            tasks = tasks[:budget]
            budget -= len(tasks)
        if tasks:
            cycle_points: dict[tuple[str, int, str], dict[str, dict[str, float | None]]] = {}
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(run_task, task): task for task in tasks}
                for future in as_completed(futures):
                    key, result, error = future.result()
                    if error is not None or result is None:
                        failed_files.append({"file": f"{key[0]} {key[2]} f{key[1]:02d}", "error": error})
                    else:
                        cycle_points[key] = result["points"]
                        if result["cached"]:
                            cached_files += 1
                        else:
                            fetched_files += 1
            for station in stations:
                triplet = str(station["station_triplet"])
                hours, stats = assemble_station_hours(cycle_points, [cycle_day], triplet)
                assembly_stats["negative_clamps"] += stats["negative_clamps"]
                assembly_stats["missing_hours"] += stats["missing_hours"]
                pending[triplet].update(hours)
        # Aggregate every month day whose window has fully arrived, then
        # prune hours that no remaining day can use as an endpoint.
        for station in stations:
            triplet = str(station["station_triplet"])
            remaining: list[date] = []
            for day in month_days:
                if day.isoformat() in aggregated[triplet]:
                    continue
                _start, end_utc = local_day_bounds(station, day)
                if end_utc <= cycle_end:
                    aggregated[triplet].update(aggregate_station_days(pending[triplet], station, [day]))
                else:
                    remaining.append(day)
            if remaining:
                next_start, _ = local_day_bounds(station, remaining[0])
                pending[triplet] = {
                    instant: hour for instant, hour in pending[triplet].items() if instant >= next_start
                }
            else:
                pending[triplet] = {}
        progress(
            f"Cycle {cycle_day.isoformat()}: cumulative {fetched_files} fetched, "
            f"{cached_files} cached, {len(failed_files)} failed"
        )
        if budget is not None and budget <= 0:
            break

    # Any day not reached above (e.g. truncated runs) aggregates as-is.
    rows: list[dict[str, Any]] = []
    for station in stations:
        triplet = str(station["station_triplet"])
        missing_days = [day for day in month_days if day.isoformat() not in aggregated[triplet]]
        if missing_days:
            aggregated[triplet].update(aggregate_station_days(pending[triplet], station, missing_days))
        for day in month_days:
            agg = aggregated[triplet][day.isoformat()]
            rows.append({
                "station_triplet": triplet,
                "station_name": station["station_name"],
                "state": station["state"],
                "latitude": station["latitude"],
                "longitude": station["longitude"],
                "elevation_m": station["elevation_m"],
                "data_tz_offset_hours": station["data_tz_offset_hours"],
                "nearest_resort": station["nearest_resort"],
                "distance_km": station["distance_km"],
                "date": day.isoformat(),
                "water_year": water_year(day),
                **agg,
            })
    rows.sort(key=lambda row: (row["station_triplet"], row["date"]))
    parquet_path = prototype_dir / f"daily_{output_year:04d}-{output_month:02d}.parquet"
    write_noaa_parquet(parquet_path, rows)

    om_rows = read_dataset_rows(dataset_dir, output_year, output_month)
    comparison = compare_daily_rows(rows, om_rows)

    spot: dict[str, Any] | None = None
    palisades_om = [row for row in om_rows if row["station_triplet"] == "784:CA:SNTL" and row.get("fcst_snowfall_cm") is not None]
    if palisades_om:
        biggest = max(palisades_om, key=lambda row: float(row["fcst_snowfall_cm"]))
        noaa_match = next((row for row in rows if row["station_triplet"] == "784:CA:SNTL" and row["date"] == biggest["date"]), None)
        spot = {
            "station_triplet": "784:CA:SNTL",
            "date": biggest["date"],
            "selection": "largest Open-Meteo fcst_snowfall_cm day for the station in the month",
            "open_meteo": {key: biggest.get(key) for key in ("fcst_snowfall_cm", "fcst_precip_mm", "fcst_t2m_mean_c", "fcst_kuchera_slr", "hrrr_hours")},
            "noaa": None if noaa_match is None else {key: noaa_match.get(key) for key in ("fcst_snowfall_cm", "fcst_precip_mm", "fcst_t2m_mean_c", "fcst_kuchera_slr", "hrrr_hours", "noaa_lead_min_h", "noaa_lead_max_h")},
            "snotel_delta_depth_mm": biggest.get("delta_depth_mm"),
            "snotel_delta_swe_mm": biggest.get("delta_swe_mm"),
            "weak_qc_passed": biggest.get("weak_qc_passed"),
        }

    elapsed = round(time.monotonic() - started, 1)
    fetch_stats = fetcher.stats() if isinstance(fetcher, UrllibRangeFetcher) else {}
    cache_bytes = sum(path.stat().st_size for path in cache_dir.rglob("*") if path.is_file())
    backlog = backlog_cycle_estimate()
    fetched_cycles = fetched_files / (len(PRODUCTS) * len(FORECAST_HOURS)) if fetched_files else 0.0
    extrapolation: dict[str, Any] = {"backlog": backlog}
    if fetched_cycles > 0:
        bytes_per_cycle = fetch_stats.get("bytes_transferred", 0) / fetched_cycles
        seconds_per_cycle = elapsed / fetched_cycles
        extrapolation.update({
            "basis_cycles_measured": round(fetched_cycles, 2),
            "bytes_per_cycle": round(bytes_per_cycle),
            "estimated_backlog_bytes": round(bytes_per_cycle * backlog["cycles"]),
            "estimated_backlog_wall_seconds_at_prototype_rate": round(seconds_per_cycle * backlog["cycles"]),
        })

    report: dict[str, Any] = {
        "generated_at_utc": iso_z(utc_now()),
        "source": "NOAA HRRR, AWS Open Data bucket noaa-hrrr-bdp-pds, 00Z cycle f01-f24, wrfsfc+wrfprs",
        "cycles": [cycle_days[0].isoformat(), cycle_days[-1].isoformat()],
        "output_month": f"{output_year:04d}-{output_month:02d}",
        "stations": len(stations),
        "files": {
            "planned": total_planned if max_files is None else min(total_planned, max_files),
            "fetched": fetched_files,
            "cached": cached_files,
            "failed": failed_files,
        },
        "operations": {
            "wall_seconds": elapsed,
            "requests": fetch_stats.get("requests"),
            "bytes_transferred": fetch_stats.get("bytes_transferred"),
            "retries": fetch_stats.get("retries"),
            "cache_bytes_on_disk": cache_bytes,
            "assembly": assembly_stats,
        },
        "coverage": {
            "station_days": len(rows),
            "days_with_full_24h": sum(1 for row in rows if row["hrrr_hours"] == 24),
            "days_with_any_hours": sum(1 for row in rows if (row["hrrr_hours"] or 0) > 0),
            "days_with_kuchera": sum(1 for row in rows if row["fcst_kuchera_slr"] is not None),
        },
        "comparison": comparison,
        "spot_check_palisades": spot,
        "backlog_extrapolation": extrapolation,
        "semantic_differences": [
            "NOAA prototype: single fixed 00Z cycle, exact leads 1-24 h per day; Open-Meteo archive blends runs opaquely.",
            "NOAA snowfall: HRRR native ASNOW differenced from run accumulation; Open-Meteo: its hourly snowfall field summed.",
            "fcst_freezing_level_m is null in the NOAA output (not in the trimmed variable set).",
            "Kuchera levels identical (profiles.PRESSURE_LEVELS_HPA subset of the extracted 13 levels).",
        ],
    }
    write_json(prototype_dir / "comparison_report.json", report)
    (prototype_dir / "summary.md").write_text(render_summary_md(report), encoding="utf-8")
    progress(
        f"NOAA prototype: {len(rows)} station-days, {fetched_files} files fetched, "
        f"{fetch_stats.get('bytes_transferred')} bytes, {elapsed}s"
    )
    return report


def render_summary_md(report: dict[str, Any]) -> str:
    comparison = report["comparison"]
    vs_obs = comparison["vs_snotel_observations_qc_passed"]
    lines = [
        f"# NOAA HRRR prototype vs Open-Meteo — {report['output_month']}",
        "",
        f"Source: {report['source']}. Stations: {report['stations']} CONUS. "
        f"Shared station-days: {comparison['shared_station_days']}.",
        "",
        "## NOAA vs Open-Meteo (identical station-days)",
        "",
        "| variable | n | mean bias (NOAA − OM) | MAE | correlation |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label, key in (("snowfall (cm)", "snowfall_cm"), ("precip (mm)", "precip_mm"), ("Kuchera SLR", "kuchera_slr")):
        stats = comparison["noaa_vs_open_meteo"][key]
        lines.append(f"| {label} | {stats['n']} | {stats['mean_bias']} | {stats['mae']} | {stats['correlation']} |")
    lines += [
        "",
        "## Daily snowfall vs SNOTEL observations (QC-passed weak-label days)",
        "",
        "| source | n | mean bias (cm) | MAE (cm) | correlation |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label, key in (("NOAA (00Z, leads 1–24 h)", "noaa"), ("Open-Meteo archive", "open_meteo")):
        stats = vs_obs[key]
        lines.append(f"| {label} | {stats['n']} | {stats['mean_bias']} | {stats['mae']} | {stats['correlation']} |")
    ops = report["operations"]
    files = report["files"]
    lines += [
        "",
        "## Operations",
        "",
        f"- Files: {files['fetched']} fetched, {files['cached']} cached, {len(files['failed'])} failed of {files['planned']} planned",
        f"- Wall time: {ops['wall_seconds']} s; requests: {ops['requests']}; bytes transferred: {ops['bytes_transferred']}; cache on disk: {ops['cache_bytes_on_disk']} bytes",
        "",
        "## Semantic differences",
        "",
    ]
    lines += [f"- {item}" for item in report["semantic_differences"]]
    lines.append("")
    return "\n".join(lines)
