"""Step 2 (gold-only model): profile fetching and feature building.

PROFILE SOURCE DECISION (read before questioning the endpoint):
Pletcher et al. (2026) trained on ERA5.  ERA5 pressure levels are NOT
reachable from this workspace:

* The Open-Meteo archive API (archive-api.open-meteo.com/v1/archive,
  the ERA5 endpoint) returns HTTP 200 with every pressure-level field
  null and ``hourly_units`` "undefined", with and without
  ``models=era5``, at lowland and mountain stations alike (probed
  2026-10-19).  Its published variable list contains no pressure-level
  variables at all, so this is structural, not a quota artefact.
* Copernicus CDS (the other ERA5 route) requires a personal API key,
  which this project does not hold and does not solicit.

The fallback sanctioned for exactly this case is the Open-Meteo
historical-forecast **best-match** archive
(historical-forecast-api.open-meteo.com) -- the same archive every
previous baseline in this project was computed from, so features and
Kuchera baselines share provenance.  Every output of this module and
of ``gold_model`` labels the source ``open-meteo-historical-forecast-
best-match``; it is NOT ERA5 and must never be reported as ERA5.
Deviations from the Pletcher spec that follow from this: (1) profile
source is the best-match forecast archive, itself a model blend whose
contributing model varies by date; (2) archive coverage starts
mid-2021 -- events before 2021-06-01 are marked
``outside_archive_coverage`` and excluded, never backfilled from
another source; (3) pressure levels are the API's 13-level set
(1000..500 hPa) instead of ERA5's 37; (4) wind is the API's
``wind_speed`` field (m/s, requested explicitly).

Features follow Pletcher et al. (2026): temperature, wind speed and
relative humidity interpolated to 300..2400 m AGL in 300 m steps from
each hour's pressure-level profile (linear in geopotential height,
clamped at the profile ends), averaged over the event's 24-h
observation window (the 24 h ending at the stored ``valid_time``,
local standard observation time), plus latitude, longitude and station
elevation: 27 features.  The Kuchera baseline for the same window is
computed with the project's established Python path
(``tracking_align.compute_baseline_slr``) over hour records built from
the same responses, so baseline and features share provenance.

UPDATE (2026-10-20): a true-ERA5 route was verified and adopted.  The
Open-Meteo best-match campaign was stopped at 1,812 of 13,660 chunks
(per-IP daily cap; the cache is kept as a comparison set).  ERA5 itself
is now fetched from the NSF NCAR mirror of ERA5 on AWS Open Data
(bucket ``nsf-ncar-era5``, anonymous read; see
``gold_profiles_era5``).  The original Intertrust bucket ``era5-pds``
was probed the same day and is NOT anonymously readable (ListBucket
and GetObject both return HTTP 403), so the NCAR mirror -- the same
ECMWF ERA5 reanalysis, curated by NSF NCAR -- is the source of record;
its source id is ``era5-nsf-ncar-aws``.  ERA5 coverage (verified by
listing: pressure-level months 1940-01 through 2026-07) removes the
2021-06-01 eligibility floor.  Source selection is explicit via
``SourceSpec``; the best-match path above is unchanged and remains the
default for the original commands.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .tracking_align import compute_baseline_slr

ENDPOINT = "https://historical-forecast-api.open-meteo.com/v1/forecast"
PROFILE_SOURCE = "open-meteo-historical-forecast-best-match"
PRESSURE_LEVELS_HPA = (1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500)
AGL_TARGETS_M = (300, 600, 900, 1200, 1500, 1800, 2100, 2400)
ARCHIVE_START = date(2021, 6, 1)  # probed coverage start (see docstring)
ERA5_PROFILE_SOURCE = "era5-nsf-ncar-aws"
ERA5_COVERAGE_START = date(1940, 1, 1)
ERA5_COVERAGE_END = date(2026, 7, 31)  # latest verified month: 202607


@dataclass(frozen=True)
class SourceSpec:
    """One profile source: provenance id, cache subdir, coverage window.

    An event is fetch-eligible when its observation_date lies inside
    [coverage_start, coverage_end] (end None = unbounded).  Events
    outside the window are reported as ``outside_archive_coverage`` by
    build_features, never silently dropped.
    """

    source_id: str
    cache_dir_name: str
    coverage_start: date
    coverage_end: date | None = None

    def covers(self, day: date) -> bool:
        if day < self.coverage_start:
            return False
        return self.coverage_end is None or day <= self.coverage_end


BEST_MATCH_SPEC = SourceSpec(PROFILE_SOURCE, "raw_cache", ARCHIVE_START)
ERA5_SPEC = SourceSpec(ERA5_PROFILE_SOURCE, "raw_cache_era5", ERA5_COVERAGE_START, ERA5_COVERAGE_END)

# Year expansion (2026-10-24): the same ECMWF ERA5 reanalysis fetched
# through the Copernicus Climate Data Store (cds.climate.copernicus.eu)
# with the user's own CDS API key (~/.cdsapirc, written by the user;
# see gold_profiles_cds).  Provenance stays distinct from the NCAR
# mirror because the retrieval route and accumulation cycles differ.
CDS_PROFILE_SOURCE = "era5-cds"
CDS_SPEC = SourceSpec(CDS_PROFILE_SOURCE, "raw_cache_cds", ERA5_COVERAGE_START, ERA5_COVERAGE_END)
MIN_VALID_HOURS = 18  # of the 24 window hours, for a complete feature set
USER_AGENT = "MySnow-SLR-research/0.1"

HOURLY_FIELDS = (
    "temperature_2m",
    "precipitation",
    "snowfall",
    *(f"temperature_{level}hPa" for level in PRESSURE_LEVELS_HPA),
    *(f"relative_humidity_{level}hPa" for level in PRESSURE_LEVELS_HPA),
    *(f"wind_speed_{level}hPa" for level in PRESSURE_LEVELS_HPA),
    *(f"geopotential_height_{level}hPa" for level in PRESSURE_LEVELS_HPA),
)

FEATURE_COLUMNS = (
    *(f"t_{h}" for h in AGL_TARGETS_M),
    *(f"ws_{h}" for h in AGL_TARGETS_M),
    *(f"rh_{h}" for h in AGL_TARGETS_M),
    "latitude",
    "longitude",
    "elevation_m",
)

REGION_FILES = {
    "rockies": "events_rockies.csv",
    "sierra-nevada": "events_sierra-nevada.csv",
    "pacific-northwest": "events_pacific-northwest.csv",
    "northeast": "events_northeast.csv",
    "great-lakes": "events_great-lakes.csv",
    "alaska": "events_alaska.csv",
}


class DailyCapError(RuntimeError):
    """Open-Meteo reported the per-IP daily request cap."""


class HourlyCapError(RuntimeError):
    """Open-Meteo reported the per-IP hourly request cap."""


def _finite(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isfinite(number):
            return number
    return None


def _float_or_none(value: Any) -> float | None:
    try:
        return _finite(float(value))
    except (TypeError, ValueError):
        return None


def water_year(day: date) -> int:
    return day.year + 1 if day.month >= 10 else day.year


def load_screened_events(pool_dir: Path) -> list[dict[str, Any]]:
    """Load the step-1 screened-v2 pool, tagging each event with its region."""
    events: list[dict[str, Any]] = []
    for region, filename in REGION_FILES.items():
        path = pool_dir / filename
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                valid_time = datetime.fromisoformat(str(row["valid_time"]))
                events.append({
                    "event_id": str(row["event_id"]),
                    "region": region,
                    "station_id": str(row["station_id"]),
                    "site_id": str(row.get("site_id") or row["station_id"]),
                    "station_name": str(row.get("station_name") or ""),
                    "state": str(row.get("state") or ""),
                    "valid_time": valid_time,
                    "observation_date": str(row["observation_date"]),
                    "water_year": water_year(valid_time.date()),
                    "month": valid_time.month,
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                    "elevation_m": _float_or_none(row.get("elevation_m")),
                    "observed_slr": float(row["observed_slr"]),
                    "fresh_snow_mm": float(row["fresh_snow_mm"]),
                    "fresh_swe_mm": float(row["fresh_swe_mm"]),
                    "climate_regime": str(row.get("climate_regime") or "unclassified"),
                    "source_tier": str(row.get("source_tier") or ""),
                })
    return events


def event_window(event: dict[str, Any]) -> tuple[datetime, datetime]:
    """(start, end] of the event's 24-h observation window."""
    end = event["valid_time"]
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end - timedelta(hours=24), end


