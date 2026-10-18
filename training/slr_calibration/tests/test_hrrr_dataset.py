from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from slr_calibration.hrrr_dataset import (
    NoDataError,
    RateLimitedClient,
    aggregate_station_month,
    build_dataset,
    build_station_set,
    derive_station_days,
)
from slr_calibration.profiles import HOURLY_FIELDS, PRESSURE_LEVELS_HPA

INCH = 25.4


def legacy_row(day: str, swe: float, depth: float, precip: float, tavg: float, tmax: float = 28.0, tmin: float = 20.0) -> dict[str, str]:
    return {
        "station_triplet": "999:UT:SNTL",
        "station_name": "Test Station",
        "state": "UT",
        "latitude": "40.0",
        "longitude": "-111.0",
        "elevation_m": "2500.0",
        "date": day,
        "swe_inches": str(swe),
        "snow_depth_inches": str(depth),
        "precip_accum_inches": str(precip),
        "tavg_f": str(tavg),
        "tmax_f": str(tmax),
        "tmin_f": str(tmin),
    }


STATION = {
    "station_triplet": "999:UT:SNTL",
    "station_name": "Test Station",
    "state": "UT",
    "latitude": 40.0,
    "longitude": -111.0,
    "elevation_m": 2500.0,
    "data_tz_offset_hours": -7.0,
    "nearest_resort": "Test Resort",
    "nearest_resort_region": "Utah",
    "nearest_resort_country": "United States",
    "distance_km": 1.0,
}


class DeriveStationDaysTests(unittest.TestCase):
    def test_unit_conversions_and_deltas(self) -> None:
        rows = [
            legacy_row("2025-01-01", 10.0, 40.0, 20.0, 32.0),
            legacy_row("2025-01-02", 10.5, 41.0, 20.5, 30.0),
        ]
        derived = derive_station_days(STATION, rows)
        day2 = derived["2025-01-02"]
        self.assertAlmostEqual(day2["swe_mm"], 10.5 * INCH)
        self.assertAlmostEqual(day2["snow_depth_mm"], 41.0 * INCH)
        self.assertAlmostEqual(day2["delta_swe_mm"], 0.5 * INCH)
        self.assertAlmostEqual(day2["delta_depth_mm"], 1.0 * INCH)
        self.assertAlmostEqual(day2["precip_mm"], 0.5 * INCH)
        self.assertAlmostEqual(day2["tavg_c"], (30.0 - 32.0) * 5.0 / 9.0, places=4)
        self.assertEqual(day2["obs_flags"], "")

    def test_precip_water_year_reset_and_negative_glitch(self) -> None:
        rows = [
            legacy_row("2025-09-30", 5.0, 10.0, 50.0, 30.0),
            legacy_row("2025-10-01", 5.0, 10.0, 0.3, 30.0),
            legacy_row("2025-10-02", 5.0, 10.0, 0.2, 30.0),
        ]
        derived = derive_station_days(STATION, rows)
        oct1 = derived["2025-10-01"]
        self.assertAlmostEqual(oct1["precip_mm"], 0.3 * INCH)
        self.assertIn("precip_wy_reset", oct1["obs_flags"])
        oct2 = derived["2025-10-02"]
        self.assertIsNone(oct2["precip_mm"])
        self.assertIn("precip_negative", oct2["obs_flags"])

    def test_nonconsecutive_days_null_deltas(self) -> None:
        rows = [
            legacy_row("2025-01-01", 10.0, 40.0, 20.0, 30.0),
            legacy_row("2025-01-03", 11.0, 42.0, 21.0, 30.0),
        ]
        derived = derive_station_days(STATION, rows)
        day3 = derived["2025-01-03"]
        self.assertIsNone(day3["delta_swe_mm"])
        self.assertIsNone(day3["delta_depth_mm"])
        self.assertIsNone(day3["precip_mm"])
        self.assertIn("nonconsecutive_previous", day3["obs_flags"])
        self.assertEqual(day3["weak_qc_rejection"], "NON_CONSECUTIVE_DAYS")

    def test_weak_qc_accepts_clean_cold_storm_day(self) -> None:
        rows = [
            legacy_row("2025-01-01", 10.0, 40.0, 20.0, 25.0),
            legacy_row("2025-01-02", 10.4, 41.0, 20.4, 26.0, tmax=30.0),
        ]
        derived = derive_station_days(STATION, rows)
        day2 = derived["2025-01-02"]
        self.assertTrue(day2["weak_qc_passed"])
        self.assertIsNone(day2["weak_qc_rejection"])
        self.assertAlmostEqual(day2["observed_slr_diff"], 2.5)
        self.assertEqual(day2["weak_qc_weight"], 0.25)

    def test_weak_qc_rejects_warm_day(self) -> None:
        rows = [
            legacy_row("2025-01-01", 10.0, 40.0, 20.0, 25.0),
            legacy_row("2025-01-02", 10.4, 41.0, 20.4, 40.0, tmax=44.0),
        ]
        derived = derive_station_days(STATION, rows)
        day2 = derived["2025-01-02"]
        self.assertFalse(day2["weak_qc_passed"])
        self.assertEqual(day2["weak_qc_rejection"], "WARM_DAY")
        self.assertIsNone(day2["observed_slr_diff"])


