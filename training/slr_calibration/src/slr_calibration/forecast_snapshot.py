from __future__ import annotations

import gzip
import hashlib
import json
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from .profiles import HOURLY_FIELDS, _validate_response
from .tracking_common import Fetcher, fetch_bytes, iso_z, utc_now
from .tracking_registry import load_registry

FORECAST_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
# Public Open-Meteo forecast-API model ids for the app's primary pair
# (ECMWF IFS and GFS seamless); see MODEL_CAPABILITIES in src/lib/api.ts.
SNAPSHOT_MODELS = ("ecmwf_ifs", "gfs_seamless")
DEFAULT_FORECAST_DAYS = 7


def forecast_url(latitude: float, longitude: float, elevation_m: float, model: str, forecast_days: int = DEFAULT_FORECAST_DAYS) -> str:
    query = urllib.parse.urlencode({
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "elevation": f"{elevation_m:.0f}",
        "hourly": ",".join(HOURLY_FIELDS),
        "models": model,
        "forecast_days": forecast_days,
        "timezone": "UTC",
    })
    return f"{FORECAST_ENDPOINT}?{query}"


def snapshot_relpath(day: str, point_id: str, model: str) -> Path:
    year, month, day_of_month = day.split("-")
    return Path(year) / month / day_of_month / f"{point_id}__{model}.json.gz"


def read_index(index_path: Path) -> list[dict[str, Any]]:
    if not index_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _index_key(row: dict[str, Any]) -> tuple[str, str, str]:
    issued_day = str(row.get("issued_at_utc") or "")[:10]
    return (issued_day, str(row.get("point_id") or ""), str(row.get("model") or ""))


def run_snapshots(
    registry_path: Path,
    snapshots_dir: Path,
    models: tuple[str, ...] = SNAPSHOT_MODELS,
    fetcher: Fetcher | None = None,
    now: datetime | None = None,
    force: bool = False,
    forecast_days: int = DEFAULT_FORECAST_DAYS,
) -> dict[str, Any]:
    """Fetch and store one forecast snapshot per registry point and model.

    Idempotent per UTC day: an existing index row for (day, point, model)
    with its payload file present is skipped unless ``force`` is set.
    """
    fetch = fetcher or fetch_bytes
    moment = now or utc_now()
    day = moment.date().isoformat()
    issued_at = iso_z(moment)
    registry = load_registry(registry_path)

    snapshots_dir.mkdir(parents=True, exist_ok=True)
    index_path = snapshots_dir / "index.jsonl"
    existing = read_index(index_path)
    existing_keys = {_index_key(row) for row in existing}

    stored: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: dict[str, str] = {}
    for resort in registry.get("resorts", []):
        for point in resort.get("forecast_points", []):
            point_id = str(point["point_id"])
            for model in models:
                key = (day, point_id, model)
                relpath = snapshot_relpath(day, point_id, model)
                target = snapshots_dir / relpath
                if key in existing_keys and target.exists() and not force:
                    skipped.append({"point_id": point_id, "model": model, "reason": "already_snapshotted_today"})
                    continue
                url = forecast_url(float(point["latitude"]), float(point["longitude"]), float(point["elevation_m"]), model, forecast_days)
                try:
                    raw = fetch(url)
                    payload = json.loads(raw.decode("utf-8"))
                    _validate_response(payload)
                except Exception as exc:  # keep other points moving; never fabricate
                    failed[f"{point_id}:{model}"] = f"{type(exc).__name__}: {exc}"
                    continue
                compressed = gzip.compress(raw, mtime=0)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(compressed)
                row = {
                    "issued_at_utc": issued_at,
                    "point_id": point_id,
                    "resort_slug": resort.get("resort_slug"),
                    "model": model,
                    "sha256": hashlib.sha256(compressed).hexdigest(),
                    "bytes": len(compressed),
                    "forecast_days": forecast_days,
                    "path": relpath.as_posix(),
                }
                if force and key in existing_keys:
                    existing = [item for item in existing if _index_key(item) != key]
                existing.append(row)
                existing_keys.add(key)
                stored.append(row)

    with index_path.open("w", encoding="utf-8") as handle:
        for row in sorted(existing, key=lambda item: (str(item.get("issued_at_utc")), str(item.get("point_id")), str(item.get("model")))):
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    return {
        "date": day,
        "issuedAtUtc": issued_at,
        "storedCount": len(stored),
        "skippedCount": len(skipped),
        "failedCount": len(failed),
        "failed": failed,
        "stored": stored,
        "indexPath": str(index_path),
    }