# ---------------------------------------------------------------------------
# Fetch planning and downloading
# ---------------------------------------------------------------------------

def plan_chunks(
    events: list[dict[str, Any]],
    gap_days: int = 4,
    spec: SourceSpec = BEST_MATCH_SPEC,
) -> list[dict[str, Any]]:
    """Group each station's fetch-eligible event windows into date chunks.

    A chunk covers a run of observation dates separated by at most
    ``gap_days``; the request range is padded one UTC day on both sides
    so every window endpoint is inside the response.  Eligibility is
    governed by ``spec`` (elevation present and observation_date inside
    the source's verified coverage window).
    """
    by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event["elevation_m"] is None:
            continue  # no AGL reference; build_features marks these missing_station_metadata
        if spec.covers(date.fromisoformat(event["observation_date"])):
            by_station[event["station_id"]].append(event)
    chunks: list[dict[str, Any]] = []
    for station_id, station_events in sorted(by_station.items()):
        station_events.sort(key=lambda e: e["valid_time"])
        runs: list[list[dict[str, Any]]] = [[station_events[0]]]
        for event in station_events[1:]:
            previous = runs[-1][-1]
            gap = (event["valid_time"].date() - previous["valid_time"].date()).days
            if gap <= gap_days:
                runs[-1].append(event)
            else:
                runs.append([event])
        first = station_events[0]
        for run in runs:
            start = min(event_window(e)[0] for e in run).date() - timedelta(days=1)
            end = max(event_window(e)[1] for e in run).date() + timedelta(days=1)
            chunks.append({
                "key": f"{station_id}|{start.isoformat()}|{end.isoformat()}",
                "station_id": station_id,
                "latitude": first["latitude"],
                "longitude": first["longitude"],
                "start": start.isoformat(),
                "end": end.isoformat(),
                "event_ids": [e["event_id"] for e in run],
            })
    return chunks


