"""Step 5 (operational transfer test): frozen step-2 gold models on HRRR profiles.

PROVENANCE -- read first.  The step-2 models (RF, GBM, RF+EB) were
trained on profile features from the Open-Meteo historical-forecast
**best-match** archive (see ``gold_profiles``; that archive is a model
blend, NOT ERA5).  This module asks the operational question: do the
FROZEN step-2 models still work when the identical 27-feature AGL spec
is built from **NOAA HRRR** pressure-level GRIB (bucket
``noaa-hrrr-bdp-pds``, product ``wrfprs``)?  No model is retrained
here; RF and GBM load from the step-2 joblib artifacts and RF+EB is
reconstructed by fitting ``gold_model.RFEBModel`` once on the full
best-match modeling frame (the same deterministic construction the
step-2 benchmark uses for its final artifacts; the run verifies its
inner RF reproduces ``rf_full.joblib`` predictions exactly).

Cycle / valid-time convention (uniform, stated exactly): for every
hourly instant ``t`` of an event's 24-h observation window (the same
(start, end] window ``gold_profiles.event_window`` defines, endpoints
included for the Kuchera baseline), the profile is the HRRR forecast
from the latest cycle initialized at or before ``t - 1 h``, at the
forecast hour valid at ``t``:

* CONUS (hourly cycles): cycle ``t - 1 h``, forecast hour f01 --
  a uniform 1-h lead.
* Alaska (3-hourly cycles in the archive): latest 3-hourly cycle at or
  before ``t - 1 h``, forecast hour f01--f03 (lead 1--3 h).

All events fall in the HRRR v4 era (archive coverage from 2020-12;
the modeling pool starts 2021-06).  Variables per file: TMP / RH /
UGRD / VGRD / HGT on the same 13 isobaric levels the best-match build
uses (1000..500 hPa), plus TMP 2 m, and the run-accumulated APCP /
ASNOW for the Kuchera baseline's hourly precipitation / snowfall
(the accumulation descriptor is verified to span (0, fxx) for every
file; hourly increments at fxx > 1 are differences within the run,
clamped at zero, exactly like ``noaa_hrrr.hourly_increments``).
Hourly records are fed to ``gold_profiles.build_event_features``, so
AGL interpolation, the >= 18 valid-hour rule and the Kuchera baseline
are byte-for-byte the step-2 construction -- only the profile source
changes.  Extraction reuses ``noaa_hrrr``'s .idx byte-range planning
and ecCodes decoding; only the wanted messages cross the network.

Outputs land under ``model-v1/transfer-hrrr/`` (gitignored data tree):
a per-cycle station-point cache (``raw_cache_hrrr/``),
``features_hrrr.parquet``, ``feature_coverage_hrrr.json``,
``fetch_summary_hrrr.json``, ``transfer_results.json`` and
``TRANSFER_REPORT.md``.  Research only: nothing here writes app
artifacts or touches the production calibration gates.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .gold_model import RFEBModel, _metrics, evaluate_predictions, load_modeling_frame
from .gold_profiles import PRESSURE_LEVELS_HPA, build_event_features, event_window, load_screened_events
from .noaa_hrrr import (
    NoaaFetchError,
    RangeFetcher,
    decode_message_values,
    message_grid,
    nearest_grid_indices,
    parse_accumulation_span,
    parse_idx,
    plan_ranges,
)

TRANSFER_SOURCE_ID = "noaa-hrrr-wrfprs-transfer"
S3_BASE = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
DOMAINS = ("conus", "alaska")
KELVIN_OFFSET = 273.15

PROFILE_VARIABLES: tuple[tuple[str, str, str], ...] = tuple(
    (f"t_{level}", "TMP", f"{level} mb") for level in PRESSURE_LEVELS_HPA
) + tuple(
    (f"rh_{level}", "RH", f"{level} mb") for level in PRESSURE_LEVELS_HPA
) + tuple(
    (f"u_{level}", "UGRD", f"{level} mb") for level in PRESSURE_LEVELS_HPA
) + tuple(
    (f"v_{level}", "VGRD", f"{level} mb") for level in PRESSURE_LEVELS_HPA
) + tuple(
    (f"hgt_{level}", "HGT", f"{level} mb") for level in PRESSURE_LEVELS_HPA
)
TRANSFER_SURFACE_VARIABLES: tuple[tuple[str, str, str], ...] = (
    ("t2m_k", "TMP", "2 m above ground"),
    ("apcp_mm", "APCP", "surface"),
    ("asnow_m", "ASNOW", "surface"),
)
WANTED_VARIABLES = PROFILE_VARIABLES + TRANSFER_SURFACE_VARIABLES
ACCUMULATION_KEYS = ("apcp_mm", "asnow_m")
ACC_VARIABLES = tuple(v for v in TRANSFER_SURFACE_VARIABLES if v[0] in ACCUMULATION_KEYS)

_SAMPLE_CYCLES = {
    "conus": datetime(2024, 1, 15, 12, tzinfo=timezone.utc),
    "alaska": datetime(2024, 1, 15, 0, tzinfo=timezone.utc),
}


# ---------------------------------------------------------------------------
# Domains, cycles, URLs (pure)
# ---------------------------------------------------------------------------

def station_domain(event: dict[str, Any]) -> str:
    """HRRR domain for an event: the Alaska grid serves the alaska region."""
    return "alaska" if event["region"] == "alaska" else "conus"


def cycle_step_hours(domain: str) -> int:
    """Cycle cadence in the archive: hourly CONUS, 3-hourly Alaska."""
    return 3 if domain == "alaska" else 1


def cycle_for_instant(instant: datetime, domain: str) -> tuple[datetime, int]:
    """(cycle init, forecast hour) valid at ``instant`` under the convention.

    The cycle is the latest one initialized at or before ``instant - 1 h``;
    the forecast hour is the whole hours from cycle init to ``instant``.
    """
    base = instant - timedelta(hours=1)
    step = cycle_step_hours(domain)
    cycle = base.replace(minute=0, second=0, microsecond=0)
    cycle -= timedelta(hours=cycle.hour % step)
    fxx = int((instant - cycle).total_seconds() // 3600)
    return cycle, fxx


def file_url(domain: str, cycle: datetime, fxx: int) -> str:
    stamp = cycle.strftime("%Y%m%d")
    suffix = ".ak" if domain == "alaska" else ""
    return f"{S3_BASE}/hrrr.{stamp}/{domain}/hrrr.t{cycle:%H}z.wrfprsf{fxx:02d}{suffix}.grib2"


def plan_transfer_files(
    events: list[dict[str, Any]],
) -> tuple[dict[tuple[str, datetime, int], set[str]], dict[tuple[str, datetime, int], set[str]]]:
    """Plan the GRIB files the transfer build needs.

    Returns (full, acc_prev): ``full`` maps (domain, cycle, fxx) -> the
    station ids sampled from that file; ``acc_prev`` maps the (cycle,
    fxx-1) files needed only for their APCP/ASNOW accumulations (hourly
    differencing at fxx > 1).  An acc_prev key that is also a full key
    needs no separate reduced extraction.
    """
    full: dict[tuple[str, datetime, int], set[str]] = defaultdict(set)
    acc_prev: dict[tuple[str, datetime, int], set[str]] = defaultdict(set)
    for event in events:
        domain = station_domain(event)
        start, end = event_window(event)
        for step in range(25):
            instant = start + timedelta(hours=step)
            cycle, fxx = cycle_for_instant(instant, domain)
            full[(domain, cycle, fxx)].add(event["station_id"])
            if fxx > 1:
                acc_prev[(domain, cycle, fxx - 1)].add(event["station_id"])
    return dict(full), dict(acc_prev)


def hourly_accumulation(current: float | None, previous: float | None, fxx: int) -> float | None:
    """Hourly increment of a run-accumulated field ending at forecast hour fxx.

    At fxx == 1 the (verified 0-1 h) accumulation IS the hourly value.
    Beyond that, difference within the run; a missing endpoint poisons
    the increment and negatives clamp to zero (mirrors
    ``noaa_hrrr.hourly_increments``).
    """
    if current is None:
        return None
    if fxx <= 1:
        return current
    if previous is None:
        return None
    return max(0.0, current - previous)


# ---------------------------------------------------------------------------
# Pooled HTTP fetcher (the noaa_hrrr urllib fetcher opens a connection
# per request; at ~30 range requests per file, TLS setup dominated the
# per-file cost in benchmarking, so the transfer fetch pools one
# keep-alive connection per thread against the single S3 host)
# ---------------------------------------------------------------------------

class PooledRangeFetcher:
    """RangeFetcher over per-thread keep-alive HTTPS connections."""

    def __init__(self, max_retries: int = 4, timeout: int = 120) -> None:
        import http.client

        self._http_client = http.client
        self._max_retries = max_retries
        self._timeout = timeout
        self._local = threading.local()
        self._lock = threading.Lock()
        self.requests = 0
        self.bytes_transferred = 0
        self.retries = 0

    def _connection(self, host: str):
        conn = getattr(self._local, "conn", None)
        if conn is None or getattr(self._local, "host", None) != host:
            conn = self._http_client.HTTPSConnection(host, timeout=self._timeout)
            self._local.conn = conn
            self._local.host = host
        return conn

    def _drop_connection(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _request(self, url: str, headers: dict[str, str], method: str = "GET") -> tuple[bytes, dict[str, str]]:
        import time
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            with self._lock:
                self.requests += 1
            try:
                conn = self._connection(parsed.netloc)
                conn.request(method, path, headers={"User-Agent": "mysnow-hrrr-transfer/0.1", **headers})
                response = conn.getresponse()
                # read() must be called even for HEAD (it returns b"")
                # or http.client leaves the connection in Request-sent
                # state and the next request raises ResponseNotReady.
                body = response.read()
                if response.status == 404:
                    raise NoaaFetchError(f"HTTP 404 for {url}")
                if response.status not in (200, 206):
                    raise NoaaFetchError(f"HTTP {response.status} for {url}")
                response_headers = {key: value for key, value in response.getheaders()}
                with self._lock:
                    self.bytes_transferred += len(body)
                return body, response_headers
            except NoaaFetchError:
                raise
            except Exception as exc:  # connection went stale, reset, timeout...
                last_error = exc
                self._drop_connection()
                if attempt >= self._max_retries:
                    break
                with self._lock:
                    self.retries += 1
                time.sleep(min(60.0, 2.0 * (2 ** attempt)))
        raise NoaaFetchError(f"persistent failure for {url}: {last_error}")

    def get(self, url: str) -> bytes:
        body, _headers = self._request(url, {})
        return body

    def head_size(self, url: str) -> int:
        _body, headers = self._request(url, {}, method="HEAD")
        return int(headers["Content-Length"])

    def get_range(self, url: str, start: int, end: int) -> bytes:
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                body, _headers = self._request(url, {"Range": f"bytes={start}-{end}"})
            except NoaaFetchError as exc:
                last_error = exc
                continue
            if len(body) == end - start + 1:
                return body
            last_error = NoaaFetchError(f"range {start}-{end} for {url} returned {len(body)} bytes")
            self._drop_connection()
        assert last_error is not None
        raise last_error

    def stats(self) -> dict[str, int]:
        return {"requests": self.requests, "bytes_transferred": self.bytes_transferred, "retries": self.retries}


# ---------------------------------------------------------------------------
# Grid indices (per domain, cached beside the point cache)
# ---------------------------------------------------------------------------

def _stations_payload(stations: list[dict[str, Any]]) -> str:
    return json.dumps(
        [[str(s["station_id"]), round(float(s["latitude"]), 5), round(float(s["longitude"]), 5)]
         for s in sorted(stations, key=lambda s: str(s["station_id"]))],
        separators=(",", ":"),
    )


def build_domain_grid_indices(
    fetcher: RangeFetcher,
    domain: str,
    stations: list[dict[str, Any]],
) -> dict[str, int]:
    """Nearest-gridpoint flat index per station from one sample file."""
    cycle = _SAMPLE_CYCLES[domain]
    url = file_url(domain, cycle, 1)
    entries = parse_idx(fetcher.get(url + ".idx").decode("utf-8"))
    file_size = fetcher.head_size(url)
    ranges = plan_ranges(entries, [("hgt_500", "HGT", "500 mb")], file_size)
    planned = ranges[0]
    body = fetcher.get_range(url, planned["start"], planned["end"])
    latitudes, longitudes, _ni, _nj = message_grid(body)
    pseudo = [
        {"station_triplet": s["station_id"], "latitude": s["latitude"], "longitude": s["longitude"]}
        for s in stations
    ]
    return nearest_grid_indices(latitudes, longitudes, pseudo)


def load_or_build_domain_grid_indices(
    fetcher: RangeFetcher | None,
    cache_dir: Path,
    domain: str,
    stations: list[dict[str, Any]],
) -> dict[str, int]:
    cache_path = cache_dir / f"grid_indices_{domain}.json"
    key = hashlib.sha256(_stations_payload(stations).encode("utf-8")).hexdigest()
    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if (
            isinstance(payload, dict)
            and payload.get("kind") == "hrrr_transfer_grid_indices"
            and payload.get("stations_sha256") == key
            and isinstance(payload.get("indices"), dict)
        ):
            return {str(k): int(v) for k, v in payload["indices"].items()}
    if fetcher is None:
        raise RuntimeError(f"grid indices for {domain} are not cached and no fetcher was given")
    indices = build_domain_grid_indices(fetcher, domain, stations)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(json.dumps(
        {"kind": "hrrr_transfer_grid_indices", "domain": domain, "stations_sha256": key, "indices": indices},
        separators=(",", ":"),
    ), encoding="utf-8")
    os.replace(tmp, cache_path)
    return indices


# ---------------------------------------------------------------------------
# Per-file point extraction with cache
# ---------------------------------------------------------------------------

def _points_cache_path(cache_dir: Path, domain: str, cycle: datetime, fxx: int, reduced: bool) -> Path:
    stamp = cycle.strftime("%Y%m%d")
    name = f"t{cycle:%H}z_f{fxx:02d}" + ("_acc" if reduced else "") + ".json.gz"
    return cache_dir / domain / stamp / name


def _read_points_cache(path: Path) -> dict[str, dict[str, float | None]] | None:
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("kind") != "hrrr_transfer_points":
        return None
    return payload["points"]


def _write_points_cache(
    path: Path, domain: str, cycle: datetime, fxx: int, reduced: bool, url: str,
    points: dict[str, dict[str, float | None]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": "hrrr_transfer_points",
        "domain": domain,
        "cycle": cycle.astimezone(timezone.utc).isoformat(),
        "fxx": fxx,
        "reduced": reduced,
        "url": url,
        "points": points,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
    os.replace(tmp, path)


def extract_cycle_points(
    fetcher: RangeFetcher | None,
    cache_dir: Path,
    domain: str,
    cycle: datetime,
    fxx: int,
    grid_indices: dict[str, int],
    reduced: bool = False,
) -> tuple[dict[str, dict[str, float | None]] | None, str]:
    """Station point values for one (domain, cycle, fxx) wrfprs file.

    Returns (points, status) with status one of ``cached``,
    ``downloaded``, ``missing`` (cache-only, not cached) or ``failed``
    (network/parse error, or an accumulation descriptor that does not
    span (0, fxx) -- a silent span mismatch would corrupt the Kuchera
    baseline, so the file is refused rather than sampled).
    With ``reduced=True`` only the APCP/ASNOW accumulation messages are
    fetched (for fxx-1 differencing support).
    """
    path = _points_cache_path(cache_dir, domain, cycle, fxx, reduced)
    cached = _read_points_cache(path)
    if cached is not None:
        return cached, "cached"
    if fetcher is None:
        return None, "missing"
    url = file_url(domain, cycle, fxx)
    wanted = ACC_VARIABLES if reduced else WANTED_VARIABLES
    try:
        entries = parse_idx(fetcher.get(url + ".idx").decode("utf-8"))
        for key, var, level in wanted:
            if key in ACCUMULATION_KEYS:
                entry = next((e for e in entries if e["var"] == var and e["level"] == level), None)
                if entry is None or parse_accumulation_span(entry["forecast"]) != (0, fxx):
                    raise ValueError(f"{var} accumulation span is not (0, {fxx}) in {url}")
        file_size = fetcher.head_size(url)
        ranges = plan_ranges(entries, wanted, file_size)
        # Fetch the planned ranges concurrently (they are independent
        # GETs; sequential fetching measured ~25 s per file), keeping
        # only the raw message bytes.
        messages: dict[str, bytes] = {}

        def fetch_range(planned: dict[str, Any]) -> None:
            body = fetcher.get_range(url, planned["start"], planned["end"])
            cursor = 0
            for span_start, span_end, key in planned["spans"]:
                length = span_end - span_start + 1
                messages[key] = body[cursor:cursor + length]
                cursor += length

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(fetch_range, planned) for planned in ranges]
            for future in as_completed(futures):
                future.result()
    except Exception:
        return None, "failed"
    # Decode one message at a time and sample the station points
    # immediately, so at most one full grid is ever in memory per file.
    points: dict[str, dict[str, float | None]] = {station_id: {} for station_id in grid_indices}
    for key, _var, _level in wanted:
        message = messages.get(key)
        if message is None:
            for row in points.values():
                row[key] = None
            continue
        array = decode_message_values(message)
        for station_id, index in grid_indices.items():
            value = float(array[index])
            points[station_id][key] = None if value != value else value
    _write_points_cache(path, domain, cycle, fxx, reduced, url, points)
    return points, "downloaded"


# ---------------------------------------------------------------------------
# Fetch driver
# ---------------------------------------------------------------------------

def domain_stations(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """One {station_id, latitude, longitude} record per station, by domain."""
    by_domain: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for event in events:
        by_domain[station_domain(event)].setdefault(event["station_id"], {
            "station_id": event["station_id"],
            "latitude": event["latitude"],
            "longitude": event["longitude"],
        })
    return {domain: sorted(rows.values(), key=lambda s: s["station_id"]) for domain, rows in by_domain.items()}


def fetch_transfer_files(
    events: list[dict[str, Any]],
    cache_dir: Path,
    fetcher: RangeFetcher,
    max_workers: int = 10,
    progress_every: int = 250,
) -> dict[str, Any]:
    """Download every planned HRRR file's wanted messages (resumable)."""
    full, acc_prev = plan_transfer_files(events)
    stations = domain_stations(events)
    grids = {
        domain: load_or_build_domain_grid_indices(fetcher, cache_dir, domain, rows)
        for domain, rows in stations.items()
    }
    jobs: list[tuple[str, datetime, int, bool]] = [
        (domain, cycle, fxx, False) for (domain, cycle, fxx) in sorted(full)
    ]
    jobs += [
        (domain, cycle, fxx, True) for (domain, cycle, fxx) in sorted(acc_prev)
        if (domain, cycle, fxx) not in full
    ]
    counts: Counter[str] = Counter()
    failed: list[str] = []
    lock = threading.Lock()
    done = 0

    def work(job: tuple[str, datetime, int, bool]) -> None:
        nonlocal done
        domain, cycle, fxx, reduced = job
        _points, status = extract_cycle_points(
            fetcher, cache_dir, domain, cycle, fxx, grids[domain], reduced=reduced,
        )
        with lock:
            counts[status] += 1
            done += 1
            if status == "failed":
                failed.append(file_url(domain, cycle, fxx) + (" (acc)" if reduced else ""))
            if done % progress_every == 0 or done == len(jobs):
                print(f"[hrrr-transfer] {done}/{len(jobs)} files "
                      f"(downloaded={counts['downloaded']}, cached={counts['cached']}, failed={counts['failed']})",
                      flush=True)

    if jobs:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(work, job) for job in jobs]
            for future in as_completed(futures):
                future.result()
    stats = fetcher.stats() if hasattr(fetcher, "stats") else {}
    return {
        "profileSource": TRANSFER_SOURCE_ID,
        "eventsPlanned": len(events),
        "filesPlanned": len(jobs),
        "filesFull": len(full),
        "filesAccPrev": len(jobs) - len(full),
        "downloaded": counts["downloaded"],
        "cached": counts["cached"],
        "failed": counts["failed"],
        "failedUrls": failed[:100],
        "requests": stats.get("requests"),
        "bytesTransferred": stats.get("bytes_transferred"),
        "retries": stats.get("retries"),
    }


