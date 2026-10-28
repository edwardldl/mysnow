from __future__ import annotations

import gzip
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import h5py
import numpy as np
import pandas as pd

from slr_calibration.gold_model import _frame_profile_source
from slr_calibration.gold_profiles import (
    BEST_MATCH_SPEC,
    ERA5_PROFILE_SOURCE,
    ERA5_SPEC,
    PROFILE_SOURCE,
    _cache_valid,
    _chunk_path,
    _payload_hours,
    build_event_features,
    plan_chunks,
)
from slr_calibration import gold_profiles_era5 as era5_module
from slr_calibration.cli import build_parser
from slr_calibration.gold_profiles_era5 import (
    GRAVITY_M_S2,
    accumu_key,
    accumu_span_start,
    accumulation_init,
    build_chunk_payload,
    fetch_profiles_era5,
    hourly_accumulation,
    pl_key,
    read_points,
    read_station_day,
    sfc_2t_key,
)

UTC = timezone.utc
STANDARD_HEIGHTS = {
    1000: 110.0, 975: 320.0, 950: 500.0, 925: 760.0, 900: 990.0,
    850: 1450.0, 800: 1950.0, 750: 2460.0, 700: 3010.0, 650: 3590.0,
    600: 4200.0, 550: 4860.0, 500: 5570.0,
}


class KeyBuilderTest(unittest.TestCase):
    def test_pressure_level_keys(self) -> None:
        self.assertEqual(
            pl_key(date(2023, 1, 14), "t"),
            "e5.oper.an.pl/202301/e5.oper.an.pl.128_130_t.ll025sc.2023011400_2023011423.nc",
        )
        self.assertEqual(
            pl_key(date(2023, 1, 14), "u"),
            "e5.oper.an.pl/202301/e5.oper.an.pl.128_131_u.ll025uv.2023011400_2023011423.nc",
        )
        self.assertEqual(
            pl_key(date(1998, 6, 16), "z"),
            "e5.oper.an.pl/199806/e5.oper.an.pl.128_129_z.ll025sc.1998061600_1998061623.nc",
        )

    def test_surface_key_month_lengths(self) -> None:
        self.assertEqual(
            sfc_2t_key(2024, 2),
            "e5.oper.an.sfc/202402/e5.oper.an.sfc.128_167_2t.ll025sc.2024020100_2024022923.nc",
        )
        self.assertEqual(
            sfc_2t_key(2023, 1),
            "e5.oper.an.sfc/202301/e5.oper.an.sfc.128_167_2t.ll025sc.2023010100_2023013123.nc",
        )

    def test_accumu_keys_and_spans(self) -> None:
        self.assertEqual(accumu_span_start(datetime(2023, 1, 15, 18, tzinfo=UTC)), datetime(2023, 1, 1, 6, tzinfo=UTC))
        self.assertEqual(accumu_span_start(datetime(2023, 1, 16, 6, tzinfo=UTC)), datetime(2023, 1, 16, 6, tzinfo=UTC))
        self.assertEqual(accumu_span_start(datetime(2023, 1, 1, 6, tzinfo=UTC)), datetime(2023, 1, 1, 6, tzinfo=UTC))
        self.assertEqual(
            accumu_key(datetime(2023, 1, 1, 6, tzinfo=UTC), "sf"),
            "e5.oper.fc.sfc.accumu/202301/e5.oper.fc.sfc.accumu.128_144_sf.ll025sc.2023010106_2023011606.nc",
        )
        # Second span of December rolls into January of the next year.
        self.assertEqual(
            accumu_key(datetime(2023, 12, 16, 6, tzinfo=UTC), "lsp"),
            "e5.oper.fc.sfc.accumu/202312/e5.oper.fc.sfc.accumu.128_142_lsp.ll025sc.2023121606_2024010106.nc",
        )