def _request_url(chunk: dict[str, Any]) -> str:
    query = urllib.parse.urlencode({
        "latitude": f"{chunk['latitude']:.4f}",
        "longitude": f"{chunk['longitude']:.4f}",
        "start_date": chunk["start"],
        "end_date": chunk["end"],
        "hourly": ",".join(HOURLY_FIELDS),
        "timezone": "UTC",
        "wind_speed_unit": "ms",
    })
    return f"{ENDPOINT}?{query}"


def _download_json(url: str, retries: int = 6) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:
                pass
            if exc.code == 429 and "daily" in body.lower() and "limit" in body.lower():
                raise DailyCapError(body[:200]) from exc
            if exc.code == 429 and "hourly" in body.lower() and "limit" in body.lower():
                raise HourlyCapError(body[:200]) from exc
            last_error = exc
            if exc.code in (400,):
                raise
            time.sleep(min(60.0, 2.0 ** attempt * 2.0))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(min(60.0, 2.0 ** attempt * 2.0))
    assert last_error is not None
    raise last_error


class _Pacer:
    """Global minimum spacing between request starts across workers."""

    def __init__(self, min_interval_s: float) -> None:
        self._interval = min_interval_s
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


def _chunk_path(cache_dir: Path, chunk: dict[str, Any]) -> Path:
    return cache_dir / chunk["station_id"] / f"{chunk['start']}_{chunk['end']}.json.gz"


def _cache_valid(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        return isinstance(payload.get("hourly", {}).get("time"), list)
    except Exception:
        return False


def fetch_profiles(
    events: list[dict[str, Any]],
    cache_dir: Path,
    max_workers: int = 6,
    min_interval_s: float = 0.15,
    gap_days: int = 4,
) -> dict[str, Any]:
    """Download best-match profiles for all eligible event windows.

    Resumable: chunks already present and valid in ``cache_dir`` are
    skipped.  Stops scheduling new work when Open-Meteo reports the
    daily or hourly cap; completed chunks stay cached for the next run.
    """
    chunks = plan_chunks(events, gap_days=gap_days)
    cache_dir.mkdir(parents=True, exist_ok=True)
    pending = [c for c in chunks if not _cache_valid(_chunk_path(cache_dir, c))]
    already = len(chunks) - len(pending)
    pacer = _Pacer(min_interval_s)
    state = {"done": 0, "failed": 0, "capped": False, "hourly_capped": False}
    lock = threading.Lock()
    stop = threading.Event()

    def work(chunk: dict[str, Any]) -> None:
        if stop.is_set():
            return
        pacer.wait()
        try:
            payload = _download_json(_request_url(chunk))
            hourly = payload.get("hourly")
            if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
                raise ValueError("response lacks hourly time series")
            path = _chunk_path(cache_dir, chunk)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as handle:
                json.dump(payload, handle)
            tmp.replace(path)
            with lock:
                state["done"] += 1
        except DailyCapError:
            with lock:
                state["capped"] = True
            stop.set()
        except HourlyCapError:
            with lock:
                state["hourly_capped"] = True
            stop.set()
        except Exception:
            with lock:
                state["failed"] += 1

    if pending:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(work, chunk) for chunk in pending]
            for future in as_completed(futures):
                future.result()
    return {
        "profileSource": PROFILE_SOURCE,
        "chunksPlanned": len(chunks),
        "chunksAlreadyCached": already,
        "chunksDownloadedThisRun": state["done"],
        "chunksFailedThisRun": state["failed"],
        "dailyCapHit": state["capped"],
        "hourlyCapHit": state["hourly_capped"],
        "chunksCachedTotal": sum(1 for c in chunks if _cache_valid(_chunk_path(cache_dir, c))),
    }


