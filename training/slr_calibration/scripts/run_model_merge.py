#!/usr/bin/env python3
"""Tiered-cadence merge runs for MySnow model serving (2026-11-16).

The twice-daily full batches (run_model_serving.py at 00Z/12Z) remain
the backbone: the only source of ECMWF leads 7-10. Between them, merge
runs refresh the windows newer cycles can already see, layered over a
persistent RAW working state:

  hrrr    hourly: score the HRRR arm from the latest posted cycle
          (48 h runs at 00/06/12/18Z, 18 h runs otherwise) and replace
          the HRRR records for the windows it covers.
  ecmwf   at 06Z/18Z: score ECMWF to its step-144 ceiling (leads ~1-6)
          plus HRRR from the same cycle when available, and replace
          those records.
  commit-full  the full batches' install step: under the same lock,
          adopt a validated full-batch candidate as the new state —
          its coverage and windows, but retaining any standing record
          whose scoring cycle is strictly newer (an hourly refresh
          that landed while the batch was scoring is never regressed)
          — then install and publish from that state.

A merge never extends coverage (windows absent from the state are
skipped), never moves a window backward (a record is replaced only by
a strictly newer cycle's record for the same window identity), and
never stacks the isotonic layer (the state is kept RAW; the layer is
applied fresh to the state on every install). Every record carries
its scoring cycle in ``cycleUtc``; the state's ``seedCycles`` records
the full batch that set the current coverage, and the installed
frontend's ``cycles`` map reports the newest contributing cycle per
arm.

Publication discipline (Codex gate, 2026-11-16): installs are written
atomically (temp file + rename) from the state under an flock lock
shared by merges, commit-full and reset. The exact bytes to publish
are first written as an immutable artifact under the state directory
and recorded in pending-publish.json; the publisher subprocess reads
that artifact (never the mutable install path) and serialises
manifest updates with its own lock. A failed publish therefore never
loses the update: any later run (or `publish-pending`) republishes the
pending artifact even when it has no new records of its own.

State: data/tracking/model-serving/merge-state/state.json. A run that
finds the lock held exits 3 and changes nothing. `reset` re-seeds the
state from a full-batch frontend (bootstrap/recovery; adopt-only, no
install or publish). `reinstall` regenerates the installed frontend
from the state after a crash.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
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


class CycleUnavailable(Exception):
    """The requested cycle's data is not (fully) posted yet."""


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


def cycle_ceiling_hours(arm: str, cycle_iso: str) -> int:
    """The dissemination ceiling of the cycle that scored a record:
    ECMWF runs to step 240 at 00Z/12Z and step 144 at 06Z/18Z; HRRR
    runs 48 h on the synoptic cycles and 18 h otherwise."""
    hour = parse_iso(cycle_iso).hour
    if arm == "ecmwf":
        return 240 if hour in (0, 12) else 144
    return 48 if hour % 6 == 0 else 18


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_write_json(path: Path, doc: dict) -> None:
    atomic_write_bytes(path, json.dumps(doc, indent=1).encode())


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
                key = (point["pointId"], arm, record["windowEndUtc"])
                if key in index:
                    raise MergeError(f"duplicate record key in frontend: {key}")
                index[key] = record
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
    index = record_index(state)
    replaced_keys: set[tuple[str, str, str]] = set()
    stats = {"replaced": 0, "skippedAbsent": 0, "skippedStale": 0}

    for (point_id, arm), rows in new_records.items():
        seen: set[str] = set()
        for record in rows:
            if record["windowEndUtc"] in seen:
                raise MergeError(f"duplicate scored record for {(point_id, arm, record['windowEndUtc'])}")
            seen.add(record["windowEndUtc"])
            key = (point_id, arm, record["windowEndUtc"])
            current = index.get(key)
            if current is None:
                stats["skippedAbsent"] += 1
                continue
            if record["cycleUtc"] <= current["cycleUtc"]:
                stats["skippedStale"] += 1
                continue
            # Window identity is the full interval, not just its end.
            if (record["windowStartUtc"] != current["windowStartUtc"]
                    or record["windowEndLocalDate"] != current["windowEndLocalDate"]):
                raise MergeError(f"window identity mismatch for {key}")
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
        # G4: no record reaches beyond its own cycle's dissemination
        # ceiling (240/144 h ECMWF by cycle hour, 48/18 h HRRR).
        cycle = parse_iso(record["cycleUtc"])
        end = parse_iso(record["windowEndUtc"])
        if (end - cycle).total_seconds() > cycle_ceiling_hours(key[1], record["cycleUtc"]) * 3600:
            raise MergeError(f"gate G4: record beyond its cycle ceiling: {key}")
    # G3: replaced records all carry one consistent cycle per arm.
    cycles_by_arm: dict[str, set[str]] = {}
    for point_id, arm, _end in replaced_keys:
        cycles_by_arm.setdefault(arm, set()).add(after_index[(point_id, arm, _end)]["cycleUtc"])
    for arm, cycles in cycles_by_arm.items():
        if len(cycles) != 1:
            raise MergeError(f"gate G3: replaced {arm} records carry cycles {sorted(cycles)}")


