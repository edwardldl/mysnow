#!/usr/bin/env python3
"""Tiered-cadence merge runs for MySnow model serving (2026-11-16).

The twice-daily full batches (run_model_serving.py at 00Z/12Z) remain
the backbone: the only source of ECMWF leads 7-10 and the only data
state that is git-committed. Between them, merge runs refresh the
windows newer cycles can already see, layered over a persistent
working state:

  hrrr    hourly: score the HRRR arm from the latest posted cycle
          (48 h runs at 00/06/12/18Z, 18 h runs otherwise) and replace
          the HRRR records for the windows it covers.
  ecmwf   at 06Z/18Z: score ECMWF to its step-144 ceiling (leads ~1-6)
          plus HRRR from the same cycle, and replace those records.

A merge never extends coverage (windows absent from the state are
skipped), never moves a window backward (a record is replaced only by
a strictly newer cycle's record), and never stacks the isotonic layer
(the state is kept RAW; the layer is applied fresh to the merged state
on every install). Every record carries its scoring cycle in
``cycleUtc``; the installed frontend's ``cycles`` map reports the
newest contributing cycle per arm.

State: data/tracking/model-serving/merge-state/state.json (raw merged
frontend) guarded by an flock lockfile in the same directory; a run
that finds the lock held exits 3 and changes nothing. ``reset``
re-seeds the state from an installed full-batch frontend (used by the
full-batch jobs after each install). Installs write
src/data/model-forecasts.json WITHOUT committing (the next full batch
resets the file with its own commit) and publish to Vercel Blob via
publish_model_serving_blob.py unless --no-publish is given.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slr_calibration.champion import ChampionModel  # noqa: E402
from slr_calibration.model_serving import (  # noqa: E402
    SOURCE_ID_ECMWF,
    SOURCE_ID_HRRR,
    assert_frontend_within_lead_cap,
    apply_isotonic_slr_layer_gated,
    load_catalogue,
)

ARM_CEILING_HOURS = {"ecmwf": 240, "hrrr": 48}
DEFAULT_STATE_DIR = "data/tracking/model-serving/merge-state"
REGISTRY = "data/tracking/registry.json"
CHAMPION_DIR = "data/slr-training-data/screened-v2/model-v1/model-v3/champion"
TRACKING_DIR = "data/tracking/model-serving"
PUBLISHER = "training/slr_calibration/publish_model_serving_blob.py"


def _load_runner():
    path = Path(__file__).with_name("run_model_serving.py")
    spec = importlib.util.spec_from_file_location("run_model_serving", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LockHeld(Exception):
    pass


class MergeError(Exception):
    pass


@contextmanager
def state_lock(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(state_dir / "state.lock", os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockHeld(f"merge state lock is held: {state_dir / 'state.lock'}")
        yield
    finally:
        os.close(fd)


def parse_iso(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Frontend transforms (pure; unit-tested)
# ---------------------------------------------------------------------------

def deraw_frontend(frontend: dict) -> dict:
    """Strip the isotonic layer: restore pre-adjustment values and drop
    its provenance, returning the raw frontend the layer was applied to."""
    out = copy.deepcopy(frontend)
    out.pop("consistencyAdjustment", None)
    for point in out["points"]:
        for rows in point["forecasts"].values():
            for record in rows:
                if "preIsotonicSlr" in record:
                    record["modelSlr"] = record.pop("preIsotonicSlr")
                    record["modelSnowfallCm"] = record.pop("preIsotonicSnowfallCm")
    return out


def stamp_record_cycles(frontend: dict) -> dict:
    """Ensure every record carries cycleUtc (defaulting to its arm's
    frontend-level cycle). Returns the frontend for chaining."""
    for point in frontend["points"]:
        for arm, rows in point["forecasts"].items():
            for record in rows:
                record.setdefault("cycleUtc", frontend["cycles"][arm])
    return frontend


def frontend_record(flat: dict) -> dict:
    """The frontend (camelCase) shape of one scored flat record — the
    same mapping the full runner's frontend assembly performs, plus the
    record's scoring cycle."""
    return {
        "leadDay": flat["lead_day"],
        "windowStartUtc": flat["window_start_utc"],
        "windowEndUtc": flat["window_end_utc"],
        "windowEndLocalDate": flat["window_end_local_date"],
        "modelSlr": flat.get("model_slr"),
        "modelSnowfallCm": flat.get("model_snowfall_cm"),
        "kucheraSlr": flat.get("kuchera_slr"),
        "kucheraSnowfallCm": flat.get("kuchera_snowfall_cm"),
        "liquidMm": flat.get("liquid_mm"),
        "cycleUtc": flat["cycle_utc"],
    }


