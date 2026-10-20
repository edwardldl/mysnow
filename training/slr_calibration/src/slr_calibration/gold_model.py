"""Step 2 (gold-only model): benchmark of SLR models on screened direct labels.

Models compared on identical splits, all trained ONLY on the step-1
screened direct (gold) labels with profile features from
``gold_profiles`` (source: open-meteo-historical-forecast-best-match
-- NOT ERA5; see that module's docstring for the provenance decision):

(a) ``rf``            scikit-learn RandomForestRegressor, Pletcher et
                      al. (2026) feature spec (27 features, direct SLR
                      regression).  n_estimators=500; all other
                      hyperparameters are sklearn defaults because the
                      paper does not fully specify them.
(b) ``gbm``           sklearn HistGradientBoostingRegressor, defaults.
                      No published SLR head-to-head exists; this is it.
(c) ``intercept``     per-region direct-intercept calibration of the
                      Kuchera baseline (the salvage winner of the
                      weak-label investigation): baseline x exp(mean
                      log residual) per region, factor clamped to
                      [0.5, 2.0], SLR clamped to [3, 30].
(d) ``eb_gold``       Candidate A: the existing EB hierarchical
                      trainer (trainer.train_artifact) fitted per
                      region on the gold events; applied with
                      evaluation.apply_artifact.
(e) ``kuchera``       raw Kuchera baseline from the same profiles.
(f) ``climatology``   region x month mean observed SLR (fallbacks:
                      region mean, then global mean).
(g) ``rf_eb``         RF + regional EB residual layer: the EB trainer
                      fitted on log(observed / RF) with the RF's
                      station-grouped out-of-fold predictions as the
                      baseline for training rows.

Splits: (1) randomized 60/40, 20 repeats; (2) leave-one-region-out;
(3) water-year temporal holdout (train < WY2026, test = WY2026);
(4) unseen-station holdout (seeded 30% of stations).

This is a research benchmark.  Nothing here writes app artifacts;
model pickles land beside the report under the data tree and are
research files only.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .evaluation import apply_artifact
from .gold_profiles import FEATURE_COLUMNS, PROFILE_SOURCE
from .trainer import TrainingConfig, train_artifact

MODELS = ("rf", "gbm", "intercept", "eb_gold", "kuchera", "climatology", "rf_eb")
MIN_SLR, MAX_SLR = 3.0, 30.0
MIN_FACTOR, MAX_FACTOR = 0.5, 2.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Frame loading
# ---------------------------------------------------------------------------

def load_modeling_frame(features_path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = pd.read_parquet(features_path)
    total = len(frame)
    modeling = frame[(frame["profile_status"] == "ok") & frame["baseline_slr"].notna()].copy()
    modeling = modeling.reset_index(drop=True)
    accounting = {
        "eventsTotal": int(total),
        "modelingRows": int(len(modeling)),
        "excludedByStatus": {
            str(k): int(v) for k, v in frame["profile_status"].value_counts().items()
        },
        "modelingByRegion": {str(k): int(v) for k, v in modeling["region"].value_counts().items()},
        "modelingWaterYears": sorted(int(y) for y in modeling["water_year"].unique()),
    }
    return modeling, accounting


# ---------------------------------------------------------------------------
# Simple models
# ---------------------------------------------------------------------------

class InterceptModel:
    """Per-region multiplicative intercept on the Kuchera baseline."""

    def fit(self, train: pd.DataFrame) -> "InterceptModel":
        residuals = np.log(train["observed_slr"] / train["baseline_slr"])
        self.global_log = float(residuals.mean())
        self.by_region = {
            str(region): float(group.mean())
            for region, group in residuals.groupby(train["region"])
        }
        return self

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        logs = test["region"].map(self.by_region).fillna(self.global_log).to_numpy()
        factors = np.exp(np.clip(logs, math.log(MIN_FACTOR), math.log(MAX_FACTOR)))
        return np.clip(test["baseline_slr"].to_numpy() * factors, MIN_SLR, MAX_SLR)


class ClimatologyModel:
    """Region x month mean observed SLR with region/global fallbacks."""

    def fit(self, train: pd.DataFrame) -> "ClimatologyModel":
        self.global_mean = float(train["observed_slr"].mean())
        self.by_region = {str(k): float(v) for k, v in train.groupby("region")["observed_slr"].mean().items()}
        self.by_region_month = {
            (str(r), int(m)): float(v)
            for (r, m), v in train.groupby(["region", "month"])["observed_slr"].mean().items()
        }
        self.by_month = {int(k): float(v) for k, v in train.groupby("month")["observed_slr"].mean().items()}
        return self

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        out = []
        for region, month in zip(test["region"], test["month"]):
            value = self.by_region_month.get((str(region), int(month)))
            if value is None:
                value = self.by_region.get(str(region))
            if value is None:
                value = self.by_month.get(int(month), self.global_mean)
            out.append(value)
        return np.array(out)


# ---------------------------------------------------------------------------
# EB (Candidate A) helpers on top of the existing trainer
# ---------------------------------------------------------------------------

def _trainer_rows(frame: pd.DataFrame, baseline_column: str = "baseline_slr") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in frame.itertuples(index=False):
        rows.append({
            "event_id": record.event_id,
            "station_id": record.station_id,
            "site_id": record.site_id,
            "valid_time": str(record.valid_time),
            "latitude": float(record.latitude),
            "longitude": float(record.longitude),
            "observed_slr": float(record.observed_slr),
            "baseline_slr": float(getattr(record, baseline_column)),
            "baseline_method": "kuchera",
            "climate_regime": record.climate_regime,
            "qc_passed": True,
            "qc_weight": 1.0,
        })
    return rows


def _apply_eb(artifact: dict[str, Any], frame: pd.DataFrame, baseline_column: str = "baseline_slr") -> np.ndarray:
    predictions = []
    for record in frame.itertuples(index=False):
        row = {
            "baseline_method": "kuchera",
            "latitude": float(record.latitude),
            "longitude": float(record.longitude),
            "site_id": record.site_id,
            "baseline_slr": float(getattr(record, baseline_column)),
        }
        predictions.append(apply_artifact(artifact, row).calibrated_slr)
    return np.array(predictions)


class EBPerRegionModel:
    """Candidate A fitted per region; unseen regions use a global fit."""

    def fit(self, train: pd.DataFrame) -> "EBPerRegionModel":
        self.global_artifact = train_artifact(
            _trainer_rows(train),
            TrainingConfig(artifact_id="gold-eb-global", source_label="screened-v2 gold events (step 2 benchmark)"),
        )
        self.by_region: dict[str, dict[str, Any]] = {}
        for region, group in train.groupby("region"):
            if len(group) >= 30:
                self.by_region[str(region)] = train_artifact(
                    _trainer_rows(group),
                    TrainingConfig(artifact_id=f"gold-eb-{region}", source_label="screened-v2 gold events (step 2 benchmark)"),
                )
        return self

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        test = test.reset_index(drop=True)
        out = np.empty(len(test))
        for region, group in test.groupby("region"):
            artifact = self.by_region.get(str(region), self.global_artifact)
            out[group.index.to_numpy()] = _apply_eb(artifact, group)
        return out


class RFEBModel:
    """RF plus an EB residual layer fitted on out-of-fold RF predictions."""

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def fit(self, train: pd.DataFrame) -> "RFEBModel":
        from sklearn.ensemble import RandomForestRegressor

        X = train[list(FEATURE_COLUMNS)].to_numpy()
        y = train["observed_slr"].to_numpy()
        # Station-grouped 5-fold out-of-fold predictions for honest
        # residual fitting (no in-sample RF residuals).
        stations = np.array(sorted(train["station_id"].unique()))
        rng = np.random.default_rng(self.seed)
        rng.shuffle(stations)
        fold_of = {station: i % 5 for i, station in enumerate(stations)}
        folds = train["station_id"].map(fold_of).to_numpy()
        oof = np.full(len(train), np.nan)
        for fold in range(5):
            mask = folds == fold
            if mask.sum() == 0 or (~mask).sum() == 0:
                continue
            model = RandomForestRegressor(n_estimators=500, random_state=self.seed, n_jobs=-1)
            model.fit(X[~mask], y[~mask])
            oof[mask] = model.predict(X[mask])
        if np.isnan(oof).any():
            oof[np.isnan(oof)] = train["baseline_slr"].to_numpy()[np.isnan(oof)]
        self.rf = RandomForestRegressor(n_estimators=500, random_state=self.seed, n_jobs=-1)
        self.rf.fit(X, y)
        residual_frame = train.copy()
        residual_frame["rf_oof"] = np.clip(oof, MIN_SLR, MAX_SLR)
        self.eb_artifact = train_artifact(
            _trainer_rows(residual_frame, baseline_column="rf_oof"),
            TrainingConfig(artifact_id="gold-rf-eb-residual", source_label="screened-v2 gold events (step 2 benchmark)"),
        )
        return self

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        X = test[list(FEATURE_COLUMNS)].to_numpy()
        rf_pred = np.clip(self.rf.predict(X), MIN_SLR, MAX_SLR)
        frame = test.copy()
        frame["rf_pred"] = rf_pred
        return _apply_eb(self.eb_artifact, frame, baseline_column="rf_pred")


# ---------------------------------------------------------------------------
# Tree models
# ---------------------------------------------------------------------------

def _fit_rf(train: pd.DataFrame, seed: int):
    from sklearn.ensemble import RandomForestRegressor

    model = RandomForestRegressor(n_estimators=500, random_state=seed, n_jobs=-1)
    model.fit(train[list(FEATURE_COLUMNS)].to_numpy(), train["observed_slr"].to_numpy())
    return model


def _fit_gbm(train: pd.DataFrame, seed: int):
    from sklearn.ensemble import HistGradientBoostingRegressor

    model = HistGradientBoostingRegressor(random_state=seed)
    model.fit(train[list(FEATURE_COLUMNS)].to_numpy(), train["observed_slr"].to_numpy())
    return model


def fit_predict(model_name: str, train: pd.DataFrame, test: pd.DataFrame, seed: int) -> np.ndarray:
    if model_name == "rf":
        return _fit_rf(train, seed).predict(test[list(FEATURE_COLUMNS)].to_numpy())
    if model_name == "gbm":
        return _fit_gbm(train, seed).predict(test[list(FEATURE_COLUMNS)].to_numpy())
    if model_name == "intercept":
        return InterceptModel().fit(train).predict(test)
    if model_name == "eb_gold":
        return EBPerRegionModel().fit(train).predict(test)
    if model_name == "kuchera":
        return test["baseline_slr"].to_numpy()
    if model_name == "climatology":
        return ClimatologyModel().fit(train).predict(test)
    if model_name == "rf_eb":
        return RFEBModel(seed).fit(train).predict(test)
    raise ValueError(f"unknown model {model_name}")


# ---------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------

def split_random(frame: pd.DataFrame, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(frame))
    cut = int(round(0.6 * len(frame)))
    return frame.iloc[order[:cut]].copy(), frame.iloc[order[cut:]].copy()


def split_loro(frame: pd.DataFrame, region: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    return frame[frame["region"] != region].copy(), frame[frame["region"] == region].copy()


def split_temporal(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    train = frame[frame["water_year"] < 2026].copy()
    test = frame[frame["water_year"] == 2026].copy()
    return train, test


def split_stations(frame: pd.DataFrame, seed: int, fraction: float = 0.30) -> tuple[pd.DataFrame, pd.DataFrame]:
    stations = np.array(sorted(frame["station_id"].unique()))
    rng = np.random.default_rng(seed)
    rng.shuffle(stations)
    held = set(stations[: int(round(fraction * len(stations)))].tolist())
    return frame[~frame["station_id"].isin(held)].copy(), frame[frame["station_id"].isin(held)].copy()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _metrics(observed: np.ndarray, predicted: np.ndarray) -> dict[str, Any]:
    errors = predicted - observed
    return {
        "n": int(len(observed)),
        "mae": round(float(np.mean(np.abs(errors))), 4) if len(observed) else None,
        "bias": round(float(np.mean(errors)), 4) if len(observed) else None,
        "rmse": round(float(np.sqrt(np.mean(errors ** 2))), 4) if len(observed) else None,
    }


def evaluate_predictions(frame: pd.DataFrame, predicted: np.ndarray) -> dict[str, Any]:
    observed = frame["observed_slr"].to_numpy()
    result: dict[str, Any] = {"overall": _metrics(observed, predicted)}
    per_region: dict[str, Any] = {}
    for region, group in frame.groupby("region"):
        mask = (frame["region"] == region).to_numpy()
        per_region[str(region)] = _metrics(observed[mask], predicted[mask])
    result["perRegion"] = per_region
    for label, mask in (("tailHigh_slr_gt_20", observed > 20), ("tailLow_slr_lt_5", observed < 5)):
        result[label] = _metrics(observed[mask], predicted[mask]) if mask.any() else {"n": 0, "mae": None, "bias": None, "rmse": None}
    snow_error = predicted * frame["fresh_swe_mm"].to_numpy() - frame["fresh_snow_mm"].to_numpy()
    result["snowfall"] = {
        "n": int(len(frame)),
        "maeMm": round(float(np.mean(np.abs(snow_error))), 4),
        "biasMm": round(float(np.mean(snow_error)), 4),
    }
    return result


# ---------------------------------------------------------------------------
# Benchmark driver
# ---------------------------------------------------------------------------

def _run_split(
    name: str,
    train: pd.DataFrame,
    test: pd.DataFrame,
    seed: int,
    models: tuple[str, ...] = MODELS,
) -> dict[str, Any]:
    out: dict[str, Any] = {"trainN": int(len(train)), "testN": int(len(test)), "models": {}}
    for model_name in models:
        predicted = fit_predict(model_name, train, test, seed)
        out["models"][model_name] = evaluate_predictions(test, predicted)
    return out


def run_benchmark(features_path: Path, output_dir: Path, random_repeats: int = 20) -> dict[str, Any]:
    frame, accounting = load_modeling_frame(features_path)
    results: dict[str, Any] = {
        "profileSource": PROFILE_SOURCE,
        "accounting": accounting,
        "splits": {},
    }

    # Split 1: randomized 60/40 repeats (headline, Pletcher-comparable).
    repeats: list[dict[str, Any]] = []
    for rep in range(random_repeats):
        train, test = split_random(frame, seed=1000 + rep)
        repeats.append(_run_split(f"random60_40_rep{rep}", train, test, seed=1000 + rep))
    aggregated: dict[str, Any] = {}
    for model_name in MODELS:
        maes = [r["models"][model_name]["overall"]["mae"] for r in repeats]
        aggregated[model_name] = {
            "maeMean": round(float(np.mean(maes)), 4),
            "maeStd": round(float(np.std(maes)), 4),
            "maeMin": round(float(np.min(maes)), 4),
            "maeMax": round(float(np.max(maes)), 4),
            "rep0": repeats[0]["models"][model_name],
        }
    results["splits"]["random60_40"] = {
        "repeats": random_repeats,
        "trainN": repeats[0]["trainN"],
        "testN": repeats[0]["testN"],
        "aggregated": aggregated,
    }

    # Permutation importance on repeat 0's split.
    from sklearn.inspection import permutation_importance

    train0, test0 = split_random(frame, seed=1000)
    rf0 = _fit_rf(train0, seed=1000)
    importance = permutation_importance(
        rf0, test0[list(FEATURE_COLUMNS)].to_numpy(), test0["observed_slr"].to_numpy(),
        n_repeats=5, random_state=0, n_jobs=-1,
    )
    order = np.argsort(importance.importances_mean)[::-1]
    results["permutationImportance"] = [
        {"feature": FEATURE_COLUMNS[i], "importanceMean": round(float(importance.importances_mean[i]), 4),
         "importanceStd": round(float(importance.importances_std[i]), 4)}
        for i in order
    ]

    # Split 2: leave-one-region-out.
    loro: dict[str, Any] = {}
    for region in sorted(frame["region"].unique()):
        train, test = split_loro(frame, str(region))
        loro[str(region)] = _run_split(f"loro_{region}", train, test, seed=2000)
    results["splits"]["leaveOneRegionOut"] = loro

    # Split 3: temporal holdout (WY2026).
    train, test = split_temporal(frame)
    results["splits"]["temporalWY2026"] = _run_split("temporal", train, test, seed=3000)

    # Split 4: unseen stations.
    train, test = split_stations(frame, seed=4000)
    results["splits"]["unseenStations"] = _run_split("stations", train, test, seed=4000)

    # Final research artifacts: RF + GBM on all modeling rows.
    import joblib

    output_dir.mkdir(parents=True, exist_ok=True)
    final_rf = _fit_rf(frame, seed=0)
    final_gbm = _fit_gbm(frame, seed=0)
    joblib.dump(final_rf, output_dir / "rf_full.joblib")
    joblib.dump(final_gbm, output_dir / "gbm_full.joblib")
    (output_dir / "model_sidecar.json").write_text(json.dumps({
        "profileSource": PROFILE_SOURCE,
        "features": list(FEATURE_COLUMNS),
        "trainingRows": int(len(frame)),
        "models": {
            "rf": "RandomForestRegressor(n_estimators=500, sklearn defaults otherwise)",
            "gbm": "HistGradientBoostingRegressor(sklearn defaults)",
        },
        "note": "Research artifacts only. Not app artifacts; do not bundle into src/.",
    }, indent=2), encoding="utf-8")

    (output_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt(value: Any) -> str:
    return "—" if value is None else f"{value:.3f}"


def _loro_winners(results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    winners: dict[str, dict[str, Any]] = {}
    for region, split in results["splits"]["leaveOneRegionOut"].items():
        maes = {m: split["models"][m]["overall"]["mae"] for m in MODELS}
        best = min(maes, key=lambda m: maes[m])
        winners[region] = {
            "winner": best,
            "winnerMae": maes[best],
            "rfMae": maes["rf"],
            "kucheraMae": maes["kuchera"],
            "interceptMae": maes["intercept"],
            "rfVsKuchera": round(maes["kuchera"] - maes["rf"], 4),
        }
    return winners


def write_report(results: dict[str, Any], coverage: dict[str, Any] | None, path: Path) -> None:
    acc = results["accounting"]
    agg = results["splits"]["random60_40"]["aggregated"]
    winners = _loro_winners(results)
    lines: list[str] = []
    lines.append("# Step 2 — pooled gold-only profile-feature model (v1 benchmark)")
    lines.append("")
    lines.append("## Provenance — read first")
    lines.append("")
    lines.append(
        "**Profile source: `open-meteo-historical-forecast-best-match` — the Open-Meteo "
        "historical-forecast best-match archive, NOT ERA5.** Pletcher et al. (2026) trained on "
        "ERA5, but ERA5 pressure levels are unreachable from this workspace: the Open-Meteo "
        "archive (ERA5) API returns every pressure-level field null with `hourly_units` "
        "\"undefined\" (its published variable list contains no pressure-level variables at "
        "all), and Copernicus CDS requires a personal API key this project does not hold. "
        "The best-match archive is the fallback sanctioned for this case; it is also the "
        "source of every previous baseline in this project, so features and Kuchera "
        "baselines share provenance. The archive's coverage starts mid-2021, so the "
        "modeling pool is the 2021-06+ subset of the screened pool. Treat all numbers "
        "below as best-match-archive numbers, not ERA5 numbers."
    )
    lines.append("")
    lines.append("Deviations from the Pletcher et al. spec: profile source (above); 13 API "
                 "pressure levels instead of ERA5's 37 for the AGL interpolation; wind from "
                 "the API wind_speed field; 24-h means over each event's observation window "
                 "(24 h ending at the stored local-standard valid_time) rather than a "
                 "calendar day; RF n_estimators=500 with sklearn defaults otherwise (paper "
                 "does not fully specify hyperparameters); GBM at sklearn defaults.")
    lines.append("")
    lines.append("## Feature coverage")
    lines.append("")
    if coverage:
        lines.append(f"Profile source: `{coverage['profileSource']}`. Total screened events: "
                     f"{coverage['eventsTotal']}. Status counts: "
                     + ", ".join(f"{k}={v}" for k, v in sorted(coverage["statusCounts"].items())) + ".")
        lines.append("")
        lines.append("| Region | ok | incomplete_profile | no_baseline | outside_archive_coverage | fetch_missing |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for region, counts in sorted(coverage["byRegion"].items()):
            lines.append(
                f"| {region} | {counts.get('ok', 0)} | {counts.get('incomplete_profile', 0)} | "
                f"{counts.get('no_baseline', 0)} | {counts.get('outside_archive_coverage', 0)} | "
                f"{counts.get('fetch_missing', 0)} |"
            )
        lines.append("")
    lines.append(f"Modeling rows (complete features + Kuchera baseline): **{acc['modelingRows']}** "
                 f"of {acc['eventsTotal']} screened events. By region: "
                 + ", ".join(f"{k}={v}" for k, v in sorted(acc["modelingByRegion"].items()))
                 + f". Water years: {acc['modelingWaterYears']}.")
    lines.append("")
    lines.append("## Headline — randomized 60/40 (20 repeats, Pletcher-comparable)")
    lines.append("")
    lines.append("| Model | MAE mean | MAE std | rep0 bias | rep0 RMSE | rep0 tail>20 MAE (n) | rep0 tail<5 MAE (n) | rep0 snowfall MAE (mm) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for model in MODELS:
        rep0 = agg[model]["rep0"]
        lines.append(
            f"| {model} | {agg[model]['maeMean']:.3f} | {agg[model]['maeStd']:.3f} | "
            f"{_fmt(rep0['overall']['bias'])} | {_fmt(rep0['overall']['rmse'])} | "
            f"{_fmt(rep0['tailHigh_slr_gt_20']['mae'])} ({rep0['tailHigh_slr_gt_20']['n']}) | "
            f"{_fmt(rep0['tailLow_slr_lt_5']['mae'])} ({rep0['tailLow_slr_lt_5']['n']}) | "
            f"{_fmt(rep0['snowfall']['maeMm'])} |"
        )
    lines.append("")
    lines.append("Published reference (different labels/profiles, NOT directly comparable): "
                 "Pletcher et al. RF MAE 2.92 CONUS; operational methods 3.51–4.76.")
    lines.append("")
    lines.append("## Leave-one-region-out (decisive transfer test)")
    lines.append("")
    lines.append("| Held-out region | test n | winner | winner MAE | RF MAE | GBM MAE | intercept MAE | EB-gold MAE | RF+EB MAE | climatology MAE | Kuchera MAE | RF − Kuchera |")
    lines.append("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for region, split in sorted(results["splits"]["leaveOneRegionOut"].items()):
        m = split["models"]
        w = winners[region]
        lines.append(
            f"| {region} | {split['testN']} | {w['winner']} | {w['winnerMae']:.3f} | "
            f"{m['rf']['overall']['mae']:.3f} | {m['gbm']['overall']['mae']:.3f} | "
            f"{m['intercept']['overall']['mae']:.3f} | {m['eb_gold']['overall']['mae']:.3f} | "
            f"{m['rf_eb']['overall']['mae']:.3f} | {m['climatology']['overall']['mae']:.3f} | "
            f"{m['kuchera']['overall']['mae']:.3f} | {w['rfVsKuchera']:+.3f} |"
        )
    lines.append("")
    lines.append("Per-region verdicts: "
                 + "; ".join(
                     f"{region}: **{w['winner']}** wins (MAE {w['winnerMae']:.3f}); RF "
                     f"{'beats' if w['rfVsKuchera'] > 0 else 'loses to'} Kuchera by "
                     f"{abs(w['rfVsKuchera']):.3f}"
                     for region, w in sorted(winners.items())
                 ) + ".")
    rf_loro_wins = sum(1 for w in winners.values() if w["rfVsKuchera"] > 0)
    lines.append("")
    lines.append(f"RF beats raw Kuchera under LORO in {rf_loro_wins} of {len(winners)} regions. "
                 "The randomized-split RF advantage "
                 + ("survives" if rf_loro_wins >= len(winners) - 1 else "does NOT fully survive")
                 + " the transfer test; see per-region rows above.")
    lines.append("")
    for split_name, title in (("temporalWY2026", "Temporal holdout (train < WY2026, test WY2026)"),
                              ("unseenStations", "Unseen-station holdout (30% of stations)")):
        split = results["splits"][split_name]
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"Train n={split['trainN']}, test n={split['testN']}.")
        lines.append("")
        lines.append("| Model | MAE | bias | RMSE | tail>20 MAE (n) | tail<5 MAE (n) | snowfall MAE (mm) |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for model in MODELS:
            r = split["models"][model]
            lines.append(
                f"| {model} | {r['overall']['mae']:.3f} | {_fmt(r['overall']['bias'])} | "
                f"{_fmt(r['overall']['rmse'])} | {_fmt(r['tailHigh_slr_gt_20']['mae'])} "
                f"({r['tailHigh_slr_gt_20']['n']}) | {_fmt(r['tailLow_slr_lt_5']['mae'])} "
                f"({r['tailLow_slr_lt_5']['n']}) | {_fmt(r['snowfall']['maeMm'])} |"
            )
        lines.append("")
    lines.append("## RF permutation importance (randomized split, rep 0)")
    lines.append("")
    for entry in results["permutationImportance"][:10]:
        lines.append(f"- {entry['feature']}: {entry['importanceMean']:.3f} ± {entry['importanceStd']:.3f}")
    top = results["permutationImportance"][:5]
    dgx = [e["feature"] for e in top if e["feature"].startswith("t_") and e["feature"] in
           ("t_1200", "t_1500", "t_1800", "t_2100", "t_2400")]
    lines.append("")
    lines.append(
        "DGZ check: "
        + (f"{len(dgx)} of the top 5 features are temperatures at 1200–2400 m AGL "
           f"({', '.join(dgx)}), replicating Pletcher et al.'s finding that growth-layer "
           "temperatures dominate." if len(dgx) >= 3 else
           f"only {len(dgx)} of the top 5 are 1200–2400 m temperatures — the DGZ "
           "concentration Pletcher et al. reported is weaker here.")
    )
    lines.append("")
    lines.append("## Verdicts")
    lines.append("")
    rf_mae, gbm_mae = agg["rf"]["maeMean"], agg["gbm"]["maeMean"]
    lines.append(f"- **RF vs GBM (randomized):** RF {rf_mae:.3f} vs GBM {gbm_mae:.3f} — "
                 f"{'RF' if rf_mae < gbm_mae else 'GBM'} wins by {abs(rf_mae - gbm_mae):.3f} MAE.")
    loro_rf = float(np.mean([results['splits']['leaveOneRegionOut'][r]['models']['rf']['overall']['mae'] for r in winners]))
    loro_gbm = float(np.mean([results['splits']['leaveOneRegionOut'][r]['models']['gbm']['overall']['mae'] for r in winners]))
    lines.append(f"- **RF vs GBM (LORO mean):** RF {loro_rf:.3f} vs GBM {loro_gbm:.3f}.")
    rfeb = agg["rf_eb"]["maeMean"]
    lines.append(f"- **RF+EB residual vs RF (randomized):** {rfeb:.3f} vs {rf_mae:.3f} — "
                 f"the regional EB residual layer {'helps' if rfeb < rf_mae else 'does not help'} "
                 f"by {abs(rfeb - rf_mae):.3f} MAE on randomized splits; LORO means: RF+EB "
                 f"{float(np.mean([results['splits']['leaveOneRegionOut'][r]['models']['rf_eb']['overall']['mae'] for r in winners])):.3f} "
                 f"vs RF {loro_rf:.3f}.")
    lines.append(f"- **Our correction approaches vs direct regression:** intercept MAE "
                 f"{agg['intercept']['maeMean']:.3f}, EB-gold {agg['eb_gold']['maeMean']:.3f} "
                 f"(randomized) vs RF {rf_mae:.3f}; raw Kuchera {agg['kuchera']['maeMean']:.3f}; "
                 f"climatology {agg['climatology']['maeMean']:.3f}.")
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append("- Labels are the step-1 screened pool: the CoCoRaHS tier is behaviour-screened, "
                 "not provenance-proven (81% pass on survey-confirmed samplers; no confirmed-copier "
                 "reference exists). Northeast and Great Lakes stations are almost all below "
                 "900 m elevation; Alaska's screen validation was indirect only.")
    lines.append("- Profiles are a best-match forecast-archive blend, not reanalysis: the "
                 "contributing model varies by date, and forecast (not analysis) fields carry "
                 "lead-time error that ERA5 would not. Numbers are therefore a lower bound "
                 "on what the same spec would do on true ERA5 profiles.")
    lines.append("- Events before 2021-06 are excluded by archive coverage; the temporal "
                 "holdout is a single water year (WY2026).")
    lines.append("- Tail subsets are small in some splits; read tail MAEs with their n.")
    lines.append("- Model pickles beside this report are research artifacts only — not app "
                 "artifacts, never bundled into src/, and the production calibration gates "
                 "are untouched.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_evaluate_gold_models(features_path: Path, output_dir: Path, random_repeats: int = 20) -> dict[str, Any]:
    results = run_benchmark(features_path, output_dir, random_repeats=random_repeats)
    coverage = None
    coverage_path = output_dir / "feature_coverage.json"
    if coverage_path.exists():
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    write_report(results, coverage, output_dir / "MODEL_REPORT.md")
    return results
