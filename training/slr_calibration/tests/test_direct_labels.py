from __future__ import annotations

import unittest

from slr_calibration.direct_labels import (
    HygieneConfig,
    ScreenConfig,
    apply_hygiene,
    classify_station,
    compute_screen_features,
    match_pletcher_sites,
    parse_ghcn_long_text,
    parse_ghcn_wide_text,
    station_pairs,
)

CONFIG = ScreenConfig()
HYGIENE = HygieneConfig()


def _day(snow=None, wesf=None, prcp=None):
    return {
        "snow_mm": snow,
        "wesf_mm": wesf,
        "prcp_mm": prcp,
        "snwd_mm": None,
        "snow_sflag": "N",
        "wesf_sflag": "N",
        "obs_time": "0700",
        "accumulated_snow": False,
        "accumulated_wesf": False,
        "qflag_failed": False,
    }


def _history(n_days, wesf_of_prcp, *, snow_mm=80.0, prcp_mm=8.0, presence=1.0):
    """Synthetic station history: paired days where WESF is wesf_of_prcp(prcp)."""
    days = {}
    for i in range(n_days):
        # (month, day) pairs repeat with period lcm(12, 28) = 84; the year
        # offset keeps keys unique for longer synthetic histories.
        key = f"{2023 + (i // 84)}{(i % 12) + 1:02d}{(i % 28) + 1:02d}"
        if i / n_days < presence:
            days[key] = _day(snow=snow_mm, wesf=wesf_of_prcp(prcp_mm, i), prcp=prcp_mm)
        else:
            days[key] = _day(snow=snow_mm, wesf=None, prcp=prcp_mm)
    return days


class HygieneTests(unittest.TestCase):
    def test_boundaries(self) -> None:
        rows = [
            {"snow_mm": 50.0, "wesf_mm": 5.0},    # snow below minimum
            {"snow_mm": 51.0, "wesf_mm": 2.7},    # wesf below minimum
            {"snow_mm": 51.0, "wesf_mm": 5.1},    # SLR exactly 10
            {"snow_mm": 51.0, "wesf_mm": 30.0},   # SLR < 2
            {"snow_mm": 300.0, "wesf_mm": 5.0},   # SLR > 50
            {"snow_mm": 100.0, "wesf_mm": 8.0},   # kept (SLR 12.5)
            {"snow_mm": 100.0, "wesf_mm": 2.0},   # fails wesf first (also SLR 50 ok)
        ]
        kept, report = apply_hygiene(rows, HYGIENE)
        self.assertEqual(len(kept), 1)
        self.assertEqual(report["inputRows"], 7)
        self.assertEqual(report["keptRows"], 1)
        seq = report["rejectedSequential"]
        self.assertEqual(seq["snow_below_minimum"], 1)
        self.assertEqual(seq["wesf_below_minimum"], 2)
        self.assertEqual(seq["slr_exactly_10_placeholder"], 1)
        self.assertEqual(seq["slr_below_minimum"], 1)
        self.assertEqual(seq["slr_above_maximum"], 1)
        self.assertEqual(sum(seq.values()) + report["keptRows"] + report["invalidPairs"], 7)
        # Standalone: the snow-starved first row also has SLR exactly 10.
        self.assertEqual(report["standalone"]["slr_exactly_10_placeholder"], 2)

    def test_invalid_pairs_counted(self) -> None:
        kept, report = apply_hygiene([{"snow_mm": None, "wesf_mm": 3.0}, {"snow_mm": 60.0, "wesf_mm": 0.0}], HYGIENE)
        self.assertEqual(kept, [])
        self.assertEqual(report["invalidPairs"], 2)


class ParserTests(unittest.TestCase):
    LONG = (
        "US1XX0001,20240105,SNOW,80,,,N,0700\n"
        "US1XX0001,20240105,WESF,64,,,N,0700\n"
        "US1XX0001,20240105,PRCP,58,,,N,0700\n"
        "US1XX0001,20240106,SNOW,30,,,N,0700\n"
        "US1XX0001,20240106,WESF,25,,,N,0700\n"
        "US1XX0001,20240106,DAPR,2,,,N,0700\n"
    )
    WIDE = (
        '"STATION","DATE","PRCP","PRCP_ATTRIBUTES","SNOW","SNOW_ATTRIBUTES","WESF","WESF_ATTRIBUTES","DAPR","DAPR_ATTRIBUTES"\n'
        '"US1XX0001","2024-01-05","58",",,N","80",",,N","64",",,N","",""\n'
        '"US1XX0001","2024-01-06","20",",,N","30",",,N","25",",,N","2",""\n'
    )

    def test_long_and_wide_agree(self) -> None:
        long_days = parse_ghcn_long_text(self.LONG)
        wide_days = parse_ghcn_wide_text(self.WIDE)
        self.assertEqual(long_days["20240105"]["wesf_mm"], 6.4)
        self.assertEqual(wide_days["20240105"]["wesf_mm"], 6.4)
        long_pairs = station_pairs(long_days)
        wide_pairs = station_pairs(wide_days)
        self.assertEqual(len(long_pairs), 1)
        self.assertEqual(len(wide_pairs), 1)
        self.assertEqual(long_pairs[0]["snow_mm"], 80.0)
        self.assertAlmostEqual(long_pairs[0]["wesf_mm"], 6.4)


