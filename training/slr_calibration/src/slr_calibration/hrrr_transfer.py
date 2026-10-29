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
from collections import Counter, OrderedDict, defaultdict
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
    # Unique tmp name per writer: two writers of the same cache path
    # (e.g. a resumed campaign overlapping a still-running earlier one)
    # must not rename each other's tmp file away mid-write.
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
    try:
        os.replace(tmp, path)
    except FileNotFoundError:
        # Another writer placed the file first; its content derives from
        # the same source file and station grid, so it is interchangeable.
        if not path.exists():
            raise
        tmp.unlink(missing_ok=True)


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
    memo_maxsize: int = 300,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per-event HRRR features for every event, with status accounting.

    Events are processed in (domain, valid_time) order so the points-cache
    memo has temporal locality, and the memo is a bounded LRU: an earlier
    version memoized every parsed file it touched (up to 26,185 payloads
    of ~800 stations x 68 values each) and was OOM-killed mid-build with
    no output.  Rows are returned in the input event order regardless of
    processing order.
    """
    cache_memo: OrderedDict[
        tuple[str, datetime, int, bool], dict[str, Any] | None
    ] = OrderedDict()

    def load_points(domain: str, cycle: datetime, fxx: int, reduced: bool):
        key = (domain, cycle, fxx, reduced)
        if key in cache_memo:
            cache_memo.move_to_end(key)
            return cache_memo[key]
        points = _read_points_cache(
            _points_cache_path(cache_dir, domain, cycle, fxx, reduced)
        )
        cache_memo[key] = points
        while len(cache_memo) > memo_maxsize:
            cache_memo.popitem(last=False)
        return points

    rows_by_id: dict[str, dict[str, Any]] = {}
    status_counts: Counter[str] = Counter()
    region_status: dict[str, Counter[str]] = defaultdict(Counter)
    wy_status: dict[str, Counter[str]] = defaultdict(Counter)
    ordered = sorted(
        events,
        key=lambda e: (station_domain(e), e["valid_time"], str(e["event_id"])),
    )
    for index, event in enumerate(ordered, start=1):
        hours = assemble_event_hours(event, load_points)
        row = build_event_features(event, hours, TRANSFER_SOURCE_ID)
        if not hours:
            row["profile_status"] = "fetch_missing"
        rows_by_id[str(event["event_id"])] = row
        status_counts[row["profile_status"]] += 1
        region_status[event["region"]][row["profile_status"]] += 1
        wy_status[str(event["water_year"])][row["profile_status"]] += 1
        if index % 2000 == 0 or index == len(ordered):
            print(f"[hrrr-transfer] built {index}/{len(ordered)} events", flush=True)
    rows = [rows_by_id[str(event["event_id"])] for event in events]
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


# ---------------------------------------------------------------------------
# Champion transfer re-run (era5-expanded-v2): ENS-EB on HRRR profiles
#
# Step 5 asked whether the frozen step-2 models (trained on Open-Meteo
# best-match profiles) survive HRRR features.  This section re-runs the
# same feature-source transfer test for the designated champion of the
# era5-expanded-v2 pool: ENS-EB, the mean of RF+EB and GBM+EB, each
# fitted on the full v2 modeling frame (ERA5 profiles, source
# ``era5-nsf-ncar-aws``) exactly as judged in the cheap-wins bundle
# (``gold_model.RFEBModel`` / ``GBMEBModel`` with seed 0; the run
# verifies the reconstructed inner RF / GBM reproduce the v2
# ``rf_full.joblib`` / ``gbm_full.joblib`` artifacts).  No HRRR data
# enters any fit; both profile sources are scored on the identical
# covered event subset, so differences are profile-source effects, not
# sample effects.  This is a feature-source transfer test on the same
# events, NOT a holdout.
#
# HRRR features are reused from the step-5 build wherever they exist:
# the event-id space is shared (ghcn:<station>:<date>) and an event's
# HRRR features depend only on the event and the HRRR archive, not on
# the training profile source.  The reuse window is the step-5 pool
# window: v2 modeling events with valid_time at or after the earliest
# step-2 modeling event.  Step-5 rows with a deterministic non-ok
# status (no_baseline: the Kuchera baseline failure follows from the
# profiles themselves) are carried verbatim; fetch_missing /
# incomplete_profile rows are rebuilt, as are in-window v2 events
# step 5 never attempted (the v2 pool's minute-offset recoveries and
# other additions).  New builds run against a points cache seeded
# from the step-5 cache (hardlinks); files needed by events at
# stations step 5 never sampled are purged from the seed so they are
# re-extracted with the enlarged station grid, because seeded payloads
# only contain step-5 stations.
# ---------------------------------------------------------------------------

CHAMPION_MODELS = ("ens_eb", "rf_eb", "gbm_eb", "kuchera")
CHAMPION_RETRY_STATUSES = ("fetch_missing", "incomplete_profile")


def plan_champion_transfer(
    v2_frame: Any,
    step2_frame: Any,
    step5_rows: Any,
    step5_station_ids: set[str],
) -> dict[str, Any]:
    """Partition the v2 pool for the champion HRRR build (pure).

    Frames are DataFrames with event_id / valid_time / station_id
    columns (step5_rows also profile_status).  Returns the window
    floor plus disjoint sorted id lists: step-5 ok rows to reuse,
    deterministic non-ok rows to carry verbatim, rows to rebuild
    (split into retry vs never-attempted), the union build list, and
    the build stations step 5 never sampled (with their event ids).
    """
    import pandas as pd

    floor = pd.to_datetime(step2_frame["valid_time"], utc=True).min()
    v2 = v2_frame.copy()
    v2["valid_time"] = pd.to_datetime(v2["valid_time"], utc=True)
    in_window = v2[v2["valid_time"] >= floor]
    status = {
        str(event_id): str(value)
        for event_id, value in zip(step5_rows["event_id"], step5_rows["profile_status"])
    }
    reused: list[str] = []
    carried: list[str] = []
    retry: list[str] = []
    fresh: list[str] = []
    for event_id in (str(value) for value in in_window["event_id"]):
        value = status.get(event_id)
        if value == "ok":
            reused.append(event_id)
        elif value in CHAMPION_RETRY_STATUSES:
            retry.append(event_id)
        elif value is not None:
            carried.append(event_id)
        else:
            fresh.append(event_id)
    build_ids = sorted(retry + fresh)
    station_of = {
        str(event_id): str(station_id)
        for event_id, station_id in zip(in_window["event_id"], in_window["station_id"])
    }
    new_stations = sorted({station_of[event_id] for event_id in build_ids} - set(step5_station_ids))
    new_station_set = set(new_stations)
    return {
        "windowFloor": floor.isoformat(),
        "v2ModelingRows": int(len(v2_frame)),
        "inWindowRows": int(len(in_window)),
        "reusedOk": sorted(reused),
        "carriedNonOk": sorted(carried),
        "retry": sorted(retry),
        "fresh": sorted(fresh),
        "build": build_ids,
        "newStationIds": new_stations,
        "newStationEventIds": sorted(
            event_id for event_id in build_ids if station_of[event_id] in new_station_set
        ),
        "step5StationCount": int(len(step5_station_ids)),
    }


def _plan_counts(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (len(value) if isinstance(value, list) else value)
        for key, value in plan.items()
    }


def seed_champion_cache(source_cache_dir: Path, cache_dir: Path) -> dict[str, int]:
    """Seed the champion points cache from the step-5 cache.

    Hardlinks where possible (copies otherwise).  Grid-index files
    are not carried over: they are keyed to the step-5 station set
    and are rebuilt for the champion station set on first fetch.
    """
    import shutil

    stats = {"linked": 0, "copied": 0, "skippedExisting": 0}
    if not source_cache_dir.exists():
        return stats
    for src in sorted(source_cache_dir.rglob("*")):
        if not src.is_file() or src.name.startswith("grid_indices_"):
            continue
        dest = cache_dir / src.relative_to(source_cache_dir)
        if dest.exists():
            stats["skippedExisting"] += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dest)
            stats["linked"] += 1
        except OSError:
            shutil.copy2(src, dest)
            stats["copied"] += 1
    return stats


def purge_new_station_files(cache_dir: Path, events: list[dict[str, Any]]) -> int:
    """Delete seeded cache files the given events need.

    Seeded payloads only contain step-5 stations, so any file a
    new-station event depends on must be re-extracted with the
    enlarged station grid; deleting it here makes the fetch driver
    download it again instead of trusting the seeded copy.
    """
    full, acc_prev = plan_transfer_files(events)
    keys: list[tuple[str, datetime, int, bool]] = [
        (domain, cycle, fxx, False) for (domain, cycle, fxx) in full
    ]
    keys += [
        (domain, cycle, fxx, True)
        for (domain, cycle, fxx) in acc_prev
        if (domain, cycle, fxx) not in full
    ]
    removed = 0
    for domain, cycle, fxx, reduced in keys:
        path = _points_cache_path(cache_dir, domain, cycle, fxx, reduced)
        if path.exists():
            path.unlink()
            removed += 1
    return removed


def merge_champion_rows(
    ordered_ids: list[str],
    reused_records: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge reused step-5 rows and newly built rows in a fixed order."""
    by_id: dict[str, dict[str, Any]] = {}
    for row in list(reused_records) + list(new_rows):
        event_id = str(row["event_id"])
        if event_id in by_id:
            raise ValueError(f"duplicate transfer row for {event_id}")
        by_id[event_id] = row
    missing = [event_id for event_id in ordered_ids if event_id not in by_id]
    if missing:
        raise ValueError(
            f"{len(missing)} in-window events have no transfer row (first: {missing[0]})"
        )
    return [by_id[event_id] for event_id in ordered_ids]


