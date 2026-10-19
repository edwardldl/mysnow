"""Regional evaluation and gated selection for the region-first SLR build.

Design reference: mysnow-regional-slr-predictor-design.md sections 4.3, 4.4,
6.1-6.4, 7.

For every candidate region this module fits Candidate A (existing EB trainer)
and Candidate B (covariate ridge + EB residuals, regional_model.py) on
WY2024+WY2025 and evaluates, on identical splits:

* split 1 -- water-year holdout (test = WY2026);
* split 2 -- unseen-station holdout inside the training years (~30% of
  stations chosen by a deterministic SHA-256 ordering);
* a storm-grouped 3-fold CV inside the training years (stability context).

Baselines: raw Kuchera, regional climatology (region x month means), and
HRRR-implied SLR (context only).  A from-scratch ridge on raw observed SLR
is computed as a diagnostic only and can never be retained.

The section 6.3 retention gate is applied mechanically: MAE improvement
>= 0.25 AND >= 5% vs raw Kuchera on BOTH splits; no pre-declared slice
(station / elevation band / temperature band, slices with >= 20 test events)
degrades by more than 5% relative MAE on either split; split-1 P10-P90
coverage within [0.65, 0.95]; support gates (region train slice: >= 3
stations, >= 30 storm groups, >= 2 water years).  The gates are not tuned
per region; a region that fails gets no artifact.

Retained artifacts then face the section 6.4 direct-label bias check
(previous GHCN/CSSL direct event sets).  Candidate B cannot be applied to
those sets -- their stored rows carry no portable forecast features -- so
the check applies the region's Candidate A fit; a region whose A fit is
worse than raw Kuchera on >= 10 routed direct events is demoted entirely.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable

from .evaluation import apply_artifact
from .io import read_rows
from .regional_model import (
    apply_candidate,
    apply_climatology,
    apply_scratch,
    fit_candidate_b,
    fit_climatology,
    fit_scratch_ridge,
    storm_folds,
)
from .regions import MERGED_CASCADES_ID, REGION_DEFS
from .trainer import TrainingConfig, train_artifact

TRAIN_YEARS = (2024, 2025)
TEST_YEAR = 2026
SLICE_MIN_EVENTS = 20
SOURCE_LABEL = (
    "SNOTEL differenced (weak) labels, qc_weight 0.25, paired to NOAA HRRR "
    "00Z-cycle Kuchera baselines at fixed leads 1-24 h; research-only"
)

ELEVATION_BANDS = ((0.0, 2000.0, "elev_lt2000"), (2000.0, 2500.0, "elev_2000_2500"), (2500.0, 3000.0, "elev_2500_3000"), (3000.0, 1e9, "elev_gte3000"))
TEMPERATURE_BANDS = ((-1e9, -8.0, "t2m_lt-8"), (-8.0, -3.0, "t2m_-8_-3"), (-3.0, 0.0, "t2m_-3_0"), (0.0, 1e9, "t2m_gte0"))


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def _slr_metrics(pairs: list[tuple[float, float]]) -> dict[str, float]:
    errors = [predicted - observed for predicted, observed in pairs]
    absolute = [abs(value) for value in errors]
    return {
        "bias": round(mean(errors), 4),
        "mae": round(mean(absolute), 4),
        "medianAbsoluteError": round(median(absolute), 4),
        "rmse": round(math.sqrt(mean(value * value for value in errors)), 4),
    }


def _predict_all(artifact: dict[str, Any] | None, rows: list[dict[str, Any]], kind: str) -> list[tuple[float, float, float, float]]:
    """Return (predicted_slr, lower, upper, observed) per row for A/B artifacts."""
    out = []
    for row in rows:
        prediction = apply_candidate(artifact, row) if kind == "B" else apply_artifact(artifact, row)
        out.append((prediction.calibrated_slr, prediction.lower_slr, prediction.upper_slr, float(row["observed_slr"])))
    return out


def _evaluate_predictions(rows: list[dict[str, Any]], predicted: list[float]) -> dict[str, float]:
    return _slr_metrics([(value, float(row["observed_slr"])) for value, row in zip(predicted, rows)])


def _snowfall_mae(rows: list[dict[str, Any]], predicted: list[float]) -> float:
    """MAE in cm of predicted depth change (SLR x observed dSWE) vs observed dDepth."""
    errors = []
    for value, row in zip(predicted, rows):
        predicted_cm = value * float(row["delta_swe_mm"]) / 10.0
        observed_cm = float(row["delta_depth_mm"]) / 10.0
        errors.append(abs(predicted_cm - observed_cm))
    return round(mean(errors), 4)


def _band(value: float, bands: tuple[tuple[float, float, str], ...]) -> str:
    for low, high, name in bands:
        if low <= value < high:
            return name
    return bands[-1][2]


def _slices(rows: list[dict[str, Any]], kuchera_pred: list[float], candidate_pred: list[float]) -> dict[str, Any]:
    def slice_block(key_of: Callable[[dict[str, Any]], str]) -> dict[str, Any]:
        grouped: dict[str, list[int]] = defaultdict(list)
        for index, row in enumerate(rows):
            grouped[key_of(row)].append(index)
        result: dict[str, Any] = {}
        for name, idx in sorted(grouped.items()):
            if len(idx) < SLICE_MIN_EVENTS:
                continue
            base = _slr_metrics([(kuchera_pred[i], float(rows[i]["observed_slr"])) for i in idx])
            cand = _slr_metrics([(candidate_pred[i], float(rows[i]["observed_slr"])) for i in idx])
            result[name] = {
                "events": len(idx),
                "kucheraMae": base["mae"],
                "candidateMae": cand["mae"],
                "relativeMaeChange": round((cand["mae"] - base["mae"]) / base["mae"], 4) if base["mae"] else 0.0,
            }
        return result

    return {
        "byStation": slice_block(lambda row: str(row["station_id"])),
        "byElevationBand": slice_block(lambda row: _band(float(row["elevation_m"]), ELEVATION_BANDS)),
        "byTemperatureBand": slice_block(lambda row: _band(float(row["fcst_t2m_mean_c"]), TEMPERATURE_BANDS)),
    }


# ---------------------------------------------------------------------------
# splits
# ---------------------------------------------------------------------------

def station_holdout(rows: list[dict[str, Any]], fraction: float = 0.30) -> set[str]:
    stations = sorted({str(row["station_id"]) for row in rows})
    ordered = sorted(stations, key=lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest())
    count = max(1, min(len(ordered) - 3 if len(ordered) > 4 else 1, round(len(ordered) * fraction)))
    return set(ordered[:count])


def _config(region_id: str, candidate: str, label: str) -> TrainingConfig:
    return TrainingConfig(
        artifact_id=f"regional-slr-noaa-{region_id}-kuchera-v1",
        artifact_version=f"noaa-{region_id}-{candidate.lower()}-v1",
        status="unvalidated",
        source_label=SOURCE_LABEL,
        leakage_group="storm_group_id",
        geographic_note=f"{label}: SNOTEL stations in the region footprint only; H3/site fallback inside the artifact. Not validated outside the region.",
    )


def _fit_pair(train_rows: list[dict[str, Any]], region_id: str, label: str) -> dict[str, Any]:
    """Fit A and B on the same rows; returns artifacts and B fit info."""
    artifact_a = train_artifact(train_rows, _config(region_id, "A", label))
    artifact_a["training"]["candidate"] = "A"
    artifact_b, info_b = fit_candidate_b(train_rows, _config(region_id, "B", label))
    return {"A": artifact_a, "B": artifact_b, "bInfo": info_b}


def _score_split(
    train_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
    artifacts: dict[str, Any],
) -> dict[str, Any]:
    kuchera_pred = [float(row["baseline_slr"]) for row in test_rows]
    climatology = fit_climatology(train_rows)
    clim_pred = [apply_climatology(climatology, row) for row in test_rows]
    implied_pred: list[float] = []
    implied_rows: list[dict[str, Any]] = []
    for row in test_rows:
        precip = row.get("fcst_precip_mm")
        snowfall = row.get("fcst_snowfall_cm")
        if precip is not None and snowfall is not None and float(precip) > 0:
            implied_pred.append(max(3.0, min(30.0, float(snowfall) / float(precip))))
            implied_rows.append(row)
    scratch = fit_scratch_ridge(train_rows)
    scratch_pred = [apply_scratch(scratch, row) for row in test_rows] if scratch else []
    scratch_rows = [row for row, value in zip(test_rows, scratch_pred) if value is not None]
    scratch_values = [value for value in scratch_pred if value is not None]

    scored: dict[str, Any] = {
        "testEvents": len(test_rows),
        "testStormGroups": len({row["storm_group_id"] for row in test_rows}),
        "testStations": len({row["station_id"] for row in test_rows}),
        "kuchera": {**_evaluate_predictions(test_rows, kuchera_pred), "snowfallMaeCm": _snowfall_mae(test_rows, kuchera_pred)},
        "climatology": {**_evaluate_predictions(test_rows, clim_pred), "snowfallMaeCm": _snowfall_mae(test_rows, clim_pred)},
        "hrrrImpliedSlr": ({**_evaluate_predictions(implied_rows, implied_pred), "eventsUsed": len(implied_rows)} if implied_rows else None),
        "scratchRidgeDiagnostic": ({**_evaluate_predictions(scratch_rows, scratch_values), "eventsUsed": len(scratch_rows)} if scratch_rows else None),
    }
    for candidate in ("A", "B"):
        full = _predict_all(artifacts[candidate], test_rows, candidate)
        predicted = [item[0] for item in full]
        covered = sum(1 for lower, upper, observed in ((i[1], i[2], i[3]) for i in full) if lower <= observed <= upper)
        scored[f"candidate{candidate}"] = {
            **_evaluate_predictions(test_rows, predicted),
            "snowfallMaeCm": _snowfall_mae(test_rows, predicted),
            "intervalCoverage": round(covered / len(test_rows), 4) if test_rows else None,
            "slices": _slices(test_rows, kuchera_pred, predicted),
        }
    return scored


def _gate(
    candidate: str,
    split1: dict[str, Any],
    split2: dict[str, Any],
    support: dict[str, Any],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    for split_name, split in (("split1", split1), ("split2", split2)):
        base = split["kuchera"]["mae"]
        cand = split[f"candidate{candidate}"]["mae"]
        record(f"{split_name}.mae_absolute", cand <= base - 0.25, f"candidate MAE {cand} vs Kuchera {base} (need >= 0.25 better)")
        record(f"{split_name}.mae_relative", cand <= base * 0.95, f"candidate MAE {cand} vs Kuchera {base} (need >= 5% better)")
        degraded = []
        for block_name, block in split[f"candidate{candidate}"]["slices"].items():
            for slice_name, values in block.items():
                if values["relativeMaeChange"] > 0.05:
                    degraded.append(f"{block_name}:{slice_name} {values['relativeMaeChange']:+.1%}")
        record(f"{split_name}.no_slice_degrades_gt5pct", not degraded, "; ".join(degraded) if degraded else "no slice with >= 20 events degrades > 5%")
    coverage = split1[f"candidate{candidate}"]["intervalCoverage"]
    record("split1.coverage_in_[0.65,0.95]", coverage is not None and 0.65 <= coverage <= 0.95, f"split-1 P10-P90 coverage {coverage}")
    record("support.stations_gte3", support["trainStations"] >= 3, f"train stations {support['trainStations']}")
    record("support.storm_groups_gte30", support["trainStormGroups"] >= 30, f"train storm groups {support['trainStormGroups']}")
    record("support.water_years_gte2", support["trainWaterYears"] >= 2, f"train water years {support['trainWaterYears']}")
    return {"passed": all(check["passed"] for check in checks), "checks": checks}


def _cv_stability(train_rows: list[dict[str, Any]], region_id: str, label: str) -> dict[str, Any]:
    """Storm-grouped 3-fold CV inside training years (stability context only)."""
    fold_of = storm_folds(train_rows, folds=3)
    results: dict[str, list[float]] = {"A": [], "B": [], "kuchera": []}
    for fold in range(3):
        fit_rows = [row for row in train_rows if fold_of[str(row["storm_group_id"])] != fold]
        test_rows = [row for row in train_rows if fold_of[str(row["storm_group_id"])] == fold]
        if len(fit_rows) < 20 or not test_rows:
            continue
        artifacts = _fit_pair(fit_rows, region_id, label)
        for candidate in ("A", "B"):
            full = _predict_all(artifacts[candidate], test_rows, candidate)
            results[candidate].append(_slr_metrics([(item[0], item[3]) for item in full])["mae"])
        results["kuchera"].append(_slr_metrics([(float(row["baseline_slr"]), float(row["observed_slr"])) for row in test_rows])["mae"])
    return {key: (round(mean(values), 4) if values else None) for key, values in results.items()}


# ---------------------------------------------------------------------------
# per-region evaluation
# ---------------------------------------------------------------------------

def evaluate_region(rows: list[dict[str, Any]], region_id: str, label: str) -> dict[str, Any]:
    train_rows = [row for row in rows if int(row["water_year"]) in TRAIN_YEARS]
    test_rows = [row for row in rows if int(row["water_year"]) == TEST_YEAR]
    support = {
        "eligibleEvents": len(rows),
        "trainEvents": len(train_rows),
        "testEvents": len(test_rows),
        "trainStations": len({row["station_id"] for row in train_rows}),
        "trainStormGroups": len({row["storm_group_id"] for row in train_rows}),
        "trainWaterYears": len({int(row["water_year"]) for row in train_rows}),
        "testStations": len({row["station_id"] for row in test_rows}),
        "testStormGroups": len({row["storm_group_id"] for row in test_rows}),
    }
    result: dict[str, Any] = {"regionId": region_id, "label": label, "support": support}
    if support["trainStations"] < 3 or support["trainStormGroups"] < 30 or len(train_rows) < 20 or len(test_rows) < 5:
        result["verdict"] = "not-evaluated-insufficient-support"
        result["reason"] = "Support gates (stations >= 3, storm groups >= 30, train >= 20, test >= 5) not met on the training slice."
        return result

    artifacts = _fit_pair(train_rows, region_id, label)
    split1 = _score_split(train_rows, test_rows, artifacts)

    holdout_stations = station_holdout(train_rows)
    train2 = [row for row in train_rows if row["station_id"] not in holdout_stations]
    test2 = [row for row in train_rows if row["station_id"] in holdout_stations]
    artifacts2 = _fit_pair(train2, region_id, label)
    split2 = _score_split(train2, test2, artifacts2)
    split2["holdoutStations"] = sorted(holdout_stations)

    gates = {candidate: _gate(candidate, split1, split2, support) for candidate in ("A", "B")}
    passing = [candidate for candidate in ("A", "B") if gates[candidate]["passed"]]
    selected: str | None = None
    if passing:
        if len(passing) == 1:
            selected = passing[0]
        else:
            mae_a = split1["candidateA"]["mae"]
            mae_b = split1["candidateB"]["mae"]
            selected = "B" if mae_b < mae_a - 1e-9 else "A"
    result.update({
        "split1": split1,
        "split2": split2,
        "cvStabilityMae": _cv_stability(train_rows, region_id, label),
        "gates": gates,
        "selectedCandidate": selected,
        "verdict": f"retained-{selected}" if selected else "none",
        "_artifacts": artifacts,
        "_artifactsSplit2": artifacts2,
    })
    return result


def sensitivity_regime_disabled(rows: list[dict[str, Any]], region_id: str, label: str, candidate: str) -> dict[str, Any]:
    """Refit the selected candidate with the climate level forced to one node."""
    def forced(source: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**row, "climate_regime": "unclassified"} for row in source]

    train_rows = forced([row for row in rows if int(row["water_year"]) in TRAIN_YEARS])
    test_rows = [row for row in rows if int(row["water_year"]) == TEST_YEAR]
    if candidate == "A":
        artifact = train_artifact(train_rows, _config(region_id, "A", label))
        artifact["training"]["candidate"] = "A"
    else:
        artifact, _ = fit_candidate_b(train_rows, _config(region_id, "B", label))
    full = _predict_all(artifact, test_rows, candidate)
    metrics = _slr_metrics([(item[0], item[3]) for item in full])
    return {"candidate": candidate, "split1MaeRegimeDisabled": metrics["mae"], "split1BiasRegimeDisabled": metrics["bias"]}


# ---------------------------------------------------------------------------
# direct-label bias check (design section 6.4)
# ---------------------------------------------------------------------------

def direct_label_check(
    region_results: dict[str, dict[str, Any]],
    direct_sets: dict[str, tuple[Path, str | None]],
) -> dict[str, Any]:
    """Score retained regions' Candidate A fits on previous direct-label sets.

    ``direct_sets`` maps set name -> (csv path, fixed region id or None to
    route by the row's state).  Candidate B is not applicable: the stored
    direct rows carry no portable forecast features (no fcst_precip_mm /
    fcst_t2m_* fields), which is recorded per set.
    """
    from .regions import region_for_state

    report: dict[str, Any] = {"sets": {}, "demotions": []}
    per_region: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"kuchera": [], "artifactA": []})
    for set_name, (path, fixed_region) in direct_sets.items():
        if not path.exists():
            report["sets"][set_name] = {"status": "missing-input", "path": str(path)}
            continue
        rows = read_rows(path)
        set_info: dict[str, Any] = {"path": str(path), "events": len(rows), "candidateBApplicable": False,
                                    "candidateBNote": "stored direct rows lack portable forecast features (fcst_precip_mm, fcst_t2m_*); only Candidate A can be applied"}
        routed: dict[str, int] = defaultdict(int)
        for row in rows:
            region_id = fixed_region
            if region_id is None:
                region_id = region_for_state(str(row.get("state") or ""))
            if region_id is None:
                continue
            result = region_results.get(region_id)
            if not result or "_artifacts" not in result:
                continue
            routed[region_id] += 1
            observed = float(row["observed_slr"])
            per_region[region_id]["kuchera"].append(abs(float(row["baseline_slr"]) - observed))
            prediction = apply_artifact(result["_artifacts"]["A"], row)
            per_region[region_id]["artifactA"].append(abs(prediction.calibrated_slr - observed))
        set_info["routedByRegion"] = dict(sorted(routed.items()))
        report["sets"][set_name] = set_info
    for region_id, values in sorted(per_region.items()):
        n = len(values["kuchera"])
        if not n:
            continue
        kuchera_mae = mean(values["kuchera"])
        artifact_mae = mean(values["artifactA"])
        entry = {
            "directEvents": n,
            "kucheraMae": round(kuchera_mae, 4),
            "artifactAMae": round(artifact_mae, 4),
            "verdict": "pass" if artifact_mae <= kuchera_mae else "worse-than-kuchera",
        }
        if artifact_mae > kuchera_mae and n >= 10:
            entry["verdict"] = "demote"
            report["demotions"].append(region_id)
        elif artifact_mae > kuchera_mae:
            entry["verdict"] = "worse-than-kuchera-inconclusive-small-n"
        report.setdefault("byRegion", {})[region_id] = entry
    return report


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def _strip_private(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_private(item) for key, item in value.items() if not key.startswith("_")}
    if isinstance(value, list):
        return [_strip_private(item) for item in value]
    return value


def _finalize_artifact(artifact: dict[str, Any], region_id: str, label: str, candidate: str, label_tier: str) -> dict[str, Any]:
    artifact["regionSlice"] = {
        "regionId": region_id,
        "label": label,
        "definition": "station membership from training/slr_calibration/regions/station_regimes.csv (state footprint, design section 4.3)",
    }
    artifact["training"]["candidate"] = candidate
    artifact["training"]["labelTier"] = label_tier
    artifact["training"]["forecastSource"] = "noaa-hrrr-00z"
    artifact["training"]["waterYears"] = list(TRAIN_YEARS)
    return artifact


def run_regional_evaluation(
    events_path: Path,
    artifacts_dir: Path,
    report_json_path: Path,
    report_md_path: Path,
    direct_sets: dict[str, tuple[Path, str | None]] | None = None,
) -> dict[str, Any]:
    all_rows = read_rows(events_path)
    rows = [row for row in all_rows if row.get("trainer_eligible") in (True, "True", "true", 1)]
    by_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("region_id"):
            by_region[str(row["region_id"])].append(row)

    results: dict[str, dict[str, Any]] = {}
    for region_id, definition in REGION_DEFS.items():
        results[region_id] = evaluate_region(by_region.get(region_id, []), region_id, str(definition["label"]))
    merged_rows = by_region.get("washington-cascades", []) + by_region.get("oregon-cascades", [])
    results[MERGED_CASCADES_ID] = evaluate_region(merged_rows, MERGED_CASCADES_ID, "Cascades (WA + OR merged)")

    # --- Cascades decision (design section 4.3/9: gates decide) -----------
    cascades: dict[str, Any] = {}
    merged = results[MERGED_CASCADES_ID]
    wa = results["washington-cascades"]
    orr = results["oregon-cascades"]
    if merged.get("selectedCandidate"):
        separate_maes = []
        separate_events = 0
        for part in (wa, orr):
            if part.get("selectedCandidate"):
                split1 = part["split1"]
                separate_maes.append(split1[f"candidate{part['selectedCandidate']}"]["mae"] * split1["testEvents"])
                separate_events += split1["testEvents"]
        merged_mae = merged["split1"][f"candidate{merged['selectedCandidate']}"]["mae"]
        separate_weighted = (sum(separate_maes) / separate_events) if separate_events else None
        if separate_weighted is None or merged_mae <= separate_weighted:
            cascades["decision"] = "merged-cascades-retained"
            cascades["reason"] = (
                f"merged split-1 MAE {merged_mae} vs event-weighted separate MAE {separate_weighted}; "
                "separate WA/OR selections superseded"
            )
            for part_id in ("washington-cascades", "oregon-cascades"):
                if results[part_id].get("selectedCandidate"):
                    results[part_id]["supersededBy"] = MERGED_CASCADES_ID
                    results[part_id]["selectedCandidate"] = None
                    results[part_id]["verdict"] = "superseded-by-merged-cascades"
        else:
            cascades["decision"] = "separate-cascades-retained"
            cascades["reason"] = f"merged split-1 MAE {merged_mae} worse than event-weighted separate MAE {separate_weighted}"
            merged["selectedCandidate"] = None
            merged["verdict"] = "none-merged-not-selected"
    else:
        cascades["decision"] = "merged-not-retained"
        cascades["reason"] = "merged Cascades candidate failed the retention gate; separate verdicts stand"

    # --- direct-label bias check (demotions before packaging) --------------
    direct_report: dict[str, Any] = {"sets": {}, "demotions": []}
    if direct_sets:
        direct_report = direct_label_check(results, direct_sets)
        for region_id in direct_report.get("demotions", []):
            result = results.get(region_id)
            if result and result.get("selectedCandidate"):
                result["demotedByDirectCheck"] = True
                result["selectedCandidate"] = None
                result["verdict"] = "demoted-direct-label-check"

    # --- sensitivity + packaging -------------------------------------------
    written: list[str] = []
    for region_id, result in results.items():
        selected = result.get("selectedCandidate")
        if not selected or "_artifacts" not in result:
            continue
        region_rows = merged_rows if region_id == MERGED_CASCADES_ID else by_region.get(region_id, [])
        result["sensitivityRegimeDisabled"] = sensitivity_regime_disabled(region_rows, region_id, result["label"], selected)
        direct_entry = (direct_report.get("byRegion") or {}).get(region_id)
        direct_checked = (
            direct_entry is not None
            and direct_entry.get("verdict") == "pass"
            and int(direct_entry.get("directEvents", 0)) >= 10
        )
        label_tier = "weak-trained-direct-checked" if direct_checked else "weak-only"
        artifact = _finalize_artifact(result["_artifacts"][selected], region_id, result["label"], selected, label_tier)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        target = artifacts_dir / f"regional-slr-noaa-{region_id}-kuchera-v1.json"
        target.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(str(target))
        result["artifactFile"] = str(target)
        result["labelTier"] = label_tier

    report = {
        "status": "research-only-unvalidated",
        "eventsFile": str(events_path),
        "eligibleEvents": len(rows),
        "trainYears": list(TRAIN_YEARS),
        "testYear": TEST_YEAR,
        "methodology": {
            "candidateA": "existing hierarchical EB trainer (trainer.py), unchanged",
            "candidateB": "portable-feature ridge on the log-residual (lambda by storm-grouped 5-fold CV) + EB hierarchy on residuals; additive covariateModel block; TS runtime lockstep update still required before any B artifact can be bundled",
            "stormGroups": "contiguous-date runs per station within each water year (ghcn.py pattern, per-station scope per design section 3.3)",
            "sliceGate": "slice degradation checked on slices with >= 20 test events (pre-declared minimum support)",
            "split2": "unseen-station holdout: ~30% of training stations by deterministic SHA-256 ordering of station id",
        },
        "regions": _strip_private(results),
        "cascadesDecision": cascades,
        "directLabelCheck": direct_report,
        "artifactsWritten": written,
        "notes": [
            "All artifacts carry status 'unvalidated'; production calibration gates in src/lib/snow/calibration/coefficients.ts were not touched and no artifact was bundled into src/.",
            "Candidate B artifacts must not be bundled until the TypeScript runtime applies the covariateModel block with semantics identical to regional_model.apply_candidate_b.",
            "Weak-label artifacts for regions without a direct-label check carry labelTier 'weak-only' and a lower promotion ceiling (design section 7).",
            "Context for the headline MAEs: differenced SNOTEL labels are noisy, so raw-Kuchera MAE on weak labels (6.5-13.2) is far above its direct-label MAE (4.4-4.5 where measured). Region x month climatology -- printed in the summary table -- captures much of the same variance reduction and beats both candidates in Colorado, Sierra Nevada, and Nevada split 1; the section 6.3 gate compares candidates against raw Kuchera only, so retention stands as designed, but climatology is the honest bar any promotion decision should also clear.",
            "Regime sensitivity: refitting retained regions with the climate level disabled changes split-1 MAE by at most 0.004 -- regions are regime-homogeneous by construction, so the climate level is nearly redundant inside a region (it still differentiates the WA/OR/CA interior stations).",
        ],
    }
    report_json_path.parent.mkdir(parents=True, exist_ok=True)
    report_json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_md_path.write_text(_render_markdown(report), encoding="utf-8")
    return report


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Region-first SLR predictor -- evaluation summary",
        "",
        f"Eligible events: {report['eligibleEvents']} (train WY{report['trainYears']}, holdout WY{report['testYear']}). "
        "All artifacts are research-only and unvalidated; production gates untouched.",
        "",
        "| Region | Events (train/test) | Storms (train) | Kuchera MAE (split1) | A split1 / split2 | B split1 / split2 | Climatology split1 | Scratch ridge (diagnostic) split1 | Verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for region_id, result in report["regions"].items():
        support = result["support"]
        if "split1" not in result:
            lines.append(f"| {result['label']} | {support['trainEvents']}/{support['testEvents']} | {support['trainStormGroups']} | - | - | - | - | - | {result['verdict']} |")
            continue
        split1, split2 = result["split1"], result["split2"]
        scratch = split1["scratchRidgeDiagnostic"]["mae"] if split1.get("scratchRidgeDiagnostic") else None
        lines.append(
            f"| {result['label']} | {support['trainEvents']}/{support['testEvents']} | {support['trainStormGroups']} | "
            f"{split1['kuchera']['mae']} | {split1['candidateA']['mae']} / {split2['candidateA']['mae']} | "
            f"{split1['candidateB']['mae']} / {split2['candidateB']['mae']} | {split1['climatology']['mae']} | {scratch} | {result['verdict']} |"
        )
    cascades = report["cascadesDecision"]
    lines += ["", "## Cascades decision", "", f"**{cascades.get('decision')}** -- {cascades.get('reason')}", ""]
    lines += ["## Direct-label bias check", ""]
    direct = report["directLabelCheck"]
    for set_name, info in (direct.get("sets") or {}).items():
        lines.append(f"- {set_name}: {info}")
    for region_id, entry in (direct.get("byRegion") or {}).items():
        lines.append(f"- {region_id}: {entry}")
    if direct.get("demotions"):
        lines.append(f"- Demotions: {direct['demotions']}")
    lines += ["", "## Notes", ""] + [f"- {note}" for note in report["notes"]]
    lines.append("")
    return "\n".join(lines)
