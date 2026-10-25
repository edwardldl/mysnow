from __future__ import annotations

import gzip
import importlib.util
import json
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from slr_calibration.gold_profiles import (
    CDS_PROFILE_SOURCE,
    CDS_SPEC,
    _cache_valid,
    _chunk_path,
    _payload_hours,
    build_event_features,
    plan_chunks,
)
from slr_calibration.gold_profiles_cds import (
    PL_DATASET,
    SL_DATASET,
    accumulation_init_cds,
    fetch_profiles_cds,
    hourly_accumulation_cds,
    load_day_cache,
    plan_jobs,
    pl_request,
    region_box,
    sl_request,
    write_day_cache,
)
from slr_calibration.gold_profiles_era5 import GRAVITY_M_S2

UTC = timezone.utc
HAVE_XARRAY = importlib.util.find_spec("xarray") is not None
STANDARD_HEIGHTS = {
    1000: 110.0, 975: 320.0, 950: 500.0, 925: 760.0, 900: 990.0,
    850: 1450.0, 800: 1950.0, 750: 2460.0, 700: 3010.0, 650: 3590.0,
    600: 4200.0, 550: 4860.0, 500: 5570.0,
}
LEVELS = sorted(STANDARD_HEIGHTS, reverse=True)


class RequestConstructionTest(unittest.TestCase):
    def test_region_box_snaps_outward(self) -> None:
        box = region_box([("S1", 39.5, -105.5)])
        self.assertEqual(box, (40.25, -106.25, 38.75, -104.75))

    def test_region_box_alaska_negative_longitudes(self) -> None:
        box = region_box([("S1", 64.0, -150.0), ("S2", 61.0, -147.0)])
        self.assertEqual(box, (64.75, -150.75, 60.25, -146.25))

    def test_pl_request(self) -> None:
        box = region_box([("S1", 39.5, -105.5)])
        request = pl_request(date(1999, 1, 14), box)
        self.assertEqual(request["product_type"], ["reanalysis"])
        self.assertEqual(request["year"], ["1999"])
        self.assertEqual(request["month"], ["01"])
        self.assertEqual(request["day"], ["14"])
        self.assertEqual(len(request["time"]), 24)
        self.assertEqual(request["time"][0], "00:00")
        self.assertEqual(request["time"][-1], "23:00")
        self.assertEqual(request["area"], [40.25, -106.25, 38.75, -104.75])
        self.assertEqual(request["data_format"], "netcdf")
        self.assertEqual(
            set(request["variable"]),
            {"temperature", "relative_humidity", "u_component_of_wind",
             "v_component_of_wind", "geopotential"},
        )
        self.assertEqual(request["pressure_level"], [str(level) for level in LEVELS])

    def test_sl_request(self) -> None:
        box = region_box([("S1", 39.5, -105.5)])
        request = sl_request(date(2023, 12, 3), box)
        self.assertEqual(
            set(request["variable"]),
            {"2m_temperature", "convective_precipitation",
             "large_scale_precipitation", "snowfall"},
        )
        self.assertNotIn("pressure_level", request)
        self.assertEqual(request["day"], ["03"])


