"""Phase 3 physics feature builder (model-v3).

Joins derived physics features onto the champion's AGL build
(``era5-expanded-v2/features.parquet``: same 58,322 rows, same labels,
same splits; the 57,376 ``ok`` modelling rows get derived features).
The base feature set is the champion's 27 AGL features, carried through
untouched from the v2 parquet; this module only ADDS columns.

RAW SOURCES (all forecast-available; no observed SWE/liquid anywhere --
observed liquid is the label's denominator and would leak):

* profile cache (``raw_cache_era5``): t / rh / ws / z on the 13 ERA5
  pressure levels + temperature_2m + precipitation (tp fallback);
* ``model-v3-features/pl_chunks``: w (Pa/s) on the 13 levels;
* ``model-v3-features/pl_chunks_q``: q (kg/kg) on the 13 levels;
* ``model-v3-features/accumu_chunks``: hourly lsp / cp / tp (= lsp+cp);
* ``model-v3-features/sfc_chunks``: 2 m dewpoint, tclw, 10 m u/v, skin
  temperature (bundle ``surface`` only; an event missing sfc hours
  keeps the rest of its features and gets None for that bundle).

METHOD (mirrors the pressure builder's conventions): the 24 window
sample instants are start+1h .. end; every source is resolved at an
instant by linear time interpolation between bracketing whole-hour
records (the ``record_at`` convention).  Per hour, profile-derived
quantities are computed from the above-ground levels only (level
geopotential height >= station elevation) with piecewise-linear
profiles in height; layer thicknesses / layer means are exact
integrals over the linear segments.  Per-hour scalars are then
aggregated over the window precipitation-weighted (weights = hourly
tp from the accumu chunks, falling back to the profile cache's
precipitation), except where a feature is defined as a window
max / min / count / fraction.  When the window's total tp is zero the
precipitation-weighted means fall back to plain hour means.

Every feature is definable from the coarse IFS open-data level subset
(1000, 925, 850, 700, 600, 500 hPa of the 13) via the same profile
interpolation, and the window aggregates degrade gracefully from
hourly to 3-hourly sampling (time interpolation between steps).

SATURATION FORMULATION (mandatory, one explicit choice):
``SATURATION_FORMULATION = "ifs_mixed_phase"`` -- saturation vapour
pressure over water above 0 C, over ice below -23 C, quadratic blend
between (the IFS mixed-phase formulation; see the FETCH_REPORT q
addendum: a water-only formula understates RH derived from q by up to
~12 points in deep-cold profiles).  All RH-from-q / ice-supersaturation
features in the ``humidity`` bundle use it.  Wet-bulb temperatures use
the Stull (2011) approximation.

DETERMINISM: fixed instant order, plain float sums in that order,
outputs rounded to 4 dp.  Rebuilding from the same caches reproduces
every row bit-for-bit.
"""

from __future__ import annotations

import gzip
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .gold_profiles import (
    ERA5_SPEC,
    FEATURE_COLUMNS,
    PRESSURE_LEVELS_HPA,
    event_window,
    record_at,
    write_features_parquet,
)
from .gold_profiles_sp import _parse_hour, load_modelling_events
from .gold_profiles_v3 import V3_DIR_NAME
from .gold_profiles_v3_sfc import SFC_FIELDS
from .pressure_features import _load_profile_hours

SATURATION_FORMULATION = "ifs_mixed_phase"
SATURATION_FORMULATION_DETAIL = (
    "saturation vapour pressure: over water above 0 C, over ice below "
    "-23 C, quadratic blend between (ice fraction = (T / -23)^2 for "
    "-23 < T < 0 C); Bolton saturation curves; vapour pressure from q "
    "as e = q * p / (0.622 + 0.378 * q)"
)

DGZ_T_LO, DGZ_T_HI, DGZ_T_OPTIMUM = -20.0, -10.0, -15.0
RIMING_T_LO, RIMING_T_HI = -10.0, 0.0
AGG_T_LO, AGG_T_HI = -5.0, 0.0
SAT_RH_MIN = 95.0
_BIG = 99999.0

BASE_FEATURES: list[str] = list(FEATURE_COLUMNS)

