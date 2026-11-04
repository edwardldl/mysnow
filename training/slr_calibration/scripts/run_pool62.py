#!/usr/bin/env python3
"""Queue item 6 driver: full-pool 62-feature model for the Open-Meteo config.

Pre-registration: data/slr-training-data/screened-v2/model-v1/model-v3-pool62/DESIGN.md
Stages: fit -> judge -> package -> report.  All stages are resumable:
cached predictions are never refitted.  Run SERIALLY (item 5 lesson:
parallel sharding is pathological in this container).
"""
from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "training/slr_calibration/src"))

from slr_calibration import gold_model  # noqa: E402
from slr_calibration.pool62_experiment import (  # noqa: E402
    adoption_verdict,
    mask_frame,
    reduced_columns,
)
from slr_calibration.prediction_cache import (  # noqa: E402
    PredictionCache,
    cohort_hash,
    feature_hash,
    manifest_hash,
    split_instances,
)
from slr_calibration.two_stage_judge import Judge  # noqa: E402

BASE = REPO / "data/slr-training-data/screened-v2/model-v1"
FEATURES = BASE / "model-v3-final/features_v3.parquet"
COLUMNS_JSON = BASE / "model-v3/screens_v2/final_combination.json"
QUEUE1 = BASE / "model-v3-queue1"
OUT = BASE / "model-v3-pool62"

# WALL's published like-for-like LORO MAEs (model-v3-western/REPORT.md
# section 4: honest refits, identical western test events).
WALL_LORO = {
    "rockies": 3.2133,
    "pacific-northwest": 3.1625,
    "sierra-nevada": 3.2861,
    "alaska": 3.7707,
}
WESTERN_REGIONS = ["rockies", "pacific-northwest", "sierra-nevada", "alaska"]


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def load_frame_and_columns():
    frame, accounting = gold_model.load_modeling_frame(FEATURES)
    columns = json.loads(COLUMNS_JSON.read_text())["columns"]
    return frame, columns, accounting


def load_manifests() -> dict:
    return json.loads((QUEUE1 / "manifests.json").read_text(encoding="utf-8"))


def make_caches(manifests: dict, columns: list[str]) -> dict[str, PredictionCache]:
    cohort = cohort_hash(FEATURES)
    mhash = manifest_hash(manifests)
    cols62 = reduced_columns(columns)
    return {
        "masked": PredictionCache(
            OUT / "cache_masked", cohort=cohort,
            features=feature_hash(columns), manifests=mhash,
        ),
        "pool62": PredictionCache(
            OUT / "cache_pool62", cohort=cohort,
            features=feature_hash(cols62), manifests=mhash,
        ),
        "queue1": PredictionCache(
            QUEUE1 / "cache", cohort=cohort,
            features=feature_hash(columns), manifests=mhash,
        ),
    }


def split_frames(frame: pd.DataFrame, inst: dict):
    if inst["family"] == "randomised":
        return gold_model.split_random(frame, seed=1000 + inst["rep"])
    if inst["family"] == "loro":
        return gold_model.split_loro(frame, inst["region"])
    if inst["family"] == "temporal":
        return gold_model.split_temporal(frame)
    return gold_model.split_stations(frame, seed=4000)


def screen_split_ids() -> list[str]:
    ids = [f"loro_{r}" for r in (
        "alaska", "great-lakes", "northeast", "pacific-northwest",
        "rockies", "sierra-nevada",
    )]
    return ids + ["random60_40_rep0", "temporalWY2026", "unseenStations"]


def select_instances(which: str, explicit: str | None) -> list[dict]:
    instances = split_instances()
    if explicit:
        wanted = set(explicit.split(","))
        return [i for i in instances if i["split_id"] in wanted]
    if which == "screen":
        wanted = set(screen_split_ids())
        return [i for i in instances if i["split_id"] in wanted]
    if which == "rest":
        wanted = set(screen_split_ids())
        return [i for i in instances if i["split_id"] not in wanted]
    return instances