# ---------------------------------------------------------------------------
# Feature building
# ---------------------------------------------------------------------------

def interpolate_agl(samples: list[tuple[float, float]], targets: Iterable[float]) -> list[float | None]:
    """Linear interpolation in height, clamped at the sample ends.

    ``samples`` are (height_m, value) pairs; they are sorted and
    de-duplicated by height.  Returns None per target when fewer than
    two samples exist.
    """
    ordered: list[tuple[float, float]] = []
    for height, value in sorted(samples):
        if ordered and abs(height - ordered[-1][0]) < 1e-6:
            ordered[-1] = (height, (ordered[-1][1] + value) / 2.0)
        else:
            ordered.append((height, value))
    if len(ordered) < 2:
        return [None for _ in targets]
    results: list[float | None] = []
    for target in targets:
        if target <= ordered[0][0]:
            results.append(ordered[0][1])
            continue
        if target >= ordered[-1][0]:
            results.append(ordered[-1][1])
            continue
        for (h0, v0), (h1, v1) in zip(ordered, ordered[1:]):
            if h0 <= target <= h1:
                frac = (target - h0) / (h1 - h0)
                results.append(v0 + frac * (v1 - v0))
                break
    return results


def hour_agl_values(
    levels: list[tuple[float, float | None, float | None, float | None, float | None]],
    station_elevation_m: float,
) -> tuple[list[float], list[float], list[float]] | None:
    """Interpolate one hour's levels to the AGL targets.

    ``levels`` are (pressure_hpa, temperature_c, rh_pct, wind_ms,
    geopotential_height_m).  Returns (temperatures, winds, rhs) at
    ``AGL_TARGETS_M`` or None when the hour cannot support the
    interpolation (fewer than 3 usable levels for any variable, or the
    profile's AGL span does not cover 300..2100 m).
    """
    by_var: dict[str, list[tuple[float, float]]] = {"t": [], "ws": [], "rh": []}
    for _pressure, temperature, rh, wind, height in levels:
        if height is None:
            continue
        if temperature is not None:
            by_var["t"].append((height, temperature))
        if wind is not None:
            by_var["ws"].append((height, wind))
        if rh is not None:
            by_var["rh"].append((height, rh))
    interpolated: dict[str, list[float | None]] = {}
    for var, samples in by_var.items():
        if len(samples) < 3:
            return None
        heights = [h - station_elevation_m for h, _ in samples]
        if min(heights) > AGL_TARGETS_M[0] or max(heights) < AGL_TARGETS_M[-2]:
            return None
        agl_samples = [(h - station_elevation_m, v) for h, v in samples]
        values = interpolate_agl(agl_samples, AGL_TARGETS_M)
        if any(value is None for value in values):
            return None
        interpolated[var] = values  # type: ignore[assignment]
    return interpolated["t"], interpolated["ws"], interpolated["rh"]


def _payload_hours(payload: dict[str, Any]) -> dict[datetime, dict[str, Any]]:
    """Parse one chunk response into instant -> hour record."""
    hourly = payload["hourly"]
    hours: dict[datetime, dict[str, Any]] = {}
    for index, time_text in enumerate(hourly["time"]):
        instant = datetime.fromisoformat(str(time_text))
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        levels = []
        for level in PRESSURE_LEVELS_HPA:
            levels.append((
                float(level),
                _finite(hourly[f"temperature_{level}hPa"][index]),
                _finite(hourly[f"relative_humidity_{level}hPa"][index]),
                _finite(hourly[f"wind_speed_{level}hPa"][index]),
                _finite(hourly[f"geopotential_height_{level}hPa"][index]),
            ))
        hours[instant] = {
            "temperature2mC": _finite(hourly["temperature_2m"][index]),
            "precipitationMm": _finite(hourly["precipitation"][index]),
            "snowfallCm": _finite(hourly["snowfall"][index]),
            "levels": levels,
        }
    return hours


def _kuchera_dict(instant: datetime, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "temperature2mC": record["temperature2mC"],
        "precipitationMm": record["precipitationMm"],
        "snowfallCm": record["snowfallCm"],
        "freezingLevelHeightM": None,
        "levels": [
            {
                "pressureHpa": pressure,
                "temperatureC": temperature,
                "geopotentialHeightM": height,
            }
            for pressure, temperature, _rh, _ws, height in record["levels"]
        ],
    }