def feature_parity(era5_frame: Any, hrrr_frame: Any) -> list[dict[str, Any]]:
    """Paired per-feature means on the covered subset (same events)."""
    import numpy as np

    from .gold_profiles import FEATURE_COLUMNS

    rows: list[dict[str, Any]] = []
    for column in list(FEATURE_COLUMNS) + ["baseline_slr"]:
        era5_values = era5_frame[column].to_numpy(dtype=float)
        hrrr_values = hrrr_frame[column].to_numpy(dtype=float)
        rows.append({
            "feature": column,
            "era5Mean": round(float(np.mean(era5_values)), 4),
            "hrrrMean": round(float(np.mean(hrrr_values)), 4),
            "meanDiffHrrrMinusEra5": round(float(np.mean(hrrr_values - era5_values)), 4),
        })
    return rows


def champion_verdict(metrics: dict[str, Any]) -> dict[str, Any]:
    """Pre-declared verdict for the champion transfer check (pure).

    The champion transfers iff ENS-EB on HRRR beats Kuchera on HRRR
    overall AND in at least 5 of 6 regions.  The transfer cost
    (ENS-EB HRRR minus ENS-EB ERA5, same events) is reported plainly
    whatever it is, as are the step-5 watch items.
    """
    ens_hrrr = metrics["hrrr"]["ens_eb"]
    ens_era5 = metrics["era5"]["ens_eb"]
    rf_hrrr = metrics["hrrr"]["rf_eb"]
    kuchera_hrrr = metrics["hrrr"]["kuchera"]
    regions = sorted(ens_hrrr["perRegion"])
    region_wins = {
        region: ens_hrrr["perRegion"][region]["mae"] < kuchera_hrrr["perRegion"][region]["mae"]
        for region in regions
    }
    wins = int(sum(region_wins.values()))
    beats_overall = ens_hrrr["overall"]["mae"] < kuchera_hrrr["overall"]["mae"]
    return {
        "ensEbBeatsKucheraOverall": bool(beats_overall),
        "regionsWonVsKuchera": wins,
        "regionsTotal": len(regions),
        "regionWinsVsKuchera": region_wins,
        "transfers": bool(beats_overall and wins >= 5),
        "transferCostMae": round(ens_hrrr["overall"]["mae"] - ens_era5["overall"]["mae"], 4),
        "transferCostByRegion": {
            region: round(
                ens_hrrr["perRegion"][region]["mae"] - ens_era5["perRegion"][region]["mae"], 4
            )
            for region in regions
        },
        "ensEbBeatsRfEbOnHrrr": bool(ens_hrrr["overall"]["mae"] < rf_hrrr["overall"]["mae"]),
        "ensEbVsRfEbOnHrrrByRegion": {
            region: bool(ens_hrrr["perRegion"][region]["mae"] < rf_hrrr["perRegion"][region]["mae"])
            for region in regions
        },
        "watchItems": {
            "alaskaBiasEnsEbHrrr": ens_hrrr["perRegion"]["alaska"]["bias"],
            "alaskaBiasRfEbHrrr": rf_hrrr["perRegion"]["alaska"]["bias"],
            "alaskaBiasEnsEbEra5": ens_era5["perRegion"]["alaska"]["bias"],
            "tailLowMaeEnsEbHrrr": ens_hrrr["tailLow_slr_lt_5"]["mae"],
            "tailLowMaeRfEbHrrr": rf_hrrr["tailLow_slr_lt_5"]["mae"],
            "tailLowMaeKucheraHrrr": kuchera_hrrr["tailLow_slr_lt_5"]["mae"],
            "tailLowEnsEbBeatsKuchera": bool(
                ens_hrrr["tailLow_slr_lt_5"]["mae"] < kuchera_hrrr["tailLow_slr_lt_5"]["mae"]
            ),
        },
    }


