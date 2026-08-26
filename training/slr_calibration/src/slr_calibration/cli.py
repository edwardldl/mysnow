from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path
from statistics import mean, median

from .cssl import CsslImportConfig, write_cssl_outputs
from .evaluation import evaluate_temporal_holdout
from .io import read_rows
from .profiles import backfill_openmeteo_profiles
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
        leakage_group=args.leakage_group,
        geographic_note=args.geographic_note,
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


def _import_cssl(args: argparse.Namespace) -> int:
    report = write_cssl_outputs(
        args.input_dir,
        args.output_events,
        args.output_rejections,
        args.output_report,
        CsslImportConfig(
            min_new_snow_cm=args.min_new_snow_cm,
            min_fresh_swe_mm=args.min_fresh_swe_mm,
            min_observed_slr=args.min_observed_slr,
            max_observed_slr=args.max_observed_slr,
            required_snow_fraction_pct=args.required_snow_fraction_pct,
        ),
    )
    print(json.dumps({
        "events": str(args.output_events),
        "report": str(args.output_report),
        "accepted": report["qc"]["acceptedEventCount"],
        "rejected": report["qc"]["rejectedRowCount"],
    }, sort_keys=True))
    return 0


def _backfill_openmeteo(args: argparse.Namespace) -> int:
    report = backfill_openmeteo_profiles(
        args.input_events,
        args.cache_dir,
        args.output,
        args.output_report,
        date.fromisoformat(args.start_date),
        date.fromisoformat(args.end_date),
        args.sleep_seconds,
    )
    print(json.dumps({
        "output": str(args.output),
        "report": str(args.output_report),
        "inputEvents": report["inputEventCount"],
        "alignedEvents": report["alignedEventCount"],
        "failedMonths": len(report["failedMonths"]),
    }, sort_keys=True))
    return 0


def _evaluate_sierra(args: argparse.Namespace) -> int:
    _, report = evaluate_temporal_holdout(
        args.input,
        args.artifact_output,
        args.report_output,
        args.artifact_id,
        args.artifact_version,
        args.test_water_year,
        args.prior_effective_sample_size,
        args.min_effective_sample_size,
    )
    print(json.dumps({
        "artifact": str(args.artifact_output),
        "report": str(args.report_output),
        "trainEvents": report["split"]["trainEventCount"],
        "testEvents": report["split"]["testEventCount"],
        "heldOutImprovement": report["improvement"]["heldOutImprovement"],
        "baselineMae": report["baseline"]["mae"],
        "calibratedMae": report["calibrated"]["mae"],
    }, sort_keys=True))
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
    train.add_argument("--leakage-group", default="event_id")
    train.add_argument("--geographic-note", default="Coverage is explicit by hierarchy; absent cells fall back to a broader level.")
    train.set_defaults(func=_train)

    inspect_bootstrap = subparsers.add_parser("inspect-bootstrap", help="summarize GHCN bootstrap QC observations without training")
    inspect_bootstrap.add_argument("--input", type=Path, required=True)
    inspect_bootstrap.add_argument("--output", type=Path, required=True)
    inspect_bootstrap.set_defaults(func=_inspect_bootstrap)

    import_cssl = subparsers.add_parser("import-cssl", help="normalize and QC the CC0 CSSL release into direct fresh-SLR events")
    import_cssl.add_argument("--input-dir", type=Path, required=True)
    import_cssl.add_argument("--output-events", type=Path, required=True)
    import_cssl.add_argument("--output-rejections", type=Path, required=True)
    import_cssl.add_argument("--output-report", type=Path, required=True)
    import_cssl.add_argument("--min-new-snow-cm", type=float, default=2.5)
    import_cssl.add_argument("--min-fresh-swe-mm", type=float, default=2.5)
    import_cssl.add_argument("--min-observed-slr", type=float, default=2.0)
    import_cssl.add_argument("--max-observed-slr", type=float, default=50.0)
    import_cssl.add_argument("--required-snow-fraction-pct", type=float, default=100.0)
    import_cssl.set_defaults(func=_import_cssl)

    profiles = subparsers.add_parser("backfill-openmeteo", help="cache recent CSSL pressure profiles for accepted events")
    profiles.add_argument("--input-events", type=Path, required=True)
    profiles.add_argument("--cache-dir", type=Path, required=True)
    profiles.add_argument("--output", type=Path, required=True)
    profiles.add_argument("--output-report", type=Path, required=True)
    profiles.add_argument("--start-date", required=True)
    profiles.add_argument("--end-date", required=True)
    profiles.add_argument("--sleep-seconds", type=float, default=0.25)
    profiles.set_defaults(func=_backfill_openmeteo)

    evaluate = subparsers.add_parser("evaluate-sierra", help="fit an unvalidated CSSL candidate and run a storm-grouped temporal holdout")
    evaluate.add_argument("--input", type=Path, required=True)
    evaluate.add_argument("--artifact-output", type=Path, required=True)
    evaluate.add_argument("--report-output", type=Path, required=True)
    evaluate.add_argument("--artifact-id", default="regional-slr-research-cssl-kuchera-v1")
    evaluate.add_argument("--artifact-version", default="research-cssl-kuchera-v1")
    evaluate.add_argument("--test-water-year", type=int)
    evaluate.add_argument("--prior-effective-sample-size", type=float, default=30.0)
    evaluate.add_argument("--min-effective-sample-size", type=float, default=5.0)
    evaluate.set_defaults(func=_evaluate_sierra)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
