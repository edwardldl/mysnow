#!/usr/bin/env python3
"""Compare resort-reported snowfall against the model forecasts.

Reads the parsed resort reports under data/tracking/resort-reports/
(reports-*.jsonl, produced by collect_resort_reports.py), aligns each
report to the model window it describes (see
slr_calibration.resort_reports for the alignment rule and its
caveats), selects the forecast of record for that window (the last
cycle issued before the window began, from the per-cycle tracking
records in data/tracking/model-serving/, falling back to the installed
frontend JSON under the same rule), and writes:

* data/tracking/resort-reports/comparisons.jsonl -- one row per
  matched (resort, window, point, arm), regenerated on every run;
* data/tracking/resort-reports/verification-<run date>.md -- the
  human-readable report: per-resort-day detail plus bias/MAE
  aggregates overall, by point role, and by resort.

Errors are reported minus forecast, in centimetres: a positive bias
means the resorts reported more snow than the model forecast.
"""

from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slr_calibration.resort_reports import (  # noqa: E402
    aggregate_errors,
    comparison_row,
    select_forecast_vintage,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORTS_DIR = REPO_ROOT / "data" / "tracking" / "resort-reports"
TRACKING_DIR = REPO_ROOT / "data" / "tracking" / "model-serving"
FRONTEND_PATH = REPO_ROOT / "src" / "data" / "model-forecasts.json"
ARMS = ("ecmwf", "hrrr")
ROLES = ("base", "mid_mountain", "summit")


def load_reports() -> list[dict]:
    """Latest parsed report per (resort, window date).

    When several collections captured the same resort-day, prefer the
    latest one that carries a 24 h snow value; otherwise the latest.
    """
    best: dict[tuple[str, str], dict] = {}
    for path in sorted(glob.glob(str(REPORTS_DIR / "reports-*.jsonl"))):
        for line in Path(path).read_text().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            key = (record["resort_slug"], record["window_end_local_date"])
            current = best.get(key)
            if current is None:
                best[key] = record
                continue
            new_has = record["new_snow_24h_cm"]["mid"] is not None
            cur_has = current["new_snow_24h_cm"]["mid"] is not None
            if (new_has and not cur_has) or (
                new_has == cur_has
                and record["collected_at_utc"] >= current["collected_at_utc"]
            ):
                best[key] = record
    return sorted(best.values(), key=lambda r: (r["window_end_local_date"], r["resort_slug"]))


def load_tracking_candidates() -> dict[tuple[str, str, str], list[dict]]:
    """(point_id, arm, window_end_local_date) -> tracking records."""
    candidates: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for path in sorted(TRACKING_DIR.glob("records-*.jsonl")):
        if "-openmeteo" in path.name or "-extended" in path.name:
            continue  # retired Open-Meteo arm / one-off extended run
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            key = (record["point_id"], record["source"], record["window_end_local_date"])
            candidates[key].append(record)
    return candidates


def load_frontend() -> tuple[dict[str, dict], dict[tuple[str, str, str], dict]]:
    """Frontend points by slug, and per-window frontend records."""
    frontend = json.loads(FRONTEND_PATH.read_text())
    points_by_slug: dict[str, list[dict]] = defaultdict(list)
    records: dict[tuple[str, str, str], dict] = {}
    for point in frontend["points"]:
        points_by_slug[point["resortSlug"]].append(point)
        for arm, rows in point["forecasts"].items():
            for row in rows:
                records[(point["pointId"], arm, row["windowEndLocalDate"])] = row
    return dict(points_by_slug), records


def build_comparisons() -> tuple[list[dict], list[dict]]:
    reports = load_reports()
    tracking = load_tracking_candidates()
    points_by_slug, frontend_records = load_frontend()

    rows: list[dict] = []
    unmatched: list[dict] = []
    for report in reports:
        slug = report["resort_slug"]
        date = report["window_end_local_date"]
        if report["new_snow_24h_cm"]["mid"] is None:
            unmatched.append({**_unmatched_base(report), "reason": "no 24 h snow value reported"})
            continue
        points = points_by_slug.get(slug, [])
        if not points:
            unmatched.append({**_unmatched_base(report), "reason": "resort not in frontend points"})
            continue
        matched_any = False
        for point in points:
            for arm in ARMS:
                key = (point["pointId"], arm, date)
                candidates = list(tracking.get(key, []))
                frontend_record = frontend_records.get(key)
                window_start = None
                if candidates:
                    window_start = candidates[0]["window_start_utc"]
                elif frontend_record is not None:
                    window_start = frontend_record["windowStartUtc"]
                if window_start is None:
                    continue
                pool = candidates + ([frontend_record] if frontend_record else [])
                chosen = select_forecast_vintage(pool, window_start)
                if chosen is None:
                    continue
                rows.append(comparison_row(report, chosen, role=point["role"], arm=arm))
                matched_any = True
        if not matched_any:
            unmatched.append(
                {**_unmatched_base(report), "reason": "no forecast issued before the window began"}
            )
    return rows, unmatched


def _unmatched_base(report: dict) -> dict:
    return {
        "resort_slug": report["resort_slug"],
        "resort_name": report["resort_name"],
        "window_end_local_date": report["window_end_local_date"],
        "report_datetime_local": report["report_datetime_local"],
        "operating_status": report["operating_status"],
    }


def _fmt(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def render_markdown(rows: list[dict], unmatched: list[dict], run_date: str) -> str:
    lines: list[str] = []
    lines.append(f"# Resort-report verification — {run_date}")
    lines.append("")
    lines.append(
        "Resort-reported 24 h snowfall (SnoCountry; midpoint of the reported "
        "range, cm) against the ENS-EB and Kuchera forecasts of record — the "
        "last cycle issued before each window began. Errors are reported "
        "minus forecast: positive bias = resorts reported more than forecast. "
        "Alignment rule and caveats: `slr_calibration/resort_reports.py`."
    )
    lines.append("")
    resort_days = {(r["resort_slug"], r["window_end_local_date"]) for r in rows}
    lines.append(
        f"Matched resort-days: **{len(resort_days)}** · comparison rows: "
        f"{len(rows)} · unmatched reports: {len(unmatched)}"
    )
    lines.append("")

    lines.append("## Overall (all points)")
    lines.append("")
    lines.append("| Arm | Method | n | Bias (cm) | MAE (cm) |")
    lines.append("|---|---|---|---|---|")
    for arm in ARMS:
        arm_rows = [r for r in rows if r["arm"] == arm]
        if not arm_rows:
            continue
        for method, key in (("ENS-EB", "err_ens_eb_cm"), ("Kuchera", "err_kuchera_cm")):
            stats = aggregate_errors(arm_rows, key)
            lines.append(
                f"| {arm} | {method} | {stats['n']} | {_fmt(stats['bias_cm'])} | {_fmt(stats['mae_cm'])} |"
            )
    lines.append("")

    lines.append("## By point role (ECMWF arm)")
    lines.append("")
    lines.append("| Role | Method | n | Bias (cm) | MAE (cm) |")
    lines.append("|---|---|---|---|---|")
    for role in ROLES:
        role_rows = [r for r in rows if r["arm"] == "ecmwf" and r["role"] == role]
        if not role_rows:
            continue
        for method, key in (("ENS-EB", "err_ens_eb_cm"), ("Kuchera", "err_kuchera_cm")):
            stats = aggregate_errors(role_rows, key)
            lines.append(
                f"| {role} | {method} | {stats['n']} | {_fmt(stats['bias_cm'])} | {_fmt(stats['mae_cm'])} |"
            )
    lines.append("")

    lines.append("## By resort (ECMWF arm, mid-mountain point)")
    lines.append("")
    lines.append("| Resort | Days | ENS-EB bias | ENS-EB MAE | Kuchera bias | Kuchera MAE |")
    lines.append("|---|---|---|---|---|---|")
    headline = [r for r in rows if r["arm"] == "ecmwf" and r["role"] == "mid_mountain"]
    by_resort: dict[str, list[dict]] = defaultdict(list)
    for row in headline:
        by_resort[row["resort_slug"]].append(row)
    for slug in sorted(by_resort, key=lambda s: by_resort[s][0]["resort_name"]):
        group = by_resort[slug]
        ens = aggregate_errors(group, "err_ens_eb_cm")
        kuc = aggregate_errors(group, "err_kuchera_cm")
        lines.append(
            f"| {group[0]['resort_name']} | {ens['n']} | {_fmt(ens['bias_cm'])} | "
            f"{_fmt(ens['mae_cm'])} | {_fmt(kuc['bias_cm'])} | {_fmt(kuc['mae_cm'])} |"
        )
    lines.append("")

    lines.append("## Detail — matched resort-days (ECMWF arm)")
    lines.append("")
    lines.append(
        "| Window end | Resort | Reported (cm) | ENS-EB base/mid/summit | Kuchera base/mid/summit |"
    )
    lines.append("|---|---|---|---|---|")
    detail: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    for row in rows:
        if row["arm"] == "ecmwf":
            detail[(row["window_end_local_date"], row["resort_slug"])][row["role"]] = row
    for (date, slug) in sorted(detail):
        roles = detail[(date, slug)]
        sample = next(iter(roles.values()))
        rep = sample["reported_cm"]
        rep_range = ""
        if sample["reported_min_cm"] is not None and sample["reported_max_cm"] is not None:
            rep_range = f" ({_fmt(sample['reported_min_cm'])}–{_fmt(sample['reported_max_cm'])})"
        ens = "/".join(_fmt(roles[r]["ens_eb_cm"]) if r in roles else "-" for r in ROLES)
        kuc = "/".join(_fmt(roles[r]["kuchera_cm"]) if r in roles else "-" for r in ROLES)
        lines.append(
            f"| {date} | {sample['resort_name']} | {_fmt(rep)}{rep_range} | {ens} | {kuc} |"
        )
    lines.append("")

    if unmatched:
        lines.append("## Unmatched reports")
        lines.append("")
        lines.append("| Window end | Resort | Reason | Operating status |")
        lines.append("|---|---|---|---|")
        for entry in unmatched:
            lines.append(
                f"| {entry['window_end_local_date']} | {entry['resort_name']} | "
                f"{entry['reason']} | {entry['operating_status']} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print", action="store_true", help="also print the report")
    args = parser.parse_args()

    rows, unmatched = build_comparisons()
    comparisons_path = REPORTS_DIR / "comparisons.jsonl"
    with comparisons_path.open("w") as out:
        for row in rows:
            out.write(json.dumps(row) + "\n")

    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    markdown = render_markdown(rows, unmatched, run_date)
    report_path = REPORTS_DIR / f"verification-{run_date}.md"
    report_path.write_text(markdown)
    print(f"wrote {len(rows)} comparison rows to {comparisons_path}")
    print(f"wrote report to {report_path} (unmatched reports: {len(unmatched)})")
    if args.print:
        print()
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