class AccumulationInitCdsTest(unittest.TestCase):
    def test_cycle_selection(self) -> None:
        cases = [
            (datetime(2023, 1, 14, 1, tzinfo=UTC), datetime(2023, 1, 14, 0, tzinfo=UTC), 1),
            (datetime(2023, 1, 14, 7, tzinfo=UTC), datetime(2023, 1, 14, 0, tzinfo=UTC), 7),
            (datetime(2023, 1, 14, 12, tzinfo=UTC), datetime(2023, 1, 14, 0, tzinfo=UTC), 12),
            (datetime(2023, 1, 14, 13, tzinfo=UTC), datetime(2023, 1, 14, 12, tzinfo=UTC), 1),
            (datetime(2023, 1, 14, 23, tzinfo=UTC), datetime(2023, 1, 14, 12, tzinfo=UTC), 11),
            (datetime(2023, 1, 15, 0, tzinfo=UTC), datetime(2023, 1, 14, 12, tzinfo=UTC), 12),
        ]
        for valid, init, forecast_hour in cases:
            self.assertEqual(accumulation_init_cds(valid), (init, forecast_hour))

    def test_hourly_differencing(self) -> None:
        init = datetime(2023, 1, 14, 0, tzinfo=UTC)
        cumulative = {("S1", init, 1): 0.004, ("S1", init, 2): 0.010, ("S1", init, 3): None}
        # fh == 1: the step-1 accumulation is the hour's own total.
        self.assertAlmostEqual(hourly_accumulation_cds(cumulative, "S1", init + timedelta(hours=1)), 0.004)
        self.assertAlmostEqual(hourly_accumulation_cds(cumulative, "S1", init + timedelta(hours=2)), 0.006)
        self.assertIsNone(hourly_accumulation_cds(cumulative, "S1", init + timedelta(hours=3)))
        self.assertIsNone(hourly_accumulation_cds(cumulative, "S1", init + timedelta(hours=4)))
        # Negative differences (reanalysis noise) clamp to zero.
        clamped = {("S1", init, 1): 0.010, ("S1", init, 2): 0.009}
        self.assertEqual(hourly_accumulation_cds(clamped, "S1", init + timedelta(hours=2)), 0.0)


def _event(event_id: str, station_id: str, region: str, day: str, lat: float, lon: float) -> dict:
    return {
        "event_id": event_id,
        "region": region,
        "station_id": station_id,
        "site_id": station_id,
        "station_name": "Test",
        "state": "CO",
        "valid_time": datetime.fromisoformat(f"{day}T07:00:00-06:00"),
        "observation_date": day,
        "water_year": 2023,
        "month": 1,
        "latitude": lat,
        "longitude": lon,
        "elevation_m": 1000.0,
        "observed_slr": 15.0,
        "fresh_snow_mm": 150.0,
        "fresh_swe_mm": 10.0,
        "climate_regime": "continental_mountain",
        "source_tier": "ghcn-coop",
    }


class PlanJobsTest(unittest.TestCase):
    def test_grouping_and_oldest_first(self) -> None:
        events = [
            _event("e:new", "TEST0001", "rockies", "2023-01-15", 39.5, -105.5),
            _event("e:old", "TEST0002", "rockies", "2005-01-15", 40.0, -106.0),
            _event("e:ak", "TEST0003", "alaska", "2005-01-15", 64.0, -150.0),
        ]
        chunks = plan_chunks(events, spec=CDS_SPEC)
        station_region = {e["station_id"]: e["region"] for e in events}
        station_coords = {e["station_id"]: (e["latitude"], e["longitude"]) for e in events}
        with tempfile.TemporaryDirectory() as tmp:
            jobs = plan_jobs(chunks, station_region, station_coords, Path(tmp))
        days = [job["day"] for job in jobs]
        self.assertEqual(days, sorted(days))
        self.assertEqual(days[0], date(2005, 1, 13))
        by_day_region = {(job["day"], job["region"]) for job in jobs}
        self.assertIn((date(2005, 1, 15), "alaska"), by_day_region)
        self.assertIn((date(2023, 1, 15), "rockies"), by_day_region)
        # A station appears only in its own region's jobs.
        for job in jobs:
            for station_id, _lat, _lon in job["stations"]:
                self.assertEqual(station_region[station_id], job["region"])

    def test_cached_stations_are_excluded(self) -> None:
        events = [
            _event("e:1", "TEST0001", "rockies", "2023-01-15", 39.5, -105.5),
            _event("e:2", "TEST0002", "rockies", "2023-01-15", 40.0, -106.0),
        ]
        chunks = plan_chunks(events, spec=CDS_SPEC)
        station_region = {e["station_id"]: e["region"] for e in events}
        station_coords = {e["station_id"]: (e["latitude"], e["longitude"]) for e in events}
        with tempfile.TemporaryDirectory() as tmp:
            day_cache = Path(tmp)
            instant = datetime(2023, 1, 15, tzinfo=UTC)
            record = {
                "temperature2mC": -5.0, "lspCumM": 0.0, "cpCumM": 0.0, "sfCumM": 0.0,
                "levels": {level: (-5.0, 80.0, 5.0, h) for level, h in STANDARD_HEIGHTS.items()},
            }
            write_day_cache(day_cache, "rockies", date(2023, 1, 15),
                            {"TEST0001": {instant: record}})
            jobs = plan_jobs(chunks, station_region, station_coords, day_cache)
            target = [j for j in jobs if j["day"] == date(2023, 1, 15)]
            self.assertEqual(len(target), 1)
            self.assertEqual([s[0] for s in target[0]["stations"]], ["TEST0002"])
            loaded = load_day_cache(day_cache, "rockies", date(2023, 1, 15))
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded["TEST0001"][instant]["levels"][850], (-5.0, 80.0, 5.0, 1450.0))


