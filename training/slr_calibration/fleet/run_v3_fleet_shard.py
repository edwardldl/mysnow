"""In-region fleet runner for the Phase 3 v3 PL fetch (w; q when enabled).

Mirrors the workspace runner /tmp/run_pl_shard.py (same planning, same
part contract, single-threaded) but rooted at the instance's /opt/mysnow
bundle and fleet-aware:

* summaries land in model-v3-features/fleet_summaries/ so they never
  collide with the workspace run's v3_pl_fetch_summary_shard{i}.json;
* exit code is honest (October round-1 lesson): 1 when any day failed,
  so the user-data endgame writes _FAILED instead of _DONE.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, "/opt/mysnow/training/slr_calibration/src")
from slr_calibration.gold_profiles_sp import load_modelling_events
from slr_calibration.gold_profiles_v3 import V3_DIR_NAME, fetch_v3_pl

parser = argparse.ArgumentParser()
parser.add_argument("--shard-index", type=int, required=True)
parser.add_argument("--shard-count", type=int, required=True)
parser.add_argument("--root", type=Path, default=Path("/opt/mysnow"))
args = parser.parse_args()

MODEL_DIR = args.root / "data/slr-training-data/screened-v2/model-v1"
POOL_DIR = args.root / "data/slr-training-data/screened-v2"
events = load_modelling_events(POOL_DIR, MODEL_DIR / "era5-expanded-v2" / "features.parquet")
v3_dir = MODEL_DIR / V3_DIR_NAME
v3_dir.mkdir(parents=True, exist_ok=True)
summary = fetch_v3_pl(
    events,
    v3_dir,
    shard_index=args.shard_index,
    shard_count=args.shard_count,
    max_workers=1,
)
out_dir = v3_dir / "fleet_summaries"
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / f"v3_pl_fetch_summary_shard{args.shard_index}.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True)
)
print(
    f"V3 FLEET SHARD {args.shard_index}/{args.shard_count} DONE",
    json.dumps({k: summary[k] for k in ("partsWritten", "partsSkippedValid", "pointsRead")}),
    "daysFailed:",
    len(summary["daysFailed"]),
    flush=True,
)
sys.exit(1 if summary["daysFailed"] else 0)
