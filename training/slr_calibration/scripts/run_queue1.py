#!/usr/bin/env python3
"""Queue items 1-2 driver: cached two-stage judge + GBM-only qualification.

Pre-registration: data/slr-training-data/screened-v2/model-v1/model-v3-queue1/DESIGN.md
Stages: manifests -> import-loro -> seed -> verify -> validate -> qualify -> report
All stages are resumable: cached entries are never refitted.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "training/slr_calibration/src"))

from slr_calibration import gold_model  # noqa: E402
from slr_calibration.prediction_cache import (  # noqa: E402
    PredictionCache,
    build_manifests,
    cohort_hash,
    feature_hash,
    manifest_hash,
    split_instances,
)
from slr_calibration.two_stage_judge import Judge, spearman  # noqa: E402

BASE = REPO / "data/slr-training-data/screened-v2/model-v1"
FEATURES = BASE / "model-v3-final/features_v3.parquet"
COLUMNS_JSON = BASE / "model-v3/screens_v2/final_combination.json"
JUDGING_JSON = BASE / (
    "model-v3/judging/results_pressure_greedy_amount_aggregation_"
    "warm_layer_surface_dgz_riming_ascent_subcloud.json"
)
LORO_PARQUET = BASE / "model-v3/loro_predictions.parquet"
OUT = BASE / "model-v3-queue1"
CACHE_ROOT = OUT / "cache"

VALIDATION_CONFIGS = ["gbm_eb", "rf_eb", "ens_rg", "tail_rf_eb", "gbm", "rf"]
INCUMBENT = "ens_eb"


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def load_frame_and_columns():
    frame, accounting = gold_model.load_modeling_frame(FEATURES)
    columns = json.loads(COLUMNS_JSON.read_text())["columns"]
    return frame, columns, accounting


def make_cache(manifests: dict) -> PredictionCache:
    return PredictionCache(
        CACHE_ROOT,
        cohort=cohort_hash(FEATURES),
        features=feature_hash(json.loads(COLUMNS_JSON.read_text())["columns"]),
        manifests=manifest_hash(manifests),
    )


def stage_manifests() -> dict:
    frame, _columns, _acc = load_frame_and_columns()
    instances = split_instances()
    manifests = build_manifests(frame, instances)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "manifests.json").write_text(json.dumps(manifests, indent=1), encoding="utf-8")
    meta = {
        "cohort_sha256": cohort_hash(FEATURES),
        "feature_sha256": feature_hash(json.loads(COLUMNS_JSON.read_text())["columns"]),
        "manifest_sha256": manifest_hash(manifests),
        "instances": len(manifests),
    }
    (OUT / "queue1_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log(f"manifests: {len(manifests)} instances, hash {meta['manifest_sha256'][:12]}")
    for split_id, entry in manifests.items():
        log(f"  {split_id}: train {entry['train_n']} test {entry['test_n']}")
    return manifests


def load_manifests() -> dict:
    return json.loads((OUT / "manifests.json").read_text(encoding="utf-8"))


def stage_import_loro(manifests: dict) -> dict:
    frame, _columns, _acc = load_frame_and_columns()
    cache = make_cache(manifests)
    loro = pd.read_parquet(LORO_PARQUET).set_index("event_id")
    judging = json.loads(JUDGING_JSON.read_text())
    # (i) ensemble = mean of components, over all events
    recon = (loro["pred_v3_rf_eb"] + loro["pred_v3_gbm_eb"]) / 2.0
    max_recon_diff = float(np.max(np.abs(recon - loro["pred_v3_ens_eb"])))
    # (ii) per-region MAEs from stored predictions vs judging JSON
    region_diffs: dict[str, dict[str, float]] = {}
    family_maes = []
    for region in sorted(loro["region"].unique()):
        sub = loro[loro["region"] == region]
        ids = manifests[f"loro_{region}"]["test_event_ids"]
        aligned = sub.reindex(ids)
        assert aligned["observed_slr"].notna().all(), f"loro import misalignment {region}"
        cache.put("rf_eb", f"loro_{region}", 2000, ids,
                  aligned["pred_v3_rf_eb"].to_numpy(), origin="imported:loro_predictions.parquet")
        cache.put("gbm_eb", f"loro_{region}", 2000, ids,
                  aligned["pred_v3_gbm_eb"].to_numpy(), origin="imported:loro_predictions.parquet")
        cache.put("kuchera", f"loro_{region}", 2000, ids,
                  aligned["kuchera_slr"].to_numpy(), origin="imported:loro_predictions.parquet")
        region_diffs[region] = {}
        for model, col in (("ens_eb", "pred_v3_ens_eb"), ("rf_eb", "pred_v3_rf_eb"), ("gbm_eb", "pred_v3_gbm_eb")):
            mae = float(np.mean(np.abs(aligned[col].to_numpy() - aligned["observed_slr"].to_numpy())))
            stored = judging["splits"]["leaveOneRegionOut"][region]["models"][model]["overall"]["mae"]
            region_diffs[region][model] = abs(mae - stored)
            if model == "ens_eb":
                family_maes.append(mae)
    max_region_diff = max(d for r in region_diffs.values() for d in r.values())
    # (iii) LORO family MAE vs the REPORT headline
    family_mae = float(np.mean(family_maes))
    result = {
        "max_ens_reconstruction_diff": max_recon_diff,
        "max_region_mae_diff_vs_judging_json": max_region_diff,
        "loro_family_mae_from_stored": round(family_mae, 4),
        "loro_family_mae_report_headline": 3.2540,
    }
    log(f"import-loro: {result}")
    return result


def split_frames(frame: pd.DataFrame, inst: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    if inst["family"] == "randomised":
        return gold_model.split_random(frame, seed=1000 + inst["rep"])
    if inst["family"] == "loro":
        return gold_model.split_loro(frame, inst["region"])
    if inst["family"] == "temporal":
        return gold_model.split_temporal(frame)
    return gold_model.split_stations(frame, seed=4000)


def fit_components(train, test, seed, columns, models):
    """Mirror of gold_model._run_pressure_split's fits, returning predictions."""
    cols = list(columns)
    out: dict[str, np.ndarray] = {}
    if "kuchera" in models:
        out["kuchera"] = test["baseline_slr"].to_numpy()
    if "rf" in models:
        out["rf"] = gold_model._fit_rf(train, seed, cols).predict(test[cols].to_numpy())
    if "gbm" in models:
        out["gbm"] = gold_model._fit_gbm(train, seed, cols).predict(test[cols].to_numpy())
    if "rf_eb" in models:
        _, pred = gold_model.RFEBModel(seed, columns=cols).fit(train).predict_parts(test)
        out["rf_eb"] = pred
    if "gbm_eb" in models:
        _, pred = gold_model.GBMEBModel(seed, columns=cols).fit(train).predict_parts(test)
        out["gbm_eb"] = pred
    return out


