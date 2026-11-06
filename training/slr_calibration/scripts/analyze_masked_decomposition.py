"""Correction study, diagnostic stage: masked-ERA5 decomposition analysis.

Joins the masked-arm paired outputs (model-v3-hrrr-correction/masked/
paired/) with the item-7 reduced cohort's paired outputs on
(split_id, event_id) and computes, for identical events and identical
fold models:

    L_E    MAE of ENS-EB on ERA5 inputs        (stored queue-1 cache)
    L_E,m  MAE of ENS-EB on ERA5-masked inputs (convective split absent)
    L_H    MAE of ENS-EB on HRRR inputs        (reduced run)

    total penalty  = L_H - L_E
    masking term   = L_E,m - L_E
    remainder      = L_H - L_E,m        (total = masking + remainder)

An accounting decomposition, not a causal attribution: either term may
be negative or exceed the total. Reported pooled (all instances),
by family, by region (own-LORO), and by water year (LORO-unique and
pooled). Writes results_decomposition.json + DECOMPOSITION.md into
model-v3-hrrr-correction/.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "data" / "slr-training-data" / "screened-v2" / "model-v1"
TRANSFER = BASE / "model-v3-hrrr-transfer"
CORRECTION = BASE / "model-v3-hrrr-correction"
FEATURES = BASE / "model-v3-final" / "features_v3.parquet"


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def block(df: pd.DataFrame) -> dict[str, Any]:
    n = len(df)
    if n == 0:
        return {"n": 0, "mae_era5": None, "mae_masked": None, "mae_hrrr": None,
                "masking": None, "remainder": None, "total": None}
    mae_e = float(np.mean(np.abs(df["era5_pred"] - df["observed"])))
    mae_m = float(np.mean(np.abs(df["masked_pred"] - df["observed"])))
    mae_h = float(np.mean(np.abs(df["hrrr_pred"] - df["observed"])))
    return {
        "n": int(n),
        "mae_era5": mae_e,
        "mae_masked": mae_m,
        "mae_hrrr": mae_h,
        "masking": mae_m - mae_e,
        "remainder": mae_h - mae_m,
        "total": mae_h - mae_e,
    }


def grouped(df: pd.DataFrame, key: str) -> dict[str, Any]:
    return {str(k): block(g) for k, g in sorted(df.groupby(key), key=lambda kv: str(kv[0]))}


def main() -> int:
    red_parts = [pd.read_parquet(p) for p in sorted((TRANSFER / "reduced" / "paired").glob("*.parquet"))]
    red = pd.concat(red_parts, ignore_index=True)
    msk_parts = [pd.read_parquet(p) for p in sorted((CORRECTION / "masked" / "paired").glob("*.parquet"))]
    msk = pd.concat(msk_parts, ignore_index=True)
    log(f"reduced paired rows {len(red)}, masked paired rows {len(msk)}")
    msk = msk[["split_id", "event_id", "era5_pred", "hrrr_pred", "observed"]].rename(
        columns={"hrrr_pred": "masked_pred", "era5_pred": "era5_pred_maskedrun",
                 "observed": "observed_maskedrun"}
    )
    df = red.merge(msk, on=["split_id", "event_id"], how="inner", validate="one_to_one")
    log(f"joined rows {len(df)}")
    if len(df) != len(red):
        raise RuntimeError("join lost reduced rows - paired sets differ")
    d_era = float(np.max(np.abs(df["era5_pred"] - df["era5_pred_maskedrun"])))
    d_obs = float(np.max(np.abs(df["observed"] - df["observed_maskedrun"])))
    log(f"join checks: max |era5_pred diff| {d_era:.3e}, max |observed diff| {d_obs:.3e}")
    if d_era > 0 or d_obs > 0:
        raise RuntimeError("stored-arm mismatch between runs")

    era = pd.read_parquet(FEATURES, columns=["event_id", "water_year"])
    wy = {str(r.event_id): int(r.water_year) for r in era.itertuples(index=False)}
    df["water_year"] = df["event_id"].astype(str).map(wy)

    fitlog = json.loads((CORRECTION / "masked" / "fitlog.json").read_text())
    integrity = {
        k: v.get("integrity_max_abs_diff") for k, v in sorted(fitlog.items())
        if v.get("status") == "ok"
    }
    build_summary = json.loads((CORRECTION / "masked" / "masked_build_summary.json").read_text())

    loro = df[df["family"] == "loro"]
    results: dict[str, Any] = {
        "study": "hrrr-correction-diagnostic-masked-decomposition",
        "cohort": "item-7 reduced cohort (5,340 complete paired events, fetch-order availability)",
        "arms": {
            "L_E": "ENS-EB on ERA5 inputs (stored queue-1 cache predictions)",
            "L_E,m": "ENS-EB on ERA5 inputs with cp/lsp source variables absent "
                     "(production-HRRR representation), standard v3 builders, same fold models",
            "L_H": "ENS-EB on HRRR inputs (item-7 reduced run)",
        },
        "identity": "total = masking + remainder, exactly, on every block",
        "build_parity": build_summary["parity"],
        "masked_diff_features": build_summary["masked_diff_features"],
        "integrity_max_abs_diff_by_instance": integrity,
        "integrity_max_abs_diff_overall": max(v for v in integrity.values() if v is not None),
        "pooled_all_instances": block(df),
        "families": grouped(df, "family"),
        "loro_unique": block(loro),
        "regions_loro": grouped(loro, "region"),
        "water_years_loro_unique": grouped(loro, "water_year"),
        "water_years_pooled": grouped(df, "water_year"),
        "lead_bands_loro_unique": grouped(loro, "lead_band"),
    }
    (CORRECTION / "results_decomposition.json").write_text(json.dumps(results, indent=2))
    _write_report(results)
    p = results["pooled_all_instances"]
    log(f"pooled: masking {p['masking']:+.4f}, remainder {p['remainder']:+.4f}, total {p['total']:+.4f}")
    return 0


def _f(v: Any) -> str:
    return "—" if v is None else f"{float(v):+.4f}"


def _m(v: Any) -> str:
    return "—" if v is None else f"{float(v):.4f}"


def _table(lines: list[str], title: str, blocks: dict[str, Any]) -> None:
    lines += ["", f"### {title}", "",
              "| group | n | L_E | L_E,m | L_H | masking | remainder | total |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, b in blocks.items():
        lines.append(
            f"| {name} | {b['n']} | {_m(b['mae_era5'])} | {_m(b['mae_masked'])} | "
            f"{_m(b['mae_hrrr'])} | {_f(b['masking'])} | {_f(b['remainder'])} | {_f(b['total'])} |"
        )


def _write_report(r: dict[str, Any]) -> None:
    p = r["pooled_all_instances"]
    lu = r["loro_unique"]
    lines = [
        "# HRRR Correction Study — Masked-ERA5 Decomposition (diagnostic stage)",
        "",
        "Pre-registration: `DESIGN.md` (same directory), implementing Codex",
        "round 9 §3. Cohort: the item-7 reduced cohort — 5,340 complete paired",
        "events selected by fetch-order availability (not a random sample);",
        "the decomposition is exact on this cohort and inherits its caveats.",
        "Nothing was fitted: the fold models are item 7's ERA5-trained refits,",
        "verified against the stored queue-1 cache.",
        "",
        "## Arms",
        "",
        "- **L_E** — ENS-EB on ERA5 inputs (stored queue-1 cache predictions).",
        "- **L_E,m** — ENS-EB on ERA5 inputs whose accumu series carries",
        "  cp_mm = lsp_mm = None at every hour (the production HRRR adapter's",
        "  representation of the structurally absent convective split), passed",
        "  through the standard v3 builders; same fold models, same events.",
        "- **L_H** — ENS-EB on production-equivalent HRRR inputs (item-7",
        "  reduced run).",
        "",
        "Identity on every block: total (L_H − L_E) = masking (L_E,m − L_E)",
        "+ remainder (L_H − L_E,m). This is an accounting decomposition, not",
        "a causal attribution: either term may be negative or exceed the",
        "total, and the remainder bundles source differences, adapter",
        "differences and interactions with missingness.",
        "",
        "## Construction checks",
        "",
        f"- Unmasked rebuild parity vs stored features_v3.parquet: "
        f"{r['build_parity']['derived_values_checked']} derived values, "
        f"{r['build_parity']['mismatches']} mismatches.",
        f"- Masked-vs-unmasked feature differences confined to: "
        f"{json.dumps(r['masked_diff_features'])} (masked values all None).",
        f"- Masked-run integrity (refit vs stored cache, max abs diff): "
        f"{r['integrity_max_abs_diff_overall']:.3e} (gate ≤ 1e-6).",
        "",
        "## Headline",
        "",
        f"Pooled over all 28 instances ({p['n']} instance-events): masking "
        f"**{_f(p['masking'])}**, remainder **{_f(p['remainder'])}**, total "
        f"**{_f(p['total'])}** (L_E {_m(p['mae_era5'])}, L_E,m "
        f"{_m(p['mae_masked'])}, L_H {_m(p['mae_hrrr'])}).",
        "",
        f"LORO-unique (each event once): masking **{_f(lu['masking'])}**, "
        f"remainder **{_f(lu['remainder'])}**, total **{_f(lu['total'])}** "
        f"(n {lu['n']}).",
    ]
    _table(lines, "By family", r["families"])
    _table(lines, "By region (own LORO instance)", r["regions_loro"])
    _table(lines, "By water year (LORO-unique)", r["water_years_loro_unique"])
    _table(lines, "By lead band (LORO-unique; Alaska's band is its region — confounded)",
           r["lead_bands_loro_unique"])
    lines += [
        "",
        "## Reading",
        "",
        "Per Codex round 9: a masking term that accounts for most of the",
        "total moves a training-time availability-mask candidate ahead of",
        "profile harmonisation or HRRR retraining; a dominant remainder",
        "with predictable structure strengthens the residual-adapter case.",
        "No candidate is selected by this document — the candidate budget",
        "is frozen in DESIGN.md after this diagnostic, and any fitting",
        "requires its own authorisation and Codex review.",
        "",
    ]
    (CORRECTION / "DECOMPOSITION.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
