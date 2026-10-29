"""Pressure-level feature builder for the MySnow SLR programme (model-v2-pressure).

The controlled counterpart to the AGL builder in ``gold_profiles``:
same screened gold events, same labels, same 24-h window, but the
profile is represented in its native pressure coordinates instead of
AGL bands.  See ``mysnow-pressure-level-model-plan.md`` for the design
and the pre-registered adoption rule; this module is the builder only
-- it trains nothing.

FEATURE SETS
------------
P-core (46): for each of the 13 pressure levels, the window means of
temperature (``t_<p>``, deg C), relative humidity (``rh_<p>``, %) and
wind speed (``ws_<p>``, m/s), taken from the cached ERA5 profiles;
plus ``latitude``, ``longitude``, ``elevation_m``;
plus ``surface_pressure_hpa`` -- the window mean of the *fetched* ERA5
surface pressure (``gold_profiles_sp``), Pa converted to hPa;
plus ``month``, ``doy_sin``, ``doy_cos`` (day-of-year of the local
observation date on a fixed 365.25-day annual cycle).

P-mask (59): P-core plus one validity flag ``valid_<p>`` per level.

VALIDITY AND FILL RULES (exact)
-------------------------------
Window instants are the same 24 sample instants the AGL builder uses
(start+1h .. end), each resolved through ``record_at`` (linear time
interpolation between bracketing whole-hour profiles, as in the v2
offset recovery); surface pressure at an instant is resolved the same
way from the fetched hourly sp (``sp_at``).

* At one instant, level p is *above ground* iff ``p < sp`` at that
  instant (sp in hPa): the level's pressure is lower than the surface
  pressure, so the level sits above the surface.
* A level's window means are computed over its above-ground instants
  only (ERA5 values at below-ground levels are source extrapolations
  and are never averaged in).
* A level is *valid* (``valid_<p>`` = 1) iff it is above ground at a
  majority of the 24 window instants (>= 12).  Otherwise its features
  are filled from the nearest valid level -- nearest by index distance
  in the level list, ties preferring the lower-pressure (higher) level.
  In P-core the same fill is applied; the flags are simply not emitted.
* Validity comes from the fetched hourly sp only.  Station elevation
  is a model feature but plays no part in the validity decision.

An event is ``ok`` only when all 24 window instants resolve a profile
record and a surface pressure; otherwise its profile features and
flags are None and its status says which side failed
(``incomplete_pressure_profile`` / ``incomplete_sp`` / ``sp_missing``).

DETERMINISM: instants are processed in fixed order, sums are plain
float additions in that order, and outputs are rounded exactly like
the AGL builder (4 dp; doy sin/cos 6 dp).  Rebuilding the same event
from the same caches reproduces its row bit-for-bit.
"""

from __future__ import annotations

import gzip
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .gold_profiles import (
    ERA5_SPEC,
    PRESSURE_LEVELS_HPA,
    _payload_hours,
    event_window,
    record_at,
    write_features_parquet,
)
from .gold_profiles_sp import SP_CACHE_DIR_NAME, load_modelling_events, load_sp_hours

LEVELS_HPA: tuple[int, ...] = tuple(PRESSURE_LEVELS_HPA)
PROFILE_FEATURES: list[str] = (
    [f"t_{p}" for p in LEVELS_HPA]
    + [f"rh_{p}" for p in LEVELS_HPA]
    + [f"ws_{p}" for p in LEVELS_HPA]
)
P_CORE_FEATURES: list[str] = PROFILE_FEATURES + [
    "latitude",
    "longitude",
    "elevation_m",
    "surface_pressure_hpa",
    "month",
    "doy_sin",
    "doy_cos",
]
VALIDITY_FEATURES: list[str] = [f"valid_{p}" for p in LEVELS_HPA]
P_MASK_FEATURES: list[str] = P_CORE_FEATURES + VALIDITY_FEATURES

METADATA_COLUMNS: list[str] = [
    "event_id",
    "region",
    "station_id",
    "site_id",
    "station_name",
    "state",
    "valid_time",
    "observation_date",
    "water_year",
    "month",
    "observed_slr",
    "fresh_snow_mm",
    "fresh_swe_mm",
    "climate_regime",
    "source_tier",
    "baseline_slr",
    "profile_status",
    "valid_hours",
    "sp_valid_instants",
    "profile_source",
]

