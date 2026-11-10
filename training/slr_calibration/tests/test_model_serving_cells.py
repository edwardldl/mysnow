"""Regression tests appended to tests/test_model_serving.py content:
HRRR cell search must handle 0..360 grid longitudes vs -180..180 points.
"""
import unittest

import numpy as np

from slr_calibration.model_serving import (
    HRRR_DOMAIN_REASON,
    HRRR_POLICY_REASON,
    hrrr_cell_distances_km,
    hrrr_cell_indices,
    hrrr_domain_exclusion,
)


class HrrrCellIndicesTest(unittest.TestCase):
    def _grid(self):
        # 3x4 synthetic grid, longitudes stored 0..360
        lats = np.array([[40.0, 40.0, 40.0, 40.0],
                         [39.0, 39.0, 39.0, 39.0],
                         [38.0, 38.0, 38.0, 38.0]])
        lons = np.array([[230.0, 240.0, 250.0, 260.0],
                         [230.0, 240.0, 250.0, 260.0],
                         [230.0, 240.0, 250.0, 260.0]])
        return lats, lons

    def test_negative_longitude_point(self):
        lats, lons = self._grid()
        # Palisades-like point: 39.2 N, -120.24 == 239.76 in 0..360
        cells = hrrr_cell_indices(lats, lons, [{"latitude": 39.2, "longitude": -120.24}])
        flat = cells[0]
        self.assertAlmostEqual(float(lats.ravel()[flat]), 39.0)
        self.assertAlmostEqual(float(lons.ravel()[flat]), 240.0)

    def test_positive_longitude_point(self):
        lats, lons = self._grid()
        cells = hrrr_cell_indices(lats, lons, [{"latitude": 38.1, "longitude": -100.0}])
        flat = cells[0]
        self.assertAlmostEqual(float(lons.ravel()[flat]), 260.0)


class HrrrCellDistancesTest(unittest.TestCase):
    """The serving batch's HRRR domain guard: on-domain points sit within
    ~half a grid diagonal of a cell centre; off-domain points (Alaska,
    far Canada) are far away. The guard threshold is 10 km."""

    def _grid(self):
        lats = np.array([[40.0, 40.0, 40.0, 40.0],
                         [39.0, 39.0, 39.0, 39.0],
                         [38.0, 38.0, 38.0, 38.0]])
        lons = np.array([[230.0, 240.0, 250.0, 260.0],
                         [230.0, 240.0, 250.0, 260.0],
                         [230.0, 240.0, 250.0, 260.0]])
        return lats, lons

    def test_point_on_cell_centre_is_zero(self):
        lats, lons = self._grid()
        dists = hrrr_cell_distances_km(
            lats, lons, [{"latitude": 39.0, "longitude": -120.0}])
        self.assertAlmostEqual(dists[0], 0.0)

    def test_nearby_point_distance_in_km(self):
        lats, lons = self._grid()
        dists = hrrr_cell_distances_km(
            lats, lons, [{"latitude": 39.2, "longitude": -120.24}])
        # Nearest cell (39.0, 240.0): 0.2 deg lat, 0.24 deg lon away.
        expected = (22.264 ** 2
                    + (0.24 * 111.32 * np.cos(np.radians(39.2))) ** 2) ** 0.5
        self.assertAlmostEqual(dists[0], expected, places=6)

    def test_far_off_domain_point_exceeds_guard_threshold(self):
        lats, lons = self._grid()
        # Alyeska-like point against a CONUS-like grid.
        dists = hrrr_cell_distances_km(
            lats, lons, [{"latitude": 60.96, "longitude": -149.09}])
        self.assertGreater(dists[0], 10.0)


class HrrrDomainExclusionTest(unittest.TestCase):
    """The HRRR serving guard separates a serving-policy exclusion
    (non-US points, even where the grid footprint overlaps southern
    Canada) from a geometric exclusion (US points implausibly far from
    any cell centre). Codex review 2026-11-10."""

    def test_us_point_near_a_cell_is_served(self):
        self.assertIsNone(hrrr_domain_exclusion("United States", 2.2))
        self.assertIsNone(hrrr_domain_exclusion("United States", 10.0))

    def test_us_point_far_from_any_cell_is_geometrically_excluded(self):
        self.assertEqual(
            hrrr_domain_exclusion("United States", 10.1), HRRR_DOMAIN_REASON)
        self.assertEqual(
            hrrr_domain_exclusion("United States", 900.0), HRRR_DOMAIN_REASON)

    def test_canadian_point_is_policy_excluded_even_inside_the_footprint(self):
        self.assertEqual(hrrr_domain_exclusion("Canada", 1.0), HRRR_POLICY_REASON)

    def test_policy_clause_takes_precedence_over_geometric(self):
        self.assertEqual(
            hrrr_domain_exclusion("Canada", 900.0), HRRR_POLICY_REASON)


if __name__ == "__main__":
    unittest.main()
