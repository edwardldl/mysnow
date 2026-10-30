from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta, timezone

from slr_calibration.gold_profiles import PRESSURE_LEVELS_HPA
from slr_calibration.gold_profiles_sp import required_hours
from slr_calibration.v3_features import (
    BUNDLES,
    DERIVED_FEATURES,
    build_event_v3_features,
    dewpoint_from_t_rh,
    es_ice_hpa,
    es_water_hpa,
    rh_from_q,
    saturation_vapour_pressure_hpa,
    wet_bulb_stull,
    _layer_measure,
)

LOCAL = timezone(timedelta(hours=-8))
UTC = timezone.utc
ELEV = 1000.0


def _t_of_z(z: float) -> float:
    return 1.0 - 0.005 * (z - ELEV)


def _z_of_p(p: float) -> float:
    return ELEV + (1000.0 - p) * 9.0


def _q_for_rh(rh_pct: float, p_hpa: float, t_c: float) -> float:
    e = rh_pct / 100.0 * saturation_vapour_pressure_hpa(t_c)
    return 0.622 * e / (p_hpa - 0.378 * e)


def _event() -> dict:
    return {
        "event_id": "ghcn:TEST0001:2021-01-15",
        "region": "rockies",
        "station_id": "TEST0001",
        "site_id": "TEST0001",
        "station_name": "Test",
        "state": "CO",
        "valid_time": datetime(2021, 1, 15, 7, 0, tzinfo=LOCAL),
        "observation_date": "2021-01-15",
        "water_year": 2021,
        "month": 1,
        "latitude": 39.0,
        "longitude": -106.0,
        "elevation_m": ELEV,
        "observed_slr": 12.0,
        "fresh_snow_mm": 120.0,
        "fresh_swe_mm": 10.0,
        "climate_regime": "continental",
        "source_tier": "ghcn",
    }


def _synthetic_inputs(event: dict, with_q: bool = True, with_sfc: bool = True):
    hours = required_hours(event)
    profile_hours = {}
    w_series, q_series, accumu, sfc = {}, {}, {}, {}
    for hour in hours:
        levels = []
        w_by_level, q_by_level = {}, {}
        for p in PRESSURE_LEVELS_HPA:
            z = _z_of_p(float(p))
            t = _t_of_z(z)
            levels.append((float(p), t, 95.0, 5.0 + (z - ELEV) * 0.001, z))
            w_by_level[int(p)] = -0.5
            q_by_level[int(p)] = _q_for_rh(90.0, float(p), t)
        profile_hours[hour] = {
            "temperature2mC": 0.5,
            "precipitationMm": 1.0,
            "snowfallCm": 1.2,
            "levels": levels,
        }
        w_series[hour] = w_by_level
        if with_q:
            q_series[hour] = q_by_level
        accumu[hour] = {"tp_mm": 1.0, "cp_mm": 0.25, "lsp_mm": 0.75}
        if with_sfc:
            sfc[hour] = {
                "dewpoint_2m": 272.15,
                "total_column_liquid_water": 0.1,
                "wind_u_10m": 3.0,
                "wind_v_10m": 4.0,
                "skin_temperature": 273.15,
            }
    return profile_hours, w_series, q_series, accumu, sfc


class SaturationFormulationTests(unittest.TestCase):
    def test_endpoints_and_blend(self):
        self.assertAlmostEqual(saturation_vapour_pressure_hpa(0.0), es_water_hpa(0.0))
        self.assertAlmostEqual(saturation_vapour_pressure_hpa(-23.0), es_ice_hpa(-23.0))
        blend = saturation_vapour_pressure_hpa(-11.5)
        self.assertLess(es_ice_hpa(-11.5), blend)
        self.assertLess(blend, es_water_hpa(-11.5))
        # quadratic blend: ice fraction at -11.5 C is (11.5/23)^2 = 0.25
        expected = 0.75 * es_water_hpa(-11.5) + 0.25 * es_ice_hpa(-11.5)
        self.assertAlmostEqual(blend, expected, places=9)

    def test_rh_from_q_roundtrip(self):
        q = _q_for_rh(80.0, 700.0, -15.0)
        self.assertAlmostEqual(rh_from_q(q, 700.0, -15.0), 80.0, places=6)

    def test_wet_bulb_bounds(self):
        self.assertAlmostEqual(wet_bulb_stull(10.0, 100.0), 10.0, delta=0.6)
        self.assertLess(wet_bulb_stull(10.0, 50.0), 10.0)
        self.assertIsNotNone(dewpoint_from_t_rh(0.0, 95.0))
        self.assertIsNone(dewpoint_from_t_rh(0.0, 0.0))


