"""HRRR correction study runner: registered fitting of C1-C3.

Executes model-v3-hrrr-correction/REGISTRATION.md exactly (round-14
certified): nested whole-pipeline validation over the 28 frozen
queue-1 instances, mirror-exclusion lambda tuning
(lambda in {3, 10, 30, 100}), candidates C1 -> C2 -> C3 judged
against C0 (uncorrected ENS-EB on HRRR inputs) under the R3/R4
decision rule with the R6(v) dual cluster bootstrap.

Stages: fit -> judge -> report.  Serial compute only.  The fit
stage is resumable per instance and per tuning split; every ENS-EB
refit and adapter fit is appended to fit/fit_ledger.jsonl.
Measurement/fitting only within the study: production calibration
stays disabled; an adopted candidate is packaged as an unserved
candidate artefact, never wired into serving.
"""
from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "training" / "slr_calibration" / "src"))

from slr_calibration import gold_model, hrrr_correction as hc  # noqa: E402
from slr_calibration.prediction_cache import (  # noqa: E402
    PredictionCache,
    cohort_hash,
    feature_hash,
    manifest_hash,
    split_instances,
)

BASE = REPO / "data" / "slr-training-data" / "screened-v2" / "model-v1"
FEATURES = BASE / "model-v3-final" / "features_v3.parquet"
COLUMNS_JSON = BASE / "model-v3" / "screens_v2" / "final_combination.json"
QUEUE1 = BASE / "model-v3-queue1"
TRANSFER = BASE / "model-v3-hrrr-transfer"
OUT = BASE / "model-v3-hrrr-correction"
FITDIR = OUT / "fit"
INSTANCES_DIR = FITDIR / "instances"
LEDGER_PATH = FITDIR / "fit_ledger.jsonl"
INTEGRITY_TOLERANCE = 1e-6
CANDIDATES = hc.CANDIDATES
LAMBDAS = hc.LAMBDAS


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def ledger(entry: dict[str, Any]) -> None:
    FITDIR.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **entry}
    with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


class IntegrityFailure(RuntimeError):
    pass


class Ctx:
    def __init__(self) -> None:
        self.columns: list[str] = json.loads(COLUMNS_JSON.read_text())["columns"]
        self.manifests: dict[str, Any] = json.loads(
            (QUEUE1 / "manifests.json").read_text(encoding="utf-8")
        )
        self.cache = PredictionCache(
            QUEUE1 / "cache",
            cohort=cohort_hash(FEATURES),
            features=feature_hash(self.columns),
            manifests=manifest_hash(self.manifests),
        )
        era, _acc = gold_model.load_modeling_frame(FEATURES)
        era["event_id"] = era["event_id"].astype(str)
        self.era = era
        hrrr = pd.read_parquet(TRANSFER / "features_hrrr_v3.parquet")
        hrrr["event_id"] = hrrr["event_id"].astype(str)
        self.complete_ids: set[str] = set(
            hrrr.loc[hrrr["hrrr_complete"], "event_id"]
        )
        # HRRR modelling frame, item 7's construction: ERA5 rows with
        # feature columns + baseline overwritten by HRRR-built values.
        h_by_id = hrrr.set_index("event_id")
        frame = era[era["event_id"].isin(self.complete_ids)].copy()
        aligned = h_by_id.loc[frame["event_id"]]
        for col in list(self.columns) + ["baseline_slr"]:
            frame[col] = aligned[col].to_numpy()
        frame["lead_band"] = aligned["lead_band"].to_numpy()
        self.hrrr_frame = frame
        self.era_by_id = era.set_index("event_id")
        self.hrrr_by_id = frame.set_index("event_id")


def split_frames(frame: pd.DataFrame, inst: dict[str, Any]):
    if inst["family"] == "randomised":
        return gold_model.split_random(frame, seed=1000 + inst["rep"])
    if inst["family"] == "loro":
        return gold_model.split_loro(frame, inst["region"])
    if inst["family"] == "temporal":
        return gold_model.split_temporal(frame)
    return gold_model.split_stations(frame, seed=4000)


# ---------------------------------------------------------------------------
# ENS-EB refits (the registered counting unit: 12 tree fits + 2 EB
# derivations each, per REGISTRATION.md R6(i))
# ---------------------------------------------------------------------------

def ens_fit(
    train: pd.DataFrame,
    seed: int,
    ctx: Ctx,
    split_id: str,
    stage: str,
    role: str,
):
    t0 = time.time()
    rf = gold_model.RFEBModel(seed, columns=list(ctx.columns)).fit(train)
    gbm = gold_model.GBMEBModel(seed, columns=list(ctx.columns)).fit(train)
    ledger({
        "kind": "ens_refit", "split_id": split_id, "stage": stage,
        "role": role, "train_n": int(len(train)), "success": True,
        "seconds": round(time.time() - t0, 1),
    })
    return rf, gbm


def ens_predict(models, frame: pd.DataFrame) -> np.ndarray:
    rf, gbm = models
    _, rf_pred = rf.predict_parts(frame)
    _, gbm_pred = gbm.predict_parts(frame)
    return (rf_pred + gbm_pred) / 2.0


