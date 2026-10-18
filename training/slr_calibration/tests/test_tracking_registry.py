from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from slr_calibration.tracking_registry import (
    build_registry,
    derive_forecast_points,
    filter_active_snotel,
    resolve_ghcn_stations,
    resolve_snotel_station,
)

CATALOG = [
    {"name": "Mammoth Mountain", "country": "United States", "region": "California", "lat": "37.640130", "lon": "-119.019104", "minElevationM": 2461, "maxElevationM": 3365},
    {"name": "Alta Ski Area", "country": "United States", "region": "Utah", "lat": "40.579932", "lon": "-111.622327", "minElevationM": 2605, "maxElevationM": 3219},
    {"name": "Jackson Hole Mountain Resort", "country": "United States", "region": "Wyoming", "lat": "43.603245", "lon": "-110.849982", "minElevationM": 1931, "maxElevationM": 3169},
    {"name": "Aspen Mountain", "country": "United States", "region": "Colorado", "lat": "39.165589", "lon": "-106.819323", "minElevationM": 2429, "maxElevationM": 3438},
    {"name": "Big Sky Resort", "country": "United States", "region": "Montana", "lat": "45.273921", "lon": "-111.419449", "minElevationM": 2080, "maxElevationM": 3400},
    {"name": "Mt Baker", "country": "United States", "region": "Washington", "lat": "48.857115", "lon": "-121.665157", "minElevationM": 1109, "maxElevationM": 1554},
    {"name": "Stevens Pass Ski Area", "country": "United States", "region": "Washington", "lat": "47.737573", "lon": "-121.090430", "minElevationM": 1168, "maxElevationM": 1731},
    {"name": "Stowe Mountain Resort", "country": "United States", "region": "Vermont", "lat": "44.530360", "lon": "-72.788162", "minElevationM": 397, "maxElevationM": 1109},
    {"name": "Sugarloaf", "country": "United States", "region": "Maine", "lat": "45.046037", "lon": "-70.314710", "minElevationM": 425, "maxElevationM": 1268},
]

AWDB_STATIONS = [
    # Inactive SNTL very close to Mammoth: must be skipped.
    {"stationTriplet": "999:CA:SNTL", "name": "Dead Station", "stateCode": "CA", "networkCode": "SNTL",
     "latitude": 37.641, "longitude": -119.020, "elevation": 9000.0,
     "beginDate": "1990-10-01 00:00", "endDate": "2020-01-01 00:00"},
    # SNOW (snow course) network at the same spot: not SNTL, must be skipped.
    {"stationTriplet": "888:CA:SNOW", "name": "Snow Course", "stateCode": "CA", "networkCode": "SNOW",
     "latitude": 37.641, "longitude": -119.020, "elevation": 9000.0,
     "beginDate": "1990-10-01 00:00", "endDate": "2100-01-01 00:00"},
    # Active SNTL near Mammoth: the winner.
    {"stationTriplet": "777:CA:SNTL", "name": "Mammoth Pass", "stateCode": "CA", "networkCode": "SNTL",
     "latitude": 37.65, "longitude": -119.03, "elevation": 10155.0,
     "beginDate": "1980-10-01 00:00", "endDate": "2100-01-01 00:00"},
    # Active SNTL near Alta.
    {"stationTriplet": "555:UT:SNTL", "name": "Brighton", "stateCode": "UT", "networkCode": "SNTL",
     "latitude": 40.59, "longitude": -111.61, "elevation": 8800.0,
     "beginDate": "1990-10-01 00:00", "endDate": "2100-01-01 00:00"},
]

UNIFIED_ROWS = [
    {"internal_id": "ghcn_A", "source": "ghcn_daily", "source_id": "US1UTSL0001", "name": "ALTA 1", "state": "UT",
     "latitude": "40.585", "longitude": "-111.63", "elevation_m": "2700", "paired_records": "10", "network": "CoCoRaHS", "measurement_environment": ""},
    {"internal_id": "ghcn_B", "source": "ghcn_daily", "source_id": "US1UTSL0002", "name": "ALTA 2", "state": "UT",
     "latitude": "40.575", "longitude": "-111.615", "elevation_m": "2650", "paired_records": "50", "network": "CoCoRaHS", "measurement_environment": ""},
    {"internal_id": "ghcn_far", "source": "ghcn_daily", "source_id": "US1COAD0100", "name": "FAR", "state": "CO",
     "latitude": "39.9", "longitude": "-105.0", "elevation_m": "1600", "paired_records": "500", "network": "CoCoRaHS", "measurement_environment": ""},
    {"internal_id": "snotel_row", "source": "snotel", "source_id": "555:UT:SNTL", "name": "SNOTEL ROW", "state": "UT",
     "latitude": "40.59", "longitude": "-111.61", "elevation_m": "2682", "paired_records": "900", "network": "SNOTEL", "measurement_environment": ""},
]