def synthetic_payload(start: datetime, hours: int, snowfall: dict[str, float] | None = None,
                      precip_mm: float = 1.0, level_temp_c: float = -2.0,
                      surface_temp_c: float = -2.0) -> dict:
    times = [start + timedelta(hours=k) for k in range(hours)]
    snowfall = snowfall or {}
    hourly: dict[str, list] = {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in times]}
    hourly["temperature_2m"] = [surface_temp_c] * hours
    hourly["precipitation"] = [precip_mm] * hours
    hourly["snowfall"] = [snowfall.get(t.strftime("%Y-%m-%dT%H:%M"), 0.0) for t in times]
    hourly["freezing_level_height"] = [3000.0] * hours
    for index, level in enumerate(PRESSURE_LEVELS_HPA):
        hourly[f"temperature_{level}hPa"] = [level_temp_c] * hours
        hourly[f"geopotential_height_{level}hPa"] = [2500.0 + index * 800.0] * hours
    return {"latitude": 40.0, "longitude": -111.0, "elevation": 2500.0, "hourly": hourly}


class AggregateStationMonthTests(unittest.TestCase):
    def test_local_day_aggregation_across_utc_offset_boundary(self) -> None:
        # Station offset -8: the local Jan 1 window is (Jan 1 08Z, Jan 2 08Z].
        station = {**STATION, "data_tz_offset_hours": -8.0, "elevation_m": 2500.0}
        payload = synthetic_payload(
            datetime(2024, 12, 31, 0, 0), 24 * 4,
            snowfall={"2025-01-01T08:00": 5.0, "2025-01-02T07:00": 1.0, "2025-01-02T08:00": 2.0},
            precip_mm=0.0,
        )
        aggregated = aggregate_station_month(payload, station, 2025, 1)
        jan1 = aggregated["2025-01-01"]
        # The 08Z Jan 1 stamp is the window-start endpoint (previous interval),
        # so its 5 cm belongs to Dec 31 local; 07Z + 08Z on Jan 2 are the last
        # two interval ends of Jan 1 local.
        self.assertEqual(jan1["fcst_snowfall_cm"], 3.0)
        self.assertEqual(jan1["hrrr_hours"], 24)
        jan2 = aggregated["2025-01-02"]
        self.assertEqual(jan2["fcst_snowfall_cm"], 0.0)
        self.assertEqual(jan2["hrrr_hours"], 24)

    def test_kuchera_daily_aggregation_known_profile(self) -> None:
        # Constant -2 C above-ground profile: max temp aloft -2 C gives
        # Kuchera SLR 12.0 (12 + (271.16 - 271.15), JS-rounded to 1 dp).
        station = {**STATION, "data_tz_offset_hours": -7.0, "elevation_m": 2500.0}
        start = datetime(2025, 1, 1, 0, 0)
        times = [start + timedelta(hours=k) for k in range(24 * 3)]
        snowfall = {t.strftime("%Y-%m-%dT%H:%M"): 0.1 for t in times}
        payload = synthetic_payload(start, 24 * 3, snowfall=snowfall, precip_mm=1.0)
        aggregated = aggregate_station_month(payload, station, 2025, 1)
        jan2 = aggregated["2025-01-02"]
        self.assertEqual(jan2["fcst_kuchera_slr"], 12.0)
        self.assertEqual(jan2["fcst_kuchera_hours"], 24)
        self.assertAlmostEqual(jan2["fcst_kuchera_weight_mm"], 24.0)
        self.assertAlmostEqual(jan2["fcst_snowfall_cm"], 2.4)
        self.assertAlmostEqual(jan2["fcst_precip_mm"], 24.0)
        self.assertAlmostEqual(jan2["fcst_t2m_mean_c"], -2.0)

    def test_no_payload_yields_zero_hours(self) -> None:
        aggregated = aggregate_station_month(None, STATION, 2025, 1)
        self.assertEqual(aggregated["2025-01-15"]["hrrr_hours"], 0)
        self.assertIsNone(aggregated["2025-01-15"]["fcst_snowfall_cm"])


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class RateLimitedClientTests(unittest.TestCase):
    def test_429_honors_retry_after_and_recovers(self) -> None:
        clock = FakeClock()
        attempts = {"count": 0}

        def flaky(url: str) -> bytes:
            attempts["count"] += 1
            if attempts["count"] <= 2:
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {"Retry-After": "0"}, None)
            return b"ok"

        client = RateLimitedClient(flaky, min_interval_s=2.0, sleep=clock.sleep, clock=clock.time)
        self.assertEqual(client.get_bytes("https://example.test/x"), b"ok")
        self.assertEqual(client.attempts, 3)
        self.assertEqual(client.retries, 2)
        self.assertIn(0.0, clock.sleeps)  # Retry-After: 0 honored for the backoff itself

    def test_400_raises_no_data_without_retry(self) -> None:
        clock = FakeClock()

        def bad(url: str) -> bytes:
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)

        client = RateLimitedClient(bad, min_interval_s=0.0, sleep=clock.sleep, clock=clock.time)
        with self.assertRaises(NoDataError):
            client.get_bytes("https://example.test/x")
        self.assertEqual(client.attempts, 1)


