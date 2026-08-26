from __future__ import annotations

import csv
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

OPEN_METEO_ENDPOINT = "https://historical-forecast-api.open-meteo.com/v1/forecast"
PRESSURE_LEVELS_HPA = (1000, 975, 950, 925, 900, 850, 800, 700, 600, 500)
HOURLY_FIELDS = (
    "temperature_2m",
    "precipitation",
    "snowfall",
    "freezing_level_height",
    *(f"temperature_{level}hPa" for level in PRESSURE_LEVELS_HPA),
    *(f"geopotential_height_{level}hPa" for level in PRESSURE_LEVELS_HPA),
)
FIXED_PST = timezone(timedelta(hours=-8), name="PST")


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _request_url(latitude: float, longitude: float, start: date, end: date) -> str:
    query = urllib.parse.urlencode({
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY_FIELDS),
        "timezone": "UTC",
    })
    return f"{OPEN_METEO_ENDPOINT}?{query}"


def _download_json(url: str, retries: int = 3) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "MySnow-SLR-research/0.1"})
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def _month_window(month: str, wanted_dates: list[date]) -> tuple[date, date]:
    year, month_number = (int(part) for part in month.split("-"))
    _ = date(year, month_number, monthrange(year, month_number)[1])
    # Fixed-PST local days need the adjacent UTC day at both boundaries.
    return min(wanted_dates) - timedelta(days=1), max(wanted_dates) + timedelta(days=1)


def _validate_response(payload: dict[str, Any]) -> None:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise ValueError("Open-Meteo response lacks hourly time series")
    expected = len(hourly["time"])
    for field in HOURLY_FIELDS:
        values = hourly.get(field)
        if not isinstance(values, list) or len(values) != expected:
            raise ValueError(f"Open-Meteo response field {field} has invalid length")