class LayerMeasureTests(unittest.TestCase):
    def test_linear_layer_thickness_and_mean(self):
        # t falls linearly from 0 C at z=1000 to -20 C at z=3000;
        # the -10..0 layer is exactly the lower 1000 m, mean t = -5.
        points = [
            {"z": 1000.0, "t": 0.0},
            {"z": 2000.0, "t": -10.0},
            {"z": 3000.0, "t": -20.0},
        ]
        thickness, means = _layer_measure(points, [("t", -10.0, 0.0)], ("t",))
        self.assertAlmostEqual(thickness, 1000.0, places=6)
        self.assertAlmostEqual(means["t"], -5.0, places=6)


class BuildEventTests(unittest.TestCase):
    def test_all_bundles_populated(self):
        event = _event()
        features, counts = build_event_v3_features(event, *_synthetic_inputs(event))
        self.assertEqual(counts["profile"], 24)  # 24 window sample instants
        for name in DERIVED_FEATURES:
            self.assertIsNotNone(features[name], name)
        habit_sum = (
            features["habit_frac_dendrite"]
            + features["habit_frac_plate"]
            + features["habit_frac_other"]
        )
        self.assertAlmostEqual(habit_sum, 1.0, places=3)
        self.assertAlmostEqual(features["convective_frac"], 0.25, places=4)
        self.assertAlmostEqual(features["fcst_liquid_mm"], 24.0, places=4)
        self.assertAlmostEqual(features["omega_dgz_pwt"], -0.5, places=4)
        self.assertGreater(features["dgz_depth_m_pwt"], 0.0)
        # surface t = 1 C falling at 5 C/km: 0 C crossing 200 m AGL
        self.assertAlmostEqual(features["freezing_level_m_agl_pwt"], 200.0, delta=30.0)
        self.assertAlmostEqual(features["ws10m_pwt"], 5.0, places=4)
        self.assertEqual(set(BUNDLES), {
            "dgz", "amount", "warm_layer", "riming", "ascent", "wind",
            "humidity", "surface", "aggregation", "subcloud", "regime",
        })

    def test_determinism(self):
        event = _event()
        first, _ = build_event_v3_features(event, *_synthetic_inputs(event))
        second, _ = build_event_v3_features(event, *_synthetic_inputs(event))
        self.assertEqual(first, second)

    def test_missing_q_blanks_only_humidity(self):
        event = _event()
        profile, w, _q, accumu, sfc = _synthetic_inputs(event)
        features, counts = build_event_v3_features(event, profile, w, {}, accumu, sfc)
        self.assertEqual(counts["q"], 0)
        for name in BUNDLES["humidity"]:
            self.assertIsNone(features[name], name)
        self.assertIsNotNone(features["dgz_t_pwt"])
        self.assertIsNotNone(features["omega_dgz_pwt"])

    def test_missing_sfc_blanks_only_surface(self):
        event = _event()
        profile, w, q, accumu, _sfc = _synthetic_inputs(event)
        features, counts = build_event_v3_features(event, profile, w, q, accumu, {})
        self.assertEqual(counts["sfc"], 0)
        for name in BUNDLES["surface"]:
            self.assertIsNone(features[name], name)
        self.assertIsNotNone(features["rhice_dgz"])


if __name__ == "__main__":
    unittest.main()
