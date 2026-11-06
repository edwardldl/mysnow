"""Queue item 7: paired v3 ENS-EB transfer check, ERA5 -> HRRR inputs.

Pre-registration: data/.../model-v3-hrrr-transfer/DESIGN.md.  The v3
champion (64 features) was trained and judged on ERA5 inputs only; this
module measures the paired SLR penalty of feeding the identical fold
models HRRR forecast profiles instead, on identical gold events.

Field-set gate (DESIGN.md): the step-5 points cache holds only the old
27-feature builder's fields (TMP/RH/UGRD/VGRD/HGT on 13 levels, TMP
2 m, APCP, ASNOW).  The v3 builders also need vertical velocity (VVEL),
specific humidity (SPFH), cloud liquid + rain mixing ratios (CLMR +
RWMR, integrated by the serving adapter's own
``model_serving.tclw_from_hydrometeors`` into column liquid water),
2 m dewpoint (DPT), 10 m winds and surface (skin) temperature.  All of
those exist in the wrfprs inventory across the era (spot-checked 2021 /
2023 / 2026, both domains) and are extracted here into a v3 points
cache; files already in the step-5 cache are seeded from it and only
the new-variable messages cross the network.  The convective
precipitation split (lsp/cp) does NOT exist in wrfprs at any checked
date, so the accumu series carries cp/lsp as None -- byte-for-byte the
convention of the production serving HRRR adapter
(``scripts/run_model_serving.py``), which has no convective field to
read either.  The regime bundle therefore takes its all-None-cp values
on HRRR inputs exactly as it does at serving time.

Cycle convention, hour assembly and the Kuchera baseline are step 5's,
unchanged: for each window endpoint t, the latest cycle initialised at
or before t - 1 h at the forecast hour valid at t (CONUS uniform f01;
Alaska f01-f03).  Feature building reuses the training builders
unmodified (``gold_profiles.build_event_features`` /
``build_event_features_offset`` and
``v3_features.build_event_v3_features``).

Measurement only: nothing here fits a correction, retrains a champion
or writes serving artefacts.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import threading
from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .gold_profiles import (
    PRESSURE_LEVELS_HPA,
    build_event_features,
    build_event_features_offset,
    event_window,
)
from .hrrr_transfer import (
    ACC_VARIABLES,
    WANTED_VARIABLES,
    _points_cache_path,
    _read_points_cache,
    cycle_for_instant,
    domain_stations,
    file_url,
    hourly_accumulation,
    load_or_build_domain_grid_indices,
    plan_transfer_files,
    station_domain,
    station_instant_record,
)
from .model_serving import tclw_from_hydrometeors
from .noaa_hrrr import (
    RangeFetcher,
    decode_message_values,
    parse_accumulation_span,
    parse_idx,
    plan_ranges,
)
from .v3_features import build_event_v3_features

V3_SOURCE_ID = "noaa-hrrr-wrfprs-transfer-v3"
CACHE_KIND_V3 = "hrrr_transfer_points_v3"
ERA_CUTOFF = datetime(2020, 12, 3, tzinfo=timezone.utc)

# (cache key, GRIB variable, GRIB level) for the v3-only fields.
NEW_VARIABLES: tuple[tuple[str, str, str], ...] = tuple(
    (f"w_{level}", "VVEL", f"{level} mb") for level in PRESSURE_LEVELS_HPA
) + tuple(
    (f"q_{level}", "SPFH", f"{level} mb") for level in PRESSURE_LEVELS_HPA
) + tuple(
    (f"clmr_{level}", "CLMR", f"{level} mb") for level in PRESSURE_LEVELS_HPA
) + tuple(
    (f"rwmr_{level}", "RWMR", f"{level} mb") for level in PRESSURE_LEVELS_HPA
) + (
    ("dpt2m_k", "DPT", "2 m above ground"),
    ("u10", "UGRD", "10 m above ground"),
    ("v10", "VGRD", "10 m above ground"),
    ("skt_k", "TMP", "surface"),
)
V3_WANTED_VARIABLES = WANTED_VARIABLES + NEW_VARIABLES
ACCUMULATION_KEYS = ("apcp_mm", "asnow_m")

# Decision-rule thresholds (DESIGN.md; SLR MAE).
POOLED_PENALTY_LIMIT = 0.10
CONCENTRATION_LIMIT = 0.15


# ---------------------------------------------------------------------------
# Event eligibility
# ---------------------------------------------------------------------------

def eligible_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Events whose whole HRRR footprint is v4-era (DESIGN.md)."""
    return [e for e in events if event_window(e)[0] >= ERA_CUTOFF]


