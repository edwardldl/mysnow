"""Correction study, diagnostic stage: score the ERA5-masked arm.

Reuses the item-7 runner's own stage_score with its OUT redirected to
model-v3-hrrr-correction/masked/, whose features_hrrr_v3.parquet holds
the ERA5-masked feature rows for the reduced cohort's 5,340 paired
events (see build_masked_era5_features.py). The integrity gate and the
paired construction are therefore identical to item 7's; in the paired
outputs, the column named hrrr_pred carries the MASKED-arm ENS-EB
predictions and era5_pred the stored queue-1 cache predictions.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "data" / "slr-training-data" / "screened-v2" / "model-v1"


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "runner7", Path(__file__).with_name("run_hrrr_transfer_v3.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    runner = _load_runner()
    runner.OUT = BASE / "model-v3-hrrr-correction" / "masked"
    runner.stage_score(None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