def stage_seed(manifests: dict, only: str | None = None,
               instance_list: list[str] | None = None) -> None:
    frame, columns, _acc = load_frame_and_columns()
    cache = make_cache(manifests)
    instances = split_instances()
    for inst in instances:
        split_id = inst["split_id"]
        if only and split_id != only:
            continue
        if instance_list is not None and split_id not in instance_list:
            continue
        ids = manifests[split_id]["test_event_ids"]
        seed = inst["fit_seed"]
        if inst["family"] == "loro":
            wanted = ["rf", "gbm"]  # rf_eb/gbm_eb/kuchera come from the import
        else:
            wanted = ["rf", "gbm", "rf_eb", "gbm_eb", "kuchera"]
        missing = [m for m in wanted if cache.get(m, split_id, seed, ids) is None]
        if not missing:
            log(f"seed {split_id}: cached, skip")
            continue
        train, test = split_frames(frame, inst)
        assert [str(e) for e in test["event_id"].tolist()] == ids, f"manifest mismatch {split_id}"
        t0 = time.time()
        preds = fit_components(train, test, seed, columns, missing)
        for model, values in preds.items():
            cache.put(model, split_id, seed, ids, values, origin="fitted:run_queue1")
        log(f"seed {split_id}: fitted {missing} in {time.time() - t0:.0f}s")


def load_predictions(cache: PredictionCache, manifests: dict, model: str) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    for inst in split_instances():
        split_id = inst["split_id"]
        ids = manifests[split_id]["test_event_ids"]
        values = cache.get(model, split_id, inst["fit_seed"], ids)
        if values is None:
            raise RuntimeError(f"cache miss: {model} {split_id}")
        out[split_id] = pd.Series(values, index=ids)
    return out