class DayCacheTest(unittest.TestCase):
    def test_corrupt_cache_reads_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            day_cache = Path(tmp)
            path = day_cache / "rockies" / "2023-01-15.json.gz"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"not gzip")
            self.assertIsNone(load_day_cache(day_cache, "rockies", date(2023, 1, 15)))


def _grid(request: dict) -> tuple[list[float], list[float]]:
    north, west, south, east = request["area"]
    count_lat = int(round((north - south) / 0.25)) + 1
    count_lon = int(round((east - west) / 0.25)) + 1
    lats = [north - 0.25 * i for i in range(count_lat)]
    lons = [west + 0.25 * i for i in range(count_lon)]
    return lats, lons


def _day_times(request: dict):
    import numpy as np

    day = date(int(request["year"][0]), int(request["month"][0]), int(request["day"][0]))
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return np.array([start + timedelta(hours=h) for h in range(24)], dtype="datetime64[ns]")


def _write_pl_netcdf(path: Path, request: dict) -> None:
    import numpy as np
    import xarray as xr

    lats, lons = _grid(request)
    shape = (24, len(LEVELS), len(lats), len(lons))
    heights = np.array([STANDARD_HEIGHTS[level] for level in LEVELS])
    data_vars = {
        "t": (("valid_time", "pressure_level", "latitude", "longitude"), np.full(shape, 268.15)),
        "r": (("valid_time", "pressure_level", "latitude", "longitude"), np.full(shape, 80.0)),
        "u": (("valid_time", "pressure_level", "latitude", "longitude"), np.full(shape, 3.0)),
        "v": (("valid_time", "pressure_level", "latitude", "longitude"), np.full(shape, 4.0)),
        "z": (("valid_time", "pressure_level", "latitude", "longitude"),
              np.broadcast_to((heights * GRAVITY_M_S2)[None, :, None, None], shape).copy()),
    }
    coords = {
        "valid_time": _day_times(request),
        "pressure_level": LEVELS,
        "latitude": lats,
        "longitude": lons,
    }
    xr.Dataset(data_vars, coords).to_netcdf(path, engine="h5netcdf")


