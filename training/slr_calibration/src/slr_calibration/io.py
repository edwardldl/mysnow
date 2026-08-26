from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable

import pyarrow.parquet as pq


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Read normalized storm-event records from CSV or Parquet."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    if suffix in {".parquet", ".pq"}:
        return pq.read_table(path).to_pylist()
    raise ValueError(f"Unsupported input format: {suffix}; expected .csv or .parquet")


def canonical_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((dict(row) for row in rows), key=lambda row: (
        str(row.get("baseline_method", "")),
        str(row.get("station_id", "")),
        str(row.get("valid_time", "")),
        str(row.get("event_id", "")),
    ))
