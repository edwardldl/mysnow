from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .tracking_common import fetch_json, finite, haversine_km

AWDB_STATIONS_URL = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/stations"
FEET_TO_M = 0.3048
DEFAULT_MAX_STATION_KM = 40.0

# Pilot resorts for the snowfall-tracking loop.  ``catalog_name`` is the exact
# entry name in src/data/ski-areas.json (verified against the catalog); the
# expected state guards against same-name resorts in other states/countries.
# Resolution notes:
# - Palisades Tahoe: the app ships a curated default location in
#   src/lib/api.ts (DEFAULT_LOCATIONS) with surveyed base/mid/summit points;
#   the registry mirrors those points exactly (geometry_source="curated").
#   The catalog also lists "Palisades Tahoe Olympic Valley" and
#   "Palisades Tahoe Alpine Meadows" as separate areas.
# - Aspen: the catalog has Aspen Mountain, Aspen Highlands and Aspen Snowmass
#   as separate areas; the namesake Aspen Mountain is the pilot.
# - Mt. Baker: catalog entry is spelled "Mt Baker" (Washington).
# - Sugarloaf: the ski resort is the exact-name "Sugarloaf" Maine entry;
#   "Sugarloaf Outdoor Center" is the separate Nordic center and is not used.
PILOT_RESORTS: list[dict[str, Any]] = [
    {"pilot_name": "Palisades Tahoe", "catalog_name": None, "state": "CA",
     "timezone": "America/Los_Angeles", "climate_regime": "sierra_maritime_mountain"},
    {"pilot_name": "Mammoth Mountain", "catalog_name": "Mammoth Mountain", "state": "CA",
     "timezone": "America/Los_Angeles", "climate_regime": "sierra_maritime_mountain"},
    {"pilot_name": "Alta", "catalog_name": "Alta Ski Area", "state": "UT",
     "timezone": "America/Denver", "climate_regime": "continental_mountain"},
    {"pilot_name": "Jackson Hole", "catalog_name": "Jackson Hole Mountain Resort", "state": "WY",
     "timezone": "America/Denver", "climate_regime": "continental_mountain"},
    {"pilot_name": "Aspen", "catalog_name": "Aspen Mountain", "state": "CO",
     "timezone": "America/Denver", "climate_regime": "continental_mountain"},
    {"pilot_name": "Big Sky", "catalog_name": "Big Sky Resort", "state": "MT",
     "timezone": "America/Denver", "climate_regime": "continental_mountain"},
    {"pilot_name": "Mt. Baker", "catalog_name": "Mt Baker", "state": "WA",
     "timezone": "America/Los_Angeles", "climate_regime": "pnw_maritime_mountain"},
    {"pilot_name": "Stevens Pass", "catalog_name": "Stevens Pass Ski Area", "state": "WA",
     "timezone": "America/Los_Angeles", "climate_regime": "pnw_maritime_mountain"},
    {"pilot_name": "Stowe", "catalog_name": "Stowe Mountain Resort", "state": "VT",
     "timezone": "America/New_York", "climate_regime": "northeast_cold_orographic"},
    {"pilot_name": "Sugarloaf", "catalog_name": "Sugarloaf", "state": "ME",
     "timezone": "America/New_York", "climate_regime": "northeast_cold_orographic"},
]

# Curated Palisades Tahoe reference points, copied verbatim from the app
# default location in src/lib/api.ts DEFAULT_LOCATIONS["palisades"]
# (geometrySource "curated"); the anchor is the mid-mountain point.
PALISADES_CURATED_POINTS: list[dict[str, Any]] = [
    {"role": "base", "name": "Village base", "latitude": 39.196999, "longitude": -120.24313, "elevation_m": 1889.0},
    {"role": "mid_mountain", "name": "High Camp", "latitude": 39.195899, "longitude": -120.260845, "elevation_m": 2499.0},
    {"role": "summit", "name": "Upper mountain", "latitude": 39.181764, "longitude": -120.266494, "elevation_m": 2708.0},
]
PALISADES_ANCHOR = {"latitude": 39.195899, "longitude": -120.260845, "elevation_m": 2499.0}