def _kuchera_hour_dicts(
    hours: dict[datetime, dict[str, Any]],
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]] | None:
    """profiles.py-shaped hour dicts for every endpoint in [start, end].

    Returns None unless all 25 hourly endpoints are present, so the
    established interval logic in compute_baseline_slr never spans a
    data gap.
    """
    return _kuchera_hour_dicts_at(start, end, hours.get)


def _kuchera_hour_dicts_at(
    start: datetime,
    end: datetime,
    at: Any,
) -> list[dict[str, Any]] | None:
    """_kuchera_hour_dicts with records resolved through ``at(instant)``."""
    dicts: list[dict[str, Any]] = []
    for step in range(25):
        instant = start + timedelta(hours=step)
        record = at(instant)
        if record is None:
            return None
        dicts.append(_kuchera_dict(instant, record))
    return dicts


def _accumulate_agl(
    event: dict[str, Any],
    start: datetime,
    at: Any,
) -> tuple[dict[str, list[float]], int]:
    """Sum AGL values over the 24 window sample instants via ``at``."""
    elevation = event["elevation_m"]
    sums = {"t": [0.0] * len(AGL_TARGETS_M), "ws": [0.0] * len(AGL_TARGETS_M), "rh": [0.0] * len(AGL_TARGETS_M)}
    valid_hours = 0
    for step in range(1, 25):
        record = at(start + timedelta(hours=step))
        if record is None:
            continue
        agl = hour_agl_values(record["levels"], elevation)
        if agl is None:
            continue
        valid_hours += 1
        for var, values in zip(("t", "ws", "rh"), agl):
            for i, value in enumerate(values):
                sums[var][i] += value
    return sums, valid_hours


def _features_from_sums(
    sums: dict[str, list[float]],
    valid_hours: int,
) -> dict[str, float | None]:
    features: dict[str, float | None] = {}
    if valid_hours >= MIN_VALID_HOURS:
        for var in ("t", "ws", "rh"):
            for i, height in enumerate(AGL_TARGETS_M):
                features[f"{var}_{height}"] = round(sums[var][i] / valid_hours, 4)
    return features


def _event_row(
    event: dict[str, Any],
    features: dict[str, float | None],
    baseline: float | None,
    valid_hours: int,
    source_id: str,
) -> dict[str, Any]:
    if features and baseline is not None:
        status = "ok"
    elif baseline is not None:
        status = "incomplete_profile"
    elif features:
        status = "no_baseline"
    else:
        status = "incomplete_profile"
    row: dict[str, Any] = {
        "event_id": event["event_id"],
        "region": event["region"],
        "station_id": event["station_id"],
        "site_id": event["site_id"],
        "station_name": event["station_name"],
        "state": event["state"],
        "valid_time": event["valid_time"].isoformat(),
        "observation_date": event["observation_date"],
        "water_year": event["water_year"],
        "month": event["month"],
        "observed_slr": event["observed_slr"],
        "fresh_snow_mm": event["fresh_snow_mm"],
        "fresh_swe_mm": event["fresh_swe_mm"],
        "climate_regime": event["climate_regime"],
        "source_tier": event["source_tier"],
        "baseline_slr": baseline,
        "profile_status": status,
        "valid_hours": valid_hours,
        "profile_source": source_id,
    }
    for column in FEATURE_COLUMNS:
        if column == "latitude":
            row[column] = event["latitude"]
        elif column == "longitude":
            row[column] = event["longitude"]
        elif column == "elevation_m":
            row[column] = event["elevation_m"]
        else:
            row[column] = features.get(column)
    return row


def build_event_features(
    event: dict[str, Any],
    hours: dict[datetime, dict[str, Any]],
    source_id: str = PROFILE_SOURCE,
) -> dict[str, Any]:
    """Compute the 24-h AGL feature means and Kuchera baseline for one event."""
    start, end = event_window(event)
    sums, valid_hours = _accumulate_agl(event, start, hours.get)
    features = _features_from_sums(sums, valid_hours)
    baseline = None
    kuchera_dicts = _kuchera_hour_dicts(hours, start, end)
    if kuchera_dicts is not None:
        computed = compute_baseline_slr(kuchera_dicts, start, end, event["elevation_m"])
        if computed is not None:
            baseline = computed[0]
    return _event_row(event, features, baseline, valid_hours, source_id)