def _fitlog_update(entry_key: str, record: dict) -> None:
    path = OUT / "fitlog.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[entry_key] = record
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def fit_masked_instance(frame, inst, manifests, caches, columns) -> None:
    """64-feature fold fit; masked predictions stored; unmasked ens
    compared in memory against the queue-1 cache (integrity check)."""
    split_id = inst["split_id"]
    seed = inst["fit_seed"]
    ids = manifests[split_id]["test_event_ids"]
    cache = caches["masked"]
    missing = [
        m for m in ("rf_eb_masked62", "gbm_eb_masked62")
        if cache.get(m, split_id, seed, ids) is None
    ]
    if not missing:
        log(f"masked {split_id}: cached, skip")
        return
    train, test = split_frames(frame, inst)
    assert [str(e) for e in test["event_id"].tolist()] == ids, f"manifest mismatch {split_id}"
    t0 = time.time()
    rf = gold_model.RFEBModel(seed, columns=list(columns)).fit(train)
    gbm = gold_model.GBMEBModel(seed, columns=list(columns)).fit(train)
    masked_test = mask_frame(test)
    _, rf_masked = rf.predict_parts(masked_test)
    _, gbm_masked = gbm.predict_parts(masked_test)
    cache.put("rf_eb_masked62", split_id, seed, ids, rf_masked, origin="fitted:run_pool62")
    cache.put("gbm_eb_masked62", split_id, seed, ids, gbm_masked, origin="fitted:run_pool62")
    _, rf_unmasked = rf.predict_parts(test)
    _, gbm_unmasked = gbm.predict_parts(test)
    ens_unmasked = (rf_unmasked + gbm_unmasked) / 2.0
    reference = caches["queue1"].get("ens_eb", split_id, seed, ids)
    diff = (
        float(np.max(np.abs(ens_unmasked - reference)))
        if reference is not None else None
    )
    seconds = time.time() - t0
    _fitlog_update(
        f"masked:{split_id}",
        {"seconds": round(seconds, 1), "integrity_max_abs_diff_vs_queue1_ens": diff},
    )
    log(f"masked {split_id}: fitted in {seconds:.0f}s, integrity diff {diff}")
    del rf, gbm
    gc.collect()


def fit_pool62_instance(frame, inst, manifests, caches, columns62) -> None:
    split_id = inst["split_id"]
    seed = inst["fit_seed"]
    ids = manifests[split_id]["test_event_ids"]
    cache = caches["pool62"]
    missing = [
        m for m in ("rf_eb", "gbm_eb")
        if cache.get(m, split_id, seed, ids) is None
    ]
    if not missing:
        log(f"pool62 {split_id}: cached, skip")
        return
    train, test = split_frames(frame, inst)
    assert [str(e) for e in test["event_id"].tolist()] == ids, f"manifest mismatch {split_id}"
    t0 = time.time()
    rf = gold_model.RFEBModel(seed, columns=list(columns62)).fit(train)
    gbm = gold_model.GBMEBModel(seed, columns=list(columns62)).fit(train)
    _, rf_pred = rf.predict_parts(test)
    _, gbm_pred = gbm.predict_parts(test)
    cache.put("rf_eb", split_id, seed, ids, rf_pred, origin="fitted:run_pool62")
    cache.put("gbm_eb", split_id, seed, ids, gbm_pred, origin="fitted:run_pool62")
    seconds = time.time() - t0
    _fitlog_update(f"pool62:{split_id}", {"seconds": round(seconds, 1)})
    log(f"pool62 {split_id}: fitted in {seconds:.0f}s")
    del rf, gbm
    gc.collect()


def stage_fit(arm: str, which: str, explicit: str | None) -> None:
    frame, columns, _acc = load_frame_and_columns()
    columns62 = reduced_columns(columns)
    manifests = load_manifests()
    caches = make_caches(manifests, columns)
    OUT.mkdir(parents=True, exist_ok=True)
    for inst in select_instances(which, explicit):
        if arm in ("masked", "both"):
            fit_masked_instance(frame, inst, manifests, caches, columns)
        if arm in ("pool62", "both"):
            fit_pool62_instance(frame, inst, manifests, caches, columns62)


def load_predictions(cache, manifests, model, split_ids=None):
    out: dict[str, pd.Series] = {}
    for inst in split_instances():
        split_id = inst["split_id"]
        if split_ids is not None and split_id not in split_ids:
            continue
        ids = manifests[split_id]["test_event_ids"]
        values = cache.get(model, split_id, inst["fit_seed"], ids)
        if values is None:
            raise RuntimeError(f"cache miss: {model} {split_id}")
        out[split_id] = pd.Series(values, index=ids)
    return out