class AccumulationInitTest(unittest.TestCase):
    def test_cycle_selection(self) -> None:
        cases = [
            (datetime(2023, 1, 14, 7, tzinfo=UTC), datetime(2023, 1, 14, 6, tzinfo=UTC), 1),
            (datetime(2023, 1, 14, 18, tzinfo=UTC), datetime(2023, 1, 14, 6, tzinfo=UTC), 12),
            (datetime(2023, 1, 14, 19, tzinfo=UTC), datetime(2023, 1, 14, 18, tzinfo=UTC), 1),
            (datetime(2023, 1, 15, 0, tzinfo=UTC), datetime(2023, 1, 14, 18, tzinfo=UTC), 6),
            (datetime(2023, 1, 15, 6, tzinfo=UTC), datetime(2023, 1, 14, 18, tzinfo=UTC), 12),
        ]
        for valid, init, forecast_hour in cases:
            self.assertEqual(accumulation_init(valid), (init, forecast_hour))

    def test_hourly_differencing(self) -> None:
        init = datetime(2023, 1, 14, 6, tzinfo=UTC)
        cumulative = {("S1", init, 1): 0.004, ("S1", init, 2): 0.010, ("S1", init, 3): None}
        self.assertAlmostEqual(hourly_accumulation(cumulative, "S1", init + timedelta(hours=1)), 0.004)
        self.assertAlmostEqual(hourly_accumulation(cumulative, "S1", init + timedelta(hours=2)), 0.006)
        self.assertIsNone(hourly_accumulation(cumulative, "S1", init + timedelta(hours=3)))
        self.assertIsNone(hourly_accumulation(cumulative, "S1", init + timedelta(hours=4)))


class ReadPointsTest(unittest.TestCase):
    def test_chunked_scatter_read_and_fill(self) -> None:
        with h5py.File("tiny", mode="w", driver="core", backing_store=False) as handle:
            dataset = handle.create_dataset(
                "X", shape=(2, 3, 10, 12), chunks=(1, 3, 5, 6), dtype="f4",
            )
            data = np.arange(2 * 3 * 10 * 12, dtype="f4").reshape(2, 3, 10, 12)
            data[0, 0, 0, 0] = 9.999e20  # ERA5 fill value
            dataset[()] = data
            points = [(0, 0, 0, 0), (0, 1, 2, 3), (1, 2, 9, 11), (1, 0, 5, 6)]
            values = read_points(dataset, points)
        self.assertIsNone(values[0])
        self.assertEqual(values[1], float(data[0, 1, 2, 3]))
        self.assertEqual(values[2], float(data[1, 2, 9, 11]))
        self.assertEqual(values[3], float(data[1, 0, 5, 6]))


def _synthetic_record() -> dict:
    return {
        "temperature2mC": -5.0,
        "precipitationMm": 1.5,
        "snowfallCm": 0.2,
        "levels": {level: (-5.0, 80.0, 6.0, height) for level, height in STANDARD_HEIGHTS.items()},
    }


def _synthetic_event() -> dict:
    return {
        "event_id": "test:era5:1",
        "region": "rockies",
        "station_id": "TEST0001",
        "site_id": "TEST0001",
        "station_name": "Test",
        "state": "CO",
        "valid_time": datetime(2023, 1, 15, 7, 0, tzinfo=timezone(timedelta(hours=-6))),
        "observation_date": "2023-01-15",
        "water_year": 2023,
        "month": 1,
        "latitude": 39.5,
        "longitude": -105.5,
        "elevation_m": 1000.0,
        "observed_slr": 15.0,
        "fresh_snow_mm": 150.0,
        "fresh_swe_mm": 10.0,
        "climate_regime": "continental_mountain",
        "source_tier": "ghcn-coop",
    }


def _second_synthetic_event() -> dict:
    event = _synthetic_event()
    event["event_id"] = "test:era5:2"
    event["valid_time"] = datetime(2023, 1, 20, 7, 0, tzinfo=timezone(timedelta(hours=-6)))
    event["observation_date"] = "2023-01-20"
    return event


class ChunkPayloadTest(unittest.TestCase):
    def _chunk_and_hours(self) -> tuple[dict, dict]:
        chunk = {
            "key": "TEST0001|2023-01-14|2023-01-16",
            "station_id": "TEST0001",
            "latitude": 39.5,
            "longitude": -105.5,
            "start": "2023-01-14",
            "end": "2023-01-16",
            "event_ids": ["test:era5:1"],
        }
        hours = {}
        start = datetime(2023, 1, 14, tzinfo=UTC)
        for step in range(72):
            hours[start + timedelta(hours=step)] = _synthetic_record()
        return chunk, hours

    def test_payload_round_trip_through_feature_builder(self) -> None:
        chunk, hours = self._chunk_and_hours()
        payload = build_chunk_payload(chunk, hours)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["profile_source"], ERA5_PROFILE_SOURCE)
        self.assertEqual(len(payload["hourly"]["time"]), 72)
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            path = _chunk_path(cache_dir, chunk)
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                json.dump(payload, handle)
            self.assertTrue(_cache_valid(path))
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                parsed = _payload_hours(json.load(handle))
        row = build_event_features(_synthetic_event(), parsed, ERA5_PROFILE_SOURCE)
        self.assertEqual(row["profile_status"], "ok")
        self.assertEqual(row["profile_source"], ERA5_PROFILE_SOURCE)
        self.assertEqual(row["t_300"], -5.0)
        self.assertEqual(row["baseline_slr"], 15.0)

    def test_missing_hour_blocks_payload(self) -> None:
        chunk, hours = self._chunk_and_hours()
        del hours[datetime(2023, 1, 15, 3, tzinfo=UTC)]
        self.assertIsNone(build_chunk_payload(chunk, hours))


