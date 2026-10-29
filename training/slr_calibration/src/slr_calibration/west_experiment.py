"""West-only experiment: does dropping or down-weighting the eastern
regions (Northeast + Great Lakes) improve the champion's scores on
western-mountain events?

Arms (pre-declared; nothing here was tuned after seeing results):

(a) ``pooled``    control — train on all rows, exactly as the v2
                  benchmark (gold_model.run_benchmark /
                  run_variant_benchmark).
(b) ``west_only`` train exclusively on western rows (Rockies,
                  Pacific Northwest, Sierra Nevada, Alaska).  The EB
                  residual layers therefore fit on western training
                  folds only.
(c) ``weighted``  pooled training with eastern rows at sample_weight
                  0.25 (western rows 1.0) in the RF / GBM base
                  learners.  The EB residual layers follow the standard
                  harness convention: fitted UNWEIGHTED on the arm's
                  training fold (the EB trainer consumes qc_weight,
                  which the gold harness fixes at 1.0 for every row).

Models: ``rf_eb`` (the reference baseline) and ``ens_eb`` (the
user-designated champion: mean of RF+EB and GBM+EB predictions).

Folds are the v2 pooled folds — the identical split functions and
seeds as the benchmark (randomised 60/40 with seeds 1000+rep, LORO
seed 2000, temporal seed 3000, unseen-station seed 4000).  Every arm
is scored on the SAME western test events: the western subset of each
pooled test fold (randomised / temporal / unseen-station) or the held
western region itself (LORO).  Arm B's training set for a fold is the
western complement of that fold, so event-level test assignments are
identical across arms.  As a fold/model identity check, the pooled
arm is ALSO scored on the full test folds and compared against the
stored v2 results (results.json / results_variants.json).

Judging (pre-declared): primary model ENS-EB.  An arm "helps" iff its
western LORO mean improves >= 0.03 vs pooled AND no western region's
LORO MAE regresses > 0.10 AND its western randomised MAE does not
regress > 0.05.  RF+EB is judged under the same rule as a secondary
read.  Eastern scores are never computed and never gated.

Research only.  Nothing here writes app artifacts; outputs land
beside the v2 benchmark outputs under the data tree.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .gold_model import (
    GBMEBModel,
    RFEBModel,
    _frame_profile_source,
    evaluate_predictions,
    load_modeling_frame,
    split_loro,
    split_random,
    split_stations,
    split_temporal,
)
from .gold_profiles import ERA5_PROFILE_SOURCE

WEST_REGIONS = ("alaska", "pacific-northwest", "rockies", "sierra-nevada")
EAST_REGIONS = ("great-lakes", "northeast")
EAST_WEIGHT = 0.25
WEST_ARMS = ("pooled", "west_only", "weighted")
WEST_MODELS = ("rf_eb", "ens_eb")

# Pre-declared decision-rule thresholds (western LORO is primary).
LORO_GAIN_MIN = 0.03
REGION_REGRESSION_MAX = 0.10
RANDOMISED_REGRESSION_MAX = 0.05


def is_west(frame: pd.DataFrame) -> pd.Series:
    return frame["region"].isin(list(WEST_REGIONS))


def arm_training(
    train_full: pd.DataFrame, arm: str
) -> tuple[pd.DataFrame, np.ndarray | None]:
    """(training frame, base-learner sample weights) for one arm.

    Weights apply to the RF/GBM base learners only; EB layers always
    follow the harness convention (unweighted), for every arm.
    """
    if arm == "pooled":
        return train_full, None
    if arm == "west_only":
        return train_full[is_west(train_full)].copy(), None
    if arm == "weighted":
        weights = np.where(
            train_full["region"].isin(list(EAST_REGIONS)).to_numpy(),
            EAST_WEIGHT,
            1.0,
        )
        return train_full, weights
    raise ValueError(f"unknown arm {arm}")


def judge_west_arm(control: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Pre-declared west-experiment decision rule for one model.

    ``control`` / ``candidate`` carry ``loroMean``, ``loroByRegion``
    ({region: MAE}) and ``randomised`` (western randomised MAE mean).
    Deltas are control - candidate (positive = improvement).
    """
    loro_delta = round(float(control["loroMean"] - candidate["loroMean"]), 4)
    region_deltas = {
        region: round(
            float(control["loroByRegion"][region] - candidate["loroByRegion"][region]), 4
        )
        for region in WEST_REGIONS
    }
    randomised_delta = round(float(control["randomised"] - candidate["randomised"]), 4)
    worst_region_delta = min(region_deltas.values())
    helps = (
        loro_delta >= LORO_GAIN_MIN
        and worst_region_delta >= -REGION_REGRESSION_MAX
        and randomised_delta >= -RANDOMISED_REGRESSION_MAX
    )
    return {
        "loroMeanDelta": loro_delta,
        "loroByRegionDelta": region_deltas,
        "randomisedDelta": randomised_delta,
        "worstRegionDelta": worst_region_delta,
        "helps": bool(helps),
    }


