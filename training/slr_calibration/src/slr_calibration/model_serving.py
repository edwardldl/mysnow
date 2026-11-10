"""Serving adapters for the v3 ENS-EB champion (model-serving test run).

Turns live forecast GRIB from the two design sources -- ECMWF IFS open
data and NOAA HRRR on AWS Open Data -- into the exact raw-input series
the training builders consume, then scores the packaged champion.

Adapter contract (mirrors the training assembly): every series is keyed
by whole UTC hours, because gold_profiles.record_at and
v3_features._series_at/_pl_at resolve instants from whole-hour records.
Each source's native stamps (ECMWF 3-hourly, HRRR hourly) are expanded
to hourly by linear time interpolation; accumulations are expanded by
interpolating the accumulated curve and differencing per hour.  Profile
variables are normalised onto the 13 ERA5 pressure levels
(PRESSURE_LEVELS_HPA): ECMWF open data carries a coarser level subset
(1000/925/850/700/600/500 hPa within the ERA5 range), so values are
interpolated vertically in log-pressure; HRRR carries all 13 natively.

Feature building reuses the training code unchanged:
gold_profiles.build_event_features_offset (27 AGL base features + the
established Python Kuchera baseline over the same profiles) and
v3_features.build_event_v3_features (derived physics features).

Scoring uses the packaged ChampionModel with the design SS5 pass-through
NaN policy (validated by scripts/validate_nan_passthrough.py): the
finiteness clause of champion.validate_event_frame is relaxed inside
this process only; the column contract is still enforced, and a
completeness guard on core inputs (forecast liquid + the eight AGL
profile temperatures) turns a window with a missing core input into a
recorded miss instead of a prediction.
"""

from __future__ import annotations

import io
import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np

from .gold_profiles import PRESSURE_LEVELS_HPA, build_event_features_offset
from .v3_features import build_event_v3_features

G_MS2 = 9.80665
FEATURE_BUILDER_VERSION = "model-serving-test-1 (adapters) + gold_profiles/v3_features (training builders, unmodified)"
SOURCE_ID_ECMWF = "ecmwf-ifs-open-data-0p25-oper"
SOURCE_ID_HRRR = "noaa-hrrr-aws-bdp"