class _StubReader:
    """Synthetic ERA5 reader: isothermal -5 C column, steady accumulation."""

    def read_pl_day(self, day: date, variable: str, stations: list) -> dict:
        day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        out = {}
        for station_id, _lat, _lon in stations:
            for hour in range(24):
                instant = day_start + timedelta(hours=hour)
                if variable == "t":
                    values = {level: 268.15 for level in STANDARD_HEIGHTS}
                elif variable == "r":
                    values = {level: 80.0 for level in STANDARD_HEIGHTS}
                elif variable == "u":
                    values = {level: 3.0 for level in STANDARD_HEIGHTS}
                elif variable == "v":
                    values = {level: 4.0 for level in STANDARD_HEIGHTS}
                else:  # z: geopotential in m^2 s^-2
                    values = {level: height * GRAVITY_M_S2 for level, height in STANDARD_HEIGHTS.items()}
                out[(station_id, instant)] = values
        return out

    def read_2t(self, instants: list, stations: list) -> dict:
        return {(sid, instant): 268.15 for sid, _la, _lo in stations for instant in instants}

    def read_accumu_cumulative(self, variable: str, requests: list) -> dict:
        rates = {"sf": 0.002, "lsp": 0.001, "cp": 0.0005}
        return {
            (station_id, init, forecast_hour): rates[variable] * forecast_hour
            for init, forecast_hour, station_id, _lat, _lon in requests
        }


class _ExplodingReader(_StubReader):
    """Stub reader whose pressure-level reads always fail for one day."""

    def __init__(self, bad_day: date) -> None:
        self.bad_day = bad_day

    def read_pl_day(self, day: date, variable: str, stations: list) -> dict:
        if day == self.bad_day:
            raise RuntimeError("boom")
        return super().read_pl_day(day, variable, stations)


class _WorkerKillingReader(_StubReader):
    """Stub reader that kills its own process outright on one day.

    os._exit bypasses exceptions and cleanup entirely, so from the
    parent's side the worker simply vanishes -- the failure mode that
    aborted the round-1 production runs with BrokenProcessPool.
    """

    def __init__(self, kill_day: date) -> None:
        self.kill_day = kill_day

    def read_pl_day(self, day: date, variable: str, stations: list) -> dict:
        if day == self.kill_day:
            os._exit(42)
        return super().read_pl_day(day, variable, stations)


class _OneShotKillingReader(_StubReader):
    """Kills its own process the first time one day is read (in any
    process), then reads normally -- a sporadic worker death such as
    an OOM kill, not a poison day.  The marker file is the
    cross-process memory of the first kill."""

    def __init__(self, kill_day: date, marker: Path) -> None:
        self.kill_day = kill_day
        self.marker = marker

    def read_pl_day(self, day: date, variable: str, stations: list) -> dict:
        if day == self.kill_day and not self.marker.exists():
            self.marker.write_text("killed")
            os._exit(42)
        return super().read_pl_day(day, variable, stations)


class _WorkerOnlyKillingReader(_StubReader):
    """Kills whichever process reads in it UNLESS that process is the
    one that constructed it: every pooled worker dies, while the
    parent process (the threaded fallback) reads normally."""

    def __init__(self) -> None:
        self.home_pid = os.getpid()

    def read_pl_day(self, day: date, variable: str, stations: list) -> dict:
        if os.getpid() != self.home_pid:
            os._exit(42)
        return super().read_pl_day(day, variable, stations)


class ReadStationDayTest(unittest.TestCase):
    def test_conversions(self) -> None:
        stations = [("TEST0001", 39.5, -105.5)]
        result = read_station_day(_StubReader(), date(2023, 1, 14), stations)  # type: ignore[arg-type]
        record = result["TEST0001"][datetime(2023, 1, 14, 12, tzinfo=UTC)]
        self.assertEqual(record["temperature2mC"], -5.0)
        temp_c, rh, wind, height = record["levels"][850]
        self.assertAlmostEqual(temp_c, -5.0)
        self.assertEqual(rh, 80.0)
        self.assertAlmostEqual(wind, 5.0)  # hypot(3, 4)
        self.assertAlmostEqual(height, 1450.0)
        self.assertAlmostEqual(record["snowfallCm"], 0.2)  # 0.002 m w.e. -> cm
        self.assertAlmostEqual(record["precipitationMm"], 1.5)  # (lsp + cp) m -> mm


