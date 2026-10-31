"""Regression tests appended to tests/test_model_serving.py content:
HRRR cell search must handle 0..360 grid longitudes vs -180..180 points.
"""
import unittest

import numpy as np

from slr_calibration.model_serving import hrrr_cell_indices


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


if __name__ == "__main__":
    unittest.main()