def _fit_arm(
    train_full: pd.DataFrame, arm: str, seed: int
) -> tuple[RFEBModel, GBMEBModel, int]:
    train, weights = arm_training(train_full, arm)
    rf_eb = RFEBModel(seed).fit(train, sample_weight=weights)
    gbm_eb = GBMEBModel(seed).fit(train, sample_weight=weights)
    return rf_eb, gbm_eb, int(len(train))


def _predict(rf_eb: RFEBModel, gbm_eb: GBMEBModel, test: pd.DataFrame) -> dict[str, np.ndarray]:
    _, rf_eb_pred = rf_eb.predict_parts(test)
    _, gbm_eb_pred = gbm_eb.predict_parts(test)
    return {"rf_eb": rf_eb_pred, "ens_eb": (rf_eb_pred + gbm_eb_pred) / 2.0}


def _score_split(
    results: dict[str, Any],
    verification: dict[str, Any],
    split_key: str,
    train_full: pd.DataFrame,
    test_full: pd.DataFrame,
    seed: int,
    *,
    test_is_west: bool = False,
) -> None:
    """Fit every arm on one fold; score western test events (+ pooled
    arm on the full fold, for the v2 identity verification)."""
    test_west = test_full if test_is_west else test_full[is_west(test_full)].copy()
    for arm in WEST_ARMS:
        rf_eb, gbm_eb, train_n = _fit_arm(train_full, arm, seed)
        predictions = _predict(rf_eb, gbm_eb, test_west)
        block: dict[str, Any] = {
            "trainN": train_n,
            "testN": int(len(test_west)),
            "models": {
                model: evaluate_predictions(test_west, predictions[model])
                for model in WEST_MODELS
            },
        }
        results["arms"][arm]["splits"][split_key] = block
        if arm == "pooled":
            full_predictions = _predict(rf_eb, gbm_eb, test_full)
            verification[split_key] = {
                model: evaluate_predictions(test_full, full_predictions[model])["overall"]
                for model in WEST_MODELS
            }


def _aggregate_randomised(
    per_rep: list[dict[str, Any]],
) -> dict[str, Any]:
    aggregated: dict[str, Any] = {}
    for model in WEST_MODELS:
        maes = [rep["models"][model]["overall"]["mae"] for rep in per_rep]
        aggregated[model] = {
            "maeMean": round(float(np.mean(maes)), 4),
            "maeStd": round(float(np.std(maes)), 4),
            "maeMin": round(float(np.min(maes)), 4),
            "maeMax": round(float(np.max(maes)), 4),
            "maeReps": maes,
            "rep0": per_rep[0]["models"][model],
        }
    return aggregated


def _reference_metrics(
    standard: dict[str, Any] | None, variants: dict[str, Any] | None
) -> dict[str, Any]:
    """The stored v2 pooled numbers the verification compares against."""
    reference: dict[str, Any] = {}

    def grab(results: dict[str, Any] | None, model: str) -> dict[str, Any] | None:
        if results is None:
            return None
        splits = results["splits"]
        return {
            "random60_40MaeMean": splits["random60_40"]["aggregated"][model]["maeMean"],
            "temporalWY2026": splits["temporalWY2026"]["models"][model]["overall"]["mae"],
            "unseenStations": splits["unseenStations"]["models"][model]["overall"]["mae"],
            "leaveOneRegionOutWest": {
                region: splits["leaveOneRegionOut"][region]["models"][model]["overall"]["mae"]
                for region in WEST_REGIONS
            },
        }

    reference["rf_eb"] = grab(standard, "rf_eb")
    reference["ens_eb"] = grab(variants, "ens_eb")
    return reference