def make_judge(manifests) -> Judge:
    frame, _columns, _acc = load_frame_and_columns()
    metadata = frame.set_index("event_id")[["observed_slr", "region"]]
    return Judge(metadata, manifests)


def stage_judge(screen_only: bool) -> dict:
    _frame, columns, _acc = load_frame_and_columns()
    manifests = load_manifests()
    caches = make_caches(manifests, columns)
    judge = make_judge(manifests)
    split_ids = set(screen_split_ids()) if screen_only else None
    masked = load_predictions(caches["masked"], manifests, "ens_eb_masked62", split_ids)
    candidate = load_predictions(caches["pool62"], manifests, "ens_eb", split_ids)
    screen = judge.screen(masked, candidate)
    log(f"screen: scalar {screen['screen_scalar']:+.4f} killed={screen['killed']} "
        f"clauses={screen['kill_clauses']}")
    if screen_only:
        payload = {"screen": json.loads(json.dumps(screen, default=float))}
        (OUT / "screen.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload
    unmasked = load_predictions(caches["queue1"], manifests, "ens_eb")
    full = judge.full(masked, candidate)
    verdict = adoption_verdict(full)
    stats = {
        "incumbent_masked": judge.family_maes(masked),
        "candidate62": judge.family_maes(candidate),
        "incumbent_unmasked": judge.family_maes(unmasked),
    }
    masking_cost = {
        "family": {
            name: stats["incumbent_masked"]["family"][name]
            - stats["incumbent_unmasked"]["family"][name]
            for name in stats["incumbent_masked"]["family"]
        },
        "loro_regions": {
            region: stats["incumbent_masked"]["loro_regions"][region]
            - stats["incumbent_unmasked"]["loro_regions"][region]
            for region in stats["incumbent_masked"]["loro_regions"]
        },
        "four_family_mean": stats["incumbent_masked"]["four_family_mean"]
        - stats["incumbent_unmasked"]["four_family_mean"],
    }
    wall = {
        region: {
            "candidate62_loro_mae": stats["candidate62"]["loro_regions"][region],
            "wall_loro_mae": WALL_LORO[region],
            "delta_candidate_minus_wall": stats["candidate62"]["loro_regions"][region]
            - WALL_LORO[region],
        }
        for region in WESTERN_REGIONS
    }
    fitlog = {}
    if (OUT / "fitlog.json").exists():
        fitlog = json.loads((OUT / "fitlog.json").read_text())
    integrity = {
        key.split(":", 1)[1]: rec.get("integrity_max_abs_diff_vs_queue1_ens")
        for key, rec in fitlog.items() if key.startswith("masked:")
    }
    payload = {
        "manifest_sha256": manifest_hash(manifests),
        "screen": screen,
        "full": {k: v for k, v in full.items() if k not in ("incumbent", "candidate")},
        "family_stats": stats,
        "verdict": verdict,
        "masking_cost_masked_minus_unmasked": masking_cost,
        "wall_comparison_loro": wall,
        "fit_integrity_max_abs_diff": integrity,
    }
    (OUT / "results.json").write_text(
        json.dumps(payload, indent=2, default=float), encoding="utf-8"
    )
    log(f"full: four-family gain {full['four_family_gain']:+.4f}, "
        f"adopted (item-6 rule) = {verdict['adopted']}")
    return payload


def stage_package() -> dict | None:
    results_path = OUT / "results.json"
    if not results_path.exists():
        log("package: no results.json; run judge first")
        return None
    results = json.loads(results_path.read_text())
    if not results["verdict"]["adopted"]:
        log("package: candidate NOT adopted under the item-6 rule; no package built")
        return None
    import joblib

    from slr_calibration import champion as champion_mod
    from slr_calibration.gold_model import _frame_profile_source

    frame, columns, accounting = load_frame_and_columns()
    columns62 = reduced_columns(columns)
    out_dir = OUT / "candidate"
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rf_eb = gold_model.RFEBModel(seed=0, columns=columns62).fit(frame)
    gbm_eb = gold_model.GBMEBModel(seed=0, columns=columns62).fit(frame)
    log(f"package: full-pool fits done in {time.time() - t0:.0f}s")
    ens_direct = (rf_eb.predict(frame) + gbm_eb.predict(frame)) / 2.0
    observed = frame["observed_slr"].to_numpy(dtype=float)
    direct_mae = float(np.mean(np.abs(ens_direct - observed)))
    joblib.dump(rf_eb.rf, out_dir / "rf.joblib")
    joblib.dump(gbm_eb.gbm, out_dir / "gbm.joblib")

    def component_block(model_file: str, description: str, eb_artifact: dict) -> dict:
        method = eb_artifact["methods"]["kuchera"]
        return {
            "modelFile": model_file,
            "model": description,
            "predictionClamp": [3.0, 30.0],
            "eb": (
                "Hierarchical empirical-Bayes log-residual layer (trainer.train_artifact) "
                "fitted on station-grouped out-of-fold component predictions: "
                "global -> climate regime -> H3 region cell (res 4) -> site nodes, each "
                "shrunk toward its parent with prior effective sample size 30; the summed "
                "log bias is clamped to a correction factor in [0.5, 2.0] and the "
                "calibrated SLR to [3, 30]. At predict time the walk stops at the first "
                "missing or under-supported level, so unseen geography degrades to the "
                "broader levels and an entirely unseen area receives the global-node "
                "correction only."
            ),
            "ebArtifact": eb_artifact,
            "ebNodes": {
                "climate": len(method["climate"]),
                "regionH3Res4": len(method["region"]),
                "localSite": len(method["local"]),
            },
        }

    stats = results["family_stats"]
    sidecar = {
        "packageFormat": champion_mod.PACKAGE_FORMAT,
        "packageVersion": champion_mod.PACKAGE_VERSION,
        "model": "ENS-EB (pool62 candidate for the Open-Meteo configuration)",
        "status": "CANDIDATE FOR THE OPEN-METEO CONFIGURATION — NOT SERVED",
        "definition": (
            "ENS-EB SLR = simple mean of the RF+EB and GBM+EB predictions "
            "(each component's clipped tree prediction passed through its own "
            "EB residual layer). Trained on the full 57,376-event gold pool "
            "with the 62-feature set (tclw_pwt / tclw_max dropped), for the "
            "Open-Meteo serving configuration whose inputs lack the pair."
        ),
        "features": columns62,
        "regions": sorted(str(r) for r in frame["region"].unique()),
        "combination": {"rule": "simple_mean", "components": ["rf_eb", "gbm_eb"]},
        "components": {
            "rf_eb": component_block(
                "rf.joblib",
                "RandomForestRegressor(n_estimators=500, random_state=0, sklearn defaults otherwise), "
                "full-pool 62-feature fit (queue item 6 packaging)",
                rf_eb.eb_artifact,
            ),
            "gbm_eb": component_block(
                "gbm.joblib",
                "HistGradientBoostingRegressor(random_state=0, sklearn defaults), "
                "full-pool 62-feature fit (queue item 6 packaging)",
                gbm_eb.eb_artifact,
            ),
        },
        "training": {
            "profileSource": _frame_profile_source(frame),
            "featuresFile": FEATURES.name,
            "trainingRows": int(len(frame)),
            "waterYears": accounting["modelingWaterYears"],
            "rowsByRegion": accounting["modelingByRegion"],
            "ebFit": "RFEBModel / GBMEBModel (gold_model) with seed 0 on the full modeling frame, 62 features.",
        },
        "judgedScores": {
            "experiment": "queue item 6 (model-v3-pool62), masked-input judging on the frozen v3 folds",
            "incumbentMaskedFamilyMae": stats["incumbent_masked"]["family"],
            "incumbentMaskedFourFamilyMean": stats["incumbent_masked"]["four_family_mean"],
            "candidate62FamilyMae": stats["candidate62"]["family"],
            "candidate62FourFamilyMean": stats["candidate62"]["four_family_mean"],
            "candidate62LoroRegions": stats["candidate62"]["loro_regions"],
            "verdict": results["verdict"],
        },
        "note": (
            "Candidate artefact for the Open-Meteo configuration only. NOT SERVED: "
            "nothing is wired into serving; production calibration gates remain OFF."
        ),
    }
    sidecar_text = json.dumps(sidecar, indent=2, allow_nan=False)
    (out_dir / champion_mod.SIDECAR_NAME).write_text(sidecar_text, encoding="utf-8")

    # Round-trip verification through the package alone.
    model = champion_mod.ChampionModel.load(out_dir)
    packaged = model.predict(frame)
    max_diff = float(np.max(np.abs(packaged - ens_direct)))
    packaged_mae = float(np.mean(np.abs(packaged - observed)))
    verification = {
        "round_trip_max_abs_diff": max_diff,
        "direct_insample_mae": direct_mae,
        "packaged_insample_mae": packaged_mae,
    }
    (OUT / "package_verification.json").write_text(
        json.dumps(verification, indent=2), encoding="utf-8"
    )
    artifact_md = "\n".join([
        "# ENS-EB pool62 — CANDIDATE FOR THE OPEN-METEO CONFIGURATION — NOT SERVED",
        "",
        "Queue item 6 (model-v3-pool62). Full-pool 62-feature ENS-EB "
        "(tclw_pwt / tclw_max dropped), fitted seed 0 on all 57,376 gold events.",
        "Adopted as the Open-Meteo-configuration candidate under the item-6 "
        "pre-registered rule (DESIGN.md section 6); see RESULTS.md in "
        "model-v3-pool62/ for the clause-by-clause verdict.",
        "",
        f"- Direct in-sample MAE: {direct_mae:.4f}",
        f"- Round-trip max abs diff (package vs direct fits): {max_diff:.3e}",
        "",
        "NOT SERVED. Production calibration gates remain OFF; serving is unchanged.",
        "",
    ])
    (out_dir / champion_mod.ARTIFACT_MD_NAME).write_text(artifact_md, encoding="utf-8")
    log(f"package: written to {out_dir}; round-trip max diff {max_diff:.3e}")
    return verification


def _fmt(value: float, digits: int = 4) -> str:
    return f"{value:+.{digits}f}" if value is not None else "—"


def stage_report() -> None:
    results = json.loads((OUT / "results.json").read_text())
    stats = results["family_stats"]
    full = results["full"]
    verdict = results["verdict"]
    screen = results["screen"]
    lines = [
        "# Queue item 6 — Full-Pool 62-Feature Model for the Open-Meteo Configuration: Results",
        "",
        "Pre-registration: DESIGN.md (same directory). Rules applied mechanically.",
        "",
        f"**Verdict: CANDIDATE-62 {'ADOPTED' if verdict['adopted'] else 'NOT ADOPTED'} "
        f"under the item-6 rule.**",
        "",
        "## Judging (masked inputs, frozen v3 folds, paired event-by-event)",
        "",
        "| family | incumbent-masked | candidate-62 | gain |",
        "|---|---:|---:|---:|",
    ]
    for family in ("randomised", "loro", "temporal", "stations"):
        inc = stats["incumbent_masked"]["family"][family]
        cand = stats["candidate62"]["family"][family]
        lines.append(f"| {family} | {inc:.4f} | {cand:.4f} | {_fmt(full['family_gain'][family])} |")
    lines.append(
        f"| **four-family mean** | {stats['incumbent_masked']['four_family_mean']:.4f} | "
        f"{stats['candidate62']['four_family_mean']:.4f} | {_fmt(full['four_family_gain'])} |"
    )
    lines += ["", "LORO by region:", "", "| region | incumbent-masked | candidate-62 | gain |", "|---|---:|---:|---:|"]
    for region in sorted(stats["incumbent_masked"]["loro_regions"]):
        inc = stats["incumbent_masked"]["loro_regions"][region]
        cand = stats["candidate62"]["loro_regions"][region]
        lines.append(f"| {region} | {inc:.4f} | {cand:.4f} | {_fmt(full['loro_region_gain'][region])} |")
    lines += ["", "## Screen", "",
              f"- Screen scalar: {screen['screen_scalar']:+.4f}; killed: {screen['killed']}"
              + (f" ({'; '.join(screen['kill_clauses'])})" if screen["kill_clauses"] else ""),
              f"- Pooled LORO gain at screen: {screen['pooled_loro_gain']:+.4f}",
              "", "## Adoption rule (item 6), clause by clause", "",
              "| clause | value | threshold | pass |", "|---|---:|---:|:--:|"]
    for clause in verdict["clauses"]:
        lines.append(
            f"| {clause['clause']} | {clause['value']:.4f} | {clause['threshold']:.2f} | "
            f"{'yes' if clause['passed'] else 'NO'} |"
        )
    lines += [
        "",
        f"- Failed clauses: {verdict['failed_clauses'] if verdict['failed_clauses'] else 'none'}",
        f"- Under the general amended rule (LORO bar 0.02) the verdict would have been: "
        f"{'ADOPTED' if verdict['standard_rule_adopted'] else 'NOT ADOPTED'} (descriptive only).",
        "",
        "## Slices (descriptive; guards are clauses above)",
        "",
        "| family | slice | n | MAE diff (cand − inc) |",
        "|---|---|---:|---:|",
    ]
    for entry in full["slices"]:
        lines.append(f"| {entry['family']} | {entry['slice']} | {entry['n']} | {_fmt(entry['mae_diff'])} |")
    cost = results["masking_cost_masked_minus_unmasked"]
    lines += ["", "## Masking cost of the incumbent (descriptive)", "",
              "MAE of the 64-feature champion with the tclw pair masked, minus its "
              "stored unmasked MAE — what the missing pair costs the model as served "
              "today on the Open-Meteo arm:", "",
              "| family | cost |", "|---|---:|"]
    for family in ("randomised", "loro", "temporal", "stations"):
        lines.append(f"| {family} | {_fmt(cost['family'][family])} |")
    lines.append(f"| four-family mean | {_fmt(cost['four_family_mean'])} |")
    lines += ["", "LORO regions: " + ", ".join(
        f"{r} {_fmt(v)}" for r, v in sorted(cost["loro_regions"].items())), ""]
    lines += ["## Candidate-62 vs WALL (descriptive, LORO only)", "",
              "Apples-to-apples: identical LORO test events and identical 62-feature "
              "inputs. Not apples-to-apples: training pool (57,376 full-pool "
              "region-blind vs WALL's 16,486 western-only); WALL's other families were "
              "cohort-restricted and are not compared. For a 62-native model, masked "
              "and native inputs are the same thing.", "",
              "| region | candidate-62 | WALL | delta (cand − WALL) |", "|---|---:|---:|---:|"]
    for region in WESTERN_REGIONS:
        entry = results["wall_comparison_loro"][region]
        lines.append(
            f"| {region} | {entry['candidate62_loro_mae']:.4f} | {entry['wall_loro_mae']:.4f} | "
            f"{_fmt(entry['delta_candidate_minus_wall'])} |"
        )
    integrity = results["fit_integrity_max_abs_diff"]
    diffs = [v for v in integrity.values() if v is not None]
    lines += ["", "## Fit-path integrity", "",
              f"- Masked-arm fits' unmasked ensemble vs the queue-1 stored cache: "
              f"max abs diff over {len(diffs)} instances = {max(diffs) if diffs else '—'} "
              f"(instances without a stored reference: "
              f"{sum(1 for v in integrity.values() if v is None)}).", ""]
    if (OUT / "package_verification.json").exists():
        pv = json.loads((OUT / "package_verification.json").read_text())
        lines += ["## Package", "",
                  f"- `candidate/` built (CANDIDATE FOR THE OPEN-METEO CONFIGURATION — NOT SERVED).",
                  f"- Round-trip max abs diff vs direct fits: {pv['round_trip_max_abs_diff']:.3e}; "
                  f"in-sample MAE direct {pv['direct_insample_mae']:.4f} / packaged "
                  f"{pv['packaged_insample_mae']:.4f}.", ""]
    else:
        lines += ["## Package", "", "- No package built (adoption rule not met).", ""]
    (OUT / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")
    log("report written")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["fit", "judge", "package", "report", "all"])
    ap.add_argument("--arm", default="both", choices=["masked", "pool62", "both"])
    ap.add_argument("--set", default="all", choices=["screen", "rest", "all"])
    ap.add_argument("--instances", default=None)
    ap.add_argument("--screen-only", action="store_true")
    args = ap.parse_args()
    if args.stage == "fit":
        stage_fit(args.arm, args.set, args.instances)
    elif args.stage == "judge":
        stage_judge(args.screen_only)
    elif args.stage == "package":
        stage_package()
    elif args.stage == "report":
        stage_report()
    elif args.stage == "all":
        stage_fit("both", "all", None)
        stage_judge(False)
        stage_package()
        stage_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
