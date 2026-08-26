from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from slr_calibration.sierra import import_ghcn_sierra

class SierraImportTests(unittest.TestCase):
    def test_direct_pairs_only_and_fixed_standard_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"US1CACV0012.csv").write_text(
                "US1CACV0012,20240101,SNOW,100,,,N,0800\n"
                "US1CACV0012,20240101,WESF,80,,,N,0800\n"
                "US1CACV0012,20240101,SNWD,900,,,N,0800\n"
                "US1CACV0012,20240102,SNOW,100,,,N,0800\n"
                "US1CACV0012,20240102,WESF,100,,,N,0800\n"
                "US1CACV0012,20240103,SNOW,100,A,,N,0800\n"
                "US1CACV0012,20240103,WESF,80,,,N,0800\n", encoding="utf-8")
            events,rejected,report=import_ghcn_sierra(root)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]["observed_slr"],12.5)
        self.assertTrue(events[0]["valid_time"].endswith("-08:00"))
        self.assertEqual(report["rawSameDatePairCount"],3)
        self.assertEqual({r.reason for r in rejected},{"SOURCE_N_EXACT_10_TO_1","MEASUREMENT_FLAG"})
        self.assertIn("RENO", report["mask"]["explicitlyExcludedPlaceNames"])

if __name__ == "__main__": unittest.main()
