"""Snow-window input-coverage audit (Codex rounds 8/9/10).

Read-only analysis of EXISTING serving records: the 2026-10-31 12Z
serving batch (extended run, 10 resorts) + the Mount Rose build,
501 scored point-windows across the ECMWF-GRIB, NOAA-HRRR and
Open-Meteo arms. No new model, no external labels.

Questions:
  1. Restricted to snow windows (source predicts snow: forecast liquid
     > 0 AND the source's own Kuchera snow gate passed), what fraction
     of served inputs fall outside the gold training distribution
     ([p1, p99] breadth and [min, max] exceedance), by source, lead
     and resort?
  2. Are the headline extremes (24 h liquid 81.9 mm served vs an
     18.3 mm gold maximum; subcloud dewpoint depression 34.7 vs
     16.0 C) genuinely comparable quantities, or accumulation /
     representation artefacts? Verified per arm against raw pulls
     and internal identities.
  3. Does the paired lowland gold cohort (item 7 / correction study)
     adequately represent these snow windows at the served points?

Gold reference: model-v3/features_v3.parquet, profile_status == 'ok'
(57,376 events), per-feature [min, p1, p99, max] -- the frozen-fold
report's reference, replicated exactly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "data/slr-training-data/screened-v2/model-v1"
OUT = BASE / "model-v3-snow-window-audit"
sys.path.insert(0, str(REPO / "training/slr_calibration/src"))
from slr_calibration.model_serving import load_catalogue  # noqa: E402

RUNS = [
    ("ext", BASE / "model-serving-extended-run"),
    ("rose", BASE / "model-serving-mount-rose"),
]
GOLD_MAX_LIQUID_TOL = 0.2  # mm; raw-pull rounding tolerance


def load_served() -> pd.DataFrame:
    frames = []
    for tag, d in RUNS:
        for fn in ("features.csv", "features_openmeteo.csv"):
            df = pd.read_csv(d / fn)
            df["run"] = tag
            frames.append(df)
        for fn in ("predictions.csv", "predictions_openmeteo.csv"):
            p = pd.read_csv(d / fn)
            p["run"] = tag
            frames.append(p.assign(_pred=True))
    feats = pd.concat([f for f in frames if "_pred" not in f.columns],
                      ignore_index=True)
    preds = pd.concat([f for f in frames if "_pred" in f.columns],
                      ignore_index=True)
    keep = ["run", "source", "point_id", "lead_day", "window_start_utc",
            "window_end_utc", "window_end_local_date", "model_snowfall_cm",
            "kuchera_snowfall_cm"]
    feats = feats.merge(preds[keep], on=["run", "source", "point_id",
                                         "lead_day"], how="left",
                        validate="many_to_one")
    cat = {p["point_id"]: p for p in load_catalogue(
        REPO / "data/tracking/registry.json")}
    feats["resort_slug"] = feats["point_id"].map(
        {k: v["resort_slug"] for k, v in cat.items()})
    feats["resort_name"] = feats["point_id"].map(
        {k: v["resort_name"] for k, v in cat.items()})
    feats["region"] = feats["point_id"].map(
        {k: v["region"] for k, v in cat.items()})
    feats["role"] = feats["point_id"].map(
        {k: v["role"] for k, v in cat.items()})
    return feats


def gold_reference() -> tuple[pd.DataFrame, pd.DataFrame]:
    sidecar = json.loads((BASE / "model-v3/champion/champion_sidecar.json")
                         .read_text())
    cols = sidecar["features"]
    assert len(cols) == 64
    gold = pd.read_parquet(BASE / "model-v3/features_v3.parquet")
    ok = gold[gold["profile_status"] == "ok"]
    ref = pd.DataFrame({
        "min": ok[cols].min(), "p1": ok[cols].quantile(0.01),
        "p99": ok[cols].quantile(0.99), "max": ok[cols].max(),
        "median": ok[cols].median()})
    return ok, ref


def exceedance(frame: pd.DataFrame, ref: pd.DataFrame,
               cols: list[str]) -> pd.DataFrame:
    """Per-row counts of features above gold max / outside [p1, p99]."""
    above = pd.DataFrame({c: frame[c] > ref.loc[c, "max"] for c in cols})
    below = pd.DataFrame({c: frame[c] < ref.loc[c, "min"] for c in cols})
    out_hi = pd.DataFrame({c: frame[c] > ref.loc[c, "p99"] for c in cols})
    out_lo = pd.DataFrame({c: frame[c] < ref.loc[c, "p1"] for c in cols})
    res = pd.DataFrame(index=frame.index)
    res["n_above_max"] = above.sum(axis=1)
    res["n_below_min"] = below.sum(axis=1)
    res["n_outside_p1p99"] = (out_hi | out_lo).sum(axis=1)
    res["any_above_max"] = res["n_above_max"] > 0
    res["liquid_above_gold_max"] = frame["fcst_liquid_mm"] > ref.loc[
        "fcst_liquid_mm", "max"]
    res["subcloud_above_gold_max"] = frame["subcloud_dewp_depression"] > \
        ref.loc["subcloud_dewp_depression", "max"]
    res["elev_above_gold_max"] = frame["elevation_m"] > ref.loc[
        "elevation_m", "max"]
    res["elev_above_gold_p99"] = frame["elevation_m"] > ref.loc[
        "elevation_m", "p99"]
    return res


def block_stats(frame: pd.DataFrame, exc: pd.DataFrame) -> dict:
    return {
        "n": int(len(frame)),
        "frac_any_above_max": float(exc["any_above_max"].mean()),
        "mean_n_above_max": float(exc["n_above_max"].mean()),
        "mean_n_outside_p1p99": float(exc["n_outside_p1p99"].mean()),
        "liquid_max": float(frame["fcst_liquid_mm"].max()),
        "liquid_p99": float(frame["fcst_liquid_mm"].quantile(0.99)),
        "frac_liquid_above_gold_max": float(
            exc["liquid_above_gold_max"].mean()),
        "subcloud_max": float(frame["subcloud_dewp_depression"].max()),
        "frac_subcloud_above_gold_max": float(
            exc["subcloud_above_gold_max"].mean()),
    }


def verify_openmeteo(frame: pd.DataFrame) -> dict:
    """Recompute every OM window's liquid from the raw pull: sum of the
    hourly `precipitation` values with window_start < t <= window_end
    (hour-ending stamps)."""
    out = {}
    for tag, d in RUNS:
        raw = json.loads((d / "om_raw_tail.json").read_text())
        if isinstance(raw, dict):
            raw = [raw]
        meta = json.loads((d / "om_pull_tail_meta.json").read_text())
        order = meta["point_ids"]
        sub = frame[(frame["run"] == tag)
                    & (frame["source"] == "ecmwf_openmeteo")]
        diffs = []
        for _, row in sub.iterrows():
            loc = raw[order.index(row["point_id"])]
            times = loc["hourly"]["time"]
            vals = loc["hourly"]["precipitation"]
            start = str(row["window_start_utc"]).replace("Z", "")
            end = str(row["window_end_utc"]).replace("Z", "")
            total = sum(v for t, v in zip(times, vals)
                        if start < t <= end and v is not None)
            diffs.append(abs(total - row["fcst_liquid_mm"]))
        out[tag] = {"n": len(diffs),
                    "max_abs_diff_mm": max(diffs) if diffs else None,
                    "n_over_tol": int(sum(
                        1 for x in diffs if x > GOLD_MAX_LIQUID_TOL))}
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    served = load_served()
    gold_ok, ref = gold_reference()
    cols = list(ref.index)
    assert len(served) == 501, len(served)

    served["snow_window"] = (served["fcst_liquid_mm"] > 0) & \
        served["baseline_slr"].notna()
    served["wet_window"] = served["fcst_liquid_mm"] > 0
    snow = served[served["snow_window"]].copy()
    exc_all = exceedance(served, ref, cols)
    exc = exc_all.loc[snow.index]
    snow = snow.join(exc)

    # ---- table 1: per-feature exceedance within snow windows
    rows = []
    for c in cols:
        v = snow[c]
        rows.append({
            "feature": c,
            "gold_min": ref.loc[c, "min"], "gold_p1": ref.loc[c, "p1"],
            "gold_p99": ref.loc[c, "p99"], "gold_max": ref.loc[c, "max"],
            "snow_n": int(v.notna().sum()),
            "frac_above_gold_max": float((v > ref.loc[c, "max"]).mean()),
            "frac_below_gold_min": float((v < ref.loc[c, "min"]).mean()),
            "frac_outside_p1p99": float(
                ((v > ref.loc[c, "p99"]) | (v < ref.loc[c, "p1"])).mean()),
            "snow_max": float(v.max()), "snow_min": float(v.min()),
            "snow_median": float(v.median()),
        })
    by_feature = pd.DataFrame(rows).sort_values(
        "frac_above_gold_max", ascending=False)
    by_feature.to_csv(OUT / "exceedance_by_feature_snow.csv", index=False)

    # ---- table 2: by source x lead band
    def band(d):
        return "1-2" if d <= 2 else "3-5" if d <= 5 else \
            "6-9" if d <= 9 else "10-14"
    snow["lead_band"] = snow["lead_day"].map(band)
    rows = []
    for (src, lb), g in snow.groupby(["source", "lead_band"]):
        e = exc.loc[g.index]
        rows.append({"source": src, "lead_band": lb, **block_stats(g, e)})
    pd.DataFrame(rows).to_csv(OUT / "exceedance_by_source_lead.csv",
                              index=False)

    # ---- table 3: by resort x source
    rows = []
    for (res, src), g in snow.groupby(["resort_slug", "source"]):
        e = exc.loc[g.index]
        rows.append({"resort": res, "region": g["region"].iloc[0],
                     "source": src, **block_stats(g, e),
                     "max_point_elev_m": float(g["elevation_m"].max())})
    pd.DataFrame(rows).to_csv(OUT / "exceedance_by_resort.csv",
                              index=False)

    # ---- extremes: top liquid snow windows
    top = snow.nlargest(15, "fcst_liquid_mm")[[
        "run", "point_id", "resort_slug", "role", "source", "lead_day",
        "window_start_utc", "window_end_utc", "fcst_liquid_mm",
        "precip_rate_mean", "precip_rate_max", "baseline_slr",
        "subcloud_dewp_depression", "elevation_m", "valid_hours"]]
    top.to_csv(OUT / "extremes_liquid_snow.csv", index=False)

    # ---- verifications
    ident = (served["precip_rate_mean"] * 24
             - served["fcst_liquid_mm"]).abs()
    om_check = verify_openmeteo(served)
    hrrr = served[served["source"] == "hrrr"]
    verif = {
        "rate_identity_max_abs_diff_mm": float(ident.max()),
        "openmeteo_raw_recompute": om_check,
        "hrrr_snow_max_hourly_rate_mm": float(
            snow.loc[snow["source"] == "hrrr", "precip_rate_max"].max()),
        "hrrr_all_max_hourly_rate_mm": float(hrrr["precip_rate_max"].max()),
        "valid_hours_lt_24_rows": served.loc[
            served["valid_hours"] < 24,
            ["point_id", "source", "lead_day", "valid_hours",
             "fcst_liquid_mm", "snow_window"]].to_dict("records"),
    }

    # ---- paired cohort (item 7) support vs served points
    pair = pd.read_parquet(BASE / "model-v3-hrrr-transfer"
                           / "features_hrrr_v3.parquet")
    idcol = "event_id" if "event_id" in pair.columns else pair.columns[0]
    comp = pair[pair.get("hrrr_complete", True) == True] \
        if "hrrr_complete" in pair.columns else pair
    gold_idx = gold_ok.set_index("event_id")
    pe = comp[idcol].astype(str).map(
        gold_idx["elevation_m"].astype(float))
    pr = comp[idcol].astype(str).map(gold_idx["region"].astype(str))
    paired_support = {
        "paired_events_joined": int(pe.notna().sum()),
        "elev_p99_m": float(pe.quantile(0.99)),
        "elev_max_m": float(pe.max()),
        "n_ge_1500m": int((pe >= 1500).sum()),
        "region_counts": pr.value_counts().to_dict(),
        "region_ge1500": pd.crosstab(
            pr[pe >= 1500].fillna("?"), columns="n").to_dict(),
    }

    above = {c: snow[c] > ref.loc[c, "max"] for c in cols}
    n_above_noelev = sum(above[c] for c in cols if c != "elevation_m")
    wet = served[served["wet_window"]]
    extra = {
        "frac_liquid_above_gold_p99": float(
            (snow["fcst_liquid_mm"]
             > ref.loc["fcst_liquid_mm", "p99"]).mean()),
        "frac_any_above_max_nonelev": float((n_above_noelev > 0).mean()),
        "frac_above_max_elev_only": float(
            ((snow["n_above_max"] > 0) & (n_above_noelev == 0)).mean()),
        "frac_subcloud_above_gold_p99": float(
            (snow["subcloud_dewp_depression"]
             > ref.loc["subcloud_dewp_depression", "p99"]).mean()),
        "wet_window_subcloud_max_c": float(
            wet["subcloud_dewp_depression"].max()),
        "snow_elev_median_m": float(snow["elevation_m"].median()),
        "frac_snow_elev_above_gold_p99": float(
            snow["elev_above_gold_p99"].mean()),
        "frac_snow_elev_above_gold_max": float(
            snow["elev_above_gold_max"].mean()),
        "per_source": {
            src: {"n": int(len(g)),
                  "median_liquid_mm": float(g["fcst_liquid_mm"].median()),
                  "frac_liquid_above_gold_max": float(
                      g["liquid_above_gold_max"].mean()),
                  "frac_any_above_max": float(g["any_above_max"].mean())}
            for src, g in snow.groupby("source")},
        "liquid_above_max_by_region": snow.groupby("region")[
            "liquid_above_gold_max"].agg(["mean", "count"]).to_dict(),
    }

    summary = {
        "snow_extra": extra,
        "served_rows": int(len(served)),
        "snow_windows_primary": int(len(snow)),
        "snow_by_source": snow["source"].value_counts().to_dict(),
        "wet_windows": int(served["wet_window"].sum()),
        "gated_windows": int(served["baseline_slr"].notna().sum()),
        "gold": {"n_ok": int(len(gold_ok)),
                 "liquid_max_mm": float(ref.loc["fcst_liquid_mm", "max"]),
                 "liquid_p99_mm": float(ref.loc["fcst_liquid_mm", "p99"]),
                 "subcloud_max_c": float(
                     ref.loc["subcloud_dewp_depression", "max"]),
                 "elev_p99_m": float(ref.loc["elevation_m", "p99"]),
                 "elev_max_m": float(ref.loc["elevation_m", "max"])},
        "snow_overall": block_stats(snow, exc),
        "verifications": verif,
        "paired_support": paired_support,
        "snow_by_region": snow["region"].value_counts().to_dict(),
        "points_above_gold_elev_max": sorted(
            served.loc[served["elevation_m"] > ref.loc["elevation_m",
                       "max"], "point_id"].unique().tolist()),
        "points_above_gold_elev_p99": sorted(
            served.loc[served["elevation_m"] > ref.loc["elevation_m",
                       "p99"], "point_id"].unique().tolist()),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
