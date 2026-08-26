from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any

SUPPORTED_METHODS = {"kuchera", "cobb_2011"}


@dataclass(frozen=True)
class QcIssue:
    code: str
    message: str


@dataclass(frozen=True)
class StormEvent:
    event_id: str
    station_id: str
    site_id: str | None
    valid_time: str
    latitude: float
    longitude: float
    observed_slr: float
    baseline_slr: float
    baseline_method: str
    climate_regime: str
    qc_weight: float

    @property
    def trained_date(self) -> str:
        return self.valid_time[:10]


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "passed"}


def parse_event(row: dict[str, Any]) -> tuple[StormEvent | None, list[QcIssue]]:
    issues: list[QcIssue] = []
    required = ("event_id", "station_id", "valid_time", "latitude", "longitude", "observed_slr", "baseline_slr", "baseline_method")
    for field in required:
        if row.get(field) in (None, ""):
            issues.append(QcIssue("MISSING_FIELD", field))
    if issues:
        return None, issues

    try:
        latitude = float(row["latitude"])
        longitude = float(row["longitude"])
        observed = float(row["observed_slr"])
        baseline = float(row["baseline_slr"])
        weight = float(row.get("qc_weight", 1.0))
        datetime.fromisoformat(str(row["valid_time"]).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        return None, [QcIssue("INVALID_VALUE", str(exc))]

    method = str(row["baseline_method"])
    if method not in SUPPORTED_METHODS:
        issues.append(QcIssue("UNSUPPORTED_METHOD", method))
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        issues.append(QcIssue("INVALID_COORDINATES", f"{latitude},{longitude}"))
    if not all(isfinite(value) for value in (observed, baseline, weight)):
        issues.append(QcIssue("NON_FINITE", "observed_slr/baseline_slr/qc_weight"))
    if not (2 < observed < 50):
        issues.append(QcIssue("OBSERVED_SLR_RANGE", str(observed)))
    if not (3 <= baseline <= 30):
        issues.append(QcIssue("BASELINE_SLR_RANGE", str(baseline)))
    if not (0 < weight <= 1):
        issues.append(QcIssue("QC_WEIGHT_RANGE", str(weight)))
    if row.get("qc_passed") not in (None, "") and not _truthy(row["qc_passed"]):
        issues.append(QcIssue("SOURCE_QC_REJECTED", str(row["qc_passed"])))
    if issues:
        return None, issues

    return StormEvent(
        event_id=str(row["event_id"]),
        station_id=str(row["station_id"]),
        site_id=str(row["site_id"]) if row.get("site_id") not in (None, "") else None,
        valid_time=str(row["valid_time"]),
        latitude=latitude,
        longitude=longitude,
        observed_slr=observed,
        baseline_slr=baseline,
        baseline_method=method,
        climate_regime=str(row.get("climate_regime") or "unclassified"),
        qc_weight=weight,
    ), []