def _champion_context(
    pool_dir: Path, output_dir: Path, source_output_dir: Path
) -> dict[str, Any]:
    import pandas as pd

    v2_frame, _v2_acc = load_modeling_frame(output_dir / "features.parquet")
    step2_frame, _s2_acc = load_modeling_frame(source_output_dir / "features.parquet")
    step5_rows = pd.read_parquet(transfer_dir(source_output_dir) / "features_hrrr.parquet")
    screened = load_screened_events(pool_dir)
    v2_ids = {str(value) for value in v2_frame["event_id"]}
    step2_ids = {str(value) for value in step2_frame["event_id"]}
    events_by_id = {e["event_id"]: e for e in screened if e["event_id"] in v2_ids}
    step5_station_ids = {e["station_id"] for e in screened if e["event_id"] in step2_ids}
    plan = plan_champion_transfer(v2_frame, step2_frame, step5_rows, step5_station_ids)
    floor = datetime.fromisoformat(plan["windowFloor"])
    ordered_ids: list[str] = []
    for event in screened:
        if event["event_id"] not in v2_ids:
            continue
        valid_time = event["valid_time"]
        if valid_time.tzinfo is None:
            valid_time = valid_time.replace(tzinfo=timezone.utc)
        if valid_time >= floor:
            ordered_ids.append(event["event_id"])
    return {
        "plan": plan,
        "floor": floor,
        "v2_frame": v2_frame,
        "step5_rows": step5_rows,
        "events_by_id": events_by_id,
        "ordered_ids": ordered_ids,
    }


