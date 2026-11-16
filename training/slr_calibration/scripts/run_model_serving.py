"""One-cycle model-serving test run (design: model-serving-design.md).

Fetches the latest complete 00/12Z cycle per arm (ECMWF IFS open data,
HRRR on AWS), builds the champion's 64 features at the catalogue resort
points for the 24 h windows ending 07:00 local, scores the packaged v3
champion, and publishes:

* append-only records to the tracking store (data/tracking/model-serving/)
* serving rows as a static JSON the frontend imports
  (src/data/model-forecasts.json -- the app has no database; static JSON
  is its data layer in the workspace)
* features / predictions CSVs + a run manifest in the test-run tree

Windows a source cannot cover (HRRR's 48 h horizon vs western 07:00-local
day-2 windows from a 12Z cycle) are recorded as missing, never filled.

ECMWF horizon extension (2026-10-31, user decision: cover all days of
ECMWF data): the ECMWF arm scores leads 1-10 against the full step-240
open-data ceiling (steps 0-144 3-hourly, 150-240 6-hourly), gated by
scripts/audit_serving_sources_extended.py (phase0_audit_extended.json:
no parameter/level gaps at any step; accumulation semantics unchanged).
Per-point ceilings: lead 9 for America/Los_Angeles + America/Denver
points, lead 10 for America/New_York; beyond-ceiling windows are
recorded missing like HRRR's.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from slr_calibration.champion import ChampionModel
from slr_calibration.gold_profiles import PRESSURE_LEVELS_HPA
from slr_calibration.model_serving import (
    FEATURE_BUILDER_VERSION,
    SOURCE_ID_ECMWF,
    SOURCE_ID_HRRR,
    assert_frontend_within_lead_cap,
    apply_isotonic_slr_layer_gated,
    build_point_window_features,
    core_inputs_finite,
    expand_hourly,
    fetch_ranges,
    grib_latlons,
    grib_regular_geometry,
    grib_values,
    hourly_amounts,
    http_get,
    hrrr_cell_distances_km,
    hrrr_cell_indices,
    hrrr_domain_exclusion,
    interp_level_series,
    load_catalogue,
    predict_with_passthrough,
    sample_regular_grid,
    tclw_from_hydrometeors,
    window_ends,
)

ECMWF_PL_PARAMS = ["t", "q", "r", "u", "v", "w", "gh"]
ECMWF_PL_LEVELS = [1000, 925, 850, 700, 600, 500]
ECMWF_SFC_PARAMS = ["2t", "2d", "10u", "10v", "skt", "tp", "sf", "cp", "tclw"]
HRRR_PL_FIELDS = ["TMP", "RH", "SPFH", "VVEL", "UGRD", "VGRD", "HGT", "CLMR", "RWMR"]
NAN_POLICY = "pass-through (design S5; validated 2026-10-31, nan_validation.json)"


# ---------------------------------------------------------------------------
# ECMWF arm
# ---------------------------------------------------------------------------


def ecmwf_index(date: str, cycle: int, step: int) -> tuple[str, list[dict]]:
    base = f"https://data.ecmwf.int/forecasts/{date}/{cycle:02d}z/ifs/0p25/oper"
    name = f"{date}{cycle:02d}0000-{step}h-oper-fc"
    text = http_get(f"{base}/{name}.index").decode()
    entries = [json.loads(line) for line in text.splitlines() if line.strip().startswith("{")]
    return f"{base}/{name}.grib2", entries


def ecmwf_steps(max_step: int) -> list[int]:
    """IFS oper open-data step schedule for 00/12Z cycles: 3-hourly to
    step 144, then 6-hourly to the step-240 open-data ceiling (verified
    per-step against the live .index catalogue by
    audit_serving_sources_extended.py, phase0_audit_extended.json)."""
    steps = list(range(0, min(max_step, 144) + 1, 3))
    if max_step > 144:
        steps += list(range(150, max_step + 1, 6))
    return steps


def build_ecmwf_series(points: list[dict], date: str, cycle: int, max_step: int = 240):
    """Returns (per-point series dict, stamp t2m per point for cross-checks)."""
    cycle_dt = datetime.strptime(date, "%Y%m%d").replace(hour=cycle, tzinfo=timezone.utc)
    steps = ecmwf_steps(max_step)
    stamps = [cycle_dt + timedelta(hours=s) for s in steps]
    # per point per stamp raw extracts
    raw: list[dict[str, Any]] = [dict() for _ in points]  # unused placeholder shape
    per_point_stamps: list[list[dict[str, Any]]] = [[] for _ in points]
    t2m_check: dict[str, dict[datetime, float]] = {p["point_id"]: {} for p in points}
    geometry = None
    for step, stamp in zip(steps, stamps):
        url, entries = ecmwf_index(date, cycle, step)
        wanted: dict[tuple[str, str, int | None], tuple[int, int]] = {}
        for e in entries:
            param, levtype = e["param"], e["levtype"]
            if levtype == "pl" and param in ECMWF_PL_PARAMS and int(e["levelist"]) in ECMWF_PL_LEVELS:
                wanted[(param, "pl", int(e["levelist"]))] = (int(e["_offset"]), int(e["_length"]))
            elif levtype == "sfc" and param in ECMWF_SFC_PARAMS:
                wanted[(param, "sfc", None)] = (int(e["_offset"]), int(e["_length"]))
        payloads = fetch_ranges(url, list(wanted.values()))
        sampled: dict[tuple[str, str, int | None], list[float]] = {}
        for key, (off, _length) in wanted.items():
            data = payloads[off]
            if geometry is None:
                geometry = grib_regular_geometry(data)
            values = grib_values(data)
            sampled[key] = [
                sample_regular_grid(values, int(geometry["ni"]), int(geometry["nj"]),
                                    geometry["lat_first"], geometry["lon_first"],
                                    geometry["di"], geometry["dj"],
                                    p["latitude"], p["longitude"])
                for p in points
            ]
        for i, point in enumerate(points):
            def src(param: str) -> dict[int, float]:
                return {lvl: sampled[(param, "pl", lvl)][i] for lvl in ECMWF_PL_LEVELS}

            t13 = interp_level_series({k: v for k, v in src("t").items()})
            r13 = interp_level_series(src("r"))
            u13 = interp_level_series(src("u"))
            v13 = interp_level_series(src("v"))
            gh13 = interp_level_series(src("gh"))
            q13 = interp_level_series(src("q"))
            w13 = interp_level_series(src("w"))
            record = {
                "levels": [
                    (p, t13[p] - 273.15, r13[p], math.hypot(u13[p], v13[p]), gh13[p])
                    for p in PRESSURE_LEVELS_HPA
                ],
                "temperature2mC": sampled[("2t", "sfc", None)][i] - 273.15,
                "precipitationMm": 0.0,
                "snowfallCm": 0.0,
            }
            per_point_stamps[i].append({
                "record": record,
                "w": w13,
                "q": q13,
                "sfc": {
                    "dewpoint_2m": sampled[("2d", "sfc", None)][i],
                    "total_column_liquid_water": sampled[("tclw", "sfc", None)][i],
                    "wind_u_10m": sampled[("10u", "sfc", None)][i],
                    "wind_v_10m": sampled[("10v", "sfc", None)][i],
                    "skin_temperature": sampled[("skt", "sfc", None)][i],
                },
                "tp_acc_mm": sampled[("tp", "sfc", None)][i] * 1000.0,
                "cp_acc_mm": sampled[("cp", "sfc", None)][i] * 1000.0,
                "sf_acc_mm": sampled[("sf", "sfc", None)][i] * 1000.0,
            })
            t2m_check[point["point_id"]][stamp] = record["temperature2mC"]
        print(f"ecmwf step {step}: {len(wanted)} messages", flush=True)

    series_by_point: dict[str, dict[str, Any]] = {}
    for i, point in enumerate(points):
        rows = per_point_stamps[i]
        profile_hours = expand_hourly(stamps, [r["record"] for r in rows])
        w_series = expand_hourly(stamps, [r["w"] for r in rows])
        q_series = expand_hourly(stamps, [r["q"] for r in rows])
        sfc_series = expand_hourly(stamps, [r["sfc"] for r in rows])
        tp_acc = expand_hourly(stamps, [r["tp_acc_mm"] for r in rows])
        cp_acc = expand_hourly(stamps, [r["cp_acc_mm"] for r in rows])
        sf_acc = expand_hourly(stamps, [r["sf_acc_mm"] for r in rows])
        tp_h = hourly_amounts(tp_acc)
        cp_h = hourly_amounts(cp_acc)
        sf_h = hourly_amounts(sf_acc)
        accumu = {
            h: {"tp_mm": tp_h[h], "cp_mm": cp_h[h], "lsp_mm": max(tp_h[h] - cp_h[h], 0.0)}
            for h in tp_h
        }
        for h, record in profile_hours.items():
            record["precipitationMm"] = tp_h.get(h, 0.0)
            record["snowfallCm"] = sf_h.get(h, 0.0)  # mm water at nominal 10:1 -> cm
        series_by_point[point["point_id"]] = {
            "profile_hours": profile_hours, "w": w_series, "q": q_series,
            "accumu": accumu, "sfc": sfc_series,
            "first": stamps[0], "last": stamps[-1],
        }
    return series_by_point, t2m_check


# ---------------------------------------------------------------------------
# HRRR arm
# ---------------------------------------------------------------------------


def hrrr_idx(date: str, cycle: int, kind: str, fxx: int) -> tuple[str, list[dict]]:
    url = (f"https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{date}/conus/"
           f"hrrr.t{cycle:02d}z.{kind}f{fxx:02d}.grib2")
    text = http_get(url + ".idx").decode()
    rows = []
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) >= 6 and parts[0].isdigit():
            rows.append({"offset": int(parts[1]), "field": parts[3], "level": parts[4], "span": parts[5]})
    # lengths from next offset
    total_order = sorted(rows, key=lambda r: r["offset"])
    for j, row in enumerate(total_order):
        row["length"] = (total_order[j + 1]["offset"] - row["offset"]) if j + 1 < len(total_order) else 0
    return url, rows


def build_hrrr_series(points: list[dict], date: str, cycle: int, max_step: int = 48):
    cycle_dt = datetime.strptime(date, "%Y%m%d").replace(hour=cycle, tzinfo=timezone.utc)
    stamps = [cycle_dt + timedelta(hours=s) for s in range(0, max_step + 1)]
    # geometry + cells from the first wrfprs message
    url0, rows0 = hrrr_idx(date, cycle, "wrfprs", 0)
    first = sorted(rows0, key=lambda r: r["offset"])[0]
    first_bytes = fetch_ranges(url0, [(first["offset"], first["length"])])[first["offset"]]
    lats, lons = grib_latlons(first_bytes)
    cells = hrrr_cell_indices(lats, lons, points)
    # HRRR covers CONUS only; points it does not serve must not silently
    # inherit border-cell data. hrrr_domain_exclusion separates the two
    # cases: non-US points are excluded by serving policy (the grid's
    # footprint overlaps southern Canada, so nearby cells can exist), and
    # US points implausibly far from any cell centre are excluded
    # geometrically (Alaska).
    cell_distances = hrrr_cell_distances_km(lats, lons, points)
    domain_excluded = {}
    for point, dist in zip(points, cell_distances):
        reason = hrrr_domain_exclusion(
            point.get("country", "United States"), dist)
        if reason is not None:
            domain_excluded[point["point_id"]] = reason

    per_point_stamps: list[list[dict[str, Any]]] = [[] for _ in points]
    t2m_check: dict[str, dict[datetime, float]] = {p["point_id"]: {} for p in points}
    era5_level_strs = {f"{lvl} mb": lvl for lvl in PRESSURE_LEVELS_HPA}
    for step, stamp in enumerate(stamps):
        extracted: dict[tuple[str, str], list[float]] = {}
        for kind in ("wrfprs", "wrfsfc"):
            url, rows = hrrr_idx(date, cycle, kind, step)
            wanted: dict[tuple[str, str], tuple[int, int]] = {}
            for row in rows:
                if row["length"] == 0:
                    continue
                key = (row["field"], row["level"])
                if kind == "wrfprs" and row["field"] in HRRR_PL_FIELDS and row["level"] in era5_level_strs:
                    wanted[key] = (row["offset"], row["length"])
                elif kind == "wrfsfc" and (
                    key in (
                        ("TMP", "2 m above ground"), ("DPT", "2 m above ground"),
                        ("UGRD", "10 m above ground"), ("VGRD", "10 m above ground"),
                        ("TMP", "surface"),
                    )
                    # APCP/ASNOW appear twice per file: the cycle
                    # accumulation ("0-N hour acc") and a 1-hour
                    # accumulation ("N-1-N hour acc"). Only the cycle
                    # accumulation may be differenced into hourly amounts.
                    or (key in (("APCP", "surface"), ("ASNOW", "surface"))
                        and row["span"].startswith("0-"))
                ):
                    wanted[key] = (row["offset"], row["length"])
            payloads = fetch_ranges(url, list(wanted.values()))
            for key, (off, _length) in wanted.items():
                values = grib_values(payloads[off])
                extracted[key] = [float(values[c]) for c in cells]
        for i, point in enumerate(points):
            def pl(field: str) -> dict[int, float]:
                return {lvl: extracted[(field, s)][i] for s, lvl in era5_level_strs.items()}

            t = pl("TMP"); rh = pl("RH"); u = pl("UGRD"); v = pl("VGRD"); z = pl("HGT")
            clmr = pl("CLMR"); rwmr = pl("RWMR")
            levels_desc = sorted(PRESSURE_LEVELS_HPA, reverse=True)
            tclw = tclw_from_hydrometeors(
                levels_desc,
                [clmr[p] + rwmr[p] for p in levels_desc],
                [z[p] for p in levels_desc],
                point["elevation_m"],
            )
            record = {
                "levels": [
                    (p, t[p] - 273.15, rh[p], math.hypot(u[p], v[p]), z[p])
                    for p in PRESSURE_LEVELS_HPA
                ],
                "temperature2mC": extracted[("TMP", "2 m above ground")][i] - 273.15,
                "precipitationMm": 0.0,
                "snowfallCm": 0.0,
            }
            per_point_stamps[i].append({
                "record": record,
                "w": pl("VVEL"),
                "q": pl("SPFH"),
                "sfc": {
                    "dewpoint_2m": extracted[("DPT", "2 m above ground")][i],
                    "total_column_liquid_water": tclw,
                    "wind_u_10m": extracted[("UGRD", "10 m above ground")][i],
                    "wind_v_10m": extracted[("VGRD", "10 m above ground")][i],
                    "skin_temperature": extracted[("TMP", "surface")][i],
                },
                "apcp_acc_mm": extracted[("APCP", "surface")][i],
                "asnow_acc_m": extracted[("ASNOW", "surface")][i],
            })
            t2m_check[point["point_id"]][stamp] = record["temperature2mC"]
        print(f"hrrr step {step}: extracted", flush=True)

    series_by_point: dict[str, dict[str, Any]] = {}
    for i, point in enumerate(points):
        if point["point_id"] in domain_excluded:
            series_by_point[point["point_id"]] = {
                "domain_missing_reason": domain_excluded[point["point_id"]],
                "profile_hours": {}, "w": {}, "q": {}, "accumu": {},
                "sfc": {}, "first": stamps[0], "last": stamps[-1],
            }
            t2m_check[point["point_id"]] = {}
            continue
        rows = per_point_stamps[i]
        profile_hours = {s: r["record"] for s, r in zip(stamps, rows)}
        apcp_acc = {s: r["apcp_acc_mm"] for s, r in zip(stamps, rows)}
        asnow_acc = {s: r["asnow_acc_m"] for s, r in zip(stamps, rows)}
        tp_h = hourly_amounts(apcp_acc)
        snow_h = hourly_amounts(asnow_acc)
        # accumulation resets would show as negative diffs; clip to the raw
        # accumulated value in that case (documented in the run manifest).
        tp_h = {h: (v if v >= 0 else apcp_acc[h]) for h, v in tp_h.items()}
        snow_h = {h: (v if v >= 0 else asnow_acc[h]) for h, v in snow_h.items()}
        accumu = {h: {"tp_mm": tp_h[h], "cp_mm": None, "lsp_mm": None} for h in stamps}
        for h, record in profile_hours.items():
            record["precipitationMm"] = tp_h.get(h, 0.0)
            record["snowfallCm"] = snow_h.get(h, 0.0) * 100.0
        series_by_point[point["point_id"]] = {
            "profile_hours": profile_hours,
            "w": {s: r["w"] for s, r in zip(stamps, rows)},
            "q": {s: r["q"] for s, r in zip(stamps, rows)},
            "accumu": accumu,
            "sfc": {s: r["sfc"] for s, r in zip(stamps, rows)},
            "first": stamps[0], "last": stamps[-1],
        }
    return series_by_point, t2m_check


# ---------------------------------------------------------------------------
# Scoring + publishing
# ---------------------------------------------------------------------------


def score_arm(arm: str, source_id: str, leads: list[int], cycle_dt: datetime,
              points: list[dict], series_by_point: dict, model: ChampionModel,
              sidecar_features: list[str]) -> tuple[list[dict], list[dict]]:
    records: list[dict] = []
    feature_rows: list[dict] = []
    pending: list[tuple[dict, dict]] = []  # (record, feature row)
    for point in points:
        series = series_by_point[point["point_id"]]
        for lead, end in zip(leads, window_ends(cycle_dt, point["timezone"], leads)):
            start = end - timedelta(hours=24)
            record: dict[str, Any] = {
                "source": arm, "source_id": source_id,
                "cycle_utc": cycle_dt.isoformat().replace("+00:00", "Z"),
                "point_id": point["point_id"], "resort_slug": point["resort_slug"],
                "resort_name": point["resort_name"], "point_name": point["point_name"],
                "role": point["role"], "region": point["region"],
                "latitude": point["latitude"], "longitude": point["longitude"],
                "elevation_m": point["elevation_m"], "timezone": point["timezone"],
                "geometry_source": point["geometry_source"],
                "lead_day": lead,
                "window_start_utc": start.isoformat().replace("+00:00", "Z"),
                "window_end_utc": end.isoformat().replace("+00:00", "Z"),
                "window_end_local_date": end.astimezone(
                    __import__("zoneinfo").ZoneInfo(point["timezone"])).date().isoformat(),
                "model_version": "ens-eb-v3-champion",
                "feature_builder_version": FEATURE_BUILDER_VERSION,
                "nan_policy": NAN_POLICY,
                "missing": False,
            }
            first_needed = start + timedelta(hours=1)
            if series.get("domain_missing_reason"):
                record["missing"] = True
                record["missing_reason"] = series["domain_missing_reason"]
                records.append(record)
                continue
            if first_needed < series["first"] or end > series["last"]:
                record["missing"] = True
                record["missing_reason"] = (
                    f"window {first_needed.isoformat()}..{end.isoformat()} outside "
                    f"source horizon {series['first'].isoformat()}..{series['last'].isoformat()}"
                )
                records.append(record)
                continue
            row = build_point_window_features(
                point, end, series["profile_hours"], series["w"], series["q"],
                series["accumu"], series["sfc"], source_id)
            feature_rows.append({"point_id": point["point_id"], "source": arm,
                                 "lead_day": lead, **{f: row.get(f) for f in sidecar_features},
                                 "baseline_slr": row.get("baseline_slr"),
                                 "profile_status": row.get("profile_status"),
                                 "valid_hours": row.get("valid_hours")})
            record["liquid_mm"] = row.get("fcst_liquid_mm")
            record["kuchera_slr"] = row.get("baseline_slr")
            record["kuchera_snowfall_cm"] = (
                row["baseline_slr"] * row["fcst_liquid_mm"] / 10.0
                if row.get("baseline_slr") is not None and row.get("fcst_liquid_mm") is not None
                else None
            )
            record["source_resolved_counts"] = row.get("source_resolved_counts")
            if not core_inputs_finite(row, sidecar_features):
                record["missing"] = True
                record["missing_reason"] = "core inputs (liquid / AGL temperatures) not finite"
                records.append(record)
                continue
            pending.append((record, row))
            records.append(record)
    if pending:
        frame = pd.DataFrame([{f: row.get(f) for f in sidecar_features} | {"region": rec["region"]}
                              for rec, row in pending])
        preds = predict_with_passthrough(model, frame)
        for (record, _row), pred in zip(pending, preds):
            record["model_slr"] = float(pred)
            record["model_snowfall_cm"] = float(pred) * float(record["liquid_mm"]) / 10.0
    return records, feature_rows


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", required=True)
    ap.add_argument("--champion-dir", required=True)
    ap.add_argument("--date", default="20261031")
    ap.add_argument("--cycle", type=int, default=12)
    ap.add_argument("--sources", default="ecmwf,hrrr")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tracking-records", required=True)
    ap.add_argument("--frontend-json", required=True)
    ap.add_argument(
        "--no-isotonic-layer", action="store_false", dest="isotonic_layer",
        default=True,
        help="skip the isotonic SLR elevation-ordering serving layer "
             "(applied by default since Registration v2 judged it READY "
             "TO APPLY / ENABLE and the user directed applying it, "
             "2026-11-10; pass this flag to emit the raw frontend)")
    return ap


def main() -> int:
    args = build_arg_parser().parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    points = load_catalogue(Path(args.registry))
    champion_dir = Path(args.champion_dir)
    sidecar = json.loads((champion_dir / "champion_sidecar.json").read_text())
    sidecar_features: list[str] = sidecar["features"]
    model = ChampionModel.load(champion_dir)
    cycle_dt = datetime.strptime(args.date, "%Y%m%d").replace(hour=args.cycle, tzinfo=timezone.utc)

    all_records: list[dict] = []
    all_features: list[dict] = []
    manifest: dict[str, Any] = {
        "run_started_utc": datetime.now(timezone.utc).isoformat(),
        "cycle": {"date": args.date, "hour_z": args.cycle},
        "points": len(points),
        "nan_policy": NAN_POLICY,
        "arms": {},
    }
    t2m_by_arm: dict[str, dict] = {}

    if "ecmwf" in args.sources:
        series, t2m = build_ecmwf_series(points, args.date, args.cycle)
        t2m_by_arm["ecmwf"] = t2m
        # Leads 1-10 requested for every point; each point's coverable
        # ceiling falls out of the 240 h open-data horizon inside
        # score_arm (window end beyond the series -> recorded missing):
        # 9 for America/Los_Angeles and America/Denver points, 10 for
        # America/New_York (phase0_audit_extended.json).
        recs, feats = score_arm("ecmwf", SOURCE_ID_ECMWF, list(range(1, 11)), cycle_dt,
                                points, series, model, sidecar_features)
        all_records += recs; all_features += feats
        manifest["arms"]["ecmwf"] = {"records": len(recs),
                                     "predicted": sum(1 for r in recs if not r["missing"]),
                                     "leads_requested": list(range(1, 11)),
                                     "step_schedule": "0-144 by 3, 150-240 by 6",
                                     "max_step": 240}
    if "hrrr" in args.sources:
        series, t2m = build_hrrr_series(points, args.date, args.cycle)
        t2m_by_arm["hrrr"] = t2m
        recs, feats = score_arm("hrrr", SOURCE_ID_HRRR, [1, 2], cycle_dt,
                                points, series, model, sidecar_features)
        all_records += recs; all_features += feats
        manifest["arms"]["hrrr"] = {
            "records": len(recs),
            "predicted": sum(1 for r in recs if not r["missing"]),
            "domain_excluded_points": sorted(
                pid for pid, srs in series.items()
                if srs.get("domain_missing_reason")),
        }

    if len(t2m_by_arm) == 2:
        diffs = []
        for pid, hours in t2m_by_arm["ecmwf"].items():
            for stamp, val in hours.items():
                other = t2m_by_arm["hrrr"].get(pid, {}).get(stamp)
                if other is not None:
                    diffs.append(abs(val - other))
        manifest["cross_check_t2m_ecmwf_vs_hrrr"] = {
            "pairs": len(diffs),
            "mean_abs_diff_c": float(np.mean(diffs)) if diffs else None,
            "max_abs_diff_c": float(np.max(diffs)) if diffs else None,
        }

    # CSVs
    if all_features:
        with open(out_dir / "features.csv", "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(all_features[0].keys()))
            writer.writeheader(); writer.writerows(all_features)
    with open(out_dir / "predictions.csv", "w", newline="") as fh:
        keys = ["source", "point_id", "resort_name", "point_name", "role", "lead_day",
                "window_end_local_date", "window_start_utc", "window_end_utc",
                "model_slr", "model_snowfall_cm", "kuchera_slr", "kuchera_snowfall_cm",
                "liquid_mm", "missing", "missing_reason"]
        writer = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        writer.writeheader(); writer.writerows(all_records)

    # Tracking records (append-only)
    tracking_path = Path(args.tracking_records)
    tracking_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tracking_path, "a") as fh:
        for rec in all_records:
            fh.write(json.dumps(rec) + "\n")

    # Frontend serving JSON
    frontend = {
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "modelVersion": "ENS-EB v3 champion (model-v3, 64 features)",
        "modelLabel": "Model forecast",
        "featureBuilderVersion": FEATURE_BUILDER_VERSION,
        "nanPolicy": NAN_POLICY,
        "cycles": {arm: cycle_dt.isoformat().replace("+00:00", "Z")
                   for arm in manifest["arms"]},
        "points": [],
    }
    for point in points:
        entry = {
            "pointId": point["point_id"], "resortSlug": point["resort_slug"],
            "resortName": point["resort_name"], "pointName": point["point_name"],
            "role": point["role"], "latitude": point["latitude"],
            "longitude": point["longitude"], "elevationM": point["elevation_m"],
            "timezone": point["timezone"], "forecasts": {},
        }
        for arm in ("ecmwf", "hrrr"):
            rows = [r for r in all_records
                    if r["point_id"] == point["point_id"] and r["source"] == arm and not r["missing"]]
            if rows:
                entry["forecasts"][arm] = [{
                    "leadDay": r["lead_day"],
                    "windowStartUtc": r["window_start_utc"],
                    "windowEndUtc": r["window_end_utc"],
                    "windowEndLocalDate": r["window_end_local_date"],
                    "modelSlr": r.get("model_slr"),
                    "modelSnowfallCm": r.get("model_snowfall_cm"),
                    "kucheraSlr": r.get("kuchera_slr"),
                    "kucheraSnowfallCm": r.get("kuchera_snowfall_cm"),
                    "liquidMm": r.get("liquid_mm"),
                    "cycleUtc": r["cycle_utc"],
                } for r in rows]
        frontend["points"].append(entry)
    # 10-day cap on all surfaces (user decision 2026-11-10): a serving
    # frontend never carries a record beyond MAX_SERVED_LEAD_DAY.
    assert_frontend_within_lead_cap(frontend)
    # Isotonic SLR layer: applied by default (Registration v2 READY;
    # user decision 2026-11-10); --no-isotonic-layer opts out (see
    # apply_isotonic_slr_layer_gated). The manifest records the layer
    # state either way.
    frontend, isotonic_report = apply_isotonic_slr_layer_gated(
        frontend, enabled=args.isotonic_layer)
    Path(args.frontend_json).write_text(json.dumps(frontend, indent=1))

    manifest["records_total"] = len(all_records)
    manifest["isotonic_layer"] = isotonic_report
    manifest["run_finished_utc"] = datetime.now(timezone.utc).isoformat()
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    print(json.dumps(manifest, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