BUNDLES: dict[str, list[str]] = {
    "dgz": [
        "dgz_t_pwt", "dgz_dist_optimum_pwt", "dgz_depth_m_pwt",
        "dgz_precip_frac", "habit_frac_dendrite", "habit_frac_plate",
        "habit_frac_other",
    ],
    "amount": [
        "fcst_liquid_mm", "precip_rate_mean", "precip_rate_max",
        "precip_rate_p90", "precip_hours_gt_1mm", "precip_first_half_frac",
    ],
    "warm_layer": [
        "tmax_column_pwt", "tmax_warmnose_pwt", "freezing_level_m_agl_pwt",
        "warm_layer_depth_m_pwt", "sfc_wetbulb_c_pwt",
    ],
    "riming": ["riming_rh_pwt", "riming_omega_pwt", "riming_index_pwt"],
    "ascent": ["omega_dgz_pwt", "omega_dgz_min", "cobb_overlap", "cobb_overlap_m_pwt"],
    "wind": [
        "ws_dgz_pwt", "ws_max_profile", "ws_dgz_hours_gt_9ms",
        "cold_windy_interaction",
    ],
    "humidity": ["rhice_dgz", "rhice_dgz_max", "sat_layer_depth_m", "dgz_sat_frac"],
    "surface": [
        "sfc_wetbulb_d2m_c_pwt", "sfc_dewpoint_depression_c_pwt",
        "tclw_pwt", "tclw_max", "ws10m_pwt", "ws10m_max", "skt_c_pwt",
    ],
    "aggregation": ["agg_layer_depth_m", "fall_distance_below_dgz_m"],
    "subcloud": [
        "subcloud_dewp_depression", "subcloud_unsat_depth_m",
        "subcloud_warm_depth_m",
    ],
    "regime": ["convective_frac", "lsp_total_mm", "cp_total_mm"],
}

DERIVED_FEATURES: list[str] = [name for names in BUNDLES.values() for name in names]


# ---------------------------------------------------------------------------
# Thermodynamics (no pre-existing helpers in the codebase; built here)
# ---------------------------------------------------------------------------

def es_water_hpa(t_c: float) -> float:
    """Bolton saturation vapour pressure over water (hPa)."""
    return 6.112 * math.exp(17.67 * t_c / (t_c + 243.5))


def es_ice_hpa(t_c: float) -> float:
    """Bolton saturation vapour pressure over ice (hPa)."""
    return 6.112 * math.exp(21.875 * t_c / (t_c + 265.5))


def saturation_vapour_pressure_hpa(t_c: float) -> float:
    """IFS mixed-phase saturation vapour pressure (hPa).

    Water above 0 C, ice below -23 C, quadratic blend between.
    """
    if t_c >= 0.0:
        return es_water_hpa(t_c)
    if t_c <= -23.0:
        return es_ice_hpa(t_c)
    ice_fraction = (t_c / -23.0) ** 2
    return (1.0 - ice_fraction) * es_water_hpa(t_c) + ice_fraction * es_ice_hpa(t_c)


def rh_from_q(q_kgkg: float, pressure_hpa: float, t_c: float) -> float:
    """Relative humidity (%) from q, p, t under SATURATION_FORMULATION."""
    e_hpa = q_kgkg * pressure_hpa / (0.622 + 0.378 * q_kgkg)
    return 100.0 * e_hpa / saturation_vapour_pressure_hpa(t_c)


def wet_bulb_stull(t_c: float, rh_pct: float) -> float:
    """Wet-bulb temperature (C) via the Stull (2011) approximation."""
    rh = min(max(rh_pct, 0.0), 100.0)
    return (
        t_c * math.atan(0.151977 * math.sqrt(rh + 8.313659))
        + math.atan(t_c + rh)
        - math.atan(rh - 1.676331)
        + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh)
        - 4.686035
    )


def dewpoint_from_t_rh(t_c: float, rh_pct: float) -> float | None:
    """Dewpoint (C) from t / RH via Magnus inversion; None if rh <= 0."""
    if rh_pct <= 0.0:
        return None
    alpha = math.log(rh_pct / 100.0) + 17.67 * t_c / (243.5 + t_c)
    return 243.5 * alpha / (17.67 - alpha)


# ---------------------------------------------------------------------------
# Piecewise-linear profile machinery (points sorted by height ascending)
# ---------------------------------------------------------------------------

def _range_interval(v0: float | None, v1: float | None, lo: float, hi: float) -> tuple[float, float] | None:
    """s-interval of [0, 1] where the linear v(s) lies within [lo, hi]."""
    if v0 is None or v1 is None:
        return None
    if v0 == v1:
        return (0.0, 1.0) if lo <= v0 <= hi else None
    s_lo = (lo - v0) / (v1 - v0)
    s_hi = (hi - v0) / (v1 - v0)
    a, b = min(s_lo, s_hi), max(s_lo, s_hi)
    a, b = max(a, 0.0), min(b, 1.0)
    return (a, b) if b > a else None


