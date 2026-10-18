from __future__ import annotations

import tempfile
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .ghcn import GhcnRockiesConfig, import_ghcn_rockies
from .ghcn_track import GHCN_DAILY_COLUMNS
from .tracking_common import finite, read_csv_rows, write_csv_rows, write_json
from .tracking_registry import load_registry

# Normalized event schema (header of
# data/slr-training-data/normalized/observations/ghcn_rockies_events.csv)
# plus the tracking label-quality tier.
EVENT_COLUMNS = [
    "event_id", "storm_group_id", "station_id", "site_id", "station_name", "state",
    "observation_date", "valid_time", "observation_time_local_standard",
    "observation_window_hours", "latitude", "longitude", "elevation_m",
    "fresh_snow_mm", "fresh_swe_mm", "observed_slr", "baseline_method",
    "climate_regime", "qc_passed", "qc_weight", "source", "source_license",
    "source_flag", "label_quality",
]

INCH_TO_MM = 25.4
INCH_TO_CM = 2.54

# Weak-label QC gates for SNOTEL total-pack differencing.  SNOTEL measures the
# whole snowpack, not fresh snow on a board, so a differenced day is at best a
# weak proxy for fresh-snow SLR and always carries a low qc_weight.
WEAK_MIN_SWE_GAIN_MM = 5.0
WEAK_MIN_DEPTH_GAIN_CM = 2.0
WEAK_MAX_TAVG_C = 0.5
WEAK_MAX_TMAX_C = 1.5
WEAK_PRECIP_EXCESS_MM = 2.0
WEAK_QC_WEIGHT = 0.25
WEAK_SOURCE = "SNOTEL daily total-pack differencing (weak label; NOT a snow-board fresh-snow measurement)"


def _f_to_c(value_f: float) -> float:
    return (value_f - 32.0) * 5.0 / 9.0


