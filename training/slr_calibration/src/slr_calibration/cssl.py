from __future__ import annotations

import csv
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

CSSL_STATION_ID = "CSSL"
CSSL_LATITUDE = 39.3257
CSSL_LONGITUDE = -120.3681
CSSL_ELEVATION_M = 2100.0
CSSL_LICENSE = "CC0-1.0"
CSSL_DOI = "10.6078/D1941T"

PRECIP_COLUMN = "24-hour Total Precip (mm)"
SNOW_FRACTION_COLUMN = "% of Precip as Snow"
NEW_SNOW_COLUMN = "New Snow (cm)"
REMARKS_COLUMN = "Remarks"
PUBLISHED_RATIO_COLUMNS = (
    "Snowfall:Precipitation Ratio",
    "Snowfall: Precipitation Ratio",
    "Snowfall Precipitation Ratio",
)
UNSAFE_REMARK_TOKENS = (
    "gauge",
    "precip pen",
    "pen was stuck",
    "stuck pen",
    "malfunction",
    "no measurement",
    "not measured",
    "missing measurement",
    "snow board missing",
    "snowboard missing",
    "wind loss",
    "loss due to evap",
)


@dataclass(frozen=True)
class CsslImportConfig:
    min_new_snow_cm: float = 2.5
    min_fresh_swe_mm: float = 2.5
    min_observed_slr: float = 2.0
    max_observed_slr: float = 50.0
    required_snow_fraction_pct: float = 100.0
    published_ratio_tolerance: float = 0.25


@dataclass(frozen=True)
class CsslRejectedRow:
    source_file: str
    row_number: int
    observation_date: str
    reason: str
    detail: str


def _parse_number(value: Any) -> tuple[float | None, str | None]:
    text = "" if value is None else str(value).strip()
    if text in {"", "--", "NA", "N/A"}:
        return None, "MISSING"
    if text.upper() in {"T", "TRACE"}:
        return None, "TRACE"
    try:
        number = float(text)
    except ValueError:
        return None, "INVALID"
    if not math.isfinite(number):
        return None, "NON_FINITE"
    return number, None


def _published_ratio(row: dict[str, Any]) -> tuple[float | None, str | None]:
    for column in PUBLISHED_RATIO_COLUMNS:
        if column in row:
            return _parse_number(row.get(column))
    return None, "ABSENT"


def _date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (AttributeError, ValueError):
        return None


def _unsafe_remark(remark: str) -> str | None:
    lowered = remark.lower()
    return next((token for token in UNSAFE_REMARK_TOKENS if token in lowered), None)


def _reject(
    rejected: list[CsslRejectedRow],
    source_file: str,
    row_number: int,
    observation_date: str,
    reason: str,
    detail: str,
) -> None:
    rejected.append(CsslRejectedRow(source_file, row_number, observation_date, reason, detail))