class Era5PlanTest(unittest.TestCase):
    def _event(self, day: str) -> dict:
        return {
            "event_id": f"e:{day}",
            "station_id": "TEST0001",
            "valid_time": datetime.fromisoformat(f"{day}T07:00:00-06:00"),
            "observation_date": day,
            "latitude": 39.5,
            "longitude": -105.5,
            "elevation_m": 2000.0,
        }

    def test_era5_spec_lifts_the_2021_floor(self) -> None:
        events = [self._event("2005-01-10"), self._event("2026-07-10"), self._event("2026-09-10")]
        era5_chunks = plan_chunks(events, spec=ERA5_SPEC)
        era5_ids = {eid for chunk in era5_chunks for eid in chunk["event_ids"]}
        # 2005 (pre-2021) and 2026-07 are eligible; 2026-09 is past the
        # verified mirror coverage end and stays out.
        self.assertEqual(era5_ids, {"e:2005-01-10", "e:2026-07-10"})
        best_chunks = plan_chunks(events, spec=BEST_MATCH_SPEC)
        best_ids = {eid for chunk in best_chunks for eid in chunk["event_ids"]}
        self.assertEqual(best_ids, {"e:2026-07-10", "e:2026-09-10"})


class FrameProfileSourceTest(unittest.TestCase):
    def test_uniform_column_wins(self) -> None:
        frame = pd.DataFrame({"profile_source": [ERA5_PROFILE_SOURCE, ERA5_PROFILE_SOURCE]})
        self.assertEqual(_frame_profile_source(frame), ERA5_PROFILE_SOURCE)

    def test_missing_column_falls_back(self) -> None:
        frame = pd.DataFrame({"other": [1, 2]})
        self.assertEqual(_frame_profile_source(frame), PROFILE_SOURCE)


class ProcessExecutorParityTest(unittest.TestCase):
    """Process mode must be indistinguishable from thread mode: same
    chunk payloads, same summary.  The injected stub reader is
    stateless and picklable, so the process path is genuinely
    exercised (workers receive it as their day-read reader)."""

    def _run(self, executor: str, reader) -> tuple[dict, dict]:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            summary = fetch_profiles_era5(
                [_synthetic_event()],
                cache_dir,
                max_workers=2,
                reader=reader,
                executor=executor,
            )
            payloads = {}
            for path in sorted(cache_dir.rglob("*.json.gz")):
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    payloads[str(path.relative_to(cache_dir))] = json.load(handle)
            return summary, payloads

    def test_process_matches_thread_outputs_and_summary(self) -> None:
        thread_summary, thread_payloads = self._run("thread", _StubReader())
        process_summary, process_payloads = self._run("process", _StubReader())
        self.assertEqual(thread_summary["chunksDownloadedThisRun"], 1)
        self.assertEqual(thread_summary, process_summary)
        # Compare decompressed payloads, not raw .json.gz bytes: the
        # gzip container embeds a write-time mtime in its header, so
        # the bytes differ between ANY two runs (thread vs thread too).
        self.assertEqual(thread_payloads, process_payloads)
        self.assertEqual(len(process_payloads), 1)

    def test_failed_day_accounting_matches(self) -> None:
        bad_day = date(2023, 1, 15)
        stderr = io.StringIO()
        # Neutralise the retry backoff so the failing day costs no wall
        # time; with the fork start method the workers inherit the
        # patch.  Both executors must still attempt, fail and report
        # the day identically (the message is printed by the parent).
        with mock.patch("time.sleep", lambda *_args: None), redirect_stderr(stderr):
            thread_summary, _ = self._run("thread", _ExplodingReader(bad_day))
            process_summary, _ = self._run("process", _ExplodingReader(bad_day))
        self.assertEqual(thread_summary, process_summary)
        self.assertEqual(process_summary["daysFailed"], 1)
        self.assertEqual(process_summary["failedDays"], ["2023-01-15"])
        self.assertEqual(process_summary["chunksDownloadedThisRun"], 0)
        self.assertEqual(process_summary["chunksFailedThisRun"], 1)
        self.assertIn(
            "day 2023-01-15 failed after retries: RuntimeError: boom",
            stderr.getvalue(),
        )

    def test_unknown_executor_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                fetch_profiles_era5(
                    [_synthetic_event()],
                    Path(tmp) / "cache",
                    reader=_StubReader(),
                    executor="bogus",
                )