def _layer_measure(
    points: list[dict[str, Any]],
    conditions: list[tuple[str, float, float]],
    variables: tuple[str, ...] = (),
) -> tuple[float, dict[str, float | None]]:
    """(thickness_m, {var: thickness-weighted mean}) over a profile layer.

    The layer is the set of heights where every condition variable lies
    in its [lo, hi] range; means are exact integrals of the linear
    segments over that set.
    """
    thickness = 0.0
    integrals = {var: 0.0 for var in variables}
    var_thickness = {var: 0.0 for var in variables}
    for p0, p1 in zip(points, points[1:]):
        dz = p1["z"] - p0["z"]
        if dz <= 0.0:
            continue
        interval: tuple[float, float] | None = (0.0, 1.0)
        for key, lo, hi in conditions:
            iv = _range_interval(p0.get(key), p1.get(key), lo, hi)
            if iv is None:
                interval = None
                break
            interval = (max(interval[0], iv[0]), min(interval[1], iv[1]))
            if interval[1] <= interval[0]:
                interval = None
                break
        if interval is None:
            continue
        a, b = interval
        thickness += (b - a) * dz
        for var in variables:
            v0, v1 = p0.get(var), p1.get(var)
            if v0 is None or v1 is None:
                continue
            va = v0 + (v1 - v0) * a
            vb = v0 + (v1 - v0) * b
            integrals[var] += dz * (b - a) * (va + vb) / 2.0
            var_thickness[var] += (b - a) * dz
    means = {
        var: (integrals[var] / var_thickness[var] if var_thickness[var] > 0.0 else None)
        for var in variables
    }
    return thickness, means


def _layer_bottom_agl(
    points: list[dict[str, Any]],
    conditions: list[tuple[str, float, float]],
    elevation_m: float,
) -> float | None:
    """Lowest AGL height (m) at which all conditions hold, or None."""
    best: float | None = None
    for p0, p1 in zip(points, points[1:]):
        dz = p1["z"] - p0["z"]
        if dz <= 0.0:
            continue
        interval: tuple[float, float] | None = (0.0, 1.0)
        for key, lo, hi in conditions:
            iv = _range_interval(p0.get(key), p1.get(key), lo, hi)
            if iv is None:
                interval = None
                break
            interval = (max(interval[0], iv[0]), min(interval[1], iv[1]))
            if interval[1] <= interval[0]:
                interval = None
                break
        if interval is None:
            continue
        z_here = p0["z"] + interval[0] * dz - elevation_m
        if best is None or z_here < best:
            best = z_here
    return best


def _surface_contiguous_depth(
    points: list[dict[str, Any]],
    key: str,
    lo: float,
    hi: float,
) -> float:
    """Depth (m) of the surface-based layer where key stays in [lo, hi]."""
    depth = 0.0
    for p0, p1 in zip(points, points[1:]):
        dz = p1["z"] - p0["z"]
        if dz <= 0.0:
            continue
        iv = _range_interval(p0.get(key), p1.get(key), lo, hi)
        if iv is None or iv[0] > 1e-9:
            break
        depth += iv[1] * dz
        if iv[1] < 1.0 - 1e-9:
            break
    return depth


def _freezing_level_agl(points: list[dict[str, Any]], elevation_m: float) -> float:
    """First 0 C crossing AGL (m) scanning up from the surface.

    No crossing: 0 when the whole column is subfreezing (freezing level
    at/below the surface), the profile-top AGL when it is all above 0 C.
    """
    for p0, p1 in zip(points, points[1:]):
        t0, t1 = p0["t"], p1["t"]
        if t0 == 0.0:
            return p0["z"] - elevation_m
        if (t0 < 0.0) != (t1 < 0.0):
            frac = (0.0 - t0) / (t1 - t0)
            return p0["z"] + frac * (p1["z"] - p0["z"]) - elevation_m
    return 0.0 if points[0]["t"] < 0.0 else points[-1]["z"] - elevation_m


def _closest_to(points: list[dict[str, Any]], key: str, target: float) -> tuple[float, float]:
    """(min |value - target|, value achieving it) over linear segments."""
    best_gap = math.inf
    best_value = points[0][key]
    for point in points:
        gap = abs(point[key] - target)
        if gap < best_gap:
            best_gap, best_value = gap, point[key]
    for p0, p1 in zip(points, points[1:]):
        v0, v1 = p0[key], p1[key]
        if min(v0, v1) <= target <= max(v0, v1):
            return 0.0, target
    return best_gap, best_value


# ---------------------------------------------------------------------------
# Per-hour profile features
# ---------------------------------------------------------------------------