REGION_BY_SLUG = {
    "palisades-tahoe": "sierra-nevada",
    "mammoth-mountain": "sierra-nevada",
    "mount-rose": "sierra-nevada",
    "june-mountain": "sierra-nevada",
    "bear-mountain": "sierra-nevada",
    "snow-summit": "sierra-nevada",
    "snow-valley": "sierra-nevada",
    "sierra-at-tahoe": "sierra-nevada",
    "heavenly": "sierra-nevada",
    "northstar": "sierra-nevada",
    "kirkwood": "sierra-nevada",
    "sugar-bowl": "sierra-nevada",
    "alta": "rockies",
    "jackson-hole": "rockies",
    "aspen": "rockies",
    "big-sky": "rockies",
    "sun-valley": "rockies",
    "arapahoe-basin": "rockies",
    "aspen-highlands": "rockies",
    "buttermilk": "rockies",
    "snowmass": "rockies",
    "brighton": "rockies",
    "copper-mountain": "rockies",
    "deer-valley": "rockies",
    "eldora": "rockies",
    "snowbasin": "rockies",
    "snowbird": "rockies",
    "solitude": "rockies",
    "steamboat": "rockies",
    "taos": "rockies",
    "winter-park": "rockies",
    "banff-sunshine": "rockies",
    "lake-louise": "rockies",
    "mt-norquay": "rockies",
    "tamarack": "rockies",
    "vail": "rockies",
    "beaver-creek": "rockies",
    "breckenridge": "rockies",
    "keystone": "rockies",
    "crested-butte": "rockies",
    "park-city": "rockies",
    "telluride": "rockies",
    "fernie": "rockies",
    "nakiska": "rockies",
    "powder-mountain": "rockies",
    "grand-targhee": "rockies",
    "whitefish": "rockies",
    "bridger-bowl": "rockies",
    "loveland": "rockies",
    "wolf-creek": "rockies",
    "monarch": "rockies",
    "purgatory": "rockies",
    "marmot-basin": "rockies",
    "mt-baker": "pacific-northwest",
    "stevens-pass": "pacific-northwest",
    "the-summit-at-snoqualmie": "pacific-northwest",
    "alpental": "pacific-northwest",
    "crystal-mountain": "pacific-northwest",
    "mt-bachelor": "pacific-northwest",
    "schweitzer": "pacific-northwest",
    "cypress-mountain": "pacific-northwest",
    "panorama": "pacific-northwest",
    "red-mountain": "pacific-northwest",
    "revelstoke": "pacific-northwest",
    "silverstar": "pacific-northwest",
    "sun-peaks": "pacific-northwest",
    "grouse-mountain": "pacific-northwest",
    "whistler-blackcomb": "pacific-northwest",
    "kicking-horse": "pacific-northwest",
    "kimberley": "pacific-northwest",
    "mt-hood-meadows": "pacific-northwest",
    "timberline": "pacific-northwest",
    "stowe": "northeast",
    "sugarloaf": "northeast",
    "blue-mountain-pa": "northeast",
    "camelback": "northeast",
    "killington": "northeast",
    "pico-mountain": "northeast",
    "loon-mountain": "northeast",
    "snowshoe": "northeast",
    "stratton": "northeast",
    "sugarbush": "northeast",
    "sunday-river": "northeast",
    "le-massif": "northeast",
    "tremblant": "northeast",
    "cranmore": "northeast",
    "jiminy-peak": "northeast",
    "ski-butternut": "northeast",
    "okemo": "northeast",
    "mount-snow": "northeast",
    "mount-sunapee": "northeast",
    "attitash": "northeast",
    "wildcat": "northeast",
    "crotched-mountain": "northeast",
    "hunter-mountain": "northeast",
    "liberty-mountain": "northeast",
    "roundtop": "northeast",
    "whitetail": "northeast",
    "jack-frost": "northeast",
    "big-boulder": "northeast",
    "seven-springs": "northeast",
    "hidden-valley-pa": "northeast",
    "laurel-mountain": "northeast",
    "mont-sainte-anne": "northeast",
    "stoneham": "northeast",
    "jay-peak": "northeast",
    "mad-river-glen": "northeast",
    "cannon-mountain": "northeast",
    "saddleback": "northeast",
    "whiteface": "northeast",
    "boyne-mountain": "great-lakes",
    "granite-peak": "great-lakes",
    "lutsen-mountains": "great-lakes",
    "snowriver": "great-lakes",
    "the-highlands": "great-lakes",
    "blue-mountain-on": "great-lakes",
    "devils-head": "great-lakes",
    "wild-mountain": "great-lakes",
    "buck-hill": "great-lakes",
    "afton-alps": "great-lakes",
    "wilmot-mountain": "great-lakes",
    "mt-brighton": "great-lakes",
    "alpine-valley-oh": "great-lakes",
    "mad-river-mountain": "great-lakes",
    "boston-mills-brandywine": "great-lakes",
    "paoli-peaks": "great-lakes",
    "hidden-valley-mo": "great-lakes",
    "snow-creek": "great-lakes",
    "alyeska": "alaska",
}

# ---------------------------------------------------------------------------
# Catalogue and windows
# ---------------------------------------------------------------------------


def load_catalogue(registry_path: Path) -> list[dict[str, Any]]:
    """Catalogue points from the tracking registry (source of record)."""
    registry = json.loads(Path(registry_path).read_text())
    points: list[dict[str, Any]] = []
    for resort in registry["resorts"]:
        slug = resort["resort_slug"]
        for point in resort["forecast_points"]:
            points.append({
                "point_id": point["point_id"],
                "resort_slug": slug,
                "resort_name": resort["pilot_name"],
                "role": point["role"],
                "point_name": point["name"],
                "latitude": float(point["latitude"]),
                "longitude": float(point["longitude"]),
                "elevation_m": float(point["elevation_m"]),
                "timezone": resort["timezone"],
                "state": resort["state"],
                "climate_regime": resort.get("climate_regime", "unclassified"),
                "country": resort.get("country", "United States"),
                "geometry_source": point["geometry_source"],
                "region": REGION_BY_SLUG[slug],
            })
    return points


