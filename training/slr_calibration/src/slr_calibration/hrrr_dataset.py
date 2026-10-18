"""Daily observed-snowfall + HRRR forecast dataset for SNOTEL stations near ski resorts.

Builds one row per station-day joining:

- AWDB SNOTEL daily observations (total-pack SWE / depth / accumulated
  precipitation / temperatures) converted to metric, with day-over-day
  deltas, a differenced daily precipitation estimate, and the tracking
  loop's weak-event QC verdict (see tracking_events.evaluate_weak_candidate)
  plus the differenced observed SLR when those gates pass.
- Open-Meteo Historical Forecast API values for ``models=ncep_hrrr_conus``
  (the archived forecast values Open-Meteo serves, not per-run operational
  control), aggregated from hourly to station-local days using each
  station's fixed AWDB ``dataTimeZone`` UTC offset, including a
  precipitation-weighted daily Kuchera SLR computed by the exact shared
  implementation in tracking_align.compute_baseline_slr.

Rate-limit discipline is a hard requirement of this module: AWDB requests
carry at most 50 station triplets for one calendar month with >= 1.5 s
between requests; Open-Meteo requests carry at most 25 locations for one
calendar month (plus/minus one padding day) with >= 2 s between requests.
Every response is cached on disk, HTTP 429 is honored with Retry-After and
exponential backoff, chunks that fail persistently are recorded and skipped
(never hot-looped), and a rerun with a complete cache performs zero network
requests.  Execution is sequential only.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.parse
from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .profiles import HOURLY_FIELDS, OPEN_METEO_ENDPOINT, _response_hours
from .snotel_track import awdb_data_url, parse_awdb_data
from .tracking_align import _parse_instant, compute_baseline_slr
from .tracking_common import Fetcher, fetch_bytes, finite, haversine_km, iso_z, utc_now, write_csv_rows, write_json
from .tracking_events import WEAK_QC_WEIGHT, evaluate_weak_candidate
from .tracking_registry import AWDB_STATIONS_URL, filter_active_snotel

HRRR_MODEL = "ncep_hrrr_conus"
MAX_RESORT_DISTANCE_KM = 25.0
AWDB_BATCH_STATIONS = 50
OPEN_METEO_BATCH_LOCATIONS = 25
AWDB_MIN_INTERVAL_S = 1.5
OPEN_METEO_MIN_INTERVAL_S = 2.0
MAX_RETRIES = 5
INCH_TO_MM = 25.4

OBS_START_DEFAULT = date(2023, 10, 1)

STATION_COLUMNS = [
    "station_triplet", "station_name", "state", "latitude", "longitude",
    "elevation_m", "data_tz_offset_hours", "nearest_resort",
    "nearest_resort_region", "nearest_resort_country", "distance_km",
]

DAILY_COLUMNS = [
    "station_triplet", "station_name", "state", "latitude", "longitude",
    "elevation_m", "data_tz_offset_hours", "nearest_resort", "distance_km",
    "date", "water_year",
    "swe_mm", "snow_depth_mm", "precip_accum_mm", "tavg_c", "tmax_c", "tmin_c",
    "delta_swe_mm", "delta_depth_mm", "precip_mm", "obs_flags",
    "weak_qc_passed", "weak_qc_rejection", "observed_slr_diff", "weak_qc_weight",
    "hrrr_available", "hrrr_status", "hrrr_hours",
    "fcst_snowfall_cm", "fcst_precip_mm", "fcst_kuchera_slr",
    "fcst_kuchera_hours", "fcst_kuchera_weight_mm",
    "fcst_t2m_mean_c", "fcst_t2m_min_c", "fcst_t2m_max_c",
    "fcst_freezing_level_m",
]


class NoDataError(Exception):
    """HTTP 400 from the API: the request itself is invalid / has no data."""


class FetchFailedError(Exception):
    """A request failed persistently after all retries."""


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    headers = getattr(exc, "headers", None) or getattr(exc, "hdrs", None)
    if headers is None:
        return None
    try:
        value = headers.get("Retry-After")
    except AttributeError:
        return None
    if value is None:
        return None
    try:
        return max(0.0, float(str(value).strip()))
    except ValueError:
        return None  # HTTP-date form: fall back to exponential backoff


class RateLimitedClient:
    """Sequential fetcher enforcing a minimum interval and 429-aware retries."""

    def __init__(
        self,
        fetcher: Fetcher,
        min_interval_s: float,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self._fetcher = fetcher
        self._min_interval_s = min_interval_s
        self._sleep = sleep
        self._clock = clock
        self._max_retries = max_retries
        self._last_attempt_at: float | None = None
        self.logical_requests = 0
        self.attempts = 0
        self.retries = 0

    def _respect_interval(self) -> None:
        if self._last_attempt_at is None:
            return
        wait = self._min_interval_s - (self._clock() - self._last_attempt_at)
        if wait > 0:
            self._sleep(wait)

    def get_bytes(self, url: str) -> bytes:
        self.logical_requests += 1
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            self._respect_interval()
            self._last_attempt_at = self._clock()
            self.attempts += 1
            try:
                return self._fetcher(url)
            except urllib.error.HTTPError as exc:
                if exc.code == 400:
                    raise NoDataError(f"HTTP 400 for {url}") from exc
                last_error = exc
                if attempt >= self._max_retries:
                    break
                delay = _retry_after_seconds(exc)
                if delay is None:
                    delay = min(300.0, 5.0 * (2 ** attempt))
                self.retries += 1
                self._sleep(delay)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt >= self._max_retries:
                    break
                self.retries += 1
                self._sleep(min(300.0, 5.0 * (2 ** attempt)))
        raise FetchFailedError(f"persistent failure for {url}: {last_error}")

    def get_json(self, url: str) -> Any:
        return json.loads(self.get_bytes(url).decode("utf-8"))

    def stats(self) -> dict[str, int]:
        return {
            "logical_requests": self.logical_requests,
            "attempts": self.attempts,
            "retries": self.retries,
        }


# ---------------------------------------------------------------------------
# Station set
# ---------------------------------------------------------------------------

def build_station_set(
    ski_areas_path: Path,
    raw_stations: list[dict[str, Any]],
    today: date,
    max_km: float = MAX_RESORT_DISTANCE_KM,
) -> list[dict[str, Any]]:
    """Deterministically regenerate the near-resort SNOTEL station set.

    Active SNTL stations (tracking_registry.filter_active_snotel) whose
    nearest entry in the ski-area catalog (every entry with parseable
    coordinates, any country) is within ``max_km``.
    """
    tz_by_triplet: dict[str, float | None] = {}
    for raw in raw_stations:
        triplet = str(raw.get("stationTriplet") or "")
        if triplet:
            tz_by_triplet[triplet] = finite(raw.get("dataTimeZone"))

    areas = json.loads(ski_areas_path.read_text(encoding="utf-8"))
    resorts: list[dict[str, Any]] = []
    for area in areas:
        latitude = finite(area.get("lat"))
        longitude = finite(area.get("lon"))
        if latitude is None or longitude is None:
            continue
        resorts.append({
            "name": str(area.get("name") or ""),
            "region": str(area.get("region") or ""),
            "country": str(area.get("country") or ""),
            "latitude": latitude,
            "longitude": longitude,
        })

    stations: list[dict[str, Any]] = []
    for station in filter_active_snotel(raw_stations, today):
        best: tuple[float, dict[str, Any]] | None = None
        for resort in resorts:
            distance = haversine_km(station["latitude"], station["longitude"], resort["latitude"], resort["longitude"])
            if best is None or distance < best[0]:
                best = (distance, resort)
        if best is None or best[0] > max_km:
            continue
        distance, resort = best
        stations.append({
            "station_triplet": station["station_triplet"],
            "station_name": station["station_name"],
            "state": station["state"],
            "latitude": station["latitude"],
            "longitude": station["longitude"],
            "elevation_m": station["elevation_m"],
            "data_tz_offset_hours": tz_by_triplet.get(station["station_triplet"]),
            "nearest_resort": resort["name"],
            "nearest_resort_region": resort["region"],
            "nearest_resort_country": resort["country"],
            "distance_km": round(distance, 3),
        })
    stations.sort(key=lambda row: row["station_triplet"])
    return stations


def load_stations_csv(path: Path) -> list[dict[str, Any]]:
    from .tracking_common import read_csv_rows

    stations: list[dict[str, Any]] = []
    for row in read_csv_rows(path):
        stations.append({
            "station_triplet": row["station_triplet"],
            "station_name": row["station_name"],
            "state": row["state"],
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "elevation_m": float(row["elevation_m"]),
            "data_tz_offset_hours": finite(row.get("data_tz_offset_hours")),
            "nearest_resort": row["nearest_resort"],
            "nearest_resort_region": row["nearest_resort_region"],
            "nearest_resort_country": row["nearest_resort_country"],
            "distance_km": float(row["distance_km"]),
        })
    return stations


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------

def water_year(day: date) -> int:
    return day.year + 1 if day.month >= 10 else day.year


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return months


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def hrrr_month_plan() -> list[tuple[int, int]]:
    """HRRR months: Oct-May of WY2024/2025/2026 plus Oct 2026.

    Processing order front-loads WY2025 and WY2026 (the minimum complete
    deliverable), then Oct 2026, then WY2024, so an interrupted run still
    leaves the most valuable water years fully cached.
    """
    def block(wy: int) -> list[tuple[int, int]]:
        return [(wy - 1, 10), (wy - 1, 11), (wy - 1, 12)] + [(wy, m) for m in range(1, 6)]

    return block(2025) + block(2026) + [(2026, 10)] + block(2024)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_read(path: Path, kind: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("kind") != kind:
        return None
    return payload


def _cache_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# AWDB observations
# ---------------------------------------------------------------------------

def _station_meta(stations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        station["station_triplet"]: {
            "station_name": station["station_name"],
            "state": station["state"],
            "latitude": station["latitude"],
            "longitude": station["longitude"],
            "elevation_m": station["elevation_m"],
        }
        for station in stations
    }


def fetch_awdb_observations(
    stations: list[dict[str, Any]],
    start: date,
    end: date,
    cache_dir: Path,
    client: RateLimitedClient,
    progress: Callable[[str], None] = print,
) -> tuple[dict[str, list[dict[str, str]]], dict[str, Any]]:
    """Fetch daily AWDB rows per (50-triplet batch, calendar month), cached."""
    meta = _station_meta(stations)
    triplets = sorted(meta)
    batches = [triplets[i:i + AWDB_BATCH_STATIONS] for i in range(0, len(triplets), AWDB_BATCH_STATIONS)]
    months = months_between(start, end)
    rows_by_triplet: dict[str, list[dict[str, str]]] = {triplet: [] for triplet in triplets}
    report: dict[str, Any] = {
        "planned_chunks": len(batches) * len(months),
        "fetched_chunks": 0,
        "cached_chunks": 0,
        "failed_chunks": [],
    }
    for batch_index, batch in enumerate(batches):
        for year, month in months:
            month_start, month_end = month_bounds(year, month)
            begin = max(month_start, start)
            finish = min(month_end, end)
            cache_path = cache_dir / f"awdb_rows_b{batch_index:02d}_{year:04d}{month:02d}.json"
            cached = _cache_read(cache_path, "awdb_rows")
            if cached is not None:
                report["cached_chunks"] += 1
                chunk_rows = cached["rows"]
            else:
                url = awdb_data_url(batch, begin, finish)
                try:
                    raw = client.get_bytes(url)
                except (NoDataError, FetchFailedError) as exc:
                    report["failed_chunks"].append({
                        "batch": batch_index, "month": f"{year:04d}-{month:02d}", "error": str(exc),
                    })
                    progress(f"AWDB batch {batch_index} {year}-{month:02d}: FAILED ({exc})")
                    continue
                payload = json.loads(raw.decode("utf-8"))
                chunk_rows = parse_awdb_data(payload, meta)
                _cache_write(cache_path, {
                    "kind": "awdb_rows",
                    "batch_index": batch_index,
                    "month": f"{year:04d}-{month:02d}",
                    "url": url,
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    "fetched_at_utc": iso_z(utc_now()),
                    "rows": chunk_rows,
                })
                report["fetched_chunks"] += 1
                progress(f"AWDB batch {batch_index} {year}-{month:02d}: {len(chunk_rows)} rows")
            for row in chunk_rows:
                rows_by_triplet[str(row["station_triplet"])].append(row)
    for triplet in rows_by_triplet:
        rows_by_triplet[triplet].sort(key=lambda row: row["date"])
    report["row_count"] = sum(len(rows) for rows in rows_by_triplet.values())
    return rows_by_triplet, report


# ---------------------------------------------------------------------------
# Observation derivation (metric + deltas + weak QC)
# ---------------------------------------------------------------------------

def _f_to_c(value: float | None) -> float | None:
    return None if value is None else (value - 32.0) * 5.0 / 9.0


def _round4(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def derive_station_days(
    station: dict[str, Any],
    legacy_rows: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """Metric daily fields, deltas, differenced precip, and weak-QC verdicts.

    ``legacy_rows`` use the existing SNOTEL CSV schema (inches / degF).
    The weak QC verdict comes from tracking_events.evaluate_weak_candidate
    itself, so thresholds can never diverge from the tracking loop.
    """
    stub_resort = {
        "resort_slug": "hrrr-dataset",
        "timezone": "UTC",
        "climate_regime": "unclassified",
        "snotel_station": {
            "station_triplet": station["station_triplet"],
            "station_name": station["station_name"],
            "state": station["state"],
            "latitude": station["latitude"],
            "longitude": station["longitude"],
            "elevation_m": station["elevation_m"],
        },
    }
    derived: dict[str, dict[str, Any]] = {}
    ordered = sorted(legacy_rows, key=lambda row: row["date"])
    for index, row in enumerate(ordered):
        day = date.fromisoformat(row["date"])
        previous = ordered[index - 1] if index > 0 else None
        prev_day = date.fromisoformat(previous["date"]) if previous else None
        consecutive = previous is not None and prev_day is not None and (day - prev_day).days == 1

        swe_in = finite(row.get("swe_inches"))
        depth_in = finite(row.get("snow_depth_inches"))
        accum_in = finite(row.get("precip_accum_inches"))
        swe_mm = None if swe_in is None else swe_in * INCH_TO_MM
        depth_mm = None if depth_in is None else depth_in * INCH_TO_MM
        accum_mm = None if accum_in is None else accum_in * INCH_TO_MM

        flags: list[str] = []
        delta_swe_mm: float | None = None
        delta_depth_mm: float | None = None
        if previous is not None and not consecutive:
            flags.append("nonconsecutive_previous")
        if consecutive and previous is not None:
            prev_swe = finite(previous.get("swe_inches"))
            prev_depth = finite(previous.get("snow_depth_inches"))
            if swe_in is not None and prev_swe is not None:
                delta_swe_mm = (swe_in - prev_swe) * INCH_TO_MM
            if depth_in is not None and prev_depth is not None:
                delta_depth_mm = (depth_in - prev_depth) * INCH_TO_MM

        precip_mm: float | None = None
        if accum_mm is None:
            flags.append("precip_missing")
        elif day.month == 10 and day.day == 1:
            # Water-year accumulation restarts: the Oct 1 accumulated value
            # is the day's precipitation (flagged as a reset estimate).
            if accum_mm < 0:
                flags.append("precip_negative")
            else:
                precip_mm = accum_mm
                flags.append("precip_wy_reset")
        elif previous is None or not consecutive:
            flags.append("precip_gap")
        else:
            prev_accum = finite(previous.get("precip_accum_inches"))
            if prev_accum is None:
                flags.append("precip_missing")
            else:
                diff = (accum_in - prev_accum) * INCH_TO_MM
                if diff < 0:
                    flags.append("precip_negative")
                else:
                    precip_mm = diff

        weak_qc_passed: bool | None = None
        weak_qc_rejection: str | None = None
        observed_slr_diff: float | None = None
        weak_qc_weight: float | None = None
        if previous is not None:
            event, reason = evaluate_weak_candidate(previous, row, stub_resort)
            weak_qc_passed = event is not None
            if event is not None:
                observed_slr_diff = float(event["observed_slr"])
                weak_qc_weight = WEAK_QC_WEIGHT
            else:
                weak_qc_rejection = reason

        derived[row["date"]] = {
            "swe_mm": _round4(swe_mm),
            "snow_depth_mm": _round4(depth_mm),
            "precip_accum_mm": _round4(accum_mm),
            "tavg_c": _round4(_f_to_c(finite(row.get("tavg_f")))),
            "tmax_c": _round4(_f_to_c(finite(row.get("tmax_f")))),
            "tmin_c": _round4(_f_to_c(finite(row.get("tmin_f")))),
            "delta_swe_mm": _round4(delta_swe_mm),
            "delta_depth_mm": _round4(delta_depth_mm),
            "precip_mm": _round4(precip_mm),
            "obs_flags": ";".join(flags),
            "weak_qc_passed": weak_qc_passed,
            "weak_qc_rejection": weak_qc_rejection,
            "observed_slr_diff": observed_slr_diff,
            "weak_qc_weight": weak_qc_weight,
        }
    return derived


# ---------------------------------------------------------------------------
# Open-Meteo HRRR aggregation
# ---------------------------------------------------------------------------

def hrrr_url(stations: list[dict[str, Any]], start: date, end: date) -> str:
    query = urllib.parse.urlencode({
        "latitude": ",".join(f"{float(station['latitude']):.4f}" for station in stations),
        "longitude": ",".join(f"{float(station['longitude']):.4f}" for station in stations),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY_FIELDS),
        "models": HRRR_MODEL,
        "timezone": "UTC",
    })
    return f"{OPEN_METEO_ENDPOINT}?{query}"


def _normalized_hours(loc_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """profiles._response_hours with missing fields filled as all-null."""
    hourly = loc_payload.get("hourly") if isinstance(loc_payload, dict) else None
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        return []
    count = len(hourly["time"])
    normalized = dict(loc_payload)
    filled = dict(hourly)
    for field in HOURLY_FIELDS:
        if not isinstance(filled.get(field), list) or len(filled[field]) != count:
            filled[field] = [None] * count
    normalized["hourly"] = filled
    return _response_hours(normalized)


def aggregate_station_month(
    loc_payload: dict[str, Any] | None,
    station: dict[str, Any],
    year: int,
    month: int,
) -> dict[str, dict[str, Any]]:
    """Aggregate one location-month of hourly HRRR to station-local days.

    Days use the station's fixed AWDB dataTimeZone offset (a documented
    simplification: no DST rules).  Interval attribution follows the
    package's established (t-1, t] convention via compute_baseline_slr.
    """
    _, month_end = month_bounds(year, month)
    days = [date(year, month, d) for d in range(1, month_end.day + 1)]
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
    }
    if loc_payload is None:
        return {day.isoformat(): dict(empty) for day in days}
    hours = _normalized_hours(loc_payload)
    if not hours:
        return {day.isoformat(): dict(empty) for day in days}
    by_instant = {_parse_instant(str(hour["time"])): hour for hour in hours}
    offset = station.get("data_tz_offset_hours")
    offset_td = timedelta(hours=float(offset)) if offset is not None else timedelta(0)
    elevation = float(station["elevation_m"])

    aggregated: dict[str, dict[str, Any]] = {}
    for day in days:
        start_utc = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - offset_td
        end_utc = start_utc + timedelta(hours=24)
        endpoints = [start_utc + timedelta(hours=k) for k in range(25)]
        present = [(instant, by_instant[instant]) for instant in endpoints if instant in by_instant]
        currents = [(instant, hour) for instant, hour in present if instant > start_utc]
        agg = dict(empty)
        agg["hrrr_hours"] = len(currents)
        if currents:
            snowfall = [hour["snowfallCm"] for _, hour in currents if hour.get("snowfallCm") is not None]
            precip = [hour["precipitationMm"] for _, hour in currents if hour.get("precipitationMm") is not None]
            t2m = [hour["temperature2mC"] for _, hour in currents if hour.get("temperature2mC") is not None]
            freezing = [hour["freezingLevelHeightM"] for _, hour in currents if hour.get("freezingLevelHeightM") is not None]
            if snowfall:
                agg["fcst_snowfall_cm"] = round(sum(snowfall), 4)
            if precip:
                agg["fcst_precip_mm"] = round(sum(precip), 4)
            if t2m:
                agg["fcst_t2m_mean_c"] = round(sum(t2m) / len(t2m), 4)
                agg["fcst_t2m_min_c"] = round(min(t2m), 4)
                agg["fcst_t2m_max_c"] = round(max(t2m), 4)
            if freezing:
                agg["fcst_freezing_level_m"] = round(sum(freezing) / len(freezing), 2)
            day_hours = [hour for _, hour in present]
            computed = compute_baseline_slr(day_hours, start_utc, end_utc, elevation)
            if computed is not None:
                slr, evaluated_hours, total_weight = computed
                agg["fcst_kuchera_slr"] = slr
                agg["fcst_kuchera_hours"] = evaluated_hours
                agg["fcst_kuchera_weight_mm"] = total_weight
        aggregated[day.isoformat()] = agg
    return aggregated


def _fetch_om_group(
    group: list[dict[str, Any]],
    start: date,
    end: date,
    client: RateLimitedClient,
) -> dict[str, dict[str, Any] | None]:
    """Fetch one location group; bisect on HTTP 400 to isolate no-data points."""
    url = hrrr_url(group, start, end)
    try:
        payload = client.get_json(url)
    except NoDataError:
        if len(group) == 1:
            return {group[0]["station_triplet"]: None}
        mid = len(group) // 2
        merged = _fetch_om_group(group[:mid], start, end, client)
        merged.update(_fetch_om_group(group[mid:], start, end, client))
        return merged
    locations = payload if isinstance(payload, list) else [payload]
    if len(locations) != len(group):
        raise FetchFailedError(
            f"Open-Meteo returned {len(locations)} locations for {len(group)} requested"
        )
    return {station["station_triplet"]: locations[index] for index, station in enumerate(group)}


def fetch_hrrr_daily(
    stations: list[dict[str, Any]],
    cache_dir: Path,
    client: RateLimitedClient,
    months: list[tuple[int, int]] | None = None,
    progress: Callable[[str], None] = print,
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, Any]]:
    """Fetch + aggregate HRRR per (25-location batch, month), cached as daily rows.

    Returns ({triplet: {date: agg}}, report).  Stations in states outside the
    CONUS HRRR domain (AK) are never requested.
    """
    plan = months if months is not None else hrrr_month_plan()
    conus = [station for station in stations if station["state"] != "AK"]
    batches = [conus[i:i + OPEN_METEO_BATCH_LOCATIONS] for i in range(0, len(conus), OPEN_METEO_BATCH_LOCATIONS)]
    daily: dict[str, dict[str, dict[str, Any]]] = {station["station_triplet"]: {} for station in stations}
    no_data_months: dict[str, set[str]] = {station["station_triplet"]: set() for station in stations}
    report: dict[str, Any] = {
        "planned_chunks": len(batches) * len(plan),
        "fetched_chunks": 0,
        "cached_chunks": 0,
        "failed_chunks": [],
        "excluded_non_conus_stations": sorted(
            station["station_triplet"] for station in stations if station["state"] == "AK"
        ),
    }
    for year, month in plan:
        month_start, month_end = month_bounds(year, month)
        padded_start = month_start - timedelta(days=1)
        padded_end = month_end + timedelta(days=1)
        for batch_index, batch in enumerate(batches):
            cache_path = cache_dir / f"om_daily_b{batch_index:02d}_{year:04d}{month:02d}.json"
            cached = _cache_read(cache_path, "om_daily")
            if cached is not None:
                report["cached_chunks"] += 1
                station_days = cached["station_days"]
                chunk_no_data = set(cached.get("no_data_triplets") or [])
            else:
                try:
                    locations = _fetch_om_group(batch, padded_start, padded_end, client)
                except FetchFailedError as exc:
                    report["failed_chunks"].append({
                        "batch": batch_index, "month": f"{year:04d}-{month:02d}", "error": str(exc),
                    })
                    progress(f"Open-Meteo batch {batch_index} {year}-{month:02d}: FAILED ({exc})")
                    continue
                station_days = {}
                chunk_no_data = set()
                for station in batch:
                    triplet = station["station_triplet"]
                    loc_payload = locations.get(triplet)
                    month_hours = 0
                    if loc_payload is not None:
                        aggregated = aggregate_station_month(loc_payload, station, year, month)
                        month_hours = sum(int(agg["hrrr_hours"]) for agg in aggregated.values())
                        if month_hours > 0:
                            station_days[triplet] = aggregated
                    if loc_payload is None or month_hours == 0:
                        chunk_no_data.add(triplet)
                _cache_write(cache_path, {
                    "kind": "om_daily",
                    "batch_index": batch_index,
                    "month": f"{year:04d}-{month:02d}",
                    "model": HRRR_MODEL,
                    "request_window": [padded_start.isoformat(), padded_end.isoformat()],
                    "fetched_at_utc": iso_z(utc_now()),
                    "station_days": station_days,
                    "no_data_triplets": sorted(chunk_no_data),
                })
                report["fetched_chunks"] += 1
                progress(
                    f"Open-Meteo batch {batch_index} {year}-{month:02d}: "
                    f"{len(station_days)} stations with data, {len(chunk_no_data)} no-data"
                )
            for triplet, days in station_days.items():
                daily[triplet].update(days)
            for triplet in chunk_no_data:
                no_data_months[triplet].add(f"{year:04d}-{month:02d}")
    report["no_data_months"] = {triplet: sorted(months_) for triplet, months_ in no_data_months.items() if months_}
    report["stations_with_data"] = sum(1 for days in daily.values() if days)
    return daily, report


# ---------------------------------------------------------------------------
# Joined dataset build
# ---------------------------------------------------------------------------

def _parquet_schema() -> Any:
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
        ("swe_mm", pa.float64()),
        ("snow_depth_mm", pa.float64()),
        ("precip_accum_mm", pa.float64()),
        ("tavg_c", pa.float64()),
        ("tmax_c", pa.float64()),
        ("tmin_c", pa.float64()),
        ("delta_swe_mm", pa.float64()),
        ("delta_depth_mm", pa.float64()),
        ("precip_mm", pa.float64()),
        ("obs_flags", pa.string()),
        ("weak_qc_passed", pa.bool_()),
        ("weak_qc_rejection", pa.string()),
        ("observed_slr_diff", pa.float64()),
        ("weak_qc_weight", pa.float64()),
        ("hrrr_available", pa.bool_()),
        ("hrrr_status", pa.string()),
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
    ])


def _write_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=_parquet_schema())
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp)
    os.replace(tmp, path)


def _null_rate(rows: list[dict[str, Any]], column: str) -> float | None:
    if not rows:
        return None
    missing = sum(1 for row in rows if row.get(column) is None)
    return round(missing / len(rows), 4)


def build_dataset(
    *,
    ski_areas_path: Path,
    dataset_dir: Path,
    obs_start: date = OBS_START_DEFAULT,
    obs_end: date | None = None,
    bytes_fetcher: Fetcher | None = None,
    awdb_interval_s: float = AWDB_MIN_INTERVAL_S,
    open_meteo_interval_s: float = OPEN_METEO_MIN_INTERVAL_S,
    hrrr_months: list[tuple[int, int]] | None = None,
    today: date | None = None,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Build the full joined dataset; returns the validation report dict."""
    started = time.monotonic()
    today = today or date.today()
    obs_end = obs_end or today
    fetcher = bytes_fetcher or fetch_bytes
    cache_dir = dataset_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    awdb_client = RateLimitedClient(fetcher, awdb_interval_s)
    om_client = RateLimitedClient(fetcher, open_meteo_interval_s)

    # --- request budget (printed before the bulk fetching starts) ---
    obs_month_count = len(months_between(obs_start, obs_end))
    plan = hrrr_months if hrrr_months is not None else hrrr_month_plan()
    progress(
        "Request budget: AWDB stations list <= 1 request; "
        f"AWDB data <= {math.ceil(499 / AWDB_BATCH_STATIONS)} batches x {obs_month_count} months; "
        f"Open-Meteo <= {math.ceil(499 / OPEN_METEO_BATCH_LOCATIONS)} batches x {len(plan)} months "
        "(exact counts follow once the station set is known; cached chunks cost 0)."
    )

    # --- station set ---
    stations_path = dataset_dir / "stations.csv"
    if stations_path.exists():
        stations = load_stations_csv(stations_path)
        stations_source = "stations.csv (cached)"
    else:
        raw_stations = awdb_client.get_json(AWDB_STATIONS_URL)
        if not isinstance(raw_stations, list):
            raise ValueError("AWDB stations payload is not a list")
        _cache_write(cache_dir / "awdb_stations_raw.json", {
            "kind": "awdb_stations_raw",
            "url": AWDB_STATIONS_URL,
            "fetched_at_utc": iso_z(utc_now()),
            "payload_sha256": hashlib.sha256(json.dumps(raw_stations, sort_keys=True).encode()).hexdigest(),
            "station_count": len(raw_stations),
        })
        stations = build_station_set(ski_areas_path, raw_stations, today)
        write_csv_rows(stations_path, stations, STATION_COLUMNS)
        stations_source = "regenerated from AWDB stations list + ski-area catalog"
    progress(f"Station set: {len(stations)} stations ({stations_source})")

    conus_count = sum(1 for station in stations if station["state"] != "AK")
    awdb_batches = math.ceil(len(stations) / AWDB_BATCH_STATIONS) if stations else 0
    om_batches = math.ceil(conus_count / OPEN_METEO_BATCH_LOCATIONS) if conus_count else 0
    progress(
        f"Planned chunks: AWDB {awdb_batches} batches x {obs_month_count} months = "
        f"{awdb_batches * obs_month_count} requests max; Open-Meteo {om_batches} batches x "
        f"{len(plan)} months = {om_batches * len(plan)} requests max (plus any 400-bisection retries)."
    )

    # --- observations ---
    rows_by_triplet, awdb_report = fetch_awdb_observations(
        stations, obs_start, obs_end, cache_dir, awdb_client, progress,
    )

    # --- HRRR daily aggregates ---
    hrrr_daily, hrrr_report = fetch_hrrr_daily(stations, cache_dir, om_client, plan, progress)
    hrrr_month_set = {f"{year:04d}-{month:02d}" for year, month in plan}
    failed_om = {f"{chunk['batch']}|{chunk['month']}" for chunk in hrrr_report["failed_chunks"]}
    failed_awdb = {f"{chunk['batch']}|{chunk['month']}" for chunk in awdb_report["failed_chunks"]}
    # Batch membership for failure attribution.
    sorted_triplets = sorted(station["station_triplet"] for station in stations)
    awdb_batch_of = {triplet: index // AWDB_BATCH_STATIONS for index, triplet in enumerate(sorted_triplets)}
    conus_sorted = [station["station_triplet"] for station in sorted(stations, key=lambda s: s["station_triplet"]) if station["state"] != "AK"]
    om_batch_of = {triplet: index // OPEN_METEO_BATCH_LOCATIONS for index, triplet in enumerate(conus_sorted)}

    stations_with_hrrr = {
        station["station_triplet"]
        for station in stations
        if any(int(agg.get("hrrr_hours") or 0) > 0 for agg in hrrr_daily.get(station["station_triplet"], {}).values())
    }

    # --- derive + join ---
    rows: list[dict[str, Any]] = []
    for station in stations:
        triplet = station["station_triplet"]
        derived = derive_station_days(station, rows_by_triplet.get(triplet, []))
        forecast_days = hrrr_daily.get(triplet, {})
        no_data_months = set(hrrr_report["no_data_months"].get(triplet, []))
        all_dates = sorted(set(derived) | set(forecast_days))
        for date_text in all_dates:
            day = date.fromisoformat(date_text)
            obs = derived.get(date_text)
            agg = forecast_days.get(date_text) or {}
            obs_flags = list(filter(None, (obs or {}).get("obs_flags", "").split(";")))
            if obs is None and triplet in awdb_batch_of and f"{awdb_batch_of[triplet]}|{date_text[:7]}" in failed_awdb:
                obs_flags.append("obs_fetch_failed")

            month_key = date_text[:7]
            if agg:
                status = "ok" if int(agg.get("hrrr_hours") or 0) > 0 else "no_coverage"
            elif month_key in no_data_months:
                status = "no_coverage"
            elif triplet in om_batch_of and f"{om_batch_of[triplet]}|{month_key}" in failed_om:
                status = "fetch_failed"
            elif month_key not in hrrr_month_set or station["state"] == "AK":
                status = "not_requested"
            else:
                status = "no_coverage"

            row: dict[str, Any] = {
                "station_triplet": triplet,
                "station_name": station["station_name"],
                "state": station["state"],
                "latitude": station["latitude"],
                "longitude": station["longitude"],
                "elevation_m": station["elevation_m"],
                "data_tz_offset_hours": station["data_tz_offset_hours"],
                "nearest_resort": station["nearest_resort"],
                "distance_km": station["distance_km"],
                "date": date_text,
                "water_year": water_year(day),
                "swe_mm": None, "snow_depth_mm": None, "precip_accum_mm": None,
                "tavg_c": None, "tmax_c": None, "tmin_c": None,
                "delta_swe_mm": None, "delta_depth_mm": None, "precip_mm": None,
                "obs_flags": ";".join(obs_flags),
                "weak_qc_passed": None, "weak_qc_rejection": None,
                "observed_slr_diff": None, "weak_qc_weight": None,
                "hrrr_available": triplet in stations_with_hrrr,
                "hrrr_status": status,
                "hrrr_hours": agg.get("hrrr_hours"),
                "fcst_snowfall_cm": agg.get("fcst_snowfall_cm"),
                "fcst_precip_mm": agg.get("fcst_precip_mm"),
                "fcst_kuchera_slr": agg.get("fcst_kuchera_slr"),
                "fcst_kuchera_hours": agg.get("fcst_kuchera_hours"),
                "fcst_kuchera_weight_mm": agg.get("fcst_kuchera_weight_mm"),
                "fcst_t2m_mean_c": agg.get("fcst_t2m_mean_c"),
                "fcst_t2m_min_c": agg.get("fcst_t2m_min_c"),
                "fcst_t2m_max_c": agg.get("fcst_t2m_max_c"),
                "fcst_freezing_level_m": agg.get("fcst_freezing_level_m"),
            }
            if obs is not None:
                for column in (
                    "swe_mm", "snow_depth_mm", "precip_accum_mm", "tavg_c", "tmax_c", "tmin_c",
                    "delta_swe_mm", "delta_depth_mm", "precip_mm",
                    "weak_qc_passed", "weak_qc_rejection", "observed_slr_diff", "weak_qc_weight",
                ):
                    row[column] = obs[column]
            rows.append(row)

    rows.sort(key=lambda row: (row["station_triplet"], row["date"]))
    by_water_year: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_water_year.setdefault(int(row["water_year"]), []).append(row)
    daily_dir = dataset_dir / "daily"
    for wy, wy_rows in sorted(by_water_year.items()):
        _write_parquet(daily_dir / f"water_year={wy}" / "data.parquet", wy_rows)

    elapsed = round(time.monotonic() - started, 1)
    report: dict[str, Any] = {
        "generated_at_utc": iso_z(utc_now()),
        "dataset_dir": str(dataset_dir),
        "obs_window": [obs_start.isoformat(), obs_end.isoformat()],
        "hrrr_months": [f"{year:04d}-{month:02d}" for year, month in plan],
        "stations": {
            "count": len(stations),
            "source": stations_source,
            "by_state": dict(sorted(Counter(station["state"] for station in stations).items())),
            "with_hrrr_data": len(stations_with_hrrr),
            "no_coverage_stations": sorted(
                station["station_triplet"] for station in stations
                if station["state"] != "AK" and station["station_triplet"] not in stations_with_hrrr
            ),
            "non_conus_stations": sorted(
                station["station_triplet"] for station in stations if station["state"] == "AK"
            ),
        },
        "observations": awdb_report,
        "hrrr": {
            key: hrrr_report[key]
            for key in ("planned_chunks", "fetched_chunks", "cached_chunks", "failed_chunks", "stations_with_data")
        },
        "requests": {
            "awdb": awdb_client.stats(),
            "open_meteo": om_client.stats(),
        },
        "output": {
            "total_rows": len(rows),
            "rows_by_water_year": {str(wy): len(wy_rows) for wy, wy_rows in sorted(by_water_year.items())},
            "stations_with_obs_days": sum(1 for station in stations if rows_by_triplet.get(station["station_triplet"])),
            "qc_passed_event_days": sum(1 for row in rows if row["weak_qc_passed"] is True),
            "days_with_hrrr": sum(1 for row in rows if row["hrrr_status"] == "ok"),
            "null_rates": {
                column: _null_rate(rows, column)
                for column in (
                    "swe_mm", "snow_depth_mm", "delta_swe_mm", "precip_mm",
                    "observed_slr_diff", "fcst_snowfall_cm", "fcst_kuchera_slr", "fcst_t2m_mean_c",
                )
            },
        },
        "elapsed_seconds": elapsed,
    }
    write_json(dataset_dir / "validation_report.json", report)
    progress(
        f"Dataset built: {len(rows)} rows across {len(by_water_year)} water years in {elapsed}s; "
        f"AWDB requests {report['requests']['awdb']}, Open-Meteo requests {report['requests']['open_meteo']}"
    )
    return report