def record_index(frontend: dict) -> dict[tuple[str, str, str], dict]:
    index: dict[tuple[str, str, str], dict] = {}
    for point in frontend["points"]:
        for arm, rows in point["forecasts"].items():
            for record in rows:
                index[(point["pointId"], arm, record["windowEndUtc"])] = record
    return index


def window_sets(frontend: dict) -> dict[tuple[str, str], set[str]]:
    sets: dict[tuple[str, str], set[str]] = {}
    for point in frontend["points"]:
        for arm, rows in point["forecasts"].items():
            sets[(point["pointId"], arm)] = {r["windowEndUtc"] for r in rows}
    return sets


def merge_frontend(state: dict, new_records: dict[tuple[str, str], list[dict]]) -> tuple[dict, dict]:
    """Replace state records with strictly newer scored records.

    new_records maps (pointId, arm) -> frontend-shaped records. Returns
    (merged_state, stats); raises MergeError on any gate failure. The
    input state is mutated (callers pass a working copy)."""
    before = copy.deepcopy(state)
    before_index = record_index(before)
    index = record_index(state)
    replaced_keys: set[tuple[str, str, str]] = set()
    stats = {"replaced": 0, "skippedAbsent": 0, "skippedStale": 0}

    for (point_id, arm), rows in new_records.items():
        for record in rows:
            key = (point_id, arm, record["windowEndUtc"])
            current = index.get(key)
            if current is None:
                stats["skippedAbsent"] += 1
                continue
            if record["cycleUtc"] <= current["cycleUtc"]:
                stats["skippedStale"] += 1
                continue
            current.clear()
            current.update(record)
            replaced_keys.add(key)
            stats["replaced"] += 1

    check_merge_gates(before, state, replaced_keys)
    return state, stats


def check_merge_gates(before: dict, after: dict, replaced_keys: set[tuple[str, str, str]]) -> None:
    # G1: window coverage is identical — merges refresh, never reshape.
    if window_sets(before) != window_sets(after):
        raise MergeError("gate G1: window coverage changed during merge")
    before_index = record_index(before)
    after_index = record_index(after)
    for key, record in after_index.items():
        # G2: records outside the refresh set are bit-identical.
        if key not in replaced_keys and record != before_index[key]:
            raise MergeError(f"gate G2: untouched record changed: {key}")
        # G4: no record reaches beyond its own cycle's arm ceiling.
        cycle = parse_iso(record["cycleUtc"])
        end = parse_iso(record["windowEndUtc"])
        if (end - cycle).total_seconds() > ARM_CEILING_HOURS[key[1]] * 3600:
            raise MergeError(f"gate G4: record beyond its cycle ceiling: {key}")
    # G3: replaced records all carry one consistent cycle per arm.
    cycles_by_arm: dict[str, set[str]] = {}
    for point_id, arm, _end in replaced_keys:
        cycles_by_arm.setdefault(arm, set()).add(after_index[(point_id, arm, _end)]["cycleUtc"])
    for arm, cycles in cycles_by_arm.items():
        if len(cycles) != 1:
            raise MergeError(f"gate G3: replaced {arm} records carry cycles {sorted(cycles)}")