# ---------------------------------------------------------------------------
# Minute-offset events (era5-expanded-v2 recovery)
#
# Some GHCN observers report at sub-hourly times (e.g. 07:45), so the
# event's valid_time -- and therefore every instant of its 24-h window --
# falls between the whole-hour profile records and the whole-hour builder
# above finds zero valid hours.  The recovery below evaluates each window
# instant from the linear time-interpolation of the two bracketing
# whole-hour profiles (every profile variable, plus temperature_2m and
# the accumulation fields the Kuchera path consumes).  For an on-the-hour
# valid_time record_at returns the stored record untouched, so the
# offset builder reduces exactly to build_event_features; it is only
# ever applied to events the whole-hour builder marked
# incomplete_profile, and only their rows are replaced (additive merge
# in run_build_features_v2), which keeps every previously built row
# bit-identical.
# ---------------------------------------------------------------------------

def _lerp_value(lo: float | None, hi: float | None, frac: float) -> float | None:
    if lo is None or hi is None:
        return None
    return lo + (hi - lo) * frac


def _lerp_record(
    lo: dict[str, Any],
    hi: dict[str, Any],
    frac: float,
) -> dict[str, Any]:
    """Linear time-interpolation of one hour record; None propagates."""
    levels = []
    for (pressure, t_lo, rh_lo, ws_lo, z_lo), (_p, t_hi, rh_hi, ws_hi, z_hi) in zip(lo["levels"], hi["levels"]):
        levels.append((
            pressure,
            _lerp_value(t_lo, t_hi, frac),
            _lerp_value(rh_lo, rh_hi, frac),
            _lerp_value(ws_lo, ws_hi, frac),
            _lerp_value(z_lo, z_hi, frac),
        ))
    return {
        "temperature2mC": _lerp_value(lo["temperature2mC"], hi["temperature2mC"], frac),
        "precipitationMm": _lerp_value(lo["precipitationMm"], hi["precipitationMm"], frac),
        "snowfallCm": _lerp_value(lo["snowfallCm"], hi["snowfallCm"], frac),
        "levels": levels,
    }


def record_at(
    hours: dict[datetime, dict[str, Any]],
    instant: datetime,
) -> dict[str, Any] | None:
    """Hour record at an arbitrary instant.

    The stored record itself when ``instant`` is on a whole hour;
    otherwise the linear time-interpolation of the two bracketing
    whole-hour records.  None when a needed record is missing.
    """
    floor = instant.replace(minute=0, second=0, microsecond=0)
    if floor == instant:
        return hours.get(instant)
    lo = hours.get(floor)
    hi = hours.get(floor + timedelta(hours=1))
    if lo is None or hi is None:
        return None
    frac = (instant - floor).total_seconds() / 3600.0
    return _lerp_record(lo, hi, frac)


def build_event_features_offset(
    event: dict[str, Any],
    hours: dict[datetime, dict[str, Any]],
    source_id: str = PROFILE_SOURCE,
) -> dict[str, Any]:
    """build_event_features for sub-hourly-offset valid_times.

    Identical aggregation to build_event_features, except every window
    instant's profile is resolved through record_at (linear time
    interpolation between the bracketing whole-hour profiles).
    """
    start, end = event_window(event)
    at = lambda instant: record_at(hours, instant)  # noqa: E731
    sums, valid_hours = _accumulate_agl(event, start, at)
    features = _features_from_sums(sums, valid_hours)
    baseline = None
    kuchera_dicts = _kuchera_hour_dicts_at(start, end, at)
    if kuchera_dicts is not None:
        computed = compute_baseline_slr(kuchera_dicts, start, end, event["elevation_m"])
        if computed is not None:
            baseline = computed[0]
    return _event_row(event, features, baseline, valid_hours, source_id)


def _blank_row(event: dict[str, Any], status: str, source_id: str = PROFILE_SOURCE) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event_id": event["event_id"], "region": event["region"],
        "station_id": event["station_id"], "site_id": event["site_id"],
        "station_name": event["station_name"], "state": event["state"],
        "valid_time": event["valid_time"].isoformat(),
        "observation_date": event["observation_date"],
        "water_year": event["water_year"], "month": event["month"],
        "observed_slr": event["observed_slr"],
        "fresh_snow_mm": event["fresh_snow_mm"],
        "fresh_swe_mm": event["fresh_swe_mm"],
        "climate_regime": event["climate_regime"],
        "source_tier": event["source_tier"],
        "baseline_slr": None, "profile_status": status,
        "valid_hours": 0, "profile_source": source_id,
    }
    for column in FEATURE_COLUMNS:
        if column == "latitude":
            row[column] = event["latitude"]
        elif column == "longitude":
            row[column] = event["longitude"]
        elif column == "elevation_m":
            row[column] = event["elevation_m"]
        else:
            row[column] = None
    return row