def _hour_profile_features(
    record: dict[str, Any],
    w_by_level: dict[int, float] | None,
    q_by_level: dict[int, float] | None,
    elevation_m: float,
) -> dict[str, Any] | None:
    """Per-hour scalars from one resolved profile record; None if unusable."""
    points: list[dict[str, Any]] = []
    for pressure, t, rh, ws, z in record["levels"]:
        if z is None or t is None or z < elevation_m:
            continue
        point: dict[str, Any] = {
            "z": float(z), "t": float(t), "rh": rh, "ws": ws,
            "p": float(pressure),
            "w": w_by_level.get(int(pressure)) if w_by_level else None,
            "q": q_by_level.get(int(pressure)) if q_by_level else None,
        }
        point["rhq"] = (
            rh_from_q(point["q"], point["p"], point["t"])
            if point["q"] is not None
            else None
        )
        points.append(point)
    points.sort(key=lambda pt: pt["z"])
    if len(points) < 3:
        return None

    dgz_cond = [("t", DGZ_T_LO, DGZ_T_HI)]
    dgz_depth, dgz_means = _layer_measure(points, dgz_cond, ("t", "w", "ws", "rhq"))
    gap, t_closest = _closest_to(points, "t", DGZ_T_OPTIMUM)
    dgz_t = dgz_means["t"] if dgz_depth > 0.0 and dgz_means["t"] is not None else t_closest
    dgz_bottom = _layer_bottom_agl(points, dgz_cond, elevation_m)
    if dgz_bottom is None:
        # No DGZ: the fall distance is measured to the level closest to
        # the DGZ optimum temperature (where the DGZ would sit).
        z_closest = min(points, key=lambda pt: abs(pt["t"] - DGZ_T_OPTIMUM))["z"]
        dgz_bottom = z_closest - elevation_m

    riming_depth, riming_means = _layer_measure(
        points, [("t", RIMING_T_LO, RIMING_T_HI)], ("rh", "w")
    )
    riming_index = None
    if riming_means["rh"] is not None and riming_means["w"] is not None:
        riming_index = riming_means["rh"] / 100.0 * max(0.0, -riming_means["w"])

    warm_depth, _ = _layer_measure(points, [("t", 0.0, _BIG)])
    has_q = any(pt["rhq"] is not None for pt in points)
    has_w = any(pt["w"] is not None for pt in points)
    sat_depth, _ = _layer_measure(points, [("rhq", SAT_RH_MIN, _BIG)])
    dgz_sat_depth, _ = _layer_measure(
        points, dgz_cond + [("rhq", SAT_RH_MIN, _BIG)]
    )
    cobb_depth, _ = _layer_measure(points, dgz_cond + [("w", -_BIG, 0.0)])
    agg_depth, _ = _layer_measure(points, [("t", AGG_T_LO, AGG_T_HI)])

    aloft = [pt for pt in points if pt["z"] - elevation_m >= 300.0] or points
    rhq_in_dgz = [
        pt["rhq"] for pt in points
        if pt["rhq"] is not None and DGZ_T_LO <= pt["t"] <= DGZ_T_HI
    ]
    surface_point = points[0]
    dewpoint = (
        dewpoint_from_t_rh(surface_point["t"], surface_point["rh"])
        if surface_point["rh"] is not None
        else None
    )
    ws_values = [pt["ws"] for pt in points if pt["ws"] is not None]
    cold_windy = None
    if dgz_means["ws"] is not None and dgz_t is not None:
        cold_windy = dgz_means["ws"] * max(0.0, DGZ_T_OPTIMUM - dgz_t) / 5.0

    return {
        "dgz_t": dgz_t,
        "dgz_dist_optimum": abs(dgz_t - DGZ_T_OPTIMUM) if dgz_t is not None else gap,
        "dgz_depth_m": dgz_depth,
        "dgz_w": dgz_means["w"],
        "dgz_ws": dgz_means["ws"],
        "dgz_rhq_mean": dgz_means["rhq"],
        "dgz_rhq_max": max(rhq_in_dgz) if rhq_in_dgz else None,
        "dgz_sat_frac": (
            (dgz_sat_depth / dgz_depth) if dgz_depth > 0.0 else None
        ) if has_q else None,
        "cobb_overlap_frac": (
            (cobb_depth / dgz_depth) if dgz_depth > 0.0 else None
        ) if has_w else None,
        "cobb_overlap_m": cobb_depth if has_w else None,
        "riming_rh": riming_means["rh"],
        "riming_omega": riming_means["w"],
        "riming_index": riming_index,
        "tmax_column": max(pt["t"] for pt in points),
        "tmax_warmnose": max(pt["t"] for pt in aloft),
        "freezing_level_m_agl": _freezing_level_agl(points, elevation_m),
        "warm_layer_depth_m": warm_depth,
        "sfc_wetbulb_c": (
            wet_bulb_stull(surface_point["t"], surface_point["rh"])
            if surface_point["rh"] is not None
            else None
        ),
        "sat_layer_depth_m": sat_depth if has_q else None,
        "agg_layer_depth_m": agg_depth,
        "fall_distance_below_dgz_m": dgz_bottom,
        "subcloud_dewp_depression": (
            surface_point["t"] - dewpoint if dewpoint is not None else None
        ),
        "subcloud_unsat_depth_m": _surface_contiguous_depth(points, "rh", -_BIG, 90.0),
        "subcloud_warm_depth_m": _surface_contiguous_depth(points, "t", 0.0, _BIG),
        "ws_max": max(ws_values) if ws_values else None,
        "cold_windy": cold_windy,
    }


