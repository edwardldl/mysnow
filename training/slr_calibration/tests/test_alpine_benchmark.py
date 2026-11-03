"""Tests for the queue-item-4 alpine benchmark (Alta + SLF).

Pair formation, hygiene, staging/subsampling and the ERA5 series
assembly are tested on synthetic fixtures; the region-swap
invariance of packaged-model scoring is tested against the real
packages when they are present in the workspace (skipped elsewhere).
"""

from __future__ import annotations

import csv
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from slr_calibration import alpine_benchmark as ab
from slr_calibration.alpine_profiles import assemble_series
from slr_calibration.gold_profiles import PRESSURE_LEVELS_HPA
from slr_calibration.gold_profiles_cds import accumulation_init_cds

UTC = timezone.utc
REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_V1 = REPO_ROOT / "data" / "slr-training-data" / "screened-v2" / "model-v1"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class HygieneTests(unittest.TestCase):
    def test_floors_and_range(self) -> None:
        self.assertEqual(ab.apply_hygiene(5.08, 10.0)[1], "snow_below_floor")
        self.assertEqual(ab.apply_hygiene(10.0, 2.78)[1], "liquid_below_floor")
        self.assertEqual(ab.apply_hygiene(1.0, 10.0)[1], "snow_below_floor")
        slr, reason, _ = ab.apply_hygiene(19.9, 10.0)
        self.assertEqual((slr, reason), (19.9, None))
        # SLR exactly 2 and exactly 50 are kept; just outside is not.
        self.assertEqual(ab.apply_hygiene(20.0, 100.0)[0], 2.0)
        self.assertEqual(ab.apply_hygiene(50.0, 10.0)[0], 50.0)
        self.assertEqual(ab.apply_hygiene(19.9, 100.0)[1], "slr_out_of_range")
        self.assertEqual(ab.apply_hygiene(50.01, 10.0)[1], "slr_out_of_range")

    def test_exact10_kept_and_flagged(self) -> None:
        slr, reason, exact = ab.apply_hygiene(10.0, 10.0)
        self.assertEqual((slr, reason, exact), (10.0, None, True))


class AltaPairTests(unittest.TestCase):
    def _csv(self, rows: list[tuple[str, str, str]]) -> Path:
        tmp = Path(tempfile.mkdtemp())
        lines = ["Time (LST),12 hr Snow (in.),12 hr LPE (in.)"]
        lines += [f"{t},{s},{l}" for t, s, l in rows]
        return _write(tmp / "cln.csv", "\n".join(lines) + "\n")

    def test_pair_formation(self) -> None:
        path = self._csv([
            ("2001-12-30 04", "2.0", "0.2"),   # era-excluded pair
            ("2001-12-30 16", "2.0", "0.2"),
            ("2003-01-15 04", "1.0", "0.2"),   # exact-10 accepted pair
            ("2003-01-15 16", "3.0", "0.2"),
            ("2003-01-16 04", "2.0", "0.2"),   # incomplete (no 16:00)
            ("2003-01-17 04", "0.1", "0.05"),  # below snow floor
            ("2003-01-17 16", "0.1", "0.05"),
            ("2003-01-18 16", "5.0", "0.5"),   # 16:00 never anchors
        ])
        pairs, funnel = ab.form_alta_pairs(path)
        self.assertEqual(funnel["rows"], 8)
        self.assertEqual(funnel["candidate_windows"], 4)
        self.assertEqual(funnel["era_excluded_pre2002"], 1)
        self.assertEqual(funnel["incomplete_pair"], 1)
        self.assertEqual(funnel["snow_below_floor"], 1)
        self.assertEqual(funnel["exact10_kept"], 1)
        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        self.assertEqual(pair["pair_id"], "alta-cln-20030116")
        # Ratio of sums: (1+3) in snow / (0.2+0.2) in liquid = 10.0.
        self.assertAlmostEqual(pair["observed_slr"], 10.0)
        self.assertAlmostEqual(pair["snow_mm"], 101.6)
        self.assertAlmostEqual(pair["liquid_mm"], 10.16)
        # 04:00 LST end = 11:00 UTC (fixed UTC-07:00 convention).
        self.assertEqual(pair["window_end_utc"], "2003-01-16T11:00:00+00:00")
        self.assertEqual(pair["window_start_utc"], "2003-01-15T11:00:00+00:00")
        self.assertEqual(pair["season_ending_year"], 2003)
        self.assertFalse(pair["reserved"])
        self.assertEqual(pair["license"], "CC BY-NC 4.0")

    def test_reserved_seasons(self) -> None:
        path = self._csv([
            ("2022-02-10 04", "2.0", "0.2"),
            ("2022-02-10 16", "2.0", "0.2"),
            ("2021-11-10 04", "2.0", "0.2"),   # season ending 2022
            ("2021-11-10 16", "2.0", "0.2"),
        ])
        pairs, _ = ab.form_alta_pairs(path)
        by_id = {p["pair_id"]: p for p in pairs}
        self.assertTrue(by_id["alta-cln-20220211"]["reserved"])
        self.assertEqual(by_id["alta-cln-20211111"]["season_ending_year"], 2022)
        self.assertTrue(by_id["alta-cln-20211111"]["reserved"])


