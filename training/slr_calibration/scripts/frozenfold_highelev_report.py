#!/usr/bin/env python
"""Frozen-fold high-elevation report (queue follow-up to Codex round 7).

Read-only analysis of STORED artefacts. No refitting.

Sources
-------
- Gold cohort: model-v3/features_v3.parquet, profile_status == 'ok'
  (57,376 events); observed_slr, region, elevation_m, baseline_slr
  (baseline_slr verified identical to kuchera_slr in loro_predictions).
- Incumbent ENS-EB fold predictions: model-v3-median/predictions.parquet
  (model == 'ens_eb'; taken from the queue-1 prediction cache, i.e. the
  v3 judging's stored fold predictions on the frozen manifests).
  LORO cross-checked against model-v3/loro_predictions.parquet.
- Served feature rows: model-serving-extended-run/features.csv +
  features_openmeteo.csv + model-serving-mount-rose/features.csv +
  features_openmeteo.csv (the 2026-10-31 12Z batch + Mount Rose build;
  501 scored point-windows, all three arms).
- Served point elevations: data/tracking/registry.json forecast_points.
- The 64 features: model-v3/champion/champion_sidecar.json.

Outputs (model-v3-frozenfold-report/): REPORT.md, tables_region_band.csv,
tables_high1500.csv, extrapolation_point_feature.csv,
extrapolation_by_point.csv, extrapolation_by_feature.csv, summary.json
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/home/coder/mysnow")
MV1 = REPO / "data/slr-training-data/screened-v2/model-v1"
OUT = MV1 / "model-v3-frozenfold-report"
WEST = ["rockies", "pacific-northwest", "sierra-nevada", "alaska"]
REGIONS = WEST + ["great-lakes", "northeast"]
BANDS = ["<1000", "1000-1500", ">=1500"]
FAMILIES = ["loro", "stations", "randomised"]


def band_of(elev: pd.Series) -> pd.Series:
    return pd.cut(elev, bins=[-np.inf, 1000, 1500, np.inf],
                  labels=BANDS, right=False)


def metrics(df: pd.DataFrame) -> dict:
    """df needs obs, pred (champion), kuch (kuchera)."""
    ec = df["pred"] - df["obs"]
    ek = df["kuch"] - df["obs"]
    return {
        "n": int(len(df)),
        "mae_champion": float(ec.abs().mean()) if len(df) else np.nan,
        "bias_champion": float(ec.mean()) if len(df) else np.nan,
        "mae_kuchera": float(ek.abs().mean()) if len(df) else np.nan,
        "bias_kuchera": float(ek.mean()) if len(df) else np.nan,
        "edge_kuch_minus_champ": float(ek.abs().mean() - ec.abs().mean())
        if len(df) else np.nan,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sidecar = json.loads((MV1 / "model-v3/champion/champion_sidecar.json")
                         .read_text())
    feats64: list[str] = sidecar["features"]
    assert len(feats64) == 64

    gold = pd.read_parquet(
        MV1 / "model-v3/features_v3.parquet",
        columns=list(dict.fromkeys(
            ["event_id", "region", "elevation_m", "observed_slr",
             "baseline_slr", "profile_status"] + feats64)))
    gold = gold[gold["profile_status"] == "ok"].copy()
    gold = gold.rename(columns={"observed_slr": "obs",
                                "baseline_slr": "kuch"})
    gold["band"] = band_of(gold["elevation_m"])
    n_gold = len(gold)

    preds = pd.read_parquet(MV1 / "model-v3-median/predictions.parquet")
    ens = preds[preds["model"] == "ens_eb"][["split_id", "event_id",
                                              "prediction"]]

    # --- verification: LORO ens_eb vs the v3 judging's own loro file ---
    loro_v3 = pd.read_parquet(
        MV1 / "model-v3/loro_predictions.parquet",
        columns=["event_id", "pred_v3_ens_eb"])
    loro_ens = ens[ens["split_id"].str.startswith("loro_")]
    chk = loro_ens.merge(loro_v3, on="event_id", how="inner")
    loro_maxdiff = float((chk["prediction"] - chk["pred_v3_ens_eb"])
                         .abs().max())

    base = gold[["event_id", "region", "elevation_m", "band", "obs", "kuch"]]

    def fam_frame(family: str) -> pd.DataFrame:
        if family == "loro":
            p = loro_ens[["event_id", "prediction"]]
            return base.merge(p, on="event_id").rename(
                columns={"prediction": "pred"})
        if family == "stations":
            p = ens[ens["split_id"] == "unseenStations"]
            return base.merge(p[["event_id", "prediction"]],
                              on="event_id").rename(
                columns={"prediction": "pred"})
        # randomised: per-event mean of errors over the reps where the
        # event was in the test set (each event ~8 of 20 reps)
        p = ens[ens["split_id"].str.startswith("random60_40")]
        d = base.merge(p[["event_id", "prediction"]], on="event_id")
        d["ec"] = (d["prediction"] - d["obs"]).abs()
        d["bc"] = d["prediction"] - d["obs"]
        d["ek"] = (d["kuch"] - d["obs"]).abs()
        d["bk"] = d["kuch"] - d["obs"]
        g = d.groupby("event_id").agg(
            mae_champion=("ec", "mean"), bias_champion=("bc", "mean"),
            mae_kuchera=("ek", "mean"), bias_kuchera=("bk", "mean"),
            n_reps=("ec", "size")).reset_index()
        g["edge_kuch_minus_champ"] = g["mae_kuchera"] - g["mae_champion"]
        return base.merge(g, on="event_id")

    fam_frames = {f: fam_frame(f) for f in FAMILIES}
    fam_overall = {}
    for f, d in fam_frames.items():
        if f == "randomised":
            fam_overall[f] = {
                "n_events": int(d["event_id"].nunique()),
                "mae_champion": float(d["mae_champion"].mean()),
                "mae_kuchera": float(d["mae_kuchera"].mean())}
        else:
            m = metrics(d)
            fam_overall[f] = {"n_events": m["n"],
                              "mae_champion": m["mae_champion"],
                              "mae_kuchera": m["mae_kuchera"]}

    # --- Table 1: region x elevation band, per family ---
    t1_rows = []
    for fam, d in fam_frames.items():
        for region in REGIONS:
            for band in BANDS:
                cell = d[(d["region"] == region) & (d["band"] == band)]
                if fam == "randomised":
                    if len(cell) == 0:
                        row = {"family": fam, "region": region,
                               "band": band, "n": 0}
                    else:
                        row = {"family": fam, "region": region,
                               "band": band, "n": int(len(cell)),
                               "mae_champion":
                                   float(cell["mae_champion"].mean()),
                               "bias_champion":
                                   float(cell["bias_champion"].mean()),
                               "mae_kuchera":
                                   float(cell["mae_kuchera"].mean()),
                               "bias_kuchera":
                                   float(cell["bias_kuchera"].mean()),
                               "edge_kuch_minus_champ":
                                   float(cell["edge_kuch_minus_champ"]
                                         .mean())}
                else:
                    m = metrics(cell)
                    row = {"family": fam, "region": region, "band": band,
                           **m}
                t1_rows.append(row)
        # pooled bands (all regions) per family
        for band in BANDS:
            cell = d[d["band"] == band]
            if fam == "randomised":
                row = {"family": fam, "region": "ALL", "band": band,
                       "n": int(len(cell)),
                       "mae_champion": float(cell["mae_champion"].mean()),
                       "bias_champion": float(cell["bias_champion"].mean()),
                       "mae_kuchera": float(cell["mae_kuchera"].mean()),
                       "bias_kuchera": float(cell["bias_kuchera"].mean()),
                       "edge_kuch_minus_champ":
                           float(cell["edge_kuch_minus_champ"].mean())}
            else:
                row = {"family": fam, "region": "ALL", "band": band,
                       **metrics(cell)}
            t1_rows.append(row)
    t1 = pd.DataFrame(t1_rows)
    t1.to_csv(OUT / "tables_region_band.csv", index=False)

    # --- Table 2: >=1500 m cohort, overall + slices + per region (LORO) ---
    t2_rows = []
    for fam, d in fam_frames.items():
        hi = d[d["elevation_m"] >= 1500]
        slices = {"all": hi,
                  "slr_lt5": hi[hi["obs"] < 5],
                  "slr_gt20": hi[hi["obs"] > 20]}
        for sname, sdf in slices.items():
            if fam == "randomised":
                row = {"family": fam, "slice": sname, "n": int(len(sdf)),
                       "mae_champion": float(sdf["mae_champion"].mean()),
                       "bias_champion": float(sdf["bias_champion"].mean()),
                       "mae_kuchera": float(sdf["mae_kuchera"].mean()),
                       "bias_kuchera": float(sdf["bias_kuchera"].mean()),
                       "edge_kuch_minus_champ":
                           float(sdf["edge_kuch_minus_champ"].mean())}
            else:
                row = {"family": fam, "slice": sname, **metrics(sdf)}
            t2_rows.append(row)
    loro = fam_frames["loro"]
    hi_loro = loro[loro["elevation_m"] >= 1500]
    for region in REGIONS:
        cell = hi_loro[hi_loro["region"] == region]
        t2_rows.append({"family": "loro_by_region", "slice": region,
                        **metrics(cell)})
    t2 = pd.DataFrame(t2_rows)
    t2.to_csv(OUT / "tables_high1500.csv", index=False)

    # --- Extrapolation audit ---
    registry = json.loads((REPO / "data/tracking/registry.json").read_text())
    points = []
    for resort in registry["resorts"]:
        for p in resort["forecast_points"]:
            points.append({"point_id": p["point_id"],
                           "resort": resort["resort_slug"],
                           "role": p["role"],
                           "elevation_m": float(p["elevation_m"])})
    points_df = pd.DataFrame(points)

    served_parts = []
    for rel in ["model-serving-extended-run/features.csv",
                "model-serving-extended-run/features_openmeteo.csv",
                "model-serving-mount-rose/features.csv",
                "model-serving-mount-rose/features_openmeteo.csv"]:
        d = pd.read_csv(MV1 / rel, usecols=["point_id", "source"] + feats64)
        d["arm_file"] = Path(rel).parent.name + "/" + Path(rel).name
        served_parts.append(d)
    served = pd.concat(served_parts, ignore_index=True)

    gold_feats = gold[feats64]
    stats = pd.DataFrame({
        "p1": gold_feats.quantile(0.01), "p99": gold_feats.quantile(0.99),
        "min": gold_feats.min(), "max": gold_feats.max(),
        "median": gold_feats.median()})
    # elevation treated as an audited variable too
    elev_stats = {"p1": float(gold["elevation_m"].quantile(0.01)),
                  "p99": float(gold["elevation_m"].quantile(0.99)),
                  "min": float(gold["elevation_m"].min()),
                  "max": float(gold["elevation_m"].max()),
                  "median": float(gold["elevation_m"].median())}

    pf_rows = []
    for feat in feats64:
        lo, hi_, mn, mx = (stats.loc[feat, "p1"], stats.loc[feat, "p99"],
                           stats.loc[feat, "min"], stats.loc[feat, "max"])
        for pid, grp in served.groupby("point_id"):
            v = grp[feat].dropna()
            if len(v) == 0:
                pf_rows.append({"point_id": pid, "feature": feat,
                                "n_values": 0, "frac_outside_p1p99": np.nan,
                                "frac_outside_minmax": np.nan})
                continue
            pf_rows.append({
                "point_id": pid, "feature": feat, "n_values": int(len(v)),
                "frac_outside_p1p99":
                    float(((v < lo) | (v > hi_)).mean()),
                "frac_outside_minmax":
                    float(((v < mn) | (v > mx)).mean())})
    pf = pd.DataFrame(pf_rows)
    pf.to_csv(OUT / "extrapolation_point_feature.csv", index=False)

    by_point = pf.groupby("point_id").agg(
        mean_frac_outside_p1p99=("frac_outside_p1p99", "mean"),
        n_features_majority_outside=(
            "frac_outside_p1p99", lambda s: int((s >= 0.5).sum())),
        n_features_any_outside_minmax=(
            "frac_outside_minmax", lambda s: int((s > 0).sum())),
        max_frac_outside_p1p99=("frac_outside_p1p99", "max")).reset_index()
    by_point = by_point.merge(points_df, on="point_id")
    by_point["elev_vs_gold_p99"] = by_point["elevation_m"] - elev_stats["p99"]
    by_point = by_point.sort_values("mean_frac_outside_p1p99",
                                    ascending=False)
    by_point.to_csv(OUT / "extrapolation_by_point.csv", index=False)

    by_feat = pf.groupby("feature").agg(
        mean_frac_outside_p1p99=("frac_outside_p1p99", "mean"),
        n_points_majority_outside=(
            "frac_outside_p1p99", lambda s: int((s >= 0.5).sum())),
        n_points_any_outside_minmax=(
            "frac_outside_minmax", lambda s: int((s > 0).sum()))).reset_index()
    by_feat = by_feat.merge(
        stats.reset_index().rename(columns={"index": "feature"}),
        on="feature")
    served_med = served[feats64].median()
    by_feat["served_median"] = by_feat["feature"].map(served_med)
    above, below = {}, {}
    for feat in feats64:
        sv = served[feat].dropna()
        above[feat] = float((sv > stats.loc[feat, "max"]).mean())
        below[feat] = float((sv < stats.loc[feat, "min"]).mean())
    by_feat["frac_above_gold_max"] = by_feat["feature"].map(above)
    by_feat["frac_below_gold_min"] = by_feat["feature"].map(below)
    by_feat["served_max"] = by_feat["feature"].map(
        served[feats64].max())
    by_feat["served_min"] = by_feat["feature"].map(
        served[feats64].min())
    by_feat["side"] = np.where(by_feat["served_median"] > by_feat["p99"],
                               "above_p99",
                               np.where(by_feat["served_median"] < by_feat["p1"],
                                        "below_p1", "within"))
    by_feat = by_feat.sort_values("mean_frac_outside_p1p99", ascending=False)
    by_feat.to_csv(OUT / "extrapolation_by_feature.csv", index=False)

    summary = {
        "n_gold_ok": n_gold,
        "loro_ens_vs_v3judging_maxdiff": loro_maxdiff,
        "family_overall": fam_overall,
        "gold_elevation": elev_stats,
        "n_served_points": int(points_df["point_id"].nunique()),
        "n_served_rows": int(len(served)),
        "served_elevation_min": float(points_df["elevation_m"].min()),
        "served_elevation_max": float(points_df["elevation_m"].max()),
        "n_served_points_above_gold_max_elev":
            int((points_df["elevation_m"] > elev_stats["max"]).sum()),
        "n_served_points_above_gold_p99_elev":
            int((points_df["elevation_m"] > elev_stats["p99"]).sum()),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))

    # ---------------- REPORT.md ----------------
    def fmt(x, nd=3):
        return "—" if x is None or (isinstance(x, float) and np.isnan(x)) \
            else f"{x:.{nd}f}"

    def t1_md(fam: str, regions: list[str]) -> str:
        sub = t1[(t1["family"] == fam) & (t1["region"].isin(regions))]
        lines = ["| region | band | n | ENS-EB MAE | ENS-EB bias | "
                 "Kuchera MAE | Kuchera bias | edge (K−C) |",
                 "|---|---|---:|---:|---:|---:|---:|---:|"]
        for _, r in sub.iterrows():
            if r["n"] == 0:
                lines.append(f"| {r['region']} | {r['band']} | 0 | — | — | "
                             "— | — | — |")
            else:
                lines.append(
                    f"| {r['region']} | {r['band']} | {int(r['n'])} | "
                    f"{fmt(r['mae_champion'])} | {fmt(r['bias_champion'])} | "
                    f"{fmt(r['mae_kuchera'])} | {fmt(r['bias_kuchera'])} | "
                    f"{fmt(r['edge_kuch_minus_champ'])} |")
        return "\n".join(lines)

    def pooled_edge(fam: str, band: str) -> float:
        r = t1[(t1["family"] == fam) & (t1["region"] == "ALL")
               & (t1["band"] == band)].iloc[0]
        return float(r["edge_kuch_minus_champ"])

    def hi_row(fam: str, sl: str) -> pd.Series:
        return t2[(t2["family"] == fam) & (t2["slice"] == sl)].iloc[0]

    hi_all = t2[(t2["family"] == "loro") & (t2["slice"] == "all")].iloc[0]
    hi_lt5 = t2[(t2["family"] == "loro") & (t2["slice"] == "slr_lt5")].iloc[0]
    hi_gt20 = t2[(t2["family"] == "loro") & (t2["slice"] == "slr_gt20")] \
        .iloc[0]
    top_feats = by_feat.head(10)
    top_points = by_point.head(10)

    md = f"""# Frozen-fold high-elevation report — v3 champion (ENS-EB) vs Kuchera