def import_cssl_directory(
    input_dir: Path,
    config: CsslImportConfig = CsslImportConfig(),
) -> tuple[list[dict[str, Any]], list[CsslRejectedRow], dict[str, Any]]:
    source_files = sorted(input_dir.glob("WY*.csv"))
    if not source_files:
        raise ValueError(f"No WY*.csv files found in {input_dir}")

    accepted: list[dict[str, Any]] = []
    rejected: list[CsslRejectedRow] = []
    input_rows = 0
    published_ratio_rows = 0
    years: set[int] = set()

    for source_file in source_files:
        try:
            water_year = int(source_file.stem.removeprefix("WY"))
            years.add(water_year)
        except ValueError:
            water_year = 0
        with source_file.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"Date", PRECIP_COLUMN, SNOW_FRACTION_COLUMN, NEW_SNOW_COLUMN, REMARKS_COLUMN}
            missing = sorted(required - set(reader.fieldnames or []))
            if missing:
                raise ValueError(f"{source_file.name} missing columns: {', '.join(missing)}")
            for row_number, row in enumerate(reader, start=2):
                input_rows += 1
                observation_text = str(row.get("Date") or "").strip()
                observation_date = _date(observation_text)
                if observation_date is None:
                    _reject(rejected, source_file.name, row_number, observation_text, "INVALID_DATE", observation_text)
                    continue

                new_snow_cm, snow_issue = _parse_number(row.get(NEW_SNOW_COLUMN))
                if snow_issue:
                    _reject(rejected, source_file.name, row_number, observation_text, f"NEW_SNOW_{snow_issue}", str(row.get(NEW_SNOW_COLUMN)))
                    continue
                precip_mm, precip_issue = _parse_number(row.get(PRECIP_COLUMN))
                if precip_issue:
                    _reject(rejected, source_file.name, row_number, observation_text, f"PRECIP_{precip_issue}", str(row.get(PRECIP_COLUMN)))
                    continue
                snow_fraction_pct, fraction_issue = _parse_number(row.get(SNOW_FRACTION_COLUMN))
                if fraction_issue:
                    _reject(rejected, source_file.name, row_number, observation_text, f"SNOW_FRACTION_{fraction_issue}", str(row.get(SNOW_FRACTION_COLUMN)))
                    continue

                assert new_snow_cm is not None and precip_mm is not None and snow_fraction_pct is not None
                if new_snow_cm <= 0:
                    _reject(rejected, source_file.name, row_number, observation_text, "NO_NEW_SNOW", str(new_snow_cm))
                    continue
                if precip_mm <= 0:
                    _reject(rejected, source_file.name, row_number, observation_text, "NO_PRECIPITATION", str(precip_mm))
                    continue
                if snow_fraction_pct != config.required_snow_fraction_pct:
                    _reject(rejected, source_file.name, row_number, observation_text, "MIXED_OR_UNCERTAIN_PHASE", str(snow_fraction_pct))
                    continue

                fresh_swe_mm = precip_mm * snow_fraction_pct / 100.0
                if new_snow_cm < config.min_new_snow_cm:
                    _reject(rejected, source_file.name, row_number, observation_text, "BELOW_NEW_SNOW_THRESHOLD", str(new_snow_cm))
                    continue
                if fresh_swe_mm < config.min_fresh_swe_mm:
                    _reject(rejected, source_file.name, row_number, observation_text, "BELOW_FRESH_SWE_THRESHOLD", str(fresh_swe_mm))
                    continue

                remarks = str(row.get(REMARKS_COLUMN) or "").strip()
                unsafe_token = _unsafe_remark(remarks)
                if unsafe_token:
                    _reject(rejected, source_file.name, row_number, observation_text, "UNSAFE_MEASUREMENT_REMARK", unsafe_token)
                    continue

                observed_slr = new_snow_cm * 10.0 / fresh_swe_mm
                if not (config.min_observed_slr < observed_slr < config.max_observed_slr):
                    _reject(rejected, source_file.name, row_number, observation_text, "OBSERVED_SLR_RANGE", f"{observed_slr:.8f}")
                    continue

                published_ratio, ratio_issue = _published_ratio(row)
                if ratio_issue is None:
                    published_ratio_rows += 1
                    assert published_ratio is not None
                    relative_error = abs(published_ratio - observed_slr) / max(abs(published_ratio), 1e-9)
                    if relative_error > config.published_ratio_tolerance:
                        _reject(
                            rejected,
                            source_file.name,
                            row_number,
                            observation_text,
                            "PUBLISHED_RATIO_MISMATCH",
                            f"computed={observed_slr:.8f};published={published_ratio:.8f}",
                        )
                        continue

                accepted.append({
                    "event_id": f"CSSL-{observation_text}",
                    "storm_group_id": "",
                    "station_id": CSSL_STATION_ID,
                    "site_id": CSSL_STATION_ID,
                    "valid_time": f"{observation_text}T16:00:00-08:00",
                    "observation_date": observation_text,
                    "water_year": water_year,
                    "latitude": CSSL_LATITUDE,
                    "longitude": CSSL_LONGITUDE,
                    "elevation_m": CSSL_ELEVATION_M,
                    "new_snow_cm": round(new_snow_cm, 6),
                    "total_precip_mm": round(precip_mm, 6),
                    "snow_fraction_pct": round(snow_fraction_pct, 6),
                    "fresh_swe_mm": round(fresh_swe_mm, 6),
                    "observed_slr": round(observed_slr, 8),
                    "published_slr": "" if published_ratio is None else round(published_ratio, 8),
                    "remarks": remarks,
                    "measurement_schedule": "daily sum of 08:00 and 16:00 PST snowboard measurements; precipitation interval not explicitly documented",
                    "profile_alignment_assumption": "fixed-PST local calendar day proxy; research-only",
                    "climate_regime": "maritime_mountain",
                    "qc_passed": True,
                    "qc_weight": 1.0,
                    "source_file": source_file.name,
                    "source_doi": CSSL_DOI,
                    "source_license": CSSL_LICENSE,
                })

    accepted.sort(key=lambda row: (row["observation_date"], row["event_id"]))
    previous_date: date | None = None
    storm_number = 0
    for row in accepted:
        current = _date(str(row["observation_date"]))
        assert current is not None
        if previous_date is None or current - previous_date > timedelta(days=1):
            storm_number += 1
        row["storm_group_id"] = f"CSSL-STORM-{storm_number:04d}"
        previous_date = current

    rejection_counts = Counter(item.reason for item in rejected)
    report = {
        "source": {
            "dataset": "UC Berkeley Central Sierra Snow Laboratory",
            "doi": CSSL_DOI,
            "license": CSSL_LICENSE,
            "stationId": CSSL_STATION_ID,
            "latitude": CSSL_LATITUDE,
            "longitude": CSSL_LONGITUDE,
            "elevationM": CSSL_ELEVATION_M,
        },
        "input": {
            "directory": str(input_dir),
            "fileCount": len(source_files),
            "rowCount": input_rows,
            "waterYears": sorted(years),
        },
        "qc": {
            "acceptedEventCount": len(accepted),
            "rejectedRowCount": len(rejected),
            "rejectionCounts": dict(sorted(rejection_counts.items())),
            "stormGroupCount": len({row["storm_group_id"] for row in accepted}),
            "dateRange": [accepted[0]["observation_date"], accepted[-1]["observation_date"]] if accepted else None,
            "minNewSnowCm": config.min_new_snow_cm,
            "minFreshSweMm": config.min_fresh_swe_mm,
            "observedSlrBoundsExclusive": [config.min_observed_slr, config.max_observed_slr],
            "requiredSnowFractionPct": config.required_snow_fraction_pct,
            "publishedRatioRows": published_ratio_rows,
            "publishedRatioAvailable": published_ratio_rows > 0,
        },
        "scientificLimitations": [
            "Single fixed Central Sierra site; this cannot establish Sierra-wide spatial generalization.",
            "Precipitation timing is not documented precisely enough to guarantee an identical window to the twice-daily snowboard sum.",
            "Only all-snow days are retained; mixed precipitation is excluded.",
            "Total snow depth and total-pack SWE are never differenced to create labels.",
        ],
    }
    return accepted, rejected, report


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_cssl_outputs(
    input_dir: Path,
    output_events: Path,
    output_rejections: Path,
    output_report: Path,
    config: CsslImportConfig = CsslImportConfig(),
) -> dict[str, Any]:
    accepted, rejected, report = import_cssl_directory(input_dir, config)
    write_csv(output_events, accepted)
    write_csv(
        output_rejections,
        [asdict(item) for item in rejected],
        ["source_file", "row_number", "observation_date", "reason", "detail"],
    )
    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