def _slf_dir(stations: list[tuple[str, float, float, float]],
             days: list[tuple[str, dict[str, float], dict[str, float]]]) -> Path:
    tmp = Path(tempfile.mkdtemp())
    station_lines = ["Station,Easting (m),Northing (m),Altitude (m.a.s.l.)"]
    for sid, east, north, elev in stations:
        station_lines.append(f"{sid},{east},{north},{elev}")
    _write(tmp / "STATION-LIST.txt", "\n".join(station_lines) + "\n")
    ids = [s[0] for s in stations]

    def _table(desc: str, units: str, values: list[dict[str, float]]) -> str:
        lines = [
            "GLOBAL FILE ATTRIBUTES", "",
            f"Description:                  {desc}",
            f"Units:                        {units}",
            "Date created:                 2024-11-11T08:49:57", "",
            "TABLE WITH DATA", "",
            "Time," + ",".join(ids),
        ]
        for (stamp, _hn, _hnw), row in zip(days, values):
            lines.append(stamp + "," + ",".join(str(row[sid]) for sid in ids))
        return "\n".join(lines) + "\n"

    _write(tmp / "HN" / "OBS-HN.txt",
           _table("new snow depth, observed", "cm", [d[1] for d in days]))
    _write(tmp / "HNW" / "OBS-HNW.txt",
           _table("new snow water equivalent, observed", "mm", [d[2] for d in days]))
    return tmp