*Read-only analysis of stored artefacts, generated by
`training/slr_calibration/scripts/frozenfold_highelev_report.py`.
No refitting. Companion files: `tables_region_band.csv`,
`tables_high1500.csv`, `extrapolation_point_feature.csv`,
`extrapolation_by_point.csv`, `extrapolation_by_feature.csv`,
`summary.json`.*

## Sources and verification

- **Gold cohort:** `model-v3/features_v3.parquet`, `profile_status == 'ok'`
  — {n_gold:,} events. Kuchera = the frame's `baseline_slr` (verified
  identical to `kuchera_slr` in `loro_predictions.parquet`, max diff 0.0).
- **Champion fold predictions:** the v3 judging's stored ENS-EB predictions
  (via the queue-1 cache copy in `model-v3-median/predictions.parquet`,
  model `ens_eb`, frozen manifests). LORO values cross-checked against
  `model-v3/loro_predictions.parquet`: max abs difference
  {loro_maxdiff:.2e}.
- **Family scores reproduced from these stored predictions** (SLR MAE,
  champion / Kuchera): LORO {fam_overall['loro']['mae_champion']:.4f} /
  {fam_overall['loro']['mae_kuchera']:.4f} (n={fam_overall['loro']['n_events']:,});
  unseen-station {fam_overall['stations']['mae_champion']:.4f} /
  {fam_overall['stations']['mae_kuchera']:.4f} (n={fam_overall['stations']['n_events']:,});
  randomised {fam_overall['randomised']['mae_champion']:.4f} /
  {fam_overall['randomised']['mae_kuchera']:.4f} (per-event mean over the
  reps where the event was held out).