def _adapter_rows(
    ctx: Ctx, event_ids: list[str], yhat: np.ndarray
) -> pd.DataFrame:
    meta = ctx.era_by_id.loc[event_ids]
    liquid = ctx.hrrr_by_id.loc[event_ids, "fcst_liquid_mm"].to_numpy(dtype=float)
    return pd.DataFrame({
        "event_id": event_ids,
        "yhat": np.asarray(yhat, dtype=float),
        "y": meta["observed_slr"].to_numpy(dtype=float),
        "liquid": liquid,
        "region": meta["region"].astype(str).to_numpy(),
    })


def inner_oof_station(
    S: pd.DataFrame, seed: int, ctx: Ctx, split_id: str, stage: str
) -> pd.DataFrame:
    """Inner OOF adapter rows for a station-family/LORO set S (R6(ii)).

    5 ENS-EB refits on S \\ fold_j (ERA5 inputs), predicting fold j's
    paired events on their HRRR inputs."""
    folds = gold_model._station_grouped_folds(S, seed)
    ids = S["event_id"].astype(str).to_numpy()
    paired = np.array([e in ctx.complete_ids for e in ids])
    parts: list[pd.DataFrame] = []
    for j in range(5):
        models = ens_fit(
            S.iloc[np.flatnonzero(folds != j)], seed, ctx, split_id,
            stage, f"inner_oof_fold{j}",
        )
        pred_ids = ids[(folds == j) & paired]
        if len(pred_ids):
            target = ctx.hrrr_by_id.loc[list(pred_ids)]
            yhat = ens_predict(models, target)
            parts.append(_adapter_rows(ctx, list(pred_ids), yhat))
        del models
        gc.collect()
    return pd.concat(parts, ignore_index=True) if parts else _adapter_rows(ctx, [], np.array([]))


def _temporal_blocks(S: pd.DataFrame) -> list[np.ndarray]:
    """5 contiguous chronological blocks of S (R6(iv)): events sorted
    by (window-end instant, event_id), numpy array_split semantics."""
    instants = pd.to_datetime(S["valid_time"], utc=True)
    order_frame = pd.DataFrame({
        "pos": np.arange(len(S)),
        "instant": instants.to_numpy(),
        "event_id": S["event_id"].astype(str).to_numpy(),
    }).sort_values(["instant", "event_id"], kind="mergesort")
    order = order_frame["pos"].to_numpy()
    return [order[b] for b in np.array_split(np.arange(len(S)), 5)]


def inner_oof_temporal(
    S: pd.DataFrame, seed: int, ctx: Ctx, split_id: str
) -> pd.DataFrame:
    """Forward-chaining inner OOF rows for the temporal set (R6(ii)).

    Blocks 1-2 are burn-in; block j's rows (j = 3..5) come from an
    ENS-EB refit on blocks 1..j-1.  3 ENS-EB refits."""
    blocks = _temporal_blocks(S)
    ids = S["event_id"].astype(str).to_numpy()
    paired = np.array([e in ctx.complete_ids for e in ids])
    parts: list[pd.DataFrame] = []
    for b in (2, 3, 4):
        fit_pos = np.concatenate(blocks[:b])
        models = ens_fit(
            S.iloc[fit_pos], seed, ctx, split_id,
            "inner_oof_temporal", f"forward_fit_blocks1-{b}",
        )
        pred_pos = blocks[b]
        pred_ids = ids[pred_pos[paired[pred_pos]]]
        if len(pred_ids):
            target = ctx.hrrr_by_id.loc[list(pred_ids)]
            yhat = ens_predict(models, target)
            rows = _adapter_rows(ctx, list(pred_ids), yhat)
            rows["block"] = b + 1
            parts.append(rows)
        del models
        gc.collect()
    return pd.concat(parts, ignore_index=True) if parts else _adapter_rows(ctx, [], np.array([]))


# ---------------------------------------------------------------------------
# Adapter fitting / tuning
# ---------------------------------------------------------------------------

def _fit_and_score(
    candidate: str,
    lam: float,
    train_rows: pd.DataFrame,
    val_rows: pd.DataFrame,
    split_id: str,
    stage: str,
    role: str,
) -> dict[str, Any]:
    """One registered adapter fit: fit on train_rows, score on val_rows."""
    t0 = time.time()
    try:
        adapter, objective = hc.fit_adapter(
            candidate,
            train_rows["yhat"].to_numpy(),
            train_rows["y"].to_numpy(),
            train_rows["liquid"].to_numpy(),
            train_rows["region"].to_numpy(),
            lam,
        )
    except hc.AdapterFitFailed as exc:
        ledger({
            "kind": "adapter_fit", "split_id": split_id, "stage": stage,
            "role": role, "candidate": candidate, "lam": lam,
            "success": False, "error": str(exc),
            "seconds": round(time.time() - t0, 1),
        })
        return {"success": False, "n": int(len(val_rows)), "sum_abs_err": None,
                "error": str(exc)}
    corrected = adapter.predict(
        val_rows["yhat"].to_numpy(),
        val_rows["liquid"].to_numpy(),
        val_rows["region"].to_numpy(),
    )
    sae = float(np.sum(np.abs(corrected - val_rows["y"].to_numpy())))
    ledger({
        "kind": "adapter_fit", "split_id": split_id, "stage": stage,
        "role": role, "candidate": candidate, "lam": lam,
        "success": True, "objective": objective,
        "val_n": int(len(val_rows)), "val_sum_abs_err": sae,
        "seconds": round(time.time() - t0, 1),
    })
    return {"success": True, "n": int(len(val_rows)), "sum_abs_err": sae,
            "objective": objective}