def build_features(
    events: list[dict[str, Any]],
    cache_dir: Path,
    chunks: list[dict[str, Any]],
    spec: SourceSpec = BEST_MATCH_SPEC,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build per-event features for every event, with status accounting."""
    chunk_by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        chunk_by_station[chunk["station_id"]].append(chunk)
    events_by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        events_by_station[event["station_id"]].append(event)

    rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    region_status: dict[str, Counter[str]] = defaultdict(Counter)

    def record(row: dict[str, Any]) -> None:
        rows.append(row)
        status_counts[row["profile_status"]] += 1
        region_status[row["region"]][row["profile_status"]] += 1

    for station_id, station_events in sorted(events_by_station.items()):
        hours: dict[datetime, dict[str, Any]] = {}
        for chunk in chunk_by_station.get(station_id, []):
            path = _chunk_path(cache_dir, chunk)
            if not _cache_valid(path):
                continue
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                hours.update(_payload_hours(json.load(handle)))
        for event in station_events:
            if event["elevation_m"] is None:
                record(_blank_row(event, "missing_station_metadata", spec.source_id))
            elif not spec.covers(date.fromisoformat(event["observation_date"])):
                row = {
                    "event_id": event["event_id"], "region": event["region"],
                    "station_id": event["station_id"], "site_id": event["site_id"],
                    "station_name": event["station_name"], "state": event["state"],
                    "valid_time": event["valid_time"].isoformat(),
                    "observation_date": event["observation_date"],
                    "water_year": event["water_year"], "month": event["month"],
                    "observed_slr": event["observed_slr"],
                    "fresh_snow_mm": event["fresh_snow_mm"],
                    "fresh_swe_mm": event["fresh_swe_mm"],
                    "climate_regime": event["climate_regime"],
                    "source_tier": event["source_tier"],
                    "baseline_slr": None, "profile_status": "outside_archive_coverage",
                    "valid_hours": 0, "profile_source": spec.source_id,
                }
                for column in FEATURE_COLUMNS:
                    row[column] = event["latitude"] if column == "latitude" else (
                        event["longitude"] if column == "longitude" else (
                            event["elevation_m"] if column == "elevation_m" else None))
                record(row)
            elif not hours:
                row = build_event_features(event, {}, spec.source_id)
                row["profile_status"] = "fetch_missing"
                record(row)
            else:
                record(build_event_features(event, hours, spec.source_id))
    coverage = {
        "profileSource": spec.source_id,
        "eventsTotal": len(events),
        "statusCounts": dict(status_counts),
        "byRegion": {region: dict(counts) for region, counts in sorted(region_status.items())},
    }
    return rows, coverage


def write_features_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    columns: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        for key, value in row.items():
            columns[key].append(value)
    table = pa.table({key: pa.array(values) for key, values in columns.items()})
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def run_fetch_profiles(pool_dir: Path, output_dir: Path, max_workers: int, min_interval_s: float) -> dict[str, Any]:
    events = load_screened_events(pool_dir)
    cache_dir = output_dir / "raw_cache"
    summary = fetch_profiles(events, cache_dir, max_workers=max_workers, min_interval_s=min_interval_s)
    summary["eventsLoaded"] = len(events)
    return summary


def run_build_features(
    pool_dir: Path,
    output_dir: Path,
    spec: SourceSpec = BEST_MATCH_SPEC,
) -> dict[str, Any]:
    events = load_screened_events(pool_dir)
    cache_dir = output_dir / spec.cache_dir_name
    chunks = plan_chunks(events, spec=spec)
    rows, coverage = build_features(events, cache_dir, chunks, spec=spec)
    write_features_parquet(rows, output_dir / "features.parquet")
    (output_dir / "feature_coverage.json").write_text(json.dumps(coverage, indent=2), encoding="utf-8")
    return coverage


def _has_subhour_offset(valid_time_text: str) -> bool:
    parsed = datetime.fromisoformat(str(valid_time_text))
    return parsed.minute != 0 or parsed.second != 0 or parsed.microsecond != 0


def _merge_recovered_rows(
    source_rows: list[dict[str, Any]],
    recovered: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Source rows in original order, with recovered rows swapped in."""
    return [recovered.get(str(row["event_id"]), row) for row in source_rows]


def run_build_features_v2(
    pool_dir: Path,
    source_dir: Path,
    output_dir: Path,
    spec: SourceSpec = ERA5_SPEC,
    extended_id_file: Path | None = None,
) -> dict[str, Any]:
    """Additive v2 build: recover minute-offset events from a prior build.

    Reads the source build's features.parquet, finds the events it
    marked ``incomplete_profile`` whose valid_time is offset from the
    whole hour, and rebuilds only those rows with
    build_event_features_offset from the same profile cache.  A row is
    replaced only when the offset build reaches status ``ok``; every
    other row is carried over untouched, so the v2 parquet differs from
    the source exactly in the recovered rows.  Writes features.parquet,
    feature_coverage.json and recovery_report.json into output_dir
    (created if needed).  The profile cache is resolved as
    ``<output_dir>/<spec.cache_dir_name>``; when that path does not
    exist it is symlinked to the source build's cache, mirroring the
    era5-expanded layout.
    """
    import pyarrow.parquet as pq

    events = load_screened_events(pool_dir)
    events_by_id = {event["event_id"]: event for event in events}
    source_rows = pq.read_table(source_dir / "features.parquet").to_pylist()
    targets = [
        row for row in source_rows
        if row["profile_status"] == "incomplete_profile" and _has_subhour_offset(str(row["valid_time"]))
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir / spec.cache_dir_name
    if not cache_dir.exists():
        real_cache = (source_dir / spec.cache_dir_name).resolve()
        if real_cache.is_dir():
            cache_dir.symlink_to(real_cache)

    chunks = plan_chunks(events, spec=spec)
    chunk_by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        chunk_by_station[chunk["station_id"]].append(chunk)

    station_hours: dict[str, dict[datetime, dict[str, Any]]] = {}

    def hours_for(station_id: str) -> dict[datetime, dict[str, Any]]:
        if station_id not in station_hours:
            hours: dict[datetime, dict[str, Any]] = {}
            for chunk in chunk_by_station.get(station_id, []):
                path = _chunk_path(cache_dir, chunk)
                if not _cache_valid(path):
                    continue
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    hours.update(_payload_hours(json.load(handle)))
            station_hours[station_id] = hours
        return station_hours[station_id]

    recovered: dict[str, dict[str, Any]] = {}
    still_failing: dict[str, dict[str, Any]] = {}
    for row in targets:
        event = events_by_id[str(row["event_id"])]
        new_row = build_event_features_offset(event, hours_for(event["station_id"]), spec.source_id)
        if new_row["profile_status"] == "ok":
            recovered[event["event_id"]] = new_row
        else:
            still_failing[event["event_id"]] = {
                "region": event["region"],
                "waterYear": event["water_year"],
                "wouldBeStatus": new_row["profile_status"],
                "validHours": new_row["valid_hours"],
                "baselinePresent": new_row["baseline_slr"] is not None,
            }

    merged_rows = _merge_recovered_rows(source_rows, recovered)
    status_counts: Counter[str] = Counter()
    region_status: dict[str, Counter[str]] = defaultdict(Counter)
    for row in merged_rows:
        status_counts[row["profile_status"]] += 1
        region_status[row["region"]][row["profile_status"]] += 1
    coverage = {
        "profileSource": spec.source_id,
        "eventsTotal": len(merged_rows),
        "statusCounts": dict(status_counts),
        "byRegion": {region: dict(counts) for region, counts in sorted(region_status.items())},
    }

    recovered_rows = list(recovered.values())
    recovery: dict[str, Any] = {
        "profileSource": spec.source_id,
        "sourceDir": str(source_dir),
        "targetEvents": len(targets),
        "recoveredEvents": len(recovered_rows),
        "stillFailingEvents": len(still_failing),
        "recoveredByRegion": dict(Counter(row["region"] for row in recovered_rows)),
        "recoveredByWaterYear": {
            str(year): count
            for year, count in sorted(Counter(row["water_year"] for row in recovered_rows).items())
        },
        "stillFailing": dict(sorted(still_failing.items())),
        "modelingRowsBefore": sum(1 for row in source_rows if row["profile_status"] == "ok"),
        "modelingRowsAfter": int(status_counts.get("ok", 0)),
    }
    if extended_id_file is not None and extended_id_file.exists():
        extended_ids = {
            line.strip()
            for line in extended_id_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        recovery["targetsInExtendedPool"] = sum(
            1 for row in targets if str(row["event_id"]) in extended_ids
        )
        recovery["recoveredInExtendedPool"] = sum(
            1 for event_id in recovered if event_id in extended_ids
        )
    write_features_parquet(merged_rows, output_dir / "features.parquet")
    (output_dir / "feature_coverage.json").write_text(json.dumps(coverage, indent=2), encoding="utf-8")
    (output_dir / "recovery_report.json").write_text(json.dumps(recovery, indent=2), encoding="utf-8")
    return recovery