# ---------------------------------------------------------------------------
# Chunk-series loaders (assembled Phase 3 trees + profile cache)
# ---------------------------------------------------------------------------

def _load_series_chunks(
    chunks_dir: Path, station_id: str, fields: list[str]
) -> dict[datetime, dict[str, float | None]]:
    """{instant: {field: value}} from one station's assembled chunks."""
    hours: dict[datetime, dict[str, float | None]] = {}
    station_dir = chunks_dir / station_id
    if not station_dir.exists():
        return hours
    for path in sorted(station_dir.glob("*.json.gz")):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            continue
        hourly = payload["hourly"]
        for index, text in enumerate(hourly["time"]):
            hours[_parse_hour(str(text))] = {
                field: hourly[field][index] if field in hourly else None
                for field in fields
            }
    return hours


def _load_pl_chunks(
    chunks_dir: Path, station_id: str, variable: str
) -> dict[datetime, dict[int, float]]:
    """{instant: {level_hpa: value}} for w or q from assembled chunks."""
    hours: dict[datetime, dict[int, float]] = {}
    station_dir = chunks_dir / station_id
    if not station_dir.exists():
        return hours
    fields = [f"{variable}_{level}hPa" for level in PRESSURE_LEVELS_HPA]
    for path in sorted(station_dir.glob("*.json.gz")):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            continue
        hourly = payload["hourly"]
        for index, text in enumerate(hourly["time"]):
            record = {
                level: hourly[field][index]
                for level, field in zip(PRESSURE_LEVELS_HPA, fields)
                if hourly[field][index] is not None
            }
            if len(record) == len(PRESSURE_LEVELS_HPA):
                hours[_parse_hour(str(text))] = record
    return hours


def _series_at(
    series: dict[datetime, dict[str, float | None]],
    instant: datetime,
    fields: list[str],
) -> dict[str, float | None] | None:
    """Lerp a field-dict series at an instant (record_at convention)."""
    floor = instant.replace(minute=0, second=0, microsecond=0)
    if floor == instant:
        return series.get(instant)
    lo = series.get(floor)
    hi = series.get(floor + timedelta(hours=1))
    if lo is None or hi is None:
        return None
    frac = (instant - floor).total_seconds() / 3600.0
    out: dict[str, float | None] = {}
    for field in fields:
        a, b = lo.get(field), hi.get(field)
        out[field] = a + (b - a) * frac if a is not None and b is not None else None
    return out


def _pl_at(
    series: dict[datetime, dict[int, float]], instant: datetime
) -> dict[int, float] | None:
    """Lerp a per-level series at an instant (record_at convention)."""
    floor = instant.replace(minute=0, second=0, microsecond=0)
    if floor == instant:
        return series.get(instant)
    lo = series.get(floor)
    hi = series.get(floor + timedelta(hours=1))
    if lo is None or hi is None:
        return None
    frac = (instant - floor).total_seconds() / 3600.0
    return {level: lo[level] + (hi[level] - lo[level]) * frac for level in PRESSURE_LEVELS_HPA}


# ---------------------------------------------------------------------------
# Window aggregation
# ---------------------------------------------------------------------------

def _pwt_mean(pairs: list[tuple[float, float | None]]) -> float | None:
    """Precipitation-weighted mean of (tp, value) pairs.

    Falls back to the plain mean when the total weight is zero; None
    when no pair carries a value.
    """
    usable = [(w, v) for w, v in pairs if v is not None]
    if not usable:
        return None
    total_w = sum(w for w, _ in usable)
    if total_w > 0.0:
        return sum(w * v for w, v in usable) / total_w  # type: ignore[operator]
    return sum(v for _, v in usable) / len(usable)  # type: ignore[arg-type]


def _pwt_fraction(pairs: list[tuple[float, bool]]) -> float | None:
    """Precipitation-weighted fraction of True flags (count fallback)."""
    if not pairs:
        return None
    total_w = sum(w for w, _ in pairs)
    if total_w > 0.0:
        return sum(w for w, flag in pairs if flag) / total_w
    return sum(1 for _, flag in pairs if flag) / len(pairs)


def _percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile (numpy default method)."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = pct / 100.0 * (len(ordered) - 1)
    lo = int(math.floor(rank))
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def _habit_band(dgz_t: float | None, dgz_depth: float) -> str:
    if dgz_t is None or dgz_depth <= 0.0:
        return "other"
    if -17.5 <= dgz_t <= -12.5:
        return "dendrite"
    if -22.5 <= dgz_t < -17.5 or -12.5 < dgz_t <= -10.0:
        return "plate"
    return "other"


# ---------------------------------------------------------------------------
# Per-event builder
# ---------------------------------------------------------------------------

