#!/usr/bin/env python3
"""Publish a MySnow serving snapshot to Vercel Blob (plan Revision 2, D5).

Uploads the batch frontend JSON as an immutable snapshot object, then a
small manifest naming it; the manifest write is the activation step, so
readers never observe a half-published batch:

    model-forecasts/snapshots/<cycle-compact>-<sha12>.json   (immutable)
    model-forecasts/manifest.json                            (pointer, last)

Guarantees, in order:
  * The uploaded snapshot bytes are exactly the bytes that were hashed
    (the hashed content is written to a temp file and that file is
    uploaded — never the caller's path, which a concurrent batch could
    replace mid-publish).
  * Snapshots are content-addressed and never overwritten: if the
    object already exists, the publish deduplicates onto it.
  * The snapshot is verified by re-downloading it and comparing
    SHA-256 before the manifest is written.
  * The manifest never moves backward: if the currently served
    manifest names a newer cycle, the publish refuses (override with
    --allow-regression).
  * After the manifest upload, activation is confirmed by polling the
    served manifest for up to ~90 s (an overwritten Blob object can be
    served from cache for up to ~60 s); an unconfirmed activation is
    reported as such, distinct from an upload failure.

Uploads go through the Vercel CLI (authed in the workspace). The
read-write token is taken from $BLOB_READ_WRITE_TOKEN or parsed out of
the repo's .env.local, passed to the CLI through its environment —
never on the command line, never printed; CLI output quoted in error
messages is scrubbed of it first.

Usage:
    publish_model_serving_blob.py --file src/data/model-forecasts.json
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BASE_URL = "https://lit7okcfu7rnngdt.public.blob.vercel-storage.com"
MANIFEST_PATHNAME = "model-forecasts/manifest.json"
SNAPSHOT_CACHE_MAX_AGE = 31_536_000  # 1 year; snapshots are immutable
MANIFEST_CACHE_MAX_AGE = 60
ACTIVATION_POLL_SECONDS = 90
ACTIVATION_POLL_INTERVAL = 10


def fail(message: str) -> "SystemExit":
    print(f"publish failed: {message}", file=sys.stderr)
    return SystemExit(1)


def load_token(env_file: Path) -> str:
    token = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    if token:
        return token
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("BLOB_READ_WRITE_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"')
    raise fail("BLOB_READ_WRITE_TOKEN not in the environment or .env.local")


def vercel_bin() -> str:
    found = shutil.which("vercel")
    if found:
        return found
    mise_shim = Path.home() / ".local/share/mise/shims/vercel"
    if mise_shim.is_file():
        return str(mise_shim)
    raise fail("vercel CLI not found on PATH or in the mise shims")


def blob_put(
    vercel: str,
    token: str,
    local_path: Path,
    pathname: str,
    cache_max_age: int,
    allow_overwrite: bool,
) -> str:
    """Upload one object. Returns 'uploaded' or 'exists' (dedupe case)."""
    # cwd=/tmp: running inside the repo makes the CLI load .env.local,
    # whose VERCEL_OIDC_TOKEN conflicts with the read-write-token path.
    env = {**os.environ, "BLOB_READ_WRITE_TOKEN": token}
    command = [
        vercel, "blob", "put", str(local_path),
        "--pathname", pathname,
        "--access", "public",
        "--allow-overwrite", "true" if allow_overwrite else "false",
        "--cache-control-max-age", str(cache_max_age),
    ]
    try:
        result = subprocess.run(command, cwd="/tmp", env=env, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise fail(f"vercel blob put {pathname} timed out (command and output withheld)")
    combined = result.stdout + result.stderr
    if not allow_overwrite and "already exists" in combined:
        return "exists"
    if result.returncode != 0 or "Success" not in combined:
        scrubbed = combined.replace(token, "***").strip()
        raise fail(f"vercel blob put {pathname} exited {result.returncode}: {scrubbed}")
    return "uploaded"


def http_get(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def describe_frontend(data: dict) -> tuple[str, int, str, str]:
    cycles = data.get("cycles") or {}
    # A merged frontend can carry a different cycle per arm (tiered
    # cadence); the manifest cycleUtc is the newest contributing one.
    cycle_utc = max(cycles.values()) if cycles else None
    if not cycle_utc:
        raise fail("frontend JSON carries no cycle timestamp")
    record_count = 0
    window_ends: list[str] = []
    for point in data.get("points") or []:
        for records in (point.get("forecasts") or {}).values():
            record_count += len(records)
            window_ends.extend(r["windowEndUtc"] for r in records)
    if record_count == 0 or not window_ends:
        raise fail("frontend JSON carries no forecast records")
    return cycle_utc, record_count, min(window_ends), max(window_ends)


def current_manifest(base_url: str) -> dict | None:
    try:
        return json.loads(http_get(f"{base_url}/{MANIFEST_PATHNAME}", timeout=15))
    except (urllib.error.URLError, ValueError):
        return None


def regressed_arms(existing_cycles: dict, candidate_cycles: dict) -> list[str]:
    """Arms the live manifest carries that the candidate would move
    backward (or drop). A merged frontend's arms advance independently,
    so freshness is judged per arm: a stale ECMWF arm must not ride in
    behind a fresh HRRR cycle, which a scalar-maximum check allows."""
    return sorted(
        arm for arm, cycle in (existing_cycles or {}).items()
        if cycle and (candidate_cycles.get(arm) is None or candidate_cycles[arm] < cycle))


def publication_refusal(existing: dict, data: dict, sha256: str, cycle_utc: str) -> str | None:
    """Why publishing this candidate over the live manifest would move
    serving backwards, or None when it may proceed. Freshness is
    judged per arm when both sides carry cycles (a merged frontend's
    arms advance independently); otherwise the scalar cycleUtc guard
    applies. A tiered-cadence candidate also carries a monotonically
    increasing servingGeneration: an older generation never
    overwrites a newer one, and different bytes at the same
    generation are a stale publisher, not a retry (a true retry has
    identical bytes and hash)."""
    if existing.get("cycles") and data.get("cycles"):
        regressions = regressed_arms(existing.get("cycles") or {}, data.get("cycles") or {})
        if regressions:
            return (
                f"refusing to move the manifest backward: the served manifest's "
                f"{', '.join(regressions)} cycle(s) {existing.get('cycles')} are newer "
                f"than this candidate's {data.get('cycles')} (pass --allow-regression to override)"
            )
    else:
        existing_cycle = existing.get("cycleUtc") or ""
        if existing_cycle > cycle_utc:
            return (
                f"refusing to move the manifest backward: served cycle {existing_cycle} "
                f"is newer than the candidate {cycle_utc} (pass --allow-regression to override)"
            )
    existing_generation = existing.get("generation")
    candidate_generation = data.get("servingGeneration")
    if isinstance(existing_generation, int) and isinstance(candidate_generation, int):
        if candidate_generation < existing_generation:
            return (
                f"refusing to publish generation {candidate_generation}: the served "
                f"manifest is already at generation {existing_generation} "
                f"(pass --allow-regression to override)"
            )
        if candidate_generation == existing_generation and existing.get("sha256") != sha256:
            return (
                f"refusing to publish different bytes at generation "
                f"{candidate_generation}: the served manifest's hash differs "
                f"(pass --allow-regression to override)"
            )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_root = Path(__file__).resolve().parents[2]
    parser.add_argument("--file", type=Path, required=True, help="serving frontend JSON to publish")
    parser.add_argument("--base-url", default=os.environ.get("MODEL_FORECASTS_BLOB_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--env-file", type=Path, default=repo_root / ".env.local")
    parser.add_argument("--allow-regression", action="store_true",
                        help="publish even if the served manifest names a newer cycle")
    parser.add_argument("--lock-file", type=Path, default=None,
                        help="serialise publishers with this lock file "
                             "(default: the merge-state publish.lock)")
    args = parser.parse_args()

    # Serialise publishers: the manifest read-check-write sequence is
    # only safe when publishers cannot interleave. The lock is held
    # for the whole run (upload + manifest write + activation poll).
    lock_path = args.lock_file or (
        Path(__file__).resolve().parents[2]
        / "data/tracking/model-serving/merge-state/publish.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(lock_path, "a+")
    fcntl.flock(lock_fd, fcntl.LOCK_EX)

    args.file = args.file.resolve()
    base_url = args.base_url.rstrip("/")
    raw = args.file.read_bytes()
    data = json.loads(raw)
    cycle_utc, record_count, coverage_start, coverage_end = describe_frontend(data)
    sha256 = hashlib.sha256(raw).hexdigest()
    cycle_compact = cycle_utc.replace("-", "").replace(":", "")
    snapshot_pathname = f"model-forecasts/snapshots/{cycle_compact}-{sha256[:12]}.json"
    snapshot_url = f"{base_url}/{snapshot_pathname}"

    existing = current_manifest(base_url)
    if existing and not args.allow_regression:
        refusal = publication_refusal(existing, data, sha256, cycle_utc)
        if refusal:
            raise fail(refusal)

    token = load_token(args.env_file)
    vercel = vercel_bin()

    # Upload exactly the hashed bytes, from a temp file we own.
    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as handle:
        handle.write(raw)
        snapshot_tmp = Path(handle.name)
    try:
        blob_put(vercel, token, snapshot_tmp, snapshot_pathname, SNAPSHOT_CACHE_MAX_AGE, allow_overwrite=False)
    finally:
        snapshot_tmp.unlink(missing_ok=True)

    downloaded = http_get(snapshot_url)
    if hashlib.sha256(downloaded).hexdigest() != sha256:
        raise fail("snapshot verification failed: re-downloaded bytes hash differently; manifest NOT written")

    manifest = {
        "version": 1,
        "cycleUtc": cycle_utc,
        "cycles": data.get("cycles") or {},
        **({"generation": data["servingGeneration"]}
           if isinstance(data.get("servingGeneration"), int) else {}),
        "sha256": sha256,
        "snapshotPathname": snapshot_pathname,
        "snapshotUrl": snapshot_url,
        "recordCount": record_count,
        "coverageStartUtc": coverage_start,
        "coverageEndUtc": coverage_end,
        "generatedAtUtc": data.get("generatedAtUtc"),
        "publishedAtUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(manifest, handle, indent=2)
        manifest_path = Path(handle.name)
    try:
        blob_put(vercel, token, manifest_path, MANIFEST_PATHNAME, MANIFEST_CACHE_MAX_AGE, allow_overwrite=True)
    finally:
        manifest_path.unlink(missing_ok=True)

    activation = "unconfirmed"
    deadline = time.monotonic() + ACTIVATION_POLL_SECONDS
    while True:
        served_manifest = current_manifest(base_url)
        if served_manifest and served_manifest.get("sha256") == sha256 and served_manifest.get("cycleUtc") == cycle_utc:
            activation = "confirmed"
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(ACTIVATION_POLL_INTERVAL)
    if activation != "confirmed":
        print(
            "warning: manifest uploaded but the served copy still shows the previous "
            "snapshot after the propagation window; Blob serves overwritten objects "
            "from cache for up to ~60 s, so activation should follow shortly.",
            file=sys.stderr,
        )

    print(json.dumps({
        "published": True,
        "activation": activation,
        "cycleUtc": cycle_utc,
        "sha256": sha256,
        "snapshotUrl": snapshot_url,
        "recordCount": record_count,
        "coverageStartUtc": coverage_start,
        "coverageEndUtc": coverage_end,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
