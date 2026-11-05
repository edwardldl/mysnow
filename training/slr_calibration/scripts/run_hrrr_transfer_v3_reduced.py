"""Queue item 7 - INTERIM reduced-cohort paired v3 HRRR transfer read.

The user chose to score sooner on the subset of eligible events whose
corrected (UTC-convention) HRRR inputs are already complete in the
still-filling v3 cache, rather than wait for the full fetch. This script
never touches the full pipeline's outputs: everything lands in
model-v3-hrrr-transfer/reduced/. Scoring reuses the item runner's own
stage_score (with its OUT redirected here) so the integrity gate and
paired construction are byte-identical to the full run's. The report is
an interim read against the pre-registered thresholds, stated
descriptively; the formal verdict remains with the full cohort.

Stages: census -> snapshot -> score -> report.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "training" / "slr_calibration" / "src"))

from slr_calibration.hrrr_transfer_v3 import (  # noqa: E402
    build_v3_transfer_features,
    decision_outcome,
    paired_block,
)

BASE = REPO / "data" / "slr-training-data" / "screened-v2" / "model-v1"
OUT = BASE / "model-v3-hrrr-transfer"
REDUCED = OUT / "reduced"
STEP5_CACHE = BASE / "transfer-hrrr" / "raw_cache_hrrr"
V3_CACHE = OUT / "raw_cache_hrrr_v3"
FETCH_LOG = Path("/tmp/item7_fetch_utc.log")

# Operational subset gate (documented in REPORT_REDUCED.md): enough
# events, broad regional span, no single region dominating, more than
# one water year materially present.
GATE_MIN_COMPLETE = 1500
GATE_MIN_REGIONS = 4
GATE_MIN_PER_REGION = 50
GATE_MAX_REGION_SHARE = 0.70
GATE_MIN_WATER_YEARS = 2
GATE_MIN_PER_WATER_YEAR = 200


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "runner7", Path(__file__).with_name("run_hrrr_transfer_v3.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fetch_progress() -> str:
    try:
        lines = FETCH_LOG.read_text(errors="replace").splitlines()
    except OSError:
        return "fetch log unavailable"
    prog = [ln for ln in lines if "files (" in ln]
    return prog[-1].strip() if prog else "no progress line yet"


def cache_file_count() -> int:
    return sum(1 for _ in V3_CACHE.rglob("*.json.gz"))


def composition(rows: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {str(e["event_id"]): e for e in events}
    comp = [r for r in rows if r["hrrr_complete"]]
    regions: Counter[str] = Counter()
    wy: Counter[str] = Counter()
    months: Counter[str] = Counter()
    bands: Counter[str] = Counter()
    for r in comp:
        e = by_id[str(r["event_id"])]
        regions[str(e["region"])] += 1
        wy[str(int(e["water_year"]))] += 1
        months[str(int(e["month"]))] += 1
        bands[str(r["lead_band"])] += 1
    return {
        "n": len(comp),
        "byRegion": dict(sorted(regions.items())),
        "byWaterYear": dict(sorted(wy.items())),
        "byMonth": dict(sorted(months.items())),
        "byLeadBand": dict(sorted(bands.items())),
    }


def gate(comp: dict[str, Any]) -> dict[str, Any]:
    n = comp["n"]
    regions = comp["byRegion"]
    wy = comp["byWaterYear"]
    big_regions = {r: c for r, c in regions.items() if c >= GATE_MIN_PER_REGION}
    big_wy = {y: c for y, c in wy.items() if c >= GATE_MIN_PER_WATER_YEAR}
    max_share = (max(regions.values()) / n) if n and regions else 1.0
    checks = {
        "n_complete>=1500": n >= GATE_MIN_COMPLETE,
        "regions>=4_with>=50": len(big_regions) >= GATE_MIN_REGIONS,
        "max_region_share<=0.70": max_share <= GATE_MAX_REGION_SHARE,
        "water_years>=2_with>=200": len(big_wy) >= GATE_MIN_WATER_YEARS,
    }
    return {
        "checks": checks,
        "maxRegionShare": round(max_share, 4),
        "regionsWith>=50": big_regions,
        "waterYearsWith>=200": big_wy,
        "proceed": all(checks.values()),
    }


def build_now():
    runner = _load_runner()
    events = runner.load_events()
    rows, coverage = build_v3_transfer_features(events, V3_CACHE, STEP5_CACHE)
    return events, rows, coverage


def stage_census() -> dict[str, Any]:
    events, rows, coverage = build_now()
    comp = composition(rows, events)
    g = gate(comp)
    record = {
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fetchProgress": fetch_progress(),
        "cacheFiles": cache_file_count(),
        "coverage": coverage,
        "composition": comp,
        "gate": g,
    }
    REDUCED.mkdir(parents=True, exist_ok=True)
    with open(REDUCED / "census_log.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    log("census: complete=%d gate=%s" % (comp["n"], g["proceed"]))
    print(json.dumps({"composition": comp, "gate": g}, indent=2))
    return record


def stage_snapshot() -> dict[str, Any]:
    events, rows, coverage = build_now()
    comp = composition(rows, events)
    g = gate(comp)
    if not g["proceed"]:
        raise RuntimeError(f"subset gate not met, refusing snapshot: {g}")
    frame = pd.DataFrame(rows)
    frame["hrrr_source_counts"] = frame["hrrr_source_counts"].apply(json.dumps)
    REDUCED.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(REDUCED / "features_hrrr_v3.parquet", index=False)
    (REDUCED / "feature_coverage_v3.json").write_text(json.dumps(coverage, indent=2))
    meta = {
        "kind": "interim-reduced-cohort-snapshot",
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fetchProgressAtSnapshot": fetch_progress(),
        "cacheFilesAtSnapshot": cache_file_count(),
        "composition": comp,
        "gate": g,
        "selectionMechanism": (
            "fetch-order availability: the corrected fetch processes full "
            "files sorted by (domain, cycle, fxx) - Alaska domain first, "
            "then CONUS in ascending cycle order - so the subset is the "
            "events whose 25 window-endpoint files had all landed when "
            "the snapshot was taken. It is not a random sample."
        ),
    }
    (REDUCED / "subset_composition.json").write_text(json.dumps(meta, indent=2))
    log(f"snapshot written: {comp['n']} complete events")
    return meta


def stage_score() -> None:
    runner = _load_runner()
    runner.OUT = REDUCED
    runner.stage_score(None)


def _rows(frame: pd.DataFrame) -> list[dict[str, float]]:
    return [
        {"observed": float(o), "era5": float(e), "hrrr": float(h)}
        for o, e, h in zip(frame["observed"], frame["era5_pred"], frame["hrrr_pred"])
    ]


def stage_report() -> dict[str, Any]:
    fitlog_path = REDUCED / "fitlog.json"
    fitlog = json.loads(fitlog_path.read_text()) if fitlog_path.exists() else {}
    failures = {k: v for k, v in fitlog.items() if v.get("status") == "integrity_failed"}
    if failures:
        raise RuntimeError(f"integrity failures recorded: {sorted(failures)}")
    parts = [pd.read_parquet(p) for p in sorted((REDUCED / "paired").glob("*.parquet"))]
    all_rows = pd.concat(parts, ignore_index=True)
    subset = json.loads((REDUCED / "subset_composition.json").read_text())
    coverage = json.loads((REDUCED / "feature_coverage_v3.json").read_text())

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
        "kind": "INTERIM reduced-cohort read - NOT the item's formal verdict",
        "subset": subset,
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
        "decision_descriptive_only": decision,
    }
    (REDUCED / "results_reduced.json").write_text(json.dumps(results, indent=2))
    _write_report(results)
    log(f"report: pooled penalty {pooled['penalty']:+.4f} -> {decision['outcome']} (descriptive)")
    return results


def _fmt(value: Any, digits: int = 4) -> str:
    return "-" if value is None else f"{float(value):+.{digits}f}"


def _mae(value: Any) -> str:
    return "-" if value is None else f"{float(value):.4f}"


def _write_report(r: dict[str, Any]) -> None:
    pooled = r["pooled_all_instances"]
    subset = r["subset"]
    comp = subset["composition"]
    lines = [
        "# Queue Item 7 - Paired v3 HRRR Transfer Check: INTERIM Reduced-Cohort Report",
        "",
        "**This is an interim read on a reduced cohort, NOT the item's "
        "formal verdict.** The formal verdict remains with the full "
        "cohort (23,074 eligible events) when the corrected fetch "
        "completes. Pre-registration: `../DESIGN.md`. Measurement only - "
        "no correction was fitted, no model changed, nothing packaged.",
        "",
        "## Subset (selection caveat first)",
        "",
        f"- Snapshot taken {subset['utc']} while the corrected fetch was "
        f"still running ({subset['fetchProgressAtSnapshot']}; "
        f"{subset['cacheFilesAtSnapshot']} cache files on disk).",
        f"- Selection mechanism: {subset['selectionMechanism']}",
        f"- Complete paired events in subset: **{comp['n']}** of 23,074 eligible.",
        f"- By region: {json.dumps(comp['byRegion'], sort_keys=True)}",
        f"- By water year: {json.dumps(comp['byWaterYear'], sort_keys=True)}",
        f"- By lead band: {json.dumps(comp['byLeadBand'], sort_keys=True)} "
        "(Alaska's band is its region - confounded, as in the design).",
        "- Because availability follows fetch order (Alaska first, then "
        "CONUS chronologically), the subset over-weights the earliest "
        "CONUS cycles relative to the full cohort. Treat every number "
        "below as descriptive of this subset only.",
        "",
        "## Indicative outcome (descriptive, vs the pre-registered rule)",
        "",
        f"**Outcome if this were the full cohort: `{r['decision_descriptive_only']['outcome']}`**",
        "",
    ]
    for reason in r["decision_descriptive_only"]["reasons"]:
        lines.append(f"- {reason}")
    lines += [
        "",
        f"Pooled paired penalty (all 28 frozen instances, {pooled['n']} paired "
        f"instance-events): **{_fmt(pooled['penalty'])}** SLR MAE "
        f"(ERA5-input MAE {_mae(pooled['mae_era5'])}, HRRR-input MAE {_mae(pooled['mae_hrrr'])}).",
        "",
        "## Integrity",
        "",
        f"Refit-vs-cache ENS-EB max abs diff over all scored instances: "
        f"{r['integrity_max_abs_diff_overall']:.2e} (gate <= 1e-6). The ERA5 arm of "
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
                f"| {family} | {v['n']} | {_mae(v['mae_era5'])} | "
                f"{_mae(v['mae_hrrr'])} | {_fmt(v['penalty'])} |"
            )
    lines += [
        "",
        "## By region (that region's own LORO instance - the decision read)",
        "",
        "| region | n | ERA5 MAE | HRRR MAE | penalty |",
        "|---|---:|---:|---:|---:|",
    ]
    for region, v in sorted(r["regions_loro"].items()):
        lines.append(
            f"| {region} | {v['n']} | {_mae(v['mae_era5'])} | "
            f"{_mae(v['mae_hrrr'])} | {_fmt(v['penalty'])} |"
        )
    lines += [
        "",
        "## By lead band (LORO-unique set; Alaska's band is its region - confounded)",
        "",
        "| lead band | n | ERA5 MAE | HRRR MAE | penalty |",
        "|---|---:|---:|---:|---:|",
    ]
    for band, v in sorted(r["lead_bands_loro_unique"].items()):
        lines.append(
            f"| {band} | {v['n']} | {_mae(v['mae_era5'])} | "
            f"{_mae(v['mae_hrrr'])} | {_fmt(v['penalty'])} |"
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
        "- Interim subset only: fetch-order availability, not a random "
        "sample; composition above. The full-cohort verdict may differ.",
        "- The paired cohort is the HRRR-v4-era subset of the gold pool "
        "(WY2021-2026, mostly northeast + great-lakes lowland stations); "
        "sierra-nevada and alaska are thin even in the full cohort.",
        "- Short-lead transfer only (CONUS f01; Alaska f01-f03). Not a "
        "snowfall-total measurement, not long-lead, not alpine validation.",
        "- The convective-split features (convective_frac, lsp_total_mm, "
        "cp_total_mm) are absent on HRRR inputs by construction - the "
        "same absence the production HRRR serving adapter has. That "
        "absence is part of the measured penalty.",
        "",
    ]
    (REDUCED / "REPORT_REDUCED.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["census", "snapshot", "score", "report"])
    args = parser.parse_args()
    if args.stage == "census":
        stage_census()
    elif args.stage == "snapshot":
        stage_snapshot()
    elif args.stage == "score":
        stage_score()
    else:
        stage_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
