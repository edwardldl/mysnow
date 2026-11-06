"""Correction study, diagnostic stage supplement.

(1) Full-precision masking audit: how much do masked-arm predictions
actually move vs ERA5-arm predictions, per paired row?
(2) Signed-residual structure (DESIGN.md diagnostic 2): conditional
mean signed error of the HRRR arm (and ERA5 arm for contrast) by
region, water year, predicted-SLR band and forecast-liquid band, on
the LORO-unique set - the descriptive evidence for whether the
remainder carries predictable, correctable structure. Pure analysis of
existing paired predictions; nothing is fitted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "data" / "slr-training-data" / "screened-v2" / "model-v1"
TRANSFER = BASE / "model-v3-hrrr-transfer"
CORRECTION = BASE / "model-v3-hrrr-correction"
FEATURES = BASE / "model-v3-final" / "features_v3.parquet"


def main() -> int:
    red = pd.concat(
        [pd.read_parquet(p) for p in sorted((TRANSFER / "reduced" / "paired").glob("*.parquet"))],
        ignore_index=True,
    )
    msk = pd.concat(
        [pd.read_parquet(p) for p in sorted((CORRECTION / "masked" / "paired").glob("*.parquet"))],
        ignore_index=True,
    )
    msk = msk[["split_id", "event_id", "hrrr_pred"]].rename(columns={"hrrr_pred": "masked_pred"})
    df = red.merge(msk, on=["split_id", "event_id"], how="inner", validate="one_to_one")

    era = pd.read_parquet(FEATURES, columns=["event_id", "water_year", "fcst_liquid_mm"])
    meta = {str(r.event_id): (int(r.water_year), float(r.fcst_liquid_mm)) for r in era.itertuples(index=False)}
    df["water_year"] = df["event_id"].astype(str).map(lambda e: meta[e][0])
    df["fcst_liquid_mm"] = df["event_id"].astype(str).map(lambda e: meta[e][1])

    diff = (df["masked_pred"] - df["era5_pred"]).abs()
    audit = {
        "paired_rows": int(len(df)),
        "rows_masked_pred_differs": int((diff > 0).sum()),
        "share_rows_differing": float((diff > 0).mean()),
        "mean_abs_pred_diff": float(diff.mean()),
        "max_abs_pred_diff": float(diff.max()),
        "pooled_masking_full_precision": float(
            np.mean(np.abs(df["masked_pred"] - df["observed"]))
            - np.mean(np.abs(df["era5_pred"] - df["observed"]))
        ),
    }

    loro = df[df["family"] == "loro"].copy()
    loro["resid_hrrr"] = loro["hrrr_pred"] - loro["observed"]
    loro["resid_era5"] = loro["era5_pred"] - loro["observed"]
    loro["pred_band"] = pd.cut(
        loro["hrrr_pred"], bins=[-np.inf, 5, 8, 11, 14, np.inf],
        labels=["<5", "5-8", "8-11", "11-14", ">14"],
    )
    loro["liquid_band"] = pd.cut(
        loro["fcst_liquid_mm"], bins=[-np.inf, 3, 6, 10, np.inf],
        labels=["<3mm", "3-6mm", "6-10mm", ">10mm"],
    )

    def table(key: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, g in loro.groupby(key, observed=True):
            out[str(name)] = {
                "n": int(len(g)),
                "mean_signed_err_hrrr": float(g["resid_hrrr"].mean()),
                "mean_signed_err_era5": float(g["resid_era5"].mean()),
                "mae_hrrr": float(np.mean(np.abs(g["resid_hrrr"]))),
                "mae_era5": float(np.mean(np.abs(g["resid_era5"]))),
            }
        return out

    result = {
        "masking_prediction_audit": audit,
        "loro_unique_overall": {
            "n": int(len(loro)),
            "mean_signed_err_hrrr": float(loro["resid_hrrr"].mean()),
            "mean_signed_err_era5": float(loro["resid_era5"].mean()),
        },
        "by_region": table("region"),
        "by_water_year": table("water_year"),
        "by_predicted_slr_band": table("pred_band"),
        "by_forecast_liquid_band": table("liquid_band"),
    }
    (CORRECTION / "residual_structure.json").write_text(json.dumps(result, indent=2))

    lines = [
        "# HRRR Correction Study — Signed-Residual Structure (diagnostic 2)",
        "",
        "Descriptive analysis of item 7's reduced-cohort paired predictions",
        "(LORO-unique set, n = %d). Nothing fitted. Signed error = prediction" % len(loro),
        "− observed SLR; a stable non-zero conditional mean is the signature",
        "of correctable structure (round 9: penalty ≠ bias — these are the",
        "bias reads).",
        "",
        f"Overall mean signed error: HRRR arm {result['loro_unique_overall']['mean_signed_err_hrrr']:+.4f}, "
        f"ERA5 arm {result['loro_unique_overall']['mean_signed_err_era5']:+.4f}.",
        "",
        "Masking prediction audit (all 28 instances pooled): "
        f"{audit['rows_masked_pred_differs']} of {audit['paired_rows']} paired rows have a "
        f"masked prediction differing from the ERA5 prediction at all "
        f"(mean |Δ| {audit['mean_abs_pred_diff']:.5f}, max {audit['max_abs_pred_diff']:.4f}); "
        f"pooled masking term at full precision: {audit['pooled_masking_full_precision']:+.6f}.",
    ]
    for title, key in (
        ("By region", "by_region"),
        ("By water year", "by_water_year"),
        ("By predicted-SLR band (HRRR arm)", "by_predicted_slr_band"),
        ("By forecast-liquid band", "by_forecast_liquid_band"),
    ):
        lines += ["", f"## {title}", "",
                  "| group | n | mean signed err (HRRR) | mean signed err (ERA5) | MAE HRRR | MAE ERA5 |",
                  "|---|---:|---:|---:|---:|---:|"]
        for name, b in result[key].items():
            lines.append(
                f"| {name} | {b['n']} | {b['mean_signed_err_hrrr']:+.4f} | "
                f"{b['mean_signed_err_era5']:+.4f} | {b['mae_hrrr']:.4f} | {b['mae_era5']:.4f} |"
            )
    lines.append("")
    (CORRECTION / "RESIDUALS.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(audit, indent=1))
    print("overall signed:", result["loro_unique_overall"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
