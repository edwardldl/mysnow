"""Open-Meteo long-range serving arm (third source) for the v3 champion.

User decision 2026-11-01: run the model on Open-Meteo data anyway for the
ECMWF tail the GRIB open data cannot cover (step-240 ceiling: lead 9 for
America/Los_Angeles + America/Denver points, lead 10 for
America/New_York), under the third-source treatment from the E1 parity
report -- NOT as a continuation of the GRIB ECMWF arm:

* source id `ecmwf_openmeteo`, display name "ECMWF IFS via Open-Meteo";
* windows are anchored on the GRIB serving batch's cycle (window-end
  local dates define the windows). Open-Meteo publishes no cycle
  metadata and the E1 parity check could not cycle-verify its forecast
  instance against any obtainable GRIB cycle, so records carry the
  anchor cycle + the retrieval timestamp, labelled as such;
* reduced-feature variant: total column liquid water is not exposed by
  Open-Meteo under any name, so `tclw_pwt` / `tclw_max` are NaN on every
  record of this arm under the validated pass-through (design S5), and
  every record carries `reducedFeatureVariant: true`.

The adapter is the E1-validated one (data/.../model-serving-test-run/
e1_parity.py), unchanged in mechanics:

* pressure levels sampled on the same six levels as the GRIB adapter
  (1000/925/850/700/600/500 hPa), normalised onto the 13 ERA5 levels by
  the same `interp_level_series`;
* `vertical_velocity` is served in m/s -> omega [Pa/s] = -rho*g*w with
  rho = p/(R_d*T) from the same level's temperature and pressure;
* `wind_speed_unit=ms` requested; 10 m wind decomposed from served speed
  + direction so hypot(u, v) reproduces the served speed exactly;
* q -> NaN (feeds only dropped bundles; E1 proved rebuilding all 90
  GRIB windows with q := NaN changes none of the 64 features);
* cp/lsp -> None (dropped regime bundle only);
* Open-Meteo `snowfall` is used ONLY as compute_baseline_slr's >0 snow
  gate (e1_kuchera_fix.py: toggling it changes none of the 64 features);
  it is never a liquid input -- precipitation remains the liquid.

Only tail windows are produced: per point, leads (GRIB ceiling + 1)..14,
the ceiling read from the serving JSON being extended. Existing arms'
records are never rewritten: the tracking store gains a new append-only
file, and the frontend JSON merge is verified to leave every
pre-existing row deep-equal.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "training/slr_calibration/src"))
sys.path.insert(0, str(REPO / "training/slr_calibration/scripts"))

from slr_calibration.champion import ChampionModel  # noqa: E402
from slr_calibration.gold_profiles import PRESSURE_LEVELS_HPA  # noqa: E402
from slr_calibration.model_serving import (  # noqa: E402
    FEATURE_BUILDER_VERSION,
    interp_level_series,
    load_catalogue,
)
from run_model_serving import NAN_POLICY, score_arm  # noqa: E402

ARM = "ecmwf_openmeteo"
SOURCE_ID_OPENMETEO = "open-meteo-ecmwf-ifs025"
SOURCE_DISPLAY_NAME = "ECMWF IFS via Open-Meteo"
MAX_LEAD = 14
OM_LEVELS = [1000, 925, 850, 700, 600, 500]
RD = 287.05
G = 9.80665
CYCLE_BASIS = (
    "anchor cycle of the GRIB serving batch; Open-Meteo publishes no "
    "cycle metadata and its forecast instance is not cycle-verified "
    "against the GRIB arm (E1 parity check, e1-parity.md)"
)


# ---------------------------------------------------------------------------
# Open-Meteo adapter (to the model_serving series contract) -- from E1
# ---------------------------------------------------------------------------


def build_om_series(points: list[dict], raw_path: Path) -> tuple[dict, dict]:
    data = json.loads(raw_path.read_text())
    if isinstance(data, dict):
        data = [data]
    assert len(data) == len(points), (len(data), len(points))
    series_by_point: dict[str, dict] = {}
    t2m_check: dict[str, dict[datetime, float]] = {}
    snap_notes = []
    for point, loc in zip(points, data):
        snap_notes.append(abs(loc["latitude"] - point["latitude"]) + abs(loc["longitude"] - point["longitude"]))
        hourly = loc["hourly"]
        times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in hourly["time"]]
        idx = {t: i for i, t in enumerate(times)}

        def col(name: str, t: datetime) -> float:
            v = hourly[name][idx[t]]
            return float(v) if v is not None else float("nan")

        profile_hours, w_series, q_series, accumu, sfc = {}, {}, {}, {}, {}
        for t in times:
            t6 = {lvl: col(f"temperature_{lvl}hPa", t) for lvl in OM_LEVELS}
            r6 = {lvl: col(f"relative_humidity_{lvl}hPa", t) for lvl in OM_LEVELS}
            ws6 = {lvl: col(f"wind_speed_{lvl}hPa", t) for lvl in OM_LEVELS}
            gh6 = {lvl: col(f"geopotential_height_{lvl}hPa", t) for lvl in OM_LEVELS}
            vv6 = {lvl: col(f"vertical_velocity_{lvl}hPa", t) for lvl in OM_LEVELS}
            om6 = {}
            for lvl in OM_LEVELS:
                rho = (lvl * 100.0) / (RD * (t6[lvl] + 273.15))
                om6[lvl] = -rho * G * vv6[lvl]
            t13 = interp_level_series(t6)
            r13 = interp_level_series(r6)
            ws13 = interp_level_series(ws6)
            gh13 = interp_level_series(gh6)
            w13 = interp_level_series(om6)
            t2m = col("temperature_2m", t)
            precip = col("precipitation", t)
            snowfall = hourly["snowfall"][idx[t]]
            ws10 = col("wind_speed_10m", t)
            wd10 = col("wind_direction_10m", t)
            u10 = -ws10 * math.sin(math.radians(wd10))
            v10 = -ws10 * math.cos(math.radians(wd10))
            profile_hours[t] = {
                "levels": [(p, t13[p], r13[p], ws13[p], gh13[p]) for p in PRESSURE_LEVELS_HPA],
                "temperature2mC": t2m,
                "precipitationMm": precip,
                # Gate-only for compute_baseline_slr (e1_kuchera_fix.py);
                # never consumed by the 64 model features.
                "snowfallCm": float(snowfall) if snowfall is not None else 0.0,
            }
            w_series[t] = w13
            q_series[t] = {p: float("nan") for p in PRESSURE_LEVELS_HPA}
            accumu[t] = {"tp_mm": precip, "cp_mm": None, "lsp_mm": None}
            sfc[t] = {
                "dewpoint_2m": col("dew_point_2m", t) + 273.15,
                "total_column_liquid_water": None,
                "wind_u_10m": u10,
                "wind_v_10m": v10,
                "skin_temperature": col("skin_temperature", t) + 273.15,
            }
            t2m_check.setdefault(point["point_id"], {})[t] = t2m
        series_by_point[point["point_id"]] = {
            "profile_hours": profile_hours, "w": w_series, "q": q_series,
            "accumu": accumu, "sfc": sfc,
            "first": times[0], "last": times[-1],
        }
    print(f"OM grid snap: max |dlat|+|dlon| = {max(snap_notes):.4f} deg", flush=True)
    return series_by_point, t2m_check


# ---------------------------------------------------------------------------
# Build + publish
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=str(REPO / "data/tracking/registry.json"))
    ap.add_argument("--champion-dir", default=str(
        REPO / "data/slr-training-data/screened-v2/model-v1/model-v3/champion"))
    ap.add_argument("--date", default="20261031")
    ap.add_argument("--cycle", type=int, default=12)
    ap.add_argument("--raw", required=True, help="saved Open-Meteo pull (JSON)")
    ap.add_argument("--pull-meta", required=True, help="pull metadata JSON (retrieval time)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tracking-records", required=True)
    ap.add_argument("--frontend-json", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frontend_path = Path(args.frontend_json)
    frontend_before = json.loads(frontend_path.read_text())

    points = load_catalogue(Path(args.registry))
    champion_dir = Path(args.champion_dir)
    sidecar = json.loads((champion_dir / "champion_sidecar.json").read_text())
    sidecar_features: list[str] = sidecar["features"]
    model = ChampionModel.load(champion_dir)
    cycle_dt = datetime.strptime(args.date, "%Y%m%d").replace(hour=args.cycle, tzinfo=timezone.utc)
    anchor_iso = cycle_dt.isoformat().replace("+00:00", "Z")

    pull_meta = json.loads(Path(args.pull_meta).read_text())
    retrieved_at = pull_meta["retrieved_at_utc"]

    # Per-point GRIB ceiling from the serving JSON being extended; the OM
    # arm produces only the windows beyond it, through MAX_LEAD.
    ceilings: dict[str, int] = {}
    for entry in frontend_before["points"]:
        rows = entry["forecasts"].get("ecmwf") or []
        ceilings[entry["pointId"]] = max((r["leadDay"] for r in rows), default=0)
    groups: dict[tuple[int, ...], list[dict]] = {}
    for point in points:
        leads = tuple(range(ceilings[point["point_id"]] + 1, MAX_LEAD + 1))
        if leads:
            groups.setdefault(leads, []).append(point)
    print("tail-lead groups:", {k: len(v) for k, v in groups.items()}, flush=True)

    series_by_point, _t2m = build_om_series(points, Path(args.raw))

    all_records: list[dict] = []
    all_features: list[dict] = []
    for leads, group_points in sorted(groups.items()):
        recs, feats = score_arm(ARM, SOURCE_ID_OPENMETEO, list(leads), cycle_dt,
                                group_points, series_by_point, model, sidecar_features)
        all_records += recs
        all_features += feats
        print(f"leads {leads}: {sum(1 for r in recs if not r['missing'])}/{len(recs)} scored", flush=True)

    for rec in all_records:
        rec["source_display_name"] = SOURCE_DISPLAY_NAME
        rec["retrieved_at_utc"] = retrieved_at
        rec["cycle_basis"] = CYCLE_BASIS
        rec["reducedFeatureVariant"] = True

    # CSVs in the extended tree (arm-suffixed; the GRIB extension outputs
    # beside them are not touched).
    if all_features:
        with open(out_dir / "features_openmeteo.csv", "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(all_features[0].keys()))
            writer.writeheader(); writer.writerows(all_features)
    with open(out_dir / "predictions_openmeteo.csv", "w", newline="") as fh:
        keys = ["source", "source_id", "point_id", "resort_name", "point_name", "role", "lead_day",
                "window_end_local_date", "window_start_utc", "window_end_utc",
                "model_slr", "model_snowfall_cm", "kuchera_slr", "kuchera_snowfall_cm",
                "liquid_mm", "missing", "missing_reason", "retrieved_at_utc", "reducedFeatureVariant"]
        writer = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        writer.writeheader(); writer.writerows(all_records)

    # Tracking records (append-only; a new file for this arm).
    tracking_path = Path(args.tracking_records)
    tracking_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tracking_path, "a") as fh:
        for rec in all_records:
            fh.write(json.dumps(rec) + "\n")

    # Frontend serving JSON: merge the new arm alongside the existing
    # ones. Nothing pre-existing is rewritten (verified below).
    frontend = json.loads(frontend_path.read_text())
    frontend["cycles"][ARM] = anchor_iso
    frontend["openMeteoArm"] = {
        "sourceId": SOURCE_ID_OPENMETEO,
        "displayName": SOURCE_DISPLAY_NAME,
        "anchorCycleUtc": anchor_iso,
        "retrievedAtUtc": retrieved_at,
        "reducedFeatureVariant": True,
        "note": (
            "Third serving source (user decision 2026-11-01): Open-Meteo "
            "ecmwf_ifs025, covering only the windows beyond the GRIB "
            "step-240 ceiling. Open-Meteo publishes no cycle metadata and "
            "the E1 parity check could not cycle-verify its forecast "
            "instance against the GRIB arm, so this arm is labelled "
            "distinctly and keeps its own evidence clock. Reduced-feature "
            "variant: total column liquid water is unavailable from "
            "Open-Meteo, so tclw_pwt / tclw_max are NaN under the "
            "validated pass-through."
        ),
    }
    for entry in frontend["points"]:
        rows = [r for r in all_records
                if r["point_id"] == entry["pointId"] and not r["missing"]]
        if rows:
            entry["forecasts"][ARM] = [{
                "leadDay": r["lead_day"],
                "windowStartUtc": r["window_start_utc"],
                "windowEndUtc": r["window_end_utc"],
                "windowEndLocalDate": r["window_end_local_date"],
                "modelSlr": r.get("model_slr"),
                "modelSnowfallCm": r.get("model_snowfall_cm"),
                "kucheraSlr": r.get("kuchera_slr"),
                "kucheraSnowfallCm": r.get("kuchera_snowfall_cm"),
                "liquidMm": r.get("liquid_mm"),
                "reducedFeatureVariant": True,
            } for r in rows]
    frontend_path.write_text(json.dumps(frontend, indent=1))

    # Verification: every pre-existing row and field is unchanged.
    after = json.loads(frontend_path.read_text())
    unchanged = True
    for before_entry, after_entry in zip(frontend_before["points"], after["points"]):
        for key, value in before_entry.items():
            if key == "forecasts":
                continue
            if after_entry.get(key) != value:
                unchanged = False
                print("CHANGED point field:", before_entry["pointId"], key, flush=True)
        for arm in ("ecmwf", "hrrr"):
            if after_entry["forecasts"].get(arm) != before_entry["forecasts"].get(arm):
                unchanged = False
                print("CHANGED rows:", before_entry["pointId"], arm, flush=True)
    for key, value in frontend_before.items():
        if key in ("points", "cycles"):
            continue
        if after.get(key) != value:
            unchanged = False
            print("CHANGED top-level field:", key, flush=True)
    for arm in ("ecmwf", "hrrr"):
        if after["cycles"].get(arm) != frontend_before["cycles"].get(arm):
            unchanged = False
            print("CHANGED cycle:", arm, flush=True)
    print("pre-existing frontend content unchanged:", unchanged, flush=True)

    manifest: dict[str, Any] = {
        "run_started_utc": retrieved_at,
        "arm": ARM,
        "source_id": SOURCE_ID_OPENMETEO,
        "source_display_name": SOURCE_DISPLAY_NAME,
        "anchor_cycle": {"date": args.date, "hour_z": args.cycle},
        "cycle_basis": CYCLE_BASIS,
        "retrieved_at_utc": retrieved_at,
        "pull_meta": {k: pull_meta.get(k) for k in
                      ("model_string", "start_date", "end_date", "total_values",
                       "nulls_by_var", "response_bytes", "locations_returned")},
        "points": len(points),
        "nan_policy": NAN_POLICY,
        "feature_builder_version": FEATURE_BUILDER_VERSION,
        "reduced_feature_variant": True,
        "grib_ceilings": ceilings,
        "records_total": len(all_records),
        "records_scored": sum(1 for r in all_records if not r["missing"]),
        "records_missing": sum(1 for r in all_records if r["missing"]),
        "preexisting_frontend_content_unchanged": unchanged,
        "run_finished_utc": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "run_manifest_openmeteo.json").write_text(json.dumps(manifest, indent=1, default=str))
    print(json.dumps(manifest, indent=1, default=str))
    return 0 if unchanged else 1


if __name__ == "__main__":
    raise SystemExit(main())