# ---------------------------------------------------------------------------
# Hour assembly + feature building (cache-only; mirrors gold_profiles)
# ---------------------------------------------------------------------------

def station_instant_record(
    points_row: dict[str, float | None] | None,
    prev_row: dict[str, float | None] | None,
    fxx: int,
) -> dict[str, Any] | None:
    """One gold_profiles-shaped hour record from a station's point values."""
    if points_row is None:
        return None
    levels = []
    for level in PRESSURE_LEVELS_HPA:
        temperature = points_row.get(f"t_{level}")
        u = points_row.get(f"u_{level}")
        v = points_row.get(f"v_{level}")
        wind = None if u is None or v is None else math.hypot(u, v)
        levels.append((
            float(level),
            None if temperature is None else temperature - KELVIN_OFFSET,
            points_row.get(f"rh_{level}"),
            wind,
            points_row.get(f"hgt_{level}"),
        ))
    t2m = points_row.get("t2m_k")
    prev_apcp = None if prev_row is None else prev_row.get("apcp_mm")
    prev_asnow = None if prev_row is None else prev_row.get("asnow_m")
    snowfall = hourly_accumulation(points_row.get("asnow_m"), prev_asnow, fxx)
    return {
        "temperature2mC": None if t2m is None else t2m - KELVIN_OFFSET,
        "precipitationMm": hourly_accumulation(points_row.get("apcp_mm"), prev_apcp, fxx),
        "snowfallCm": None if snowfall is None else snowfall * 100.0,
        "levels": levels,
    }