def stage_verify(manifests: dict) -> dict:
    frame, _columns, _acc = load_frame_and_columns()
    cache = make_cache(manifests)
    metadata = frame.set_index("event_id")[["observed_slr", "region"]]
    judge = Judge(metadata, manifests)
    judging = json.loads(JUDGING_JSON.read_text())
    models = ["rf", "gbm", "rf_eb", "gbm_eb", "ens_eb", "kuchera"]
    instance_diffs: dict[str, float] = {}
    family_diffs: dict[str, float] = {}
    for model in models:
        preds = load_predictions(cache, manifests, model)
        stats = judge.family_maes(preds)
        # instance-level: LORO regions, temporal, stations, random rep0
        checks = {}
        for region, entry in judging["splits"]["leaveOneRegionOut"].items():
            checks[f"loro_{region}"] = entry["models"][model]["overall"]["mae"]
        checks["temporalWY2026"] = judging["splits"]["temporalWY2026"]["models"][model]["overall"]["mae"]
        checks["unseenStations"] = judging["splits"]["unseenStations"]["models"][model]["overall"]["mae"]
        checks["random60_40_rep0"] = judging["splits"]["random60_40"]["aggregated"][model]["rep0"]["overall"]["mae"]
        for split_id, stored in checks.items():
            instance_diffs[f"{model}/{split_id}"] = abs(stats["per_instance"][split_id] - stored)
        family_diffs[f"{model}/randomised"] = abs(
            stats["family"]["randomised"] - judging["splits"]["random60_40"]["aggregated"][model]["maeMean"]
        )
        family_diffs[f"{model}/loro"] = abs(
            stats["family"]["loro"]
            - float(np.mean([judging["splits"]["leaveOneRegionOut"][r]["models"][model]["overall"]["mae"] for r in judging["splits"]["leaveOneRegionOut"]]))
        )
        family_diffs[f"{model}/temporal"] = abs(stats["family"]["temporal"] - checks["temporalWY2026"])
        family_diffs[f"{model}/stations"] = abs(stats["family"]["stations"] - checks["unseenStations"])
    result = {
        "max_instance_mae_diff_vs_judging_json": max(instance_diffs.values()),
        "max_family_mae_diff_vs_judging_json": max(family_diffs.values()),
        "instance_diffs": {k: round(v, 6) for k, v in sorted(instance_diffs.items())},
        "family_diffs": {k: round(v, 6) for k, v in sorted(family_diffs.items())},
    }
    log(f"verify: max instance diff {result['max_instance_mae_diff_vs_judging_json']:.6f}, "
        f"max family diff {result['max_family_mae_diff_vs_judging_json']:.6f}")
    return result


def make_judge(manifests: dict) -> Judge:
    frame, _columns, _acc = load_frame_and_columns()
    metadata = frame.set_index("event_id")[["observed_slr", "region"]]
    return Judge(metadata, manifests)


def stage_validate(manifests: dict) -> dict:
    cache = make_cache(manifests)
    judge = make_judge(manifests)
    incumbent = load_predictions(cache, manifests, INCUMBENT)
    rows = []
    for config in VALIDATION_CONFIGS:
        candidate = load_predictions(cache, manifests, config)
        screen = judge.screen(incumbent, candidate)
        full = judge.full(incumbent, candidate)
        rows.append({
            "config": config,
            "screen_scalar": screen["screen_scalar"],
            "screen_killed": screen["killed"],
            "screen_kill_clauses": screen["kill_clauses"],
            "screen": {k: v for k, v in screen.items() if k not in ("killed", "kill_clauses", "screen_scalar")},
            "full_four_family_gain": full["four_family_gain"],
            "full_family_gain": full["family_gain"],
            "full_loro_region_gain": full["loro_region_gain"],
            "full_adopted": full["adopted"],
            "full_failed_clauses": full["failed_clauses"],
        })
        log(f"validate {config}: screen {screen['screen_scalar']:+.4f} killed={screen['killed']} "
            f"full gain {full['four_family_gain']:+.4f} adopted={full['adopted']}")
    rho = spearman([r["screen_scalar"] for r in rows], [r["full_four_family_gain"] for r in rows])
    false_rejections = [r["config"] for r in rows if r["screen_killed"] and r["full_adopted"]]
    trusted = bool(rho >= 0.8 and not false_rejections)
    result = {
        "configs": rows,
        "spearman_screen_vs_full": rho,
        "false_rejections": false_rejections,
        "screen_kill_rule_trusted": trusted,
        "fallback_if_untrusted": "exact-LORO early stop only (pooled LORO gain < 0.02 or any region gain < -0.05)",
    }
    log(f"validate: spearman {rho:.3f}, false rejections {false_rejections}, trusted={trusted}")
    return result


def stage_qualify(manifests: dict) -> dict:
    cache = make_cache(manifests)
    judge = make_judge(manifests)
    incumbent = load_predictions(cache, manifests, INCUMBENT)
    candidate = load_predictions(cache, manifests, "gbm_eb")
    result = judge.qualification(incumbent, candidate)
    log(f"qualify gbm_eb: qualified={result['qualified']} diffs={result['diffs']}")
    return json.loads(json.dumps(result, default=float))