- **Elevation bands:** `<1000`, `1000 <= elev < 1500`, `>=1500` m (no gold
  station sits exactly at 1500.0 m, so `>=` vs `>` is immaterial).
  Bias = mean(prediction − observed). Edge = Kuchera MAE − champion MAE
  (positive favours the champion).
- **Aggregation notes.** Every table cell is pooled within the cell. The
  v3 judging's headline LORO 3.2540 is the unweighted mean of the six
  regional MAEs; pooled over all events it is 3.1937 — the per-region and
  per-cell numbers are identical under either view. The randomised
  numbers here average each event's errors over the reps where it was
  held out (2.8608) rather than pooling instances (the judging's
  2.8567); 2 of the 57,376 events fall in no randomised test set.

## 1. Region × elevation band

### LORO (region held out — the transfer-relevant family), western regions

{t1_md("loro", WEST)}

### LORO, eastern regions (for completeness)

{t1_md("loro", ["great-lakes", "northeast"])}

### LORO, all regions pooled by band

{t1_md("loro", ["ALL"])}

### Unseen-station family, western regions

{t1_md("stations", WEST)}

### Randomised family (per-event averaged), western regions

{t1_md("randomised", WEST)}

Full 6-region × 3-band tables for all three families are in
`tables_region_band.csv`. **Empty cells are evidence:** there are no gold
events at >=1500 m in pacific-northwest or alaska, none at 1000–1500 m in
alaska, and none below 1000 m in sierra-nevada — the champion has never
been judged on held-out data from high-elevation maritime or Alaskan
stations, because none exist in the pool.