def _write_sl_netcdf(path: Path, request: dict, as_zip: bool) -> None:
    import numpy as np
    import xarray as xr

    lats, lons = _grid(request)
    shape = (24, len(lats), len(lons))
    day = date(int(request["year"][0]), int(request["month"][0]), int(request["day"][0]))
    day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    step = np.array([
        accumulation_init_cds(day_start + timedelta(hours=h))[1] for h in range(24)
    ], dtype=float)
    coords = {"valid_time": _day_times(request), "latitude": lats, "longitude": lons}
    instant = xr.Dataset(
        {"t2m": (("valid_time", "latitude", "longitude"), np.full(shape, 268.15))}, coords)
    accum = xr.Dataset({
        "lsp": (("valid_time", "latitude", "longitude"), np.broadcast_to((0.001 * step)[:, None, None], shape).copy()),
        "cp": (("valid_time", "latitude", "longitude"), np.broadcast_to((0.0005 * step)[:, None, None], shape).copy()),
        "sf": (("valid_time", "latitude", "longitude"), np.broadcast_to((0.002 * step)[:, None, None], shape).copy()),
    }, coords)
    if not as_zip:
        xr.merge([instant, accum]).to_netcdf(path, engine="h5netcdf")
        return
    with tempfile.TemporaryDirectory() as tmp:
        instant_path = Path(tmp) / "data_stream-oper_stepType-instant.nc"
        accum_path = Path(tmp) / "data_stream-oper_stepType-accum.nc"
        instant.to_netcdf(instant_path, engine="h5netcdf")
        accum.to_netcdf(accum_path, engine="h5netcdf")
        with zipfile.ZipFile(path, "w") as archive:
            archive.write(instant_path, instant_path.name)
            archive.write(accum_path, accum_path.name)


class _FakeCdsClient:
    """Planted-value CDS stand-in: isothermal -5 C column, steady accumulation."""

    def __init__(self, log: list, sl_zip: bool = False, fail_times: dict | None = None,
                 fatal: bool = False) -> None:
        self.log = log
        self.sl_zip = sl_zip
        self.fail_times = fail_times if fail_times is not None else {}
        self.fatal = fatal

    def retrieve(self, dataset: str, request: dict, target: str) -> None:
        self.log.append(dataset)
        if self.fatal:
            raise Exception("Error 403: the required licence has not been accepted")
        if self.fail_times.get(dataset, 0) > 0:
            self.fail_times[dataset] -= 1
            raise Exception("503 Service Unavailable (transient)")
        if dataset == PL_DATASET:
            _write_pl_netcdf(Path(target), request)
        else:
            _write_sl_netcdf(Path(target), request, as_zip=self.sl_zip)


