from __future__ import annotations

import unittest

from slr_calibration.snotel_track import SNOTEL_CSV_COLUMNS, merge_snotel_rows, parse_awdb_data

META = {
    "1039:UT:SNTL": {
        "station_triplet": "1039:UT:SNTL",
        "station_name": "Cascade Mountain",
        "state": "UT",
        "latitude": 40.283,
        "longitude": -111.60992,
        "elevation_m": 2362.2,
    },
}


def awdb_payload(wteq_values: list[tuple[str, float]]) -> list[dict]:
    return [{
        "stationTriplet": "1039:UT:SNTL",
        "data": [
            {"stationElement": {"elementCode": "WTEQ", "storedUnitCode": "in"},
             "values": [{"date": day, "value": value} for day, value in wteq_values]},
            {"stationElement": {"elementCode": "SNWD", "storedUnitCode": "in"},
             "values": [{"date": day, "value": value * 2} for day, value in wteq_values]},
            {"stationElement": {"elementCode": "TAVG", "storedUnitCode": "degF"},
             "values": [{"date": day, "value": 30.0} for day, _ in wteq_values]},
        ],
    }]


class SnotelTrackTests(unittest.TestCase):
    def test_parse_awdb_data_matches_existing_csv_schema(self) -> None:
        rows = parse_awdb_data(awdb_payload([("2026-10-01", 0.1), ("2026-10-02", 0.3)]), META)
        self.assertEqual(len(rows), 2)
        self.assertEqual(list(rows[0].keys()), SNOTEL_CSV_COLUMNS)
        self.assertEqual(rows[0]["station_name"], "Cascade Mountain")
        self.assertEqual(rows[1]["swe_inches"], "0.3")
        self.assertEqual(rows[1]["snow_depth_inches"], "0.6")
        self.assertEqual(rows[1]["tavg_f"], "30.0")

    def test_parse_converts_celsius_stored_units_to_fahrenheit(self) -> None:
        payload = [{
            "stationTriplet": "1039:UT:SNTL",
            "data": [{"stationElement": {"elementCode": "TAVG", "storedUnitCode": "degC"},
                       "values": [{"date": "2026-10-01", "value": 0.0}]}],
        }]
        rows = parse_awdb_data(payload, META)
        self.assertEqual(rows[0]["tavg_f"], "32.0")

    def test_merge_dedupes_by_station_and_date(self) -> None:
        existing = parse_awdb_data(awdb_payload([("2026-10-01", 0.1), ("2026-10-02", 0.3)]), META)
        new = parse_awdb_data(awdb_payload([("2026-10-02", 0.4), ("2026-10-03", 0.5)]), META)
        merged, counts = merge_snotel_rows(existing, new)
        self.assertEqual(counts, {"added": 1, "updated": 1, "total": 3})
        by_date = {row["date"]: row for row in merged}
        self.assertEqual(by_date["2026-10-02"]["swe_inches"], "0.4", "fresh fetch wins on conflict")
        merged_again, counts_again = merge_snotel_rows(merged, new)
        self.assertEqual(counts_again["added"], 0)
        self.assertEqual(counts_again["total"], 3)
        self.assertEqual(len(merged_again), 3)


if __name__ == "__main__":
    unittest.main()