class ProcessPoolResilienceTest(unittest.TestCase):
    """A dead worker must degrade the run, not abort it: the affected
    day is accounted failed, the pool is recreated, remaining days
    still complete, and the summary is always produced."""

    def _run(self, executor: str, reader, events: list, max_workers: int = 2) -> tuple[dict, dict]:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            summary = fetch_profiles_era5(
                events,
                cache_dir,
                max_workers=max_workers,
                reader=reader,
                executor=executor,
            )
            payloads = {}
            for path in sorted(cache_dir.rglob("*.json.gz")):
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    payloads[str(path.relative_to(cache_dir))] = json.load(handle)
            return summary, payloads

    def test_worker_death_fails_one_day_and_run_completes(self) -> None:
        kill_day = date(2023, 1, 15)
        # max_workers=1 serialises dispatch, so exactly the killed day
        # is in flight when the pool breaks: deterministic accounting.
        # The thread run over an always-raising reader defines the
        # expected summary for "that one day failed"; the patch only
        # neutralises the retry backoff (inherited by fork workers).
        with mock.patch("time.sleep", lambda *_args: None):
            process_summary, process_payloads = self._run(
                "process", _WorkerKillingReader(kill_day), [_synthetic_event()], max_workers=1
            )
            thread_summary, _ = self._run(
                "thread", _ExplodingReader(kill_day), [_synthetic_event()], max_workers=1
            )
        self.assertEqual(process_summary, thread_summary)
        self.assertEqual(process_summary["daysFailed"], 1)
        self.assertEqual(process_summary["failedDays"], ["2023-01-15"])
        self.assertEqual(process_summary["chunksDownloadedThisRun"], 0)
        self.assertEqual(process_summary["chunksFailedThisRun"], 1)
        self.assertEqual(process_payloads, {})

    def test_break_lost_day_is_requeued_and_completes(self) -> None:
        kill_day = date(2023, 1, 15)
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "killed.marker"
            with mock.patch("time.sleep", lambda *_args: None):
                process_summary, process_payloads = self._run(
                    "process",
                    _OneShotKillingReader(kill_day, marker),
                    [_synthetic_event()],
                    max_workers=1,
                )
                thread_summary, thread_payloads = self._run(
                    "thread", _StubReader(), [_synthetic_event()], max_workers=1
                )
        # The day whose first read killed a worker is retried in a
        # fresh pool and completes: nothing is counted failed and the
        # outputs are exactly the clean run's.
        self.assertEqual(process_summary, thread_summary)
        self.assertEqual(process_summary["daysFailed"], 0)
        self.assertEqual(process_summary["chunksDownloadedThisRun"], 1)
        self.assertEqual(process_payloads, thread_payloads)

    def test_repeated_pool_breaks_fall_back_to_threaded(self) -> None:
        events = [_synthetic_event(), _second_synthetic_event()]
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            summary, payloads = self._run(
                "process", _WorkerOnlyKillingReader(), events, max_workers=1
            )
        # The first three days each kill their worker (three
        # consecutive breaks).  Break-lost days are requeued rather
        # than counted failed, so when the fallback engages they are
        # read in-process along with the remaining days -- and this
        # reader only kills pooled workers, so every day completes.
        self.assertEqual(summary["daysFailed"], 0)
        self.assertEqual(summary["failedDays"], [])
        self.assertEqual(summary["chunksDownloadedThisRun"], 2)
        self.assertEqual(summary["chunksFailedThisRun"], 0)
        self.assertEqual(len(payloads), 2)
        self.assertIn("falling back to threaded day reads", stderr.getvalue())

    def test_pool_recycling_is_output_neutral(self) -> None:
        with mock.patch.object(era5_module, "POOL_RECYCLE_DAYS", 2):
            process_summary, process_payloads = self._run(
                "process", _StubReader(), [_synthetic_event()]
            )
        thread_summary, thread_payloads = self._run(
            "thread", _StubReader(), [_synthetic_event()]
        )
        self.assertEqual(thread_summary, process_summary)
        self.assertEqual(thread_payloads, process_payloads)
        self.assertEqual(len(process_payloads), 1)


class Era5ExecutorCliTest(unittest.TestCase):
    def test_executor_flag_parses_and_defaults_to_thread(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["fetch-gold-profiles-era5"])
        self.assertEqual(args.executor, "thread")
        args = parser.parse_args(["fetch-gold-profiles-era5", "--executor", "process"])
        self.assertEqual(args.executor, "process")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["fetch-gold-profiles-era5", "--executor", "bogus"])


if __name__ == "__main__":
    unittest.main()
