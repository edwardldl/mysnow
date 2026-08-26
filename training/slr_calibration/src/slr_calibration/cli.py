from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean, median

from .io import read_rows
from .trainer import TrainingConfig, train_artifact


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")


def _train(args: argparse.Namespace) -> int:
    rows = read_rows(args.input)
    config = TrainingConfig(
        artifact_id=args.artifact_id,
        artifact_version=args.artifact_version,
        status=args.status,
        region_resolution=args.region_resolution,
        climate_resolution=args.climate_resolution,
        prior_effective_sample_size=args.prior_effective_sample_size,
        min_effective_sample_size=args.min_effective_sample_size,
        source_label=args.source_label,
    )
    artifact = train_artifact(rows, config)
    _write_json(args.output, artifact)
    print(json.dumps({"output": str(args.output), "accepted": artifact["training"]["acceptedEventCount"], "status": artifact["status"]}, sort_keys=True))
    return 0


def _inspect_bootstrap(args: argparse.Namespace) -> int:
    with args.input.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    slrs = [float(row["slr"]) for row in rows]
    stations = {row["station_id"] for row in rows}
    states: dict[str, int] = {}
    for row in rows:
        state = row.get("state", "")
        states[state] = states.get(state, 0) + 1
    report = {
        "input": str(args.input),
        "recordCount": len(rows),
        "stationCount": len(stations),
        "dateRange": [min(row["date"] for row in rows), max(row["date"] for row in rows)],
        "slr": {"min": min(slrs), "median": median(slrs), "mean": mean(slrs), "max": max(slrs)},
        "stateCounts": dict(sorted(states.items())),
        "canTrainResidualCalibration": False,
        "blockingFields": ["physical baseline SLR aligned to each observation", "event-time atmospheric/profile predictors"],
        "note": "These observations validate ingestion and QC coverage only; they must not produce a deployable residual artifact until baseline predictions are paired.",
    }
    _write_json(args.output, report)
    print(json.dumps({"output": str(args.output), "records": len(rows), "canTrainResidualCalibration": False}, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MySnow offline regional SLR calibration")
    subparsers = parser.add_subparsers(dest="command", required=True)

    train = subparsers.add_parser("train", help="fit and deterministically export an artifact")
    train.add_argument("--input", type=Path, required=True)
    train.add_argument("--output", type=Path, required=True)
    train.add_argument("--artifact-id", required=True)
    train.add_argument("--artifact-version", default="unvalidated-v1")
    train.add_argument("--status", choices=("unvalidated", "validated", "production"), default="unvalidated")
    train.add_argument("--source-label", default="normalized storm-event observations")
    train.add_argument("--region-resolution", type=int, default=4)
    train.add_argument("--climate-resolution", type=int, default=2)
    train.add_argument("--prior-effective-sample-size", type=float, default=30.0)
    train.add_argument("--min-effective-sample-size", type=float, default=5.0)
    train.set_defaults(func=_train)

    inspect_bootstrap = subparsers.add_parser("inspect-bootstrap", help="summarize GHCN bootstrap QC observations without training")
    inspect_bootstrap.add_argument("--input", type=Path, required=True)
    inspect_bootstrap.add_argument("--output", type=Path, required=True)
    inspect_bootstrap.set_defaults(func=_inspect_bootstrap)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