def lead_band(event: dict[str, Any]) -> str:
    return "1-3h" if station_domain(event) == "alaska" else "1h"


# ---------------------------------------------------------------------------
# v3 points cache (complete extended rows; seeded from the step-5 cache)
# ---------------------------------------------------------------------------

def _read_points_any(path: Path) -> dict[str, dict[str, float | None]] | None:
    """Points payload of either cache kind, or None."""
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("points"), dict):
        return None
    return payload["points"]


def _write_v3_points(
    path: Path, domain: str, cycle: datetime, fxx: int, reduced: bool, url: str,
    points: dict[str, dict[str, float | None]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": CACHE_KIND_V3,
        "domain": domain,
        "cycle": cycle.astimezone(timezone.utc).isoformat(),
        "fxx": fxx,
        "reduced": reduced,
        "url": url,
        "points": points,
    }
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
    try:
        os.replace(tmp, path)
    except FileNotFoundError:
        if not path.exists():
            raise
        tmp.unlink(missing_ok=True)


def _fetch_messages(
    fetcher: RangeFetcher, url: str, wanted: tuple[tuple[str, str, str], ...],
    fxx: int, *, verify_accumulations: bool,
) -> dict[str, bytes]:
    """Raw GRIB message bytes for the wanted variables of one file."""
    entries = parse_idx(fetcher.get(url + ".idx").decode("utf-8"))
    if verify_accumulations:
        for key, var, level in wanted:
            if key in ACCUMULATION_KEYS:
                entry = next(
                    (e for e in entries if e["var"] == var and e["level"] == level), None
                )
                if entry is not None and parse_accumulation_span(entry["forecast"]) != (0, fxx):
                    raise ValueError(
                        f"{var} accumulation span is not (0, {fxx}) in {url}"
                    )
    have = {(e["var"], e["level"]) for e in entries}
    wanted = tuple(w for w in wanted if (w[1], w[2]) in have)
    file_size = fetcher.head_size(url)
    ranges = plan_ranges(entries, wanted, file_size)
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
    return messages


def _decode_points(
    messages: dict[str, bytes],
    wanted: tuple[tuple[str, str, str], ...],
    grid_indices: dict[str, int],
) -> dict[str, dict[str, float | None]]:
    points: dict[str, dict[str, float | None]] = {
        station_id: {} for station_id in grid_indices
    }
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
    return points


def cycle_for_instant_utc(instant: datetime, domain: str) -> tuple[datetime, int]:
    """Step-5 cycle convention evaluated in UTC (the archive's frame).

    ``hrrr_transfer.cycle_for_instant`` floors in the instant's own tz
    representation and the shared cache path / URL builders format wall
    time, so files were mislabelled whenever events in different UTC
    offsets shared a date (measured 2026-11-05: adjacent-hour content
    swaps between -07:00 and -08:00 events).  This variant normalises
    to UTC first: latest cycle init <= instant - 1 h; CONUS hourly;
    Alaska 3-hourly (00/03/.../21Z); fxx = whole hours cycle -> instant.
    """
    from slr_calibration.hrrr_transfer import cycle_step_hours

    utc = instant.astimezone(timezone.utc)
    base = utc - timedelta(hours=1)
    step = cycle_step_hours(domain)
    cycle = base.replace(minute=0, second=0, microsecond=0)
    cycle -= timedelta(hours=cycle.hour % step)
    fxx = int((utc - cycle).total_seconds() // 3600)
    return cycle, fxx


def file_url_utc(domain: str, cycle: datetime, fxx: int) -> str:
    """Archive URL for a UTC-normalised cycle (cf. hrrr_transfer.file_url)."""
    from slr_calibration.hrrr_transfer import S3_BASE

    stamp = cycle.strftime("%Y%m%d")
    suffix = ".ak" if domain == "alaska" else ""
    return f"{S3_BASE}/hrrr.{stamp}/{domain}/hrrr.t{cycle:%H}z.wrfprsf{fxx:02d}{suffix}.grib2"


def _points_path_utc(
    cache_dir: Path, domain: str, cycle: datetime, fxx: int, reduced: bool
) -> Path:
    """Cache path for a UTC-normalised cycle (same layout as step 5's)."""
    stamp = cycle.strftime("%Y%m%d")
    name = f"t{cycle:%H}z_f{fxx:02d}" + ("_acc" if reduced else "") + ".json.gz"
    return cache_dir / domain / stamp / name


def plan_transfer_files_utc(
    events: list[dict[str, Any]],
) -> tuple[dict[tuple[str, datetime, int], set[str]], dict[tuple[str, datetime, int], set[str]]]:
    """plan_transfer_files with UTC-normalised cycles."""
    full: dict[tuple[str, datetime, int], set[str]] = defaultdict(set)
    acc_prev: dict[tuple[str, datetime, int], set[str]] = defaultdict(set)
    for event in events:
        domain = station_domain(event)
        start, end = event_window(event)
        for step in range(25):
            instant = start + timedelta(hours=step)
            cycle, fxx = cycle_for_instant_utc(instant, domain)
            full[(domain, cycle, fxx)].add(event["station_id"])
            if fxx > 1:
                acc_prev[(domain, cycle, fxx - 1)].add(event["station_id"])
    return dict(full), dict(acc_prev)


def extract_cycle_points_v3(
    fetcher: RangeFetcher | None,
    v3_cache_dir: Path,
    step5_cache_dir: Path,
    domain: str,
    cycle: datetime,
    fxx: int,
    grid_indices: dict[str, int],
    reduced: bool = False,
) -> tuple[dict[str, dict[str, float | None]] | None, str]:
    """Station point values (old + v3 fields) for one wrfprs file.

    UTC-normalised cycle/path/URL throughout; no step-5 seeding (the
    step-5 cache's wall-time naming cannot be trusted as a seed under
    the corrected convention).  Variables absent from a file's idx
    inventory (CLMR in 970 files of the first pass) degrade to None
    values, exactly as the serving adapter would see them.
    Statuses: ``cached``, ``downloaded``, ``missing``, ``failed``.
    """
    path = _points_path_utc(v3_cache_dir, domain, cycle, fxx, reduced)
    cached = _read_points_any(path)
    if cached is not None:
        return cached, "cached"
    if fetcher is None:
        return None, "missing"
    url = file_url_utc(domain, cycle, fxx)
    wanted = ACC_VARIABLES if reduced else V3_WANTED_VARIABLES
    try:
        messages = _fetch_messages(
            fetcher, url, wanted, fxx, verify_accumulations=True
        )
    except Exception:
        return None, "failed"
    points = _decode_points(messages, wanted, grid_indices)
    _write_v3_points(path, domain, cycle, fxx, reduced, url, points)
    return points, "downloaded"


# ---------------------------------------------------------------------------
# Fetch driver
# ---------------------------------------------------------------------------

def fetch_v3_files(
    events: list[dict[str, Any]],
    v3_cache_dir: Path,
    step5_cache_dir: Path,
    fetcher: RangeFetcher,
    max_workers: int = 10,
    progress_every: int = 250,
) -> dict[str, Any]:
    """Download/extend every planned file's wanted messages (resumable)."""
    full, acc_prev = plan_transfer_files_utc(events)
    stations = domain_stations(events)
    grids = {
        domain: load_or_build_domain_grid_indices(fetcher, v3_cache_dir, domain, rows)
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
        _points, status = extract_cycle_points_v3(
            fetcher, v3_cache_dir, step5_cache_dir, domain, cycle, fxx,
            grids[domain], reduced=reduced,
        )
        with lock:
            counts[status] += 1
            done += 1
            if status == "failed":
                failed.append(file_url_utc(domain, cycle, fxx) + (" (acc)" if reduced else ""))
            if done % progress_every == 0 or done == len(jobs):
                print(
                    f"[hrrr-transfer-v3] {done}/{len(jobs)} files "
                    f"(extended={counts['extended']}, downloaded={counts['downloaded']}, "
                    f"cached={counts['cached']}, seeded={counts['seeded']}, "
                    f"failed={counts['failed']})",
                    flush=True,
                )

    if jobs:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(work, job) for job in jobs]
            for future in as_completed(futures):
                future.result()
    stats = fetcher.stats() if hasattr(fetcher, "stats") else {}
    return {
        "profileSource": V3_SOURCE_ID,
        "eventsPlanned": len(events),
        "filesPlanned": len(jobs),
        "filesFull": len(full),
        "filesAccPrev": len(jobs) - len(full),
        "statusCounts": dict(counts),
        "failedUrls": failed[:100],
        "requests": stats.get("requests"),
        "bytesTransferred": stats.get("bytes_transferred"),
        "retries": stats.get("retries"),
    }


# ---------------------------------------------------------------------------
# Hour assembly + v3 feature building (cache-only)
# ---------------------------------------------------------------------------

def _complete_level_dict(
    row: dict[str, float | None], prefix: str
) -> dict[int, float] | None:
    """{level: value} when all 13 levels are present, else None.

    Mirrors the ERA5 chunk semantics the builders were written against:
    a w/q hour either exists whole or not at all (``_pl_at`` would raise
    on a None level when interpolating).
    """
    out: dict[int, float] = {}
    for level in PRESSURE_LEVELS_HPA:
        value = row.get(f"{prefix}_{level}")
        if value is None:
            return None
        out[level] = float(value)
    return out


def _tclw_for_row(
    row: dict[str, float | None], elevation_m: float
) -> float | None:
    """Column liquid water from one instant's point values.

    The serving adapter's construction (``model_serving.
    tclw_from_hydrometeors``): hydrostatic integral of CLMR + RWMR over
    the segments at/above the surface elevation, using HGT heights.
    """
    levels_desc = sorted(PRESSURE_LEVELS_HPA, reverse=True)
    liquid: list[float] = []
    heights: list[float] = []
    for level in levels_desc:
        clmr = row.get(f"clmr_{level}")
        rwmr = row.get(f"rwmr_{level}")
        hgt = row.get(f"hgt_{level}")
        if clmr is None or rwmr is None or hgt is None:
            return None
        liquid.append(float(clmr) + float(rwmr))
        heights.append(float(hgt))
    return tclw_from_hydrometeors(levels_desc, liquid, heights, elevation_m)


def assemble_event_series(
    event: dict[str, Any],
    load_points: Any,
) -> tuple[
    dict[datetime, dict[str, Any]],
    dict[datetime, dict[int, float]],
    dict[datetime, dict[int, float]],
    dict[datetime, dict[str, float | None]],
    dict[datetime, dict[str, float | None]],
    int,
]:
    """All builder input series for one event from cached points.

    ``load_points(domain, cycle, fxx, reduced)`` returns the points dict
    for one file (or None).  Returns (profile_hours, w_series,
    q_series, accumu_series, sfc_series, max_fxx).
    """
    domain = station_domain(event)
    station_id = event["station_id"]
    elevation_m = float(event["elevation_m"])
    start, _end = event_window(event)
    profile_hours: dict[datetime, dict[str, Any]] = {}
    w_series: dict[datetime, dict[int, float]] = {}
    q_series: dict[datetime, dict[int, float]] = {}
    accumu_series: dict[datetime, dict[str, float | None]] = {}
    sfc_series: dict[datetime, dict[str, float | None]] = {}
    max_fxx = 0
    for step in range(25):
        instant = start + timedelta(hours=step)
        cycle, fxx = cycle_for_instant_utc(instant, domain)
        max_fxx = max(max_fxx, fxx)
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
            profile_hours[instant] = record
        if row is None:
            continue
        w_dict = _complete_level_dict(row, "w")
        if w_dict is not None:
            w_series[instant] = w_dict
        q_dict = _complete_level_dict(row, "q")
        if q_dict is not None:
            q_series[instant] = q_dict
        prev_apcp = None if prev_row is None else prev_row.get("apcp_mm")
        accumu_series[instant] = {
            "tp_mm": hourly_accumulation(row.get("apcp_mm"), prev_apcp, fxx),
            "cp_mm": None,
            "lsp_mm": None,
        }
        sfc_series[instant] = {
            "dewpoint_2m": row.get("dpt2m_k"),
            "total_column_liquid_water": _tclw_for_row(row, elevation_m),
            "wind_u_10m": row.get("u10"),
            "wind_v_10m": row.get("v10"),
            "skin_temperature": row.get("skt_k"),
        }
    return profile_hours, w_series, q_series, accumu_series, sfc_series, max_fxx


def build_v3_transfer_features(
    events: list[dict[str, Any]],
    v3_cache_dir: Path,
    step5_cache_dir: Path,
    memo_maxsize: int = 150,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per-event HRRR 64-feature rows for every event, with accounting.

    The base build mirrors the v2 recovery exactly: whole-hour build
    first; a row still ``incomplete_profile`` whose valid_time is
    sub-hourly gets the offset build, kept only if it reaches ``ok``.
    """
    cache_memo: OrderedDict[
        tuple[str, datetime, int, bool], dict[str, Any] | None
    ] = OrderedDict()

    def load_points(domain: str, cycle: datetime, fxx: int, reduced: bool):
        key = (domain, cycle, fxx, reduced)
        if key in cache_memo:
            cache_memo.move_to_end(key)
            return cache_memo[key]
        points = _read_points_any(_points_path_utc(v3_cache_dir, domain, cycle, fxx, reduced))
        cache_memo[key] = points
        while len(cache_memo) > memo_maxsize:
            cache_memo.popitem(last=False)
        return points

    rows_by_id: dict[str, dict[str, Any]] = {}
    status_counts: Counter[str] = Counter()
    complete_counts: Counter[str] = Counter()
    region_status: dict[str, Counter[str]] = defaultdict(Counter)
    ordered = sorted(
        events,
        key=lambda e: (station_domain(e), e["valid_time"], str(e["event_id"])),
    )
    for index, event in enumerate(ordered, start=1):
        (profile_hours, w_series, q_series, accumu_series, sfc_series,
         max_fxx) = assemble_event_series(event, load_points)
        row = build_event_features(event, profile_hours, V3_SOURCE_ID)
        if row["profile_status"] == "incomplete_profile":
            valid = event["valid_time"]
            if valid.minute or valid.second or valid.microsecond:
                retry = build_event_features_offset(event, profile_hours, V3_SOURCE_ID)
                if retry["profile_status"] == "ok":
                    row = retry
        if not profile_hours:
            row["profile_status"] = "fetch_missing"
        derived, counts = build_event_v3_features(
            event, profile_hours, w_series, q_series, accumu_series, sfc_series
        )
        row.update(derived)
        row["hrrr_source_counts"] = counts
        row["hrrr_max_fxx"] = max_fxx
        row["lead_band"] = lead_band(event)
        complete = (
            row["profile_status"] in ("ok", "no_baseline")
            and counts["profile"] == 24
            and counts["w"] == 24
            and counts["q"] == 24
            and counts["accumu"] == 24
        )
        row["hrrr_complete"] = complete
        rows_by_id[str(event["event_id"])] = row
        status_counts[row["profile_status"]] += 1
        complete_counts["complete" if complete else "incomplete"] += 1
        region_status[event["region"]][
            "complete" if complete else f"incomplete:{row['profile_status']}"
        ] += 1
        if index % 2000 == 0 or index == len(ordered):
            print(f"[hrrr-transfer-v3] built {index}/{len(ordered)} events", flush=True)
    rows = [rows_by_id[str(event["event_id"])] for event in events]
    coverage = {
        "profileSource": V3_SOURCE_ID,
        "eventsTotal": len(events),
        "statusCounts": dict(status_counts),
        "completeCounts": dict(complete_counts),
        "byRegion": {region: dict(c) for region, c in sorted(region_status.items())},
    }
    return rows, coverage


# ---------------------------------------------------------------------------
# Paired metrics + the pre-registered decision rule (pure)
# ---------------------------------------------------------------------------

def paired_block(rows: list[dict[str, float]]) -> dict[str, Any]:
    """MAEs and the paired penalty for a set of paired rows.

    Each row: {observed, era5, hrrr}.  The penalty is the mean of the
    per-event absolute-error differences (identical to the difference
    of the two MAEs on the same set).
    """
    n = len(rows)
    if n == 0:
        return {"n": 0, "mae_era5": None, "mae_hrrr": None, "penalty": None}
    mae_era5 = sum(abs(r["era5"] - r["observed"]) for r in rows) / n
    mae_hrrr = sum(abs(r["hrrr"] - r["observed"]) for r in rows) / n
    return {"n": n, "mae_era5": mae_era5, "mae_hrrr": mae_hrrr,
            "penalty": mae_hrrr - mae_era5}


def decision_outcome(
    pooled_penalty: float,
    region_penalties: dict[str, float],
    band_penalties: dict[str, float],
) -> dict[str, Any]:
    """The DESIGN.md decision rule, applied mechanically."""
    worst_region = max(region_penalties.items(), key=lambda kv: kv[1])
    worst_band = max(band_penalties.items(), key=lambda kv: kv[1])
    if pooled_penalty >= POOLED_PENALTY_LIMIT:
        outcome = "correction_study_warranted"
        reasons = [
            f"pooled paired penalty {pooled_penalty:+.4f} >= {POOLED_PENALTY_LIMIT:.2f}"
        ]
    elif worst_region[1] >= CONCENTRATION_LIMIT or worst_band[1] >= CONCENTRATION_LIMIT:
        outcome = "accepted_with_concentration_flag"
        reasons = []
        if worst_region[1] >= CONCENTRATION_LIMIT:
            reasons.append(
                f"region {worst_region[0]} penalty {worst_region[1]:+.4f} "
                f">= {CONCENTRATION_LIMIT:.2f}"
            )
        if worst_band[1] >= CONCENTRATION_LIMIT:
            reasons.append(
                f"lead band {worst_band[0]} penalty {worst_band[1]:+.4f} "
                f">= {CONCENTRATION_LIMIT:.2f}"
            )
    else:
        outcome = "transfer_accepted"
        reasons = [
            f"pooled paired penalty {pooled_penalty:+.4f} < {POOLED_PENALTY_LIMIT:.2f}; "
            f"worst region {worst_region[0]} {worst_region[1]:+.4f} and worst lead "
            f"band {worst_band[0]} {worst_band[1]:+.4f} both < {CONCENTRATION_LIMIT:.2f}"
        ]
    return {
        "outcome": outcome,
        "reasons": reasons,
        "worst_region": {"region": worst_region[0], "penalty": worst_region[1]},
        "worst_lead_band": {"band": worst_band[0], "penalty": worst_band[1]},
    }