class SlfPairTests(unittest.TestCase):
    STATIONS = [("SLF.9AA", 600000.0, 200000.0, 2000.0),
                ("SLF.9BB", 600000.0, 200000.0, 1200.0)]

    def test_pairs_stages_and_funnel(self) -> None:
        days = [
            ("2020-01-02 06:00:00",
             {"SLF.9AA": 20, "SLF.9BB": -9999}, {"SLF.9AA": 20, "SLF.9BB": 10}),
            ("2020-01-03 06:00:00",
             {"SLF.9AA": 3, "SLF.9BB": 30}, {"SLF.9AA": 5, "SLF.9BB": 15}),
            ("2020-01-04 06:00:00",
             {"SLF.9AA": 25, "SLF.9BB": -9999}, {"SLF.9AA": -9999, "SLF.9BB": -9999}),
        ]
        pairs, funnel, stations = ab.form_slf_pairs(_slf_dir(self.STATIONS, days))
        self.assertEqual(funnel["cells"], 6)
        self.assertEqual(funnel["hn_missing"], 1)
        self.assertEqual(funnel["hnw_missing"], 1)
        self.assertEqual(funnel["both_missing"], 1)
        self.assertEqual(funnel["snow_below_floor"], 1)
        self.assertEqual(funnel["exact10_kept"], 1)
        self.assertEqual(len(pairs), 2)
        by_station = {p["station_id"]: p for p in pairs}
        a = by_station["SLF.9AA"]
        self.assertEqual(a["stage"], "A")
        self.assertAlmostEqual(a["observed_slr"], 10.0)
        # CET end 06:00 -> 05:00 UTC; window is the prior 24 h.
        self.assertEqual(a["window_end_utc"], "2020-01-02T05:00:00+00:00")
        self.assertEqual(a["window_start_utc"], "2020-01-01T05:00:00+00:00")
        self.assertEqual(a["license"], "CC BY 4.0")
        self.assertEqual(by_station["SLF.9BB"]["stage"], "C")
        self.assertAlmostEqual(by_station["SLF.9BB"]["observed_slr"], 20.0)
        self.assertTrue(math.isfinite(stations["SLF.9AA"]["latitude"]))

    def test_control_subsample_is_deterministic_and_capped(self) -> None:
        days = [
            (f"2020-02-{d:02d} 06:00:00",
             {"SLF.9CC": 20}, {"SLF.9CC": 10})
            for d in range(1, 11)
        ]
        stations = [("SLF.9CC", 600000.0, 200000.0, 1100.0)]
        original = ab.SLF_CONTROL_CAP
        ab.SLF_CONTROL_CAP = 3
        try:
            pairs, funnel, _ = ab.form_slf_pairs(_slf_dir(stations, days))
        finally:
            ab.SLF_CONTROL_CAP = original
        self.assertEqual(len(pairs), 3)
        self.assertEqual(funnel["control_subsample_excluded"], 7)
        kept_days = sorted(p["pair_id"][-8:] for p in pairs)
        self.assertEqual(kept_days, ["20200201", "20200205", "20200209"])


class AssemblyTests(unittest.TestCase):
    """Synthetic parsed payloads -> CDS extract (differencing) -> assembly."""

    LAT, LON = 40.5, -111.5

    def _payloads(self, drop_lsp_at: datetime | None = None):
        import numpy as np

        start = datetime(2024, 1, 10, 1, 0, tzinfo=UTC)  # fh=1 of 00Z init
        instants = [start + timedelta(hours=k) for k in range(48)]
        levels = list(PRESSURE_LEVELS_HPA)

        def pl_payload(fill):
            arr = np.zeros((len(instants), len(levels), 1, 1))
            for li, lev in enumerate(levels):
                arr[:, li, 0, 0] = fill(lev)
            return {"times": instants, "levels": levels,
                    "lats": [self.LAT], "lons": [self.LON], "values": arr}

        pl_vars = {
            "temperature": pl_payload(lambda lev: 268.15),
            "relative_humidity": pl_payload(lambda lev: 80.0),
            "u_component_of_wind": pl_payload(lambda lev: 3.0),
            "v_component_of_wind": pl_payload(lambda lev: 4.0),
            "geopotential": pl_payload(lambda lev: 5000.0 * 9.80665),
            "vertical_velocity": pl_payload(lambda lev: 0.1),
            "specific_humidity": pl_payload(lambda lev: 0.002),
        }

        def sl_payload(values_by_instant):
            times = sorted(values_by_instant)
            arr = np.array([values_by_instant[t] for t in times],
                           dtype="float64").reshape(len(times), 1, 1)
            return {"times": times, "levels": None,
                    "lats": [self.LAT], "lons": [self.LON], "values": arr}

        def cum(factor):
            out = {}
            for t in instants:
                _init, fh = accumulation_init_cds(t)
                out[t] = fh * factor
            return out

        lsp = cum(0.001)  # 1 mm of water per forecast hour
        if drop_lsp_at is not None:
            del lsp[drop_lsp_at]
        sl_vars = {
            "2m_temperature": sl_payload({t: 269.15 for t in instants}),
            "2m_dewpoint_temperature": sl_payload({t: 268.0 for t in instants}),
            "total_column_liquid_water": sl_payload({t: 0.1 for t in instants}),
            "10m_u_component_of_wind": sl_payload({t: 1.0 for t in instants}),
            "10m_v_component_of_wind": sl_payload({t: 2.0 for t in instants}),
            "skin_temperature": sl_payload({t: 269.0 for t in instants}),
            "large_scale_precipitation": sl_payload(lsp),
            "convective_precipitation": sl_payload(cum(0.0)),
            "snowfall": sl_payload(cum(0.002)),
        }
        return pl_vars, sl_vars, instants

    def _series(self, drop_lsp_at: datetime | None = None):
        from slr_calibration.alpine_profiles import extract_station_hours

        pl_vars, sl_vars, instants = self._payloads(drop_lsp_at)
        hours = extract_station_hours(pl_vars, sl_vars, self.LAT, self.LON)
        return assemble_series(hours), instants

    def test_accumulation_differencing(self) -> None:
        series, instants = self._series()
        profile = series["profile"]
        self.assertEqual(len(profile), 48)
        for instant, record in profile.items():
            self.assertAlmostEqual(record["precipitationMm"], 1.0, places=9)
            self.assertAlmostEqual(record["snowfallCm"], 0.2, places=9)
            self.assertEqual(len(record["levels"]), len(PRESSURE_LEVELS_HPA))
            self.assertAlmostEqual(record["temperature2mC"], -4.0)
        start = instants[0]
        self.assertAlmostEqual(series["accumu"][start]["tp_mm"], 1.0)
        self.assertEqual(series["sfc"][start]["dewpoint_2m"], 268.0)
        self.assertEqual(len(series["w"]), 48)
        self.assertEqual(len(series["q"]), 48)

    def test_gap_in_cycle_yields_none_not_a_spike(self) -> None:
        start = datetime(2024, 1, 10, 1, 0, tzinfo=UTC)
        gap = start + timedelta(hours=5)  # fh=6 lsp step removed
        series, _ = self._series(drop_lsp_at=gap)
        self.assertIsNone(series["profile"][gap]["precipitationMm"])
        after_gap = start + timedelta(hours=6)  # fh=7: previous step gone
        self.assertIsNone(series["profile"][after_gap]["precipitationMm"])
        self.assertAlmostEqual(
            series["profile"][start + timedelta(hours=4)]["precipitationMm"], 1.0)