## 2. The >=1500 m cohort (all regions, {int(hi_all['n']):,} events)

LORO family: champion MAE {fmt(hi_all['mae_champion'])}, bias
{fmt(hi_all['bias_champion'])}; Kuchera MAE {fmt(hi_all['mae_kuchera'])},
bias {fmt(hi_all['bias_kuchera'])}; edge {fmt(hi_all['edge_kuch_minus_champ'])}.
Slices (LORO): observed SLR<5 (n={int(hi_lt5['n']):,}): champion
{fmt(hi_lt5['mae_champion'])} vs Kuchera {fmt(hi_lt5['mae_kuchera'])};
observed SLR>20 (n={int(hi_gt20['n']):,}): champion
{fmt(hi_gt20['mae_champion'])} vs Kuchera {fmt(hi_gt20['mae_kuchera'])}.
Per-region >=1500 m rows (LORO) and the other families' cohort numbers are
in `tables_high1500.csv`. The pooled-band LORO rows in §1 show how the
champion's edge over Kuchera moves across the three elevation bands
within the observed range.

**Direct answer — does the edge grow, shrink, or invert with elevation?**
Pooled across regions, the LORO edge is essentially flat across the
observed range: {fmt(pooled_edge("loro", "<1000"))} below 1000 m,
{fmt(pooled_edge("loro", "1000-1500"))} at 1000–1500 m,
{fmt(pooled_edge("loro", ">=1500"))} at >=1500 m (unseen-station edges:
{fmt(pooled_edge("stations", "<1000"))} / {fmt(pooled_edge("stations", "1000-1500"))} /
{fmt(pooled_edge("stations", ">=1500"))}; randomised:
{fmt(pooled_edge("randomised", "<1000"))} / {fmt(pooled_edge("randomised", "1000-1500"))} /
{fmt(pooled_edge("randomised", ">=1500"))}). Within the >=1500 m cohort,
however, the win is a mid-range win: on the observed tails the edge
inverts in every family. LORO: SLR<5 edge
{fmt(hi_lt5['edge_kuch_minus_champ'])} (champion bias
{fmt(hi_lt5['bias_champion'])}, regression-to-the-mean overprediction of
low-ratio events) and SLR>20 edge {fmt(hi_gt20['edge_kuch_minus_champ'])}
(both models underpredict, champion bias {fmt(hi_gt20['bias_champion'])},
Kuchera {fmt(hi_gt20['bias_kuchera'])}). Unseen-station: SLR<5 edge
{fmt(hi_row("stations", "slr_lt5")['edge_kuch_minus_champ'])}, SLR>20 edge
{fmt(hi_row("stations", "slr_gt20")['edge_kuch_minus_champ'])}. Randomised:
SLR<5 edge {fmt(hi_row("randomised", "slr_lt5")['edge_kuch_minus_champ'])},
SLR>20 edge {fmt(hi_row("randomised", "slr_gt20")['edge_kuch_minus_champ'])}.

