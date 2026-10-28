"""Tests for the W0 operator-transfer study helpers (research track)."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from slr_calibration.observation_operator import HourlySeries, OperatorParams
from slr_calibration.w0_transfer import (
    covariate_params,
    evaluate_exit_criterion,
    fetch_runs_for_events,
    load_sntl_stations,
    pair_band,
    pair_census,
    render_w0_report,
    score_station_events,
    window_residuals_multi,
)
from slr_calibration.observation_operator import StormWindow


class PairBandTests(unittest.TestCase):
    def test_primary_boundaries(self) -> None:
        self.assertEqual(pair_band(10.0, 150.0), "primary")
        self.assertEqual(pair_band(0.5, 3.0), "primary")

    def test_secondary_boundaries(self) -> None:
        self.assertEqual(pair_band(10.01, 150.0), "secondary")
        self.assertEqual(pair_band(25.0, 300.0), "secondary")

    def test_outside(self) -> None:
        self.assertIsNone(pair_band(25.01, 100.0))
        self.assertIsNone(pair_band(5.0, 300.01))


class FetchRunsTests(unittest.TestCase):
    def test_event_month_plus_minus_one(self) -> None:
        events = [{"endLocal": datetime(2020, 1, 15, 7, 0)}]
        runs = fetch_runs_for_events(events)
        self.assertEqual(runs, [(date(2019, 12, 1), date(2020, 2, 29))])

    def test_disjoint_runs(self) -> None:
        events = [
            {"endLocal": datetime(2020, 1, 15, 7, 0)},
            {"endLocal": datetime(2020, 6, 15, 7, 0)},
        ]
        runs = fetch_runs_for_events(events)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0], (date(2019, 12, 1), date(2020, 2, 29)))
        self.assertEqual(runs[1], (date(2020, 5, 1), date(2020, 7, 31)))


class SntlStationLoadingTests(unittest.TestCase):
    def test_filters_and_converts(self) -> None:
        payload = [
            {"stationTriplet": "1:CO:SNTL", "networkCode": "SNTL", "name": "A",
             "stateCode": "CO", "latitude": 39.0, "longitude": -106.0,
             "elevation": 10000.0, "dataTimeZone": -7.0},
            {"stationTriplet": "2:CO:SCAN", "networkCode": "SCAN", "name": "B",
             "stateCode": "CO", "latitude": 39.0, "longitude": -106.0,
             "elevation": 5000.0},
            {"stationTriplet": "3:CO:SNTL", "networkCode": "SNTL", "name": "C",
             "stateCode": "CO", "latitude": None, "longitude": -106.0,
             "elevation": 5000.0},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stations.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            stations = load_sntl_stations(path)
        self.assertEqual(len(stations), 1)
        self.assertEqual(stations[0]["triplet"], "1:CO:SNTL")
        self.assertAlmostEqual(stations[0]["elevation_m"], 3048.0, places=1)


class PairCensusTests(unittest.TestCase):
    def test_nearest_pairing(self) -> None:
        gold = {
            "G1": {"station_id": "G1", "region": "rockies", "station_name": "g",
                   "state": "CO", "latitude": 39.0, "longitude": -106.0,
                   "elevation_m": 3000.0, "n_events_total": 5,
                   "events": [{"waterYear": 2020}, {"waterYear": 2021}]},
            "G2": {"station_id": "G2", "region": "rockies", "station_name": "h",
                   "state": "CO", "latitude": 35.0, "longitude": -106.0,
                   "elevation_m": 3000.0, "n_events_total": 3,
                   "events": [{"waterYear": 2020}]},
        }
        sntl = [{"triplet": "9:CO:SNTL", "name": "s", "state": "CO",
                 "latitude": 39.01, "longitude": -106.0, "elevation_m": 3050.0,
                 "data_tz_offset_hours": -7.0, "begin_date": "", "end_date": ""}]
        census = pair_census(gold, sntl)
        self.assertEqual(len(census["pairs"]), 1)
        pair = census["pairs"][0]
        self.assertEqual(pair["gold_station"], "G1")
        self.assertEqual(pair["band"], "primary")
        self.assertEqual(pair["n_events_scoreable"], 2)
        self.assertEqual(census["summary"]["primary"]["events_scoreable"], 2)

    def test_nearest_within_band_wins_over_nearer_out_of_band(self) -> None:
        # Absolute-nearest SNTL is 2 km away but 400 m above the gold
        # station (out of band); a second SNTL at 8 km / 50 m must be
        # chosen as the primary pair instead of dropping the station.
        gold = {
            "G1": {"station_id": "G1", "region": "sierra-nevada",
                   "station_name": "g", "state": "CA", "latitude": 39.0,
                   "longitude": -106.0, "elevation_m": 2000.0,
                   "n_events_total": 4, "events": [{"waterYear": 2020}]},
        }
        sntl = [
            {"triplet": "1:CA:SNTL", "name": "near-high", "state": "CA",
             "latitude": 39.018, "longitude": -106.0, "elevation_m": 2400.0,
             "data_tz_offset_hours": -8.0, "begin_date": "", "end_date": ""},
            {"triplet": "2:CA:SNTL", "name": "far-inband", "state": "CA",
             "latitude": 39.072, "longitude": -106.0, "elevation_m": 2050.0,
             "data_tz_offset_hours": -8.0, "begin_date": "", "end_date": ""},
        ]
        census = pair_census(gold, sntl)
        self.assertEqual(len(census["pairs"]), 1)
        self.assertEqual(census["pairs"][0]["sntl_triplet"], "2:CA:SNTL")
        self.assertEqual(census["pairs"][0]["band"], "primary")


class CovariateParamsTests(unittest.TestCase):
    def test_reference_elevation(self) -> None:
        vec = [1.2, 0.5, 100.0, 10.0, 20.0, 17.0, 2.0]
        params = covariate_params(vec, 2000.0)
        self.assertAlmostEqual(params.gamma, 1.2)
        self.assertAlmostEqual(params.log_eta_old, 17.0)

    def test_elevation_shift_and_clipping(self) -> None:
        vec = [1.2, 0.5, 100.0, 10.0, 20.0, 17.0, 2.0]
        params = covariate_params(vec, 3000.0)
        self.assertAlmostEqual(params.gamma, 1.7)
        self.assertAlmostEqual(params.log_eta_old, 19.0)
        clipped = covariate_params([1.2, 5.0, 100.0, 10.0, 20.0, 17.0, 0.0], 3000.0)
        self.assertAlmostEqual(clipped.gamma, 1.8)


def _region_metrics(naive_mae: float, rec_mae: float, bias: float, n: int = 50) -> dict:
    return {
        "naive": {"mae": naive_mae, "bias": -2.0, "n": n},
        "recoveredOnNaiveSubset": {"mae": rec_mae, "bias": bias, "n": n},
    }


class ExitCriterionTests(unittest.TestCase):
    def test_three_regions_pass(self) -> None:
        by_region = {
            "rockies": _region_metrics(5.0, 4.0, 0.5),
            "alaska": _region_metrics(5.0, 4.1, -0.9),
            "sierra-nevada": _region_metrics(6.0, 4.8, 0.2),
        }
        verdict = evaluate_exit_criterion(by_region)
        self.assertTrue(verdict["criterionMet"])
        self.assertEqual(sorted(verdict["regionsPassing"]),
                         ["alaska", "rockies", "sierra-nevada"])

    def test_bias_failure_blocks(self) -> None:
        by_region = {
            "rockies": _region_metrics(5.0, 4.0, 0.5),
            "alaska": _region_metrics(5.0, 4.1, -0.9),
            "sierra-nevada": _region_metrics(6.0, 4.8, 1.4),
        }
        verdict = evaluate_exit_criterion(by_region)
        self.assertFalse(verdict["criterionMet"])
        self.assertEqual(verdict["perRegion"]["sierra-nevada"]["passed"], False)

    def test_missing_region_is_neutral(self) -> None:
        by_region = {"rockies": _region_metrics(5.0, 4.0, 0.5)}
        verdict = evaluate_exit_criterion(by_region)
        self.assertEqual(verdict["perRegion"]["alaska"]["status"], "no paired data")
        self.assertFalse(verdict["criterionMet"])


class ScoringTests(unittest.TestCase):
    def _series(self) -> HourlySeries:
        start = datetime(2020, 1, 1, 0, 0)
        times = [start + timedelta(hours=k) for k in range(49)]
        swe = [100.0] * 25 + [100.0 + 10.0 * (k - 24) / 24.0 for k in range(25, 49)]
        depth = [500.0] * 25 + [500.0 + 100.0 * (k - 24) / 24.0 for k in range(25, 49)]
        temp = [-5.0] * 49
        return HourlySeries("9:CO:SNTL", times, swe, depth, temp)

    def test_naive_and_recovered_defined(self) -> None:
        series = self._series()
        events = [{
            "date": "2020-01-03", "waterYear": 2020, "observedSlr": 10.0,
            "endLocal": datetime(2020, 1, 3, 0, 0),
        }]
        scored = score_station_events(series, events, OperatorParams())
        self.assertEqual(scored["accounting"]["scored"], 1)
        row = scored["rows"][0]
        self.assertIsNotNone(row["naiveSlr"])
        self.assertAlmostEqual(row["naiveSlr"], 10.0, places=2)
        self.assertIsNotNone(row["recoveredSlr"])
        self.assertGreater(row["recoveredSlr"], 0.0)

    def test_window_missing_accounted(self) -> None:
        series = self._series()
        events = [{
            "date": "2021-01-02", "waterYear": 2021, "observedSlr": 10.0,
            "endLocal": datetime(2021, 1, 2, 0, 0),
        }]
        scored = score_station_events(series, events, OperatorParams())
        self.assertEqual(scored["accounting"]["window_missing"], 1)
        self.assertEqual(scored["accounting"]["scored"], 0)


class PooledResidualTests(unittest.TestCase):
    def test_residuals_align_with_observed_depth(self) -> None:
        start = datetime(2020, 1, 1, 0, 0)
        times = [start + timedelta(hours=k) for k in range(30)]
        swe = [100.0 + float(k) for k in range(30)]
        depth = [500.0 + 5.0 * k for k in range(30)]
        series = HourlySeries("9:CO:SNTL", times, swe, depth, [-5.0] * 30)
        window = StormWindow(0, 29, 29.0)
        residuals = window_residuals_multi(
            [(series, [window])], lambda _t: OperatorParams())
        self.assertEqual(len(residuals), 30)
        self.assertTrue(all(isinstance(r, float) for r in residuals))


class RenderTests(unittest.TestCase):
    def test_report_includes_verdict_and_availability(self) -> None:
        results = {
            "stage1Census": {"pairs": [], "summary": {}, "sntlNetwork": {},
                             "goldStationsWithEvents": 0},
            "availabilityAudit": {
                "1:AK:SNTL": {"monthsCached": 3,
                              "monthsWithValues": {"WTEQ": 0, "SNWD": 3, "TOBS": 3},
                              "monthsEmpty": 0, "events": 10, "scored": 0,
                              "goldStations": ["G1"]},
            },
            "stage5Verdict": {"bestVariant": "V0-frozen", "recommendation": "STOP",
                              "perVariant": {}},
        }
        md = render_w0_report(results)
        self.assertIn("Recommendation: STOP", md)
        self.assertIn("1:AK:SNTL", md)
        self.assertIn("Interpretation", md)


if __name__ == "__main__":
    unittest.main()
