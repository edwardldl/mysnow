"""Queue item 7 runner: paired v3 HRRR transfer check.

Stages: fetch -> build -> score -> report.  Pre-registration in
model-v3-hrrr-transfer/DESIGN.md.  Measurement only.
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

from slr_calibration import gold_model  # noqa: E402
from slr_calibration.gold_profiles_sp import load_modelling_events  # noqa: E402
from slr_calibration.hrrr_transfer import PooledRangeFetcher  # noqa: E402
from slr_calibration.hrrr_transfer_v3 import (  # noqa: E402
    build_v3_transfer_features,
    decision_outcome,
    eligible_events,
    fetch_v3_files,
    paired_block,
)
from slr_calibration.prediction_cache import (  # noqa: E402
    PredictionCache,
    cohort_hash,
    feature_hash,
    manifest_hash,
    split_instances,
)

BASE = REPO / "data" / "slr-training-data" / "screened-v2" / "model-v1"
POOL = REPO / "data" / "slr-training-data" / "screened-v2"
FEATURES = BASE / "model-v3-final" / "features_v3.parquet"
V2_FEATURES = BASE / "era5-expanded-v2" / "features.parquet"
COLUMNS_JSON = BASE / "model-v3" / "screens_v2" / "final_combination.json"
QUEUE1 = BASE / "model-v3-queue1"
STEP5_CACHE = BASE / "transfer-hrrr" / "raw_cache_hrrr"
OUT = BASE / "model-v3-hrrr-transfer"
V3_CACHE = OUT / "raw_cache_hrrr_v3"
INTEGRITY_TOLERANCE = 1e-6


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def load_columns() -> list[str]:
    return json.loads(COLUMNS_JSON.read_text())["columns"]


def load_events() -> list[dict[str, Any]]:
    events = load_modelling_events(POOL, V2_FEATURES)
    frame, _acc = gold_model.load_modeling_frame(FEATURES)
    frame_ids = {str(e) for e in frame["event_id"]}
    events = [e for e in events if str(e["event_id"]) in frame_ids]
    return eligible_events(events)


def stage_fetch() -> dict[str, Any]:
    events = load_events()
    log(f"fetch: {len(events)} eligible events")
    fetcher = PooledRangeFetcher(max_retries=int(__import__("os").environ.get("FETCH_RETRIES", "4")))
    summary = fetch_v3_files(events, V3_CACHE, STEP5_CACHE, fetcher)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fetch_summary_v3.json").write_text(json.dumps(summary, indent=2))
    log(f"fetch done: {summary['statusCounts']}")
    return summary


def stage_build() -> dict[str, Any]:
    events = load_events()
    log(f"build: {len(events)} eligible events")
    rows, coverage = build_v3_transfer_features(events, V3_CACHE, STEP5_CACHE)
    frame = pd.DataFrame(rows)
    frame["hrrr_source_counts"] = frame["hrrr_source_counts"].apply(json.dumps)
    OUT.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(OUT / "features_hrrr_v3.parquet", index=False)
    (OUT / "feature_coverage_v3.json").write_text(json.dumps(coverage, indent=2))
    log(f"build done: {coverage['completeCounts']}")
    return coverage


def load_manifests() -> dict[str, Any]:
    return json.loads((QUEUE1 / "manifests.json").read_text(encoding="utf-8"))


def make_queue1_cache(manifests: dict[str, Any], columns: list[str]) -> PredictionCache:
    return PredictionCache(
        QUEUE1 / "cache",
        cohort=cohort_hash(FEATURES),
        features=feature_hash(columns),
        manifests=manifest_hash(manifests),
    )


def split_frames(frame: pd.DataFrame, inst: dict[str, Any]):
    if inst["family"] == "randomised":
        return gold_model.split_random(frame, seed=1000 + inst["rep"])
    if inst["family"] == "loro":
        return gold_model.split_loro(frame, inst["region"])
    if inst["family"] == "temporal":
        return gold_model.split_temporal(frame)
    return gold_model.split_stations(frame, seed=4000)


def _fitlog_update(key: str, record: dict[str, Any]) -> None:
    path = OUT / "fitlog.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[key] = record
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def score_instance(
    inst: dict[str, Any],
    era: pd.DataFrame,
    hrrr_frame: pd.DataFrame,
    complete_ids: set[str],
    manifests: dict[str, Any],
    cache: PredictionCache,
    columns: list[str],
) -> None:
    split_id = inst["split_id"]
    seed = inst["fit_seed"]
    out_path = OUT / "paired" / f"{split_id}.parquet"
    if out_path.exists():
        log(f"score {split_id}: paired file exists, skip")
        return
    test_ids = manifests[split_id]["test_event_ids"]
    paired_ids = [e for e in test_ids if e in complete_ids]
    if not paired_ids:
        log(f"score {split_id}: no paired events")
        _fitlog_update(split_id, {"n_paired": 0, "status": "no_paired_events"})
        return
    stored = cache.get("ens_eb", split_id, seed, test_ids)
    if stored is None:
        raise RuntimeError(f"queue-1 cache miss for ens_eb {split_id}")
    position = {eid: i for i, eid in enumerate(test_ids)}
    stored_paired = np.array([stored[position[e]] for e in paired_ids])

    train, test = split_frames(era, inst)
    assert [str(e) for e in test["event_id"].tolist()] == test_ids, split_id
    era_by_id = test.set_index("event_id")
    hrrr_by_id = hrrr_frame.set_index("event_id")
    era_paired = era_by_id.loc[paired_ids].reset_index()
    hrrr_paired = hrrr_by_id.loc[paired_ids].reset_index()

    t0 = time.time()
    rf = gold_model.RFEBModel(seed, columns=list(columns)).fit(train)
    gbm = gold_model.GBMEBModel(seed, columns=list(columns)).fit(train)
    _, rf_era = rf.predict_parts(era_paired)
    _, gbm_era = gbm.predict_parts(era_paired)
    ens_era_refit = (rf_era + gbm_era) / 2.0
    integrity = float(np.max(np.abs(ens_era_refit - stored_paired)))
    if integrity > INTEGRITY_TOLERANCE:
        _fitlog_update(split_id, {
            "status": "integrity_failed", "integrity_max_abs_diff": integrity,
            "n_paired": len(paired_ids),
        })
        log(f"score {split_id}: INTEGRITY FAILED diff={integrity:.3e}; no paired file written")
        del rf, gbm
        gc.collect()
        return
    _, rf_h = rf.predict_parts(hrrr_paired)
    _, gbm_h = gbm.predict_parts(hrrr_paired)
    ens_hrrr = (rf_h + gbm_h) / 2.0
    seconds = time.time() - t0
    out = pd.DataFrame({
        "split_id": split_id,
        "family": inst["family"],
        "event_id": paired_ids,
        "region": era_paired["region"].astype(str).tolist(),
        "lead_band": hrrr_paired["lead_band"].astype(str).tolist(),
        "observed": era_paired["observed_slr"].astype(float).tolist(),
        "era5_pred": stored_paired,
        "hrrr_pred": ens_hrrr,
        "kuchera_era5": era_paired["baseline_slr"].astype(float).tolist(),
        "kuchera_hrrr": hrrr_paired["baseline_slr"].astype(float).tolist(),
    })
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, index=False)
    _fitlog_update(split_id, {
        "status": "ok", "seconds": round(seconds, 1),
        "integrity_max_abs_diff": integrity, "n_paired": len(paired_ids),
    })
    log(f"score {split_id}: {len(paired_ids)} paired, integrity {integrity:.2e}, {seconds:.0f}s")
    del rf, gbm
    gc.collect()


def stage_score(only: str | None) -> None:
    columns = load_columns()
    manifests = load_manifests()
    cache = make_queue1_cache(manifests, columns)
    era, _acc = gold_model.load_modeling_frame(FEATURES)
    hrrr = pd.read_parquet(OUT / "features_hrrr_v3.parquet")
    complete_ids = {str(e) for e in hrrr.loc[hrrr["hrrr_complete"], "event_id"]}
    log(f"score: {len(complete_ids)} complete events")
    # HRRR modelling frame: ERA5 rows with feature columns + baseline
    # overwritten by the HRRR-built values (metadata byte-identical).
    h_by_id = hrrr.set_index("event_id")
    hrrr_frame = era[era["event_id"].astype(str).isin(complete_ids)].copy()
    aligned = h_by_id.loc[hrrr_frame["event_id"].astype(str)]
    for col in list(columns) + ["baseline_slr"]:
        hrrr_frame[col] = aligned[col].to_numpy()
    hrrr_frame["lead_band"] = aligned["lead_band"].to_numpy()

    instances = split_instances()
    if only:
        wanted = set(only.split(","))
        instances = [i for i in instances if i["split_id"] in wanted]
    for inst in instances:
        score_instance(inst, era, hrrr_frame, complete_ids, manifests, cache, columns)


def _rows(frame: pd.DataFrame) -> list[dict[str, float]]:
    return [
        {"observed": float(o), "era5": float(e), "hrrr": float(h)}
        for o, e, h in zip(frame["observed"], frame["era5_pred"], frame["hrrr_pred"])
    ]


def stage_report() -> dict[str, Any]:
    fitlog = json.loads((OUT / "fitlog.json").read_text()) if (OUT / "fitlog.json").exists() else {}
    failures = {k: v for k, v in fitlog.items() if v.get("status") == "integrity_failed"}
    if failures:
        raise RuntimeError(f"integrity failures recorded: {sorted(failures)}")
    parts = [pd.read_parquet(p) for p in sorted((OUT / "paired").glob("*.parquet"))]
    all_rows = pd.concat(parts, ignore_index=True)
    coverage = json.loads((OUT / "feature_coverage_v3.json").read_text())

    pooled = paired_block(_rows(all_rows))
    families: dict[str, Any] = {}
    for family, group in all_rows.groupby("family"):
        families[str(family)] = paired_block(_rows(group))
    loro = all_rows[all_rows["family"] == "loro"]
    regions: dict[str, Any] = {}
    for region, group in loro.groupby("region"):
        regions[str(region)] = paired_block(_rows(group))
    bands: dict[str, Any] = {}
    for band, group in loro.groupby("lead_band"):
        bands[str(band)] = paired_block(_rows(group))
    loro_unique = paired_block(_rows(loro))
    kuchera = {
        "mae_era5": float(np.mean(np.abs(loro["kuchera_era5"] - loro["observed"]))),
        "mae_hrrr": float(np.mean(np.abs(loro["kuchera_hrrr"] - loro["observed"]))),
        "n": int(len(loro)),
    }
    decision = decision_outcome(
        float(pooled["penalty"]),
        {r: float(v["penalty"]) for r, v in regions.items()},
        {b: float(v["penalty"]) for b, v in bands.items()},
    )
    integrity = {
        split: entry.get("integrity_max_abs_diff")
        for split, entry in sorted(fitlog.items())
        if entry.get("status") == "ok"
    }
    results = {
        "item": "queue-7-paired-v3-hrrr-transfer",
        "coverage": coverage,
        "pooled_all_instances": pooled,
        "families": families,
        "loro_unique": loro_unique,
        "regions_loro": regions,
        "lead_bands_loro_unique": bands,
        "kuchera_context_loro_unique": kuchera,
        "integrity_max_abs_diff_by_instance": integrity,
        "integrity_max_abs_diff_overall": max(
            (v for v in integrity.values() if v is not None), default=None
        ),
        "decision": decision,
    }
    (OUT / "results.json").write_text(json.dumps(results, indent=2))
    _write_report(results)
    log(f"report: pooled penalty {pooled['penalty']:+.4f} -> {decision['outcome']}")
    return results


def _fmt(value: Any, digits: int = 4) -> str:
    return "—" if value is None else f"{float(value):+.{digits}f}"


def _mae(value: Any) -> str:
    return "—" if value is None else f"{float(value):.{4}f}"


def _write_report(r: dict[str, Any]) -> None:
    pooled = r["pooled_all_instances"]
    lines = [
        "# Queue Item 7 — Paired v3 HRRR Transfer Check: Report",
        "",
        "Pre-registration: `DESIGN.md` (same directory). Measurement only —",
        "no correction was fitted, no model changed, nothing packaged.",
        "",
        "## Verdict",
        "",
        f"**Decision outcome: `{r['decision']['outcome']}`**",
        "",
    ]
    for reason in r["decision"]["reasons"]:
        lines.append(f"- {reason}")
    lines += [
        "",
        f"Pooled paired penalty (all 28 frozen instances, {pooled['n']} paired",
        f"instance-events): **{_fmt(pooled['penalty'])}** SLR MAE "
        f"(ERA5-input MAE {_mae(pooled['mae_era5'])}, HRRR-input MAE {_mae(pooled['mae_hrrr'])}).",
        "",
        "## Funnel",
        "",
        f"- Screened-v2 modelling pool: 57,376 events.",
        f"- HRRR-v4-era eligible (window start ≥ 2020-12-03Z): {r['coverage']['eventsTotal']}.",
        f"- HRRR build status: {json.dumps(r['coverage']['statusCounts'], sort_keys=True)}.",
        f"- Complete paired events (base ok, profile/w/q/accumu 24/24): "
        f"{r['coverage']['completeCounts'].get('complete', 0)}.",
        "",
        "## Integrity",
        "",
        f"Refit-vs-cache ENS-EB max abs diff over all instances: "
        f"{r['integrity_max_abs_diff_overall']:.3e} (gate ≤ 1e-6). The ERA5 arm of",
        "every pair is the stored queue-1 cache prediction.",
        "",
        "## By family (all instances pooled within family)",
        "",
        "| family | n | ERA5 MAE | HRRR MAE | penalty |",
        "|---|---:|---:|---:|---:|",
    ]
    for family in ("randomised", "loro", "temporal", "stations"):
        v = r["families"].get(family)
        if v:
            lines.append(
                f"| {family} | {v['n']} | {_mae(v['mae_era5'])} | {_mae(v['mae_hrrr'])} | {_fmt(v['penalty'])} |"
            )
    lines += [
        "",
        "## By region (that region's own LORO instance — the decision read)",
        "",
        "| region | n | ERA5 MAE | HRRR MAE | penalty |",
        "|---|---:|---:|---:|---:|",
    ]
    for region, v in sorted(r["regions_loro"].items()):
        lines.append(
            f"| {region} | {v['n']} | {_mae(v['mae_era5'])} | {_mae(v['mae_hrrr'])} | {_fmt(v['penalty'])} |"
        )
    lines += [
        "",
        "## By lead band (LORO-unique set; Alaska's band is its region — confounded)",
        "",
        "| lead band | n | ERA5 MAE | HRRR MAE | penalty |",
        "|---|---:|---:|---:|---:|",
    ]
    for band, v in sorted(r["lead_bands_loro_unique"].items()):
        lines.append(
            f"| {band} | {v['n']} | {_mae(v['mae_era5'])} | {_mae(v['mae_hrrr'])} | {_fmt(v['penalty'])} |"
        )
    lu = r["loro_unique"]
    kuch = r["kuchera_context_loro_unique"]
    lines += [
        "",
        f"LORO-unique overall (each paired event once): penalty {_fmt(lu['penalty'])} "
        f"(n {lu['n']}). Kuchera context on the same events: ERA5-input MAE "
        f"{kuch['mae_era5']:.4f}, HRRR-input MAE {kuch['mae_hrrr']:.4f}.",
        "",
        "## Caveats",
        "",
        "- The paired cohort is the HRRR-v4-era subset of the gold pool",
        "  (WY2021–2026, ~78% northeast + great-lakes lowland stations);",
        "  sierra-nevada and alaska are thin. Selection effects are structural.",
        "- Short-lead transfer only (CONUS f01; Alaska f01–f03) under the",
        "  step-5 cycle convention. Not a long-lead forecast measurement,",
        "  not a snowfall-total measurement, not alpine validation.",
        "- The convective-split features (convective_frac, lsp_total_mm,",
        "  cp_total_mm) are absent on HRRR inputs by construction — the",
        "  same absence the production HRRR serving adapter has (no ACPCP",
        "  exists in wrfprs). That absence is part of the measured penalty.",
        "",
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["fetch", "build", "score", "report"])
    parser.add_argument("--only", default=None, help="comma-separated split ids (score)")
    args = parser.parse_args()
    if args.stage == "fetch":
        stage_fetch()
    elif args.stage == "build":
        stage_build()
    elif args.stage == "score":
        stage_score(args.only)
    else:
        stage_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
