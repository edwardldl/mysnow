from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .forecast_snapshot import read_index
from .tracking_common import read_csv_rows, write_json
from .tracking_registry import load_registry

RECENT_OBSERVATION_DAYS = 3


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_production_gates(coefficients_path: Path) -> dict[str, Any]:
    """Read the live production calibration gates from the app source.

    Read-only inspection of src/lib/snow/calibration/coefficients.ts; the
    tracking loop never modifies production code.
    """
    gates: dict[str, Any] = {
        "coefficientsPath": str(coefficients_path),
        "modifiersEnabled": None,
        "regionalBiasEnabled": None,
        "allowUnvalidatedRegionalArtifact": None,
        "productionCalibrationEnabled": None,
    }
    if not coefficients_path.exists():
        return gates
    text = coefficients_path.read_text(encoding="utf-8")
    for key in ("modifiersEnabled", "regionalBiasEnabled", "allowUnvalidatedRegionalArtifact"):
        match = re.search(rf"{key}\s*:\s*(true|false)", text)
        if match:
            gates[key] = match.group(1) == "true"
    if gates["regionalBiasEnabled"] is not None:
        gates["productionCalibrationEnabled"] = bool(gates["regionalBiasEnabled"])
    return gates


def _recent_stations(rows: list[dict[str, str]], today: date, days: int = RECENT_OBSERVATION_DAYS) -> list[dict[str, Any]]:
    latest: dict[str, str] = {}
    for row in rows:
        station = str(row.get("station_triplet") or row.get("station_id") or "")
        day = str(row.get("date") or "")
        if station and day and (station not in latest or day > latest[station]):
            latest[station] = day
    cutoff = (today - timedelta(days=days)).isoformat()
    return [
        {"station": station, "latestObservationDate": day}
        for station, day in sorted(latest.items()) if day >= cutoff
    ]


