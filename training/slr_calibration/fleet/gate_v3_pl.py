"""Verification gate for the Phase 3 v3 in-region w fetch.

Re-extracts a fixed list of (day, station) parts with the production
V3PlReader and the production payload construction (same rounding as
fetch_v3_pl), writes them to a scratch tree, and diffs every value
against the workspace-fetched reference parts.  Exit 0 only if every
field of every gate part matches within TOL (values are stored rounded
to 5 dp by both sides, so a pass should be exact; 1e-3 is the October
gate's tolerance).

Usage (on the instance, bundle venv):
    gate_v3_pl.py --root /opt/mysnow --reference /opt/gate_reference \
        --out /opt/gate_out --days 19991122,19991123,...
The reference dir mirrors pl_parts layout: {yyyymmdd}/{station}.json.gz
"""

import argparse
import gzip
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, "/opt/mysnow/training/slr_calibration/src")
from slr_calibration.gold_profiles import PRESSURE_LEVELS_HPA
from slr_calibration.gold_profiles_sp import (
    _parse_hour,
    _write_gzip_json,
    load_modelling_events,
    plan_sp_chunks,
    station_coordinates,
)
from slr_calibration.gold_profiles_v3 import V3PlReader, day_needs

TOL = 1e-3

# (yyyymmdd, station_id) -- all present in the workspace pl_parts tree
# when the gate list was drawn (47 parts landed by 2026-10-30 ~08:45 UTC).
GATE_PARTS = [
    ("19981016", "US1COMT0005"),
    ("19981209", "US1COLR0255"),
    ("19981209", "US1COLR0260"),
    ("19990927", "US1COLR0270"),
    ("19991016", "US1COLR0495"),
    ("19991016", "US1COLR0501"),
    ("19991122", "US1COLR0255"),
    ("19991122", "US1COLR0260"),
    ("19991122", "US1COLR0270"),
    ("19991122", "US1COLR0495"),
    ("19991122", "US1COLR0500"),
    ("19991122", "US1COLR0501"),
    ("19991122", "US1COMT0005"),
    ("19991123", "US1COLR0260"),
    ("19991123", "US1COLR0270"),
    ("19991123", "US1COLR0500"),
    ("19991123", "US1COLR0501"),
    ("19991123", "US1COMT0005"),
    ("20051231", "US1CORT0016"),
    ("20051231", "US1COSU0008"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/opt/mysnow"))
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    model_dir = args.root / "data/slr-training-data/screened-v2/model-v1"
    pool_dir = args.root / "data/slr-training-data/screened-v2"
    events = load_modelling_events(pool_dir, model_dir / "era5-expanded-v2" / "features.parquet")
    needs = day_needs(plan_sp_chunks(events))
    coords = station_coordinates(events)
    reader = V3PlReader()

    by_day: dict[date, list[str]] = {}
    for day_text, sid in GATE_PARTS:
        day = date(int(day_text[:4]), int(day_text[4:6]), int(day_text[6:8]))
        by_day.setdefault(day, []).append(sid)

    failures: list[str] = []
    max_diff = 0.0
    compared = 0
    for day, sids in sorted(by_day.items()):
        expected = needs[day]
        stations = [(sid, coords[sid][0], coords[sid][1]) for sid in sorted(sids)]
        hours_dt = {sid: [_parse_hour(t) for t in expected[sid]] for sid in sids}
        values = reader.read_day(day, stations, hours_dt)
        for sid in sids:
            hours = expected[sid]
            payload: dict = {"time": hours}
            for level in PRESSURE_LEVELS_HPA:
                payload[f"w_{level}hPa"] = [
                    round(values[(sid, _parse_hour(t))][level], 5)
                    if values.get((sid, _parse_hour(t)), {}).get(level) is not None
                    else None
                    for t in hours
                ]
            out_path = args.out / f"{day:%Y%m%d}" / f"{sid}.json.gz"
            _write_gzip_json(out_path, payload)
            ref_path = args.reference / f"{day:%Y%m%d}" / f"{sid}.json.gz"
            if not ref_path.exists():
                failures.append(f"{day:%Y%m%d}/{sid}: reference part missing")
                continue
            with gzip.open(ref_path, "rt", encoding="utf-8") as handle:
                ref = json.load(handle)
            if ref.get("time") != payload["time"]:
                failures.append(f"{day:%Y%m%d}/{sid}: time axis differs")
                continue
            for level in PRESSURE_LEVELS_HPA:
                field = f"w_{level}hPa"
                for i, (mine, theirs) in enumerate(zip(payload[field], ref[field])):
                    compared += 1
                    if mine is None or theirs is None:
                        if mine is not theirs:
                            failures.append(f"{day:%Y%m%d}/{sid} {field}[{i}]: None mismatch")
                        continue
                    diff = abs(mine - theirs)
                    max_diff = max(max_diff, diff)
                    if diff > TOL:
                        failures.append(
                            f"{day:%Y%m%d}/{sid} {field}[{i}]: {mine} vs {theirs} (diff {diff})"
                        )
    print(f"gate parts: {len(GATE_PARTS)}, values compared: {compared}, max abs diff: {max_diff}")
    if failures:
        print(f"GATE FAIL ({len(failures)} problems):")
        for line in failures[:50]:
            print(" ", line)
        return 1
    print("GATE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