def _empty_pooled() -> dict[str, dict[float, dict[str, Any]]]:
    return {
        c: {lam: {"success": True, "n": 0, "sum_abs_err": 0.0} for lam in LAMBDAS}
        for c in CANDIDATES
    }


def _merge_pooled(pooled, split_results: dict[str, dict[str, Any]]) -> None:
    for c in CANDIDATES:
        for lam in LAMBDAS:
            entry = split_results[f"{c}|{lam}"]
            slot = pooled[c][lam]
            slot["success"] = slot["success"] and bool(entry["success"])
            slot["n"] += int(entry["n"])
            if entry["sum_abs_err"] is not None:
                slot["sum_abs_err"] += float(entry["sum_abs_err"])


def tuning_splits_station(
    inst: dict[str, Any], train: pd.DataFrame, ctx: Ctx, idir: Path
) -> dict[str, dict[float, dict[str, Any]]]:
    """Lambda-validation splits for station families and LORO (R6(ii)).

    Per split: rows_T' = INNER_OOF(T') (5 refits) and base_V from an
    ENS-EB refit on T' (1 refit); 12 adapter fits.  Results persist
    per split for resumability."""
    split_id = inst["split_id"]
    seed = inst["fit_seed"]
    tdir = idir / "tuning"
    tdir.mkdir(parents=True, exist_ok=True)
    if inst["family"] == "loro":
        split_defs = [
            (f"region_{r}", train["region"].astype(str).to_numpy() == r)
            for r in sorted(train["region"].astype(str).unique())
        ]
    else:
        folds_t = gold_model._station_grouped_folds(train, seed)
        split_defs = [(f"fold{j}", folds_t == j) for j in range(5)]
    pooled = _empty_pooled()
    for key, vmask in split_defs:
        spath = tdir / f"{key}.json"
        if spath.exists():
            split_results = json.loads(spath.read_text())["results"]
            _merge_pooled(pooled, split_results)
            log(f"{split_id} tuning {key}: persisted, skip")
            continue
        Tp = train.iloc[np.flatnonzero(~vmask)]
        V = train.iloc[np.flatnonzero(vmask)]
        rows_tp = inner_oof_station(Tp, seed, ctx, split_id, "tuning_inner_oof")
        v_ids = [
            e for e in V["event_id"].astype(str).tolist() if e in ctx.complete_ids
        ]
        models = ens_fit(Tp, seed, ctx, split_id, "tuning_base", key)
        base_v = _adapter_rows(
            ctx, v_ids, ens_predict(models, ctx.hrrr_by_id.loc[v_ids])
        )
        del models
        gc.collect()
        split_results: dict[str, dict[str, Any]] = {}
        for c in CANDIDATES:
            for lam in LAMBDAS:
                split_results[f"{c}|{lam}"] = _fit_and_score(
                    c, lam, rows_tp, base_v, split_id, "tuning_adapter", key
                )
        spath.write_text(json.dumps({
            "key": key, "ens_refits": 6, "adapter_fits": 12,
            "results": split_results,
        }, indent=2))
        _merge_pooled(pooled, split_results)
        log(f"{split_id} tuning {key}: done")
    return pooled


def tuning_temporal(
    inst: dict[str, Any], train: pd.DataFrame, ctx: Ctx, idir: Path
) -> tuple[dict[str, dict[float, dict[str, Any]]], pd.DataFrame]:
    """Temporal tuning (R6(ii)): the single split reuses the
    forward-chaining fits — rows_T' are the block-3/4 rows and base_V
    is the block-5 rows' own inner OOF predictions (0 extra refits).
    Also produces rows_T (blocks 3-5) for the final adapter fit."""
    split_id = inst["split_id"]
    seed = inst["fit_seed"]
    rows_path = idir / "rows_t.parquet"
    if rows_path.exists():
        rows_all = pd.read_parquet(rows_path)
    else:
        rows_all = inner_oof_temporal(train, seed, ctx, split_id)
        rows_all.to_parquet(rows_path, index=False)
    tpath = idir / "tuning" / "temporal.json"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    pooled = _empty_pooled()
    if tpath.exists():
        split_results = json.loads(tpath.read_text())["results"]
        _merge_pooled(pooled, split_results)
        log(f"{split_id} tuning temporal: persisted, skip")
        return pooled, rows_all
    rows_tp = rows_all[rows_all["block"].isin([3, 4])]
    base_v = rows_all[rows_all["block"] == 5]
    split_results = {}
    for c in CANDIDATES:
        for lam in LAMBDAS:
            split_results[f"{c}|{lam}"] = _fit_and_score(
                c, lam, rows_tp, base_v, split_id, "tuning_adapter", "block5"
            )
    tpath.write_text(json.dumps({
        "key": "temporal", "ens_refits": 0, "adapter_fits": 12,
        "results": split_results,
    }, indent=2))
    _merge_pooled(pooled, split_results)
    log(f"{split_id} tuning temporal: done")
    return pooled, rows_all