def window_ends(cycle_utc: datetime, tz_name: str, leads: Iterable[int]) -> list[datetime]:
    """07:00-local window end instants (UTC) for the given lead days.

    Lead k ends at 07:00 local on (cycle's local date + k days); zoneinfo
    handles DST transitions inside the window.
    """
    tz = ZoneInfo(tz_name)
    local_date = cycle_utc.astimezone(tz).date()
    ends = []
    for k in leads:
        d = local_date + timedelta(days=k)
        end_local = datetime(d.year, d.month, d.day, 7, 0, tzinfo=tz)
        ends.append(end_local.astimezone(timezone.utc))
    return ends


def hours_between(start: datetime, end: datetime) -> list[datetime]:
    out = []
    t = start
    while t <= end:
        out.append(t)
        t += timedelta(hours=1)
    return out


# ---------------------------------------------------------------------------
# HTTP range fetching (index/idx byte-range subsetting for both sources)
# ---------------------------------------------------------------------------


def http_get(url: str, *, headers: dict[str, str] | None = None, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "mysnow-model-serving/1.0", **(headers or {})})
    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:  # transient proxy / connection resets
            last_exc = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500:
                raise
            time.sleep(2.0 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def fetch_ranges(url: str, ranges: list[tuple[int, int]], *, merge_gap: int = 65536,
                 max_workers: int = 6) -> dict[int, bytes]:
    """Fetch (offset, length) byte ranges; returns {offset: bytes}.

    Nearby ranges are merged into single requests; payloads are then
    sliced back apart in memory.
    """
    if not ranges:
        return {}
    ordered = sorted(ranges)
    merged: list[tuple[int, int]] = []
    cur_start, cur_len = ordered[0]
    cur_end = cur_start + cur_len
    for off, length in ordered[1:]:
        if off - cur_end <= merge_gap:
            cur_end = max(cur_end, off + length)
        else:
            merged.append((cur_start, cur_end - cur_start))
            cur_start, cur_end = off, off + length
    merged.append((cur_start, cur_end - cur_start))

    def fetch_one(span: tuple[int, int]) -> tuple[int, bytes]:
        start, length = span
        data = http_get(url, headers={"Range": f"bytes={start}-{start + length - 1}"})
        if len(data) != length:
            raise IOError(f"range fetch {url} @{start}+{length}: got {len(data)} bytes")
        return start, data

    payloads: dict[int, bytes] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for start, data in pool.map(fetch_one, merged):
            payloads[start] = data
    out: dict[int, bytes] = {}
    for off, length in ranges:
        for start, data in payloads.items():
            if start <= off and off + length <= start + len(data):
                out[off] = data[off - start: off - start + length]
                break
        else:
            raise IOError(f"range @{off}+{length} not covered by merged fetch")
    return out


# ---------------------------------------------------------------------------
# GRIB extraction at points (eccodes)
# ---------------------------------------------------------------------------


def _gid(data: bytes) -> int:
    """ecCodes handle for a single-message payload (codes_new_from_message
    avoids the real-file fileno that codes_grib_new_from_file requires)."""
    import eccodes

    return eccodes.codes_new_from_message(data)


def grib_values(data: bytes) -> np.ndarray:
    """Values of the single GRIB message in ``data`` (one index range =
    one message for both sources, so callers label messages from the
    source index rather than trusting decoder short names)."""
    import eccodes

    gid = _gid(data)
    try:
        return np.asarray(eccodes.codes_get_values(gid), dtype=float)
    finally:
        eccodes.codes_release(gid)


def grib_regular_geometry(data: bytes) -> dict[str, float]:
    import eccodes

    gid = _gid(data)
    try:
        return {
            "ni": int(eccodes.codes_get(gid, "Ni")),
            "nj": int(eccodes.codes_get(gid, "Nj")),
            "lat_first": float(eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")),
            "lon_first": float(eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")),
            "di": float(eccodes.codes_get(gid, "iDirectionIncrementInDegrees")),
            "dj": -float(eccodes.codes_get(gid, "jDirectionIncrementInDegrees")),
        }
    finally:
        eccodes.codes_release(gid)


def grib_latlons(data: bytes) -> tuple[np.ndarray, np.ndarray]:
    import eccodes

    gid = _gid(data)
    try:
        ni = int(eccodes.codes_get(gid, "Ni"))
        nj = int(eccodes.codes_get(gid, "Nj"))
        lats = np.asarray(eccodes.codes_get_array(gid, "latitudes"), dtype=float)
        lons = np.asarray(eccodes.codes_get_array(gid, "longitudes"), dtype=float)
        return lats.reshape(nj, ni), lons.reshape(nj, ni)
    finally:
        eccodes.codes_release(gid)


def sample_regular_grid(values: np.ndarray, ni: int, nj: int,
                        lat_first: float, lon_first: float,
                        di: float, dj: float,
                        lat: float, lon: float) -> float:
    """Bilinear sample of a regular lat/lon grid (row 0 = lat_first,
    values ordered west->east, north->south when dj < 0)."""
    lon = lon % 360.0
    lon0 = lon_first % 360.0
    fx = (lon - lon0) / di
    fy = (lat - lat_first) / dj
    x0 = int(math.floor(fx)) % ni
    x1 = (x0 + 1) % ni
    y0 = min(max(int(math.floor(fy)), 0), nj - 2)
    tx = fx - math.floor(fx)
    ty = fy - math.floor(fy)
    grid = values.reshape(nj, ni)
    top = grid[y0, x0] * (1 - tx) + grid[y0, x1] * tx
    bot = grid[y0 + 1, x0] * (1 - tx) + grid[y0 + 1, x1] * tx
    return float(top * (1 - ty) + bot * ty)


def hrrr_cell_indices(lats: np.ndarray, lons: np.ndarray,
                      points: list[dict[str, Any]]) -> list[int]:
    """Nearest-cell flat index per point on the HRRR Lambert grid."""
    out = []
    flat_lat = lats.ravel()
    flat_lon = lons.ravel()
    for p in points:
        dlat = (flat_lat - p["latitude"]) * 111.32
        # Grid longitudes may be stored 0..360 while point longitudes are
        # -180..180: use the circular difference so the seam never wins.
        dlon_deg = (flat_lon - p["longitude"] + 180.0) % 360.0 - 180.0
        dlon = dlon_deg * 111.32 * math.cos(math.radians(p["latitude"]))
        out.append(int(np.argmin(dlat * dlat + dlon * dlon)))
    return out


def hrrr_cell_distances_km(lats: np.ndarray, lons: np.ndarray,
                           points: list[dict[str, Any]]) -> list[float]:
    """Distance (km) from each point to its nearest HRRR cell centre.

    Uses the same equirectangular metric as hrrr_cell_indices. On-domain
    points sit within ~half a grid diagonal (~2.2 km at 3 km spacing) of a
    cell centre; points outside the CONUS footprint are far away, which is
    what the serving batch uses to detect off-domain points.
    """
    out = []
    flat_lat = lats.ravel()
    flat_lon = lons.ravel()
    for p in points:
        dlat = (flat_lat - p["latitude"]) * 111.32
        dlon_deg = (flat_lon - p["longitude"] + 180.0) % 360.0 - 180.0
        dlon = dlon_deg * 111.32 * math.cos(math.radians(p["latitude"]))
        out.append(float(np.sqrt(np.min(dlat * dlat + dlon * dlon))))
    return out


HRRR_POLICY_REASON = (
    "HRRR arm not served for points outside the United States "
    "(CONUS-only product)"
)
HRRR_DOMAIN_REASON = "outside HRRR CONUS domain (arm not applicable)"
HRRR_MAX_CELL_DISTANCE_KM = 10.0


def hrrr_domain_exclusion(country: str, distance_km: float) -> str | None:
    """Why the HRRR arm does not serve a point, or None if it does.

    Two distinct exclusions (Codex review 2026-11-10): a SERVING POLICY
    exclusion — HRRR is a CONUS product, so points outside the United
    States are not served even where the grid's lat/lon footprint
    overlaps southern Canada and nearby cells exist — and a GEOMETRIC
    exclusion for US points whose nearest cell centre is implausibly
    far for a 3 km grid (on-domain maximum ~2.2 km; the 10 km threshold
    is a coarse safeguard, not an exact footprint boundary — it catches
    Alaska). The policy clause takes precedence when both apply.
    """
    if country != "United States":
        return HRRR_POLICY_REASON
    if distance_km > HRRR_MAX_CELL_DISTANCE_KM:
        return HRRR_DOMAIN_REASON
    return None


# ---------------------------------------------------------------------------
# Vertical / temporal normalisation helpers
# ---------------------------------------------------------------------------


def interp_level_series(source: dict[int, float], targets: Iterable[int] = PRESSURE_LEVELS_HPA) -> dict[int, float]:
    """Interpolate a {pressure_hpa: value} profile onto target levels,
    linear in log-pressure; outside the source range, clamp to the
    nearest source level's value (below-ground levels are discarded by
    the builders via geopotential height)."""
    levels = sorted(source)
    out: dict[int, float] = {}
    for target in targets:
        if target in source:
            out[target] = float(source[target])
        elif target > levels[-1]:
            out[target] = float(source[levels[-1]])
        elif target < levels[0]:
            out[target] = float(source[levels[0]])
        else:
            hi = min(l for l in levels if l > target)
            lo = max(l for l in levels if l < target)
            frac = (math.log(target) - math.log(lo)) / (math.log(hi) - math.log(lo))
            out[target] = float(source[lo] + (source[hi] - source[lo]) * frac)
    return out


def expand_hourly(stamps: list[datetime], series: list[Any]) -> dict[datetime, Any]:
    """Linear time interpolation of stamp-keyed values onto whole hours."""
    out: dict[datetime, Any] = {}
    for i, stamp in enumerate(stamps):
        out[stamp] = series[i]
    for i in range(len(stamps) - 1):
        t0, t1 = stamps[i], stamps[i + 1]
        span = (t1 - t0).total_seconds() / 3600.0
        step = 1
        while step < span:
            frac = step / span
            out[t0 + timedelta(hours=step)] = _lerp_any(series[i], series[i + 1], frac)
            step += 1
    return out


def _lerp_any(a: Any, b: Any, frac: float) -> Any:
    if isinstance(a, dict):
        return {k: _lerp_any(a[k], b[k], frac) for k in a}
    if isinstance(a, (list, tuple)):
        return type(a)(_lerp_any(x, y, frac) for x, y in zip(a, b))
    if a is None or b is None:
        return None
    return a + (b - a) * frac


def hourly_amounts(accumulated: dict[datetime, float]) -> dict[datetime, float]:
    """Per-hour amounts from an accumulated curve keyed by hour:
    amount(t) = A(t) - A(t-1h); the first key gets its own value."""
    out: dict[datetime, float] = {}
    keys = sorted(accumulated)
    for i, t in enumerate(keys):
        if i == 0:
            out[t] = accumulated[t]
        else:
            out[t] = accumulated[t] - accumulated[keys[i - 1]]
    return out


def tclw_from_hydrometeors(levels_hpa: list[int], liquid_kgkg: list[float],
                           heights_m: list[float], elevation_m: float) -> float:
    """Column liquid water (kg/m^2) from liquid hydrometeor mixing ratios
    on pressure levels: (1/g) * integral of q_l dp, trapezoid in p over
    segments whose midpoint height is at/above the surface."""
    total = 0.0
    for i in range(len(levels_hpa) - 1):
        p_top, p_bot = levels_hpa[i + 1], levels_hpa[i]  # levels sorted desc by pressure in caller
        mid_height = 0.5 * (heights_m[i] + heights_m[i + 1])
        if mid_height < elevation_m:
            continue
        dp_pa = (p_bot - p_top) * 100.0
        total += 0.5 * (liquid_kgkg[i] + liquid_kgkg[i + 1]) * dp_pa
    return total / G_MS2


# ---------------------------------------------------------------------------
# Feature building and scoring
# ---------------------------------------------------------------------------


def make_event(point: dict[str, Any], window_end_utc: datetime, source_id: str) -> dict[str, Any]:
    tz = ZoneInfo(point["timezone"])
    local_end = window_end_utc.astimezone(tz)
    return {
        "event_id": f"serving-{source_id}-{point['point_id']}-{window_end_utc.strftime('%Y%m%dT%H%MZ')}",
        "region": point["region"],
        "station_id": point["point_id"],
        "site_id": point["point_id"],
        "station_name": f"{point['resort_name']} — {point['point_name']}",
        "state": point["state"],
        "valid_time": window_end_utc,
        "observation_date": local_end.date().isoformat(),
        "water_year": local_end.year + (1 if local_end.month >= 10 else 0),
        "month": local_end.month,
        "observed_slr": 0.0,
        "fresh_snow_mm": 0.0,
        "fresh_swe_mm": 0.0,
        "climate_regime": point["climate_regime"],
        "source_tier": "serving-test-run",
        "elevation_m": point["elevation_m"],
        "latitude": point["latitude"],
        "longitude": point["longitude"],
    }


def build_point_window_features(
    point: dict[str, Any],
    window_end_utc: datetime,
    profile_hours: dict[datetime, dict[str, Any]],
    w_series: dict[datetime, dict[int, float]],
    q_series: dict[datetime, dict[int, float]],
    accumu_series: dict[datetime, dict[str, float | None]],
    sfc_series: dict[datetime, dict[str, float | None]],
    source_id: str,
) -> dict[str, Any]:
    """Full 64-feature row + Kuchera baseline for one point x window."""
    event = make_event(point, window_end_utc, source_id)
    base_row = build_event_features_offset(event, profile_hours, source_id)
    derived, counts = build_event_v3_features(event, profile_hours, w_series, q_series,
                                              accumu_series, sfc_series)
    row: dict[str, Any] = {}
    for name, value in base_row.items():
        row[name] = value
    row.update(derived)
    row["source_resolved_counts"] = counts
    row["profile_status"] = base_row.get("profile_status")
    row["valid_hours"] = base_row.get("valid_hours")
    return row


CORE_FEATURE_PREFIXES = ("fcst_liquid_mm",)


def core_inputs_finite(row: dict[str, Any], sidecar_features: list[str]) -> bool:
    """Design SS5 completeness guard: forecast liquid and the eight AGL
    profile temperatures must be finite for a prediction to be issued."""
    if row.get("fcst_liquid_mm") is None:
        return False
    agl_temps = [f for f in sidecar_features if f.startswith("t_") and f.endswith("m")]
    return all(row.get(f) is not None and math.isfinite(float(row[f])) for f in agl_temps)


def predict_with_passthrough(model: Any, frame: Any) -> Any:
    """ChampionModel.predict with the finiteness clause of
    validate_event_frame relaxed (SS5 pass-through, validated)."""
    import slr_calibration.champion as champion_mod

    def relaxed(frame_: Any, features: list[str], *, strict: bool = False) -> None:
        missing = [c for c in features if c not in frame_.columns]
        if "region" not in frame_.columns:
            missing.append("region")
        if missing:
            raise ValueError("missing columns: " + ", ".join(missing))

    original = champion_mod.validate_event_frame
    champion_mod.validate_event_frame = relaxed
    try:
        return model.predict(frame)
    finally:
        champion_mod.validate_event_frame = original
