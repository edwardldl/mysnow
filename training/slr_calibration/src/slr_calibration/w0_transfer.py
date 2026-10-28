"""W0 (weak-label research track): does the step-4 observation operator
transfer across the SNOTEL network?

Design: ``~/workspace/your_files/mysnow-weak-label-research-track-design``
(phase W0, corrected design).  Research-only: this module never writes
a production or retained artifact, never changes the retention gates,
and never touches the application runtime.

The gold pool contains no SNTEL stations (all 58,322 screened events
are GHCN CoCoRaHS/COOP), so the transfer instrument is proximity
pairing: gold events whose observer station sits near a SNOTEL station,
scored against that station's hourly SWE/depth series.  The CSSL board
and step 4's four probe sites remain the only true co-located anchors;
here pair distance and elevation difference are first-class covariates
and all results are stratified by them.

Discipline (the track's core rule): operator parameters are fitted
ONLY on hourly depth trajectories, never on gold labels.  Gold labels
are used exclusively for scoring.  Variant selection follows the
pre-declared set in ``VARIANT_PLAN``; the verdict follows the
pre-registered exit criterion in ``EXIT_CRITERION``.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Sequence

from .observation_operator import (
    DEPTH_FIT_SIGMA_MM,
    HourlySeries,
    OperatorParams,
    StormWindow,
    fetch_hourly_station,
    invert_window,
    load_hourly_series,
    segment_storms,
    simulate_window,
    slr_metrics,
    water_year_of,
)

REGIONS = (
    "rockies",
    "pacific-northwest",
    "sierra-nevada",
    "alaska",
    "northeast",
    "great-lakes",
)
WESTERN_REGIONS = ("rockies", "pacific-northwest", "sierra-nevada", "alaska")

PRIMARY_MAX_DIST_KM = 10.0
PRIMARY_MAX_DELEV_M = 150.0
SECONDARY_MAX_DIST_KM = 25.0
SECONDARY_MAX_DELEV_M = 300.0

FT_TO_M = 0.3048

# Step-4 final fit (observation_operator.json -> finalFit.params),
# fitted on CSSL WY2024+2025 storm-window trajectories only.
FROZEN_PARAMS = OperatorParams(
    gamma=1.2874,
    rho0_intercept=105.277,
    rho0_temp_slope=25.0,
    log_eta_new=28.0,
    log_eta_old=16.8014,
)

# Pre-declared variant set for stage 4 (declared before any stage-4
# scoring; gold labels never enter any fit):
#   V0  frozen step-4 params (stage 3; the control).
#   V1  per-region-cluster refits, judged leave-one-station-out within
#       the cluster: parameters fitted on the depth trajectories of the
#       cluster's other stations only.
#   V2  covariate-conditioned refit, judged leave-one-station-out over
#       all paired stations: gamma and log_eta_old are linear in
#       station elevation (km above a reference), the other three
#       parameters are shared; fitted on trajectories of the remaining
#       stations only.  V2 runs only if stage 3 shows error structure
#       vs covariates, per the rule in the study notes: stratified
#       recovered bias differing by > 1.0 SLR across elevation bands,
#       or a monotone bias trend across distance/delevation bands.
VARIANT_PLAN = ("V0-frozen", "V1-cluster-loso", "V2-covariate-loso")

# Pre-registered exit criterion (design doc, re-scoped 2026-10-28):
# the best variant must achieve, under leave-stations-out judging,
# recovered-label MAE at least 15% better than naive differencing AND
# absolute bias <= 1.0 SLR in at least 3 of the western regions that
# have paired data.
EXIT_MIN_REL_MAE_IMPROVEMENT = 0.15
EXIT_MAX_ABS_BIAS = 1.0
EXIT_MIN_REGIONS = 3


# ---------------------------------------------------------------------------
# Station + event loading
# ---------------------------------------------------------------------------

def load_sntl_stations(stations_json: Path) -> list[dict[str, Any]]:
    """All SNTL stations from the cached full AWDB station list.

    The cached payload is the raw AWDB stations response; elevation is
    in feet there.  Stations missing coordinates/elevation are dropped
    and counted (returned via the ``_dropped`` sentinel key on the
    list's first element is avoided -- callers use
    ``count_sntl_stations`` for the accounting).
    """
    payload = json.loads(stations_json.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for rec in payload:
        if str(rec.get("networkCode") or "") != "SNTL":
            continue
        try:
            lat = float(rec["latitude"])
            lon = float(rec["longitude"])
            elev_m = float(rec["elevation"]) * FT_TO_M
        except (KeyError, TypeError, ValueError):
            continue
        out.append({
            "triplet": str(rec.get("stationTriplet") or ""),
            "name": str(rec.get("name") or ""),
            "state": str(rec.get("stateCode") or ""),
            "latitude": lat,
            "longitude": lon,
            "elevation_m": elev_m,
            "data_tz_offset_hours": rec.get("dataTimeZone"),
            "begin_date": str(rec.get("beginDate") or "")[:10],
            "end_date": str(rec.get("endDate") or "")[:10],
        })
    return [s for s in out if s["triplet"]]


def count_sntl_stations(stations_json: Path) -> dict[str, int]:
    payload = json.loads(stations_json.read_text(encoding="utf-8"))
    total = sum(1 for r in payload if str(r.get("networkCode") or "") == "SNTL")
    usable = len(load_sntl_stations(stations_json))
    return {"sntl_total": total, "sntl_with_coords": usable, "sntl_dropped": total - usable}


def load_gold_events_all_years(screened_dir: Path) -> dict[str, dict[str, Any]]:
    """All scoreable gold events grouped by station, all water years.

    Generalises ``observation_operator.load_gold_events`` (which is
    restricted to WY2024-2026): an event is scoreable when its
    observation window is 24 h and its local-standard observation time
    parses, so the paired-station window is [end - 24 h, end].
    """
    stations: dict[str, dict[str, Any]] = {}
    for region in REGIONS:
        path = screened_dir / f"events_{region}.csv"
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                station_id = row.get("station_id") or ""
                if not station_id:
                    continue
                entry = stations.setdefault(station_id, {
                    "station_id": station_id,
                    "region": region,
                    "station_name": row.get("station_name") or "",
                    "state": row.get("state") or "",
                    "latitude": _to_float(row.get("latitude")),
                    "longitude": _to_float(row.get("longitude")),
                    "elevation_m": _to_float(row.get("elevation_m")),
                    "n_events_total": 0,
                    "events": [],
                })
                entry["n_events_total"] += 1
                try:
                    obs_date = date.fromisoformat(row["observation_date"][:10])
                    hours = float(row.get("observation_window_hours") or 24)
                    slr = float(row["observed_slr"])
                except (KeyError, TypeError, ValueError):
                    continue
                if abs(hours - 24.0) > 0.01:
                    continue
                time_text = (row.get("observation_time_local_standard") or "").strip()
                try:
                    hh, mm = (int(part) for part in time_text.split(":")[:2])
                except (TypeError, ValueError):
                    continue
                end = datetime(obs_date.year, obs_date.month, obs_date.day, hh, mm)
                wy = obs_date.year + 1 if obs_date.month >= 10 else obs_date.year
                entry["events"].append({
                    "date": obs_date.isoformat(),
                    "endLocal": end,
                    "observedSlr": slr,
                    "waterYear": wy,
                })
    return stations


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Stage 1: pair census
# ---------------------------------------------------------------------------

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180.0
    a = (0.5 - math.cos((lat2 - lat1) * p) / 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2)
    return 12742 * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def pair_band(distance_km: float, delev_m: float) -> str | None:
    if distance_km <= PRIMARY_MAX_DIST_KM and delev_m <= PRIMARY_MAX_DELEV_M:
        return "primary"
    if distance_km <= SECONDARY_MAX_DIST_KM and delev_m <= SECONDARY_MAX_DELEV_M:
        return "secondary"
    return None


def pair_census(
    gold_stations: dict[str, dict[str, Any]],
    sntl_stations: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Pair every gold station to its nearest in-band SNTL station.

    The paired station is the nearest SNTL station *within* the
    tightest band the gold station qualifies for (primary first, then
    secondary) -- not the absolute nearest station regardless of band,
    which would discard stations whose closest neighbour fails the
    elevation-difference limit while another in-band station exists.
    """
    pairs: list[dict[str, Any]] = []
    for gold in gold_stations.values():
        if gold["latitude"] is None or gold["longitude"] is None or gold["elevation_m"] is None:
            continue
        candidates: list[tuple[float, float, dict[str, Any]]] = []
        for sntl in sntl_stations:
            d = _haversine_km(gold["latitude"], gold["longitude"],
                              sntl["latitude"], sntl["longitude"])
            if d > SECONDARY_MAX_DIST_KM:
                continue
            delev = abs(gold["elevation_m"] - sntl["elevation_m"])
            candidates.append((d, delev, sntl))
        chosen: tuple[float, float, dict[str, Any]] | None = None
        band: str | None = None
        for want, max_d, max_de in (
            ("primary", PRIMARY_MAX_DIST_KM, PRIMARY_MAX_DELEV_M),
            ("secondary", SECONDARY_MAX_DIST_KM, SECONDARY_MAX_DELEV_M),
        ):
            in_band = [c for c in candidates if c[0] <= max_d and c[1] <= max_de]
            if in_band:
                chosen = min(in_band, key=lambda c: c[0])
                band = want
                break
        if chosen is None or band is None:
            continue
        distance_km, delev_m, sntl = chosen
        best = {"sntl": sntl, "distance_km": distance_km, "delev_m": delev_m}
        years = sorted({e["waterYear"] for e in gold["events"]})
        pairs.append({
            "gold_station": gold["station_id"],
            "gold_name": gold["station_name"],
            "region": gold["region"],
            "gold_state": gold["state"],
            "gold_elevation_m": round(gold["elevation_m"], 1),
            "sntl_triplet": sntl["triplet"],
            "sntl_name": sntl["name"],
            "sntl_state": sntl["state"],
            "sntl_elevation_m": round(sntl["elevation_m"], 1),
            "sntl_tz_offset_hours": sntl["data_tz_offset_hours"],
            "distance_km": round(best["distance_km"], 2),
            "delev_m": round(best["delev_m"], 1),
            "band": band,
            "n_events_total": gold["n_events_total"],
            "n_events_scoreable": len(gold["events"]),
            "water_years": years,
        })
    pairs.sort(key=lambda p: (p["band"] != "primary", p["distance_km"]))
    summary: dict[str, Any] = {}
    for band in ("primary", "secondary"):
        sub = [p for p in pairs if p["band"] == band]
        by_region: dict[str, dict[str, int]] = {}
        for p in sub:
            reg = by_region.setdefault(p["region"], {"stations": 0, "events": 0})
            reg["stations"] += 1
            reg["events"] += p["n_events_scoreable"]
        summary[band] = {
            "stations": len(sub),
            "events_scoreable": sum(p["n_events_scoreable"] for p in sub),
            "events_total": sum(p["n_events_total"] for p in sub),
            "by_region": by_region,
        }
    return {"pairs": pairs, "summary": summary}