_ACCUMU_FIELDS = ["tp_mm", "cp_mm", "lsp_mm"]


def build_event_v3_features(
    event: dict[str, Any],
    profile_hours: dict[datetime, dict[str, Any]],
    w_series: dict[datetime, dict[int, float]],
    q_series: dict[datetime, dict[int, float]],
    accumu_series: dict[datetime, dict[str, float | None]],
    sfc_series: dict[datetime, dict[str, float | None]],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Derived features + per-source resolved-instant counts for one event."""
    start, _end = event_window(event)
    instants = [start + timedelta(hours=i) for i in range(1, 25)]
    elevation_m = float(event["elevation_m"])

    counts = {"profile": 0, "w": 0, "q": 0, "accumu": 0, "sfc": 0}
    tps: list[float | None] = []
    cps: list[float | None] = []
    lsps: list[float | None] = []
    hour_feats: list[dict[str, Any] | None] = []
    sfc_feats: list[dict[str, float | None] | None] = []

    for instant in instants:
        record = record_at(profile_hours, instant)
        w_dict = _pl_at(w_series, instant)
        q_dict = _pl_at(q_series, instant)
        acc = _series_at(accumu_series, instant, _ACCUMU_FIELDS)
        sfc = _series_at(sfc_series, instant, SFC_FIELDS)
        if record is not None:
            counts["profile"] += 1
        if w_dict is not None:
            counts["w"] += 1
        if q_dict is not None:
            counts["q"] += 1
        if acc is not None and acc.get("tp_mm") is not None:
            counts["accumu"] += 1
        if sfc is not None and all(sfc.get(f) is not None for f in SFC_FIELDS):
            counts["sfc"] += 1

        tp = acc.get("tp_mm") if acc is not None else None
        if tp is None and record is not None:
            tp = record.get("precipitationMm")
        tps.append(tp)
        cps.append(acc.get("cp_mm") if acc is not None else None)
        lsps.append(acc.get("lsp_mm") if acc is not None else None)

        hour_feats.append(
            _hour_profile_features(record, w_dict, q_dict, elevation_m)
            if record is not None
            else None
        )

        sfc_row: dict[str, float | None] | None = None
        t2m = record.get("temperature2mC") if record is not None else None
        if sfc is not None and t2m is not None and sfc.get("dewpoint_2m") is not None:
            d2m_c = float(sfc["dewpoint_2m"]) - 273.15
            rh_sfc = 100.0 * es_water_hpa(d2m_c) / es_water_hpa(float(t2m))
            u10, v10 = sfc.get("wind_u_10m"), sfc.get("wind_v_10m")
            sfc_row = {
                "wetbulb_d2m": wet_bulb_stull(float(t2m), rh_sfc),
                "dewp_depression": float(t2m) - d2m_c,
                "tclw": sfc.get("total_column_liquid_water"),
                "ws10m": math.hypot(u10, v10) if u10 is not None and v10 is not None else None,
                "skt_c": float(sfc["skin_temperature"]) - 273.15 if sfc.get("skin_temperature") is not None else None,
            }
        sfc_feats.append(sfc_row)

    weights = [tp if tp is not None else 0.0 for tp in tps]

    def pairs(key: str) -> list[tuple[float, float | None]]:
        return [
            (weights[i], hf[key])
            for i, hf in enumerate(hour_feats)
            if hf is not None
        ]

    def sfc_pairs(key: str) -> list[tuple[float, float | None]]:
        return [
            (weights[i], sf[key])
            for i, sf in enumerate(sfc_feats)
            if sf is not None
        ]

    features: dict[str, Any] = {name: None for name in DERIVED_FEATURES}

    # --- bundle 1: DGZ -------------------------------------------------------
    features["dgz_t_pwt"] = _pwt_mean(pairs("dgz_t"))
    features["dgz_dist_optimum_pwt"] = _pwt_mean(pairs("dgz_dist_optimum"))
    features["dgz_depth_m_pwt"] = _pwt_mean(pairs("dgz_depth_m"))
    features["dgz_precip_frac"] = _pwt_fraction(
        [(weights[i], hf["dgz_depth_m"] > 0.0) for i, hf in enumerate(hour_feats) if hf is not None]
    )
    for band, name in (
        ("dendrite", "habit_frac_dendrite"),
        ("plate", "habit_frac_plate"),
        ("other", "habit_frac_other"),
    ):
        features[name] = _pwt_fraction(
            [
                (weights[i], _habit_band(hf["dgz_t"], hf["dgz_depth_m"]) == band)
                for i, hf in enumerate(hour_feats)
                if hf is not None
            ]
        )

    # --- bundle 2: amount ----------------------------------------------------
    resolved_tp = [tp for tp in tps if tp is not None]
    if resolved_tp:
        total_tp = sum(resolved_tp)
        features["fcst_liquid_mm"] = total_tp
        features["precip_rate_mean"] = total_tp / 24.0
        features["precip_rate_max"] = max(resolved_tp)
        features["precip_rate_p90"] = _percentile(resolved_tp, 90.0)
        features["precip_hours_gt_1mm"] = sum(1 for tp in resolved_tp if tp > 1.0)
        first_half = sum(tp for tp in tps[:12] if tp is not None)
        features["precip_first_half_frac"] = (
            first_half / total_tp if total_tp > 0.0 else 0.5
        )

    # --- bundle 3: warm layer -------------------------------------------------
    features["tmax_column_pwt"] = _pwt_mean(pairs("tmax_column"))
    features["tmax_warmnose_pwt"] = _pwt_mean(pairs("tmax_warmnose"))
    features["freezing_level_m_agl_pwt"] = _pwt_mean(pairs("freezing_level_m_agl"))
    features["warm_layer_depth_m_pwt"] = _pwt_mean(pairs("warm_layer_depth_m"))
    features["sfc_wetbulb_c_pwt"] = _pwt_mean(pairs("sfc_wetbulb_c"))

    # --- bundle 4: riming proxy (RH + ascent in the -10..0 C layer) -----------
    features["riming_rh_pwt"] = _pwt_mean(pairs("riming_rh"))
    features["riming_omega_pwt"] = _pwt_mean(pairs("riming_omega"))
    features["riming_index_pwt"] = _pwt_mean(pairs("riming_index"))

    # --- bundle 5: ascent ------------------------------------------------------
    omega_values = [hf["dgz_w"] for hf in hour_feats if hf is not None and hf["dgz_w"] is not None]
    features["omega_dgz_pwt"] = _pwt_mean(pairs("dgz_w"))
    features["omega_dgz_min"] = min(omega_values) if omega_values else None
    features["cobb_overlap"] = _pwt_mean(pairs("cobb_overlap_frac"))
    features["cobb_overlap_m_pwt"] = _pwt_mean(pairs("cobb_overlap_m"))

    # --- bundle 6: wind ---------------------------------------------------------
    ws_max_values = [hf["ws_max"] for hf in hour_feats if hf is not None and hf["ws_max"] is not None]
    features["ws_dgz_pwt"] = _pwt_mean(pairs("dgz_ws"))
    features["ws_max_profile"] = max(ws_max_values) if ws_max_values else None
    features["ws_dgz_hours_gt_9ms"] = sum(
        1 for hf in hour_feats if hf is not None and hf["dgz_ws"] is not None and hf["dgz_ws"] > 9.0
    )
    features["cold_windy_interaction"] = _pwt_mean(pairs("cold_windy"))

    # --- bundle 7: humidity (q under the IFS mixed-phase formulation) ----------
    rhq_max_values = [
        hf["dgz_rhq_max"] for hf in hour_feats if hf is not None and hf["dgz_rhq_max"] is not None
    ]
    features["rhice_dgz"] = _pwt_mean(pairs("dgz_rhq_mean"))
    features["rhice_dgz_max"] = max(rhq_max_values) if rhq_max_values else None
    features["sat_layer_depth_m"] = _pwt_mean(pairs("sat_layer_depth_m"))
    features["dgz_sat_frac"] = _pwt_mean(pairs("dgz_sat_frac"))

    # --- bundle 8: surface set ---------------------------------------------------
    tclw_values = [sf["tclw"] for sf in sfc_feats if sf is not None and sf["tclw"] is not None]
    ws10_values = [sf["ws10m"] for sf in sfc_feats if sf is not None and sf["ws10m"] is not None]
    features["sfc_wetbulb_d2m_c_pwt"] = _pwt_mean(sfc_pairs("wetbulb_d2m"))
    features["sfc_dewpoint_depression_c_pwt"] = _pwt_mean(sfc_pairs("dewp_depression"))
    features["tclw_pwt"] = _pwt_mean(sfc_pairs("tclw"))
    features["tclw_max"] = max(tclw_values) if tclw_values else None
    features["ws10m_pwt"] = _pwt_mean(sfc_pairs("ws10m"))
    features["ws10m_max"] = max(ws10_values) if ws10_values else None
    features["skt_c_pwt"] = _pwt_mean(sfc_pairs("skt_c"))

    # --- bundle 9: aggregation proxies -------------------------------------------
    features["agg_layer_depth_m"] = _pwt_mean(pairs("agg_layer_depth_m"))
    features["fall_distance_below_dgz_m"] = _pwt_mean(pairs("fall_distance_below_dgz_m"))

    # --- bundle 10: sub-cloud ------------------------------------------------------
    features["subcloud_dewp_depression"] = _pwt_mean(pairs("subcloud_dewp_depression"))
    features["subcloud_unsat_depth_m"] = _pwt_mean(pairs("subcloud_unsat_depth_m"))
    features["subcloud_warm_depth_m"] = _pwt_mean(pairs("subcloud_warm_depth_m"))

    # --- bundle 11: regime (lsp/cp split) -------------------------------------------
    cp_total = sum(cp for cp in cps if cp is not None)
    lsp_total = sum(lsp for lsp in lsps if lsp is not None)
    if any(cp is not None for cp in cps):
        features["cp_total_mm"] = cp_total
        features["lsp_total_mm"] = lsp_total
        features["convective_frac"] = (
            cp_total / (cp_total + lsp_total) if (cp_total + lsp_total) > 0.0 else 0.0
        )

    for name in DERIVED_FEATURES:
        value = features[name]
        if isinstance(value, float):
            features[name] = round(value, 4)
    return features, counts


# ---------------------------------------------------------------------------
# Run entry point
# ---------------------------------------------------------------------------

def run_build_v3_features(pool_dir: Path, model_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Build model-v3 features: v2 AGL rows + Phase 3 derived bundles.

    ``model_dir`` is the model-v1 directory holding ``raw_cache_era5``,
    ``model-v3-features`` (the assembled Phase 3 trees) and
    ``era5-expanded-v2/features.parquet`` (base rows + the ok pool).
    """
    import pyarrow.parquet as pq

    v2_path = model_dir / "era5-expanded-v2" / "features.parquet"
    base_rows = pq.read_table(v2_path).to_pylist()
    events = load_modelling_events(pool_dir, v2_path)
    events_by_id = {event["event_id"]: event for event in events}
    v3_dir = model_dir / V3_DIR_NAME

    events_by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        events_by_station[event["station_id"]].append(event)

    derived_by_id: dict[str, dict[str, Any]] = {}
    status_by_id: dict[str, str] = {}
    status_counts: dict[str, int] = defaultdict(int)
    sfc_complete = 0
    for station_id in sorted(events_by_station):
        profile_hours = _load_profile_hours(model_dir / ERA5_SPEC.cache_dir_name, station_id)
        w_series = _load_pl_chunks(v3_dir / "pl_chunks", station_id, "w")
        q_series = _load_pl_chunks(v3_dir / "pl_chunks_q", station_id, "q")
        accumu_series = _load_series_chunks(v3_dir / "accumu_chunks", station_id, _ACCUMU_FIELDS)
        sfc_series = _load_series_chunks(v3_dir / "sfc_chunks", station_id, SFC_FIELDS)
        for event in sorted(events_by_station[station_id], key=lambda e: e["event_id"]):
            features, counts = build_event_v3_features(
                event, profile_hours, w_series, q_series, accumu_series, sfc_series
            )
            derived_by_id[event["event_id"]] = features
            shortfalls = [
                name for name in ("profile", "w", "q", "accumu") if counts[name] < 24
            ]
            status = "ok" if not shortfalls else "incomplete_" + "_".join(shortfalls)
            status_by_id[event["event_id"]] = status
            status_counts[status] += 1
            if counts["sfc"] == 24:
                sfc_complete += 1

    rows: list[dict[str, Any]] = []
    for base in base_rows:
        row = dict(base)
        derived = derived_by_id.get(row["event_id"])
        for name in DERIVED_FEATURES:
            row[name] = derived[name] if derived is not None else None
        row["v3_status"] = status_by_id.get(row["event_id"], "not_modelled")
        rows.append(row)

    output_dir.mkdir(parents=True, exist_ok=True)
    write_features_parquet(rows, output_dir / "features_v3.parquet")
    (output_dir / "feature_columns.json").write_text(
        json.dumps(
            {
                "baseFeatures": BASE_FEATURES,
                "bundles": BUNDLES,
                "derivedFeatures": DERIVED_FEATURES,
                "saturationFormulation": SATURATION_FORMULATION,
                "saturationFormulationDetail": SATURATION_FORMULATION_DETAIL,
                "aggregation": (
                    "per-hour scalars from above-ground profile levels, "
                    "aggregated precipitation-weighted (weights = hourly "
                    "tp from accumu chunks; plain-mean fallback when the "
                    "window tp total is zero); window max/min/count "
                    "features as named; precip_first_half_frac = 0.5 and "
                    "convective_frac = 0.0 when the window tp total is 0"
                ),
                "leakageRule": (
                    "forecast-available variables only; no observed "
                    "SWE/liquid features (observed liquid is the label's "
                    "denominator)"
                ),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    summary = {
        "eventsTotal": len(base_rows),
        "modellingEvents": len(events),
        "statusCounts": dict(status_counts),
        "sfcCompleteEvents": sfc_complete,
        "baseFeatures": BASE_FEATURES,
        "bundles": BUNDLES,
        "saturationFormulation": SATURATION_FORMULATION,
    }
    (output_dir / "build_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    summary["outputDir"] = str(output_dir)
    return summary
