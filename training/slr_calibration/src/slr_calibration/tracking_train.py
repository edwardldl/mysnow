from __future__ import annotations

import math
from pathlib import Path
from statistics import mean, median
from typing import Any

from .evaluation import apply_artifact
from .io import read_rows
from .trainer import TrainingConfig, train_artifact
from .tracking_common import write_json

ARTIFACT_ID = "regional-slr-tracking-kuchera-v1"
ARTIFACT_VERSION = "tracking-kuchera-v1"
SOURCE_LABEL = (
    "MySnow tracking loop: GHCN direct SNOW+WESF labels and clearly flagged "
    "weak SNOTEL total-pack differencing labels, paired with production "
    "Kuchera baselines; research-only"
)


def _water_year(row: dict[str, Any]) -> int:
    value = str(row["valid_time"])[:10]
    year, month, _ = (int(part) for part in value.split("-"))
    return year + 1 if month >= 10 else year


def _metrics(rows: list[dict[str, Any]], prediction_key: str) -> dict[str, float]:
    errors = [float(row[prediction_key]) - float(row["observed_slr"]) for row in rows]
    absolute = [abs(value) for value in errors]
    return {
        "bias": mean(errors),
        "mae": mean(absolute),
        "medianAbsoluteError": median(absolute),
        "rmse": math.sqrt(mean(value * value for value in errors)),
    }


def _config() -> TrainingConfig:
    return TrainingConfig(
        artifact_id=ARTIFACT_ID,
        artifact_version=ARTIFACT_VERSION,
        status="unvalidated",
        source_label=SOURCE_LABEL,
        leakage_group="storm_group_id",
        geographic_note=(
            "Pilot-resort tracking coverage only (Sierra, Rockies, PNW, Northeast); "
            "absent cells fall back to broader hierarchy levels. Not validated for production."
        ),
    )


def train_tracking(
    rows_path: Path,
    artifact_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    """Train an unvalidated candidate and, when data allow, hold out a year.

    Uses the existing trainer (train_artifact) and artifact application
    (evaluation.apply_artifact).  The temporal holdout mirrors
    evaluation.evaluate_temporal_holdout: the latest water year with at
    least five rows is tested with complete storm groups, trained on
    strictly earlier years.  When a holdout is not computable the artifact
    is still fit on all rows and the report says so explicitly.
    """
    rows = read_rows(rows_path) if rows_path.exists() else []
    report: dict[str, Any] = {
        "status": "unvalidated",
        "artifactId": ARTIFACT_ID,
        "artifactVersion": ARTIFACT_VERSION,
        "input": str(rows_path),
        "rowCount": len(rows),
        "split": None,
        "baseline": None,
        "calibrated": None,
        "holdoutComputable": False,
        "holdoutNote": None,
        "productionEnablement": False,
    }
    if not rows:
        report["holdoutNote"] = "No aligned trainer rows exist yet; nothing was trained."
        write_json(report_path, report)
        return report

    counts: dict[int, int] = {}
    for row in rows:
        counts[_water_year(row)] = counts.get(_water_year(row), 0) + 1
    eligible_years = [year for year, count in counts.items() if count >= 5]
    artifact: dict[str, Any] | None = None
    if eligible_years and len(rows) >= 10:
        test_year = max(eligible_years)
        test_groups = {
            str(row.get("storm_group_id") or row["event_id"])
            for row in rows if _water_year(row) == test_year
        }
        train_rows = [
            row for row in rows
            if str(row.get("storm_group_id") or row["event_id"]) not in test_groups
            and _water_year(row) < test_year
        ]
        test_rows = [row for row in rows if str(row.get("storm_group_id") or row["event_id"]) in test_groups]
        if len(train_rows) >= 5 and len(test_rows) >= 5:
            artifact = train_artifact(train_rows, _config())
            evaluated: list[dict[str, Any]] = []
            for row in test_rows:
                result = apply_artifact(artifact, row)
                evaluated.append({
                    **row,
                    "baseline_prediction": float(row["baseline_slr"]),
                    "calibrated_prediction": result.calibrated_slr,
                })
            baseline_metrics = _metrics(evaluated, "baseline_prediction")
            calibrated_metrics = _metrics(evaluated, "calibrated_prediction")
            report.update({
                "holdoutComputable": True,
                "split": {
                    "method": "chronological water-year holdout with complete storm groups",
                    "leakageGroup": "storm_group_id",
                    "trainWaterYears": sorted({_water_year(row) for row in train_rows}),
                    "testWaterYear": test_year,
                    "trainEventCount": len(train_rows),
                    "testEventCount": len(test_rows),
                    "stationCount": len({str(row["station_id"]) for row in rows}),
                },
                "baseline": baseline_metrics,
                "calibrated": calibrated_metrics,
                "improvement": {
                    "mae": baseline_metrics["mae"] - calibrated_metrics["mae"],
                    "medianAbsoluteError": baseline_metrics["medianAbsoluteError"] - calibrated_metrics["medianAbsoluteError"],
                    "heldOutImprovement": baseline_metrics["mae"] - calibrated_metrics["mae"] > 0,
                },
            })
    if artifact is None:
        artifact = train_artifact(rows, _config())
        report["holdoutNote"] = (
            "Temporal holdout not computable (needs >=10 rows, a test water year with >=5 rows, "
            "and >=5 earlier-year training rows); candidate fit on all rows without holdout metrics."
        )
        report["split"] = {
            "method": "none (insufficient data for a temporal holdout)",
            "trainEventCount": len(rows),
            "stationCount": len({str(row["station_id"]) for row in rows}),
            "waterYears": dict(sorted(counts.items())),
        }

    write_json(artifact_path, artifact)
    report["artifactPath"] = str(artifact_path)
    report["acceptedEventCount"] = artifact["training"]["acceptedEventCount"]
    write_json(report_path, report)
    return report
