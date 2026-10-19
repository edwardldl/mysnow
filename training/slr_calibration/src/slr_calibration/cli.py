from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, median

from .cssl import CsslImportConfig, write_cssl_outputs
from .ecmwf_transfer import run_transfer_test
from .evaluation import evaluate_rockies_station_temporal_holdout, evaluate_temporal_holdout
from .forecast_snapshot import SNAPSHOT_MODELS, run_snapshots
from .ghcn import GhcnRockiesConfig, import_ghcn_rockies, write_ghcn_import
from .ghcn_track import ingest_ghcn
from .hrrr_events import adapt_hrrr_events
from .hrrr_dataset import OBS_START_DEFAULT as HRRR_OBS_START_DEFAULT
from .hrrr_dataset import build_dataset as build_hrrr_dataset
from .hrrr_dataset import hrrr_month_plan
from .io import read_rows
from .noaa_hrrr import extract_noaa_hrrr, extract_noaa_months
from .profiles import backfill_openmeteo_multistation_profiles, backfill_openmeteo_profiles
from .regional_evaluation import run_regional_evaluation
from .sierra import SierraImportConfig, import_sierra_ghcn_directory, write_sierra_import
from .sierra_evaluation import evaluate_sierra_station_water_year_holdout
from .snotel_track import ingest_snotel
from .tracking_align import align_events
from .tracking_common import fetch_json
from .tracking_events import build_events
from .tracking_registry import build_registry, write_registry
from .tracking_report import build_status
from .tracking_train import train_tracking
from .trainer import TrainingConfig, train_artifact
from .weak_label_investigation import run_weak_label_investigation


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