def stage_report(payload: dict) -> None:
    lines = ["# Queue items 1–2 — Results", "",
             "Pre-registration: DESIGN.md (same directory). Rules applied mechanically.", ""]
    if "loro_import" in payload:
        li = payload["loro_import"]
        lines += ["## LORO import + ensemble reconstruction (item 1b)", "",
                  f"- Max |ens − (rf_eb+gbm_eb)/2| over 57,376 events: {li['max_ens_reconstruction_diff']:.3e}",
                  f"- Max per-region MAE diff vs judging JSON: {li['max_region_mae_diff_vs_judging_json']:.6f}",
                  f"- LORO family MAE from stored predictions: {li['loro_family_mae_from_stored']} "
                  f"(REPORT headline: {li['loro_family_mae_report_headline']})", ""]
    if "refit" in payload:
        rf = payload["refit"]
        lines += ["## Cache seeding verification (refits vs judging JSON)", "",
                  f"- Max instance MAE diff: {rf['max_instance_mae_diff_vs_judging_json']:.6f}",
                  f"- Max family MAE diff: {rf['max_family_mae_diff_vs_judging_json']:.6f}", ""]
    if "screen_validation" in payload:
        sv = payload["screen_validation"]
        lines += ["## Screen validation (item 1d)", "",
                  "| config | screen scalar | screen killed | full four-family gain | full adopted |",
                  "|---|---|---|---|---|"]
        for row in sv["configs"]:
            lines.append(f"| {row['config']} | {row['screen_scalar']:+.4f} | {row['screen_killed']} | "
                         f"{row['full_four_family_gain']:+.4f} | {row['full_adopted']} |")
        lines += ["", f"- Spearman(screen scalar, full gain): {sv['spearman_screen_vs_full']:.3f}",
                  f"- False rejections: {sv['false_rejections']}",
                  f"- **Screen kill rule trusted: {sv['screen_kill_rule_trusted']}**", ""]
    if "gbm_qualification" in payload:
        q = payload["gbm_qualification"]
        lines += ["## GBM-only qualification (item 2)", "",
                  f"- Four-family mean: incumbent {q['incumbent']['four_family_mean']:.4f} vs "
                  f"GBM+EB {q['candidate']['four_family_mean']:.4f} (diff {q['diffs']['four_family_mean']:+.4f}, limit +0.02)",
                  f"- Pooled LORO diff: {q['diffs']['loro']:+.4f} (limit 0.00)",
                  f"- Temporal diff: {q['diffs']['temporal']:+.4f} (limit +0.03); "
                  f"stations diff: {q['diffs']['stations']:+.4f} (limit +0.03); "
                  f"randomised diff: {q['diffs']['randomised']:+.4f}",
                  "- Per-region LORO diffs: " + ", ".join(
                      f"{r} {d:+.4f}" for r, d in q["loro_region_diffs"].items()),
                  f"- Failed clauses: {q['failed_clauses']}",
                  f"- **GBM-only qualified as research/packaging default: {q['qualified']}**", ""]
    (OUT / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")
    log("report written")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["manifests", "import-loro", "seed", "verify",
                                      "validate", "qualify", "report", "all"])
    ap.add_argument("--only", default=None, help="seed a single split_id")
    ap.add_argument("--instances", default=None, help="comma-separated split_ids to seed")
    args = ap.parse_args()
    manifests = load_manifests() if (OUT / "manifests.json").exists() and args.stage != "manifests" else None
    payload: dict = {}
    if args.stage in ("manifests", "all"):
        manifests = stage_manifests()
    if args.stage in ("import-loro", "all"):
        payload["loro_import"] = stage_import_loro(manifests)
        (OUT / "seed_verification.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.stage in ("seed", "all"):
        stage_seed(manifests, only=args.only,
                   instance_list=args.instances.split(",") if args.instances else None)
    if args.stage in ("verify", "all"):
        existing = {}
        if (OUT / "seed_verification.json").exists():
            existing = json.loads((OUT / "seed_verification.json").read_text())
        existing["refit"] = stage_verify(manifests)
        (OUT / "seed_verification.json").write_text(json.dumps(existing, indent=2), encoding="utf-8")
        payload.update(existing)
    if args.stage in ("validate", "all"):
        payload["screen_validation"] = stage_validate(manifests)
        (OUT / "screen_validation.json").write_text(
            json.dumps(payload["screen_validation"], indent=2), encoding="utf-8")
    if args.stage in ("qualify", "all"):
        payload["gbm_qualification"] = stage_qualify(manifests)
        (OUT / "gbm_qualification.json").write_text(
            json.dumps(payload["gbm_qualification"], indent=2), encoding="utf-8")
    if args.stage in ("report", "all"):
        for name, fname in (("loro_import", None), ("screen_validation", "screen_validation.json"),
                            ("gbm_qualification", "gbm_qualification.json")):
            if name not in payload and fname and (OUT / fname).exists():
                payload[name] = json.loads((OUT / fname).read_text())
        if (OUT / "seed_verification.json").exists():
            payload.update(json.loads((OUT / "seed_verification.json").read_text()))
        stage_report(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
