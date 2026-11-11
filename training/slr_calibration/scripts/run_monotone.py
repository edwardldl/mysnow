#!/usr/bin/env python3
"""Queue item 7 driver: monotone-constrained refit of the ENS-EB champion.

Pre-registration: data/slr-training-data/screened-v2/model-v1/
model-v3-monotone/{DESIGN,REGISTRATION}.md (incl. Amendment A1).
Stages: manifest -> fit -> judge -> physical -> verdict -> package ->
dual -> report.  All stages are resumable: cached predictions are
never refitted.  Run SERIALLY (item 5 lesson: parallel sharding is
pathological in this container).
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "training/slr_calibration/src"))

from slr_calibration import gold_model  # noqa: E402
from slr_calibration.monotone_experiment import (  # noqa: E402
    CONSTRAINT_SIGNS,
    build_triples,
    direction_audit,
    inversion_stats,
    monotonic_cst_vector,
    noninferiority_verdict,
    physical_success,
    screen_kill,
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
OUT = BASE / "model-v3-monotone"
NA = BASE / "model-serving-na-expansion"
CHAMPION_DIR = BASE / "model-v3/champion"
SHARDS = ("shard-a", "shard-b", "shard-c")
CONTROL_TOL = 1e-9
CHAMPION_REPRO_TOL = 1e-6


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_frame_and_columns():
    frame, accounting = gold_model.load_modeling_frame(FEATURES)
    columns = json.loads(COLUMNS_JSON.read_text())["columns"]
    return frame, columns, accounting


def load_manifests() -> dict:
    return json.loads((QUEUE1 / "manifests.json").read_text(encoding="utf-8"))


def make_caches(manifests: dict, columns: list[str]) -> dict[str, PredictionCache]:
    cohort = cohort_hash(FEATURES)
    mhash = manifest_hash(manifests)
    fhash = feature_hash(columns)
    return {
        "queue1": PredictionCache(QUEUE1 / "cache", cohort=cohort, features=fhash, manifests=mhash),
        "mono": PredictionCache(OUT / "cache_mono", cohort=cohort, features=fhash, manifests=mhash),
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


def _results_update(section: str, payload) -> dict:
    path = OUT / "results.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[section] = payload
    path.write_text(json.dumps(data, indent=2, default=float), encoding="utf-8")
    return data


# ---------------------------------------------------------------------------
# Stage: manifest (pre-fit; freezes the physical population + audits)
# ---------------------------------------------------------------------------

def load_na_rows() -> tuple[pd.DataFrame, dict, dict]:
    """Join the three shards' features/predictions on (source, point_id,
    lead_day), restricted to ELIGIBLE rows.  Per REGISTRATION A1 section
    3 the exactly-1:1 cardinality requirement applies to eligible rows
    (non-missing champion model_slr).  Predictions-only rows are batch
    rows marked missing for which no features were built (HRRR lead-1
    windows); duplicate keys, features rows without an eligible
    prediction, or eligible predictions without features all halt."""
    hashes: dict[str, str] = {}
    frames = []
    accounting: dict[str, dict] = {}
    for shard in SHARDS:
        fpath, ppath = NA / shard / "features.csv", NA / shard / "predictions.csv"
        hashes[f"{shard}/features.csv"] = sha256_file(fpath)
        hashes[f"{shard}/predictions.csv"] = sha256_file(ppath)
        feats = pd.read_csv(fpath)
        preds = pd.read_csv(ppath)
        keys = ["source", "point_id", "lead_day"]
        if feats.duplicated(subset=keys).any() or preds.duplicated(subset=keys).any():
            raise RuntimeError(f"{shard}: duplicate join keys")
        eligible_preds = preds[preds["model_slr"].notna()]
        feat_keys = set(map(tuple, feats[keys].values))
        elig_keys = set(map(tuple, eligible_preds[keys].values))
        if feat_keys != elig_keys:
            raise RuntimeError(
                f"{shard}: features rows and eligible predictions rows are not the same set"
            )
        merged = feats.merge(
            preds[keys + ["window_end_local_date", "model_slr", "missing"]],
            on=keys, how="left", indicator=True, validate="one_to_one",
        )
        if (merged["_merge"] != "both").any():
            raise RuntimeError(f"{shard}: features row without a predictions row")
        accounting[shard] = {
            "features_rows": int(len(feats)),
            "predictions_rows": int(len(preds)),
            "eligible_rows": int(len(eligible_preds)),
            "ineligible_predictions_rows": int(len(preds) - len(eligible_preds)),
        }
        frames.append(merged.drop(columns=["_merge"]))
    joined = pd.concat(frames, ignore_index=True)
    if joined.duplicated(subset=["source", "point_id", "lead_day"]).any():
        raise RuntimeError("duplicate (source, point_id, lead_day) across shards")
    return joined, hashes, accounting


def stage_manifest() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    joined, hashes, na_accounting = load_na_rows()
    eligible = joined[joined["model_slr"].notna()].copy()
    log(f"manifest: {len(joined)} joined rows, {len(eligible)} eligible (non-missing champion)")

    # Window population: complete triples vs incomplete groups.
    from slr_calibration.monotone_experiment import resort_key, role_of
    groups: dict[tuple, dict[str, int]] = {}
    for rec in eligible.itertuples(index=False):
        key = (resort_key(rec.point_id), rec.source, str(rec.window_end_local_date))
        groups.setdefault(key, {})[role_of(rec.point_id)] = groups.get(key, {}).get(role_of(rec.point_id), 0) + 1
    complete_keys = sorted(k for k, v in groups.items() if set(v) == {"base", "mid_mountain", "summit"} and all(n == 1 for n in v.values()))
    incomplete = {str(k): v for k, v in sorted(groups.items()) if k not in set(complete_keys)}
    log(f"manifest: {len(complete_keys)} complete windows, {len(incomplete)} incomplete groups")
    if len(complete_keys) != 1268:
        raise RuntimeError(
            f"window count {len(complete_keys)} != frozen 1268; halt for a pre-fit amendment"
        )
    complete_set = set(complete_keys)
    win_rows = eligible[
        eligible.apply(
            lambda r: (resort_key(r["point_id"]), r["source"], str(r["window_end_local_date"])) in complete_set,
            axis=1,
        )
    ].copy()
    triples = build_triples(win_rows, "model_slr")
    champ_stats = inversion_stats([triples[k] for k in complete_keys])
    per_arm = {}
    for arm in ("ecmwf", "hrrr"):
        keys = [k for k in complete_keys if k[1] == arm]
        per_arm[arm] = inversion_stats([triples[k] for k in keys])
    log(f"manifest: champion violating {champ_stats['violating']} of {champ_stats['windows']}")
    if champ_stats["violating"] != 611:
        raise RuntimeError(
            f"champion violating count {champ_stats['violating']} != frozen 611 "
            "under the 1e-6 tolerance; halt for a pre-fit amendment"
        )

    audit = direction_audit(win_rows)
    for col, rec in audit.items():
        log(f"audit {col}: pairs={rec['pairs']} fail={rec['direction_fail']} nan={rec['nan_transition']}")

    # Cached-identity check: ens_eb == mean(rf_eb, gbm_eb) per instance.
    manifests = load_manifests()
    _frame, columns, _acc = load_frame_and_columns()
    caches = make_caches(manifests, columns)
    identity: dict[str, float] = {}
    for inst in split_instances():
        split_id, seed = inst["split_id"], inst["fit_seed"]
        ids = manifests[split_id]["test_event_ids"]
        ens = caches["queue1"].get("ens_eb", split_id, seed, ids)
        rf = caches["queue1"].get("rf_eb", split_id, seed, ids)
        gbm = caches["queue1"].get("gbm_eb", split_id, seed, ids)
        if ens is None or rf is None or gbm is None:
            raise RuntimeError(f"queue-1 cache miss for identity check: {split_id}")
        identity[split_id] = float(np.max(np.abs(ens - (rf + gbm) / 2.0)))
    max_identity = max(identity.values())
    log(f"manifest: cached identity max diff {max_identity:.3e}")
    if max_identity > CONTROL_TOL:
        raise RuntimeError("cached ens_eb identity fails at 1e-9; halt")

    import numpy
    import sklearn
    import scipy
    builder_files = [
        "v3_features.py", "gold_profiles_v3.py", "gold_profiles_v3_sfc.py", "gold_profiles.py",
    ]
    src_dir = REPO / "training/slr_calibration/src/slr_calibration"
    manifest = {
        "na_accounting": na_accounting,
        "source_hashes": hashes,
        "champion_package_hashes": {
            name: sha256_file(CHAMPION_DIR / name)
            for name in ("rf.joblib", "gbm.joblib", "champion_sidecar.json")
        },
        "feature_builder_hashes": {n: sha256_file(src_dir / n) for n in builder_files},
        "libraries": {
            "sklearn": sklearn.__version__, "numpy": numpy.__version__,
            "pandas": pd.__version__, "scipy": scipy.__version__,
        },
        "joined_rows": int(len(joined)),
        "eligible_rows": int(len(eligible)),
        "window_keys": [list(k) for k in complete_keys],
        "incomplete_groups": incomplete,
        "champion_triples": {str(k): list(triples[k]) for k in complete_keys},
        "champion_stats": champ_stats,
        "champion_stats_per_arm": per_arm,
        "direction_audit": audit,
        "cached_identity_max_abs_diff": identity,
    }
    canonical = json.dumps(manifest, sort_keys=True, default=float)
    manifest["manifest_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    (OUT / "physical_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=float), encoding="utf-8"
    )
    log(f"manifest written: sha256 {manifest['manifest_sha256'][:16]}...")
    return manifest


# ---------------------------------------------------------------------------
# Stage: fit (control first on screen instances, then constrained fits)
# ---------------------------------------------------------------------------

def fit_instance(frame, inst, manifests, caches, columns, *, control: bool) -> None:
    split_id, seed = inst["split_id"], inst["fit_seed"]
    ids = manifests[split_id]["test_event_ids"]
    vector = monotonic_cst_vector(columns)
    if control:
        fitlog = json.loads((OUT / "fitlog.json").read_text()) if (OUT / "fitlog.json").exists() else {}
        if f"control:{split_id}" not in fitlog:
            train, test = split_frames(frame, inst)
            assert [str(e) for e in test["event_id"].tolist()] == ids
            t0 = time.time()
            ctrl = gold_model.GBMEBModel(seed, columns=list(columns)).fit(train)
            _, ctrl_pred = ctrl.predict_parts(test)
            reference = caches["queue1"].get("gbm_eb", split_id, seed, ids)
            diff = float(np.max(np.abs(ctrl_pred - reference))) if reference is not None else None
            _fitlog_update(f"control:{split_id}", {
                "seconds": round(time.time() - t0, 1),
                "max_abs_diff_vs_queue1_gbm_eb": diff,
            })
            log(f"control {split_id}: diff {diff} in {time.time() - t0:.0f}s")
            del ctrl
            gc.collect()
            if diff is None or diff > CONTROL_TOL:
                (OUT / "INTEGRITY_FAILED.json").write_text(json.dumps({
                    "stage": "control", "split_id": split_id, "max_abs_diff": diff,
                    "tolerance": CONTROL_TOL,
                    "status": "INTEGRITY FAILED / NOT JUDGED",
                }, indent=2), encoding="utf-8")
                raise SystemExit(
                    f"INTEGRITY FAILED: control {split_id} diff {diff} > {CONTROL_TOL}; halted"
                )
    if caches["mono"].get("gbm_eb_mono", split_id, seed, ids) is not None:
        log(f"mono {split_id}: cached, skip")
        return
    train, test = split_frames(frame, inst)
    assert [str(e) for e in test["event_id"].tolist()] == ids, f"manifest mismatch {split_id}"
    t0 = time.time()
    model = gold_model.GBMEBModel(seed, columns=list(columns), monotonic_cst=vector).fit(train)
    _, pred = model.predict_parts(test)
    caches["mono"].put("gbm_eb_mono", split_id, seed, ids, pred, origin="fitted:run_monotone")
    seconds = time.time() - t0
    _fitlog_update(f"mono:{split_id}", {"seconds": round(seconds, 1)})
    log(f"mono {split_id}: fitted in {seconds:.0f}s")
    del model
    gc.collect()


def stage_fit(which: str, explicit: str | None) -> None:
    frame, columns, _acc = load_frame_and_columns()
    manifests = load_manifests()
    caches = make_caches(manifests, columns)
    OUT.mkdir(parents=True, exist_ok=True)
    screen_ids = set(screen_split_ids())
    for inst in select_instances(which, explicit):
        fit_instance(
            frame, inst, manifests, caches, columns,
            control=inst["split_id"] in screen_ids,
        )


# ---------------------------------------------------------------------------
# Stage: judge
# ---------------------------------------------------------------------------

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


def load_challenger(caches, manifests, split_ids=None):
    out: dict[str, pd.Series] = {}
    for inst in split_instances():
        split_id = inst["split_id"]
        if split_ids is not None and split_id not in split_ids:
            continue
        ids = manifests[split_id]["test_event_ids"]
        rf = caches["queue1"].get("rf_eb", split_id, inst["fit_seed"], ids)
        mono = caches["mono"].get("gbm_eb_mono", split_id, inst["fit_seed"], ids)
        if rf is None or mono is None:
            raise RuntimeError(f"challenger cache miss: {split_id}")
        out[split_id] = pd.Series((rf + mono) / 2.0, index=ids)
    return out


def make_judge(manifests) -> Judge:
    frame, _columns, _acc = load_frame_and_columns()
    metadata = frame.set_index("event_id")[["observed_slr", "region"]]
    return Judge(metadata, manifests)


def stage_judge(stage: str) -> dict:
    manifests = load_manifests()
    _frame, columns, _acc = load_frame_and_columns()
    caches = make_caches(manifests, columns)
    judge = make_judge(manifests)
    if stage == "screen":
        ids = set(screen_split_ids())
        incumbent = load_predictions(caches["queue1"], manifests, "ens_eb", ids)
        candidate = load_challenger(caches, manifests, ids)
        builtin = judge.screen(incumbent, candidate)
        killed, clauses = screen_kill(builtin)
        payload = {
            "builtin_screen_descriptive": builtin,
            "registered_kill": {"killed": killed, "clauses": clauses},
            "screen_scalar": builtin["screen_scalar"],
        }
        _results_update("screen", payload)
        log(f"screen: scalar {builtin['screen_scalar']:+.4f}; registered kill={killed} {clauses}; "
            f"builtin killed={builtin['killed']}")
        return payload
    incumbent = load_predictions(caches["queue1"], manifests, "ens_eb")
    candidate = load_challenger(caches, manifests)
    full = judge.full(incumbent, candidate)
    ni_ok, ni_clauses = noninferiority_verdict(full)
    payload = {
        "family_stats": {
            "incumbent": judge.family_maes(incumbent),
            "candidate": judge.family_maes(candidate),
        },
        "four_family_gain": full["four_family_gain"],
        "family_gain": full["family_gain"],
        "loro_region_gain": full["loro_region_gain"],
        "slices": full["slices"],
        "standing_rule_descriptive": {
            "adopted": full["adopted"], "failed_clauses": full["failed_clauses"],
        },
        "noninferiority": {"passed": ni_ok, "failed_clauses": ni_clauses},
    }
    _results_update("full", payload)
    log(f"full: four-family gain {full['four_family_gain']:+.4f}; non-inferiority={ni_ok} {ni_clauses}")
    return payload


# ---------------------------------------------------------------------------
# Stage: physical (paired endpoint on the frozen NA batch)
# ---------------------------------------------------------------------------

def endpoint_frame() -> tuple[pd.DataFrame, dict]:
    """Eligible NA rows as a predict frame (serving conventions:
    site_id = point_id; region = the registry climate regime label —
    carried for validation only, it does not drive predictions)."""
    joined, _hashes, _acct = load_na_rows()
    eligible = joined[joined["model_slr"].notna()].copy()
    region_by_point: dict[str, str] = {}
    for shard in SHARDS:
        registry = json.loads((NA / f"registry-{shard}.json").read_text())
        for resort in registry["resorts"]:
            for point in resort["forecast_points"]:
                region_by_point[point["point_id"]] = resort["climate_regime"]
    eligible["region"] = eligible["point_id"].map(region_by_point)
    eligible["site_id"] = eligible["point_id"]
    if eligible["region"].isna().any():
        raise RuntimeError("endpoint frame: point without registry region")
    return eligible.reset_index(drop=True), {}


def _predict_parts_relaxed(model, frame: pd.DataFrame):
    """ChampionModel.predict_parts under the serving SS5 relaxed
    finiteness clause (mirrors model_serving.predict_with_passthrough,
    which patches the same module-level validator for predict())."""
    import slr_calibration.champion as champion_mod

    def relaxed(frame_, features, *, strict=False):
        missing = [c for c in features if c not in frame_.columns]
        if "region" not in frame_.columns:
            missing.append("region")
        if missing:
            raise ValueError("missing columns: " + ", ".join(missing))

    original = champion_mod.validate_event_frame
    champion_mod.validate_event_frame = relaxed
    try:
        return model.predict_parts(frame)
    finally:
        champion_mod.validate_event_frame = original


def stage_physical() -> dict:
    import joblib

    from slr_calibration import champion as champion_mod
    from slr_calibration.gold_model import MAX_SLR, MIN_SLR

    manifest = json.loads((OUT / "physical_manifest.json").read_text())
    _frame, columns, _acc = load_frame_and_columns()
    frame, _ = endpoint_frame()
    frame = frame.sort_values(["source", "point_id", "lead_day"]).reset_index(drop=True)
    keys = list(zip(frame["source"], frame["point_id"], frame["lead_day"].astype(int)))
    log(f"physical: {len(frame)} eligible rows")

    champ = champion_mod.ChampionModel.load(CHAMPION_DIR)
    rf_eb_c, gbm_eb_c = _predict_parts_relaxed(champ, frame)
    ens_c = (rf_eb_c + gbm_eb_c) / 2.0
    # Integrity: champion re-run vs the frozen manifest values, aligned
    # on (source, point_id, lead_day) via window triples' row order —
    # compare through the joined model_slr column instead (same rows).
    stored = frame["model_slr"].to_numpy(dtype=float)
    repro_diff = float(np.max(np.abs(ens_c - stored)))
    log(f"physical: champion reproduction max abs diff {repro_diff:.3e}")
    if repro_diff > CHAMPION_REPRO_TOL:
        (OUT / "INTEGRITY_FAILED.json").write_text(json.dumps({
            "stage": "physical", "max_abs_diff": repro_diff,
            "tolerance": CHAMPION_REPRO_TOL, "status": "INTEGRITY FAILED / NOT JUDGED",
        }, indent=2), encoding="utf-8")
        raise SystemExit(f"INTEGRITY FAILED: champion reproduction diff {repro_diff}; halted")

    # Challenger GBM seat: full-pool constrained fit (seed 0), saved
    # once; the exact joblib the candidate package would ship.
    gbm_path = OUT / "fullpool_gbm.joblib"
    eb_path = OUT / "fullpool_gbm_eb.json"
    if gbm_path.exists() and eb_path.exists():
        mono = gold_model.GBMEBModel(0, columns=list(columns), monotonic_cst=monotonic_cst_vector(columns))
        mono.gbm = joblib.load(gbm_path)
        mono.eb_artifact = json.loads(eb_path.read_text())
        log("physical: reusing saved full-pool constrained GBM")
    else:
        train_frame, _c, _a = load_frame_and_columns()
        t0 = time.time()
        mono = gold_model.GBMEBModel(
            0, columns=list(columns), monotonic_cst=monotonic_cst_vector(columns)
        ).fit(train_frame)
        joblib.dump(mono.gbm, gbm_path)
        eb_path.write_text(json.dumps(mono.eb_artifact), encoding="utf-8")
        log(f"physical: full-pool constrained GBM fitted in {time.time() - t0:.0f}s")
    raw_mono, eb_mono = mono.predict_parts(frame)
    if not np.isfinite(eb_mono).all():
        raise SystemExit("physical: non-finite challenger prediction; halted")
    ens_ch = (rf_eb_c + eb_mono) / 2.0

    # Stage-wise values (raw seat means, then EB seat means).
    X = frame[champ.features].to_numpy(dtype=float)
    raw_rf = np.clip(champ.rf.predict(X), MIN_SLR, MAX_SLR)
    raw_gbm_c = np.clip(champ.gbm.predict(X), MIN_SLR, MAX_SLR)
    ens_raw_c = (raw_rf + raw_gbm_c) / 2.0
    ens_raw_ch = (raw_rf + raw_mono) / 2.0

    work = frame[["point_id", "source", "window_end_local_date"]].copy()
    complete_keys = [tuple(k) for k in manifest["window_keys"]]

    def triples_for(values: np.ndarray):
        from slr_calibration.monotone_experiment import resort_key

        w = work.copy()
        w["slr"] = values
        w["resort"] = w["point_id"].map(resort_key)
        keep = set(complete_keys)
        w = w[w.apply(
            lambda r: (r["resort"], r["source"], str(r["window_end_local_date"])) in keep,
            axis=1,
        )]
        triples = build_triples(w[["point_id", "source", "window_end_local_date", "slr"]], "slr")
        return [triples[k] for k in complete_keys]

    champ_stats = inversion_stats(triples_for(ens_c))
    result = {
        "champion_reproduction_max_abs_diff": repro_diff,
        "champion": champ_stats,
        "champion_raw_stage": inversion_stats(triples_for(ens_raw_c)),
        "challenger": inversion_stats(triples_for(ens_ch)),
        "challenger_raw_stage": inversion_stats(triples_for(ens_raw_ch)),
        "per_arm": {},
    }
    for arm in ("ecmwf", "hrrr"):
        keys_arm = [k for k in complete_keys if k[1] == arm]
        idx = [complete_keys.index(k) for k in keys_arm]
        ct = [triples_for(ens_c)[i] for i in idx]
        ht = [triples_for(ens_ch)[i] for i in idx]
        result["per_arm"][arm] = {
            "champion": inversion_stats(ct), "challenger": inversion_stats(ht),
        }
    ok, clauses = physical_success(manifest["champion_stats"], result["challenger"])
    result["physical_success"] = {"passed": ok, "failed_clauses": clauses}
    result["descriptives"] = {
        "mean_abs_prediction_shift": float(np.mean(np.abs(ens_ch - ens_c))),
        "max_abs_prediction_shift": float(np.max(np.abs(ens_ch - ens_c))),
        "clamp_rate_champion": float(np.mean((ens_c <= 3.0 + 1e-9) | (ens_c >= 30.0 - 1e-9))),
        "clamp_rate_challenger": float(np.mean((ens_ch <= 3.0 + 1e-9) | (ens_ch >= 30.0 - 1e-9))),
    }
    # Persist per-row challenger values for the package round-trip.
    out_rows = frame[["source", "point_id", "lead_day"]].copy()
    out_rows["champion_slr"] = ens_c
    out_rows["challenger_slr"] = ens_ch
    out_rows.to_csv(OUT / "physical_rows.csv", index=False)
    _results_update("physical", result)
    log(f"physical: champion violating {champ_stats['violating']}, challenger "
        f"{result['challenger']['violating']}; success={ok} {clauses}")
    return result


# ---------------------------------------------------------------------------
# Stage: verdict (mechanical, REGISTRATION section 5)
# ---------------------------------------------------------------------------

def stage_verdict() -> dict:
    results = json.loads((OUT / "results.json").read_text())
    screen = results.get("screen")
    full = results.get("full")
    physical = results.get("physical")
    clauses: list[str] = []
    if screen is None:
        verdict = {"adopted": False, "stage": "incomplete", "detail": "screen not run"}
    elif screen["registered_kill"]["killed"]:
        verdict = {
            "adopted": False, "stage": "screen-killed",
            "detail": screen["registered_kill"]["clauses"],
            "full_accuracy": "NOT RUN", "standing_rule": "NOT RUN",
        }
    elif full is None or physical is None:
        verdict = {"adopted": False, "stage": "incomplete",
                   "detail": "full judge and/or physical endpoint not run"}
    else:
        if not full["noninferiority"]["passed"]:
            clauses += [f"accuracy: {c}" for c in full["noninferiority"]["failed_clauses"]]
        if not physical["physical_success"]["passed"]:
            clauses += [f"physical: {c}" for c in physical["physical_success"]["failed_clauses"]]
        verdict = {
            "adopted": not clauses,
            "stage": "adopted" if not clauses else "not-adopted",
            "detail": clauses,
        }
    _results_update("verdict", verdict)
    log(f"verdict: {verdict}")
    return verdict


# ---------------------------------------------------------------------------
# Stage: package (only if adopted; DESIGN section 6 / A1 section 5)
# ---------------------------------------------------------------------------

def stage_package() -> dict | None:
    import joblib

    from slr_calibration import champion as champion_mod
    from slr_calibration.gold_model import _frame_profile_source

    results = json.loads((OUT / "results.json").read_text())
    if not results["verdict"]["adopted"]:
        log("package: NOT adopted; no package built")
        return None
    frame, columns, accounting = load_frame_and_columns()
    out_dir = OUT / "candidate"
    out_dir.mkdir(parents=True, exist_ok=True)
    # RF seat: the champion's own joblib, byte-identical.
    rf_bytes = (CHAMPION_DIR / "rf.joblib").read_bytes()
    (out_dir / "rf.joblib").write_bytes(rf_bytes)
    rf_hash_ok = sha256_file(out_dir / "rf.joblib") == sha256_file(CHAMPION_DIR / "rf.joblib")
    # GBM seat: the exact full-pool fit evaluated on the endpoint.
    (out_dir / "gbm.joblib").write_bytes((OUT / "fullpool_gbm.joblib").read_bytes())
    champ_sidecar = json.loads((CHAMPION_DIR / "champion_sidecar.json").read_text())
    mono_eb = json.loads((OUT / "fullpool_gbm_eb.json").read_text())

    def component_block(model_file, description, eb_artifact):
        method = eb_artifact["methods"]["kuchera"]
        return {
            "modelFile": model_file,
            "model": description,
            "predictionClamp": [3.0, 30.0],
            "eb": champ_sidecar["components"]["rf_eb"]["eb"],
            "ebArtifact": eb_artifact,
            "ebNodes": {
                "climate": len(method["climate"]),
                "regionH3Res4": len(method["region"]),
                "localSite": len(method["local"]),
            },
        }

    stats = results["full"]["family_stats"]
    sidecar = {
        "packageFormat": champion_mod.PACKAGE_FORMAT,
        "packageVersion": champion_mod.PACKAGE_VERSION,
        "model": "ENS-EB (monotone-constrained refit candidate, queue item 7)",
        "status": "CANDIDATE — MONOTONE-CONSTRAINED REFIT — NOT SERVED",
        "definition": (
            "ENS-EB SLR = simple mean of the RF+EB and GBM+EB predictions. "
            "The RF seat is the packaged champion's full-pool fit, unchanged. "
            "The GBM seat is a full-pool HistGradientBoostingRegressor fit "
            "with monotonic_cst on six features (elevation_m:+1; "
            "tmax_column_pwt, tmax_warmnose_pwt, freezing_level_m_agl_pwt, "
            "warm_layer_depth_m_pwt, subcloud_warm_depth_m: -1; all other "
            "columns unconstrained), with its own EB residual layer."
        ),
        "features": columns,
        "regions": sorted(str(r) for r in frame["region"].unique()),
        "combination": {"rule": "simple_mean", "components": ["rf_eb", "gbm_eb"]},
        "components": {
            "rf_eb": component_block(
                "rf.joblib",
                champ_sidecar["components"]["rf_eb"]["model"]
                + " (copied byte-identical from the packaged champion)",
                champ_sidecar["components"]["rf_eb"]["ebArtifact"],
            ),
            "gbm_eb": component_block(
                "gbm.joblib",
                "HistGradientBoostingRegressor(random_state=0, sklearn defaults, "
                "monotonic_cst=registered six-feature vector), full-pool fit "
                "(queue item 7 packaging; the exact fit evaluated on the "
                "physical endpoint)",
                mono_eb,
            ),
        },
        "training": {
            "profileSource": _frame_profile_source(frame),
            "featuresFile": FEATURES.name,
            "trainingRows": int(len(frame)),
            "waterYears": accounting["modelingWaterYears"],
            "rowsByRegion": accounting["modelingByRegion"],
            "ebFit": "GBMEBModel (gold_model) with seed 0 on the full modeling frame, 64 features, monotonic_cst registered vector; RF seat inherited from the champion package.",
        },
        "judgedScores": {
            "experiment": "queue item 7 (model-v3-monotone), frozen v3 folds + paired NA-batch physical endpoint",
            "incumbentFamilyMae": stats["incumbent"]["family"],
            "incumbentFourFamilyMean": stats["incumbent"]["four_family_mean"],
            "candidateFamilyMae": stats["candidate"]["family"],
            "candidateFourFamilyMean": stats["candidate"]["four_family_mean"],
            "verdict": results["verdict"],
            "physical": results["physical"]["challenger"],
        },
        "note": (
            "Candidate artefact only. NOT SERVED: nothing is wired into "
            "serving; production calibration gates remain OFF."
        ),
    }
    (out_dir / champion_mod.SIDECAR_NAME).write_text(
        json.dumps(sidecar, indent=2, allow_nan=False), encoding="utf-8"
    )
    artifact_md = "\n".join([
        "# ENS-EB monotone-constrained refit — CANDIDATE — NOT SERVED",
        "",
        "Queue item 7 (model-v3-monotone). Full-pool ENS-EB whose GBM seat",
        "was refit with the registered six-feature monotonic_cst vector;",
        "the RF seat is the packaged champion's fit, byte-identical.",
        "Adopted under the item-7 pre-registered rule (non-inferiority on",
        "the frozen folds + physical-endpoint success); see RESULTS.md in",
        "the model-v3-monotone folder. NOT SERVED.",
        "",
    ])
    (out_dir / "ARTIFACT.md").write_text(artifact_md, encoding="utf-8")

    # Round-trip verification: package alone vs direct fits, in-sample
    # and on the physical-endpoint rows (A1 section 5).  Direct fits use
    # the gold_model EBModel wrappers (proper station site ids); the
    # packaged loader is given site_id = station_id so both routes apply
    # the same EB geography.
    model = champion_mod.ChampionModel.load(out_dir)
    import joblib as _jl
    rfm = gold_model.RFEBModel(0, columns=list(columns))
    rfm.rf = _jl.load(CHAMPION_DIR / "rf.joblib")
    rfm.eb_artifact = champ_sidecar["components"]["rf_eb"]["ebArtifact"]
    mono = gold_model.GBMEBModel(0, columns=list(columns), monotonic_cst=monotonic_cst_vector(columns))
    mono.gbm = _jl.load(OUT / "fullpool_gbm.joblib")
    mono.eb_artifact = mono_eb
    direct = (rfm.predict(frame) + mono.predict(frame)) / 2.0
    frame2 = frame.copy()
    frame2["site_id"] = frame2["station_id"]
    packaged = model.predict(frame2)
    observed = frame["observed_slr"].to_numpy(dtype=float)
    verification = {
        "rf_joblib_byte_identical": bool(rf_hash_ok),
        "round_trip_max_abs_diff": float(np.max(np.abs(packaged - direct))),
        "direct_insample_mae": float(np.mean(np.abs(direct - observed))),
        "packaged_insample_mae": float(np.mean(np.abs(packaged - observed))),
    }
    eframe, _ = endpoint_frame()
    eframe = eframe.sort_values(["source", "point_id", "lead_day"]).reset_index(drop=True)
    _ep_rf, _ep_gbm = _predict_parts_relaxed(model, eframe)
    packaged_endpoint = (_ep_rf + _ep_gbm) / 2.0
    stored_rows = pd.read_csv(OUT / "physical_rows.csv")
    verification["endpoint_round_trip_max_abs_diff"] = float(
        np.max(np.abs(packaged_endpoint - stored_rows["challenger_slr"].to_numpy(dtype=float)))
    )
    (OUT / "package_verification.json").write_text(
        json.dumps(verification, indent=2), encoding="utf-8"
    )
    log(f"package: verification {verification}")
    return verification


# ---------------------------------------------------------------------------
# Stage: dual (contingent descriptive; only if adopted)
# ---------------------------------------------------------------------------

def stage_dual() -> dict | None:
    results = json.loads((OUT / "results.json").read_text())
    if not results["verdict"]["adopted"]:
        log("dual: primary not adopted; MONO-DUAL-PHYS not fitted (registered)")
        return None
    from slr_calibration import champion as champion_mod

    manifest = json.loads((OUT / "physical_manifest.json").read_text())
    frame, columns, _acc = load_frame_and_columns()
    eframe, _ = endpoint_frame()
    eframe = eframe.sort_values(["source", "point_id", "lead_day"]).reset_index(drop=True)
    candidate = champion_mod.ChampionModel.load(OUT / "candidate")
    _rf_part, seat_a = _predict_parts_relaxed(candidate, eframe)
    t0 = time.time()
    seat_b_model = gold_model.GBMEBModel(
        1, columns=list(columns), monotonic_cst=monotonic_cst_vector(columns)
    ).fit(frame)
    _raw_b, seat_b = seat_b_model.predict_parts(eframe)
    ens_dual = (seat_a + seat_b) / 2.0
    work = eframe[["point_id", "source", "window_end_local_date"]].copy()
    work["slr"] = ens_dual
    from slr_calibration.monotone_experiment import resort_key
    keep = set(tuple(k) for k in manifest["window_keys"])
    work["resort"] = work["point_id"].map(resort_key)
    work = work[work.apply(
        lambda r: (r["resort"], r["source"], str(r["window_end_local_date"])) in keep, axis=1
    )]
    triples = build_triples(work, "slr")
    stats = inversion_stats([triples[k] for k in sorted(triples)])
    payload = {
        "description": "both seats constrained on the same six coordinates "
                       "(descriptive only; conditional on primary adoption)",
        "seconds": round(time.time() - t0, 1),
        "stats": stats,
    }
    _results_update("dual_phys", payload)
    log(f"dual: {stats}")
    return payload


# ---------------------------------------------------------------------------
# Stage: report
# ---------------------------------------------------------------------------

def stage_report() -> None:
    results = json.loads((OUT / "results.json").read_text())
    manifest = json.loads((OUT / "physical_manifest.json").read_text())
    fitlog = {}
    if (OUT / "fitlog.json").exists():
        fitlog = json.loads((OUT / "fitlog.json").read_text())
    lines = ["# Queue item 7 — Monotone-Constrained Refit: Results", ""]
    lines.append(f"*Generated by scripts/run_monotone.py. Pre-registration: DESIGN.md + REGISTRATION.md (incl. Amendment A1). Manifest sha256: {manifest['manifest_sha256'][:16]}…*")
    lines.append("")
    v = results.get("verdict", {})
    lines.append(f"## Verdict: {'ADOPTED' if v.get('adopted') else 'NOT ADOPTED'} ({v.get('stage')})")
    lines.append("")
    for d in v.get("detail", []) if isinstance(v.get("detail"), list) else [v.get("detail")]:
        if d:
            lines.append(f"- {d}")
    lines.append("")
    s = results.get("screen")
    if s:
        b = s["builtin_screen_descriptive"]
        lines.append("## Screen")
        lines.append("")
        lines.append(f"- Screen scalar {b['screen_scalar']:+.4f}; pooled LORO gain {b['pooled_loro_gain']:+.4f}; sampled gains {b['sampled_family_gain']}; region gains {b['loro_region_gain']}.")
        lines.append(f"- Registered kill clause: killed={s['registered_kill']['killed']} {s['registered_kill']['clauses']}.")
        lines.append(f"- Built-in improvement screen (descriptive only): killed={b['killed']} {b['kill_clauses']}.")
        lines.append("")
    f = results.get("full")
    if f:
        lines.append("## Full judge (28 instances)")
        lines.append("")
        lines.append(f"- Four-family gain {f['four_family_gain']:+.4f}; family gains {f['family_gain']}; region gains {f['loro_region_gain']}.")
        lines.append(f"- Incumbent family MAEs {f['family_stats']['incumbent']['family']} (four-family {f['family_stats']['incumbent']['four_family_mean']:.4f}); candidate {f['family_stats']['candidate']['family']} (four-family {f['family_stats']['candidate']['four_family_mean']:.4f}).")
        lines.append(f"- Non-inferiority (registered): passed={f['noninferiority']['passed']} {f['noninferiority']['failed_clauses']}.")
        lines.append(f"- Standing improvement rule (descriptive): adopted={f['standing_rule_descriptive']['adopted']} {f['standing_rule_descriptive']['failed_clauses']}.")
        lines.append(f"- Slices: {f['slices']}.")
        lines.append("")
    p = results.get("physical")
    if p:
        lines.append("## Physical endpoint (paired, frozen NA batch)")
        lines.append("")
        lines.append(f"- Champion reproduction max abs diff: {p['champion_reproduction_max_abs_diff']:.3e} (gate 1e-6).")
        lines.append(f"- Champion: {p['champion']}.")
        lines.append(f"- Challenger: {p['challenger']}.")
        lines.append(f"- Raw stage (pre-EB seat means): champion {p['champion_raw_stage']}; challenger {p['challenger_raw_stage']}.")
        lines.append(f"- Per arm: {p['per_arm']}.")
        lines.append(f"- Descriptives: {p['descriptives']}.")
        lines.append(f"- Physical success (registered): passed={p['physical_success']['passed']} {p['physical_success']['failed_clauses']}.")
        lines.append("")
    lines.append("## Pre-fit audits and integrity")
    lines.append("")
    lines.append(f"- Direction audit: {manifest['direction_audit']}.")
    ident = manifest["cached_identity_max_abs_diff"]
    lines.append(f"- Cached identity ens_eb = mean(rf_eb, gbm_eb): max diff {max(ident.values()):.3e} over 28 instances.")
    controls = {k: r for k, r in fitlog.items() if k.startswith("control:")}
    lines.append(f"- Unconstrained control refits (screen instances): {controls}.")
    lines.append("")
    if "dual_phys" in results:
        lines.append("## MONO-DUAL-PHYS (contingent descriptive)")
        lines.append("")
        lines.append(f"- {results['dual_phys']}.")
        lines.append("")
    lines.append("## Serving")
    lines.append("")
    lines.append("- Serving is unchanged under every outcome; any candidate package is packaged-unserved. Production calibration gates untouched.")
    (OUT / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")
    log("report written")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["manifest", "fit", "judge", "physical", "verdict", "package", "dual", "report"])
    parser.add_argument("--which", default="all", choices=["screen", "rest", "all"])
    parser.add_argument("--instances", default=None)
    parser.add_argument("--judge-stage", default="full", choices=["screen", "full"])
    args = parser.parse_args()
    if args.stage == "manifest":
        stage_manifest()
    elif args.stage == "fit":
        stage_fit(args.which, args.instances)
    elif args.stage == "judge":
        stage_judge(args.judge_stage)
    elif args.stage == "physical":
        stage_physical()
    elif args.stage == "verdict":
        stage_verdict()
    elif args.stage == "package":
        stage_package()
    elif args.stage == "dual":
        stage_dual()
    else:
        stage_report()


if __name__ == "__main__":
    main()