@unittest.skipUnless(HAVE_XARRAY, "xarray not installed")
class FetchEndToEndTest(unittest.TestCase):
    def _run_fetch(self, tmp: str, client: _FakeCdsClient) -> dict:
        events = [_event("test:cds:1", "TEST0001", "rockies", "2023-01-15", 39.5, -105.5)]
        return fetch_profiles_cds(
            events, Path(tmp) / "raw_cache_cds", Path(tmp) / "cds_day_cache",
            max_workers=2, client_factory=lambda: client, retry_backoff_s=0.0,
        )

    def test_planted_values_round_trip(self) -> None:
        log: list = []
        with tempfile.TemporaryDirectory() as tmp:
            summary = self._run_fetch(tmp, _FakeCdsClient(log))
            self.assertIsNone(summary["fatalError"])
            self.assertEqual(summary["profileSource"], CDS_PROFILE_SOURCE)
            self.assertEqual(summary["chunksCachedTotal"], 1)
            self.assertEqual(summary["jobsDownloadedThisRun"], 4)  # Jan 13..16
            self.assertEqual(summary["requestsSubmitted"], 8)
            self.assertEqual(log.count(PL_DATASET), 4)
            self.assertEqual(log.count(SL_DATASET), 4)

            events = [_event("test:cds:1", "TEST0001", "rockies", "2023-01-15", 39.5, -105.5)]
            chunk = plan_chunks(events, spec=CDS_SPEC)[0]
            path = _chunk_path(Path(tmp) / "raw_cache_cds", chunk)
            self.assertTrue(_cache_valid(path))
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["profile_source"], "era5-cds")
            parsed = _payload_hours(payload)
            row = build_event_features(events[0], parsed, CDS_PROFILE_SOURCE)
            self.assertEqual(row["profile_status"], "ok")
            self.assertEqual(row["t_300"], -5.0)
            self.assertEqual(row["baseline_slr"], 15.0)

            # Resume: a second run submits nothing and assembles nothing new.
            log2: list = []
            summary2 = self._run_fetch(tmp, _FakeCdsClient(log2))
            self.assertEqual(log2, [])
            self.assertEqual(summary2["chunksAlreadyCached"], 1)
            self.assertEqual(summary2["chunksCachedTotal"], 1)

    def test_zip_split_single_levels(self) -> None:
        log: list = []
        with tempfile.TemporaryDirectory() as tmp:
            summary = self._run_fetch(tmp, _FakeCdsClient(log, sl_zip=True))
            self.assertEqual(summary["chunksCachedTotal"], 1)

    def test_transient_failures_retry(self) -> None:
        log: list = []
        client = _FakeCdsClient(log, fail_times={PL_DATASET: 2})
        with tempfile.TemporaryDirectory() as tmp:
            summary = self._run_fetch(tmp, client)
            self.assertEqual(summary["chunksCachedTotal"], 1)
            # First job's PL needed 3 attempts; the other three jobs 1 each.
            self.assertEqual(log.count(PL_DATASET), 6)

    def test_no_credentials_configured_stops_before_any_client(self) -> None:
        import slr_calibration.gold_profiles_cds as cds_module

        original = cds_module.cds_credentials_present
        cds_module.cds_credentials_present = lambda: False
        try:
            with tempfile.TemporaryDirectory() as tmp:
                events = [_event("test:cds:1", "TEST0001", "rockies", "2023-01-15", 39.5, -105.5)]
                summary = fetch_profiles_cds(
                    events, Path(tmp) / "raw_cache_cds", Path(tmp) / "cds_day_cache",
                    max_workers=1, retry_backoff_s=0.0,
                )
        finally:
            cds_module.cds_credentials_present = original
        self.assertIsNotNone(summary["fatalError"])
        self.assertIn("cdsapirc", summary["fatalError"])
        self.assertEqual(summary["requestsSubmitted"], 0)
        self.assertEqual(summary["jobsPlanned"], 4)

    def test_missing_cdsapirc_is_fatal(self) -> None:
        class _NoConfigClient:
            def retrieve(self, dataset: str, request: dict, target: str) -> None:
                raise Exception("Missing/incomplete configuration file: ~/.cdsapirc")

        with tempfile.TemporaryDirectory() as tmp:
            events = [_event("test:cds:1", "TEST0001", "rockies", "2023-01-15", 39.5, -105.5)]
            summary = fetch_profiles_cds(
                events, Path(tmp) / "raw_cache_cds", Path(tmp) / "cds_day_cache",
                max_workers=1, client_factory=_NoConfigClient, retry_backoff_s=0.0,
            )
            self.assertIsNotNone(summary["fatalError"])
            self.assertEqual(summary["jobsDownloadedThisRun"], 0)

    def test_fatal_licence_error_stops_fast(self) -> None:
        log: list = []
        client = _FakeCdsClient(log, fatal=True)
        with tempfile.TemporaryDirectory() as tmp:
            events = [_event("test:cds:1", "TEST0001", "rockies", "2023-01-15", 39.5, -105.5)]
            summary = fetch_profiles_cds(
                events, Path(tmp) / "raw_cache_cds", Path(tmp) / "cds_day_cache",
                max_workers=1, client_factory=lambda: client, retry_backoff_s=0.0,
            )
            self.assertIsNotNone(summary["fatalError"])
            self.assertEqual(summary["jobsDownloadedThisRun"], 0)
            self.assertEqual(log, [PL_DATASET])  # no retries, no further jobs


class CdsSpecTest(unittest.TestCase):
    def test_spec_lifts_the_2021_floor(self) -> None:
        self.assertEqual(CDS_SPEC.source_id, "era5-cds")
        self.assertEqual(CDS_SPEC.cache_dir_name, "raw_cache_cds")
        events = [_event("e:old", "TEST0001", "rockies", "2005-01-10", 39.5, -105.5)]
        chunks = plan_chunks(events, spec=CDS_SPEC)
        self.assertEqual({e for c in chunks for e in c["event_ids"]}, {"e:old"})


if __name__ == "__main__":
    unittest.main()