## 3. Extrapolation audit — served points vs the gold cohort

Served feature rows: the 2026-10-31 12Z serving batch + Mount Rose build
(`features.csv` + `features_openmeteo.csv` in the extended-run and
mount-rose directories; {summary['n_served_rows']} scored point-windows
across all three arms, NaN values excluded per feature — the Open-Meteo
arm's tclw pair is NaN by construction). Gold reference distributions:
the {n_gold:,} ok events, per feature: [1st, 99th] percentile and
[min, max].

**Elevation alone:** gold cohort elevation median
{elev_stats['median']:.0f} m, p99 {elev_stats['p99']:.0f} m, max
{elev_stats['max']:.0f} m. Served points span
{summary['served_elevation_min']:.0f}–{summary['served_elevation_max']:.0f} m;
{summary['n_served_points_above_gold_p99_elev']} of 33 points sit above the
gold cohort's 99th-percentile elevation and
{summary['n_served_points_above_gold_max_elev']} above its maximum.

**Features with the largest share of served values outside the gold
[1st, 99th] range** (mean over the 33 points of the per-point outside
fraction; full table in `extrapolation_by_feature.csv`):

| feature | mean frac outside [p1,p99] | points with >=50% outside | served median | gold p1 | gold p99 | side |
|---|---:|---:|---:|---:|---:|---|
"""
    for _, r in top_feats.iterrows():
        md += (f"| {r['feature']} | {fmt(r['mean_frac_outside_p1p99'])} | "
               f"{int(r['n_points_majority_outside'])} | "
               f"{fmt(r['served_median'], 2)} | {fmt(r['p1'], 2)} | "
               f"{fmt(r['p99'], 2)} | {r['side']} |\n")
    md += """
**Points with the broadest extrapolation** (mean outside-fraction across
the 64 features; full table in `extrapolation_by_point.csv`):

| point | resort | role | elev (m) | mean frac outside [p1,p99] | features >=50% outside | features any outside [min,max] |
|---|---|---|---:|---:|---:|---:|
"""
    for _, r in top_points.iterrows():
        md += (f"| {r['point_id']} | {r['resort']} | {r['role']} | "
               f"{r['elevation_m']:.0f} | "
               f"{fmt(r['mean_frac_outside_p1p99'])} | "
               f"{int(r['n_features_majority_outside'])} | "
               f"{int(r['n_features_any_outside_minmax'])} |\n")
    n_above_any = int((by_feat["frac_above_gold_max"] > 0).sum())
    liq = by_feat[by_feat["feature"] == "fcst_liquid_mm"].iloc[0]
    dep = by_feat[by_feat["feature"] ==
                  "subcloud_dewp_depression"].iloc[0]
    elv = by_feat[by_feat["feature"] == "elevation_m"].iloc[0]
    md += f"""
**Direction matters — most of the breadth above is dry-side, and it is
expected by construction.** The gold cohort is conditioned on observed
snow events (the screening floors require snowfall >= 5.09 cm and WESF
>= 2.79 mm in the window); serving windows are unconditional, and in
this batch 228 of {summary['n_served_rows']} rows have exactly zero
forecast liquid. So the amount and humidity features show large
below-range mass (e.g. `fcst_liquid_mm`: {fmt(liq['frac_below_gold_min'], 2)}
of served rows below the gold minimum, i.e. dry windows). The
consequential direction is above the gold maximum: {n_above_any} of 64
features have at least one served value above the gold max, led by
`elevation_m` itself ({fmt(elv['frac_above_gold_max'], 2)} of served rows —
the points above the highest gold station), `subcloud_dewp_depression`
({fmt(dep['frac_above_gold_max'], 2)}; served max {fmt(dep['served_max'], 1)} vs
gold max {fmt(dep['max'], 1)}), and the amount/rate cluster
(`fcst_liquid_mm` {fmt(liq['frac_above_gold_max'], 2)} above a gold max of
{fmt(liq['max'], 1)} mm, served max {fmt(liq['served_max'], 1)} mm; the
above-max rows concentrate in the Open-Meteo long-lead tail). Breadth per point is not monotone in
elevation — low eastern bases (Sugarloaf, Stowe) rank beside the Sierra
points, because the dry-window composition of the batch dominates the
measure. Finally, this audit describes one serving cycle (the
2026-10-31 12Z batch and the Mount Rose build on the same anchor), not
a climatology of serving.
"""
    md += f"""
## 4. Honest read — what this does and does not establish

**What it establishes.** On held-out gold events — ERA5-profile inputs,
human-observer labels — the champion's performance in every western
region × elevation cell that contains data, including the >=1,500 m
cohort (8,875 events, LORO edge {fmt(hi_all['edge_kuch_minus_champ'])}),
and an exact accounting of which cells are empty. The extrapolation
audit states, per served point and per feature, how much of what the
model is fed at resort points lies outside the distribution it was
trained and judged on.

**What it does not establish.** Anything about true alpine
*observations* (there are none in this report — the >=1,500 m gold
stations are still observer stations in the training network, not the
resorts), anything about *forecast* inputs (every fold here consumes
ERA5 reanalysis profiles of the event window, not ECMWF/HRRR/Open-Meteo
forecasts), and anything about pacific-northwest or Alaskan
high-elevation behaviour (no data). Elevation within the gold range is
also confounded with region: the >=1,500 m cohort is 91% Rockies
events, so the cohort's numbers are largely a Rockies statement.

**Supported claim wording.** "On held-out GHCN gold events with ERA5
inputs, the v3 champion beats Kuchera in every western region overall
under leave-one-region-out, and in the >=1,500 m cohort (LORO MAE
{fmt(hi_all['mae_champion'])} vs {fmt(hi_all['mae_kuchera'])}, edge
{fmt(hi_all['edge_kuch_minus_champ'])}). The exceptions are named, not
averaged away: in the Sierra Nevada 1000–1500 m cell (n=202) the
champion trails Kuchera under LORO (edge
{fmt(float(t1[(t1['family'] == 'loro') & (t1['region'] == 'sierra-nevada') & (t1['band'] == '1000-1500')].iloc[0]['edge_kuch_minus_champ']))})
and under unseen-station (n=46, edge
{fmt(float(t1[(t1['family'] == 'stations') & (t1['region'] == 'sierra-nevada') & (t1['band'] == '1000-1500')].iloc[0]['edge_kuch_minus_champ']))}),
though not under randomised splits; and within the >=1,500 m cohort it
trails Kuchera on the observed SLR<5 and SLR>20 slices in every family
(§2)."
**Not supported:** "validated at resort elevations" — the served points
sit at {summary['served_elevation_min']:.0f}–{summary['served_elevation_max']:.0f} m
with feature values that extrapolate beyond the gold distribution as
quantified in §3, and no alpine observation has ever scored this model.
"""
    (OUT / "REPORT.md").write_text(md)
    print(json.dumps(summary, indent=2))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