MAJORITY_MIN_INSTANTS = 12  # of the 24 window instants
ANNUAL_PERIOD_DAYS = 365.25


def sp_at(sp_hours: dict[datetime, float], instant: datetime) -> float | None:
    """Surface pressure (Pa) at an arbitrary instant.

    Mirrors ``gold_profiles.record_at``: the stored value on a whole
    hour, otherwise the linear interpolation of the bracketing whole
    hours; None when a needed hour is missing.
    """
    floor = instant.replace(minute=0, second=0, microsecond=0)
    if floor == instant:
        return sp_hours.get(instant)
    lo = sp_hours.get(floor)
    hi = sp_hours.get(floor + timedelta(hours=1))
    if lo is None or hi is None:
        return None
    frac = (instant - floor).total_seconds() / 3600.0
    return lo + (hi - lo) * frac


def _nearest_valid(index: int, valid: list[bool]) -> int | None:
    candidates = [k for k, is_valid in enumerate(valid) if is_valid]
    if not candidates:
        return None
    return min(candidates, key=lambda k: (abs(k - index), -k))


def build_event_pressure_features(
    event: dict[str, Any],
    hours: dict[datetime, dict[str, Any]],
    sp_hours: dict[datetime, float],
    baseline: float | None = None,
) -> dict[str, Any]:
    """One full row (metadata + P-mask feature superset) for one event."""
    start, _end = event_window(event)
    instants = [start + timedelta(hours=i) for i in range(1, 25)]
    records = [record_at(hours, instant) for instant in instants]
    sps_pa = [sp_at(sp_hours, instant) for instant in instants]
    valid_instants = sum(1 for record in records if record is not None)
    sp_instants = sum(1 for value in sps_pa if value is not None)

    observation = date.fromisoformat(event["observation_date"])
    doy = observation.timetuple().tm_yday
    angle = 2.0 * math.pi * (doy - 1) / ANNUAL_PERIOD_DAYS
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
        "profile_status": "ok",
        "valid_hours": valid_instants,
        "sp_valid_instants": sp_instants,
        "profile_source": ERA5_SPEC.source_id,
        "latitude": event["latitude"],
        "longitude": event["longitude"],
        "elevation_m": event["elevation_m"],
        "doy_sin": round(math.sin(angle), 6),
        "doy_cos": round(math.cos(angle), 6),
    }
    for column in PROFILE_FEATURES + ["surface_pressure_hpa"] + VALIDITY_FEATURES:
        row[column] = None

    if not sp_hours:
        row["profile_status"] = "sp_missing"
        return row
    if sp_instants < 24:
        row["profile_status"] = "incomplete_sp"
        return row
    if valid_instants < 24:
        row["profile_status"] = "incomplete_pressure_profile"
        return row

    sps_hpa = [value / 100.0 for value in sps_pa if value is not None]
    row["surface_pressure_hpa"] = round(sum(sps_hpa) / len(sps_hpa), 4)

    n_levels = len(LEVELS_HPA)
    above = [[False] * n_levels for _ in instants]
    for i, record in enumerate(records):
        if record is None:
            continue
        for j, (pressure, _t, _rh, _ws, _z) in enumerate(record["levels"]):
            above[i][j] = pressure < sps_hpa[i]
    above_counts = [sum(1 for i in range(len(instants)) if above[i][j]) for j in range(n_levels)]
    valid = [count >= MAJORITY_MIN_INSTANTS for count in above_counts]

    means: list[tuple[float, float, float] | None] = []
    for j in range(n_levels):
        if not valid[j]:
            means.append(None)
            continue
        columns: list[list[float]] = [[], [], []]
        complete = True
        for var_index in range(3):
            for i, record in enumerate(records):
                if record is None or not above[i][j]:
                    continue
                value = record["levels"][j][var_index + 1]
                if value is None:
                    complete = False
                    break
                columns[var_index].append(value)
            if not complete or not columns[var_index]:
                break
        if not complete:
            valid[j] = False
            means.append(None)
            continue
        means.append(tuple(sum(col) / len(col) for col in columns))  # type: ignore[arg-type]

    for j, level in enumerate(LEVELS_HPA):
        source = j if valid[j] else _nearest_valid(j, valid)
        if source is None or means[source] is None:
            row["profile_status"] = "no_valid_level"
            for column in PROFILE_FEATURES + VALIDITY_FEATURES:
                row[column] = None
            return row
        t_mean, rh_mean, ws_mean = means[source]  # type: ignore[misc]
        row[f"t_{level}"] = round(t_mean, 4)
        row[f"rh_{level}"] = round(rh_mean, 4)
        row[f"ws_{level}"] = round(ws_mean, 4)
        row[f"valid_{level}"] = 1 if valid[j] else 0
    return row


