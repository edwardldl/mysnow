#!/usr/bin/env python3
"""Queue item 5 driver: feature-based Kuchera gate.

Pre-registration: data/slr-training-data/screened-v2/model-v1/model-v3-gate/DESIGN.md
Stages: oof -> apply -> judge -> report; package runs inside judge when
the mechanical adoption rule passes.  All stages are resumable: inner
OOF predictions and per-instance gates live beside the experiment's
outputs; gated fold predictions live in the queue-1 keyed cache.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "training/slr_calibration/src"))

from slr_calibration import gold_model  # noqa: E402
from slr_calibration.gate_model import (  # noqa: E402
    BLEND_WEIGHTS,
    GATE_INPUT_NAMES,
    LOW_PREDICTED_SLR,
    GateApplicator,
    KucheraGate,
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
OUT = BASE / "model-v3-gate"
CACHE_ROOT = QUEUE1 / "cache"

INCUMBENT = "ens_eb"
CANDIDATE = "ens_gate"


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------------------
# Shared loading (mirrors run_median_m.py)
# ---------------------------------------------------------------------------

def load_columns() -> list[str]:
    return json.loads(COLUMNS_JSON.read_text(encoding="utf-8"))["columns"]


def load_manifests() -> dict:
    return json.loads((QUEUE1 / "manifests.json").read_text(encoding="utf-8"))


def make_cache(manifests: dict) -> PredictionCache:
    return PredictionCache(
        CACHE_ROOT,
        cohort=cohort_hash(FEATURES),
        features=feature_hash(load_columns()),
        manifests=manifest_hash(manifests),
    )


def split_frames(frame: pd.DataFrame, inst: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    if inst["family"] == "randomised":
        return gold_model.split_random(frame, seed=1000 + inst["rep"])
    if inst["family"] == "loro":
        return gold_model.split_loro(frame, inst["region"])
    if inst["family"] == "temporal":
        return gold_model.split_temporal(frame)
    return gold_model.split_stations(frame, seed=4000)


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


# ---------------------------------------------------------------------------
# Stage: oof — inner out-of-fold ENS-EB predictions per outer instance
# ---------------------------------------------------------------------------

def _oof_instance(frame: pd.DataFrame, columns: list[str], inst: dict) -> dict:
    split_id = inst["split_id"]
    seed = inst["fit_seed"]
    out_dir = OUT / "oof"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{split_id}.parquet"
    sidecar = out_dir / f"{split_id}.json"
    if path.exists() and sidecar.exists():
        return {"split_id": split_id, "status": "cached"}

    manifests = load_manifests()
    train, test = split_frames(frame, inst)
    ids = manifests[split_id]["test_event_ids"]
    assert [str(e) for e in test["event_id"].tolist()] == ids, f"manifest mismatch {split_id}"
    train = train.reset_index(drop=True)

    t0 = time.time()
    folds = gold_model._station_grouped_folds(train, seed)
    rf_oof = np.full(len(train), np.nan)
    gbm_oof = np.full(len(train), np.nan)
    for fold in range(5):
        mask = folds == fold
        if mask.sum() == 0 or (~mask).sum() == 0:
            continue
        inner_train = train[~mask]
        inner_test = train[mask]
        rf_model = gold_model.RFEBModel(seed, columns=columns).fit(inner_train)
        gbm_model = gold_model.GBMEBModel(seed, columns=columns).fit(inner_train)
        rf_oof[mask] = rf_model.predict(inner_test)
        gbm_oof[mask] = gbm_model.predict(inner_test)
        log(f"{split_id} inner fold {fold} done ({time.time() - t0:.0f}s)")
    assert not np.isnan(rf_oof).any(), f"OOF NaN remains {split_id}"
    assert not np.isnan(gbm_oof).any(), f"OOF NaN remains {split_id}"

    part = pd.DataFrame({
        "event_id": [str(e) for e in train["event_id"].tolist()],
        "rf_oof": rf_oof,
        "gbm_oof": gbm_oof,
        "ens_oof": (rf_oof + gbm_oof) / 2.0,
    })
    part.to_parquet(path, index=False)
    result = {
        "split_id": split_id,
        "seed": seed,
        "n_train": int(len(train)),
        "seconds": round(time.time() - t0, 1),
        "status": "fitted",
    }
    sidecar.write_text(json.dumps(result, indent=2), encoding="utf-8")
    log(f"{split_id} OOF done in {result['seconds']}s")
    return result


def stage_oof(shard: tuple[int, int] | None = None) -> None:
    frame, _acc = gold_model.load_modeling_frame(FEATURES)
    columns = load_columns()
    instances = split_instances()
    if shard is not None:
        i, n = shard
        instances = [inst for k, inst in enumerate(instances) if k % n == i]
    for inst in instances:
        _oof_instance(frame, columns, inst)


# ---------------------------------------------------------------------------
# Stage: apply — fit the gate per instance, blend cached test predictions
# ---------------------------------------------------------------------------

def stage_apply() -> None:
    frame, _acc = gold_model.load_modeling_frame(FEATURES)
    columns = load_columns()
    manifests = load_manifests()
    cache = make_cache(manifests)
    gates_dir = OUT / "gates"
    parts_dir = OUT / "predictions_parts"
    gates_dir.mkdir(parents=True, exist_ok=True)
    parts_dir.mkdir(parents=True, exist_ok=True)

    for inst in split_instances():
        split_id = inst["split_id"]
        seed = inst["fit_seed"]
        ids = manifests[split_id]["test_event_ids"]
        gate_path = gates_dir / f"{split_id}.json"
        part_path = parts_dir / f"{split_id}.parquet"
        if gate_path.exists() and part_path.exists() and cache.get(CANDIDATE, split_id, seed, ids) is not None:
            log(f"{split_id} apply cached")
            continue

        train, test = split_frames(frame, inst)
        assert [str(e) for e in test["event_id"].tolist()] == ids, f"manifest mismatch {split_id}"
        oof = pd.read_parquet(OUT / "oof" / f"{split_id}.parquet")
        train = train.reset_index(drop=True)
        assert [str(e) for e in train["event_id"].tolist()] == oof["event_id"].tolist(), (
            f"OOF/train order mismatch {split_id}"
        )
        ens_oof = oof["ens_oof"].to_numpy(dtype=float)
        kuch_train = train["baseline_slr"].to_numpy(dtype=float)
        obs_train = train["observed_slr"].to_numpy(dtype=float)

        gate = KucheraGate().fit(train, ens_oof, kuch_train, obs_train, seed=seed)

        ens_test = cache.get(INCUMBENT, split_id, seed, ids)
        kuch_test = cache.get("kuchera", split_id, seed, ids)
        assert ens_test is not None and kuch_test is not None, f"cache miss {split_id}"
        kuch_frame = test["baseline_slr"].to_numpy(dtype=float)
        kuch_diff = float(np.max(np.abs(kuch_test - kuch_frame)))

        gated, w = gate.predict(test, ens_test, kuch_test)
        if cache.get(CANDIDATE, split_id, seed, ids) is None:
            cache.put(CANDIDATE, split_id, seed, ids, gated, origin="fitted:run_gate")

        payload = gate.to_dict()
        payload.update({
            "split_id": split_id,
            "seed": seed,
            "n_train": int(len(train)),
            "kuchera_cache_vs_frame_max_abs_diff": kuch_diff,
        })
        gate_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        part = pd.DataFrame({
            "event_id": ids,
            "ens_eb": ens_test,
            "kuchera": kuch_test,
            "w": w,
            "gated": gated,
        })
        part.to_parquet(part_path, index=False)
        log(f"{split_id} gate fitted: leaves={len(gate.leaf_weights_)} "
            f"weights={sorted(gate.leaf_weights_.values())} kuch_diff={kuch_diff:.2e}")


# ---------------------------------------------------------------------------
# Behaviour analysis (descriptive; DESIGN section 3)
# ---------------------------------------------------------------------------

def _mae(obs: np.ndarray, pred: np.ndarray) -> float:
    return float(np.mean(np.abs(pred - obs)))


def behaviour_report(frame: pd.DataFrame, manifests: dict) -> dict[str, Any]:
    meta = frame.set_index("event_id")[
        ["observed_slr", "region", "tmax_warmnose_pwt"]
    ]
    frames = []
    for inst in split_instances():
        split_id = inst["split_id"]
        part = pd.read_parquet(OUT / "predictions_parts" / f"{split_id}.parquet")
        part["split_id"] = split_id
        part["family"] = manifests[split_id]["family"]
        frames.append(part)
    pooled = pd.concat(frames, ignore_index=True)
    pooled.to_parquet(OUT / "predictions.parquet", index=False)
    pooled = pooled.join(meta, on="event_id")

    def group_stats(df: pd.DataFrame) -> dict[str, Any]:
        obs = df["observed_slr"].to_numpy(dtype=float)
        ens = df["ens_eb"].to_numpy(dtype=float)
        kuch = df["kuchera"].to_numpy(dtype=float)
        gated = df["gated"].to_numpy(dtype=float)
        w = df["w"].to_numpy(dtype=float)
        invoked = w > 0
        out: dict[str, Any] = {
            "n": int(len(df)),
            "invocation_rate": float(np.mean(invoked)) if len(df) else 0.0,
            "w_distribution": {
                repr(v): float(np.mean(w == v)) if len(df) else 0.0 for v in BLEND_WEIGHTS
            },
            "mae": {
                "incumbent": _mae(obs, ens),
                "gated": _mae(obs, gated),
                "kuchera": _mae(obs, kuch),
            },
        }
        if invoked.any():
            err_ens = np.abs(ens - obs)
            err_g = np.abs(gated - obs)
            delta = err_g - err_ens
            helped = invoked & (delta < 0)
            hurt = invoked & (delta > 0)
            out["false_switch"] = {
                "n_invoked": int(invoked.sum()),
                "helped_n": int(helped.sum()),
                "hurt_n": int(hurt.sum()),
                "tie_n": int((invoked & (delta == 0)).sum()),
                "mean_delta_helped": float(np.mean(delta[helped])) if helped.any() else 0.0,
                "mean_delta_hurt": float(np.mean(delta[hurt])) if hurt.any() else 0.0,
                "by_w": {
                    repr(v): {
                        "n": int((w == v).sum()),
                        "mean_delta": float(np.mean(delta[w == v])) if (w == v).any() else 0.0,
                    }
                    for v in BLEND_WEIGHTS if v > 0
                },
            }
        return out

    report: dict[str, Any] = {"pooled_all_instances": group_stats(pooled), "by_family": {}}
    for family, group in pooled.groupby("family"):
        report["by_family"][family] = group_stats(group)

    # Feature-defined strata (pooled across instances, labelled as such).
    warm = pooled["tmax_warmnose_pwt"].to_numpy(dtype=float) > 0
    lowpred = pooled["ens_eb"].to_numpy(dtype=float) < LOW_PREDICTED_SLR
    strata: dict[str, Any] = {}
    for name, mask in (
        ("warm_layer_present", warm),
        ("warm_layer_absent", ~warm),
        ("low_predicted_slr", lowpred),
        ("not_low_predicted_slr", ~lowpred),
        ("warm_and_low_predicted", warm & lowpred),
        ("warm_and_not_low_predicted", warm & ~lowpred),
        ("no_warm_and_low_predicted", ~warm & lowpred),
        ("no_warm_and_not_low_predicted", ~warm & ~lowpred),
    ):
        sub = pooled[mask]
        if len(sub):
            strata[name] = group_stats(sub)
    report["strata_feature_defined"] = strata

    # Observed SLR<5 slice: outcome-selected, descriptive only.
    low_obs = pooled["observed_slr"].to_numpy(dtype=float) < 5
    slices: dict[str, Any] = {}
    for name, mask in (("pooled_all_instances", low_obs),):
        sub = pooled[mask]
        if len(sub):
            slices[name] = group_stats(sub)
    for family, group in pooled.groupby("family"):
        mask = group["observed_slr"].to_numpy(dtype=float) < 5
        if mask.any():
            slices[family] = group_stats(group[mask])
    report["observed_slr_lt_5_descriptive"] = slices
    return report


def gate_stability() -> dict[str, Any]:
    from collections import Counter

    split_features: Counter = Counter()
    root_features: Counter = Counter()
    leaf_weight_values: Counter = Counter()
    global_weights: list[float] = []
    n_gates = 0
    for inst in split_instances():
        payload = json.loads((OUT / "gates" / f"{inst['split_id']}.json").read_text(encoding="utf-8"))
        tree = payload["tree"]
        names = payload["input_names"]
        children_left = tree["children_left"]
        for node, feat in enumerate(tree["feature"]):
            if children_left[node] != -1:
                split_features[names[feat]] += 1
                if node == 0:
                    root_features[names[feat]] += 1
        for w in payload["leaf_weights"].values():
            leaf_weight_values[repr(w)] += 1
        global_weights.append(payload["global_weight"])
        n_gates += 1
    return {
        "n_gates": n_gates,
        "split_feature_counts": dict(split_features),
        "root_feature_counts": dict(root_features),
        "leaf_weight_value_counts": dict(leaf_weight_values),
        "global_weight_distribution": {
            repr(v): global_weights.count(v) for v in sorted(set(global_weights))
        },
    }


# ---------------------------------------------------------------------------
# Stage: judge (+ package when adopted)
# ---------------------------------------------------------------------------

def stage_judge() -> dict[str, Any]:
    manifests = load_manifests()
    meta_check = json.loads((QUEUE1 / "queue1_meta.json").read_text(encoding="utf-8"))
    assert manifest_hash(manifests) == meta_check["manifest_sha256"], "manifest hash mismatch"
    cache = make_cache(manifests)
    frame, _acc = gold_model.load_modeling_frame(FEATURES)
    metadata = frame.set_index("event_id")[["observed_slr", "region"]]
    judge = Judge(metadata, manifests)

    inc = load_predictions(cache, manifests, INCUMBENT)
    cand = load_predictions(cache, manifests, CANDIDATE)
    screen = judge.screen(inc, cand)
    full = judge.full(inc, cand)
    behaviour = behaviour_report(frame, manifests)
    stability = gate_stability()

    results: dict[str, Any] = {
        "screen": screen,
        "full": {
            "incumbent": full["incumbent"],
            "candidate": full["candidate"],
            "family_gain": full["family_gain"],
            "four_family_gain": full["four_family_gain"],
            "loro_region_gain": full["loro_region_gain"],
            "slices": full["slices"],
            "adopted": full["adopted"],
            "failed_clauses": full["failed_clauses"],
        },
        "screen_vs_full": {
            "screen_scalar": screen["screen_scalar"],
            "screen_killed": screen["killed"],
            "full_four_family_gain": full["four_family_gain"],
            "full_minus_screen": full["four_family_gain"] - screen["screen_scalar"],
        },
        "behaviour": behaviour,
        "gate_stability": stability,
        "adopted": bool(full["adopted"]),
    }
    if full["adopted"]:
        results["package"] = stage_package(frame)
    (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    log(f"judge done: adopted={full['adopted']} four_family_gain={full['four_family_gain']:+.4f}")
    return results


# ---------------------------------------------------------------------------
# Stage: package (only when adopted; DESIGN section 5)
# ---------------------------------------------------------------------------

def stage_package(frame: pd.DataFrame) -> dict[str, Any]:
    import joblib

    columns = load_columns()
    frame = frame.reset_index(drop=True)
    t0 = time.time()
    folds = gold_model._station_grouped_folds(frame, 0)
    ens_oof = np.full(len(frame), np.nan)
    for fold in range(5):
        mask = folds == fold
        rf_model = gold_model.RFEBModel(0, columns=columns).fit(frame[~mask])
        gbm_model = gold_model.GBMEBModel(0, columns=columns).fit(frame[~mask])
        ens_oof[mask] = (rf_model.predict(frame[mask]) + gbm_model.predict(frame[mask])) / 2.0
    assert not np.isnan(ens_oof).any()
    kuch = frame["baseline_slr"].to_numpy(dtype=float)
    obs = frame["observed_slr"].to_numpy(dtype=float)
    gate = KucheraGate().fit(frame, ens_oof, kuch, obs, seed=0)
    gated, w = gate.predict(frame, ens_oof, kuch)

    cand_dir = OUT / "candidate"
    cand_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {"gate": gate, "input_names": list(GATE_INPUT_NAMES), "blend_weights": list(BLEND_WEIGHTS)},
        cand_dir / "gate.joblib",
    )
    sidecar = gate.to_dict()
    sidecar.update({
        "status": "CANDIDATE — NOT SERVED",
        "fit_protocol": "full-pool station-grouped 5-fold inner OOF (seed 0); gate fitted on OOF inputs",
        "base_model": "v3 champion package (model-v3/champion/) supplies ENS-EB predictions at serving time",
        "judged_results": "see ../results.json",
    })
    (cand_dir / "gate_sidecar.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    stored = pd.DataFrame({
        "event_id": [str(e) for e in frame["event_id"].tolist()],
        "ens_oof": ens_oof,
        "kuchera": kuch,
        "w": w,
        "gated": gated,
    })
    stored.to_parquet(cand_dir / "fullpool_predictions.parquet", index=False)

    # Round-trip (a): reload the packaged gate, reproduce stored predictions.
    loaded = joblib.load(cand_dir / "gate.joblib")["gate"]
    gated_rt, _ = loaded.predict(frame, ens_oof, kuch)
    rt_a = float(np.max(np.abs(gated_rt - gated)))
    # Round-trip (b): judged instances reproduced from exported JSON gates.
    rt_b = 0.0
    manifests = load_manifests()
    cache = make_cache(manifests)
    for inst in split_instances():
        split_id = inst["split_id"]
        payload = json.loads((OUT / "gates" / f"{split_id}.json").read_text(encoding="utf-8"))
        applicator = GateApplicator(payload)
        _train, test = split_frames(frame, inst)
        ids = manifests[split_id]["test_event_ids"]
        ens_test = cache.get(INCUMBENT, split_id, inst["fit_seed"], ids)
        kuch_test = cache.get("kuchera", split_id, inst["fit_seed"], ids)
        gated_json, _w = applicator.predict(test, ens_test, kuch_test)
        part = pd.read_parquet(OUT / "predictions_parts" / f"{split_id}.parquet")
        rt_b = max(rt_b, float(np.max(np.abs(gated_json - part["gated"].to_numpy(dtype=float)))))
    verification = {
        "fullpool_reload_max_abs_diff": rt_a,
        "judged_instances_json_reproduction_max_abs_diff": rt_b,
        "seconds": round(time.time() - t0, 1),
    }
    (OUT / "package_verification.json").write_text(
        json.dumps(verification, indent=2), encoding="utf-8"
    )
    log(f"package done: round-trip a={rt_a:.2e} b={rt_b:.2e}")
    return {"candidate_dir": str(cand_dir), "verification": verification}


# ---------------------------------------------------------------------------
# Stage: report
# ---------------------------------------------------------------------------

def _f(value: Any, digits: int = 4) -> str:
    return f"{float(value):.{digits}f}"


def stage_report(results: dict[str, Any]) -> None:
    full = results["full"]
    inc = full["incumbent"]
    cand = full["candidate"]
    screen = results["screen"]
    lines: list[str] = []
    lines.append("# Queue item 5 — Feature-Based Kuchera Gate: Results")
    lines.append("")
    lines.append("Pre-registration: DESIGN.md (same directory). Rules applied mechanically.")
    lines.append("")
    verdict = "ADOPTED" if results["adopted"] else "NOT ADOPTED"
    lines.append(f"## Verdict: {verdict}")
    lines.append("")
    if results["adopted"]:
        lines.append("All clauses of the amended adoption rule passed; the candidate "
                     "package is built (see Packaging).")
    else:
        lines.append("Failed clauses:")
        for clause in full["failed_clauses"]:
            lines.append(f"- {clause}")
    lines.append("")
    lines.append("## Full judging (SLR MAE, frozen manifests, paired event-by-event)")
    lines.append("")
    lines.append("| family | incumbent ENS-EB | gated candidate | gain |")
    lines.append("|---|---:|---:|---:|")
    for fam in ("randomised", "loro", "temporal", "stations"):
        lines.append(
            f"| {fam} | {_f(inc['family'][fam])} | {_f(cand['family'][fam])} | "
            f"{full['family_gain'][fam]:+.4f} |"
        )
    lines.append(
        f"| **four-family mean** | **{_f(inc['four_family_mean'])}** | "
        f"**{_f(cand['four_family_mean'])}** | **{full['four_family_gain']:+.4f}** |"
    )
    lines.append("")
    lines.append("LORO by region (gain = incumbent − candidate):")
    lines.append("")
    lines.append("| region | incumbent | candidate | gain |")
    lines.append("|---|---:|---:|---:|")
    for region in sorted(inc["loro_regions"]):
        lines.append(
            f"| {region} | {_f(inc['loro_regions'][region])} | "
            f"{_f(cand['loro_regions'][region])} | {full['loro_region_gain'][region]:+.4f} |"
        )
    lines.append("")
    lines.append("## Screen vs full (screen-calibration evidence)")
    lines.append("")
    lines.append(f"- Screen scalar: {screen['screen_scalar']:+.4f}; killed: {screen['killed']}"
                 + (f" ({'; '.join(screen['kill_clauses'])})" if screen["kill_clauses"] else ""))
    lines.append(f"- Full four-family gain: {full['four_family_gain']:+.4f}; "
                 f"full − screen: {results['screen_vs_full']['full_minus_screen']:+.4f}")
    lines.append("")
    beh = results["behaviour"]
    pooled = beh["pooled_all_instances"]
    lines.append("## Behaviour (descriptive)")
    lines.append("")
    lines.append(f"- Invocation rate (w > 0), pooled across instances: "
                 f"{pooled['invocation_rate']:.3f} of {pooled['n']} prediction records.")
    lines.append(f"- w distribution: {pooled['w_distribution']}")
    if "false_switch" in pooled:
        fs = pooled["false_switch"]
        lines.append(f"- False-switch accounting on invoked records: helped {fs['helped_n']}, "
                     f"hurt {fs['hurt_n']}, ties {fs['tie_n']}; mean Δ|err| helped "
                     f"{fs['mean_delta_helped']:+.4f}, hurt {fs['mean_delta_hurt']:+.4f}.")
    lines.append("")
    lines.append("Feature-defined strata (pooled across instances; MAE incumbent / gated / Kuchera):")
    lines.append("")
    lines.append("| stratum | n | invocation | inc | gated | kuchera |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for name, entry in beh["strata_feature_defined"].items():
        m = entry["mae"]
        lines.append(f"| {name} | {entry['n']} | {entry['invocation_rate']:.3f} | "
                     f"{_f(m['incumbent'])} | {_f(m['gated'])} | {_f(m['kuchera'])} |")
    lines.append("")
    lines.append("Observed SLR<5 slice (OUTCOME-SELECTED — descriptive only):")
    lines.append("")
    lines.append("| scope | n | invocation | inc | gated | kuchera |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for name, entry in beh["observed_slr_lt_5_descriptive"].items():
        m = entry["mae"]
        lines.append(f"| {name} | {entry['n']} | {entry['invocation_rate']:.3f} | "
                     f"{_f(m['incumbent'])} | {_f(m['gated'])} | {_f(m['kuchera'])} |")
    lines.append("")
    stab = results["gate_stability"]
    lines.append("## Gate stability across the 28 folds")
    lines.append("")
    lines.append(f"- Split-feature counts: {stab['split_feature_counts']}")
    lines.append(f"- Root-feature counts: {stab['root_feature_counts']}")
    lines.append(f"- Leaf-weight value counts: {stab['leaf_weight_value_counts']}")
    lines.append(f"- Global fallback weight distribution: {stab['global_weight_distribution']}")
    lines.append("")
    if results["adopted"]:
        pkg = results["package"]
        lines.append("## Packaging")
        lines.append("")
        lines.append(f"- Candidate artefact: `{pkg['candidate_dir']}` (gate.joblib + "
                     "gate_sidecar.json + fullpool_predictions.parquet) — CANDIDATE, NOT SERVED.")
        lines.append(f"- Round-trip: full-pool reload max abs diff "
                     f"{pkg['verification']['fullpool_reload_max_abs_diff']:.2e}; judged-instance "
                     f"JSON reproduction max abs diff "
                     f"{pkg['verification']['judged_instances_json_reproduction_max_abs_diff']:.2e}.")
        lines.append("")
    lines.append("## Artefacts")
    lines.append("")
    lines.append("- DESIGN.md (pre-registration), results.json (full machine record), "
                 "predictions.parquet (per-instance ens_eb / kuchera / w / gated), "
                 "gates/*.json (per-instance fitted trees + leaf weights), oof/*.parquet "
                 "(inner OOF predictions).")
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log("report written")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", default="all",
                        choices=("oof", "apply", "judge", "report", "all"))
    parser.add_argument("--shard", default=None, help="i/n for the oof stage")
    args = parser.parse_args()
    shard = None
    if args.shard:
        i, n = args.shard.split("/")
        shard = (int(i), int(n))
    if args.stage in ("oof", "all"):
        stage_oof(shard)
    if args.stage in ("apply", "all"):
        stage_apply()
    results = None
    if args.stage in ("judge", "all"):
        results = stage_judge()
    if args.stage in ("report", "all"):
        if results is None:
            results = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
        stage_report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