def build_direct_events(
    ghcn_daily_path: Path,
    registry: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Direct GHCN SNOW+WESF events via the generalized existing importer.

    Rows are grouped per resort so each resort's stations are imported with
    that resort's timezone and climate regime, with the Rockies domain mask
    disabled (stations were pre-selected by the tracking registry).
    """
    rows = read_csv_rows(ghcn_daily_path)
    by_resort: dict[str, list[dict[str, str]]] = {}
    station_to_resort: dict[str, dict[str, Any]] = {}
    for resort in registry.get("resorts", []):
        for station in resort.get("ghcn_stations", []):
            station_to_resort[str(station["station_id"])] = resort
    for row in rows:
        resort = station_to_resort.get(str(row.get("station_id") or ""))
        if resort is not None:
            by_resort.setdefault(str(resort["resort_slug"]), []).append(row)

    events: list[dict[str, Any]] = []
    report: dict[str, Any] = {"inputRowCount": len(rows), "resorts": {}}
    for slug, resort_rows in sorted(by_resort.items()):
        resort = station_to_resort[str(resort_rows[0]["station_id"])]
        config = GhcnRockiesConfig(
            min_elevation_m=0.0,
            apply_domain_mask=False,
            timezone_name=str(resort["timezone"]),
            climate_regime=str(resort["climate_regime"]),
            storm_group_prefix=f"tracking-{slug}",
        )
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "ghcn_daily.csv"
            write_csv_rows(staging, resort_rows, GHCN_DAILY_COLUMNS)
            accepted, rejected, import_report = import_ghcn_rockies(staging, config)
        for event in accepted:
            events.append({**event, "label_quality": "direct"})
        report["resorts"][slug] = {
            "inputRowCount": import_report["inputRowCount"],
            "acceptedEventCount": import_report["acceptedEventCount"],
            "rejectedRowCount": import_report["rejectedRowCount"],
            "rejectionCounts": import_report["rejectionCounts"],
            "rejectedReasons": [item.reason for item in rejected],
        }
    report["acceptedEventCount"] = len(events)
    return events, report


def evaluate_weak_candidate(
    previous: dict[str, str],
    current: dict[str, str],
    resort: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Apply weak-label QC to one SNOTEL day-over-day difference.

    Returns (event, None) on acceptance or (None, rejection_reason).
    """
    prev_date_text = str(previous.get("date") or "")
    curr_date_text = str(current.get("date") or "")
    try:
        prev_date = date.fromisoformat(prev_date_text)
        curr_date = date.fromisoformat(curr_date_text)
    except ValueError:
        return None, "INVALID_DATE"
    if (curr_date - prev_date).days != 1:
        return None, "NON_CONSECUTIVE_DAYS"

    prev_swe = finite(previous.get("swe_inches"))
    curr_swe = finite(current.get("swe_inches"))
    prev_depth = finite(previous.get("snow_depth_inches"))
    curr_depth = finite(current.get("snow_depth_inches"))
    tavg_f = finite(current.get("tavg_f"))
    tmax_f = finite(current.get("tmax_f"))
    prev_precip = finite(previous.get("precip_accum_inches"))
    curr_precip = finite(current.get("precip_accum_inches"))
    if prev_swe is None or curr_swe is None or prev_depth is None or curr_depth is None or tavg_f is None:
        return None, "MISSING_MEASUREMENT"

    delta_swe_mm = (curr_swe - prev_swe) * INCH_TO_MM
    delta_depth_cm = (curr_depth - prev_depth) * INCH_TO_CM
    if delta_depth_cm < 0:
        return None, "DEPTH_DECREASED_MELT_OR_SETTLEMENT"
    if delta_swe_mm < WEAK_MIN_SWE_GAIN_MM:
        return None, "INSUFFICIENT_SWE_GAIN"
    if delta_depth_cm < WEAK_MIN_DEPTH_GAIN_CM:
        return None, "INSUFFICIENT_DEPTH_GAIN"
    if _f_to_c(tavg_f) > WEAK_MAX_TAVG_C:
        return None, "WARM_DAY"
    # Rain-phase indicators: a warm daily max, or gauge precipitation well in
    # excess of the SWE gain (liquid input the pack retained only in part).
    if tmax_f is not None and _f_to_c(tmax_f) > WEAK_MAX_TMAX_C:
        return None, "RAIN_PHASE_SUSPECTED_TMAX"
    if prev_precip is not None and curr_precip is not None:
        delta_precip_mm = (curr_precip - prev_precip) * INCH_TO_MM
        if delta_precip_mm > delta_swe_mm + WEAK_PRECIP_EXCESS_MM:
            return None, "RAIN_PHASE_SUSPECTED_PRECIP_EXCESS"

    observed_slr = (delta_depth_cm * 10.0) / delta_swe_mm
    if not (2.0 < observed_slr < 50.0):
        return None, "SLR_OUT_OF_RANGE"

    station = resort["snotel_station"]
    timezone = ZoneInfo(str(resort["timezone"]))
    valid_local = datetime.combine(curr_date + timedelta(days=1), time(0, 0), tzinfo=timezone)
    event = {
        "event_id": f"snotel-diff:{station['station_triplet']}:{curr_date.isoformat()}",
        "storm_group_id": f"tracking-{resort['resort_slug']}-{curr_date.isoformat()}",
        "station_id": station["station_triplet"],
        "site_id": station["station_triplet"],
        "station_name": station["station_name"],
        "state": station["state"],
        "observation_date": curr_date.isoformat(),
        "valid_time": valid_local.isoformat(),
        "observation_time_local_standard": "2400",
        "observation_window_hours": 24,
        "latitude": station["latitude"],
        "longitude": station["longitude"],
        "elevation_m": station["elevation_m"],
        "fresh_snow_mm": round(delta_depth_cm * 10.0, 3),
        "fresh_swe_mm": round(delta_swe_mm, 3),
        "observed_slr": round(observed_slr, 6),
        "baseline_method": "kuchera",
        "climate_regime": resort["climate_regime"],
        "qc_passed": True,
        "qc_weight": WEAK_QC_WEIGHT,
        "source": WEAK_SOURCE,
        "source_license": "USDA/NRCS AWDB public data",
        "source_flag": "weak_snotel_diff",
        "label_quality": "weak_snotel_diff",
    }
    return event, None


def build_weak_events(
    snotel_daily_path: Path,
    registry: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = read_csv_rows(snotel_daily_path)
    resorts_by_triplet: dict[str, dict[str, Any]] = {}
    for resort in registry.get("resorts", []):
        station = resort.get("snotel_station")
        if station:
            resorts_by_triplet[str(station["station_triplet"])] = resort

    by_station: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_station.setdefault(str(row.get("station_triplet") or ""), []).append(row)

    events: list[dict[str, Any]] = []
    rejection_counts: Counter[str] = Counter()
    candidates = 0
    for triplet, station_rows in sorted(by_station.items()):
        resort = resorts_by_triplet.get(triplet)
        if resort is None:
            continue
        ordered = sorted(station_rows, key=lambda row: str(row.get("date") or ""))
        for previous, current in zip(ordered, ordered[1:]):
            candidates += 1
            event, reason = evaluate_weak_candidate(previous, current, resort)
            if event is not None:
                events.append(event)
            else:
                rejection_counts[str(reason)] += 1
    report = {
        "inputRowCount": len(rows),
        "candidateCount": candidates,
        "acceptedEventCount": len(events),
        "rejectedCandidateCount": sum(rejection_counts.values()),
        "rejectionCounts": dict(sorted(rejection_counts.items())),
        "qcWeight": WEAK_QC_WEIGHT,
        "gates": {
            "minSweGainMm": WEAK_MIN_SWE_GAIN_MM,
            "minDepthGainCm": WEAK_MIN_DEPTH_GAIN_CM,
            "maxTavgC": WEAK_MAX_TAVG_C,
            "maxTmaxC": WEAK_MAX_TMAX_C,
            "precipExcessMm": WEAK_PRECIP_EXCESS_MM,
        },
    }
    return events, report


def build_events(
    ghcn_daily_path: Path,
    snotel_daily_path: Path,
    registry_path: Path,
    events_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    registry = load_registry(registry_path)
    direct_events, direct_report = build_direct_events(ghcn_daily_path, registry)
    weak_events, weak_report = build_weak_events(snotel_daily_path, registry)

    combined: dict[str, dict[str, Any]] = {}
    for event in [*direct_events, *weak_events]:
        normalized = dict(event)
        if isinstance(normalized.get("qc_passed"), bool):
            normalized["qc_passed"] = "true" if normalized["qc_passed"] else "false"
        combined[str(normalized["event_id"])] = normalized
    events = sorted(combined.values(), key=lambda row: (str(row["observation_date"]), str(row["station_id"])))
    # Storm-group ids from the direct importer are per-resort contiguous runs;
    # weak events keep their single-day groups.  Both are leakage-safe keys.
    write_csv_rows(events_path, events, EVENT_COLUMNS)
    report = {
        "eventsPath": str(events_path),
        "eventCount": len(events),
        "direct": direct_report,
        "weakSnotelDiff": weak_report,
        "labelQualityCounts": dict(sorted(Counter(str(event["label_quality"]) for event in events).items())),
    }
    write_json(report_path, report)
    return report