def recompute_cycles(frontend: dict) -> dict:
    cycles: dict[str, str] = {}
    for arm in ("ecmwf", "hrrr"):
        values = [
            record["cycleUtc"]
            for point in frontend["points"]
            for record in point["forecasts"].get(arm, [])
        ]
        if values:
            cycles[arm] = max(values)
    frontend["cycles"] = cycles
    return frontend


def finalise_frontend(state: dict) -> tuple[dict, dict]:
    """The installable frontend for a raw state: fresh timestamp and
    per-arm cycles, lead-cap assertion, isotonic layer applied fresh."""
    out = recompute_cycles(copy.deepcopy(state))
    out["generatedAtUtc"] = datetime.now(timezone.utc).isoformat()
    assert_frontend_within_lead_cap(out)
    adjusted, report = apply_isotonic_slr_layer_gated(out, enabled=True)
    return adjusted, report


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------

def load_state(state_dir: Path) -> dict:
    path = state_dir / "state.json"
    if not path.is_file():
        raise MergeError(f"no merge state at {path}; run `reset` from an installed frontend first")
    return json.loads(path.read_text())


def save_state(state_dir: Path, state: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = state_dir / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(state_dir / "state.json")


def reset_state(frontend_path: Path, state_dir: Path) -> dict:
    frontend = json.loads(frontend_path.read_text())
    state = stamp_record_cycles(deraw_frontend(frontend))
    recompute_cycles(state)
    save_state(state_dir, state)
    return {"action": "reset", "cycles": state["cycles"],
            "records": sum(len(rows) for p in state["points"] for rows in p["forecasts"].values())}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def load_model_and_points(repo: Path):
    points = load_catalogue(repo / REGISTRY)
    champion_dir = repo / CHAMPION_DIR
    sidecar = json.loads((champion_dir / "champion_sidecar.json").read_text())
    model = ChampionModel.load(champion_dir)
    return points, model, sidecar["features"]


def group_frontend_records(flat_records: list[dict]) -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for flat in flat_records:
        if flat["missing"]:
            continue
        grouped.setdefault((flat["point_id"], flat["source"]), []).append(frontend_record(flat))
    return grouped


def append_tracking(repo: Path, flat_records: list[dict], cycle_compact: str) -> None:
    path = repo / TRACKING_DIR / f"records-{cycle_compact}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        for record in flat_records:
            fh.write(json.dumps(record) + "\n")


def score_hrrr(rms, points, model, features, date: str, hour: int, max_step: int):
    series, _t2m = rms.build_hrrr_series(points, date, hour, max_step=max_step)
    cycle_dt = datetime.strptime(date, "%Y%m%d").replace(hour=hour, tzinfo=timezone.utc)
    records, _feats = rms.score_arm("hrrr", SOURCE_ID_HRRR, [1, 2], cycle_dt,
                                    points, series, model, features)
    return records


def score_ecmwf(rms, points, model, features, date: str, cycle: int):
    series, _t2m = rms.build_ecmwf_series(points, date, cycle, max_step=144)
    cycle_dt = datetime.strptime(date, "%Y%m%d").replace(hour=cycle, tzinfo=timezone.utc)
    records, _feats = rms.score_arm("ecmwf", SOURCE_ID_ECMWF, list(range(1, 11)), cycle_dt,
                                    points, series, model, features)
    return records


# ---------------------------------------------------------------------------
# Merge execution
# ---------------------------------------------------------------------------

def run_merge(args, new_records: dict[tuple[str, str], list[dict]],
              flat_records: list[dict], run_cycles: dict[str, str]) -> dict:
    repo = Path(args.repo)
    state_dir = repo / args.state_dir
    with state_lock(state_dir):
        state = load_state(state_dir)
        merged, stats = merge_frontend(state, new_records)
        if stats["replaced"] == 0:
            raise MergeError(f"nothing newer to merge (skipped: {stats}); state and install untouched")
        installed, isotonic_report = finalise_frontend(merged)
        out_path = repo / args.frontend_out
        out_path.write_text(json.dumps(installed, indent=1))
        save_state(state_dir, merged)
    newest = max(run_cycles.values())
    cycle_compact = newest[:13].replace("-", "").replace(":", "") + "Z"
    append_tracking(repo, flat_records, cycle_compact)
    receipt = {
        "action": "merge",
        "runCycles": run_cycles,
        **stats,
        "recordsTotal": sum(len(rows) for p in installed["points"] for rows in p["forecasts"].values()),
        "cycles": installed["cycles"],
        "isotonicAdjustedWindows": isotonic_report.get("adjusted_windows"),
        "installed": str(out_path),
        "published": False,
    }
    if not args.no_publish:
        result = subprocess.run(
            [sys.executable, str(repo / PUBLISHER), "--file", str(out_path)],
            cwd=repo, capture_output=True, text=True, timeout=900,
        )
        if result.returncode != 0:
            raise MergeError(f"installed but Blob publish failed: {result.stderr.strip()[-500:]}")
        receipt["published"] = True
        receipt["publishReceipt"] = json.loads(result.stdout)
    return receipt


def cmd_hrrr(args) -> dict:
    rms = _load_runner()
    repo = Path(args.repo)
    points, model, features = load_model_and_points(repo)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    last_error: Exception | None = None
    for back in (1, 2, 3, 4):
        candidate = now - timedelta(hours=back)
        date, hour = candidate.strftime("%Y%m%d"), candidate.hour
        max_step = 48 if hour % 6 == 0 else 18
        try:
            flat = score_hrrr(rms, points, model, features, date, hour, max_step)
        except Exception as exc:  # cycle not fully posted yet; try the previous one
            last_error = exc
            continue
        cycle_iso = iso_z(candidate)
        return run_merge(args, group_frontend_records(flat), flat, {"hrrr": cycle_iso})
    raise MergeError(f"no complete HRRR cycle in the last 4 hours (last error: {last_error})")


def cmd_ecmwf(args) -> dict:
    if args.cycle not in (6, 18):
        raise MergeError("ecmwf merge cycles are the off-cycles 6 and 18")
    rms = _load_runner()
    repo = Path(args.repo)
    points, model, features = load_model_and_points(repo)
    flat = score_ecmwf(rms, points, model, features, args.date, args.cycle)
    flat += score_hrrr(rms, points, model, features, args.date, args.cycle, max_step=48)
    cycle_dt = datetime.strptime(args.date, "%Y%m%d").replace(hour=args.cycle, tzinfo=timezone.utc)
    cycle_iso = iso_z(cycle_dt)
    return run_merge(args, group_frontend_records(flat), flat,
                     {"ecmwf": cycle_iso, "hrrr": cycle_iso})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_default = Path(__file__).resolve().parents[3]
    parser.add_argument("--repo", default=str(repo_default))
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    parser.add_argument("--frontend-out", default="src/data/model-forecasts.json")
    parser.add_argument("--no-publish", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    p_reset = sub.add_parser("reset")
    p_reset.add_argument("--frontend", required=True)
    sub.add_parser("hrrr")
    p_ecmwf = sub.add_parser("ecmwf")
    p_ecmwf.add_argument("--date", required=True)
    p_ecmwf.add_argument("--cycle", type=int, required=True)
    args = parser.parse_args()

    try:
        if args.command == "reset":
            state_dir = Path(args.repo) / args.state_dir
            frontend_path = Path(args.frontend)
            if not frontend_path.is_absolute():
                frontend_path = Path(args.repo) / frontend_path
            with state_lock(state_dir):
                receipt = reset_state(frontend_path, state_dir)
        elif args.command == "hrrr":
            receipt = cmd_hrrr(args)
        else:
            receipt = cmd_ecmwf(args)
    except LockHeld as exc:
        print(json.dumps({"skipped": str(exc)}))
        return 3
    except MergeError as exc:
        print(f"merge failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