def process_instance(inst: dict[str, Any], ctx: Ctx) -> None:
    split_id = inst["split_id"]
    seed = inst["fit_seed"]
    idir = INSTANCES_DIR / split_id
    idir.mkdir(parents=True, exist_ok=True)
    ipath = idir / "instance.json"
    if ipath.exists():
        log(f"{split_id}: instance complete, skip")
        return
    t_start = time.time()
    train, test = split_frames(ctx.era, inst)
    test_ids = ctx.manifests[split_id]["test_event_ids"]
    assert [str(e) for e in test["event_id"].tolist()] == test_ids, split_id

    if inst["family"] == "temporal":
        pooled, rows_t = tuning_temporal(inst, train, ctx, idir)
    else:
        pooled = tuning_splits_station(inst, train, ctx, idir)
        rows_path = idir / "rows_t.parquet"
        if rows_path.exists():
            rows_t = pd.read_parquet(rows_path)
        else:
            rows_t = inner_oof_station(train, seed, ctx, split_id, "final_inner_oof")
            rows_t.to_parquet(rows_path, index=False)

    lam_star: dict[str, float | None] = {}
    cand_status: dict[str, str] = {}
    cand_reason: dict[str, str] = {}
    for c in CANDIDATES:
        scores = {}
        for lam in LAMBDAS:
            e = pooled[c][lam]
            scores[lam] = (
                e["sum_abs_err"] / e["n"] if e["success"] and e["n"] > 0 else None
            )
        lam_star[c] = hc.select_lambda(scores)
        if lam_star[c] is None:
            cand_status[c] = "fit_failed"
            cand_reason[c] = "no_eligible_lambda"

    # Final ENS-EB refit on the full outer training set; integrity
    # gate against the queue-1 cache before base_X is used (R6(ii)).
    paired_ids = [e for e in test_ids if e in ctx.complete_ids]
    stored = ctx.cache.get("ens_eb", split_id, seed, test_ids)
    if stored is None:
        raise RuntimeError(f"queue-1 cache miss for ens_eb {split_id}")
    position = {eid: i for i, eid in enumerate(test_ids)}
    stored_paired = np.array([stored[position[e]] for e in paired_ids])
    models = ens_fit(train, seed, ctx, split_id, "final_base", "full_train")
    era_pred = ens_predict(models, ctx.era_by_id.loc[paired_ids])
    integrity = float(np.max(np.abs(era_pred - stored_paired)))
    if integrity > INTEGRITY_TOLERANCE:
        ipath.write_text(json.dumps({
            "split_id": split_id, "status": "integrity_failed",
            "integrity_max_abs_diff": integrity,
        }, indent=2))
        raise IntegrityFailure(
            f"{split_id}: integrity gate failed, diff={integrity:.3e}"
        )
    base_x = ens_predict(models, ctx.hrrr_by_id.loc[paired_ids])
    del models
    gc.collect()

    meta = ctx.era_by_id.loc[paired_ids]
    liquid_x = ctx.hrrr_by_id.loc[paired_ids, "fcst_liquid_mm"].to_numpy(dtype=float)
    regions_x = meta["region"].astype(str).to_numpy()
    out = pd.DataFrame({
        "split_id": split_id,
        "family": inst["family"],
        "event_id": paired_ids,
        "station_id": meta["station_id"].astype(str).tolist(),
        "region": regions_x,
        "water_year": meta["water_year"].astype(int).tolist(),
        "wdate": meta["valid_time"].astype(str).str.slice(0, 10).tolist(),
        "elevation_m": meta["elevation_m"].astype(float).tolist(),
        "lead_band": ctx.hrrr_by_id.loc[paired_ids, "lead_band"].astype(str).tolist(),
        "liquid": liquid_x,
        "observed": meta["observed_slr"].astype(float).tolist(),
        "era5_pred": stored_paired,
        "pred_c0": base_x,
    })
    adapter_params: dict[str, Any] = {}
    for c in CANDIDATES:
        col = f"pred_{c.lower()}"
        if c in cand_status:
            out[col] = np.nan
            continue
        lam = float(lam_star[c])
        t0 = time.time()
        try:
            adapter, objective = hc.fit_adapter(
                c,
                rows_t["yhat"].to_numpy(),
                rows_t["y"].to_numpy(),
                rows_t["liquid"].to_numpy(),
                rows_t["region"].to_numpy(),
                lam,
            )
        except hc.AdapterFitFailed as exc:
            ledger({
                "kind": "adapter_fit", "split_id": split_id,
                "stage": "final_adapter", "role": "full_train_rows",
                "candidate": c, "lam": lam, "success": False,
                "error": str(exc), "seconds": round(time.time() - t0, 1),
            })
            cand_status[c] = "fit_failed"
            cand_reason[c] = "final_refit_failed"
            out[col] = np.nan
            continue
        ledger({
            "kind": "adapter_fit", "split_id": split_id,
            "stage": "final_adapter", "role": "full_train_rows",
            "candidate": c, "lam": lam, "success": True,
            "objective": objective, "seconds": round(time.time() - t0, 1),
        })
        out[col] = adapter.predict(base_x, liquid_x, regions_x)
        cand_status[c] = "ok"
        adapter_params[c] = {
            "lam": lam,
            "theta": [float(v) for v in adapter.theta],
            "deltas": {k: float(v) for k, v in adapter.deltas.items()},
        }
    out.to_parquet(idir / "test_predictions.parquet", index=False)
    ipath.write_text(json.dumps({
        "split_id": split_id,
        "family": inst["family"],
        "fit_seed": seed,
        "status": "ok",
        "integrity_max_abs_diff": integrity,
        "lam_star": {c: lam_star[c] for c in CANDIDATES},
        "candidate_status": cand_status,
        "candidate_failure_reasons": cand_reason,
        "adapter_params": adapter_params,
        "n_paired_test": len(paired_ids),
        "n_adapter_rows": int(len(rows_t)),
        "seconds_total": round(time.time() - t_start, 1),
    }, indent=2))
    log(
        f"{split_id}: complete (integrity {integrity:.2e}, "
        f"lam* {lam_star}, {time.time() - t_start:.0f}s)"
    )