def _verification_block(
    standard_path: Path, variants_path: Path, computed: dict[str, Any]
) -> dict[str, Any]:
    standard = json.loads(standard_path.read_text(encoding="utf-8")) if standard_path.exists() else None
    variants = json.loads(variants_path.read_text(encoding="utf-8")) if variants_path.exists() else None
    reference = _reference_metrics(standard, variants)
    matches: dict[str, Any] = {}

    def close(a: float | None, b: float | None) -> bool:
        return a is not None and b is not None and abs(float(a) - float(b)) <= 0.0002

    for model in WEST_MODELS:
        ref = reference.get(model)
        if ref is None:
            matches[model] = {"available": False}
            continue
        loro_match = all(
            close(
                computed["leaveOneRegionOutWest"][region][model]["mae"],
                ref["leaveOneRegionOutWest"][region],
            )
            for region in WEST_REGIONS
        )
        matches[model] = {
            "available": True,
            "random60_40MaeMean": close(
                computed["random60_40MaeMean"][model], ref["random60_40MaeMean"]
            ),
            "temporalWY2026": close(
                computed["temporalWY2026"][model]["mae"], ref["temporalWY2026"]
            ),
            "unseenStations": close(
                computed["unseenStations"][model]["mae"], ref["unseenStations"]
            ),
            "leaveOneRegionOutWest": loro_match,
        }
    return {"reference": reference, "computed": computed, "matches": matches}