class StationSetTests(unittest.TestCase):
    def test_filters_network_activity_and_distance(self) -> None:
        raw_stations = [
            {"stationTriplet": "999:UT:SNTL", "networkCode": "SNTL", "name": "Near", "stateCode": "UT",
             "latitude": 40.0, "longitude": -111.0, "elevation": 8202.0,
             "beginDate": "2000-10-01 00:00", "endDate": "2100-01-01 00:00", "dataTimeZone": -7.0},
            {"stationTriplet": "998:UT:SNTL", "networkCode": "SNTL", "name": "Retired", "stateCode": "UT",
             "latitude": 40.0, "longitude": -111.0, "elevation": 8202.0,
             "beginDate": "1990-10-01 00:00", "endDate": "2020-01-01 00:00", "dataTimeZone": -7.0},
            {"stationTriplet": "997:UT:SNOW", "networkCode": "SNOW", "name": "Snow Course", "stateCode": "UT",
             "latitude": 40.0, "longitude": -111.0, "elevation": 8202.0,
             "beginDate": "1990-10-01 00:00", "endDate": "2100-01-01 00:00", "dataTimeZone": -7.0},
            {"stationTriplet": "996:UT:SNTL", "networkCode": "SNTL", "name": "Far", "stateCode": "UT",
             "latitude": 42.0, "longitude": -111.0, "elevation": 8202.0,
             "beginDate": "1990-10-01 00:00", "endDate": "2100-01-01 00:00", "dataTimeZone": -7.0},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "ski-areas.json"
            catalog.write_text(json.dumps([
                {"name": "Test Resort", "country": "United States", "region": "Utah",
                 "lat": "40.001", "lon": "-111.001", "minElevationM": 2000, "maxElevationM": 3000},
            ]), encoding="utf-8")
            stations = build_station_set(catalog, raw_stations, date(2026, 10, 17))
        self.assertEqual([s["station_triplet"] for s in stations], ["999:UT:SNTL"])
        self.assertEqual(stations[0]["data_tz_offset_hours"], -7.0)
        self.assertEqual(stations[0]["nearest_resort"], "Test Resort")
        self.assertLess(stations[0]["distance_km"], 0.2)


# --- End-to-end tiny build with fake fetchers -------------------------------

RAW_STATIONS = [
    {"stationTriplet": "999:UT:SNTL", "networkCode": "SNTL", "name": "Test Station", "stateCode": "UT",
     "latitude": 40.0, "longitude": -111.0, "elevation": 8202.0,
     "beginDate": "2000-10-01 00:00", "endDate": "2100-01-01 00:00", "dataTimeZone": -7.0},
    {"stationTriplet": "998:AK:SNTL", "networkCode": "SNTL", "name": "Alaska Station", "stateCode": "AK",
     "latitude": 61.0, "longitude": -149.0, "elevation": 1500.0,
     "beginDate": "2000-10-01 00:00", "endDate": "2100-01-01 00:00", "dataTimeZone": -9.0},
]

CATALOG = [
    {"name": "Test Resort", "country": "United States", "region": "Utah",
     "lat": "40.001", "lon": "-111.001", "minElevationM": 2000, "maxElevationM": 3000},
    {"name": "Alaska Resort", "country": "United States", "region": "Alaska",
     "lat": "61.001", "lon": "-149.001", "minElevationM": 400, "maxElevationM": 800},
]


def awdb_data_payload() -> list:
    blocks = []
    for triplet in ("999:UT:SNTL", "998:AK:SNTL"):
        elements = []
        for code, base in (("WTEQ", 10.0), ("SNWD", 40.0), ("PREC", 20.0)):
            elements.append({
                "stationElement": {"elementCode": code, "storedUnitCode": "in"},
                "values": [{"date": f"2025-01-0{d}", "value": base + 0.1 * d} for d in range(1, 6)],
            })
        for code in ("TAVG", "TMAX", "TMIN"):
            elements.append({
                "stationElement": {"elementCode": code, "storedUnitCode": "degF"},
                "values": [{"date": f"2025-01-0{d}", "value": 25.0} for d in range(1, 6)],
            })
        blocks.append({"stationTriplet": triplet, "data": elements})
    return blocks


def om_payloads(count: int) -> list:
    start = datetime(2024, 12, 31, 0, 0)
    times = [start + timedelta(hours=k) for k in range(24 * 33)]
    snowfall = {t.strftime("%Y-%m-%dT%H:%M"): 0.1 for t in times}
    return [synthetic_payload(start, 24 * 33, snowfall=snowfall, precip_mm=1.0) for _ in range(count)]


class BuildDatasetEndToEndTests(unittest.TestCase):
    def _run_build(self, tmp: str, calls: list[str], om_bad_lat: float | None = None) -> dict:
        root = Path(tmp)
        catalog = root / "ski-areas.json"
        catalog.write_text(json.dumps(CATALOG), encoding="utf-8")

        def fetcher(url: str) -> bytes:
            calls.append(url)
            if "awdbRestApi/services/v1/stations" in url:
                return json.dumps(RAW_STATIONS).encode()
            if "awdbRestApi/services/v1/data" in url:
                return json.dumps(awdb_data_payload()).encode()
            if "historical-forecast-api" in url:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                lats = query["latitude"][0].split(",")
                if om_bad_lat is not None:
                    if len(lats) > 1:
                        raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)
                    if abs(float(lats[0]) - om_bad_lat) < 1e-6:
                        raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)
                    return json.dumps(om_payloads(1)[0]).encode()
                return json.dumps(om_payloads(len(lats))).encode()
            raise AssertionError(f"Unexpected URL {url}")

        return build_dataset(
            ski_areas_path=catalog,
            dataset_dir=root / "dataset",
            obs_start=date(2025, 1, 1),
            obs_end=date(2025, 1, 5),
            bytes_fetcher=fetcher,
            awdb_interval_s=0.0,
            open_meteo_interval_s=0.0,
            hrrr_months=[(2025, 1)],
            today=date(2026, 10, 17),
            progress=lambda _msg: None,
        )

    def test_build_and_cache_resume_makes_zero_requests(self) -> None:
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as tmp:
            calls: list[str] = []
            report = self._run_build(tmp, calls)
            self.assertEqual(report["stations"]["count"], 2)
            # 1 stations list + 1 AWDB data chunk + 1 Open-Meteo chunk.
            self.assertEqual(len(calls), 3)
            self.assertEqual(report["requests"]["awdb"]["logical_requests"], 2)
            self.assertEqual(report["requests"]["open_meteo"]["logical_requests"], 1)

            dataset_dir = Path(tmp) / "dataset"
            parquet_path = dataset_dir / "daily" / "water_year=2025" / "data.parquet"
            self.assertTrue(parquet_path.exists())
            table = pq.read_table(parquet_path)
            rows = table.to_pylist()
            by_key = {(row["station_triplet"], row["date"]): row for row in rows}
            utah = by_key[("999:UT:SNTL", "2025-01-03")]
            self.assertTrue(utah["hrrr_available"])
            self.assertEqual(utah["hrrr_status"], "ok")
            self.assertEqual(utah["hrrr_hours"], 24)
            self.assertAlmostEqual(utah["fcst_snowfall_cm"], 2.4)
            self.assertEqual(utah["fcst_kuchera_slr"], 12.0)
            self.assertAlmostEqual(utah["swe_mm"], (10.0 + 0.3) * INCH)
            alaska = by_key[("998:AK:SNTL", "2025-01-03")]
            self.assertFalse(alaska["hrrr_available"])
            self.assertEqual(alaska["hrrr_status"], "not_requested")
            self.assertIsNone(alaska["hrrr_hours"])

            calls_second: list[str] = []
            self._run_build(tmp, calls_second)
            self.assertEqual(calls_second, [], "complete cache must mean zero network requests")

    def test_bisection_isolates_no_data_station(self) -> None:
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as tmp:
            calls: list[str] = []
            # Utah station (lat 40.0) has no HRRR data; the batch 400s, the
            # good single succeeds, the bad single 400s again.
            raw_extra = RAW_STATIONS + [{
                "stationTriplet": "997:UT:SNTL", "networkCode": "SNTL", "name": "Good Station", "stateCode": "UT",
                "latitude": 40.05, "longitude": -111.05, "elevation": 8202.0,
                "beginDate": "2000-10-01 00:00", "endDate": "2100-01-01 00:00", "dataTimeZone": -7.0,
            }]
            catalog = Path(tmp) / "ski-areas.json"
            catalog.write_text(json.dumps(CATALOG + [{
                "name": "Good Resort", "country": "United States", "region": "Utah",
                "lat": "40.051", "lon": "-111.051", "minElevationM": 2000, "maxElevationM": 3000,
            }]), encoding="utf-8")
            root = Path(tmp)

            def fetcher(url: str) -> bytes:
                calls.append(url)
                if "awdbRestApi/services/v1/stations" in url:
                    return json.dumps(raw_extra).encode()
                if "awdbRestApi/services/v1/data" in url:
                    return json.dumps(awdb_data_payload()).encode()
                if "historical-forecast-api" in url:
                    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                    lats = [float(v) for v in query["latitude"][0].split(",")]
                    if len(lats) > 1:
                        raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)
                    if abs(lats[0] - 40.0) < 1e-6:
                        raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)
                    return json.dumps(om_payloads(1)[0]).encode()
                raise AssertionError(f"Unexpected URL {url}")

            report = build_dataset(
                ski_areas_path=catalog,
                dataset_dir=root / "dataset",
                obs_start=date(2025, 1, 1),
                obs_end=date(2025, 1, 5),
                bytes_fetcher=fetcher,
                awdb_interval_s=0.0,
                open_meteo_interval_s=0.0,
                hrrr_months=[(2025, 1)],
                today=date(2026, 10, 17),
                progress=lambda _msg: None,
            )
            table = pq.read_table(Path(tmp) / "dataset" / "daily" / "water_year=2025" / "data.parquet")
            rows = table.to_pylist()
            by_key = {(row["station_triplet"], row["date"]): row for row in rows}
            bad = by_key[("999:UT:SNTL", "2025-01-03")]
            self.assertEqual(bad["hrrr_status"], "no_coverage")
            self.assertFalse(bad["hrrr_available"])
            good = by_key[("997:UT:SNTL", "2025-01-03")]
            self.assertEqual(good["hrrr_status"], "ok")
            self.assertTrue(good["hrrr_available"])
            self.assertEqual(report["stations"]["no_coverage_stations"], ["999:UT:SNTL"])


if __name__ == "__main__":
    unittest.main()