def stage_fit(only: str | None) -> None:
    version = hc.verify_scipy_version()
    log(f"scipy gate ok: {version}")
    ctx = Ctx()
    instances = split_instances()
    if only:
        wanted = set(only.split(","))
        instances = [i for i in instances if i["split_id"] in wanted]
    for inst in instances:
        process_instance(inst, ctx)


# ---------------------------------------------------------------------------
# Judging (REGISTRATION.md R3 / R4 / R6(v))
# ---------------------------------------------------------------------------

FAMILIES = ("randomised", "loro", "temporal", "stations")
REGIONS = (
    "alaska", "great-lakes", "northeast",
    "pacific-northwest", "rockies", "sierra-nevada",
)
SLICE_DIMS = (
    "region", "water_year", "slr_band", "liquid_band",
    "elevation_band", "domain", "lead_band",
)
REGISTERED_LEDGER = {"ens_refits": 976, "adapter_fits": 1716}


def _load_fit_outputs() -> tuple[dict[str, Any], pd.DataFrame]:
    metas: dict[str, Any] = {}
    frames: list[pd.DataFrame] = []
    for inst in split_instances():
        split_id = inst["split_id"]
        idir = INSTANCES_DIR / split_id
        meta = json.loads((idir / "instance.json").read_text())
        if meta["status"] != "ok":
            raise RuntimeError(f"{split_id}: instance status {meta['status']}")
        metas[split_id] = meta
        frames.append(pd.read_parquet(idir / "test_predictions.parquet"))
    return metas, pd.concat(frames, ignore_index=True)


def _ledger_counts() -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {}
    totals = {"ens_refit": 0, "adapter_fit": 0, "adapter_fit_failed": 0}
    if LEDGER_PATH.exists():
        for line in LEDGER_PATH.read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            per = counts.setdefault(
                entry["split_id"], {"ens_refit": 0, "adapter_fit": 0}
            )
            if entry["kind"] == "ens_refit":
                totals["ens_refit"] += 1
                per["ens_refit"] += 1
            elif entry["kind"] == "adapter_fit":
                totals["adapter_fit"] += 1
                per["adapter_fit"] += 1
                if not entry.get("success", True):
                    totals["adapter_fit_failed"] += 1
    return {"totals": totals, "by_instance": counts}