def adopt_full_frontend(old_state: dict | None, candidate: dict) -> tuple[dict, dict]:
    """The state after a full batch: the candidate's coverage and
    windows, retaining any standing record scored by a strictly newer
    cycle (a refresh that landed while the batch was scoring is never
    regressed by the batch's older cycle)."""
    new_state = stamp_record_cycles(deraw_frontend(candidate))
    new_state["seedCycles"] = dict(candidate["cycles"])
    stats = {"retainedNewer": 0, "adopted": 0}
    if old_state is None:
        stats["adopted"] = sum(len(rows) for p in new_state["points"] for rows in p["forecasts"].values())
        return new_state, stats
    old_index = record_index(old_state)
    index = record_index(new_state)
    for key, record in index.items():
        current = old_index.get(key)
        if current is not None and current["cycleUtc"] > record["cycleUtc"]:
            record.clear()
            record.update(copy.deepcopy(current))
            stats["retainedNewer"] += 1
        else:
            stats["adopted"] += 1
    return new_state, stats


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
    """The installable frontend for a raw state: fresh timestamp,
    per-arm cycles and seed cycles, lead-cap assertion, isotonic layer
    applied fresh."""
    out = recompute_cycles(copy.deepcopy(state))
    out["seedCycles"] = dict(state.get("seedCycles") or state["cycles"])
    out["generatedAtUtc"] = datetime.now(timezone.utc).isoformat()
    assert_frontend_within_lead_cap(out)
    adjusted, report = apply_isotonic_slr_layer_gated(out, enabled=True)
    return adjusted, report


# ---------------------------------------------------------------------------
# State I/O, artifacts, pending publication
# ---------------------------------------------------------------------------

def load_state(state_dir: Path) -> dict:
    path = state_dir / "state.json"
    if not path.is_file():
        raise MergeError(f"no merge state at {path}; run `reset` from an installed frontend first")
    state = json.loads(path.read_text())
    state.setdefault("seedCycles", dict(state.get("cycles") or {}))
    return state