def run_west_experiment(
    features_path: Path,
    output_dir: Path,
    random_repeats: int = 20,
) -> dict[str, Any]:
    frame, accounting = load_modeling_frame(features_path)
    results: dict[str, Any] = {
        "experiment": "west-only (pooled vs west-only vs east-downweighted training)",
        "profileSource": _frame_profile_source(frame),
        "accounting": accounting,
        "regions": {"west": list(WEST_REGIONS), "east": list(EAST_REGIONS)},
        "eastWeight": EAST_WEIGHT,
        "models": list(WEST_MODELS),
        "arms": {arm: {"splits": {}} for arm in WEST_ARMS},
        "complete": False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "west_experiment.json"

    def flush() -> None:
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    flush()
    pooled_full: dict[str, Any] = {}  # pooled arm scored on FULL folds

    # --- LORO over the four western regions (verification anchor). ---
    loro_pooled_full: dict[str, Any] = {}
    for region in WEST_REGIONS:
        train_full, test = split_loro(frame, region)
        per_arm: dict[str, Any] = {}
        for arm in WEST_ARMS:
            rf_eb, gbm_eb, train_n = _fit_arm(train_full, arm, seed=2000)
            predictions = _predict(rf_eb, gbm_eb, test)
            per_arm[arm] = {
                "trainN": train_n,
                "testN": int(len(test)),
                "models": {
                    model: evaluate_predictions(test, predictions[model])
                    for model in WEST_MODELS
                },
            }
            if arm == "pooled":
                loro_pooled_full[region] = {
                    model: per_arm[arm]["models"][model]["overall"] for model in WEST_MODELS
                }
        for arm in WEST_ARMS:
            results["arms"][arm]["splits"].setdefault("leaveOneRegionOutWest", {})[region] = per_arm[arm]
        flush()
        print(f"[west] LORO {region} done", flush=True)
    pooled_full["leaveOneRegionOutWest"] = {
        region: {model: loro_pooled_full[region][model] for model in WEST_MODELS}
        for region in WEST_REGIONS
    }

    # --- Temporal + unseen-station (western subsets of pooled folds). ---
    for split_key, (train_full, test_full), seed in (
        ("temporalWY2026", split_temporal(frame), 3000),
        ("unseenStations", split_stations(frame, seed=4000), 4000),
    ):
        verification: dict[str, Any] = {}
        _score_split(
            results, verification, split_key, train_full, test_full, seed
        )
        pooled_full[split_key] = verification[split_key]
        flush()
        print(f"[west] {split_key} done", flush=True)

    # --- Randomised 60/40 repeats (western subsets of pooled folds). ---
    per_rep: dict[str, list[dict[str, Any]]] = {arm: [] for arm in WEST_ARMS}
    verification_reps: list[dict[str, Any]] = []
    for rep in range(random_repeats):
        train_full, test_full = split_random(frame, seed=1000 + rep)
        verification: dict[str, Any] = {}
        # reuse _score_split machinery per rep via a scratch results dict
        scratch: dict[str, Any] = {"arms": {arm: {"splits": {}} for arm in WEST_ARMS}}
        _score_split(
            scratch, verification, "rep", train_full, test_full, seed=1000 + rep
        )
        for arm in WEST_ARMS:
            per_rep[arm].append(scratch["arms"][arm]["splits"]["rep"])
        verification_reps.append(verification["rep"])
        flush()
        print(f"[west] randomised rep {rep} done", flush=True)
    for arm in WEST_ARMS:
        results["arms"][arm]["splits"]["random60_40"] = {
            "repeats": random_repeats,
            "westernTestNRep0": per_rep[arm][0]["testN"],
            "aggregated": _aggregate_randomised(per_rep[arm]),
        }
    pooled_full["random60_40MaeMean"] = {
        model: round(
            float(np.mean([rep[model]["mae"] for rep in verification_reps])), 4
        )
        for model in WEST_MODELS
    }

    # --- Western LORO means + verdicts. ---
    for arm in WEST_ARMS:
        loro = results["arms"][arm]["splits"]["leaveOneRegionOutWest"]
        results["arms"][arm]["westernLoro"] = {
            model: {
                "byRegion": {
                    region: loro[region]["models"][model]["overall"]["mae"]
                    for region in WEST_REGIONS
                },
                "mean": round(
                    float(
                        np.mean(
                            [
                                loro[region]["models"][model]["overall"]["mae"]
                                for region in WEST_REGIONS
                            ]
                        )
                    ),
                    4,
                ),
            }
            for model in WEST_MODELS
        }

    def judge_block(arm: str, model: str) -> dict[str, Any]:
        return {
            "loroMean": results["arms"][arm]["westernLoro"][model]["mean"],
            "loroByRegion": results["arms"][arm]["westernLoro"][model]["byRegion"],
            "randomised": results["arms"][arm]["splits"]["random60_40"]["aggregated"][model]["maeMean"],
        }

    results["verdicts"] = {
        model: {
            arm: judge_west_arm(judge_block("pooled", model), judge_block(arm, model))
            for arm in ("west_only", "weighted")
        }
        for model in WEST_MODELS
    }

    # --- Verification against stored v2 pooled results. ---
    computed = {
        "random60_40MaeMean": pooled_full["random60_40MaeMean"],
        "temporalWY2026": pooled_full["temporalWY2026"],
        "unseenStations": pooled_full["unseenStations"],
        "leaveOneRegionOutWest": pooled_full["leaveOneRegionOutWest"],
    }
    results["verification"] = _verification_block(
        output_dir / "results.json", output_dir / "results_variants.json", computed
    )

    results["complete"] = True
    flush()
    return results


# ---------------------------------------------------------------------------
# Markdown summary
# ---------------------------------------------------------------------------


def _f(value: Any) -> str:
    return "—" if value is None else f"{float(value):.3f}"


def _d(value: Any) -> str:
    return "—" if value is None else f"{float(value):+.3f}"


def write_west_report(results: dict[str, Any], path: Path) -> None:
    lines: list[str] = []
    lines.append("## West-only experiment — does eastern data help or hurt the West?")
    lines.append("")
    if results.get("profileSource") == ERA5_PROFILE_SOURCE:
        lines.append(
            "Profile source: `era5-nsf-ncar-aws` (NSF NCAR ERA5), identical features "
            "and Kuchera baselines to the era5-expanded-v2 benchmark this experiment "
            "extends. All arms use the same features; only the training rows (and, "
            "for the weighted arm, base-learner sample weights) differ."
        )
    else:
        lines.append(f"Profile source: `{results.get('profileSource')}`.")
    lines.append("")
    lines.append(
        "Arms: **pooled** (control, all 57,376 rows) · **west_only** (16,486 western "
        "rows only) · **weighted** (pooled, eastern rows at sample_weight 0.25 in the "
        "RF/GBM base learners; EB residual layers fitted unweighted on each arm's "
        "training fold, the standard harness convention — the EB trainer consumes "
        "qc_weight, fixed at 1.0 for gold rows). Models: RF+EB and ENS-EB (the "
        "champion: mean of RF+EB and GBM+EB). Folds are the v2 pooled folds (same "
        "split functions and seeds); every arm is scored on the SAME western test "
        "events — the western subset of each pooled test fold, or the held western "
        "region for LORO — so comparisons are event-paired."
    )
    lines.append("")

    ver = results.get("verification", {})
    matches = ver.get("matches", {})
    lines.append("### Fold/model identity verification (pooled arm, full folds vs stored v2)")
    lines.append("")
    if matches:
        for model in WEST_MODELS:
            m = matches.get(model, {})
            if not m.get("available"):
                lines.append(f"- {model}: stored v2 reference unavailable — not verified.")
                continue
            lines.append(
                f"- {model}: randomised maeMean match={m['random60_40MaeMean']}, "
                f"LORO-west match={m['leaveOneRegionOutWest']}, "
                f"temporal match={m['temporalWY2026']}, "
                f"unseen-station match={m['unseenStations']} "
                "(tolerance 0.0002 on MAE; computed values in west_experiment.json)."
            )
    lines.append("")

    lines.append("### Western headline scores by arm")
    lines.append("")
    lines.append("| Arm | Model | randomised (west) | LORO mean (west) | temporal (west) | unseen-station (west) | rep0 tail>20 | rep0 tail<5 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for arm in WEST_ARMS:
        splits = results["arms"][arm]["splits"]
        for model in WEST_MODELS:
            agg = splits["random60_40"]["aggregated"][model]
            lines.append(
                f"| {arm} | {model} | {_f(agg['maeMean'])} ± {agg['maeStd']:.3f} | "
                f"{_f(results['arms'][arm]['westernLoro'][model]['mean'])} | "
                f"{_f(splits['temporalWY2026']['models'][model]['overall']['mae'])} | "
                f"{_f(splits['unseenStations']['models'][model]['overall']['mae'])} | "
                f"{_f(agg['rep0']['tailHigh_slr_gt_20']['mae'])} (n={agg['rep0']['tailHigh_slr_gt_20']['n']}) | "
                f"{_f(agg['rep0']['tailLow_slr_lt_5']['mae'])} (n={agg['rep0']['tailLow_slr_lt_5']['n']}) |"
            )
    lines.append("")

    lines.append("### Western LORO by region")
    lines.append("")
    lines.append("| Arm | Model | " + " | ".join(WEST_REGIONS) + " | mean |")
    lines.append("|---|---|" + "---:|" * (len(WEST_REGIONS) + 1))
    for arm in WEST_ARMS:
        for model in WEST_MODELS:
            by_region = results["arms"][arm]["westernLoro"][model]["byRegion"]
            lines.append(
                f"| {arm} | {model} | "
                + " | ".join(_f(by_region[region]) for region in WEST_REGIONS)
                + f" | {_f(results['arms'][arm]['westernLoro'][model]['mean'])} |"
            )
    lines.append("")

    lines.append("### Verdicts (pre-declared rule)")
    lines.append("")
    lines.append(
        "An arm helps iff western LORO mean improves >= 0.03 vs pooled, no western "
        "region's LORO regresses > 0.10, and western randomised MAE does not regress "
        "> 0.05. ENS-EB is the primary read; RF+EB secondary. Deltas are pooled − arm "
        "(positive = improvement)."
    )
    lines.append("")
    for model in WEST_MODELS:
        for arm in ("west_only", "weighted"):
            v = results["verdicts"][model][arm]
            lines.append(
                f"- **{model} / {arm}: {'HELPS' if v['helps'] else 'does not help'}** — "
                f"LORO mean delta {_d(v['loroMeanDelta'])}, worst region delta "
                f"{_d(v['worstRegionDelta'])}, randomised delta {_d(v['randomisedDelta'])}."
            )
    lines.append("")
    lines.append(
        "Full machine-readable results (every arm × model × split × region, plus the "
        "verification block): `west_experiment.json` beside this report."
    )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