def stage_judge() -> dict[str, Any]:
    metas, rows = _load_fit_outputs()
    rows["domain"] = np.where(rows["region"] == "alaska", "alaska", "conus")
    rows["slr_band"] = hc.slr_band(rows["pred_c0"].to_numpy())
    rows["liquid_band"] = hc.liquid_band(rows["liquid"].to_numpy())
    rows["elevation_band"] = hc.elevation_band(rows["elevation_m"].to_numpy())
    rows["water_year_s"] = rows["water_year"].astype(str)
    rows = rows.rename(columns={"water_year_s": "water_year_slice"})
    err0 = np.abs(rows["pred_c0"].to_numpy() - rows["observed"].to_numpy())
    err_era5 = np.abs(rows["era5_pred"].to_numpy() - rows["observed"].to_numpy())

    # ---- support assessments (R4), recomputed from the judged rows
    loro_rows = rows[rows["family"] == "loro"]
    region_support: dict[str, Any] = {}
    for region in REGIONS:
        g = loro_rows[loro_rows["region"] == region]
        entry = {
            "n_unique": int(g["event_id"].nunique()),
            "stations": int(g["station_id"].nunique()),
            "dates": int(g["wdate"].nunique()),
        }
        entry["binds"] = (
            entry["n_unique"] >= 200 and entry["stations"] >= 30
            and entry["dates"] >= 20
        )
        region_support[region] = entry
    t_rows = rows[rows["family"] == "temporal"]
    temporal_support = {
        "n_unique": int(t_rows["event_id"].nunique()),
        "water_years": sorted(int(v) for v in t_rows["water_year"].unique()),
        "dates": int(t_rows["wdate"].nunique()),
    }
    temporal_support["evaluable"] = bool(
        temporal_support["n_unique"] >= 500
        and len(temporal_support["water_years"]) >= 3
        and temporal_support["dates"] >= 100
    )

    # ---- bootstrap masks (one aligned replicate stream per clustering)
    masks: dict[str, np.ndarray] = {}
    for fam in FAMILIES:
        masks[f"fam|{fam}"] = (rows["family"] == fam).to_numpy()
    for region in REGIONS:
        masks[f"region|{region}"] = (
            (rows["family"] == "loro") & (rows["region"] == region)
        ).to_numpy()
    slice_dims = {
        "region": rows["region"], "water_year": rows["water_year_slice"],
        "slr_band": rows["slr_band"], "liquid_band": rows["liquid_band"],
        "elevation_band": rows["elevation_band"], "domain": rows["domain"],
        "lead_band": rows["lead_band"],
    }
    slice_support: dict[str, dict[str, Any]] = {}
    for fam in FAMILIES:
        fam_mask = rows["family"] == fam
        for dim, series in slice_dims.items():
            for level in sorted(series[fam_mask].unique()):
                key = f"slice|{fam}|{dim}|{level}"
                mask = (fam_mask & (series == level)).to_numpy()
                masks[key] = mask
                slice_support[key] = {
                    "family": fam, "dim": dim, "level": str(level),
                    "n_rows": int(mask.sum()),
                    "n_unique": int(rows.loc[mask, "event_id"].nunique()),
                }

    station_levels = sorted(rows["station_id"].unique())
    station_pos = {s: i for i, s in enumerate(station_levels)}
    station_idx = rows["station_id"].map(station_pos).to_numpy()
    date_keys = rows["region"] + "|" + rows["wdate"]
    date_levels = sorted(date_keys.unique())
    date_pos = {s: i for i, s in enumerate(date_levels)}
    date_idx = date_keys.map(date_pos).to_numpy()
    clusterings = {
        "station": (station_idx, len(station_levels), hc.BOOTSTRAP_SEED_STATION),
        "date": (date_idx, len(date_levels), hc.BOOTSTRAP_SEED_DATE),
    }

    def point_gain(mask: np.ndarray, errc: np.ndarray) -> float:
        return float(np.mean(err0[mask]) - np.mean(errc[mask]))

    fit_failed: dict[str, list[str]] = {
        c: [
            sid for sid, m in metas.items()
            if m["candidate_status"].get(c) != "ok"
        ]
        for c in CANDIDATES
    }
    judged = [c for c in CANDIDATES if not fit_failed[c]]

    candidates_out: dict[str, Any] = {}
    for c in CANDIDATES:
        if c not in judged:
            candidates_out[c] = {
                "status": "fit_failed",
                "fit_failed_instances": fit_failed[c],
            }
            continue
        errc = np.abs(
            rows[f"pred_{c.lower()}"].to_numpy() - rows["observed"].to_numpy()
        )
        estimates: dict[str, float] = {}
        for key, mask in masks.items():
            estimates[key] = point_gain(mask, errc)
        fam_gain = {f: estimates[f"fam|{f}"] for f in FAMILIES}
        estimates["ff"] = float(np.mean([fam_gain[f] for f in FAMILIES]))

        intervals: dict[str, dict[str, Any]] = {}
        for cl_name, (idx, k, seed) in clusterings.items():
            reps = hc.bootstrap_gains(err0, errc, idx, k, masks, seed=seed)
            fam_stack = np.vstack([reps[f"fam|{f}"] for f in FAMILIES])
            valid = np.all(np.isfinite(fam_stack), axis=0)
            comp = np.array([
                float(np.mean(fam_stack[:, b])) if valid[b] else np.nan
                for b in range(fam_stack.shape[1])
            ])
            reps["ff"] = comp
            intervals[cl_name] = {
                key: hc.percentile_interval(reps[key]) for key in reps
            }

        def iv(key: str) -> list:
            return [intervals["station"].get(key), intervals["date"].get(key)]

        clauses: dict[str, Any] = {}
        clauses["four_family"] = {
            "estimate": estimates["ff"],
            "status": hc.clause_status(
                estimates["ff"], fail_below=0.03, straddle=(0.0, 0.03),
                intervals=iv("ff"), support_ok=True,
            ),
        }
        clauses["pooled_loro"] = {
            "estimate": estimates["fam|loro"],
            "status": hc.clause_status(
                estimates["fam|loro"], fail_below=0.02, straddle=(0.0, 0.02),
                intervals=iv("fam|loro"), support_ok=True,
            ),
        }
        clauses["randomised"] = {
            "estimate": estimates["fam|randomised"],
            "status": hc.clause_status(
                estimates["fam|randomised"], fail_below=-0.02,
                straddle=(-0.02, 0.0), intervals=iv("fam|randomised"),
                support_ok=True,
            ),
        }
        clauses["temporal"] = {
            "estimate": estimates["fam|temporal"],
            "support": temporal_support,
            "status": hc.clause_status(
                estimates["fam|temporal"], fail_below=0.0,
                straddle=(-0.05, 0.0), intervals=iv("fam|temporal"),
                support_ok=temporal_support["evaluable"],
            ),
        }
        clauses["unseen_station"] = {
            "estimate": estimates["fam|stations"],
            "status": hc.clause_status(
                estimates["fam|stations"], fail_below=0.0,
                straddle=(-0.05, 0.0), intervals=iv("fam|stations"),
                support_ok=True,
            ),
        }
        region_detail: dict[str, Any] = {}
        for region in REGIONS:
            key = f"region|{region}"
            region_detail[region] = {
                "estimate": estimates[key],
                "support": region_support[region],
                "status": hc.clause_status(
                    estimates[key], fail_below=-0.05, straddle=(-0.05, 0.0),
                    intervals=iv(key),
                    support_ok=region_support[region]["binds"],
                ),
            }
        region_status = _aggregate_status(
            [d["status"] for d in region_detail.values()]
        )
        clauses["regional"] = {"status": region_status, "per_region": region_detail}

        slice_detail: dict[str, Any] = {}
        for key, support in slice_support.items():
            binding = support["n_unique"] >= 200
            status = (
                hc.clause_status(
                    estimates[key], fail_below=-0.20, straddle=(-0.20, 0.0),
                    intervals=iv(key), support_ok=True,
                )
                if binding else "report_only"
            )
            slice_detail[key] = {
                **support, "estimate": estimates[key], "binding": binding,
                "status": status,
            }
        binding_statuses = [
            d["status"] for d in slice_detail.values() if d["binding"]
        ]
        clauses["slice_guards"] = {
            "status": _aggregate_status(binding_statuses),
            "n_binding": len(binding_statuses),
            "per_slice": slice_detail,
        }

        statuses = [cl["status"] for cl in clauses.values()]
        if "inconclusive" in statuses:
            verdict = "inconclusive"
        elif "failed" in statuses:
            verdict = "rejected"
        else:
            verdict = "adopted"
        remaining = {}
        for fam in FAMILIES:
            mask = masks[f"fam|{fam}"]
            remaining[fam] = float(
                np.mean(errc[mask]) - np.mean(err_era5[mask])
            )
        candidates_out[c] = {
            "status": verdict,
            "clauses": clauses,
            "family_gains": fam_gain,
            "family_mae_c0": {
                f: float(np.mean(err0[masks[f"fam|{f}"]])) for f in FAMILIES
            },
            "family_mae_candidate": {
                f: float(np.mean(errc[masks[f"fam|{f}"]])) for f in FAMILIES
            },
            "remaining_transfer_penalty": remaining,
            "intervals": {
                cl: {
                    key: (
                        None if val is None else
                        {"lo": val[0], "hi": val[1], "n_valid": val[2]}
                    )
                    for key, val in per.items()
                }
                for cl, per in intervals.items()
            },
        }

    nominee = next(
        (c for c in CANDIDATES if candidates_out[c]["status"] == "adopted"),
        None,
    )
    ledger_info = _ledger_counts()
    executed = ledger_info["totals"]
    deviations = []
    for sid, meta in metas.items():
        for c in CANDIDATES:
            if meta["candidate_status"].get(c) == "fit_failed":
                deviations.append(
                    f"{sid}/{c}: {meta['candidate_failure_reasons'].get(c)}"
                )
    results = {
        "study": "hrrr-correction-c1-c3",
        "registered_ledger": REGISTERED_LEDGER,
        "executed_ledger": {
            "ens_refits": executed["ens_refit"],
            "adapter_fits": executed["adapter_fit"],
            "adapter_fits_failed": executed["adapter_fit_failed"],
            "by_instance": ledger_info["by_instance"],
        },
        "ledger_deviations": deviations,
        "integrity_max_abs_diff": {
            sid: m["integrity_max_abs_diff"] for sid, m in metas.items()
        },
        "lam_star": {sid: m["lam_star"] for sid, m in metas.items()},
        "region_support": region_support,
        "temporal_support": temporal_support,
        "n_appearance_rows": int(len(rows)),
        "candidates": candidates_out,
        "nominee": nominee,
    }
    (OUT / "results.json").write_text(json.dumps(results, indent=2))
    log(f"judge: nominee={nominee}, "
        f"verdicts={ {c: candidates_out[c]['status'] for c in CANDIDATES} }")
    return results