class TrackingRegistryTests(unittest.TestCase):
    def test_filter_active_snotel_skips_inactive_and_non_sntl(self) -> None:
        active = filter_active_snotel(AWDB_STATIONS, date(2026, 10, 17))
        triplets = {station["station_triplet"] for station in active}
        self.assertEqual(triplets, {"777:CA:SNTL", "555:UT:SNTL"})
        mammoth = next(station for station in active if station["station_triplet"] == "777:CA:SNTL")
        self.assertAlmostEqual(mammoth["elevation_m"], 10155.0 * 0.3048, places=1)

    def test_resolve_snotel_picks_nearest_within_radius(self) -> None:
        active = filter_active_snotel(AWDB_STATIONS, date(2026, 10, 17))
        resolved = resolve_snotel_station(37.640130, -119.019104, 2913.0, active)
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved["station_triplet"], "777:CA:SNTL")
        self.assertLess(resolved["distance_km"], 5.0)
        self.assertIsNone(resolve_snotel_station(0.0, 0.0, 0.0, active))

    def test_resolve_ghcn_ranks_by_paired_records(self) -> None:
        resolved = resolve_ghcn_stations(40.579932, -111.622327, UNIFIED_ROWS)
        self.assertEqual([row["station_id"] for row in resolved], ["US1UTSL0002", "US1UTSL0001"])

    def test_triad_derivation_matches_app_geometry(self) -> None:
        points = derive_forecast_points("mammoth-mountain", 37.64013, -119.019104, 2461.0, 3365.0)
        self.assertEqual([point["role"] for point in points], ["base", "mid_mountain", "summit"])
        self.assertEqual(points[0]["elevation_m"], 2461.0)
        self.assertEqual(points[1]["elevation_m"], 2913.0)
        self.assertEqual(points[2]["elevation_m"], 3365.0)
        self.assertTrue(all(point["geometry_source"] == "estimated" for point in points))

    def test_build_registry_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ski_areas = root / "ski-areas.json"
            ski_areas.write_text(json.dumps(CATALOG), encoding="utf-8")
            unified = root / "unified.csv"
            with unified.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(UNIFIED_ROWS[0]))
                writer.writeheader()
                writer.writerows(UNIFIED_ROWS)
            registry = build_registry(ski_areas, unified, fetcher=lambda url: AWDB_STATIONS, today=date(2026, 10, 17))
        resorts = {resort["pilot_name"]: resort for resort in registry["resorts"]}
        self.assertEqual(len(resorts), 10)
        palisades = resorts["Palisades Tahoe"]
        self.assertEqual([point["elevation_m"] for point in palisades["forecast_points"]], [1889.0, 2499.0, 2708.0])
        self.assertTrue(all(point["geometry_source"] == "curated" for point in palisades["forecast_points"]))
        # Exact curated coordinates/names from src/lib/api.ts DEFAULT_LOCATIONS.
        self.assertEqual(
            [(point["name"], point["latitude"], point["longitude"]) for point in palisades["forecast_points"]],
            [
                ("Village base", 39.196999, -120.24313),
                ("High Camp", 39.195899, -120.260845),
                ("Upper mountain", 39.181764, -120.266494),
            ],
        )
        mammoth = resorts["Mammoth Mountain"]
        assert mammoth["snotel_station"] is not None
        self.assertEqual(mammoth["snotel_station"]["station_triplet"], "777:CA:SNTL")
        alta = resorts["Alta"]
        self.assertEqual([s["station_id"] for s in alta["ghcn_stations"]], ["US1UTSL0002", "US1UTSL0001"])
        baker = resorts["Mt. Baker"]
        self.assertEqual(baker["catalog_name"], "Mt Baker")


if __name__ == "__main__":
    unittest.main()
