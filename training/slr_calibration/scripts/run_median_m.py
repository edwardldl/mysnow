#!/usr/bin/env python3
"""Queue item 3 driver: median experiment M.

Pre-registration: data/slr-training-data/screened-v2/model-v1/model-v3-median/DESIGN.md
Stages: fit -> judge -> report; package runs inside judge when the
mechanical adoption rule passes.  All stages are resumable: fitted
predictions live in the queue-1 keyed cache, per-instance masked /
verification artefacts beside this experiment's outputs.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "training/slr_calibration/src"))

from slr_calibration import gold_model  # noqa: E402
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
OUT = BASE / "model-v3-median"
CACHE_ROOT = QUEUE1 / "cache"
TCLW_COLUMNS = ["tclw_pwt", "tclw_max"]
WESTERN = ("rockies", "pacific-northwest", "sierra-nevada", "alaska")

INCUMBENT = "ens_eb"
CANDIDATE = "ens_med_m"
DIAGNOSTICS = {"d1": "ens_med_d1", "d2": "ens_med_d2"}


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------------------
# Shared loading
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


# ---------------------------------------------------------------------------
# Stage: fit (per split instance; parallel workers)
# ---------------------------------------------------------------------------

_W: dict[str, Any] = {}


def _worker_init() -> None:
    frame, _acc = gold_model.load_modeling_frame(FEATURES)
    _W["frame"] = frame
    _W["columns"] = load_columns()
    _W["manifests"] = load_manifests()
    _W["cache"] = make_cache(_W["manifests"])


def _fit_instance(inst: dict) -> dict:
    from slr_calibration.median_model import AbsoluteErrorGBMModel

    split_id = inst["split_id"]
    seed = inst["fit_seed"]
    parts_dir = OUT / "fit_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = parts_dir / f"{split_id}.json"
    masked_path = OUT / "masked" / f"{split_id}.parquet"
    if sidecar_path.exists() and masked_path.exists():
        return {"split_id": split_id, "status": "cached"}

    frame = _W["frame"]
    columns = _W["columns"]
    cache: PredictionCache = _W["cache"]
    ids = _W["manifests"][split_id]["test_event_ids"]
    train, test = split_frames(frame, inst)
    assert [str(e) for e in test["event_id"].tolist()] == ids, f"manifest mismatch {split_id}"

    t0 = time.time()
    rf_model = gold_model.RFEBModel(seed, columns=columns).fit(train)
    gbm_model = gold_model.GBMEBModel(seed, columns=columns).fit(train)
    med_model = AbsoluteErrorGBMModel(seed, columns=columns).fit(train)

    _, rf_eb_refit = rf_model.predict_parts(test)
    _, gbm_eb_refit = gbm_model.predict_parts(test)
    med_base, med_eb = med_model.predict_parts(test)
    med_mean_eb = med_model.predict_mean_eb(test)

    # Verification of the refits against the queue-1 cache (unmasked).
    verify: dict[str, float] = {}
    for name, refit in (("rf_eb", rf_eb_refit), ("gbm_eb", gbm_eb_refit)):
        cached = cache.get(name, split_id, seed, ids)
        assert cached is not None, f"incumbent cache miss {name} {split_id}"
        verify[f"{name}_max_abs_diff"] = float(np.max(np.abs(refit - cached)))

    # Store the median component's predictions in the keyed cache.
    for model, values in (
        ("gbm_med", med_base),
        ("gbm_med_eb", med_eb),
        ("gbm_med_mean_eb", med_mean_eb),
    ):
        if cache.get(model, split_id, seed, ids) is None:
            cache.put(model, split_id, seed, ids, values, origin="fitted:run_median_m")

    # Masked-tclw condition: same fitted models, tclw pair NaN at
    # predict time only (DESIGN section 4).
    test_masked = test.copy()
    test_masked[TCLW_COLUMNS] = np.nan
    _, rf_eb_m = rf_model.predict_parts(test_masked)
    _, gbm_eb_m = gbm_model.predict_parts(test_masked)
    _base_m, med_eb_m = med_model.predict_parts(test_masked)
    masked = pd.DataFrame({
        "event_id": ids,
        "rf_eb": rf_eb_m,
        "gbm_eb": gbm_eb_m,
        "ens_eb": (rf_eb_m + gbm_eb_m) / 2.0,
        "gbm_med_eb": med_eb_m,
        "ens_med_m": (rf_eb_m + med_eb_m) / 2.0,
    })
    masked_path.parent.mkdir(parents=True, exist_ok=True)
    masked.to_parquet(masked_path, index=False)

    result = {
        "split_id": split_id,
        "seed": seed,
        "n_test": len(ids),
        "verify": verify,
        "seconds": round(time.time() - t0, 1),
        "status": "fitted",
    }
    if split_id == "unseenStations":
        hierarchy = med_model.hierarchy_median(test)
        d3 = pd.DataFrame({
            "event_id": ids,
            "hierarchy": [">".join(h) for h in hierarchy],
            "site_node_used": ["local" in h for h in hierarchy],
        })
        d3.to_parquet(OUT / "d3_hierarchy.parquet", index=False)
    sidecar_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def stage_fit(workers: int = 4, shard: tuple[int, int] | None = None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    instances = split_instances()
    if shard is not None:
        # Serial in-process execution for one shard. ProcessPoolExecutor
        # workers are daemonic, which makes joblib fall back to a
        # threading backend inside them; the RF fits then thrash
        # (measured: 4 pool workers burned ~48k core-seconds with zero
        # completed instances). Independent main processes keep joblib
        # on its process backend, as in the queue-1 seeding.
        index, count = shard
        instances = instances[index::count]
        _worker_init()
        for inst in instances:
            result = _fit_instance(inst)
            log(f"fit {result['split_id']}: {result['status']}"
                + (f" in {result['seconds']}s" if result["status"] == "fitted" else ""))
        return
    with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init) as pool:
        futures = {pool.submit(_fit_instance, inst): inst["split_id"] for inst in instances}
        for future in as_completed(futures):
            result = future.result()
            log(f"fit {result['split_id']}: {result['status']}"
                + (f" in {result['seconds']}s" if result["status"] == "fitted" else ""))


# ---------------------------------------------------------------------------
# Stage: judge
# ---------------------------------------------------------------------------

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


def _aggregate_family(per_instance: dict[str, float], manifests: dict) -> dict[str, float]:
    families: dict[str, list[str]] = {}
    for split_id, entry in manifests.items():
        families.setdefault(entry["family"], []).append(split_id)
    out: dict[str, float] = {}
    for name, split_ids in families.items():
        if name in ("loro", "randomised"):
            out[name] = float(np.mean([per_instance[s] for s in split_ids]))
        else:
            out[name] = per_instance[split_ids[0]]
    return out


def snowfall_family_maes(
    predictions: dict[str, pd.Series], metadata: pd.DataFrame, manifests: dict
) -> dict[str, Any]:
    """Family snowfall MAEs (mm): |pred_SLR x SWE - fresh snow|."""
    per_instance: dict[str, float] = {}
    for split_id, series in predictions.items():
        meta = metadata.loc[series.index]
        err = np.abs(
            series.to_numpy(dtype=float) * meta["fresh_swe_mm"].to_numpy(dtype=float)
            - meta["fresh_snow_mm"].to_numpy(dtype=float)
        )
        per_instance[split_id] = float(np.mean(err))
    family = _aggregate_family(per_instance, manifests)
    return {
        "per_instance": per_instance,
        "family": family,
        "four_family_mean": float(np.mean([family[k] for k in ("randomised", "loro", "temporal", "stations")])),
    }


SLICES = {
    "slr_lt_5": lambda meta: meta["observed_slr"].to_numpy(dtype=float) < 5,
    "slr_gt_20": lambda meta: meta["observed_slr"].to_numpy(dtype=float) > 20,
    "wesf_2.79_5.59": lambda meta: (
        (meta["fresh_swe_mm"].to_numpy(dtype=float) >= 2.79)
        & (meta["fresh_swe_mm"].to_numpy(dtype=float) <= 5.59)
    ),
    "western_gt_1500m": lambda meta: (
        meta["region"].isin(WESTERN).to_numpy()
        & (meta["elevation_m"].to_numpy(dtype=float) > 1500)
    ),
    "alaska": lambda meta: (meta["region"] == "alaska").to_numpy(),
}


def _slice_mae(series: pd.Series, metadata: pd.DataFrame, mask: np.ndarray) -> float:
    obs = metadata.loc[series.index, "observed_slr"].to_numpy(dtype=float)
    return float(np.mean(np.abs(series.to_numpy(dtype=float)[mask] - obs[mask])))


def slice_report(
    incumbent: dict[str, pd.Series],
    candidate: dict[str, pd.Series],
    metadata: pd.DataFrame,
    manifests: dict,
) -> list[dict[str, Any]]:
    """Per-family slice MAEs for both models (DESIGN section 4).

    temporal/stations: the single fold.  randomised: mean of per-rep
    slice MAEs.  loro: pooled over the six folds (each event occurs in
    exactly one fold).
    """
    out: list[dict[str, Any]] = []
    reps = [f"random60_40_rep{i}" for i in range(20)]
    loro_ids = [s for s in manifests if s.startswith("loro_")]
    for name, mask_fn in SLICES.items():
        for family, split_ids, mode in (
            ("temporal", ["temporalWY2026"], "single"),
            ("stations", ["unseenStations"], "single"),
            ("randomised", reps, "mean_reps"),
            ("loro", loro_ids, "pooled"),
        ):
            if mode == "pooled":
                inc_cat = pd.concat([incumbent[s] for s in split_ids])
                cand_cat = pd.concat([candidate[s] for s in split_ids])
                meta = metadata.loc[inc_cat.index]
                mask = mask_fn(meta)
                n = int(mask.sum())
                entry = {
                    "slice": name, "family": family, "n": n,
                    "incumbent_mae": _slice_mae(inc_cat, metadata, mask) if n else None,
                    "candidate_mae": _slice_mae(cand_cat, metadata, mask) if n else None,
                }
            else:
                inc_vals, cand_vals, ns = [], [], []
                for split_id in split_ids:
                    meta = metadata.loc[incumbent[split_id].index]
                    mask = mask_fn(meta)
                    ns.append(int(mask.sum()))
                    if mask.sum():
                        inc_vals.append(_slice_mae(incumbent[split_id], metadata, mask))
                        cand_vals.append(_slice_mae(candidate[split_id], metadata, mask))
                entry = {
                    "slice": name, "family": family,
                    "n": int(ns[0]) if mode == "single" else int(np.sum(ns)),
                    "incumbent_mae": float(np.mean(inc_vals)) if inc_vals else None,
                    "candidate_mae": float(np.mean(cand_vals)) if cand_vals else None,
                }
            entry["mae_diff"] = (
                None if entry["candidate_mae"] is None
                else entry["candidate_mae"] - entry["incumbent_mae"]
            )
            out.append(entry)
    return out


def masked_report(metadata: pd.DataFrame, manifests: dict) -> dict[str, Any]:
    frames = []
    for inst in split_instances():
        split_id = inst["split_id"]
        part = pd.read_parquet(OUT / "masked" / f"{split_id}.parquet")
        part["split_id"] = split_id
        part["family"] = manifests[split_id]["family"]
        frames.append(part)
    pooled = pd.concat(frames, ignore_index=True)
    obs = metadata.loc[pooled["event_id"], "observed_slr"].to_numpy(dtype=float)
    result: dict[str, Any] = {
        "n_records": int(len(pooled)),
        "pooled": {
            "incumbent_mae": float(np.mean(np.abs(pooled["ens_eb"].to_numpy() - obs))),
            "candidate_mae": float(np.mean(np.abs(pooled["ens_med_m"].to_numpy() - obs))),
        },
    }
    result["pooled"]["mae_diff"] = result["pooled"]["candidate_mae"] - result["pooled"]["incumbent_mae"]
    per_instance: dict[str, dict[str, float]] = {"incumbent": {}, "candidate": {}}
    for split_id, group in pooled.groupby("split_id"):
        o = metadata.loc[group["event_id"], "observed_slr"].to_numpy(dtype=float)
        per_instance["incumbent"][split_id] = float(np.mean(np.abs(group["ens_eb"].to_numpy() - o)))
        per_instance["candidate"][split_id] = float(np.mean(np.abs(group["ens_med_m"].to_numpy() - o)))
    result["family"] = {
        model: _aggregate_family(values, manifests) for model, values in per_instance.items()
    }
    pooled.to_parquet(OUT / "masked_predictions.parquet", index=False)
    return result


def d3_report(
    incumbent: dict[str, pd.Series],
    candidate: dict[str, pd.Series],
    components: dict[str, dict[str, pd.Series]],
    metadata: pd.DataFrame,
) -> dict[str, Any]:
    split_id = "unseenStations"
    ids = incumbent[split_id].index
    obs = metadata.loc[ids, "observed_slr"].to_numpy(dtype=float)
    hierarchy = pd.read_parquet(OUT / "d3_hierarchy.parquet").set_index("event_id").loc[ids]
    site_used = hierarchy["site_node_used"].to_numpy(dtype=bool)

    def errors(series: pd.Series) -> np.ndarray:
        return series.to_numpy(dtype=float) - obs

    med_err = errors(components["gbm_med_eb"][split_id])
    old_err = errors(components["gbm_eb"][split_id])
    rf_err = errors(components["rf_eb"][split_id])
    paired = np.abs(med_err) - np.abs(old_err)

    def paired_summary(mask: np.ndarray) -> dict[str, float]:
        return {
            "n": int(mask.sum()),
            "mean_paired_abs_change": float(np.mean(paired[mask])) if mask.sum() else 0.0,
            "median_paired_abs_change": float(np.median(paired[mask])) if mask.sum() else 0.0,
            "share_improved": float(np.mean(paired[mask] < 0)) if mask.sum() else 0.0,
        }

    table = pd.DataFrame({
        "event_id": ids,
        "observed_slr": obs,
        "site_node_used": site_used,
        "rf_eb": components["rf_eb"][split_id].to_numpy(),
        "gbm_eb_old": components["gbm_eb"][split_id].to_numpy(),
        "gbm_med_eb": components["gbm_med_eb"][split_id].to_numpy(),
        "ens_incumbent": incumbent[split_id].to_numpy(),
        "ens_m": candidate[split_id].to_numpy(),
        "paired_abs_change_med_minus_old": paired,
    })
    table.to_parquet(OUT / "d3_unseen_stations.parquet", index=False)
    return {
        "split": split_id,
        "component_mae": {
            "rf_eb": float(np.mean(np.abs(rf_err))),
            "gbm_eb_old": float(np.mean(np.abs(old_err))),
            "gbm_med_eb": float(np.mean(np.abs(med_err))),
            "ens_incumbent": float(np.mean(np.abs(errors(incumbent[split_id])))),
            "ens_m": float(np.mean(np.abs(errors(candidate[split_id])))),
        },
        "paired_med_minus_old": {
            "all": paired_summary(np.ones(len(ids), dtype=bool)),
            "site_node_used": paired_summary(site_used),
            "site_node_absent": paired_summary(~site_used),
        },
        "error_correlation": {
            "median_gbm_vs_rf": float(np.corrcoef(med_err, rf_err)[0, 1]),
            "incumbent_gbm_vs_rf": float(np.corrcoef(old_err, rf_err)[0, 1]),
        },
    }


def stage_judge() -> dict[str, Any]:
    manifests = load_manifests()
    meta_check = json.loads((QUEUE1 / "queue1_meta.json").read_text(encoding="utf-8"))
    assert manifest_hash(manifests) == meta_check["manifest_sha256"], "manifest hash mismatch"
    cache = make_cache(manifests)
    frame, _acc = gold_model.load_modeling_frame(FEATURES)
    metadata = frame.set_index("event_id")[
        ["observed_slr", "region", "fresh_swe_mm", "fresh_snow_mm", "elevation_m"]
    ]
    judge = Judge(metadata, manifests)

    models = {
        name: load_predictions(cache, manifests, name)
        for name in ("ens_eb", "rf_eb", "gbm_eb", "gbm_med", "gbm_med_eb",
                     "gbm_med_mean_eb", "ens_med_m", "ens_med_d1", "ens_med_d2")
    }
    inc = models[INCUMBENT]
    cand = models[CANDIDATE]

    gains_m = judge.gains(inc, cand)
    screen_m = judge.screen(inc, cand)
    screens = {"m": screen_m}
    full_gains = {"m": gains_m["four_family_gain"]}
    for key, model in DIAGNOSTICS.items():
        screens[key] = judge.screen(inc, models[model])
        full_gains[key] = judge.gains(inc, models[model])["four_family_gain"]
    screen_vs_full = {
        key: {
            "screen_scalar": screens[key]["screen_scalar"],
            "screen_killed": screens[key]["killed"],
            "screen_kill_clauses": screens[key]["kill_clauses"],
            "full_four_family_gain": full_gains[key],
            "full_minus_screen": full_gains[key] - screens[key]["screen_scalar"],
        }
        for key in screens
    }

    snowfall = {
        "incumbent": snowfall_family_maes(inc, metadata, manifests),
        "candidate": snowfall_family_maes(cand, metadata, manifests),
    }
    slices = slice_report(inc, cand, metadata, manifests)
    masked = masked_report(metadata, manifests)
    d3 = d3_report(inc, cand, models, metadata)
    hgb_only = {
        name: judge.family_maes(models[name])
        for name in ("gbm_eb", "gbm_med_eb", "gbm_med_mean_eb", "gbm_med")
    }

    # Refit verification aggregate (masked-stage refits vs cache).
    verify_rows = []
    for inst in split_instances():
        sidecar = json.loads((OUT / "fit_parts" / f"{inst['split_id']}.json").read_text())
        verify_rows.append({"split_id": inst["split_id"], **sidecar["verify"]})
    verify = {
        "per_instance": verify_rows,
        "max_rf_eb_abs_diff": max(r["rf_eb_max_abs_diff"] for r in verify_rows),
        "max_gbm_eb_abs_diff": max(r["gbm_eb_max_abs_diff"] for r in verify_rows),
    }
    (OUT / "refit_verification.json").write_text(json.dumps(verify, indent=2), encoding="utf-8")

    # --- Adoption rule (DESIGN section 6), applied mechanically. ---
    fg = gains_m["family_gain"]
    region_gain = gains_m["loro_region_gain"]
    clauses: list[dict[str, Any]] = []

    def clause(ok: bool, text: str, value: float) -> None:
        clauses.append({"pass": bool(ok), "text": text, "value": value})

    clause(gains_m["four_family_gain"] >= 0.03,
           "four-family mean gain >= 0.03", gains_m["four_family_gain"])
    clause(fg["loro"] >= 0.02, "pooled LORO gain >= 0.02", fg["loro"])
    clause(min(region_gain.values()) >= -0.05,
           "no held-out region gain < -0.05", min(region_gain.values()))
    clause(fg["temporal"] >= 0.0, "temporal gain >= 0", fg["temporal"])
    clause(fg["stations"] >= 0.0, "unseen-station gain >= 0", fg["stations"])
    clause(fg["randomised"] >= -0.02, "randomised gain >= -0.02", fg["randomised"])
    slice_failures = [
        e for e in slices
        if e["family"] in ("temporal", "stations") and e["n"] >= 200
        and e["mae_diff"] is not None and e["mae_diff"] > 0.20
    ]
    clause(not slice_failures,
           "no temporal/stations slice (n>=200) worsens by > 0.20",
           max((e["mae_diff"] for e in slices
                if e["family"] in ("temporal", "stations") and e["n"] >= 200
                and e["mae_diff"] is not None), default=0.0))
    clause(masked["pooled"]["mae_diff"] <= 0.03,
           "masked-tclw pooled MAE diff <= +0.03", masked["pooled"]["mae_diff"])
    adopted = all(c["pass"] for c in clauses)
    kill_rule_withdrawn = bool(adopted and screen_m["killed"])

    results = {
        "experiment": "queue item 3: median experiment M",
        "design": "model-v3-median/DESIGN.md (pre-registered 2026-11-02)",
        "incumbent": gains_m["incumbent"],
        "candidate_m": gains_m["candidate"],
        "family_gain": fg,
        "four_family_gain": gains_m["four_family_gain"],
        "loro_region_gain": region_gain,
        "screen_vs_full": screen_vs_full,
        "snowfall_mae": snowfall,
        "slices": slices,
        "masked_tclw": masked,
        "diagnostics": {
            "d1_family": judge.family_maes(models[DIAGNOSTICS["d1"]]),
            "d2_family": judge.family_maes(models[DIAGNOSTICS["d2"]]),
            "d3_unseen_stations": d3,
            "hgb_component_family_maes": hgb_only,
        },
        "refit_verification": {
            "max_rf_eb_abs_diff": verify["max_rf_eb_abs_diff"],
            "max_gbm_eb_abs_diff": verify["max_gbm_eb_abs_diff"],
        },
        "adoption": {
            "clauses": clauses,
            "adopted": adopted,
            "screen_kill_rule_withdrawn": kill_rule_withdrawn,
        },
    }
    (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    # Predictions deliverable (long format).
    pred_frames = []
    for name in ("ens_eb", "ens_med_m", "ens_med_d1", "ens_med_d2", "gbm_eb", "gbm_med_eb"):
        for split_id, series in models[name].items():
            pred_frames.append(pd.DataFrame({
                "split_id": split_id, "event_id": series.index,
                "model": name, "prediction": series.to_numpy(dtype=float),
            }))
    pd.concat(pred_frames, ignore_index=True).to_parquet(OUT / "predictions.parquet", index=False)
    log(f"judge: four-family gain {gains_m['four_family_gain']:+.4f}, adopted={adopted}")
    return results


# ---------------------------------------------------------------------------
# Stage: report (+ package when adopted)
# ---------------------------------------------------------------------------

def _f(value: Any, digits: int = 4) -> str:
    return "—" if value is None else f"{value:+.{digits}f}" if isinstance(value, float) else str(value)


def stage_report(results: dict[str, Any]) -> None:
    inc, cand = results["incumbent"], results["candidate_m"]
    lines = [
        "# Queue item 3 — Median Experiment M: Report",
        "",
        "Pre-registration: `DESIGN.md` (same directory), written before any",
        "fitting or scoring. All rules applied mechanically.",
        "",
        "## Headline (SLR MAE, frozen v3 folds, paired event-by-event)",
        "",
        "| family | incumbent ENS-EB | candidate M | gain (inc − M) |",
        "|---|---:|---:|---:|",
    ]
    for fam in ("randomised", "loro", "temporal", "stations"):
        lines.append(
            f"| {fam} | {inc['family'][fam]:.4f} | {cand['family'][fam]:.4f} "
            f"| {results['family_gain'][fam]:+.4f} |"
        )
    lines.append(
        f"| **four-family mean** | **{inc['four_family_mean']:.4f}** | "
        f"**{cand['four_family_mean']:.4f}** | **{results['four_family_gain']:+.4f}** |"
    )
    lines += ["", "## LORO by region (gain = incumbent − M)", "",
              "| region | incumbent | M | gain |", "|---|---:|---:|---:|"]
    for region, gain in results["loro_region_gain"].items():
        lines.append(
            f"| {region} | {inc['loro_regions'][region]:.4f} | "
            f"{cand['loro_regions'][region]:.4f} | {gain:+.4f} |"
        )
    lines += ["", "## Screen (advisory for this run, DESIGN section 5)", "",
              "| candidate | screen scalar | screen killed | full four-family gain | full − screen |",
              "|---|---:|---|---:|---:|"]
    for key, entry in results["screen_vs_full"].items():
        lines.append(
            f"| {key} | {entry['screen_scalar']:+.4f} | {entry['screen_killed']} | "
            f"{entry['full_four_family_gain']:+.4f} | {entry['full_minus_screen']:+.4f} |"
        )
    if results["adoption"]["screen_kill_rule_withdrawn"]:
        lines.append("")
        lines.append("M passed the full adoption gate after a screen rejection: "
                     "**the screen kill rule stands withdrawn** (DESIGN section 5).")
    lines += ["", "## Snowfall MAE (mm, observed SWE)", "",
              "| family | incumbent | M |", "|---|---:|---:|"]
    for fam in ("randomised", "loro", "temporal", "stations"):
        lines.append(
            f"| {fam} | {results['snowfall_mae']['incumbent']['family'][fam]:.4f} | "
            f"{results['snowfall_mae']['candidate']['family'][fam]:.4f} |"
        )
    lines += ["", "## Slices (SLR MAE; diff = M − incumbent)", "",
              "| slice | family | n | incumbent | M | diff |", "|---|---|---:|---:|---:|---:|"]
    for e in results["slices"]:
        lines.append(
            f"| {e['slice']} | {e['family']} | {e['n']} | "
            f"{'—' if e['incumbent_mae'] is None else format(e['incumbent_mae'], '.4f')} | "
            f"{'—' if e['candidate_mae'] is None else format(e['candidate_mae'], '.4f')} | "
            f"{'—' if e['mae_diff'] is None else format(e['mae_diff'], '+.4f')} |"
        )
    masked = results["masked_tclw"]
    lines += ["", "## Masked-tclw condition (pooled over all 28 instances)", "",
              f"- records: {masked['n_records']}",
              f"- incumbent pooled MAE: {masked['pooled']['incumbent_mae']:.4f}",
              f"- M pooled MAE: {masked['pooled']['candidate_mae']:.4f}",
              f"- diff (M − incumbent): {masked['pooled']['mae_diff']:+.4f} "
              f"(adoption limit +0.03)", ""]
    d3 = results["diagnostics"]["d3_unseen_stations"]
    lines += ["## Diagnostics (scored, not adoptable)", "",
              "### D3 — unseen-station components", "",
              "| component | MAE |", "|---|---:|"]
    for name, mae in d3["component_mae"].items():
        lines.append(f"| {name} | {mae:.4f} |")
    lines += ["",
              f"- paired |err| change, median component − old GBM+EB (all): "
              f"mean {d3['paired_med_minus_old']['all']['mean_paired_abs_change']:+.4f}, "
              f"median {d3['paired_med_minus_old']['all']['median_paired_abs_change']:+.4f}, "
              f"share improved {d3['paired_med_minus_old']['all']['share_improved']:.3f} "
              f"(n={d3['paired_med_minus_old']['all']['n']})",
              f"- site node used: mean {d3['paired_med_minus_old']['site_node_used']['mean_paired_abs_change']:+.4f} "
              f"(n={d3['paired_med_minus_old']['site_node_used']['n']}); "
              f"absent: mean {d3['paired_med_minus_old']['site_node_absent']['mean_paired_abs_change']:+.4f} "
              f"(n={d3['paired_med_minus_old']['site_node_absent']['n']})",
              f"- error correlation: median GBM vs RF {d3['error_correlation']['median_gbm_vs_rf']:.4f}; "
              f"incumbent GBM vs RF {d3['error_correlation']['incumbent_gbm_vs_rf']:.4f}",
              "",
              "### D1 / D2 ensembles and HGB components (four-family MAE)", "",
              "| model | four-family MAE |", "|---|---:|",
              f"| incumbent ens_eb | {inc['four_family_mean']:.4f} |",
              f"| M (ens_med_m) | {cand['four_family_mean']:.4f} |",
              f"| D1 ens_med_d1 | {results['diagnostics']['d1_family']['four_family_mean']:.4f} |",
              f"| D2 ens_med_d2 | {results['diagnostics']['d2_family']['four_family_mean']:.4f} |",
              f"| gbm_eb (incumbent component) | {results['diagnostics']['hgb_component_family_maes']['gbm_eb']['four_family_mean']:.4f} |",
              f"| gbm_med_eb (M component) | {results['diagnostics']['hgb_component_family_maes']['gbm_med_eb']['four_family_mean']:.4f} |",
              f"| gbm_med_mean_eb (D2 component) | {results['diagnostics']['hgb_component_family_maes']['gbm_med_mean_eb']['four_family_mean']:.4f} |",
              f"| gbm_med (raw median base) | {results['diagnostics']['hgb_component_family_maes']['gbm_med']['four_family_mean']:.4f} |",
              "",
              "## Refit verification (masked-stage refits vs queue-1 cache)", "",
              f"- max |rf_eb refit − cache|: {results['refit_verification']['max_rf_eb_abs_diff']:.2e}",
              f"- max |gbm_eb refit − cache|: {results['refit_verification']['max_gbm_eb_abs_diff']:.2e}",
              "",
              "## Adoption verdict (DESIGN section 6, mechanical)", ""]
    for c in results["adoption"]["clauses"]:
        lines.append(f"- [{'PASS' if c['pass'] else 'FAIL'}] {c['text']} (value {c['value']:+.4f})")
    lines += ["", f"**Adopted: {results['adoption']['adopted']}**", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    log("report written")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", default="all", choices=("fit", "judge", "all"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--shard", default=None,
                        help="I/N: fit only instances[I::N], serially in-process")
    args = parser.parse_args()
    shard = None
    if args.shard:
        i, n = args.shard.split("/")
        shard = (int(i), int(n))
    if args.stage in ("fit", "all"):
        stage_fit(workers=args.workers, shard=shard)
    if args.stage in ("judge", "all"):
        results = stage_judge()
        stage_report(results)
        if results["adoption"]["adopted"]:
            from run_median_package import stage_package
            stage_package(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