def slugify(name: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-").replace("--", "-")


def derive_forecast_points(
    resort_slug: str,
    latitude: float,
    longitude: float,
    min_elevation_m: float,
    max_elevation_m: float,
) -> list[dict[str, Any]]:
    """Mirror src/lib/snow/resort/geometry.ts resortReferencePoints().

    Without curated points the app places base/mid/summit at the resort
    coordinates with min / mean / max elevations (geometrySource "estimated").
    """
    roles = [
        ("base", "Base estimate", min_elevation_m),
        ("mid_mountain", "Mid-mountain estimate", (min_elevation_m + max_elevation_m) / 2.0),
        ("summit", "Summit estimate", max_elevation_m),
    ]
    return [
        {
            "point_id": f"{resort_slug}-{role}",
            "role": role,
            "name": name,
            "latitude": latitude,
            "longitude": longitude,
            "elevation_m": elevation,
            "geometry_source": "estimated",
        }
        for role, name, elevation in roles
    ]


def load_catalog_entry(ski_areas_path: Path, catalog_name: str, expected_state_region: str) -> dict[str, Any]:
    areas = json.loads(ski_areas_path.read_text(encoding="utf-8"))
    matches = [
        area for area in areas
        if area.get("name") == catalog_name and area.get("country") == "United States"
    ]
    if not matches:
        raise ValueError(f"Catalog entry {catalog_name!r} not found in {ski_areas_path}")
    if len(matches) > 1:
        matches = [area for area in matches if str(area.get("region") or "") == expected_state_region] or matches
    return matches[0]


def filter_active_snotel(stations: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Keep active SNTL stations from a raw AWDB stations payload.

    The AWDB stations endpoint ignores server-side filters, so activity and
    network are enforced client-side.  AWDB reports elevation in feet.
    """
    active: list[dict[str, Any]] = []
    for station in stations:
        if station.get("networkCode") != "SNTL":
            continue
        end_text = str(station.get("endDate") or "")[:10]
        try:
            end_date = date.fromisoformat(end_text)
        except ValueError:
            continue
        if end_date < today:
            continue
        latitude = finite(station.get("latitude"))
        longitude = finite(station.get("longitude"))
        elevation_ft = finite(station.get("elevation"))
        if latitude is None or longitude is None or elevation_ft is None:
            continue
        active.append({
            "station_triplet": str(station.get("stationTriplet") or ""),
            "station_name": str(station.get("name") or ""),
            "state": str(station.get("stateCode") or ""),
            "latitude": latitude,
            "longitude": longitude,
            "elevation_m": round(elevation_ft * FEET_TO_M, 1),
        })
    return active


def resolve_snotel_station(
    anchor_latitude: float,
    anchor_longitude: float,
    anchor_elevation_m: float,
    stations: list[dict[str, Any]],
    max_km: float = DEFAULT_MAX_STATION_KM,
) -> dict[str, Any] | None:
    """Nearest pre-filtered SNOTEL station within ``max_km`` of the anchor."""
    best: tuple[float, dict[str, Any]] | None = None
    for station in stations:
        distance = haversine_km(anchor_latitude, anchor_longitude, station["latitude"], station["longitude"])
        if distance <= max_km and (best is None or distance < best[0]):
            best = (distance, station)
    if best is None:
        return None
    distance, station = best
    return {
        **station,
        "distance_km": round(distance, 3),
        "elevation_delta_m": round(station["elevation_m"] - anchor_elevation_m, 1),
    }


def resolve_ghcn_stations(
    anchor_latitude: float,
    anchor_longitude: float,
    registry_rows: list[dict[str, str]],
    max_km: float = DEFAULT_MAX_STATION_KM,
) -> list[dict[str, Any]]:
    """GHCN paired stations from the unified registry within ``max_km``.

    Ranked by paired_records (most direct SNOW+WESF evidence first), then by
    distance.  Only ``ghcn_daily`` rows are eligible.
    """
    resolved: list[dict[str, Any]] = []
    for row in registry_rows:
        if row.get("source") != "ghcn_daily":
            continue
        latitude = finite(row.get("latitude"))
        longitude = finite(row.get("longitude"))
        elevation_m = finite(row.get("elevation_m"))
        paired = finite(row.get("paired_records"))
        if latitude is None or longitude is None or elevation_m is None:
            continue
        distance = haversine_km(anchor_latitude, anchor_longitude, latitude, longitude)
        if distance > max_km:
            continue
        resolved.append({
            "station_id": str(row.get("source_id") or ""),
            "station_name": str(row.get("name") or ""),
            "state": str(row.get("state") or ""),
            "latitude": latitude,
            "longitude": longitude,
            "elevation_m": elevation_m,
            "paired_records": int(paired) if paired is not None else 0,
            "network": str(row.get("network") or ""),
            "distance_km": round(distance, 3),
        })
    resolved.sort(key=lambda item: (-item["paired_records"], item["distance_km"]))
    return resolved


def build_registry(
    ski_areas_path: Path,
    station_registry_path: Path,
    fetcher: Callable[[str], Any] | None = None,
    today: date | None = None,
    max_station_km: float = DEFAULT_MAX_STATION_KM,
) -> dict[str, Any]:
    today = today or date.today()
    fetch = fetcher or (lambda url: fetch_json(url))
    raw_stations = fetch(AWDB_STATIONS_URL)
    if not isinstance(raw_stations, list):
        raise ValueError("AWDB stations payload is not a list")
    snotel_stations = filter_active_snotel(raw_stations, today)

    with station_registry_path.open("r", encoding="utf-8-sig", newline="") as handle:
        unified_rows = list(csv.DictReader(handle))

    resorts: list[dict[str, Any]] = []
    for pilot in PILOT_RESORTS:
        slug = slugify(str(pilot["pilot_name"]))
        if pilot["catalog_name"] is None:
            anchor = PALISADES_ANCHOR
            points = [
                {
                    "point_id": f"{slug}-{point['role']}",
                    "role": point["role"],
                    "name": point["name"],
                    "latitude": point["latitude"],
                    "longitude": point["longitude"],
                    "elevation_m": point["elevation_m"],
                    "geometry_source": "curated",
                }
                for point in PALISADES_CURATED_POINTS
            ]
            catalog_name = "Palisades Tahoe (app default location, src/lib/api.ts)"
            latitude, longitude = anchor["latitude"], anchor["longitude"]
            min_elevation = min(point["elevation_m"] for point in points)
            max_elevation = max(point["elevation_m"] for point in points)
            match_note = (
                "Curated app default location in src/lib/api.ts; catalog entries "
                "'Palisades Tahoe Olympic Valley'/'Palisades Tahoe Alpine Meadows' not used."
            )
        else:
            entry = load_catalog_entry(ski_areas_path, str(pilot["catalog_name"]), str(pilot.get("region") or ""))
            latitude = float(entry["lat"])
            longitude = float(entry["lon"])
            min_elevation = finite(entry.get("minElevationM"))
            max_elevation = finite(entry.get("maxElevationM"))
            if min_elevation is None or max_elevation is None:
                raise ValueError(f"Catalog entry {pilot['catalog_name']!r} lacks min/max elevation")
            anchor = {"latitude": latitude, "longitude": longitude, "elevation_m": (min_elevation + max_elevation) / 2.0}
            points = derive_forecast_points(slug, latitude, longitude, min_elevation, max_elevation)
            catalog_name = str(pilot["catalog_name"])
            match_note = f"Exact catalog name match in src/data/ski-areas.json ({entry.get('region')})."
        snotel = resolve_snotel_station(anchor["latitude"], anchor["longitude"], anchor["elevation_m"], snotel_stations, max_station_km)
        ghcn = resolve_ghcn_stations(anchor["latitude"], anchor["longitude"], unified_rows, max_station_km)
        resorts.append({
            "pilot_name": pilot["pilot_name"],
            "resort_slug": slug,
            "catalog_name": catalog_name,
            "match_note": match_note,
            "state": pilot["state"],
            "timezone": pilot["timezone"],
            "climate_regime": pilot["climate_regime"],
            "latitude": anchor["latitude"],
            "longitude": anchor["longitude"],
            "min_elevation_m": min_elevation,
            "max_elevation_m": max_elevation,
            "forecast_points": points,
            "snotel_station": snotel,
            "ghcn_stations": ghcn,
        })

    return {
        "schema_version": 1,
        "generated_for_date": today.isoformat(),
        "station_search_radius_km": max_station_km,
        "awdb_stations_url": AWDB_STATIONS_URL,
        "active_snotel_station_count": len(snotel_stations),
        "resorts": resorts,
    }


def write_registry(registry: dict[str, Any], json_path: Path, csv_path: Path | None = None) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if csv_path is not None:
        fieldnames = [
            "pilot_name", "resort_slug", "catalog_name", "state", "timezone", "climate_regime",
            "latitude", "longitude", "min_elevation_m", "max_elevation_m",
            "forecast_point_ids", "snotel_station_triplet", "snotel_distance_km",
            "snotel_elevation_delta_m", "ghcn_station_ids",
        ]
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for resort in registry["resorts"]:
                snotel = resort.get("snotel_station") or {}
                writer.writerow({
                    "pilot_name": resort["pilot_name"],
                    "resort_slug": resort["resort_slug"],
                    "catalog_name": resort["catalog_name"],
                    "state": resort["state"],
                    "timezone": resort["timezone"],
                    "climate_regime": resort["climate_regime"],
                    "latitude": resort["latitude"],
                    "longitude": resort["longitude"],
                    "min_elevation_m": resort["min_elevation_m"],
                    "max_elevation_m": resort["max_elevation_m"],
                    "forecast_point_ids": ";".join(point["point_id"] for point in resort["forecast_points"]),
                    "snotel_station_triplet": snotel.get("station_triplet", ""),
                    "snotel_distance_km": snotel.get("distance_km", ""),
                    "snotel_elevation_delta_m": snotel.get("elevation_delta_m", ""),
                    "ghcn_station_ids": ";".join(station["station_id"] for station in resort["ghcn_stations"]),
                })


def load_registry(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resort_for_station(registry: dict[str, Any], station_key: str) -> dict[str, Any] | None:
    """Find the resort a SNOTEL triplet or GHCN station id is attached to."""
    for resort in registry.get("resorts", []):
        snotel = resort.get("snotel_station") or {}
        if snotel.get("station_triplet") == station_key:
            return resort
        if any(station.get("station_id") == station_key for station in resort.get("ghcn_stations", [])):
            return resort
    return None
