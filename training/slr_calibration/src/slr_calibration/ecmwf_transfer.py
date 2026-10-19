"""ECMWF transfer test (design section 8).

For a deterministic subsample of WY2026 holdout stations per retained
region, backfill ECMWF IFS profiles from the Open-Meteo historical-forecast
API (explicit ``models=ecmwf_ifs`` -- profiles.py's default request is the
best-match archive, which is not what this test measures), compute ECMWF
Kuchera baselines with the package's Python Kuchera path
(``tracking_align.compute_baseline_slr`` over the station-local day window,
the same (t-1, t] semantics as the NOAA dataset), and score three ways
against the observed differenced SLR:

(a) raw Kuchera-on-ECMWF;
(b) the retained HRRR-trained artifact applied to ECMWF features;
(c) the same artifact applied to the HRRR features of the same events.

Requests are cached per station under the output cache dir and spaced
>= 1.2 s apart.  A provider daily-limit 429 stops the run cleanly: the
report then records exactly how many stations/events were backfilled, and
the test is reported as quota-blocked, not as a scientific result.

Measured limitation (2026-10-19): with ``models=ecmwf_ifs`` the API
populates the surface fields but returns null for *every* pressure-level
variable (``hourly_units`` says ``undefined``).  The Kuchera baseline
cannot be computed without the profile, so the run detects this on the
first station with parsed hours and stops with status
``blocked-profile-levels-unavailable`` rather than scoring anything.
Levels do exist in the same API's best-match archive (that is how the
previous CSSL/Rockies profiles were built), but the contributing model is
not exposed there, so a best-match profile cannot honestly be labelled
ECMWF IFS and is not substituted.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from .io import read_rows
from .profiles import HOURLY_FIELDS, OPEN_METEO_ENDPOINT, _response_hours
from .regional_model import apply_candidate
from .tracking_align import compute_baseline_slr

NOMINAL_LEAD_H = 24.0  # HRRR training lead is the constant 00Z f24 daily lead


class QuotaBlocked(Exception):
    """Raised when the provider reports the daily request limit is exhausted."""


def _request_url(latitude: float, longitude: float, start: str, end: str) -> str:
    query = urllib.parse.urlencode({
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "start_date": start,
        "end_date": end,
        "hourly": ",".join(HOURLY_FIELDS),
        "timezone": "UTC",
        "models": "ecmwf_ifs",
    })
    return f"{OPEN_METEO_ENDPOINT}?{query}"


def _fetch_json(url: str, retries: int = 3) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "MySnow-SLR-research/0.1"})
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace") if exc.fp else ""
            if exc.code == 429 and "Daily" in body:
                raise QuotaBlocked(body.strip()[:300]) from exc
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt * 2)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt * 2)
    assert last_error is not None
    raise last_error


def _levels_available(hours: list[dict[str, Any]]) -> bool:
    """True when any parsed hour carries any non-null profile level value."""
    for hour in hours:
        for level in hour.get("levels") or []:
            if level.get("temperatureC") is not None or level.get("geopotentialHeightM") is not None:
                return True
    return False


def _station_day_features(hours: list[dict[str, Any]], start_utc: datetime, end_utc: datetime, elevation: float) -> dict[str, Any] | None:
    baseline = compute_baseline_slr(hours, start_utc, end_utc, elevation)
    if baseline is None:
        return None
    precip_values: list[float] = []
    t2m_values: list[float] = []
    for hour in hours:
        instant = datetime.fromisoformat(str(hour["time"]).replace("Z", "+00:00"))
        if start_utc < instant <= end_utc:
            if hour.get("precipitationMm") is not None:
                precip_values.append(float(hour["precipitationMm"]))
            if hour.get("temperature2mC") is not None:
                t2m_values.append(float(hour["temperature2mC"]))
    if not precip_values or not t2m_values:
        return None
    return {
        "baseline_slr": baseline[0],
        "fcst_precip_mm": sum(precip_values),
        "fcst_t2m_mean_c": mean(t2m_values),
        "fcst_t2m_min_c": min(t2m_values),
        "fcst_t2m_max_c": max(t2m_values),
        "evaluated_hours": baseline[1],
    }


def run_transfer_test(
    events_path: Path,
    evaluation_report_path: Path,
    cache_dir: Path,
    output_path: Path,
    max_stations_per_region: int = 4,
    sleep_seconds: float = 1.2,
) -> dict[str, Any]:
    evaluation = json.loads(evaluation_report_path.read_text(encoding="utf-8"))
    retained: dict[str, dict[str, Any]] = {}
    for region_id, result in evaluation["regions"].items():
        if result.get("selectedCandidate") and result.get("artifactFile"):
            artifact = json.loads(Path(result["artifactFile"]).read_text(encoding="utf-8"))
            retained[region_id] = {"candidate": result["selectedCandidate"], "artifact": artifact}
    rows = [row for row in read_rows(events_path) if row.get("trainer_eligible") in (True, "True", "true", 1) and int(row["water_year"]) == 2026]

    by_region_station: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        region_id = str(row.get("region_id") or "")
        if region_id in retained:
            by_region_station[region_id][str(row["station_id"])].append(row)

    sampled: dict[str, list[str]] = {}
    for region_id, stations in by_region_station.items():
        ordered = sorted(stations.items(), key=lambda item: (-len(item[1]), item[0]))
        sampled[region_id] = [station_id for station_id, _ in ordered[:max_stations_per_region]]

    cache_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "status": "complete",
        "model": "ecmwf_ifs via Open-Meteo historical-forecast API (explicit models=ecmwf_ifs)",
        "retainedRegions": sorted(retained),
        "sampledStationsPerRegion": sampled,
        "networkRequests": 0,
        "cacheHits": 0,
        "stationsCompleted": 0,
        "stationsAttempted": 0,
        "eventsBackfilled": 0,
        "eventsAttempted": 0,
        "perRegion": {},
        "quotaMessage": None,
        "blockingReason": None,
        "eventsBaselineFailed": 0,
    }
    scores: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"a_ecmwf_raw": [], "b_ecmwf_artifact": [], "c_hrrr_artifact": []})
    quota_blocked = False
    levels_blocked = False
    for region_id in sorted(sampled):
        artifact = retained[region_id]["artifact"]
        for station_id in sampled[region_id]:
            station_rows = by_region_station[region_id][station_id]
            report["stationsAttempted"] += 1
            report["eventsAttempted"] += len(station_rows)
            first = station_rows[0]
            start = min(str(row["observation_date"]) for row in station_rows)
            end = max(str(row["observation_date"]) for row in station_rows)
            cache_path = cache_dir / f"{station_id.replace(':', '_')}.json"
            payload: dict[str, Any] | None = None
            if cache_path.exists():
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cached.get("span") == [start, end] and cached.get("model") == "ecmwf_ifs":
                    payload = cached["payload"]
                    report["cacheHits"] += 1
            if payload is None:
                if quota_blocked:
                    continue
                if levels_blocked:
                    # Already known: this source serves no profile levels.
                    # Count the station's events as baseline-failed from the
                    # cache bookkeeping alone; do not spend quota refetching.
                    report["eventsBaselineFailed"] += len(station_rows)
                    continue
                url = _request_url(float(first["latitude"]), float(first["longitude"]), start, end)
                try:
                    payload = _fetch_json(url)
                except QuotaBlocked as exc:
                    quota_blocked = True
                    report["status"] = "quota-blocked"
                    report["quotaMessage"] = str(exc)
                    continue
                except Exception as exc:  # recorded, not hidden
                    report.setdefault("stationErrors", {})[station_id] = f"{type(exc).__name__}: {exc}"
                    continue
                report["networkRequests"] += 1
                cache_path.write_text(json.dumps({"span": [start, end], "model": "ecmwf_ifs", "payload": payload}), encoding="utf-8")
                time.sleep(sleep_seconds)
            hours = _response_hours(payload)
            if not _levels_available(hours):
                # The Kuchera baseline needs the vertical profile.  When the
                # provider serves no level data for this model at all, every
                # remaining station will fail identically -- stop and say so
                # instead of burning quota on identical failures.
                report["status"] = "blocked-profile-levels-unavailable"
                levels_blocked = True
                report["blockingReason"] = (
                    "Open-Meteo historical-forecast API returns null for every ECMWF IFS "
                    "pressure-level variable (hourly_units reports 'undefined' for "
                    "temperature_*hPa / geopotential_height_*hPa) while surface fields are "
                    "populated. The Python Kuchera path requires the temperature/geopotential "
                    "profile, so no ECMWF baseline can be computed from this source."
                )
                report["eventsBaselineFailed"] += len(station_rows)
                continue
            offset = timezone(timedelta(hours=float(first["data_tz_offset_hours"])))
            station_done = True
            for row in station_rows:
                day = datetime.fromisoformat(str(row["observation_date"])).replace(tzinfo=offset)
                start_utc = day.astimezone(timezone.utc)
                end_utc = start_utc + timedelta(hours=24)
                features = _station_day_features(hours, start_utc, end_utc, float(row["elevation_m"]))
                if features is None:
                    station_done = False
                    report["eventsBaselineFailed"] += 1
                    continue
                observed = float(row["observed_slr"])
                ecmwf_row = {
                    **row,
                    "baseline_slr": features["baseline_slr"],
                    "fcst_precip_mm": features["fcst_precip_mm"],
                    "fcst_t2m_mean_c": features["fcst_t2m_mean_c"],
                    "fcst_t2m_min_c": features["fcst_t2m_min_c"],
                    "fcst_t2m_max_c": features["fcst_t2m_max_c"],
                    "hrrr_lead_max_h": NOMINAL_LEAD_H,
                }
                scores[region_id]["a_ecmwf_raw"].append(abs(features["baseline_slr"] - observed))
                scores[region_id]["b_ecmwf_artifact"].append(abs(apply_candidate(artifact, ecmwf_row).calibrated_slr - observed))
                scores[region_id]["c_hrrr_artifact"].append(abs(apply_candidate(artifact, row).calibrated_slr - observed))
                report["eventsBackfilled"] += 1
            if station_done:
                report["stationsCompleted"] += 1

    for region_id, values in sorted(scores.items()):
        if not values["a_ecmwf_raw"]:
            continue
        report["perRegion"][region_id] = {
            "candidate": retained[region_id]["candidate"],
            "events": len(values["a_ecmwf_raw"]),
            "maeRawKucheraOnEcmwf": round(mean(values["a_ecmwf_raw"]), 4),
            "maeArtifactOnEcmwf": round(mean(values["b_ecmwf_artifact"]), 4),
            "maeArtifactOnHrrrSameEvents": round(mean(values["c_hrrr_artifact"]), 4),
            "transferVerdict": (
                "artifact improves on ECMWF features"
                if mean(values["b_ecmwf_artifact"]) < mean(values["a_ecmwf_raw"])
                else "artifact does not improve on ECMWF features (refit needed)"
            ),
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