def build_status(
    tracking_dir: Path,
    coefficients_path: Path,
    output_json_path: Path,
    output_md_path: Path,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    registry_path = tracking_dir / "registry.json"
    registry = load_registry(registry_path) if registry_path.exists() else {"resorts": []}
    resorts = registry.get("resorts", [])

    snotel_rows = read_csv_rows(tracking_dir / "observations" / "snotel_daily.csv")
    ghcn_rows = read_csv_rows(tracking_dir / "observations" / "ghcn_daily.csv")
    events = read_csv_rows(tracking_dir / "events" / "events.csv")
    trainer_rows = read_csv_rows(tracking_dir / "alignment" / "trainer_rows.csv")
    index_rows = read_index(tracking_dir / "snapshots" / "index.jsonl")
    alignment_report = _read_json(tracking_dir / "alignment" / "alignment_report.json")
    training_report = _read_json(tracking_dir / "artifacts" / "training_report.json")

    snapshot_days = sorted({str(row.get("issued_at_utc") or "")[:10] for row in index_rows if row.get("issued_at_utc")})
    label_counts = Counter(str(event.get("label_quality") or "unknown") for event in events)
    region_counts = Counter(str(event.get("climate_regime") or "unknown") for event in events)
    source_counts = Counter(str(event.get("source") or "unknown") for event in events)

    status: dict[str, Any] = {
        "generatedForDate": today.isoformat(),
        "registry": {
            "resortCount": len(resorts),
            "forecastPointCount": sum(len(resort.get("forecast_points", [])) for resort in resorts),
            "resortsWithSnotel": sum(1 for resort in resorts if resort.get("snotel_station")),
            "resortsWithGhcn": sum(1 for resort in resorts if resort.get("ghcn_stations")),
            "ghcnStationCount": sum(len(resort.get("ghcn_stations", [])) for resort in resorts),
        },
        "observations": {
            "snotelDailyRowCount": len(snotel_rows),
            "snotelStationCount": len({row.get("station_triplet") for row in snotel_rows}),
            "ghcnDailyRowCount": len(ghcn_rows),
            "ghcnStationCount": len({row.get("station_id") for row in ghcn_rows}),
            "recentStations": {
                "windowDays": RECENT_OBSERVATION_DAYS,
                "snotel": _recent_stations(snotel_rows, today),
                "ghcn": _recent_stations(ghcn_rows, today),
            },
        },
        "snapshots": {
            "snapshotCount": len(index_rows),
            "daysCollected": snapshot_days,
            "dayCount": len(snapshot_days),
            "models": sorted({str(row.get("model")) for row in index_rows}),
        },
        "events": {
            "eventCount": len(events),
            "byLabelQuality": dict(sorted(label_counts.items())),
            "byClimateRegime": dict(sorted(region_counts.items())),
            "bySource": dict(sorted(source_counts.items())),
            "dateRange": [
                min((str(event["observation_date"]) for event in events), default=None),
                max((str(event["observation_date"]) for event in events), default=None),
            ],
        },
        "alignment": {
            "trainerRowCount": len(trainer_rows),
            "report": alignment_report,
        },
        "training": training_report,
        "productionGates": _parse_production_gates(coefficients_path),
    }
    write_json(output_json_path, status)
    output_md_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path.write_text(render_status_markdown(status), encoding="utf-8")
    return status


def render_status_markdown(status: dict[str, Any]) -> str:
    registry = status["registry"]
    observations = status["observations"]
    snapshots = status["snapshots"]
    events = status["events"]
    training = status.get("training") or {}
    gates = status["productionGates"]
    lines = [
        "# MySnow SLR tracking status",
        "",
        f"Generated for {status['generatedForDate']}.",
        "",
        "## Coverage",
        "",
        f"- Pilot resorts: {registry['resortCount']} ({registry['forecastPointCount']} forecast points)",
        f"- Resorts with a SNOTEL station within 40 km: {registry['resortsWithSnotel']}",
        f"- Resorts with GHCN paired stations within 40 km: {registry['resortsWithGhcn']} "
        f"({registry['ghcnStationCount']} stations)",
        f"- SNOTEL daily rows: {observations['snotelDailyRowCount']} across {observations['snotelStationCount']} stations",
        f"- GHCN daily rows: {observations['ghcnDailyRowCount']} across {observations['ghcnStationCount']} stations",
        "",
        "## Recent observations (last 3 days)",
        "",
    ]
    recent = observations["recentStations"]
    if recent["snotel"] or recent["ghcn"]:
        for item in recent["snotel"]:
            lines.append(f"- SNOTEL {item['station']}: latest {item['latestObservationDate']}")
        for item in recent["ghcn"]:
            lines.append(f"- GHCN {item['station']}: latest {item['latestObservationDate']}")
    else:
        lines.append("- None in the last 3 days.")
    lines += [
        "",
        "## Forecast snapshots",
        "",
        f"- Snapshots stored: {snapshots['snapshotCount']} over {snapshots['dayCount']} day(s)",
        f"- Models: {', '.join(snapshots['models']) or 'none'}",
        "",
        "## Events",
        "",
        f"- Total events: {events['eventCount']} ({events['dateRange'][0]} .. {events['dateRange'][1]})",
    ]
    for label, count in events["byLabelQuality"].items():
        lines.append(f"- label_quality={label}: {count}")
    for regime, count in events["byClimateRegime"].items():
        lines.append(f"- region {regime}: {count}")
    lines += [
        "",
        "## Alignment and training",
        "",
        f"- Trainer rows aligned: {status['alignment']['trainerRowCount']}",
    ]
    if training.get("holdoutComputable") and training.get("baseline") and training.get("calibrated"):
        lines.append(
            f"- Holdout MAE: Kuchera baseline {training['baseline']['mae']:.3f} vs "
            f"candidate {training['calibrated']['mae']:.3f}"
        )
    else:
        lines.append("- Holdout MAE: not computable yet (insufficient aligned events).")
        if training.get("holdoutNote"):
            lines.append(f"- Note: {training['holdoutNote']}")
    lines += [
        "",
        "## Production gates",
        "",
        f"- modifiersEnabled: {gates['modifiersEnabled']}",
        f"- regionalBiasEnabled: {gates['regionalBiasEnabled']}",
        f"- allowUnvalidatedRegionalArtifact: {gates['allowUnvalidatedRegionalArtifact']}",
        f"- Tracking artifact status: {training.get('status', 'none trained')}",
        "- Production calibration remains DISABLED. Tracking artifacts are unvalidated",
        "  research candidates; enabling production requires validated holdout evidence",
        "  and a separate, explicit production change.",
        "",
    ]
    return "\n".join(lines)