def _aggregate_status(statuses: list[str]) -> str:
    if "inconclusive" in statuses:
        return "inconclusive"
    if "failed" in statuses:
        return "failed"
    return "passed"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _f(value: Any, digits: int = 4) -> str:
    return "—" if value is None else f"{float(value):+.{digits}f}"


def _iv_text(interval: Any) -> str:
    if interval is None:
        return "undefined"
    return f"[{interval['lo']:+.4f}, {interval['hi']:+.4f}] (n={interval['n_valid']})"


def stage_report() -> None:
    results = json.loads((OUT / "results.json").read_text())
    lines: list[str] = [
        "# HRRR Correction Study — Results",
        "",
        "Protocol: `REGISTRATION.md` (round-14 certified) and `DESIGN.md`",
        "in this directory. Candidates C1-C3 were fitted under nested",
        "whole-pipeline validation against C0 (uncorrected ENS-EB on HRRR",
        "inputs) on item 7's full paired cohort (22,202 events, 28 frozen",
        "instances, 209,738 instance-appearances). Production calibration",
        "remains disabled; nothing was wired into serving.",
        "",
        "## Verdicts",
        "",
        "| candidate | verdict |",
        "|---|---|",
    ]
    for c in CANDIDATES:
        lines.append(f"| {c} | **{results['candidates'][c]['status']}** |")
    lines += [
        "",
        f"Nominee (predeclared parsimony, C1 before C2 before C3): "
        f"**{results['nominee']}**",
        "",
        "## Integrity and ledger",
        "",
        f"- Integrity gate (refit ERA5-input predictions vs the queue-1",
        f"  cache, per instance): max abs diff overall "
        f"{max(results['integrity_max_abs_diff'].values()):.3e} (gate ≤ 1e-6).",
        f"- Executed ledger: {results['executed_ledger']['ens_refits']} ENS-EB "
        f"refits and {results['executed_ledger']['adapter_fits']} adapter fits "
        f"({results['executed_ledger']['adapter_fits_failed']} failed), against "
        f"the registered {results['registered_ledger']['ens_refits']} / "
        f"{results['registered_ledger']['adapter_fits']}.",
    ]
    if results["ledger_deviations"]:
        lines.append("- Deviations from the registered counts:")
        for dev in results["ledger_deviations"]:
            lines.append(f"  - {dev}")
    else:
        lines.append("- No deviations from the registered counts.")
    lines += [
        "",
        "## Clause evidence",
        "",
    ]
    for c in CANDIDATES:
        cand = results["candidates"][c]
        lines += [f"### {c} — {cand['status']}", ""]
        if cand["status"] == "fit_failed":
            lines.append(
                f"Fit-failed instances: {cand['fit_failed_instances']}. "
                "No judgment is permitted for a candidate without a "
                "complete registered prediction set (R7(a))."
            )
            lines.append("")
            continue
        lines += [
            "| clause | gain (SLR MAE) | station 95% CI | date 95% CI | status |",
            "|---|---:|---|---|---|",
        ]
        clause_keys = {
            "four_family": "ff", "pooled_loro": "fam|loro",
            "randomised": "fam|randomised", "temporal": "fam|temporal",
            "unseen_station": "fam|stations",
        }
        for clause, key in clause_keys.items():
            cl = cand["clauses"][clause]
            lines.append(
                f"| {clause} | {_f(cl['estimate'])} | "
                f"{_iv_text(cand['intervals']['station'].get(key))} | "
                f"{_iv_text(cand['intervals']['date'].get(key))} | "
                f"{cl['status']} |"
            )
        reg = cand["clauses"]["regional"]
        lines.append(
            f"| regional | — | — | — | {reg['status']} |"
        )
        for region, detail in reg["per_region"].items():
            lines.append(
                f"| ↳ {region} | {_f(detail['estimate'])} | "
                f"{_iv_text(cand['intervals']['station'].get(f'region|{region}'))} | "
                f"{_iv_text(cand['intervals']['date'].get(f'region|{region}'))} | "
                f"{detail['status']} (binds={detail['support']['binds']}, "
                f"n={detail['support']['n_unique']}, "
                f"stations={detail['support']['stations']}, "
                f"dates={detail['support']['dates']}) |"
            )
        sg = cand["clauses"]["slice_guards"]
        lines.append(
            f"| slice_guards | — | — | — | {sg['status']} "
            f"({sg['n_binding']} binding slices) |"
        )
        bad = {
            k: v for k, v in sg["per_slice"].items()
            if v["binding"] and v["status"] != "passed"
        }
        for key, detail in sorted(bad.items()):
            lines.append(
                f"| ↳ {key} | {_f(detail['estimate'])} | "
                f"{_iv_text(cand['intervals']['station'].get(key))} | "
                f"{_iv_text(cand['intervals']['date'].get(key))} | "
                f"{detail['status']} (n_unique={detail['n_unique']}) |"
            )
        lines += [
            "",
            "Family gains: "
            + ", ".join(
                f"{f} {_f(g)}" for f, g in cand["family_gains"].items()
            )
            + ". Remaining transfer penalty vs ERA5 (corrected HRRR MAE "
            "− ERA5 MAE): "
            + ", ".join(
                f"{f} {_f(g)}"
                for f, g in cand["remaining_transfer_penalty"].items()
            )
            + ".",
            "",
        ]
    ts = results["temporal_support"]
    lines += [
        "## Support assessments (R4)",
        "",
        f"- Temporal clause: {ts['n_unique']} unique paired events, water",
        f"  years {ts['water_years']}, {ts['dates']} distinct window-end",
        f"  dates -> evaluable = {ts['evaluable']} (requires ≥500 events,",
        "  ≥3 water years, ≥100 dates).",
    ]
    for region, entry in results["region_support"].items():
        lines.append(
            f"- Region {region}: n={entry['n_unique']}, "
            f"stations={entry['stations']}, dates={entry['dates']} "
            f"-> binds = {entry['binds']} (requires ≥200 / ≥30 / ≥20)."
        )
    lines += [
        "",
        "## Claim scope (R4, mandatory context)",
        "",
        "Judged gains are claims about the HRRR arm over the paired",
        "cohort's support only. Of the audited served snow windows,",
        "18 of 204 (8.8%) are on the HRRR arm; 67.6% sit inside the",
        "full-gold liquid/elevation bounding rectangle, 66.2% inside",
        "the paired cohort's own liquid/elevation maxima, and 38.9% of",
        "the HRRR arm's snow windows do. The served HRRR windows are",
        "daily-window leads 1-2 while this cohort's inputs are forecast",
        "hour 1 (CONUS) / hours 1-3 (Alaska): forecast-hour overlap is",
        "unestablished and no lead-coverage claim is made. No claim",
        "extends to the ECMWF-GRIB or Open-Meteo arms, to alpine",
        "observations, or to out-of-envelope served windows. Any",
        "activation proposal additionally requires DESIGN.md §6's",
        "independent confirmation on held-out stations/years.",
        "",
    ]
    (OUT / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")
    log("report written")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["fit", "judge", "report"])
    parser.add_argument("--only", default=None,
                        help="comma-separated split ids (fit)")
    args = parser.parse_args()
    if args.stage == "fit":
        stage_fit(args.only)
    elif args.stage == "judge":
        stage_judge()
    else:
        stage_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