class MetricTests(unittest.TestCase):
    def test_summarize(self) -> None:
        rows = [{
            "observed_slr": 10.0, "liquid_mm": 10.0, "snow_mm": 100.0,
            "champion_slr": 10.0, "kuchera_slr": 12.0,
            "wall_slr": 11.0, "w1500_slr": 9.0,
        } for _ in range(6)]
        out = ab.summarize(rows)
        self.assertEqual(out["champion"]["slr_mae"], 0.0)
        self.assertEqual(out["kuchera"]["slr_mae"], 2.0)
        self.assertEqual(out["kuchera"]["snowfall_mae_mm"], 20.0)
        self.assertEqual(out["champion_minus_kuchera_slr_mae"], -2.0)
        self.assertLess(out["champion_vs_kuchera_sign_test_p"], 0.05)


@unittest.skipUnless(
    (DATA_V1 / "model-v3" / "champion").exists()
    and (DATA_V1 / "model-v3" / "features_v3.parquet").exists(),
    "packaged champion / v3 features not present in this checkout",
)
class RegionSwapInvarianceTests(unittest.TestCase):
    def test_region_label_does_not_change_predictions(self) -> None:
        import pandas as pd

        from slr_calibration.champion import ChampionModel
        from slr_calibration.model_serving import predict_with_passthrough

        champion = ChampionModel.load(DATA_V1 / "model-v3" / "champion")
        features = list(champion.sidecar["features"])
        frame = pd.read_parquet(DATA_V1 / "model-v3" / "features_v3.parquet").head(3)
        base = frame[features].copy()
        base["site_id"] = frame["site_id"].values
        rockies = base.copy()
        rockies["region"] = "rockies"
        external = base.copy()
        external["region"] = "alpine-external"
        pred_a = predict_with_passthrough(champion, rockies)
        pred_b = predict_with_passthrough(champion, external)
        self.assertTrue((abs(pred_a - pred_b) < 1e-12).all())


if __name__ == "__main__":
    unittest.main()