# ---------------------------------------------------------------------------
# Stage 2: hourly pulls
# ---------------------------------------------------------------------------

def fetch_runs_for_events(events: Sequence[dict[str, Any]]) -> list[tuple[date, date]]:
    """Contiguous month runs to fetch: each event's month +/- 1 month.

    The +/- 1 month guarantees the event's own 24 h window, the
    segmentation pad, and storm context around the event are present
    while keeping the request count proportional to event density.
    """
    months: set[tuple[int, int]] = set()
    for event in events:
        end: datetime = event["endLocal"]
        for shift in (-1, 0, 1):
            year = end.year + (end.month - 1 + shift) // 12
            month = (end.month - 1 + shift) % 12 + 1
            months.add((year, month))
    if not months:
        return []
    ordered = sorted(months)
    runs: list[tuple[date, date]] = []
    run_start = run_end = ordered[0]
    for ym in ordered[1:]:
        prev_index = run_end[0] * 12 + run_end[1]
        this_index = ym[0] * 12 + ym[1]
        if this_index == prev_index + 1:
            run_end = ym
        else:
            runs.append((date(run_start[0], run_start[1], 1), _month_end(run_end)))
            run_start = run_end = ym
    runs.append((date(run_start[0], run_start[1], 1), _month_end(run_end)))
    return runs


def _month_end(ym: tuple[int, int]) -> date:
    year, month = ym
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def fetch_station_runs(
    triplet: str,
    runs: Sequence[tuple[date, date]],
    cache_dir: Path,
    *,
    sleep_seconds: float = 1.5,
    progress: Any = None,
) -> dict[str, Any]:
    totals = {"triplet": triplet, "fetched": 0, "cached": 0, "failed": 0, "runs": len(runs)}
    for begin, end in runs:
        acct = fetch_hourly_station(triplet, begin, end, cache_dir,
                                     sleep_seconds=sleep_seconds, progress=progress)
        totals["fetched"] += acct["fetched"]
        totals["cached"] += acct["cached"]
        totals["failed"] += acct["failed"]
    return totals


# ---------------------------------------------------------------------------
# Stage 3: scoring (frozen or any parameter set)
# ---------------------------------------------------------------------------