def _select_rockies_split(args: argparse.Namespace) -> int:
    train_stations = {value.strip() for value in args.train_stations.split(",") if value.strip()}
    held_out_stations = {value.strip() for value in args.held_out_stations.split(",") if value.strip()}
    if train_stations & held_out_stations:
        raise ValueError("Train and held-out station sets must be disjoint")
    with args.input.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("No Rockies events found")
    selected = []
    for row in rows:
        observed = date.fromisoformat(row["observation_date"])
        water_year = observed.year + 1 if observed.month >= 10 else observed.year
        if (row["station_id"] in train_stations and water_year == args.train_water_year) or (
            row["station_id"] in held_out_stations and water_year == args.test_water_year
        ):
            selected.append(row)
    missing = (train_stations | held_out_stations) - {row["station_id"] for row in selected}
    if missing:
        raise ValueError(f"Selected station/year combinations contain no events: {sorted(missing)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(selected)
    print(json.dumps({
        "output": str(args.output),
        "eventCount": len(selected),
        "trainEventCount": sum(row["station_id"] in train_stations for row in selected),
        "testEventCount": sum(row["station_id"] in held_out_stations for row in selected),
        "stationCount": len({row["station_id"] for row in selected}),
    }, sort_keys=True))
    return 0

def _import_ghcn_rockies(args: argparse.Namespace) -> int:
    events, rejected, report = import_ghcn_rockies(
        args.input,
        GhcnRockiesConfig(
            min_elevation_m=args.min_elevation_m,
            min_snow_mm=args.min_snow_mm,
            min_wesf_mm=args.min_wesf_mm,
            min_observed_slr=args.min_observed_slr,
            max_observed_slr=args.max_observed_slr,
        ),
    )
    write_ghcn_import(events, rejected, report, args.output_events, args.output_rejections, args.output_report)
    print(json.dumps({
        "events": str(args.output_events),
        "report": str(args.output_report),
        "accepted": report["acceptedEventCount"],
        "rejected": report["rejectedRowCount"],
        "stations": report["stationCount"],
    }, sort_keys=True))
    return 0


def _backfill_openmeteo_rockies(args: argparse.Namespace) -> int:
    station_ids = {value.strip() for value in args.station_ids.split(",") if value.strip()} if args.station_ids else None
    report = backfill_openmeteo_multistation_profiles(
        args.input_events,
        args.cache_dir,
        args.output,
        args.output_report,
        date.fromisoformat(args.start_date),
        date.fromisoformat(args.end_date),
        station_ids,
        args.sleep_seconds,
    )
    print(json.dumps({
        "output": str(args.output),
        "report": str(args.output_report),
        "inputEvents": report["inputEventCount"],
        "alignedEvents": report["alignedEventCount"],
        "failedRequests": len(report["failedRequests"]),
    }, sort_keys=True))
    return 0


def _evaluate_rockies(args: argparse.Namespace) -> int:
    held_out = {value.strip() for value in args.held_out_stations.split(",") if value.strip()}
    _, report = evaluate_rockies_station_temporal_holdout(
        args.input,
        args.artifact_output,
        args.report_output,
        args.artifact_id,
        args.artifact_version,
        held_out,
        args.train_water_year,
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

def _import_ghcn_sierra(args: argparse.Namespace) -> int:
    events, rejected, report = import_sierra_ghcn_directory(
        args.station_directory,
        args.station_metadata,
        SierraImportConfig(
            min_snow_mm=args.min_snow_mm,
            min_wesf_mm=args.min_wesf_mm,
            min_observed_slr=args.min_observed_slr,
            max_observed_slr=args.max_observed_slr,
        ),
    )
    write_sierra_import(events, rejected, report, args.output_events, args.output_rejections, args.output_report)
    print(json.dumps({
        "events": str(args.output_events), "report": str(args.output_report),
        "accepted": report["acceptedEventCount"], "rejected": report["rejectedPairCount"],
        "stations": report["stationCount"],
    }, sort_keys=True))
    return 0


def _select_sierra_split(args: argparse.Namespace) -> int:
    train = {value.strip() for value in args.train_stations.split(",") if value.strip()}
    held_out = {value.strip() for value in args.held_out_stations.split(",") if value.strip()}
    if train & held_out:
        raise ValueError("Train and held-out station sets must be disjoint")
    with args.input.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    selected = []
    for row in rows:
        observed = date.fromisoformat(row["observation_date"])
        water_year = observed.year + 1 if observed.month >= 10 else observed.year
        if (row["station_id"] in train and water_year == args.train_water_year) or (row["station_id"] in held_out and water_year == args.test_water_year):
            selected.append(row)
    missing = (train | held_out) - {row["station_id"] for row in selected}
    if missing:
        raise ValueError(f"Selected station/year combinations contain no events: {sorted(missing)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(selected)
    print(json.dumps({"output": str(args.output), "eventCount": len(selected), "stationCount": len({row["station_id"] for row in selected})}, sort_keys=True))
    return 0


def _backfill_openmeteo_sierra(args: argparse.Namespace) -> int:
    station_ids = {value.strip() for value in args.station_ids.split(",") if value.strip()}
    report = backfill_openmeteo_multistation_profiles(
        args.input_events, args.cache_dir, args.output, args.output_report,
        date.fromisoformat(args.start_date), date.fromisoformat(args.end_date),
        station_ids, args.sleep_seconds,
        profile_alignment_label="GHCN 24-hour window ending at reported fixed-PST local-standard observation time",
    )
    print(json.dumps({
        "output": str(args.output), "report": str(args.output_report),
        "inputEvents": report["inputEventCount"], "alignedEvents": report["alignedEventCount"],
        "failedRequests": len(report["failedRequests"]),
    }, sort_keys=True))
    return 0


def _evaluate_sierra_ghcn(args: argparse.Namespace) -> int:
    held_out = {value.strip() for value in args.held_out_stations.split(",") if value.strip()}
    _, report = evaluate_sierra_station_water_year_holdout(
        args.input, args.artifact_output, args.report_output,
        args.artifact_id, args.artifact_version, held_out,
        args.train_water_year, args.test_water_year,
        args.prior_effective_sample_size, args.min_effective_sample_size,
    )
    print(json.dumps({
        "artifact": str(args.artifact_output), "report": str(args.report_output),
        "trainEvents": report["split"]["trainEventCount"],
        "testEvents": report["split"]["testEventCount"],
        "heldOutImprovement": report["improvement"]["heldOutImprovement"],
        "baselineMae": report["baseline"]["mae"], "calibratedMae": report["calibrated"]["mae"],
    }, sort_keys=True))
    return 0


def _track_registry(args: argparse.Namespace) -> int:
    registry = build_registry(
        args.ski_areas,
        args.station_registry,
        fetcher=fetch_json,
        max_station_km=args.max_station_km,
    )
    json_path = args.tracking_dir / "registry.json"
    csv_path = args.tracking_dir / "registry.csv"
    write_registry(registry, json_path, csv_path)
    print(json.dumps({
        "output": str(json_path),
        "resorts": len(registry["resorts"]),
        "resortsWithSnotel": sum(1 for resort in registry["resorts"] if resort["snotel_station"]),
        "resortsWithGhcn": sum(1 for resort in registry["resorts"] if resort["ghcn_stations"]),
        "activeSnotelStationsConsidered": registry["active_snotel_station_count"],
    }, sort_keys=True))
    return 0


def _track_snapshot(args: argparse.Namespace) -> int:
    models = tuple(value.strip() for value in args.models.split(",") if value.strip()) or SNAPSHOT_MODELS
    report = run_snapshots(
        args.tracking_dir / "registry.json",
        args.tracking_dir / "snapshots",
        models=models,
        force=args.force,
    )
    print(json.dumps({
        "date": report["date"],
        "stored": report["storedCount"],
        "skipped": report["skippedCount"],
        "failed": report["failedCount"],
    }, sort_keys=True))
    return 0


def _snotel_range(args: argparse.Namespace) -> tuple[date, date]:
    end = date.fromisoformat(args.end) if args.end else date.today()
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=13)
    return start, end


def _track_ingest_snotel(args: argparse.Namespace) -> int:
    start, end = _snotel_range(args)
    report = ingest_snotel(
        args.tracking_dir / "registry.json",
        args.tracking_dir / "observations" / "snotel_daily.csv",
        start,
        end,
        fetch_json,
    )
    print(json.dumps({
        "output": report["output"],
        "stations": report["stationCount"],
        "fetchedRows": report["fetchedRowCount"],
        "added": report["added"],
        "updated": report["updated"],
        "total": report["total"],
    }, sort_keys=True))
    return 0


def _track_ingest_ghcn(args: argparse.Namespace) -> int:
    report = ingest_ghcn(
        args.tracking_dir / "registry.json",
        args.tracking_dir / "observations" / "ghcn_daily.csv",
        water_years=args.water_years,
    )
    print(json.dumps({
        "output": report["output"],
        "stations": report["stationCount"],
        "rows": report["rowCount"],
        "windowStart": report["windowStart"],
        "failures": len(report["failures"]),
    }, sort_keys=True))
    return 0


def _track_build_events(args: argparse.Namespace) -> int:
    report = build_events(
        args.tracking_dir / "observations" / "ghcn_daily.csv",
        args.tracking_dir / "observations" / "snotel_daily.csv",
        args.tracking_dir / "registry.json",
        args.tracking_dir / "events" / "events.csv",
        args.tracking_dir / "events" / "build_report.json",
    )
    print(json.dumps({
        "events": report["eventCount"],
        "byLabelQuality": report["labelQualityCounts"],
        "weakRejectedCandidates": report["weakSnotelDiff"]["rejectedCandidateCount"],
    }, sort_keys=True))
    return 0


def _track_align(args: argparse.Namespace) -> int:
    report = align_events(
        args.tracking_dir / "events" / "events.csv",
        args.tracking_dir / "registry.json",
        args.tracking_dir / "snapshots",
        args.tracking_dir / "alignment" / "trainer_rows.csv",
        args.tracking_dir / "alignment" / "alignment_report.json",
        args.tracking_dir / "alignment" / "profile_cache",
        sleep_seconds=args.sleep_seconds,
    )
    print(json.dumps({
        "inputEvents": report["inputEventCount"],
        "alignedRows": report["alignedRowCount"],
        "sourceCounts": report["sourceCounts"],
    }, sort_keys=True))
    return 0


def _track_train(args: argparse.Namespace) -> int:
    report = train_tracking(
        args.tracking_dir / "alignment" / "trainer_rows.csv",
        args.tracking_dir / "artifacts" / "tracking-candidate-v1.json",
        args.tracking_dir / "artifacts" / "training_report.json",
    )
    print(json.dumps({
        "rows": report["rowCount"],
        "holdoutComputable": report["holdoutComputable"],
        "status": report["status"],
        "productionEnablement": report["productionEnablement"],
    }, sort_keys=True))
    return 0


def _track_report(args: argparse.Namespace) -> int:
    status = build_status(
        args.tracking_dir,
        args.coefficients,
        args.tracking_dir / "reports" / "status.json",
        args.tracking_dir / "reports" / "status.md",
    )
    print(json.dumps({
        "events": status["events"]["eventCount"],
        "byLabelQuality": status["events"]["byLabelQuality"],
        "snapshotDays": status["snapshots"]["dayCount"],
        "trainerRows": status["alignment"]["trainerRowCount"],
        "productionCalibrationEnabled": status["productionGates"]["productionCalibrationEnabled"],
    }, sort_keys=True))
    return 0


def _track_run(args: argparse.Namespace) -> int:
    """Daily loop: snapshot -> ingest SNOTEL -> ingest GHCN -> events -> report."""
    registry_path = args.tracking_dir / "registry.json"
    if not registry_path.exists():
        print(json.dumps({"note": "registry missing; building it first"}))
        _track_registry(args)
    _track_snapshot(args)
    _track_ingest_snotel(args)
    _track_ingest_ghcn(args)
    _track_build_events(args)
    _track_report(args)
    return 0


def _build_hrrr_dataset(args: argparse.Namespace) -> int:
    report = build_hrrr_dataset(
        ski_areas_path=args.ski_areas,
        dataset_dir=args.dataset_dir,
        obs_start=date.fromisoformat(args.obs_start) if args.obs_start else HRRR_OBS_START_DEFAULT,
        obs_end=date.fromisoformat(args.obs_end) if args.obs_end else date.today(),
        awdb_interval_s=args.awdb_interval,
        open_meteo_interval_s=args.open_meteo_interval,
        forecast_source=args.forecast_source,
    )
    print(json.dumps({
        "stations": report["stations"]["count"],
        "rows": report["output"]["total_rows"],
        "rowsByWaterYear": report["output"]["rows_by_water_year"],
        "qcPassedEventDays": report["output"]["qc_passed_event_days"],
        "forecastSource": report["forecast_source"],
        "awdbRequests": report["requests"]["awdb"]["logical_requests"],
        "openMeteoRequests": report["requests"]["open_meteo"]["logical_requests"],
        "failedChunks": len(report["observations"]["failed_chunks"]) + len(report["hrrr"]["failed_chunks"]),
        "elapsedSeconds": report["elapsed_seconds"],
    }, sort_keys=True))
    return 0


def _extract_noaa_months(args: argparse.Namespace) -> int:
    if args.months:
        months = []
        for token in args.months.split(","):
            token = token.strip()
            if not token:
                continue
            year_text, month_text = token.split("-", 1)
            months.append((int(year_text), int(month_text)))
    else:
        months = hrrr_month_plan()
    summary = extract_noaa_months(
        months,
        dataset_dir=args.dataset_dir,
        max_workers=args.max_workers,
    )
    print(json.dumps({
        "months": {
            key: {
                "complete": entry.get("complete"),
                "skipped": bool(entry.get("skipped")),
                "stationDays": entry.get("station_days"),
                "filesFailed": len(entry.get("files_failed") or []),
                "bytesTransferred": entry.get("bytes_transferred"),
                "wallSeconds": entry.get("wall_seconds"),
            }
            for key, entry in summary["months"].items()
        },
        "totals": summary["totals"],
    }, sort_keys=True))
    return 0


def _extract_noaa_hrrr(args: argparse.Namespace) -> int:
    report = extract_noaa_hrrr(
        dataset_dir=args.dataset_dir,
        start_cycle=date.fromisoformat(args.start_cycle),
        end_cycle=date.fromisoformat(args.end_cycle),
        output_year=int(args.output_month.split("-")[0]),
        output_month=int(args.output_month.split("-")[1]),
        max_workers=args.max_workers,
        max_files=args.max_files,
    )
    print(json.dumps({
        "stations": report["stations"],
        "stationDays": report["coverage"]["station_days"],
        "filesFetched": report["files"]["fetched"],
        "filesCached": report["files"]["cached"],
        "filesFailed": len(report["files"]["failed"]),
        "bytesTransferred": report["operations"]["bytes_transferred"],
        "elapsedSeconds": report["operations"]["wall_seconds"],
    }, sort_keys=True))
    return 0


def _adapt_hrrr_events(args: argparse.Namespace) -> int:
    report = adapt_hrrr_events(
        dataset_dir=args.dataset_dir,
        regimes_path=args.regimes,
        output_parquet=args.output_parquet,
        output_csv=args.output_csv,
        report_path=args.output_report,
    )
    print(json.dumps({"output": str(args.output_parquet), "emitted": report["emittedEvents"], "eligible": report["trainerEligibleEvents"]}, sort_keys=True))
    return 0


def _evaluate_regional_models(args: argparse.Namespace) -> int:
    direct_sets = {
        "rockies-ghcn": (args.direct_rockies, None),
        "sierra-ghcn": (args.direct_sierra, "sierra-nevada"),
        "cssl": (args.direct_cssl, "sierra-nevada"),
    }
    report = run_regional_evaluation(
        events_path=args.events,
        artifacts_dir=args.artifacts_dir,
        report_json_path=args.report_json,
        report_md_path=args.report_md,
        direct_sets=direct_sets,
    )
    verdicts = {region_id: result["verdict"] for region_id, result in report["regions"].items()}
    print(json.dumps({"eligible": report["eligibleEvents"], "verdicts": verdicts, "artifacts": report["artifactsWritten"]}, sort_keys=True))
    return 0


def _transfer_ecmwf(args: argparse.Namespace) -> int:
    report = run_transfer_test(
        events_path=args.events,
        evaluation_report_path=args.evaluation_report,
        cache_dir=args.cache_dir,
        output_path=args.output,
        max_stations_per_region=args.max_stations_per_region,
        sleep_seconds=args.sleep_seconds,
    )
    print(json.dumps({"status": report["status"], "stationsCompleted": report["stationsCompleted"], "eventsBackfilled": report["eventsBackfilled"]}, sort_keys=True))
    return 0


def _investigate_weak_labels(args: argparse.Namespace) -> int:
    results = run_weak_label_investigation(
        events_path=args.events,
        evaluation_report_path=args.evaluation_report,
        rockies_baseline=args.direct_rockies,
        rockies_events=args.rockies_events,
        sierra_baseline=args.direct_sierra,
        sierra_events=args.sierra_events,
        cssl_baseline=args.direct_cssl,
        cssl_profile_events=args.cssl_profile_events,
        cssl_raw_dir=args.cssl_raw_dir,
        dataset_dir=args.dataset_dir,
        regimes_path=args.regimes,
        report_json_path=args.report_json,
        report_md_path=args.report_md,
    )
    reproduction = {
        region_id: {
            "directMae": entry["directMae"],
            "directMaePublished": entry["directMaePublished"],
            "directKucheraMae": entry["directKucheraMae"],
        }
        for region_id, entry in results["reproduction"].items()
    }
    print(json.dumps({"status": results["status"], "reproduction": reproduction}, sort_keys=True))
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

    select_rockies = subparsers.add_parser("select-rockies-split", help="materialize disjoint station and water-year profile candidates")
    select_rockies.add_argument("--input", type=Path, required=True)
    select_rockies.add_argument("--output", type=Path, required=True)
    select_rockies.add_argument("--train-stations", required=True)
    select_rockies.add_argument("--held-out-stations", required=True)
    select_rockies.add_argument("--train-water-year", type=int, default=2024)
    select_rockies.add_argument("--test-water-year", type=int, default=2025)
    select_rockies.set_defaults(func=_select_rockies_split)

    ghcn = subparsers.add_parser("import-ghcn-rockies", help="normalize direct GHCN SNOW+WESF pairs inside the explicit U.S. Rockies research mask")
    ghcn.add_argument("--input", type=Path, required=True)
    ghcn.add_argument("--output-events", type=Path, required=True)
    ghcn.add_argument("--output-rejections", type=Path, required=True)
    ghcn.add_argument("--output-report", type=Path, required=True)
    ghcn.add_argument("--min-elevation-m", type=float, default=1200.0)
    ghcn.add_argument("--min-snow-mm", type=float, default=25.0)
    ghcn.add_argument("--min-wesf-mm", type=float, default=2.5)
    ghcn.add_argument("--min-observed-slr", type=float, default=2.0)
    ghcn.add_argument("--max-observed-slr", type=float, default=50.0)
    ghcn.set_defaults(func=_import_ghcn_rockies)

    rockies_profiles = subparsers.add_parser("backfill-openmeteo-rockies", help="cache recent station-specific pressure profiles for selected GHCN Rockies events")
    rockies_profiles.add_argument("--input-events", type=Path, required=True)
    rockies_profiles.add_argument("--cache-dir", type=Path, required=True)
    rockies_profiles.add_argument("--output", type=Path, required=True)
    rockies_profiles.add_argument("--output-report", type=Path, required=True)
    rockies_profiles.add_argument("--start-date", required=True)
    rockies_profiles.add_argument("--end-date", required=True)
    rockies_profiles.add_argument("--station-ids", required=True, help="comma-separated audited station ids")
    rockies_profiles.add_argument("--sleep-seconds", type=float, default=0.25)
    rockies_profiles.set_defaults(func=_backfill_openmeteo_rockies)

    rockies_eval = subparsers.add_parser("evaluate-rockies", help="fit and evaluate an unvalidated station+time-held-out Rockies candidate")
    rockies_eval.add_argument("--input", type=Path, required=True)
    rockies_eval.add_argument("--artifact-output", type=Path, required=True)
    rockies_eval.add_argument("--report-output", type=Path, required=True)
    rockies_eval.add_argument("--held-out-stations", required=True, help="comma-separated station ids absent from training")
    rockies_eval.add_argument("--artifact-id", default="regional-slr-research-us-rockies-kuchera-v1")
    rockies_eval.add_argument("--artifact-version", default="research-us-rockies-kuchera-v1")
    rockies_eval.add_argument("--train-water-year", type=int, default=2024)
    rockies_eval.add_argument("--test-water-year", type=int, default=2025)
    rockies_eval.add_argument("--prior-effective-sample-size", type=float, default=30.0)
    rockies_eval.add_argument("--min-effective-sample-size", type=float, default=5.0)
    rockies_eval.set_defaults(func=_evaluate_rockies)
    sierra_import = subparsers.add_parser("import-ghcn-sierra", help="normalize strict direct GHCN SNOW+WESF pairs for the audited Sierra station whitelist")
    sierra_import.add_argument("--station-directory", type=Path, required=True)
    sierra_import.add_argument("--station-metadata", type=Path, required=True)
    sierra_import.add_argument("--output-events", type=Path, required=True)
    sierra_import.add_argument("--output-rejections", type=Path, required=True)
    sierra_import.add_argument("--output-report", type=Path, required=True)
    sierra_import.add_argument("--min-snow-mm", type=float, default=25.0)
    sierra_import.add_argument("--min-wesf-mm", type=float, default=2.5)
    sierra_import.add_argument("--min-observed-slr", type=float, default=2.0)
    sierra_import.add_argument("--max-observed-slr", type=float, default=50.0)
    sierra_import.set_defaults(func=_import_ghcn_sierra)

    sierra_split = subparsers.add_parser("select-sierra-split", help="select disjoint Sierra station/water-year cohorts")
    sierra_split.add_argument("--input", type=Path, required=True)
    sierra_split.add_argument("--output", type=Path, required=True)
    sierra_split.add_argument("--train-stations", required=True)
    sierra_split.add_argument("--held-out-stations", required=True)
    sierra_split.add_argument("--train-water-year", type=int, default=2023)
    sierra_split.add_argument("--test-water-year", type=int, default=2024)
    sierra_split.set_defaults(func=_select_sierra_split)

    sierra_profiles = subparsers.add_parser("backfill-openmeteo-sierra", help="cache station-water-year profiles for selected Sierra events")
    sierra_profiles.add_argument("--input-events", type=Path, required=True)
    sierra_profiles.add_argument("--cache-dir", type=Path, required=True)
    sierra_profiles.add_argument("--output", type=Path, required=True)
    sierra_profiles.add_argument("--output-report", type=Path, required=True)
    sierra_profiles.add_argument("--start-date", required=True)
    sierra_profiles.add_argument("--end-date", required=True)
    sierra_profiles.add_argument("--station-ids", required=True)
    sierra_profiles.add_argument("--sleep-seconds", type=float, default=0.25)
    sierra_profiles.set_defaults(func=_backfill_openmeteo_sierra)

    sierra_eval = subparsers.add_parser("evaluate-ghcn-sierra", help="evaluate a research-only unseen-station Sierra candidate")
    sierra_eval.add_argument("--input", type=Path, required=True)
    sierra_eval.add_argument("--artifact-output", type=Path, required=True)
    sierra_eval.add_argument("--report-output", type=Path, required=True)
    sierra_eval.add_argument("--held-out-stations", required=True)
    sierra_eval.add_argument("--artifact-id", default="regional-slr-research-sierra-nevada-kuchera-v1")
    sierra_eval.add_argument("--artifact-version", default="research-sierra-nevada-kuchera-v1")
    sierra_eval.add_argument("--train-water-year", type=int, default=2023)
    sierra_eval.add_argument("--test-water-year", type=int, default=2024)
    sierra_eval.add_argument("--prior-effective-sample-size", type=float, default=30.0)
    sierra_eval.add_argument("--min-effective-sample-size", type=float, default=5.0)
    sierra_eval.set_defaults(func=_evaluate_sierra_ghcn)

    def _add_tracking_dir(parser_: argparse.ArgumentParser) -> None:
        parser_.add_argument("--tracking-dir", type=Path, default=Path("data/tracking"))

    def _add_registry_args(parser_: argparse.ArgumentParser) -> None:
        parser_.add_argument("--ski-areas", type=Path, default=Path("src/data/ski-areas.json"))
        parser_.add_argument(
            "--station-registry", type=Path,
            default=Path("data/slr-training-data/normalized/station_metadata/unified_station_registry.csv"),
        )
        parser_.add_argument("--max-station-km", type=float, default=40.0)

    def _add_snotel_args(parser_: argparse.ArgumentParser) -> None:
        parser_.add_argument("--start", help="YYYY-MM-DD; default 14 days before --end")
        parser_.add_argument("--end", help="YYYY-MM-DD; default today")

    track_registry = subparsers.add_parser("track-registry", help="build the pilot resort/station tracking registry")
    _add_tracking_dir(track_registry)
    _add_registry_args(track_registry)
    track_registry.set_defaults(func=_track_registry)

    track_snapshot = subparsers.add_parser("track-snapshot", help="store today's Open-Meteo forecast snapshots for registry points")
    _add_tracking_dir(track_snapshot)
    track_snapshot.add_argument("--models", default=",".join(SNAPSHOT_MODELS))
    track_snapshot.add_argument("--force", action="store_true")
    track_snapshot.set_defaults(func=_track_snapshot)

    track_snotel = subparsers.add_parser("track-ingest-snotel", help="ingest AWDB SNOTEL daily totals for registry stations")
    _add_tracking_dir(track_snotel)
    _add_snotel_args(track_snotel)
    track_snotel.set_defaults(func=_track_ingest_snotel)

    track_ghcn = subparsers.add_parser("track-ingest-ghcn", help="ingest NCEI GHCN daily rows for registry stations")
    _add_tracking_dir(track_ghcn)
    track_ghcn.add_argument("--water-years", type=int, default=2)
    track_ghcn.set_defaults(func=_track_ingest_ghcn)

    track_events = subparsers.add_parser("track-build-events", help="build normalized direct + weak tracking events")
    _add_tracking_dir(track_events)
    track_events.set_defaults(func=_track_build_events)

    track_align = subparsers.add_parser("track-align", help="pair events with Kuchera baselines (snapshots preferred, backfill fallback)")
    _add_tracking_dir(track_align)
    track_align.add_argument("--sleep-seconds", type=float, default=0.25)
    track_align.set_defaults(func=_track_align)

    track_train = subparsers.add_parser("track-train", help="train an unvalidated candidate and run the temporal holdout when computable")
    _add_tracking_dir(track_train)
    track_train.set_defaults(func=_track_train)

    track_report = subparsers.add_parser("track-report", help="write the tracking status report (JSON + Markdown)")
    _add_tracking_dir(track_report)
    track_report.add_argument("--coefficients", type=Path, default=Path("src/lib/snow/calibration/coefficients.ts"))
    track_report.set_defaults(func=_track_report)

    track_run = subparsers.add_parser("track-run", help="daily loop: snapshot + ingest + events + report")
    _add_tracking_dir(track_run)
    _add_registry_args(track_run)
    _add_snotel_args(track_run)
    track_run.add_argument("--models", default=",".join(SNAPSHOT_MODELS))
    track_run.add_argument("--force", action="store_true")
    track_run.add_argument("--water-years", type=int, default=2)
    track_run.add_argument("--coefficients", type=Path, default=Path("src/lib/snow/calibration/coefficients.ts"))
    track_run.set_defaults(func=_track_run)

    hrrr_dataset = subparsers.add_parser(
        "build-hrrr-dataset",
        help="build the daily SNOTEL observations + HRRR forecast dataset for near-resort stations (rate-limited, resumable)",
    )
    hrrr_dataset.add_argument("--dataset-dir", type=Path, default=Path("data/hrrr-dataset"))
    hrrr_dataset.add_argument("--ski-areas", type=Path, default=Path("src/data/ski-areas.json"))
    hrrr_dataset.add_argument("--obs-start", default="2023-10-01")
    hrrr_dataset.add_argument("--obs-end", help="YYYY-MM-DD; default today")
    hrrr_dataset.add_argument("--awdb-interval", type=float, default=1.5)
    hrrr_dataset.add_argument("--open-meteo-interval", type=float, default=2.0)
    hrrr_dataset.add_argument(
        "--forecast-source",
        choices=("noaa", "open-meteo"),
        default="noaa",
        help="forecast source feeding the dataset (default noaa: NOAA GRIB monthly store; open-meteo is the legacy path)",
    )
    hrrr_dataset.set_defaults(func=_build_hrrr_dataset)

    noaa_months = subparsers.add_parser(
        "extract-noaa-months",
        help="extract NOAA HRRR GRIB2 daily aggregates into the noaa-monthly store that build-hrrr-dataset joins (resumable; skips complete months)",
    )
    noaa_months.add_argument("--dataset-dir", type=Path, default=Path("data/hrrr-dataset"))
    noaa_months.add_argument("--months", help="comma-separated YYYY-MM list; default is the full dataset plan order")
    noaa_months.add_argument("--max-workers", type=int, default=4)
    noaa_months.set_defaults(func=_extract_noaa_months)

    noaa_hrrr = subparsers.add_parser(
        "extract-noaa-hrrr",
        help="prototype: extract NOAA HRRR GRIB2 daily aggregates from AWS S3 and benchmark them against the Open-Meteo dataset month",
    )
    noaa_hrrr.add_argument("--dataset-dir", type=Path, default=Path("data/hrrr-dataset"))
    noaa_hrrr.add_argument("--start-cycle", default="2024-11-30")
    noaa_hrrr.add_argument("--end-cycle", default="2025-01-01")
    noaa_hrrr.add_argument("--output-month", default="2024-12")
    noaa_hrrr.add_argument("--max-workers", type=int, default=4)
    noaa_hrrr.add_argument("--max-files", type=int, help="debug cap on GRIB files processed")
    noaa_hrrr.set_defaults(func=_extract_noaa_hrrr)

    adapt = subparsers.add_parser("adapt-hrrr-events", help="adapt the NOAA HRRR/SNOTEL dataset into normalized trainer events with the station regime map")
    adapt.add_argument("--dataset-dir", type=Path, default=Path("data/hrrr-dataset"))
    adapt.add_argument("--regimes", type=Path, default=Path("training/slr_calibration/regions/station_regimes.csv"))
    adapt.add_argument("--output-parquet", type=Path, default=Path("data/hrrr-dataset/model-evaluation/events.parquet"))
    adapt.add_argument("--output-csv", type=Path)
    adapt.add_argument("--output-report", type=Path, default=Path("data/hrrr-dataset/model-evaluation/adapter_report.json"))
    adapt.set_defaults(func=_adapt_hrrr_events)

    regional = subparsers.add_parser("evaluate-regional-models", help="fit and gate Candidates A and B per region on the adapted HRRR events")
    regional.add_argument("--events", type=Path, default=Path("data/hrrr-dataset/model-evaluation/events.parquet"))
    regional.add_argument("--artifacts-dir", type=Path, default=Path("training/slr_calibration/artifacts"))
    regional.add_argument("--report-json", type=Path, default=Path("data/hrrr-dataset/model-evaluation/evaluation_report.json"))
    regional.add_argument("--report-md", type=Path, default=Path("data/hrrr-dataset/model-evaluation/summary.md"))
    regional.add_argument("--direct-rockies", type=Path, default=Path("data/slr-training-data/normalized/baseline/ghcn_rockies_kuchera.csv"))
    regional.add_argument("--direct-sierra", type=Path, default=Path("data/sierra-slr-research/normalized/sierra_kuchera.csv"))
    regional.add_argument("--direct-cssl", type=Path, default=Path("data/cssl/v13/research/cssl-aligned-baselines.csv"))
    regional.set_defaults(func=_evaluate_regional_models)

    transfer = subparsers.add_parser("transfer-ecmwf", help="ECMWF IFS transfer test for retained regional artifacts (design section 8)")
    transfer.add_argument("--events", type=Path, default=Path("data/hrrr-dataset/model-evaluation/events.parquet"))
    transfer.add_argument("--evaluation-report", type=Path, default=Path("data/hrrr-dataset/model-evaluation/evaluation_report.json"))
    transfer.add_argument("--cache-dir", type=Path, default=Path("data/hrrr-dataset/model-evaluation/ecmwf-cache"))
    transfer.add_argument("--output", type=Path, default=Path("data/hrrr-dataset/model-evaluation/transfer_report.json"))
    transfer.add_argument("--max-stations-per-region", type=int, default=4)
    transfer.add_argument("--sleep-seconds", type=float, default=1.2)
    transfer.set_defaults(func=_transfer_ecmwf)

    investigate = subparsers.add_parser("investigate-weak-labels", help="diagnose why weak-label regional corrections fail on direct labels (research-only, no artifacts)")
    investigate.add_argument("--events", type=Path, default=Path("data/hrrr-dataset/model-evaluation/events.parquet"))
    investigate.add_argument("--evaluation-report", type=Path, default=Path("data/hrrr-dataset/model-evaluation/evaluation_report.json"))
    investigate.add_argument("--direct-rockies", type=Path, default=Path("data/slr-training-data/normalized/baseline/ghcn_rockies_kuchera.csv"))
    investigate.add_argument("--rockies-events", type=Path, default=Path("data/slr-training-data/normalized/observations/ghcn_rockies_events.csv"))
    investigate.add_argument("--direct-sierra", type=Path, default=Path("data/sierra-slr-research/normalized/sierra_kuchera.csv"))
    investigate.add_argument("--sierra-events", type=Path, default=Path("data/sierra-slr-research/normalized/sierra_events.csv"))
    investigate.add_argument("--direct-cssl", type=Path, default=Path("data/cssl/v13/research/cssl-aligned-baselines.csv"))
    investigate.add_argument("--cssl-profile-events", type=Path, default=Path("data/cssl/v13/research/cssl-profile-events.jsonl"))
    investigate.add_argument("--cssl-raw-dir", type=Path, default=Path("data/cssl/v13/raw"))
    investigate.add_argument("--dataset-dir", type=Path, default=Path("data/hrrr-dataset"))
    investigate.add_argument("--regimes", type=Path, default=Path("training/slr_calibration/regions/station_regimes.csv"))
    investigate.add_argument("--report-json", type=Path, default=Path("data/hrrr-dataset/model-evaluation/weak_label_investigation.json"))
    investigate.add_argument("--report-md", type=Path, default=Path("data/hrrr-dataset/model-evaluation/weak-label-investigation.md"))
    investigate.set_defaults(func=_investigate_weak_labels)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