def save_state(state_dir: Path, state: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(state_dir / "state.json", state)


def write_artifact(state_dir: Path, installed: dict) -> tuple[Path, str]:
    payload = json.dumps(installed, indent=1).encode()
    sha = hashlib.sha256(payload).hexdigest()
    path = state_dir / "artifacts" / f"{sha}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise MergeError(f"artifact name collision with different bytes: {path}")
    else:
        atomic_write_bytes(path, payload)
    return path, sha


def set_pending(state_dir: Path, artifact: Path, sha: str) -> None:
    atomic_write_json(state_dir / "pending-publish.json",
                      {"artifact": artifact.name, "sha256": sha,
                       "queuedAtUtc": datetime.now(timezone.utc).isoformat()})


def read_pending(state_dir: Path) -> dict | None:
    path = state_dir / "pending-publish.json"
    return json.loads(path.read_text()) if path.is_file() else None


def publish_artifact(repo: Path, state_dir: Path, artifact: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(repo / PUBLISHER), "--file", str(artifact)],
        cwd=repo, capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        raise MergeError(f"Blob publish failed for {artifact.name}: {result.stderr.strip()[-500:]}")
    return json.loads(result.stdout)


def publish_pending(repo: Path, state_dir: Path) -> dict | None:
    """Publish the queued artifact, if any. Returns the publisher
    receipt, or None when nothing is pending. On success the pending
    record is cleared; on failure it stays queued for the next run."""
    pending = read_pending(state_dir)
    if pending is None:
        return None
    artifact = state_dir / "artifacts" / pending["artifact"]
    if not artifact.is_file():
        raise MergeError(f"pending artifact is missing: {artifact}")
    receipt = publish_artifact(repo, state_dir, artifact)
    (state_dir / "pending-publish.json").unlink()
    return receipt


def append_receipt(state_dir: Path, receipt: dict) -> None:
    path = state_dir / "receipts.jsonl"
    with open(path, "a") as fh:
        fh.write(json.dumps(receipt, default=str) + "\n")


def install_and_queue(state_dir: Path, state: dict, frontend_out: Path) -> tuple[dict, dict]:
    """Finalise the state, install it atomically, persist the state,
    and queue the exact installed bytes for publication. Caller holds
    the state lock. Returns (installed, isotonic_report)."""
    installed, report = finalise_frontend(state)
    atomic_write_json(frontend_out, installed)
    save_state(state_dir, state)
    artifact, sha = write_artifact(state_dir, installed)
    set_pending(state_dir, artifact, sha)
    return installed, report


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


def build_hrrr(rms, points, date: str, hour: int, max_step: int):
    try:
        series, _t2m = rms.build_hrrr_series(points, date, hour, max_step=max_step)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise CycleUnavailable(f"HRRR {date} {hour:02d}Z not posted (HTTP 404)") from exc
        raise
    return series


def score_hrrr_series(rms, points, model, features, series, date: str, hour: int):
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
# Commands
# ---------------------------------------------------------------------------

def finish_run(args, receipt: dict, flat_records: list[dict], cycle_compact: str) -> dict:
    """Shared tail of a merge/commit-full run: tracking (best effort),
    pending publication (this run's artifact and any older queued one),
    durable receipt."""
    repo = Path(args.repo)
    state_dir = repo / args.state_dir
    if flat_records:
        try:
            append_tracking(repo, flat_records, cycle_compact)
        except Exception as exc:  # tracking must never block publication
            receipt["trackingWarning"] = str(exc)
    published = publish_pending(repo, state_dir)
    receipt["published"] = published is not None
    if published is not None:
        receipt["publishReceipt"] = published
    append_receipt(state_dir, receipt)
    return receipt


def run_merge(args, new_records: dict[tuple[str, str], list[dict]],
              flat_records: list[dict], run_cycles: dict[str, str],
              warnings: list[str]) -> dict:
    repo = Path(args.repo)
    state_dir = repo / args.state_dir
    with state_lock(state_dir):
        state = load_state(state_dir)
        merged, stats = merge_frontend(state, new_records)
        receipt: dict[str, Any] = {"action": "merge", "runCycles": run_cycles, **stats,
                                   "warnings": warnings}
        if stats["replaced"] == 0:
            receipt["outcome"] = "already-current"
        else:
            installed, isotonic_report = install_and_queue(
                state_dir, merged, repo / args.frontend_out)
            receipt["outcome"] = "merged"
            receipt["recordsTotal"] = sum(
                len(rows) for p in installed["points"] for rows in p["forecasts"].values())
            receipt["cycles"] = installed["cycles"]
            receipt["isotonicAdjustedWindows"] = isotonic_report.get("adjusted_windows")
    newest = max(run_cycles.values())
    cycle_compact = newest[:13].replace("-", "").replace(":", "") + "Z"
    return finish_run(args, receipt, flat_records, cycle_compact)


def cmd_hrrr(args) -> dict:
    rms = _load_runner()
    repo = Path(args.repo)
    points, model, features = load_model_and_points(repo)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    rejections: list[str] = []
    for back in (1, 2, 3, 4):
        candidate = now - timedelta(hours=back)
        date, hour = candidate.strftime("%Y%m%d"), candidate.hour
        max_step = 48 if hour % 6 == 0 else 18
        try:
            series = build_hrrr(rms, points, date, hour, max_step)
        except CycleUnavailable as exc:
            rejections.append(str(exc))
            continue
        flat = score_hrrr_series(rms, points, model, features, series, date, hour)
        if not any(not r["missing"] for r in flat):
            rejections.append(f"HRRR {date} {hour:02d}Z scored no usable records")
            continue
        return run_merge(args, group_frontend_records(flat), flat,
                         {"hrrr": iso_z(candidate)}, rejections)
    raise MergeError(f"no usable HRRR cycle in the last 4 hours: {rejections}")


def cmd_ecmwf(args) -> dict:
    if args.cycle not in (6, 18):
        raise MergeError("ecmwf merge cycles are the off-cycles 6 and 18")
    rms = _load_runner()
    repo = Path(args.repo)
    points, model, features = load_model_and_points(repo)
    flat = score_ecmwf(rms, points, model, features, args.date, args.cycle)
    warnings: list[str] = []
    try:
        series = build_hrrr(rms, points, args.date, args.cycle, max_step=48)
        hrrr_flat = score_hrrr_series(rms, points, model, features, series, args.date, args.cycle)
        if any(not r["missing"] for r in hrrr_flat):
            flat += hrrr_flat
        else:
            warnings.append(f"HRRR {args.date} {args.cycle:02d}Z scored no usable records; ECMWF merged alone")
    except CycleUnavailable as exc:
        warnings.append(f"{exc}; ECMWF merged alone")
    cycle_dt = datetime.strptime(args.date, "%Y%m%d").replace(hour=args.cycle, tzinfo=timezone.utc)
    return run_merge(args, group_frontend_records(flat), flat,
                     {"ecmwf": iso_z(cycle_dt)}, warnings)


def cmd_commit_full(args) -> dict:
    repo = Path(args.repo)
    state_dir = repo / args.state_dir
    candidate_path = Path(args.candidate)
    if not candidate_path.is_absolute():
        candidate_path = repo / candidate_path
    candidate = json.loads(candidate_path.read_text())
    with state_lock(state_dir):
        old_state = load_state(state_dir) if (state_dir / "state.json").is_file() else None
        new_state, stats = adopt_full_frontend(old_state, candidate)
        installed, isotonic_report = install_and_queue(
            state_dir, new_state, repo / args.frontend_out)
    receipt = {"action": "commit-full", "candidateCycles": candidate["cycles"], **stats,
               "recordsTotal": sum(len(rows) for p in installed["points"] for rows in p["forecasts"].values()),
               "cycles": installed["cycles"],
               "isotonicAdjustedWindows": isotonic_report.get("adjusted_windows"),
               "warnings": []}
    return finish_run(args, receipt, [], "commit-full")


def cmd_reset(args) -> dict:
    repo = Path(args.repo)
    state_dir = repo / args.state_dir
    frontend_path = Path(args.frontend)
    if not frontend_path.is_absolute():
        frontend_path = repo / frontend_path
    frontend = json.loads(frontend_path.read_text())
    with state_lock(state_dir):
        old_state = load_state(state_dir) if (state_dir / "state.json").is_file() else None
        state, stats = adopt_full_frontend(old_state, frontend)
        save_state(state_dir, state)
    return {"action": "reset", "cycles": recompute_cycles(state)["cycles"], **stats,
            "records": sum(len(rows) for p in state["points"] for rows in p["forecasts"].values())}


def cmd_reinstall(args) -> dict:
    repo = Path(args.repo)
    state_dir = repo / args.state_dir
    with state_lock(state_dir):
        state = load_state(state_dir)
        installed, _report = finalise_frontend(state)
        atomic_write_json(repo / args.frontend_out, installed)
    return {"action": "reinstall", "cycles": installed["cycles"]}


def cmd_publish_pending(args) -> dict:
    repo = Path(args.repo)
    state_dir = repo / args.state_dir
    published = publish_pending(repo, state_dir)
    return {"action": "publish-pending", "published": published is not None,
            **({"publishReceipt": published} if published else {})}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_default = Path(__file__).resolve().parents[3]
    parser.add_argument("--repo", default=str(repo_default))
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    parser.add_argument("--frontend-out", default="src/data/model-forecasts.json")
    sub = parser.add_subparsers(dest="command", required=True)
    p_reset = sub.add_parser("reset")
    p_reset.add_argument("--frontend", required=True)
    sub.add_parser("hrrr")
    p_ecmwf = sub.add_parser("ecmwf")
    p_ecmwf.add_argument("--date", required=True)
    p_ecmwf.add_argument("--cycle", type=int, required=True)
    p_full = sub.add_parser("commit-full")
    p_full.add_argument("--candidate", required=True)
    sub.add_parser("reinstall")
    sub.add_parser("publish-pending")
    args = parser.parse_args()

    try:
        if args.command == "reset":
            receipt = cmd_reset(args)
        elif args.command == "hrrr":
            receipt = cmd_hrrr(args)
        elif args.command == "ecmwf":
            receipt = cmd_ecmwf(args)
        elif args.command == "commit-full":
            receipt = cmd_commit_full(args)
        elif args.command == "reinstall":
            receipt = cmd_reinstall(args)
        else:
            receipt = cmd_publish_pending(args)
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