def assemble_event_hours(
    event: dict[str, Any],
    load_points: Any,
) -> dict[datetime, dict[str, Any]]:
    """Hour records for the event's 25 window endpoints from cached points.

    ``load_points(domain, cycle, fxx, reduced)`` returns the points dict
    for one file (or None).  For fxx > 1 the previous forecast hour's
    accumulations come from the full cache when present, else the
    reduced acc cache.
    """
    domain = station_domain(event)
    station_id = event["station_id"]
    start, end = event_window(event)
    hours: dict[datetime, dict[str, Any]] = {}
    for step in range(25):
        instant = start + timedelta(hours=step)
        cycle, fxx = cycle_for_instant(instant, domain)
        points = load_points(domain, cycle, fxx, False)
        row = None if points is None else points.get(station_id)
        prev_row = None
        if fxx > 1 and row is not None:
            prev = load_points(domain, cycle, fxx - 1, False)
            if prev is None:
                prev = load_points(domain, cycle, fxx - 1, True)
            prev_row = None if prev is None else prev.get(station_id)
        record = station_instant_record(row, prev_row, fxx)
        if record is not None:
            hours[instant] = record
    return hours


def build_transfer_features(
    events: list[dict[str, Any]],
    cache_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per-event HRRR features for every event, with status accounting."""
    cache_memo: dict[tuple[str, datetime, int, bool], dict[str, Any] | None] = {}

    def load_points(domain: str, cycle: datetime, fxx: int, reduced: bool):
        key = (domain, cycle, fxx, reduced)
        if key not in cache_memo:
            cache_memo[key] = _read_points_cache(_points_cache_path(cache_dir, domain, cycle, fxx, reduced))
        return cache_memo[key]

    rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    region_status: dict[str, Counter[str]] = defaultdict(Counter)
    wy_status: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        hours = assemble_event_hours(event, load_points)
        row = build_event_features(event, hours, TRANSFER_SOURCE_ID)
        if not hours:
            row["profile_status"] = "fetch_missing"
        rows.append(row)
        status_counts[row["profile_status"]] += 1
        region_status[event["region"]][row["profile_status"]] += 1
        wy_status[str(event["water_year"])][row["profile_status"]] += 1
    coverage = {
        "profileSource": TRANSFER_SOURCE_ID,
        "eventsTotal": len(events),
        "statusCounts": dict(status_counts),
        "byRegion": {region: dict(counts) for region, counts in sorted(region_status.items())},
        "byWaterYear": {wy: dict(counts) for wy, counts in sorted(wy_status.items())},
    }
    return rows, coverage


# ---------------------------------------------------------------------------
# Drivers shared by the CLI
# ---------------------------------------------------------------------------

def modeling_events(pool_dir: Path, output_dir: Path, event_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Screened events that are step-2 modeling rows (frozen-model inputs)."""
    frame, _accounting = load_modeling_frame(output_dir / "features.parquet")
    wanted = {str(event_id) for event_id in frame["event_id"]}
    if event_ids is not None:
        wanted &= event_ids
    return [event for event in load_screened_events(pool_dir) if event["event_id"] in wanted]


def transfer_dir(output_dir: Path) -> Path:
    return output_dir / "transfer-hrrr"


def run_fetch_hrrr_transfer(
    pool_dir: Path,
    output_dir: Path,
    max_workers: int = 10,
    event_ids: set[str] | None = None,
) -> dict[str, Any]:
    events = modeling_events(pool_dir, output_dir, event_ids)
    out = transfer_dir(output_dir)
    cache_dir = out / "raw_cache_hrrr"
    cache_dir.mkdir(parents=True, exist_ok=True)
    fetcher = PooledRangeFetcher()
    summary = fetch_transfer_files(events, cache_dir, fetcher, max_workers=max_workers)
    summary["eventsLoaded"] = len(events)
    (out / "fetch_summary_hrrr.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def run_build_hrrr_features(pool_dir: Path, output_dir: Path) -> dict[str, Any]:
    from .gold_profiles import write_features_parquet

    events = modeling_events(pool_dir, output_dir)
    out = transfer_dir(output_dir)
    rows, coverage = build_transfer_features(events, out / "raw_cache_hrrr")
    write_features_parquet(rows, out / "features_hrrr.parquet")
    (out / "feature_coverage_hrrr.json").write_text(json.dumps(coverage, indent=2), encoding="utf-8")
    return coverage


# ---------------------------------------------------------------------------
# Frozen-model transfer evaluation
# ---------------------------------------------------------------------------

TRANSFER_MODELS = ("rf", "gbm", "rf_eb", "kuchera")


def _predict_frozen(
    model_name: str,
    frame: Any,
    frozen: dict[str, Any],
) -> Any:
    import numpy as np

    from .gold_profiles import FEATURE_COLUMNS

    if model_name == "kuchera":
        return frame["baseline_slr"].to_numpy()
    if model_name == "rf_eb":
        return frozen["rf_eb"].predict(frame)
    return frozen[model_name].predict(frame[list(FEATURE_COLUMNS)].to_numpy())


def _season_metrics(frame: Any, predicted: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    observed = frame["observed_slr"].to_numpy()
    for water_year, group in frame.groupby("water_year"):
        mask = (frame["water_year"] == water_year).to_numpy()
        out[str(int(water_year))] = _metrics(observed[mask], predicted[mask])
    return out


def run_evaluate_hrrr_transfer(output_dir: Path) -> dict[str, Any]:
    import joblib
    import numpy as np
    import pandas as pd

    from .gold_profiles import FEATURE_COLUMNS

    out = transfer_dir(output_dir)
    bm_frame, accounting = load_modeling_frame(output_dir / "features.parquet")
    hrrr_all = pd.read_parquet(out / "features_hrrr.parquet")
    hrrr_frame = hrrr_all[(hrrr_all["profile_status"] == "ok") & hrrr_all["baseline_slr"].notna()].copy()
    hrrr_frame = hrrr_frame.reset_index(drop=True)

    covered_ids = {str(event_id) for event_id in hrrr_frame["event_id"]}
    bm_subset = bm_frame[bm_frame["event_id"].isin(covered_ids)].copy().reset_index(drop=True)
    hrrr_subset = hrrr_frame.set_index("event_id").loc[bm_subset["event_id"]].reset_index(drop=True)

    # Frozen step-2 models: RF/GBM from the joblib artifacts; RF+EB
    # reconstructed once on the full best-match frame (deterministic).
    frozen: dict[str, Any] = {
        "rf": joblib.load(output_dir / "rf_full.joblib"),
        "gbm": joblib.load(output_dir / "gbm_full.joblib"),
    }
    rf_eb = RFEBModel(seed=0).fit(bm_frame)
    frozen["rf_eb"] = rf_eb
    inner_diff = float(np.max(np.abs(
        rf_eb.rf.predict(bm_frame[list(FEATURE_COLUMNS)].to_numpy())
        - frozen["rf"].predict(bm_frame[list(FEATURE_COLUMNS)].to_numpy())
    )))

    sources = {"bestMatch": bm_subset, "hrrr": hrrr_subset}
    metrics: dict[str, Any] = {}
    for source, frame in sources.items():
        metrics[source] = {}
        for model_name in TRANSFER_MODELS:
            predicted = _predict_frozen(model_name, frame, frozen)
            entry = evaluate_predictions(frame, predicted)
            entry["byWaterYear"] = _season_metrics(frame, predicted)
            metrics[source][model_name] = entry

    regions = sorted(bm_subset["region"].unique())
    verdicts: dict[str, Any] = {}
    for region in regions:
        bm_region = metrics["bestMatch"]["rf_eb"]["perRegion"][region]
        hrrr_region = metrics["hrrr"]["rf_eb"]["perRegion"][region]
        kuchera_region = metrics["hrrr"]["kuchera"]["perRegion"][region]
        verdicts[region] = {
            "n": hrrr_region["n"],
            "rfEbMaeBestMatch": bm_region["mae"],
            "rfEbMaeHrrr": hrrr_region["mae"],
            "rfEbDegradation": round(hrrr_region["mae"] - bm_region["mae"], 4),
            "kucheraMaeHrrr": kuchera_region["mae"],
            "kucheraMaeBestMatch": metrics["bestMatch"]["kuchera"]["perRegion"][region]["mae"],
            "rfEbBeatsKucheraOnHrrr": hrrr_region["mae"] < kuchera_region["mae"],
            "rfEbMarginVsKucheraHrrr": round(kuchera_region["mae"] - hrrr_region["mae"], 4),
        }

    results: dict[str, Any] = {
        "provenance": {
            "trainingProfileSource": "open-meteo-historical-forecast-best-match",
            "transferProfileSource": TRANSFER_SOURCE_ID,
            "transferProduct": "NOAA HRRR wrfprs GRIB2, bucket noaa-hrrr-bdp-pds",
            "cycleConvention": (
                "per window instant t: latest cycle initialized at or before t-1h, "
                "forecast hour valid at t (CONUS hourly cycles -> f01 uniform; "
                "Alaska 3-hourly cycles -> f01-f03)"
            ),
            "modelsFrozen": "rf/gbm loaded from step-2 joblib artifacts; rf_eb refit once on the "
                              "full best-match modeling frame via gold_model.RFEBModel(seed=0), no HRRR data in any fit",
            "rfEbInnerRfMaxAbsDiffVsRfFull": inner_diff,
        },
        "accounting": {
            "bestMatchModelingRows": int(len(bm_frame)),
            "hrrrCoveredRows": int(len(hrrr_subset)),
            "hrrrCoverageByRegion": {
                str(k): int(v) for k, v in hrrr_subset["region"].value_counts().items()
            },
            "bestMatchByRegion": accounting["modelingByRegion"],
            "hrrrStatusCounts": {
                str(k): int(v) for k, v in hrrr_all["profile_status"].value_counts().items()
            },
        },
        "metrics": metrics,
        "perRegionVerdicts": verdicts,
    }
    (out / "transfer_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    write_transfer_report(results, out / "TRANSFER_REPORT.md")
    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt(value: Any) -> str:
    return "—" if value is None else f"{value:.3f}"


def write_transfer_report(results: dict[str, Any], path: Path) -> None:
    prov = results["provenance"]
    acc = results["accounting"]
    metrics = results["metrics"]
    verdicts = results["perRegionVerdicts"]
    lines: list[str] = []
    lines.append("# Step 5 — HRRR operational transfer test of the frozen step-2 gold models")
    lines.append("")
    lines.append("## Provenance — read first")
    lines.append("")
    lines.append(
        "**Training profiles: `open-meteo-historical-forecast-best-match`** (the Open-Meteo "
        "historical-forecast best-match archive; NOT ERA5). **Transfer profiles: NOAA HRRR "
        "`wrfprs` GRIB2** from the AWS bucket `noaa-hrrr-bdp-pds` (HRRR v4 era throughout the "
        "event pool). Cycle convention: for each hourly instant *t* of an event's 24-h "
        "observation window, the latest cycle initialized at or before *t* − 1 h at the "
        "forecast hour valid at *t* — CONUS hourly cycles give a uniform f01 (1-h lead); "
        "Alaska's 3-hourly cycles give f01–f03. The 27 AGL features and the Kuchera baseline "
        "are built by the identical step-2 code (`gold_profiles.build_event_features`); the "
        "models are frozen (RF/GBM from the step-2 joblib artifacts; RF+EB reconstructed once "
        "on the best-match frame only — its inner RF reproduces `rf_full.joblib` with max "
        f"abs prediction difference {prov['rfEbInnerRfMaxAbsDiffVsRfFull']:.4g}). No HRRR data "
        "enters any fit. All metrics below compare the same models on best-match vs HRRR "
        "features **on the identical covered event subset**, so differences are profile-source "
        "effects, not sample effects."
    )
    lines.append("")
    lines.append("## Coverage")
    lines.append("")
    lines.append(
        f"HRRR features complete for **{acc['hrrrCoveredRows']}** of "
        f"{acc['bestMatchModelingRows']} best-match modeling events. By region (covered / "
        "best-match rows): "
        + ", ".join(
            f"{region} {acc['hrrrCoverageByRegion'].get(region, 0)}/{n}"
            for region, n in sorted(acc["bestMatchByRegion"].items())
        )
        + ". HRRR build status counts: "
        + ", ".join(f"{k}={v}" for k, v in sorted(acc["hrrrStatusCounts"].items())) + "."
    )
    lines.append("")
    lines.append("## Headline — same subset, best-match vs HRRR profiles (MAE)")
    lines.append("")
    lines.append("| Model | best-match MAE | HRRR MAE | Δ (HRRR − best-match) | best-match bias | HRRR bias | HRRR RMSE | HRRR tail>20 MAE (n) | HRRR tail<5 MAE (n) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for model in TRANSFER_MODELS:
        bm = metrics["bestMatch"][model]["overall"]
        hr = metrics["hrrr"][model]["overall"]
        tail_h = metrics["hrrr"][model]["tailHigh_slr_gt_20"]
        tail_l = metrics["hrrr"][model]["tailLow_slr_lt_5"]
        lines.append(
            f"| {model} | {bm['mae']:.3f} | {hr['mae']:.3f} | {hr['mae'] - bm['mae']:+.3f} | "
            f"{_fmt(bm['bias'])} | {_fmt(hr['bias'])} | {_fmt(hr['rmse'])} | "
            f"{_fmt(tail_h['mae'])} ({tail_h['n']}) | {_fmt(tail_l['mae'])} ({tail_l['n']}) |"
        )
    lines.append("")
    lines.append("## Per-region verdicts — RF+EB on HRRR vs Kuchera on HRRR")
    lines.append("")
    lines.append("| Region | n | RF+EB best-match | RF+EB HRRR | Δ | Kuchera HRRR | RF+EB margin | verdict |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
    for region, verdict in sorted(verdicts.items()):
        lines.append(
            f"| {region} | {verdict['n']} | {verdict['rfEbMaeBestMatch']:.3f} | "
            f"{verdict['rfEbMaeHrrr']:.3f} | {verdict['rfEbDegradation']:+.3f} | "
            f"{verdict['kucheraMaeHrrr']:.3f} | {verdict['rfEbMarginVsKucheraHrrr']:+.3f} | "
            f"{'RF+EB beats Kuchera' if verdict['rfEbBeatsKucheraOnHrrr'] else 'RF+EB LOSES to Kuchera'} |"
        )
    lines.append("")
    wins = sum(1 for v in verdicts.values() if v["rfEbBeatsKucheraOnHrrr"])
    lines.append(
        f"RF+EB on HRRR features beats Kuchera on HRRR features in **{wins} of "
        f"{len(verdicts)} regions** on the covered subset."
    )
    lines.append("")
    lines.append("## Tails (same subset)")
    lines.append("")
    lines.append("| Model | source | tail>20 MAE (n) | tail<5 MAE (n) |")
    lines.append("|---|---|---:|---:|")
    for model in TRANSFER_MODELS:
        for source in ("bestMatch", "hrrr"):
            entry = metrics[source][model]
            lines.append(
                f"| {model} | {source} | {_fmt(entry['tailHigh_slr_gt_20']['mae'])} "
                f"({entry['tailHigh_slr_gt_20']['n']}) | {_fmt(entry['tailLow_slr_lt_5']['mae'])} "
                f"({entry['tailLow_slr_lt_5']['n']}) |"
            )
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append("- HRRR is a 3-km convection-allowing forecast model, not a reanalysis: its "
                 "profiles carry genuine short-lead forecast error, complex-terrain smoothing "
                 "(the nearest gridpoint's terrain can sit well below a mountain station), and "
                 "its own precipitation phase partitioning (APCP/ASNOW drive the Kuchera "
                 "baseline's interval weighting here, exactly as the best-match build used its "
                 "archive's precipitation/snowfall fields).")
    lines.append("- Alaska cycles in the archive are 3-hourly, so Alaska instants use leads of "
                 "1–3 h while CONUS uses a uniform 1-h lead; read Alaska's degradation with "
                 "that asymmetry in mind.")
    lines.append("- Events whose HRRR windows are incomplete (missing files or < 18 valid "
                 "profile hours, or no Kuchera baseline) are excluded from the covered subset "
                 "and counted in the coverage block above; both profile sources are scored on "
                 "the identical subset.")
    lines.append("- Labels and frozen models are unchanged from step 2, with its caveats "
                 "(behaviour-screened CoCoRaHS tier; low-elevation Northeast/Great Lakes). "
                 "Research only — production calibration gates untouched.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