def score_station_events(
    series: HourlySeries,
    events: Sequence[dict[str, Any]],
    params: OperatorParams,
) -> dict[str, Any]:
    """Score naive vs operator-recovered SLR for one station's events.

    Mirrors ``observation_operator.evaluate_probe_station`` but keeps
    full accounting (why events were dropped) for the coverage report.
    """
    rows: list[dict[str, Any]] = []
    accounting = {
        "events": len(events),
        "window_missing": 0,
        "low_coverage": 0,
        "no_initial_depth": 0,
        "scored": 0,
        "naive_defined": 0,
        "recovered_defined": 0,
    }
    for event in events:
        end: datetime = event["endLocal"]
        idx = series.window_slice(end - timedelta(hours=24), end)
        if idx is None:
            accounting["window_missing"] += 1
            continue
        if series.coverage(idx) < 0.9:
            accounting["low_coverage"] += 1
            continue
        if series.depth_mm[idx[0]] is None:
            accounting["no_initial_depth"] += 1
            continue
        inv = invert_window(series, idx, params)
        row = {
            "date": event["date"],
            "waterYear": event["waterYear"],
            "observedSlr": round(float(event["observedSlr"]), 3),
            "naiveSlr": round(inv["naiveSlr"], 3) if inv["naiveSlr"] is not None else None,
            "recoveredSlr": round(inv["recoveredSlr"], 3) if inv["recoveredSlr"] is not None else None,
            "windowSweGainMm": inv.get("windowSweGainMm"),
            "deltaDepthMm": inv.get("deltaDepthMm"),
            "recoveredDepthMm": inv.get("recoveredDepthMm"),
            "recoveredSweMm": inv.get("recoveredSweMm"),
        }
        rows.append(row)
        accounting["scored"] += 1
        accounting["naive_defined"] += row["naiveSlr"] is not None
        accounting["recovered_defined"] += row["recoveredSlr"] is not None
    return {"rows": rows, "accounting": accounting}


def _metrics_from_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    naive_pairs = [(r["naiveSlr"], r["observedSlr"]) for r in rows if r["naiveSlr"] is not None]
    recovered_pairs = [(r["recoveredSlr"], r["observedSlr"]) for r in rows if r["recoveredSlr"] is not None]
    both = [r for r in rows if r["naiveSlr"] is not None and r["recoveredSlr"] is not None]
    return {
        "nRows": len(rows),
        "naive": slr_metrics(naive_pairs),
        "recovered": slr_metrics(recovered_pairs),
        "recoveredOnNaiveSubset": slr_metrics(
            [(r["recoveredSlr"], r["observedSlr"]) for r in both]),
        "naiveOnRecoveredSubset": slr_metrics(
            [(r["naiveSlr"], r["observedSlr"]) for r in both]),
    }


def _distance_band(distance_km: float) -> str:
    if distance_km <= 2.0:
        return "<=2 km"
    if distance_km <= 5.0:
        return "2-5 km"
    if distance_km <= 10.0:
        return "5-10 km"
    return "10-25 km"


def _delev_band(delev_m: float) -> str:
    if delev_m <= 25.0:
        return "<=25 m"
    if delev_m <= 75.0:
        return "25-75 m"
    if delev_m <= 150.0:
        return "75-150 m"
    return "150-300 m"


def _elev_band(elev_m: float) -> str:
    if elev_m < 1500.0:
        return "<1500 m"
    if elev_m < 2500.0:
        return "1500-2500 m"
    return ">=2500 m"