def _load_profile_hours(profile_cache_dir: Path, station_id: str) -> dict[datetime, dict[str, Any]]:
    hours: dict[datetime, dict[str, Any]] = {}
    station_dir = profile_cache_dir / station_id
    if not station_dir.exists():
        return hours
    for path in sorted(station_dir.glob("*.json.gz")):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            hours.update(_payload_hours(payload))
        except Exception:
            continue
    return hours


def build_pressure_features(
    events: list[dict[str, Any]],
    profile_cache_dir: Path,
    sp_cache_dir: Path,
    baselines: dict[str, float | None],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Build (core rows, mask rows, summary) for the given events."""
    events_by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        events_by_station[event["station_id"]].append(event)

    full_rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = defaultdict(int)
    for station_id in sorted(events_by_station):
        hours = _load_profile_hours(profile_cache_dir, station_id)
        sp_hours = load_sp_hours(sp_cache_dir, station_id)
        for event in sorted(events_by_station[station_id], key=lambda e: e["event_id"]):
            row = build_event_pressure_features(
                event, hours, sp_hours, baseline=baselines.get(event["event_id"])
            )
            status_counts[row["profile_status"]] += 1
            full_rows.append(row)
    full_rows.sort(key=lambda r: r["event_id"])

    core_rows = [
        {column: row[column] for column in METADATA_COLUMNS + P_CORE_FEATURES} for row in full_rows
    ]
    mask_rows = [
        {column: row[column] for column in METADATA_COLUMNS + P_MASK_FEATURES} for row in full_rows
    ]
    summary = {
        "eventsTotal": len(events),
        "statusCounts": dict(status_counts),
        "pCoreFeatures": P_CORE_FEATURES,
        "pMaskFeatures": P_MASK_FEATURES,
    }
    return core_rows, mask_rows, summary


def run_build_pressure_features(pool_dir: Path, model_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Build model-v2-pressure features for the v2 modelling pool.

    ``model_dir`` is the model-v1 directory holding ``raw_cache_era5``,
    ``raw_cache_era5_sp`` and ``era5-expanded-v2/features.parquet``
    (labels + baselines + the ok-pool definition).
    """
    import pyarrow.parquet as pq

    v2_path = model_dir / "era5-expanded-v2" / "features.parquet"
    table = pq.read_table(v2_path, columns=["event_id", "profile_status", "baseline_slr"])
    baselines = {
        event_id: baseline
        for event_id, status, baseline in zip(
            table.column("event_id").to_pylist(),
            table.column("profile_status").to_pylist(),
            table.column("baseline_slr").to_pylist(),
        )
        if status == "ok"
    }
    events = load_modelling_events(pool_dir, v2_path)
    core_rows, mask_rows, summary = build_pressure_features(
        events,
        model_dir / ERA5_SPEC.cache_dir_name,
        model_dir / SP_CACHE_DIR_NAME,
        baselines,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    write_features_parquet(core_rows, output_dir / "features_pressure_core.parquet")
    write_features_parquet(mask_rows, output_dir / "features_pressure_mask.parquet")
    (output_dir / "feature_columns.json").write_text(
        json.dumps(
            {
                "pCoreFeatures": P_CORE_FEATURES,
                "pMaskFeatures": P_MASK_FEATURES,
                "levelsHpa": list(LEVELS_HPA),
                "validityRule": (
                    "valid_<p> = 1 iff pressure p is below the fetched hourly surface "
                    "pressure (p < sp) at >= 12 of the 24 window instants; level means "
                    "are computed over above-ground instants only"
                ),
                "fillRule": (
                    "invalid levels are filled from the nearest valid level by index "
                    "distance in the level list, ties preferring the lower-pressure level"
                ),
                "surfacePressure": (
                    "window mean of fetched ERA5 sp (an.sfc 128_134_sp), Pa converted to hPa"
                ),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (output_dir / "build_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    summary["outputDir"] = str(output_dir)
    return summary