class ScreenTests(unittest.TestCase):
    def test_copier_rejected(self) -> None:
        # WESF identical to gauge catch on every day, incl. big days.
        days = _history(60, lambda prcp, i: prcp, prcp_mm=12.0)
        features = compute_screen_features(days, CONFIG)
        self.assertEqual(features["equality_rate_big"], 1.0)
        self.assertEqual(features["excess_rate_big"], 0.0)
        decision, reason, tier = classify_station(features, "cocorahs", CONFIG)
        self.assertEqual(decision, "rejected")
        self.assertEqual(reason, "gauge_copy_signature")
        self.assertIsNone(tier)

    def test_genuine_core_sampler_admitted(self) -> None:
        # Core SWE exceeds (undercaught) catch on most big days, with scatter.
        days = _history(60, lambda prcp, i: prcp * (1.25 if i % 3 else 0.95))
        features = compute_screen_features(days, CONFIG)
        decision, reason, tier = classify_station(features, "cocorahs", CONFIG)
        self.assertEqual(decision, "admitted")
        self.assertEqual(tier, "ghcn-cocorahs-screened")

    def test_sporadic_reporter_rejected(self) -> None:
        # 100 snow days, SWE on only 30% of them: enough pairs to judge
        # (>= 20) but far below the presence floor.
        days = _history(100, lambda prcp, i: prcp * 1.25, presence=0.3)
        features = compute_screen_features(days, CONFIG)
        decision, reason, _ = classify_station(features, "cocorahs", CONFIG)
        self.assertEqual(decision, "rejected")
        self.assertEqual(reason, "sporadic_swe_reporting")

    def test_insufficient_history_rejected(self) -> None:
        days = _history(10, lambda prcp, i: prcp * 1.25)
        features = compute_screen_features(days, CONFIG)
        decision, reason, _ = classify_station(features, "cocorahs", CONFIG)
        self.assertEqual(decision, "rejected")
        self.assertEqual(reason, "insufficient_history")

    def test_exact10_heaper_rejected(self) -> None:
        # Observer reports depth as exactly 10x SWE on most candidate pairs.
        days = {}
        for i in range(40):
            key = f"2024{(i % 12) + 1:02d}{(i % 28) + 1:02d}"
            days[key] = _day(snow=100.0, wesf=10.0, prcp=9.0)
        features = compute_screen_features(days, CONFIG)
        self.assertGreater(features["exact10_rate"], 0.15)
        decision, reason, _ = classify_station(features, "cocorahs", CONFIG)
        self.assertEqual(decision, "rejected")
        self.assertEqual(reason, "slr10_heaping")

    def test_coop_admitted_on_protocol_despite_equality(self) -> None:
        # COOP WESF is gauge-derived by protocol: equality is not copying.
        # (snow 90 / catch 8 keeps SLR off the exact-10 heaping floor.)
        days = _history(60, lambda prcp, i: prcp, snow_mm=90.0)
        features = compute_screen_features(days, CONFIG)
        decision, reason, tier = classify_station(features, "coop", CONFIG)
        self.assertEqual(decision, "admitted")
        self.assertEqual(reason, "coop_protocol")
        self.assertEqual(tier, "ghcn-coop")

    def test_few_big_days_fallback_rule(self) -> None:
        # Small-event station: no big days, but whole history is copied.
        days = _history(40, lambda prcp, i: prcp, snow_mm=30.0, prcp_mm=2.0)
        features = compute_screen_features(days, CONFIG)
        self.assertLess(features["n_big"], CONFIG.min_big_days)
        decision, reason, _ = classify_station(features, "cocorahs", CONFIG)
        self.assertEqual(decision, "rejected")
        self.assertEqual(reason, "gauge_copy_signature")


class PletcherMatchTests(unittest.TestCase):
    def test_exact_and_tolerance_matching(self) -> None:
        metadata = {
            "US1AAAA0001": {"station_id": "US1AAAA0001", "station_name": "A", "state": "CO", "latitude": 39.0, "longitude": -105.0, "elevation_m": 2000.0},
            "US1BBBB0002": {"station_id": "US1BBBB0002", "station_name": "B", "state": "CO", "latitude": 40.0, "longitude": -106.0, "elevation_m": 2100.0},
        }
        sites = [(39.0, -105.0, 2000.0), (40.0009, -106.0, 2100.0), (35.0, -100.0, 500.0)]
        matched, report = match_pletcher_sites(sites, metadata)
        self.assertEqual(matched, {"US1AAAA0001", "US1BBBB0002"})
        self.assertEqual(report["exactMatches"], 1)
        self.assertEqual(report["toleranceMatches"], 1)
        self.assertEqual(report["unmatchedSites"], 1)


if __name__ == "__main__":
    unittest.main()