def stratified_metrics(
    station_results: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate per-station scored rows into stratified metric tables.

    ``station_results`` items carry pair metadata + scored rows.
    """

    def collect(key_fn: Any) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for st in station_results:
            key = key_fn(st)
            if key is None:
                continue
            groups.setdefault(key, []).extend(st["rows"])
        return {key: _metrics_from_rows(rows) for key, rows in sorted(groups.items())}

    all_rows = [row for st in station_results for row in st["rows"]]
    return {
        "overall": _metrics_from_rows(all_rows),
        "byRegion": collect(lambda st: st["pair"]["region"]),
        "byDistanceBand": collect(lambda st: _distance_band(st["pair"]["distance_km"])),
        "byDelevBand": collect(lambda st: _delev_band(st["pair"]["delev_m"])),
        "bySntlElevationBand": collect(lambda st: _elev_band(st["pair"]["sntl_elevation_m"])),
        "byStation": {
            st["pair"]["gold_station"]: _metrics_from_rows(st["rows"])
            for st in station_results
        },
    }


# ---------------------------------------------------------------------------
# Stage 4: pooled trajectory fitting + LOSO variants
# ---------------------------------------------------------------------------

def window_residuals_multi(
    station_series: Sequence[tuple[HourlySeries, Sequence[StormWindow]]],
    params_for: Any,
) -> list[float]:
    """Depth-trajectory residuals pooled across stations.

    ``params_for`` maps a series' triplet to the OperatorParams to use
    for that station (shared params = constant function).
    """
    residuals: list[float] = []
    for series, windows in station_series:
        params = params_for(series.triplet)
        if params is None:
            continue
        for window in windows:
            idx = list(range(window.start_idx, window.end_idx + 1))
            sim = simulate_window(series, idx, params)
            if sim is None:
                continue
            for pos, i in enumerate(idx):
                observed = series.depth_mm[i]
                if observed is not None:
                    residuals.append((sim.depth_sim_mm[pos] - observed) / DEPTH_FIT_SIGMA_MM)
    return residuals


def fit_pooled_operator(
    station_series: Sequence[tuple[HourlySeries, Sequence[StormWindow]]],
    *,
    starts: Sequence[OperatorParams] | None = None,
) -> dict[str, Any]:
    """Least-squares fit of one shared parameter set on pooled windows."""
    from scipy.optimize import least_squares

    if starts is None:
        starts = (
            FROZEN_PARAMS,
            OperatorParams(gamma=1.35, rho0_intercept=120.0, rho0_temp_slope=5.0,
                           log_eta_new=18.5, log_eta_old=19.0),
        )
    bounds = (
        [0.7, 40.0, -2.0, 8.0, 8.0],
        [1.8, 200.0, 25.0, 28.0, 28.0],
    )
    best: dict[str, Any] | None = None
    for start in starts:
        result = least_squares(
            lambda vec: window_residuals_multi(
                station_series, lambda _t: OperatorParams.from_vector(vec)),
            start.vector(), bounds=bounds,
            x_scale=[0.3, 50.0, 3.0, 3.0, 3.0], max_nfev=300,
        )
        if best is None or float(result.cost) < best["cost"]:
            residuals = window_residuals_multi(
                station_series,
                lambda _t: OperatorParams.from_vector(result.x))
            best = {
                "params": OperatorParams.from_vector(result.x),
                "cost": float(result.cost),
                "nResiduals": len(residuals),
                "depthRmseMm": float(
                    (sum(r * r for r in residuals) / len(residuals)) ** 0.5
                    * DEPTH_FIT_SIGMA_MM) if residuals else None,
                "success": bool(result.success),
            }
    assert best is not None
    return best


def segment_station_windows(series: HourlySeries) -> list[StormWindow]:
    return segment_storms(series)


# Fitting-efficiency cap: a station may contribute at most this many
# storm windows to any single fit (evenly strided, deterministic).
# Trajectory fitting only -- this never touches scoring.
MAX_FIT_WINDOWS_PER_STATION = 48


def _cap_windows(windows: Sequence[StormWindow]) -> list[StormWindow]:
    if len(windows) <= MAX_FIT_WINDOWS_PER_STATION:
        return list(windows)
    stride = len(windows) / MAX_FIT_WINDOWS_PER_STATION
    return [windows[int(i * stride)] for i in range(MAX_FIT_WINDOWS_PER_STATION)]


def loso_cluster_refits(
    stations: Sequence[dict[str, Any]],
    *,
    min_fit_windows: int = 5,
) -> dict[str, Any]:
    """V1: per-region-cluster refits judged leave-one-station-out.

    ``stations`` items: {"pair", "series", "windows", "events"}.
    Fitting uses depth trajectories of the cluster's other stations
    only; the held-out station's events are then scored with the
    cluster parameters.
    """
    by_region: dict[str, list[dict[str, Any]]] = {}
    for st in stations:
        by_region.setdefault(st["pair"]["region"], []).append(st)
    out: dict[str, Any] = {"stations": {}, "byRegion": {}}
    for region, members in sorted(by_region.items()):
        region_rows: list[dict[str, Any]] = []
        for held in members:
            others = [m for m in members if m is not held]
            fit_input = [(m["series"], _cap_windows(m["windows"])) for m in others]
            n_windows = sum(len(w) for _, w in fit_input)
            entry: dict[str, Any] = {
                "gold_station": held["pair"]["gold_station"],
                "sntl_triplet": held["pair"]["sntl_triplet"],
                "fitStations": [m["pair"]["gold_station"] for m in others],
                "fitWindows": n_windows,
            }
            if len(others) == 0 or n_windows < min_fit_windows:
                entry["status"] = "skipped: insufficient fit windows"
                out["stations"][held["pair"]["gold_station"]] = entry
                continue
            fit = fit_pooled_operator(fit_input)
            entry["params"] = _params_json(fit["params"])
            entry["fitDepthRmseMm"] = fit["depthRmseMm"]
            entry["status"] = "scored"
            scored = score_station_events(held["series"], held["events"], fit["params"])
            entry["rows"] = scored["rows"]
            entry["metrics"] = _metrics_from_rows(scored["rows"])
            region_rows.extend(scored["rows"])
            out["stations"][held["pair"]["gold_station"]] = entry
        out["byRegion"][region] = _metrics_from_rows(region_rows)
    return out


# Covariate-conditioned variant (V2): gamma and log_eta_old linear in
# station elevation in km relative to a reference; the other three
# parameters are shared.  Free vector:
# [gamma0, gamma_per_km, rho0_intercept, rho0_temp_slope, log_eta_new,
#  log_eta_old0, log_eta_old_per_km]
ELEV_REF_M = 2000.0
COVARIATE_BOUNDS = (
    [0.7, -1.0, 40.0, -2.0, 8.0, 8.0, -10.0],
    [1.8, 1.0, 200.0, 25.0, 28.0, 28.0, 10.0],
)


def covariate_params(vec: Sequence[float], elevation_m: float) -> OperatorParams:
    delev_km = (elevation_m - ELEV_REF_M) / 1000.0
    gamma = min(1.8, max(0.7, vec[0] + vec[1] * delev_km))
    log_eta_old = min(28.0, max(8.0, vec[5] + vec[6] * delev_km))
    return OperatorParams(
        gamma=gamma,
        rho0_intercept=vec[2],
        rho0_temp_slope=vec[3],
        log_eta_new=vec[4],
        log_eta_old=log_eta_old,
    )


def fit_covariate_operator(
    station_series: Sequence[tuple[HourlySeries, Sequence[StormWindow], float]],
    *,
    starts: Sequence[Sequence[float]] | None = None,
) -> dict[str, Any]:
    """Fit the covariate-conditioned parameterisation on trajectories.

    ``station_series`` items are (series, windows, sntl_elevation_m).
    """
    from scipy.optimize import least_squares

    frozen = FROZEN_PARAMS
    if starts is None:
        starts = (
            [frozen.gamma, 0.0, frozen.rho0_intercept, frozen.rho0_temp_slope,
             frozen.log_eta_new, frozen.log_eta_old, 0.0],
            [1.1, 0.0, 90.0, 3.0, 17.0, 17.5, 0.0],
        )

    def params_for(vec: Sequence[float]) -> Any:
        elev_by_triplet = {s.triplet: e for s, _, e in station_series}
        return lambda triplet: covariate_params(vec, elev_by_triplet[triplet])

    def residuals(vec: Sequence[float]) -> list[float]:
        sw = [(s, w) for s, w, _ in station_series]
        return window_residuals_multi(sw, params_for(vec))

    best: dict[str, Any] | None = None
    for start in starts:
        result = least_squares(residuals, list(start), bounds=COVARIATE_BOUNDS,
                               x_scale=[0.3, 0.3, 50.0, 3.0, 3.0, 3.0, 3.0],
                               max_nfev=300)
        if best is None or float(result.cost) < best["cost"]:
            res = residuals(result.x)
            best = {
                "vector": [float(v) for v in result.x],
                "cost": float(result.cost),
                "nResiduals": len(res),
                "depthRmseMm": float(
                    (sum(r * r for r in res) / len(res)) ** 0.5
                    * DEPTH_FIT_SIGMA_MM) if res else None,
                "success": bool(result.success),
            }
    assert best is not None
    return best


def loso_covariate_refits(
    stations: Sequence[dict[str, Any]],
    *,
    min_fit_windows: int = 10,
) -> dict[str, Any]:
    """V2: covariate-conditioned refits judged leave-one-station-out
    over all paired stations pooled (covariates carry the transfer)."""
    out: dict[str, Any] = {"stations": {}, "byRegion": {}}
    region_rows: dict[str, list[dict[str, Any]]] = {}
    for held in stations:
        others = [m for m in stations if m is not held]
        fit_input = [(m["series"], _cap_windows(m["windows"]), m["pair"]["sntl_elevation_m"])
                     for m in others]
        n_windows = sum(len(w) for _, w, _ in fit_input)
        entry: dict[str, Any] = {
            "gold_station": held["pair"]["gold_station"],
            "sntl_triplet": held["pair"]["sntl_triplet"],
            "region": held["pair"]["region"],
            "fitStations": [m["pair"]["gold_station"] for m in others],
            "fitWindows": n_windows,
        }
        if n_windows < min_fit_windows:
            entry["status"] = "skipped: insufficient fit windows"
            out["stations"][held["pair"]["gold_station"]] = entry
            continue
        fit = fit_covariate_operator(fit_input)
        params = covariate_params(fit["vector"], held["pair"]["sntl_elevation_m"])
        entry["vector"] = fit["vector"]
        entry["paramsForStation"] = _params_json(params)
        entry["fitDepthRmseMm"] = fit["depthRmseMm"]
        entry["status"] = "scored"
        scored = score_station_events(held["series"], held["events"], params)
        entry["rows"] = scored["rows"]
        entry["metrics"] = _metrics_from_rows(scored["rows"])
        region_rows.setdefault(held["pair"]["region"], []).extend(scored["rows"])
        out["stations"][held["pair"]["gold_station"]] = entry
    for region, rows in sorted(region_rows.items()):
        out["byRegion"][region] = _metrics_from_rows(rows)
    return out


def _params_json(params: OperatorParams) -> dict[str, float]:
    return {
        "gamma": round(params.gamma, 4),
        "rho0Intercept": round(params.rho0_intercept, 3),
        "rho0TempSlope": round(params.rho0_temp_slope, 4),
        "logEtaNew": round(params.log_eta_new, 4),
        "logEtaOld": round(params.log_eta_old, 4),
    }


# ---------------------------------------------------------------------------
# Stage 5: verdict vs the pre-registered exit criterion
# ---------------------------------------------------------------------------

def evaluate_exit_criterion(
    variant_by_region: dict[str, Any],
) -> dict[str, Any]:
    """Judge one variant's per-region metrics against the criterion.

    ``variant_by_region`` maps region -> _metrics_from_rows output.
    The like-for-like comparison is recovered-on-the-naive-subset vs
    naive on the same events (recovered SLR also exists for some
    events where naive differencing is undefined; that extra coverage
    is reported but is not part of the criterion).
    """
    per_region: dict[str, Any] = {}
    passing: list[str] = []
    present: list[str] = []
    for region in WESTERN_REGIONS:
        metrics = variant_by_region.get(region)
        if not metrics or metrics.get("recoveredOnNaiveSubset", {}).get("mae") is None:
            per_region[region] = {"status": "no paired data"}
            continue
        present.append(region)
        rec = metrics["recoveredOnNaiveSubset"]
        naive = metrics["naive"]
        rel = (naive["mae"] - rec["mae"]) / naive["mae"] if naive["mae"] else None
        bias_ok = rec["bias"] is not None and abs(rec["bias"]) <= EXIT_MAX_ABS_BIAS
        passed = bool(rel is not None and rel >= EXIT_MIN_REL_MAE_IMPROVEMENT and bias_ok)
        per_region[region] = {
            "status": "judged",
            "n": rec["n"],
            "naiveMae": naive["mae"],
            "recoveredMaeCommonSubset": rec["mae"],
            "relativeMaeImprovement": round(rel, 4) if rel is not None else None,
            "recoveredBias": rec["bias"],
            "biasWithinLimit": bias_ok,
            "passed": passed,
        }
        if passed:
            passing.append(region)
    return {
        "perRegion": per_region,
        "regionsWithPairedData": present,
        "regionsPassing": passing,
        "criterionMet": len(passing) >= EXIT_MIN_REGIONS,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _save(results: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")


def _load_station_series_and_events(
    pairs: Sequence[dict[str, Any]],
    gold_stations: dict[str, dict[str, Any]],
    cache_dir: Path,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pair in pairs:
        gold = gold_stations[pair["gold_station"]]
        series = load_hourly_series(cache_dir, pair["sntl_triplet"])
        out.append({
            "pair": pair,
            "series": series,
            "events": gold["events"],
        })
    return out


def run_w0_study(
    *,
    screened_dir: Path,
    sntl_stations_json: Path,
    cache_dir: Path,
    results_json_path: Path,
    report_md_path: Path,
    stage: str = "all",
    sleep_seconds: float = 1.5,
    progress: Any = None,
) -> dict[str, Any]:
    log = progress or (lambda _msg: None)
    results: dict[str, Any] = {"researchOnly": True, "track": "weak-label W0"}
    if results_json_path.exists():
        try:
            prior = json.loads(results_json_path.read_text(encoding="utf-8"))
            if isinstance(prior, dict):
                results.update(prior)
        except (OSError, json.JSONDecodeError):
            pass
    results["variantPlan"] = list(VARIANT_PLAN)
    results["frozenParams"] = _params_json(FROZEN_PARAMS)
    results["exitCriterion"] = {
        "minRelativeMaeImprovement": EXIT_MIN_REL_MAE_IMPROVEMENT,
        "maxAbsBias": EXIT_MAX_ABS_BIAS,
        "minRegions": EXIT_MIN_REGIONS,
        "regions": list(WESTERN_REGIONS),
        "judging": "leave-stations-out (V1/V2) / fitted on CSSL only (V0)",
        "comparison": "recovered-on-naive-subset vs naive, same events",
    }

    gold_stations = load_gold_events_all_years(screened_dir)
    sntl_stations = load_sntl_stations(sntl_stations_json)

    # ---- Stage 1: census (always refreshed; it is cheap) ----
    census = pair_census(gold_stations, sntl_stations)
    census["sntlNetwork"] = count_sntl_stations(sntl_stations_json)
    census["goldStationsWithEvents"] = len(gold_stations)
    results["stage1Census"] = census
    _save(results, results_json_path)
    log(f"stage1 census: primary {census['summary']['primary']}, "
        f"secondary {census['summary']['secondary']}")
    if stage == "census":
        return results

    pairs = census["pairs"]
    primary_pairs = [p for p in pairs if p["band"] == "primary"]
    all_pairs = list(pairs)

    # ---- Stage 2: hourly pulls ----
    if stage in ("fetch", "all"):
        accounting: list[dict[str, Any]] = []
        for pair in all_pairs:
            gold = gold_stations[pair["gold_station"]]
            runs = fetch_runs_for_events(gold["events"])
            acct = fetch_station_runs(pair["sntl_triplet"], runs, cache_dir,
                                      sleep_seconds=sleep_seconds, progress=progress)
            acct["gold_station"] = pair["gold_station"]
            acct["band"] = pair["band"]
            accounting.append(acct)
            log(f"stage2 fetch {pair['sntl_triplet']} ({pair['gold_station']}): {acct}")
        results["stage2Fetch"] = accounting
        _save(results, results_json_path)
        if stage == "fetch":
            return results

    # ---- Stage 3: frozen-operator scoring ----
    if stage in ("score", "refit", "all"):
        loaded = _load_station_series_and_events(all_pairs, gold_stations, cache_dir)
        station_results: list[dict[str, Any]] = []
        for item in loaded:
            scored = score_station_events(item["series"], item["events"], FROZEN_PARAMS)
            station_results.append({
                "pair": item["pair"],
                "rows": scored["rows"],
                "accounting": scored["accounting"],
                "seriesHours": len(item["series"].times),
                "seriesSpan": [
                    item["series"].times[0].isoformat() if item["series"].times else None,
                    item["series"].times[-1].isoformat() if item["series"].times else None,
                ],
            })
            log(f"stage3 {item['pair']['gold_station']}: {scored['accounting']}")
        results["stage3Frozen"] = {
            "stations": station_results,
            "stratified": stratified_metrics(station_results),
        }
        _save(results, results_json_path)
        if stage == "score":
            return results

    # ---- Stage 4: LOSO refit variants (primary stations only) ----
    if stage in ("refit", "all"):
        loaded_primary = _load_station_series_and_events(
            primary_pairs, gold_stations, cache_dir)
        for item in loaded_primary:
            item["windows"] = segment_station_windows(item["series"])
            log(f"stage4 windows {item['pair']['gold_station']} "
                f"({item['pair']['sntl_triplet']}): {len(item['windows'])}")
        stage4: dict[str, Any] = {
            "windowCounts": {
                item["pair"]["gold_station"]: len(item["windows"])
                for item in loaded_primary
            },
        }
        v1 = loso_cluster_refits(loaded_primary)
        stage4["v1ClusterLoso"] = v1
        _save(results, results_json_path)

        trigger = _v2_trigger(results["stage3Frozen"]["stratified"])
        stage4["v2Trigger"] = trigger
        if trigger["triggered"]:
            v2 = loso_covariate_refits(loaded_primary)
            stage4["v2CovariateLoso"] = v2
        _save(results, results_json_path)
        results["stage4Refits"] = stage4

    # ---- Stage 5: verdict ----
    if stage in ("refit", "all"):
        verdicts: dict[str, Any] = {}
        frozen_by_region = results["stage3Frozen"]["stratified"]["byRegion"]
        verdicts["V0-frozen"] = evaluate_exit_criterion(frozen_by_region)
        stage4 = results.get("stage4Refits", {})
        if "v1ClusterLoso" in stage4:
            verdicts["V1-cluster-loso"] = evaluate_exit_criterion(
                stage4["v1ClusterLoso"]["byRegion"])
        if "v2CovariateLoso" in stage4:
            verdicts["V2-covariate-loso"] = evaluate_exit_criterion(
                stage4["v2CovariateLoso"]["byRegion"])
        best_name = None
        best_key: tuple[int, float] | None = None
        for name, verdict in verdicts.items():
            n_pass = len(verdict["regionsPassing"])
            region_metrics = {
                "V0-frozen": frozen_by_region,
                "V1-cluster-loso": stage4.get("v1ClusterLoso", {}).get("byRegion", {}),
                "V2-covariate-loso": stage4.get("v2CovariateLoso", {}).get("byRegion", {}),
            }[name]
            maes = [m["recoveredOnNaiveSubset"]["mae"]
                    for m in region_metrics.values()
                    if m.get("recoveredOnNaiveSubset", {}).get("mae") is not None]
            pooled = mean(maes) if maes else float("inf")
            key = (n_pass, -pooled)
            if best_key is None or key > best_key:
                best_key = key
                best_name = name
        results["stage5Verdict"] = {
            "perVariant": verdicts,
            "bestVariant": best_name,
            "recommendation": (
                "PROCEED" if best_name and verdicts[best_name]["criterionMet"]
                else "STOP"),
        }
        _save(results, results_json_path)
        report_md_path.parent.mkdir(parents=True, exist_ok=True)
        report_md_path.write_text(render_w0_report(results), encoding="utf-8")
        log(f"stage5 verdict: {results['stage5Verdict']['recommendation']} "
            f"(best: {best_name})")
    return results


def _v2_trigger(stratified: dict[str, Any]) -> dict[str, Any]:
    """Pre-declared V2 trigger, evaluated on stage-3 stratified bias.

    Triggered when the recovered bias range across bands (n >= 20 per
    band) exceeds 1.0 SLR in any of the elevation / delev / distance
    stratifications.
    """
    evidence: dict[str, Any] = {}
    triggered = False
    for table_name in ("bySntlElevationBand", "byDelevBand", "byDistanceBand"):
        table = stratified.get(table_name, {})
        biases = {
            band: m["recovered"]["bias"]
            for band, m in table.items()
            if m["recovered"]["bias"] is not None and m["recovered"]["n"] >= 20
        }
        if len(biases) >= 2:
            spread = max(biases.values()) - min(biases.values())
            evidence[table_name] = {"biases": biases, "spread": round(spread, 3)}
            if spread > 1.0:
                triggered = True
    return {"triggered": triggered, "evidence": evidence,
            "rule": "bias spread > 1.0 SLR across bands (n>=20) in any of "
                    "elevation/delev/distance stratifications"}


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _metric_cells(metrics: dict[str, Any], key: str) -> str:
    block = metrics.get(key, {})
    return (f"{_fmt(block.get('mae'))} | {_fmt(block.get('bias'))} | "
            f"{block.get('n', 0)}")


def render_w0_report(results: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# W0: does the step-4 observation operator transfer across the SNOTEL network?")
    lines.append("")
    lines.append("Research-only. No artifact was retained, no gate was changed, and "
                 "nothing here modifies the production calibration configuration. "
                 "Operator parameters are fitted only on hourly depth trajectories; "
                 "gold labels are used exclusively for scoring.")
    lines.append("")
    census = results.get("stage1Census", {})
    summary = census.get("summary", {})
    lines.append("## Stage 1 — pair census (full AWDB network)")
    lines.append("")
    net = census.get("sntlNetwork", {})
    lines.append(f"Full AWDB station list: {net.get('sntl_total', '?')} SNTL stations, "
                 f"{net.get('sntl_with_coords', '?')} with usable coordinates/elevation "
                 f"({net.get('sntl_dropped', '?')} dropped). "
                 f"Gold stations with events: {census.get('goldStationsWithEvents', '?')}.")
    lines.append("")
    for band in ("primary", "secondary"):
        block = summary.get(band, {})
        lines.append(f"**{band.title()} band** "
                     f"(<= {PRIMARY_MAX_DIST_KM if band == 'primary' else SECONDARY_MAX_DIST_KM} km, "
                     f"<= {PRIMARY_MAX_DELEV_M if band == 'primary' else SECONDARY_MAX_DELEV_M} m): "
                     f"{block.get('stations', 0)} stations, "
                     f"{block.get('events_scoreable', 0)} scoreable events "
                     f"({block.get('events_total', 0)} total).")
        for region, counts in sorted(block.get("by_region", {}).items()):
            lines.append(f"- {region}: {counts['stations']} stations, {counts['events']} events")
        lines.append("")
    lines.append("| Gold station | SNTEL | Region | Dist (km) | dElev (m) | Band | Events (scoreable/total) | Water years |")
    lines.append("|---|---|---|---:|---:|---|---:|---|")
    for p in census.get("pairs", []):
        years = p["water_years"]
        year_span = f"{years[0]}-{years[-1]}" if years else "none"
        lines.append(
            f"| {p['gold_station']} | {p['sntl_triplet']} | {p['region']} | "
            f"{_fmt(p['distance_km'])} | {_fmt(p['delev_m'], 0)} | {p['band']} | "
            f"{p['n_events_scoreable']}/{p['n_events_total']} | {year_span} |")
    lines.append("")

    fetch = results.get("stage2Fetch")
    if fetch:
        lines.append("## Stage 2 — hourly pulls")
        lines.append("")
        lines.append("| SNTEL | Gold station | Band | Fetched | Cached | Failed |")
        lines.append("|---|---|---|---:|---:|---:|")
        for acct in fetch:
            lines.append(f"| {acct['triplet']} | {acct['gold_station']} | {acct['band']} | "
                         f"{acct['fetched']} | {acct['cached']} | {acct['failed']} |")
        lines.append("")
        lines.append("Fetch accounting above is the final (mop-up) pass; across all "
                     "passes, 1,446 new station-months were fetched for the 16 paired "
                     "SNTEL stations (1,548 station-months cached for them in total, "
                     "including 102 pre-cached by step 4; 1,616 month files in the "
                     "cache overall). Six station-months failed transiently on first "
                     "attempt (read timeouts, HTTP 500/502, one TLS handshake timeout) "
                     "and all six were recovered by the mop-up pass. The fetch ran as "
                     "three parallel polite clients (1.5 s spacing each) after AWDB "
                     "response latency proved to be the bottleneck.")
        lines.append("")

    audit = results.get("availabilityAudit")
    if audit:
        lines.append("### Hourly data availability audit (per paired SNTEL station)")
        lines.append("")
        lines.append("Months with at least one non-null hourly value, from the cached "
                     "AWDB payloads themselves. The operator and naive differencing "
                     "both require WTEQ (pillow SWE); stations without it cannot be "
                     "scored at all.")
        lines.append("")
        lines.append("| SNTEL | Months cached | Months w/ WTEQ | Months w/ SNWD | Empty months | Paired events | Scored |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for trip, a in sorted(audit.items()):
            lines.append(f"| {trip} | {a['monthsCached']} | {a['monthsWithValues']['WTEQ']} | "
                         f"{a['monthsWithValues']['SNWD']} | {a['monthsEmpty']} | "
                         f"{a['events']} | {a['scored']} |")
        lines.append("")
        lines.append("Three Alaska stations return no hourly WTEQ: 1332:AK:SNTL "
                     "(Elmendorf Field) returns empty hourly payloads in all 97 cached "
                     "months; 2044:AK:SNTL (Spring Creek) and 968:AK:SNTL report hourly "
                     "SNWD/TOBS but never WTEQ. Together they make 393 of the 877 Alaska "
                     "paired events unscorable. This is an AWDB data-availability limit, "
                     "documented here rather than silently shrinking the set.")
        lines.append("")

    frozen = results.get("stage3Frozen")
    if frozen:
        lines.append("## Stage 3 — frozen step-4 operator (no refitting)")
        lines.append("")
        lines.append("Coverage accounting per station (events -> scored):")
        lines.append("")
        lines.append("| Gold station | Events | Window missing | Low coverage | No initial depth | Scored |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for st in frozen["stations"]:
            a = st["accounting"]
            lines.append(f"| {st['pair']['gold_station']} | {a['events']} | "
                         f"{a['window_missing']} | {a['low_coverage']} | "
                         f"{a['no_initial_depth']} | {a['scored']} |")
        lines.append("")
        strat = frozen["stratified"]
        lines.append("Overall and stratified metrics. Columns: MAE | bias | n. "
                     "'Recovered (common)' is recovered MAE on the subset where "
                     "naive differencing is defined — the like-for-like comparison.")
        lines.append("")
        overall = strat["overall"]
        lines.append(f"- **Overall:** naive {_metric_cells(overall, 'naive')}; "
                     f"recovered {_metric_cells(overall, 'recovered')}; "
                     f"recovered (common) {_metric_cells(overall, 'recoveredOnNaiveSubset')}")
        lines.append("")
        for table_name, label in (
            ("byRegion", "Region"), ("byDistanceBand", "Distance band"),
            ("byDelevBand", "dElev band"), ("bySntlElevationBand", "SNTEL elevation band"),
        ):
            lines.append(f"**{label}:**")
            lines.append("")
            lines.append("| Group | Naive MAE | Naive bias | n | Recovered MAE | Recovered bias | n | Recovered (common) MAE | bias | n |")
            lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
            for group, m in strat[table_name].items():
                lines.append(
                    f"| {group} | {_fmt(m['naive']['mae'])} | {_fmt(m['naive']['bias'])} | "
                    f"{m['naive']['n']} | {_fmt(m['recovered']['mae'])} | "
                    f"{_fmt(m['recovered']['bias'])} | {m['recovered']['n']} | "
                    f"{_fmt(m['recoveredOnNaiveSubset']['mae'])} | "
                    f"{_fmt(m['recoveredOnNaiveSubset']['bias'])} | "
                    f"{m['recoveredOnNaiveSubset']['n']} |")
            lines.append("")

    stage4 = results.get("stage4Refits")
    if stage4:
        lines.append("## Stage 4 — refit variants, judged leave-stations-out")
        lines.append("")
        lines.append("Window counts available for trajectory fitting: "
                     + ", ".join(f"{k} {v}" for k, v in stage4.get("windowCounts", {}).items()))
        lines.append("")
        for key, label in (("v1ClusterLoso", "V1 per-region-cluster refits (LOSO)"),
                           ("v2CovariateLoso", "V2 covariate-conditioned refits (LOSO)")):
            variant = stage4.get(key)
            if not variant:
                if key == "v2CovariateLoso":
                    trigger = stage4.get("v2Trigger", {})
                    lines.append(f"**{label}:** not run — pre-declared trigger not met "
                                 f"({trigger.get('rule', '')}; evidence: "
                                 f"{json.dumps(trigger.get('evidence', {}))}).")
                    lines.append("")
                continue
            lines.append(f"**{label}**")
            lines.append("")
            lines.append("| Region | Naive MAE | n | Recovered MAE (common) | bias | n |")
            lines.append("|---|---:|---:|---:|---:|---:|")
            for region, m in sorted(variant["byRegion"].items()):
                lines.append(
                    f"| {region} | {_fmt(m['naive']['mae'])} | {m['naive']['n']} | "
                    f"{_fmt(m['recoveredOnNaiveSubset']['mae'])} | "
                    f"{_fmt(m['recoveredOnNaiveSubset']['bias'])} | "
                    f"{m['recoveredOnNaiveSubset']['n']} |")
            lines.append("")
            for station_id, entry in sorted(variant["stations"].items()):
                if entry.get("status") != "scored":
                    lines.append(f"- {station_id}: {entry.get('status')}")
                else:
                    m = entry.get("metrics", {})
                    rec = m.get("recoveredOnNaiveSubset", {})
                    lines.append(
                        f"- {station_id}: params {json.dumps(entry.get('params') or entry.get('paramsForStation'))}, "
                        f"fit depth RMSE {_fmt(entry.get('fitDepthRmseMm'), 1)} mm "
                        f"on {entry.get('fitWindows')} windows; scored: naive MAE "
                        f"{_fmt(m.get('naive', {}).get('mae'))}, recovered MAE (common) "
                        f"{_fmt(rec.get('mae'))}, bias {_fmt(rec.get('bias'))}, n {rec.get('n')}")
            lines.append("")

    verdict = results.get("stage5Verdict")
    if verdict:
        lines.append("## Stage 5 — verdict vs the pre-registered exit criterion")
        lines.append("")
        lines.append("Criterion: leave-stations-out recovered MAE >= 15% better than naive "
                     "AND |bias| <= 1.0 SLR in at least 3 western regions with paired data "
                     "(comparison on the common subset where naive is defined).")
        lines.append("")
        for name, block in verdict.get("perVariant", {}).items():
            lines.append(f"**{name}:** regions passing: "
                         f"{', '.join(block['regionsPassing']) or 'none'} "
                         f"(paired data: {', '.join(block['regionsWithPairedData']) or 'none'})")
            for region, detail in block["perRegion"].items():
                if detail.get("status") == "judged":
                    lines.append(
                        f"- {region}: naive MAE {_fmt(detail['naiveMae'])}, recovered "
                        f"{_fmt(detail['recoveredMaeCommonSubset'])}, improvement "
                        f"{_fmt(100.0 * detail['relativeMaeImprovement'], 1)}%, bias "
                        f"{_fmt(detail['recoveredBias'])}, n {detail['n']} — "
                        f"{'PASS' if detail['passed'] else 'fail'}")
                else:
                    lines.append(f"- {region}: no paired data")
            lines.append("")
        lines.append(f"**Best variant:** {verdict.get('bestVariant')}")
        lines.append("")
        lines.append(f"**Recommendation: {verdict.get('recommendation')}**")
        lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("**MAE transfers; levels do not.** The frozen CSSL operator improves on "
                 "naive differencing almost everywhere (relative MAE improvement of "
                 "45-78% in the Rockies, Sierra Nevada, and Alaska region aggregates), "
                 "but its recovered SLR is biased low at essentially every station "
                 "with data — regional bias -2.3 to -4.4, and -5.36 at the single "
                 "tightest pair (Silverthorne, 1.82 km from Summit Ranch). The level "
                 "error is therefore not a pair-distance artefact; it is the step-4 "
                 "finding (partial level transfer, probe bias about -2.30) reproduced "
                 "at network scale and amplified over the full 2003-2026 span.")
    lines.append("")
    lines.append("**Trajectory-only refitting at network scale is degenerate.** The "
                 "pre-declared refit variants (V1 cluster LOSO, V2 covariate LOSO) do "
                 "not fix the level; they make it worse. Pooled multi-station "
                 "trajectory fits run to the parameter bounds (gamma -> 1.8, fresh-"
                 "density intercept -> 165-200 kg/m3, temperature slope -> 25) that "
                 "fit depth trajectories while capping the recoverable SLR — recovered "
                 "bias -2.0 to -10.9 per station. The one asymmetric exception is "
                 "instructive: Tahoe-fitted parameters score Truckee almost sanely "
                 "(MAE 3.40, bias -2.01), while Truckee-fitted parameters collapse at "
                 "Tahoe (MAE 6.80, bias -6.73). This is step 4's identification "
                 "warning (parameters weakly identified by depth trajectories, "
                 "several at bounds even at CSSL) playing out across stations: the "
                 "operator's parameter set is not a transferable physical constant "
                 "set, and depth trajectories alone do not pin it down at new "
                 "stations.")
    lines.append("")
    lines.append("**Naive differencing at paired stations is a weak baseline.** At "
                 "non-co-located pairs, naive SLR error can be dominated by spatial "
                 "mismatch rather than by the measurement process the operator "
                 "corrects (e.g. USC00048760: naive MAE 22.1, bias +12.7). Relative "
                 "MAE gains over that baseline overstate operator skill; the "
                 "pre-registered bias gate is the binding test, and every variant "
                 "fails it in every judged region.")
    lines.append("")
    lines.append("**Coverage reality check.** Of 2,914 paired gold events, 1,809 "
                 "(62%) were scored. The losses are documented per station above: "
                 "393 Alaska events sit at stations with no hourly WTEQ in AWDB at "
                 "all; the rest are window/coverage/depth-availability gaps in the "
                 "hourly record, concentrated in the earlier water years. Alaska's "
                 "judged evidence therefore rests on the 967/1260/1070/1302 stations "
                 "only.")
    lines.append("")
    lines.append("**Conclusion.** Under the pre-registered criterion the track stops "
                 "at W0: the observation operator is a CSSL instrument, not a "
                 "network instrument. Its recovered labels must not be produced at "
                 "scale for training (W1) on the basis of trajectory-only fitting. "
                 "Any future round would need a level signal that trajectories "
                 "cannot supply — i.e. label-supervised calibration, which is a "
                 "different (W2-style) design and would need its own pre-registered "
                 "gates before any gold label is touched for fitting.")
    lines.append("")
    return "\n".join(lines)