def _finite(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        number = float(value)
        if number == number and number not in (float("inf"), float("-inf")):
            return number
    return None


def _response_hours(payload: dict[str, Any]) -> list[dict[str, Any]]:
    _validate_response(payload)
    hourly = payload["hourly"]
    hours: list[dict[str, Any]] = []
    for index, time_text in enumerate(hourly["time"]):
        instant = datetime.fromisoformat(str(time_text))
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        levels = []
        for level in PRESSURE_LEVELS_HPA:
            levels.append({
                "pressureHpa": level,
                "temperatureC": _finite(hourly[f"temperature_{level}hPa"][index]),
                "geopotentialHeightM": _finite(hourly[f"geopotential_height_{level}hPa"][index]),
            })
        hours.append({
            "time": instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "localPstDate": instant.astimezone(FIXED_PST).date().isoformat(),
            "temperature2mC": _finite(hourly["temperature_2m"][index]),
            "precipitationMm": _finite(hourly["precipitation"][index]),
            "snowfallCm": _finite(hourly["snowfall"][index]),
            "freezingLevelHeightM": _finite(hourly["freezing_level_height"][index]),
            "levels": levels,
        })
    return hours


def _read_events(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def backfill_openmeteo_profiles(
    input_events: Path,
    cache_dir: Path,
    output_jsonl: Path,
    output_report: Path,
    start_date: date,
    end_date: date,
    sleep_seconds: float = 0.25,
) -> dict[str, Any]:
    events = [
        row for row in _read_events(input_events)
        if start_date <= _parse_date(row["observation_date"]) <= end_date
    ]
    if not events:
        raise ValueError("No CSSL events fall inside the requested profile period")

    cache_dir.mkdir(parents=True, exist_ok=True)
    latitude = float(events[0]["latitude"])
    longitude = float(events[0]["longitude"])
    station_elevation = float(events[0]["elevation_m"])
    wanted_by_month: dict[str, list[date]] = defaultdict(list)
    for event in events:
        value = _parse_date(event["observation_date"])
        wanted_by_month[_month_key(value)].append(value)

    payloads: dict[str, dict[str, Any]] = {}
    request_records: list[dict[str, Any]] = []
    failed_months: dict[str, str] = {}
    downloaded = 0
    reused = 0
    for month in sorted(wanted_by_month):
        cache_path = cache_dir / f"{month}.json"
        request_start, request_end = _month_window(month, wanted_by_month[month])
        url = _request_url(latitude, longitude, request_start, request_end)
        try:
            if cache_path.exists():
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                reused += 1
            else:
                payload = _download_json(url)
                _validate_response(payload)
                cache_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
                downloaded += 1
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
            _validate_response(payload)
            payloads[month] = payload
            request_records.append({
                "month": month,
                "requestStart": request_start.isoformat(),
                "requestEnd": request_end.isoformat(),
                "requestUrl": url,
                "cacheFile": cache_path.name,
                "cacheSha256": hashlib.sha256(cache_path.read_bytes()).hexdigest(),
                "returnedLatitude": payload.get("latitude"),
                "returnedLongitude": payload.get("longitude"),
                "returnedElevationM": payload.get("elevation"),
                "timezone": payload.get("timezone"),
                "utcOffsetSeconds": payload.get("utc_offset_seconds"),
            })
        except Exception as exc:  # preserve partial coverage rather than fabricating
            failed_months[month] = f"{type(exc).__name__}: {exc}"

    hours_by_local_date_and_time: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for payload in payloads.values():
        for hour in _response_hours(payload):
            hours_by_local_date_and_time[hour["localPstDate"]][hour["time"]] = hour
    hours_by_local_date = {
        key: sorted(value.values(), key=lambda row: row["time"])
        for key, value in hours_by_local_date_and_time.items()
    }
    hours_by_time = {
        hour["time"]: hour
        for by_time in hours_by_local_date_and_time.values()
        for hour in by_time.values()
    }

    output_records: list[dict[str, Any]] = []
    rejection_counts: Counter[str] = Counter()
    for event in events:
        event_date = event["observation_date"]
        month = event_date[:7]
        if month in failed_months:
            rejection_counts["PROFILE_MONTH_UNAVAILABLE"] += 1
            continue
        hours = hours_by_local_date.get(event_date, [])
        if len(hours) < 20:
            rejection_counts["INCOMPLETE_LOCAL_DAY"] += 1
            continue
        snow_hours = [hour for hour in hours if (hour["snowfallCm"] or 0.0) > 0]
        if not snow_hours:
            rejection_counts["NO_MODEL_SNOWFALL_ALIGNMENT"] += 1
            continue
        usable_snow_hours = [
            hour for hour in snow_hours
            if any(
                level["temperatureC"] is not None
                and level["geopotentialHeightM"] is not None
                and level["geopotentialHeightM"] >= station_elevation - 100
                for level in hour["levels"]
            )
        ]
        if not usable_snow_hours:
            rejection_counts["NO_USABLE_ABOVE_GROUND_PROFILE"] += 1
            continue
        first_hour = datetime.fromisoformat(hours[0]["time"].replace("Z", "+00:00"))
        previous_key = (first_hour - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        previous_hour = hours_by_time.get(previous_key)
        if previous_hour is None:
            rejection_counts["MISSING_PRECEDING_INTERVAL_ENDPOINT"] += 1
            continue
        context_hours = [previous_hour, *hours]
        output_records.append({
            **event,
            "elevation_m": station_elevation,
            "profileSource": "Open-Meteo Historical Forecast API",
            "profileModel": "best-match archive; exact contributing model not exposed by response",
            "profileRetrievedFrom": OPEN_METEO_ENDPOINT,
            "profileAlignment": "fixed-PST local calendar day; production (t-1,t] interval-mean profile; model-precipitation weighted Kuchera; research-only",
            "profileHourCount": len(usable_snow_hours),
            "contextHourCount": len(context_hours),
            "modelSnowfallCm": round(sum(float(hour["snowfallCm"]) for hour in usable_snow_hours), 6),
            "hourly": context_hours,
        })

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with output_jsonl.open("w", encoding="utf-8") as handle:
        for record in sorted(output_records, key=lambda row: row["event_id"]):
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

    report = {
        "source": {
            "name": "Open-Meteo Historical Forecast API",
            "endpoint": OPEN_METEO_ENDPOINT,
            "requestedLatitude": latitude,
            "requestedLongitude": longitude,
            "stationElevationM": station_elevation,
            "hourlyFields": list(HOURLY_FIELDS),
            "modelIdentification": "best-match archive; exact contributing model not exposed by response",
        },
        "requestedPeriod": [start_date.isoformat(), end_date.isoformat()],
        "inputEventCount": len(events),
        "alignedEventCount": len(output_records),
        "rejectedEventCount": len(events) - len(output_records),
        "rejectionCounts": dict(sorted(rejection_counts.items())),
        "monthRequestCount": len(wanted_by_month),
        "downloadedMonthCount": downloaded,
        "reusedCacheMonthCount": reused,
        "failedMonths": failed_months,
        "requests": request_records,
        "limitations": [
            "Historical Forecast coverage is recent and model-dependent; it cannot backfill the 1971-present CSSL history.",
            "The response does not identify the exact contributing best-match model, so model provenance is incomplete.",
            "CSSL measurement windows are not documented precisely enough for exact hourly alignment; fixed-PST local calendar day is a research proxy.",
            "Only model-snowfall hours are used; observed events with no aligned modeled snowfall are rejected.",
            "The requested profile lacks dew point and vertical velocity, so it supports exact MySnow Kuchera but not a true Cobb estimate.",
        ],
    }
    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