def run_fetch_champion_transfer(
    pool_dir: Path,
    output_dir: Path,
    source_output_dir: Path,
    max_workers: int = 10,
) -> dict[str, Any]:
    """Seed the champion cache from step 5 and fetch what is missing."""
    ctx = _champion_context(pool_dir, output_dir, source_output_dir)
    plan = ctx["plan"]
    out = transfer_dir(output_dir)
    cache_dir = out / "raw_cache_hrrr"
    cache_dir.mkdir(parents=True, exist_ok=True)
    seed = seed_champion_cache(
        transfer_dir(source_output_dir) / "raw_cache_hrrr", cache_dir
    )
    new_station_events = [ctx["events_by_id"][event_id] for event_id in plan["newStationEventIds"]]
    purged = purge_new_station_files(cache_dir, new_station_events) if new_station_events else 0
    build_events = [ctx["events_by_id"][event_id] for event_id in plan["build"]]
    fetcher = PooledRangeFetcher()
    summary = fetch_transfer_files(build_events, cache_dir, fetcher, max_workers=max_workers)
    result = {
        "plan": _plan_counts(plan),
        "seed": seed,
        "purgedForNewStations": purged,
        "fetch": summary,
    }
    (out / "fetch_summary_champion.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


def _has_baseline(row: dict[str, Any]) -> bool:
    value = row.get("baseline_slr")
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def champion_coverage(
    rows: list[dict[str, Any]], v2_frame: Any, floor: datetime
) -> dict[str, Any]:
    """Coverage accounting for the merged champion HRRR table."""
    import pandas as pd

    v2 = v2_frame.copy()
    v2["valid_time"] = pd.to_datetime(v2["valid_time"], utc=True)
    floor_ts = pd.Timestamp(floor)
    if floor_ts.tzinfo is None:
        floor_ts = floor_ts.tz_localize("UTC")
    in_window = v2[v2["valid_time"] >= floor_ts]
    covered_ids = {
        str(row["event_id"]) for row in rows if row["profile_status"] == "ok" and _has_baseline(row)
    }
    by_region: dict[str, Any] = {}
    for region in sorted(str(value) for value in v2["region"].unique()):
        region_rows = [row for row in rows if row["region"] == region]
        by_region[region] = {
            "v2ModelingRows": int((v2["region"] == region).sum()),
            "inWindowRows": int((in_window["region"] == region).sum()),
            "coveredRows": int(sum(1 for row in region_rows if str(row["event_id"]) in covered_ids)),
            "statusCounts": dict(Counter(row["profile_status"] for row in region_rows)),
        }
    return {
        "profileSource": TRANSFER_SOURCE_ID,
        "v2ModelingRows": int(len(v2)),
        "inWindowRows": int(len(in_window)),
        "coveredRows": len(covered_ids),
        "statusCounts": dict(Counter(row["profile_status"] for row in rows)),
        "byRegion": by_region,
    }


def run_build_champion_features(
    pool_dir: Path,
    output_dir: Path,
    source_output_dir: Path,
) -> dict[str, Any]:
    """Build HRRR features for the champion build set and merge with step 5."""
    from .gold_profiles import write_features_parquet

    ctx = _champion_context(pool_dir, output_dir, source_output_dir)
    plan = ctx["plan"]
    out = transfer_dir(output_dir)
    cache_dir = out / "raw_cache_hrrr"
    build_events = [ctx["events_by_id"][event_id] for event_id in plan["build"]]
    new_rows, _new_coverage = build_transfer_features(build_events, cache_dir)
    keep = set(plan["reusedOk"]) | set(plan["carriedNonOk"])
    step5 = ctx["step5_rows"]
    reused_records = step5[step5["event_id"].astype(str).isin(keep)].to_dict("records")
    rows = merge_champion_rows(ctx["ordered_ids"], reused_records, new_rows)
    write_features_parquet(rows, out / "features_hrrr.parquet")
    coverage = champion_coverage(rows, ctx["v2_frame"], ctx["floor"])
    coverage["reuse"] = _plan_counts(plan)
    (out / "feature_coverage_hrrr.json").write_text(
        json.dumps(coverage, indent=2), encoding="utf-8"
    )
    return coverage


# ---------------------------------------------------------------------------
# Champion transfer evaluation
# ---------------------------------------------------------------------------

def _champion_predictions(frame: Any, fitted: dict[str, Any]) -> dict[str, Any]:
    import numpy as np

    rf_pred = fitted["rf_eb"].predict(frame)
    gbm_pred = fitted["gbm_eb"].predict(frame)
    return {
        "ens_eb": (rf_pred + gbm_pred) / 2.0,
        "rf_eb": rf_pred,
        "gbm_eb": gbm_pred,
        "kuchera": frame["baseline_slr"].to_numpy(),
    }


def run_evaluate_champion_transfer(output_dir: Path) -> dict[str, Any]:
    import joblib
    import numpy as np
    import pandas as pd

    from .gold_model import GBMEBModel
    from .gold_profiles import FEATURE_COLUMNS

    out = transfer_dir(output_dir)
    era5_frame, accounting = load_modeling_frame(output_dir / "features.parquet")
    hrrr_all = pd.read_parquet(out / "features_hrrr.parquet")
    hrrr_frame = hrrr_all[
        (hrrr_all["profile_status"] == "ok") & hrrr_all["baseline_slr"].notna()
    ].copy().reset_index(drop=True)
    covered_ids = {str(value) for value in hrrr_frame["event_id"]}
    era5_subset = era5_frame[era5_frame["event_id"].astype(str).isin(covered_ids)].copy()
    era5_subset = era5_subset.reset_index(drop=True)
    hrrr_subset = hrrr_frame.set_index("event_id").loc[era5_subset["event_id"]].reset_index(drop=True)

    # The champion, fitted on the full v2 ERA5 modeling frame exactly
    # as judged (seed 0).  Verify the reconstructed inner models
    # reproduce the v2 full-pool artifacts before trusting them.
    rf_eb = RFEBModel(seed=0).fit(era5_frame)
    gbm_eb = GBMEBModel(seed=0).fit(era5_frame)
    features = era5_frame[list(FEATURE_COLUMNS)].to_numpy()
    rf_diff = float(np.max(np.abs(
        rf_eb.rf.predict(features)
        - joblib.load(output_dir / "rf_full.joblib").predict(features)
    )))
    gbm_diff = float(np.max(np.abs(
        gbm_eb.gbm.predict(features)
        - joblib.load(output_dir / "gbm_full.joblib").predict(features)
    )))
    fitted = {"rf_eb": rf_eb, "gbm_eb": gbm_eb}

    metrics: dict[str, Any] = {}
    for source, frame in (("era5", era5_subset), ("hrrr", hrrr_subset)):
        predictions = _champion_predictions(frame, fitted)
        metrics[source] = {}
        for model_name in CHAMPION_MODELS:
            entry = evaluate_predictions(frame, predictions[model_name])
            entry["byWaterYear"] = _season_metrics(frame, predictions[model_name])
            metrics[source][model_name] = entry

    coverage_path = out / "feature_coverage_hrrr.json"
    build_coverage = (
        json.loads(coverage_path.read_text(encoding="utf-8")) if coverage_path.exists() else None
    )
    results: dict[str, Any] = {
        "provenance": {
            "trainingProfileSource": "era5-nsf-ncar-aws",
            "trainingProfiles": "ERA5 via the NSF NCAR AWS mirror (ds633.0), "
                                "era5-expanded-v2 feature build",
            "transferProfileSource": TRANSFER_SOURCE_ID,
            "transferProduct": "NOAA HRRR wrfprs GRIB2, bucket noaa-hrrr-bdp-pds",
            "cycleConvention": (
                "per window instant t: latest cycle initialized at or before t-1h, "
                "forecast hour valid at t (CONUS hourly cycles -> f01 uniform; "
                "Alaska 3-hourly cycles -> f01-f03)"
            ),
            "champion": "ENS-EB = mean of RF+EB and GBM+EB predictions; each component "
                        "is gold_model.RFEBModel / GBMEBModel (seed 0) fitted on the full "
                        "v2 ERA5 modeling frame; no HRRR data in any fit",
            "framing": "feature-source transfer test on the same events (not a holdout): "
                       "identical covered subset scored with ERA5 vs HRRR features",
            "rfEbInnerRfMaxAbsDiffVsRfFull": rf_diff,
            "gbmEbInnerGbmMaxAbsDiffVsGbmFull": gbm_diff,
        },
        "accounting": {
            "era5ModelingRows": int(len(era5_frame)),
            "coveredRows": int(len(hrrr_subset)),
            "coveredByRegion": {
                str(k): int(v) for k, v in hrrr_subset["region"].value_counts().items()
            },
            "era5ByRegion": accounting["modelingByRegion"],
            "buildCoverage": build_coverage,
        },
        "metrics": metrics,
        "featureParity": feature_parity(era5_subset, hrrr_subset),
        "verdict": champion_verdict(metrics),
    }
    (out / "transfer_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    write_champion_transfer_report(results, out / "TRANSFER_REPORT.md")
    return results


# ---------------------------------------------------------------------------
# Champion transfer report
# ---------------------------------------------------------------------------

def write_champion_transfer_report(results: dict[str, Any], path: Path) -> None:
    prov = results["provenance"]
    acc = results["accounting"]
    metrics = results["metrics"]
    verdict = results["verdict"]
    watch = verdict["watchItems"]
    build = acc.get("buildCoverage") or {}
    reuse = build.get("reuse") or {}
    lines: list[str] = []
    lines.append("# HRRR transfer check — champion ENS-EB (era5-expanded-v2)")
    lines.append("")
    lines.append("## Provenance — read first")
    lines.append("")
    lines.append(
        "**Training profiles: ERA5 (`era5-nsf-ncar-aws`)** — ECMWF ERA5 via the NSF NCAR "
        "AWS mirror (RDA ds633.0), the era5-expanded-v2 feature build. **Transfer profiles: "
        "NOAA HRRR `wrfprs` GRIB2** from the AWS bucket `noaa-hrrr-bdp-pds` (HRRR v4 era "
        "throughout the covered window). Cycle convention: for each hourly instant *t* of "
        "an event's 24-h observation window, the latest cycle initialized at or before "
        "*t* − 1 h at the forecast hour valid at *t* — CONUS hourly cycles give a uniform "
        "f01 (1-h lead); Alaska's 3-hourly cycles give f01–f03. The 27 AGL features and "
        "the Kuchera baseline are built by the identical code "
        "(`gold_profiles.build_event_features`) for both sources; the snowfall field "
        "(HRRR ASNOW) feeds only the Kuchera baseline's interval weighting and never the "
        "27 model features, exactly as in step 5."
    )
    lines.append("")
    lines.append(
        "**Champion: ENS-EB** — the simple average of RF+EB and GBM+EB, each fitted on "
        "the full v2 ERA5 modeling frame with the judged construction "
        "(`RFEBModel` / `GBMEBModel`, seed 0; reconstructed inner RF vs `rf_full.joblib` "
        f"max abs prediction difference {prov['rfEbInnerRfMaxAbsDiffVsRfFull']:.4g}; inner "
        f"GBM vs `gbm_full.joblib` {prov['gbmEbInnerGbmMaxAbsDiffVsGbmFull']:.4g}). No HRRR "
        "data enters any fit. **Framing: this is a feature-source transfer test on the "
        "same events, not a holdout** — the identical covered subset is scored with ERA5 "
        "vs HRRR features, so differences are profile-source effects, not sample effects."
    )
    lines.append("")
    lines.append("## Coverage")
    lines.append("")
    lines.append(
        f"HRRR features complete (status ok, Kuchera baseline computable) for "
        f"**{acc['coveredRows']}** of {build.get('inWindowRows', '?')} v2 modeling events "
        f"in the step-5 window (valid_time at or after {reuse.get('windowFloor', '?')}) "
        f"and of {acc['era5ModelingRows']} v2 modeling events overall (the rest predate "
        "the window). Reuse accounting: "
        f"{reuse.get('reusedOk', '?')} step-5 ok rows reused verbatim, "
        f"{reuse.get('carriedNonOk', '?')} deterministic no-baseline rows carried, "
        f"{reuse.get('build', '?')} events rebuilt "
        f"({reuse.get('fresh', '?')} never attempted by step 5, "
        f"{reuse.get('retry', '?')} retried after a step-5 fetch/incomplete failure). "
        "HRRR build status counts (merged table): "
        + ", ".join(f"{k}={v}" for k, v in sorted((build.get("statusCounts") or {}).items()))
        + "."
    )
    lines.append("")
    lines.append("| Region | covered | in-window v2 rows | all v2 rows |")
    lines.append("|---|---:|---:|---:|")
    for region, entry in sorted((build.get("byRegion") or {}).items()):
        lines.append(
            f"| {region} | {entry['coveredRows']} | {entry['inWindowRows']} | "
            f"{entry['v2ModelingRows']} |"
        )
    lines.append("")
    lines.append("## Headline — same covered subset, ERA5 vs HRRR profiles (MAE)")
    lines.append("")
    lines.append("| Model | ERA5 MAE | HRRR MAE | Δ (HRRR − ERA5) | ERA5 bias | HRRR bias | HRRR RMSE | HRRR tail>20 MAE (n) | HRRR tail<5 MAE (n) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for model in CHAMPION_MODELS:
        era5 = metrics["era5"][model]["overall"]
        hrrr = metrics["hrrr"][model]["overall"]
        tail_h = metrics["hrrr"][model]["tailHigh_slr_gt_20"]
        tail_l = metrics["hrrr"][model]["tailLow_slr_lt_5"]
        lines.append(
            f"| {model} | {era5['mae']:.3f} | {hrrr['mae']:.3f} | {hrrr['mae'] - era5['mae']:+.3f} | "
            f"{_fmt(era5['bias'])} | {_fmt(hrrr['bias'])} | {_fmt(hrrr['rmse'])} | "
            f"{_fmt(tail_h['mae'])} ({tail_h['n']}) | {_fmt(tail_l['mae'])} ({tail_l['n']}) |"
        )
    lines.append("")
    lines.append("## Per-region — ENS-EB on HRRR vs Kuchera on HRRR")
    lines.append("")
    lines.append("| Region | n | ENS-EB ERA5 | ENS-EB HRRR | Δ | Kuchera HRRR | ENS-EB margin | ENS-EB bias (HRRR) | RF+EB HRRR | verdict |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for region in sorted(metrics["hrrr"]["ens_eb"]["perRegion"]):
        ens_e = metrics["era5"]["ens_eb"]["perRegion"][region]
        ens_h = metrics["hrrr"]["ens_eb"]["perRegion"][region]
        kuch = metrics["hrrr"]["kuchera"]["perRegion"][region]
        rf_h = metrics["hrrr"]["rf_eb"]["perRegion"][region]
        won = verdict["regionWinsVsKuchera"][region]
        lines.append(
            f"| {region} | {ens_h['n']} | {ens_e['mae']:.3f} | {ens_h['mae']:.3f} | "
            f"{ens_h['mae'] - ens_e['mae']:+.3f} | {kuch['mae']:.3f} | "
            f"{kuch['mae'] - ens_h['mae']:+.3f} | {_fmt(ens_h['bias'])} | {rf_h['mae']:.3f} | "
            f"{'ENS-EB beats Kuchera' if won else 'ENS-EB LOSES to Kuchera'} |"
        )
    lines.append("")
    lines.append(
        f"ENS-EB on HRRR features beats Kuchera on HRRR features in "
        f"**{verdict['regionsWonVsKuchera']} of {verdict['regionsTotal']} regions** and "
        f"{'beats' if verdict['ensEbBeatsKucheraOverall'] else 'loses to'} Kuchera overall. "
        f"Transfer cost (ENS-EB HRRR − ERA5, same events): "
        f"**{verdict['transferCostMae']:+.3f} MAE**. ENS-EB vs RF+EB on HRRR: ENS-EB "
        f"{'wins' if verdict['ensEbBeatsRfEbOnHrrr'] else 'loses'} overall "
        f"({metrics['hrrr']['ens_eb']['overall']['mae']:.3f} vs "
        f"{metrics['hrrr']['rf_eb']['overall']['mae']:.3f})."
    )
    lines.append("")
    lines.append("## Step-5 watch items")
    lines.append("")
    lines.append(
        f"- **Alaska bias (HRRR):** ENS-EB {_fmt(watch['alaskaBiasEnsEbHrrr'])}, RF+EB "
        f"{_fmt(watch['alaskaBiasRfEbHrrr'])} (step 5 recorded −1.32 for RF+EB on the old "
        f"pool/features; ENS-EB on ERA5 features for the same covered Alaska events: "
        f"{_fmt(watch['alaskaBiasEnsEbEra5'])})."
    )
    lines.append(
        f"- **SLR < 5 slice (HRRR):** ENS-EB {_fmt(watch['tailLowMaeEnsEbHrrr'])}, RF+EB "
        f"{_fmt(watch['tailLowMaeRfEbHrrr'])}, Kuchera {_fmt(watch['tailLowMaeKucheraHrrr'])} "
        f"— ENS-EB {'beats' if watch['tailLowEnsEbBeatsKuchera'] else 'does not beat'} "
        "Kuchera on this slice (step 5: Kuchera 3.54 edged RF+EB 3.63)."
    )
    lines.append("")
    lines.append("## Tails (same covered subset)")
    lines.append("")
    lines.append("| Model | source | tail>20 MAE (n) | tail<5 MAE (n) |")
    lines.append("|---|---|---:|---:|")
    for model in CHAMPION_MODELS:
        for source in ("era5", "hrrr"):
            entry = metrics[source][model]
            lines.append(
                f"| {model} | {source} | {_fmt(entry['tailHigh_slr_gt_20']['mae'])} "
                f"({entry['tailHigh_slr_gt_20']['n']}) | {_fmt(entry['tailLow_slr_lt_5']['mae'])} "
                f"({entry['tailLow_slr_lt_5']['n']}) |"
            )
    lines.append("")
    lines.append("## Feature parity — paired means on the covered subset")
    lines.append("")
    lines.append(
        "Identical 27-feature AGL spec from the identical builder for both sources; "
        "ASNOW is not a model feature on either side. Paired means below are the "
        "empirical check that the two builds describe the same events."
    )
    lines.append("")
    lines.append("| Feature | ERA5 mean | HRRR mean | mean Δ (HRRR − ERA5) |")
    lines.append("|---|---:|---:|---:|")
    for row in results["featureParity"]:
        lines.append(
            f"| {row['feature']} | {row['era5Mean']:.3f} | {row['hrrrMean']:.3f} | "
            f"{row['meanDiffHrrrMinusEra5']:+.3f} |"
        )
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    if verdict["transfers"]:
        lines.append(
            "**The champion transfers.** ENS-EB on HRRR features beats Kuchera on HRRR "
            f"features overall and in {verdict['regionsWonVsKuchera']} of "
            f"{verdict['regionsTotal']} regions, at a transfer cost of "
            f"{verdict['transferCostMae']:+.3f} MAE against its own ERA5-feature scores "
            "on the same events."
        )
    else:
        lines.append(
            "**The champion does not clear the step-5 transfer bar.** ENS-EB on HRRR "
            f"features {'beats' if verdict['ensEbBeatsKucheraOverall'] else 'loses to'} "
            f"Kuchera on HRRR overall and wins {verdict['regionsWonVsKuchera']} of "
            f"{verdict['regionsTotal']} regions (bar: overall win and at least 5 of 6). "
            f"Transfer cost: {verdict['transferCostMae']:+.3f} MAE."
        )
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append("- HRRR is a 3-km convection-allowing forecast model, not a reanalysis: its "
                 "profiles carry genuine short-lead forecast error, complex-terrain smoothing "
                 "(the nearest gridpoint's terrain can sit well below a mountain station), and "
                 "its own precipitation phase partitioning (APCP/ASNOW drive the Kuchera "
                 "baseline's interval weighting here, exactly as the ERA5 build used its "
                 "accumulation fields).")
    lines.append("- Alaska cycles in the archive are 3-hourly, so Alaska instants use leads of "
                 "1–3 h while CONUS uses a uniform 1-h lead; read Alaska's degradation with "
                 "that asymmetry in mind.")
    lines.append("- Events whose HRRR windows are incomplete (missing files or < 18 valid "
                 "profile hours) or whose Kuchera baseline is not computable on HRRR profiles "
                 "are excluded from the covered subset and counted in the coverage block "
                 "above; both profile sources are scored on the identical subset. The "
                 "no-baseline exclusion is deterministic given the HRRR profiles and is the "
                 "largest single coverage loss, exactly as in step 5.")
    lines.append("- Minute-offset events (recovered into the v2 pool) are sampled on HRRR at "
                 "the forecast valid at each window instant (a whole forecast hour), where "
                 "the ERA5 v2 build time-interpolated between whole-hour profiles; the "
                 "feature construction is otherwise identical.")
    lines.append("- Labels are the screened-v2 gold labels, with their caveats "
                 "(behaviour-screened CoCoRaHS tier; low-elevation Northeast/Great Lakes). "
                 "Research only — production calibration gates untouched.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
