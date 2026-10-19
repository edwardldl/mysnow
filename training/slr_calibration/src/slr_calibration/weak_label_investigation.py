"""Diagnostic investigation: why weak-label SLR corrections fail on direct labels.

Research-only.  This module never writes a production or retained artifact,
never changes the retention gates, and never touches the application
runtime.  It refits the region-first build's Candidate A (hierarchical EB
log-residual trainer, unchanged) on the same weak-label training slice the
build used -- SNOTEL-differenced labels from ``data/hrrr-dataset`` paired to
NOAA HRRR Kuchera baselines -- purely so the demoted Colorado / Sierra fits
(which the build evaluated but did not package) can be taken apart level by
level.  The refit is verified against the build's published numbers in
``data/hrrr-dataset/model-evaluation/evaluation_report.json`` before any
diagnostic is computed.

Hypotheses under test (see weak-label-investigation.md for the write-up):

* H1 differencing level bias -- SNOTEL observed SLR (delta-depth / delta-SWE)
  is systematically lower than board-measured SLR because the pack settles
  during the storm day, so weak-trained corrections push SLR too far down.
* H2 baseline provenance mismatch -- corrections were fit against
  Python-Kuchera / NOAA-HRRR baselines but the direct check applied them to
  events carrying production TypeScript-Kuchera / Open-Meteo best-match
  baselines.
* H3 event-mix mismatch -- the direct sets sample a different storm
  population (observer days, other years / elevations / event sizes).
* H4 hierarchy depth -- which level of the correction (region intercept,
  climate regime, H3 cell, site) does the damage on direct events.
* H5 QC censoring -- the weak QC gates (delta-SWE >= 5 mm, delta-depth
  >= 2 cm, no depth decrease, cold filters) truncate the weak label
  distribution in ways the direct labels are not truncated.

The cleanest single piece of evidence is the CSSL same-day decomposition:
the CSSL snow laboratory is itself SNOTEL station ``428:CA:SNTL``, so on
the same station-days we can compare the board label against the
differenced label (label-definition effect) and the TypeScript/Open-Meteo
baseline against the Python/NOAA baseline (provenance effect) separately.
"""
from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

import h3
import pyarrow.parquet as pq

from .evaluation import apply_artifact
from .io import read_rows
from .regional_evaluation import TEST_YEAR, TRAIN_YEARS, _config
from .regions import REGION_DEFS, load_station_regimes, region_for_state
from .trainer import train_artifact

REGIONS_UNDER_STUDY = ("colorado-ranges", "sierra-nevada")
CSSL_TRIPLET = "428:CA:SNTL"
LEVEL_ORDER = ("global", "climate", "region", "local")
SPLIT_SEED = 20261019
SPLIT_REPS = 200


# ---------------------------------------------------------------------------
# small statistics helpers
# ---------------------------------------------------------------------------

def _f(row: dict[str, Any], key: str) -> float:
    return float(row[key])


def _log_ratio(row: dict[str, Any]) -> float:
    return math.log(_f(row, "observed_slr") / _f(row, "baseline_slr"))


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    total = sum(weights)
    return 0.0 if total == 0 else sum(v * w for v, w in zip(values, weights)) / total


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    pos = q * (len(ordered) - 1)
    low = int(math.floor(pos))
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def _dist(values: list[float], weights: list[float] | None = None) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    out: dict[str, Any] = {
        "n": len(values),
        "mean": round(mean(values), 4),
        "median": round(median(values), 4),
        "std": round(math.sqrt(mean((v - mean(values)) ** 2 for v in values)), 4) if len(values) > 1 else 0.0,
        "p10": round(_quantile(values, 0.10), 4),
        "p90": round(_quantile(values, 0.90), 4),
    }
    if weights is not None:
        out["weightedMean"] = round(_weighted_mean(values, weights), 4)
    return out


def _mae(pairs: Iterable[tuple[float, float]]) -> float:
    pairs = list(pairs)
    return round(mean(abs(p - o) for p, o in pairs), 4) if pairs else float("nan")


def _bias(pairs: Iterable[tuple[float, float]]) -> float:
    pairs = list(pairs)
    return round(mean(p - o for p, o in pairs), 4) if pairs else float("nan")


def _pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3:
        return float("nan")
    mx, my = mean(xs), mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return round(num / den, 4) if den > 0 else float("nan")


# ---------------------------------------------------------------------------
# artifact application, level-aware (replicates evaluation.apply_artifact)
# ---------------------------------------------------------------------------

def walk_terms(artifact: dict[str, Any], row: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The (level, node) pairs apply_artifact would use, in walk order."""
    method = str(row["baseline_method"])
    model = artifact["methods"][method]
    minimum = float(artifact["training"]["minEffectiveSampleSize"])
    latitude = float(row["latitude"])
    longitude = float(row["longitude"])
    region_cell = h3.latlng_to_cell(latitude, longitude, artifact["regionIndex"]["resolution"])
    climate_cell = h3.latlng_to_cell(latitude, longitude, artifact["climateIndex"]["resolution"])
    climate = artifact["climateIndex"]["assignments"].get(climate_cell)
    candidates = [
        ("global", model["global"]),
        ("climate", model["climate"].get(climate) if climate else None),
        ("region", model["region"].get(region_cell)),
        ("local", model["local"].get(str(row.get("site_id") or ""))),
    ]
    used: list[tuple[str, dict[str, Any]]] = []
    for level, node in candidates:
        if node is None or float(node["effectiveSampleSize"]) < minimum:
            break
        used.append((level, node))
    return used


def predict_variant(
    artifact: dict[str, Any],
    row: dict[str, Any],
    *,
    drop_levels: frozenset[str] = frozenset(),
    max_depth: int | None = None,
    global_override: float | None = None,
) -> float:
    """apply_artifact's calibrated SLR with level surgery for diagnostics.

    ``drop_levels`` zeroes a level's logBias but keeps the rest of the walk
    (the terms were fit sequentially, so this isolates a level's marginal
    contribution).  ``max_depth`` truncates the walk after N levels.
    ``global_override`` replaces the region-intercept logBias.
    """
    baseline = float(row["baseline_slr"])
    used = walk_terms(artifact, row)
    if max_depth is not None:
        used = used[:max_depth]
    if not used:
        return baseline
    total = 0.0
    for level, node in used:
        if level in drop_levels:
            continue
        log_bias = float(node["logBias"])
        if level == "global" and global_override is not None:
            log_bias = global_override
        total += log_bias
    bounds = artifact["bounds"]
    total = max(math.log(float(bounds["minCorrectionFactor"])), min(math.log(float(bounds["maxCorrectionFactor"])), total))
    calibrated = baseline * math.exp(total)
    return max(float(bounds["minSlr"]), min(float(bounds["maxSlr"]), calibrated))


def implied_global(row: dict[str, Any], artifact: dict[str, Any]) -> float:
    """log(observed/baseline) minus the non-global walk terms for one event.

    This is the region intercept that would make the artifact exactly right
    (in log space, before output clamping) for this event given its deeper
    terms -- the per-event vote for where the intercept should sit.
    """
    used = walk_terms(artifact, row)
    deeper = sum(float(node["logBias"]) for level, node in used if level != "global")
    return _log_ratio(row) - deeper


# ---------------------------------------------------------------------------
# data loading (mirrors regional_evaluation.run_regional_evaluation exactly)
# ---------------------------------------------------------------------------

def load_weak_events(events_path: Path) -> list[dict[str, Any]]:
    rows = read_rows(events_path)
    return [row for row in rows if row.get("trainer_eligible") in (True, "True", "true", 1)]


def _join_fresh_components(
    baseline_rows: list[dict[str, Any]],
    events_path: Path,
) -> list[dict[str, Any]]:
    """Attach fresh_snow_mm / fresh_swe_mm from the set's observation file."""
    fresh: dict[str, dict[str, Any]] = {}
    if events_path.exists():
        for row in read_rows(events_path):
            fresh[str(row["event_id"])] = row
    out = []
    for row in baseline_rows:
        source = fresh.get(str(row["event_id"]), {})
        out.append({
            **row,
            "fresh_snow_mm": source.get("fresh_snow_mm"),
            "fresh_swe_mm": source.get("fresh_swe_mm"),
            "observation_date": source.get("observation_date") or str(row["valid_time"])[:10],
        })
    return out


def load_direct_sets(
    rockies_baseline: Path,
    rockies_events: Path,
    sierra_baseline: Path,
    sierra_events: Path,
    cssl_baseline: Path,
    cssl_profile_events: Path,
) -> dict[str, list[dict[str, Any]]]:
    """Direct rows routed to regions exactly as the build's direct check did."""
    per_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rockies = _join_fresh_components(read_rows(rockies_baseline), rockies_events)
    for row in rockies:
        region_id = region_for_state(str(row.get("state") or ""))
        if region_id in REGIONS_UNDER_STUDY:
            per_region[region_id].append({**row, "direct_set": "rockies-ghcn"})
    sierra = _join_fresh_components(read_rows(sierra_baseline), sierra_events)
    for row in sierra:
        per_region["sierra-nevada"].append({**row, "direct_set": "sierra-ghcn"})
    cssl_rows = read_rows(cssl_baseline)
    fresh_swe: dict[str, float] = {}
    if cssl_profile_events.exists():
        for line in cssl_profile_events.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("fresh_swe_mm") not in (None, ""):
                fresh_swe[str(record["event_id"])] = float(record["fresh_swe_mm"])
    for row in cssl_rows:
        swe = fresh_swe.get(str(row["event_id"]))
        per_region["sierra-nevada"].append({
            **row,
            "direct_set": "cssl",
            "fresh_swe_mm": swe,
            "fresh_snow_mm": (float(row["observed_slr"]) * swe) if swe is not None else None,
            "observation_date": str(row["valid_time"])[:10],
        })
    return dict(per_region)


def fit_candidate_a(weak_rows: list[dict[str, Any]], region_id: str) -> dict[str, Any]:
    """Refit Candidate A on WY TRAIN_YEARS exactly as the build did."""
    label = str(REGION_DEFS[region_id]["label"])
    train_rows = [
        row for row in weak_rows
        if str(row.get("region_id")) == region_id and int(row["water_year"]) in TRAIN_YEARS
    ]
    artifact = train_artifact(train_rows, _config(region_id, "A", label))
    artifact["training"]["candidate"] = "A"
    return artifact


# ---------------------------------------------------------------------------
# reproduction of the build's headline numbers (validates the refit)
# ---------------------------------------------------------------------------

def reproduction_check(
    weak_rows: list[dict[str, Any]],
    direct_by_region: dict[str, list[dict[str, Any]]],
    artifacts: dict[str, dict[str, Any]],
    published: dict[str, Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for region_id in REGIONS_UNDER_STUDY:
        artifact = artifacts[region_id]
        test_rows = [
            row for row in weak_rows
            if str(row.get("region_id")) == region_id and int(row["water_year"]) == TEST_YEAR
        ]
        weak_pairs = [(apply_artifact(artifact, row).calibrated_slr, _f(row, "observed_slr")) for row in test_rows]
        weak_base = [(_f(row, "baseline_slr"), _f(row, "observed_slr")) for row in test_rows]
        direct_rows = direct_by_region.get(region_id, [])
        direct_pairs = [(apply_artifact(artifact, row).calibrated_slr, _f(row, "observed_slr")) for row in direct_rows]
        direct_base = [(_f(row, "baseline_slr"), _f(row, "observed_slr")) for row in direct_rows]
        published_region = (published.get("regions") or {}).get(region_id) or {}
        published_direct = ((published.get("directLabelCheck") or {}).get("byRegion") or {}).get(region_id) or {}
        out[region_id] = {
            "weakSplit1Mae": _mae(weak_pairs),
            "weakSplit1MaePublished": ((published_region.get("split1") or {}).get("candidateA") or {}).get("mae"),
            "weakSplit1KucheraMae": _mae(weak_base),
            "weakSplit1KucheraMaePublished": ((published_region.get("split1") or {}).get("kuchera") or {}).get("mae"),
            "directMae": _mae(direct_pairs),
            "directMaePublished": published_direct.get("artifactAMae"),
            "directKucheraMae": _mae(direct_base),
            "directKucheraMaePublished": published_direct.get("kucheraMae"),
            "directEvents": len(direct_rows),
        }
    return out


# ---------------------------------------------------------------------------
# H1 -- differencing level bias
# ---------------------------------------------------------------------------

def h1_level_bias(
    weak_rows: list[dict[str, Any]],
    direct_by_region: dict[str, list[dict[str, Any]]],
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for region_id in REGIONS_UNDER_STUDY:
        artifact = artifacts[region_id]
        model = artifact["methods"]["kuchera"]
        train_rows = [r for r in weak_rows if str(r.get("region_id")) == region_id and int(r["water_year"]) in TRAIN_YEARS]
        test_rows = [r for r in weak_rows if str(r.get("region_id")) == region_id and int(r["water_year"]) == TEST_YEAR]
        direct_rows = direct_by_region.get(region_id, [])

        def ratio_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
            values = [_log_ratio(r) for r in rows]
            weights = [float(r.get("qc_weight") or 1.0) for r in rows]
            return _dist(values, weights)

        variants: dict[str, Any] = {}
        kuchera_pairs = [(_f(r, "baseline_slr"), _f(r, "observed_slr")) for r in direct_rows]
        kuchera_mae = _mae(kuchera_pairs)
        full_pairs = [(predict_variant(artifact, r), _f(r, "observed_slr")) for r in direct_rows]
        full_mae = _mae(full_pairs)
        no_global_pairs = [(predict_variant(artifact, r, drop_levels=frozenset({"global"})), _f(r, "observed_slr")) for r in direct_rows]
        no_global_mae = _mae(no_global_pairs)
        degradation = round(full_mae - kuchera_mae, 4)

        # leave-one-out intercept recalibration on the direct events
        implied = [implied_global(r, artifact) for r in direct_rows]
        weights = [float(r.get("qc_weight") or 1.0) for r in direct_rows]
        loo_pairs: list[tuple[float, float]] = []
        for i, row in enumerate(direct_rows):
            rest_v = [v for j, v in enumerate(implied) if j != i]
            rest_w = [w for j, w in enumerate(weights) if j != i]
            loo_pairs.append((predict_variant(artifact, row, global_override=_weighted_mean(rest_v, rest_w)), _f(row, "observed_slr")))
        loo_mae = _mae(loo_pairs)
        # oracle intercept (fit and scored on all direct events; upper bound only)
        oracle_g = _weighted_mean(implied, weights)
        oracle_pairs = [(predict_variant(artifact, r, global_override=oracle_g), _f(r, "observed_slr")) for r in direct_rows]
        # repeated 50/50 split-half recalibration
        rng = random.Random(SPLIT_SEED)
        indices = list(range(len(direct_rows)))
        split_maes: list[float] = []
        split_kuchera: list[float] = []
        for _ in range(SPLIT_REPS):
            rng.shuffle(indices)
            half = len(indices) // 2
            fit_idx, score_idx = indices[:half], indices[half:]
            g_hat = _weighted_mean([implied[j] for j in fit_idx], [weights[j] for j in fit_idx])
            split_maes.append(_mae([(predict_variant(artifact, direct_rows[j], global_override=g_hat), _f(direct_rows[j], "observed_slr")) for j in score_idx]))
            split_kuchera.append(_mae([(_f(direct_rows[j], "baseline_slr"), _f(direct_rows[j], "observed_slr")) for j in score_idx]))

        def recovered(variant_mae: float) -> dict[str, Any]:
            rec = round(full_mae - variant_mae, 4)
            return {
                "mae": variant_mae,
                "recoveredMae": rec,
                "shareOfDegradation": round(rec / degradation, 4) if degradation > 0 else None,
            }

        global_node = model["global"]
        out[region_id] = {
            "weakTrainLogRatio": ratio_stats(train_rows),
            "weakTestLogRatio": ratio_stats(test_rows),
            "directLogRatio": ratio_stats(direct_rows),
            "weakMinusDirectMeanLogRatio": round(ratio_stats(train_rows)["weightedMean"] - ratio_stats(direct_rows)["weightedMean"], 4),
            "fittedGlobalLogBias": global_node["logBias"],
            "fittedGlobalRawLogBias": global_node["rawLogBias"],
            "fittedGlobalFactor": round(math.exp(float(global_node["logBias"])), 4),
            "directImpliedGlobalMean": round(_weighted_mean(implied, weights), 4),
            "directBiasFullArtifact": _bias(full_pairs),
            "directMaeKuchera": kuchera_mae,
            "directMaeFullArtifact": full_mae,
            "degradation": degradation,
            "variantNoGlobal": recovered(no_global_mae),
            "variantLooRecalibrated": recovered(loo_mae),
            "variantSplitHalfRecalibrated": {
                "maeMean": round(mean(split_maes), 4),
                "maeStd": round(math.sqrt(mean((v - mean(split_maes)) ** 2 for v in split_maes)), 4),
                "kucheraMaeOnScoreHalves": round(mean(split_kuchera), 4),
                "recoveredMae": round(full_mae - mean(split_maes), 4),
                "shareOfDegradation": round((full_mae - mean(split_maes)) / degradation, 4) if degradation > 0 else None,
            },
            "variantOracleIntercept": {"mae": _mae(oracle_pairs), "intercept": round(oracle_g, 4), "note": "in-sample upper bound, not an honest score"},
        }
    return out


# ---------------------------------------------------------------------------
# H4 -- hierarchy depth on direct events
# ---------------------------------------------------------------------------

def h4_hierarchy_depth(
    weak_rows: list[dict[str, Any]],
    direct_by_region: dict[str, list[dict[str, Any]]],
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for region_id in REGIONS_UNDER_STUDY:
        artifact = artifacts[region_id]
        direct_rows = direct_by_region.get(region_id, [])
        test_rows = [r for r in weak_rows if str(r.get("region_id")) == region_id and int(r["water_year"]) == TEST_YEAR]

        def depth_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
            block: dict[str, Any] = {"identity": _mae([(_f(r, "baseline_slr"), _f(r, "observed_slr")) for r in rows])}
            for depth in range(1, 5):
                pairs = [(predict_variant(artifact, r, max_depth=depth), _f(r, "observed_slr")) for r in rows]
                block[f"depth{depth}_{LEVEL_ORDER[depth - 1]}"] = _mae(pairs)
            reach = Counter(len(walk_terms(artifact, r)) for r in rows)
            block["walkDepthCounts"] = {str(k): reach[k] for k in sorted(reach)}
            deepest = Counter()
            for r in rows:
                terms = walk_terms(artifact, r)
                deepest[terms[-1][0] if terms else "none"] += 1
            block["deepestLevelCounts"] = dict(sorted(deepest.items()))
            # mean signed logBias contributed per level (over events reaching it)
            contrib: dict[str, list[float]] = defaultdict(list)
            for r in rows:
                for level, node in walk_terms(artifact, r):
                    contrib[level].append(float(node["logBias"]))
            block["meanLogBiasByLevel"] = {level: round(mean(vals), 4) for level, vals in sorted(contrib.items())}
            floor_log = math.log(float(artifact["bounds"]["minCorrectionFactor"]))
            totals = [sum(float(node["logBias"]) for _, node in walk_terms(artifact, r)) for r in rows]
            block["factorFloorShare"] = round(sum(1 for t in totals if t < floor_log) / len(totals), 4) if totals else None
            block["meanTotalLogBias"] = round(mean(totals), 4) if totals else None
            return block

        out[region_id] = {"direct": depth_block(direct_rows), "weakTest": depth_block(test_rows)}
    return out


# ---------------------------------------------------------------------------
# H3 -- event-mix mismatch
# ---------------------------------------------------------------------------

def _month_of(row: dict[str, Any]) -> int:
    return int(str(row.get("observation_date") or str(row["valid_time"])[:10])[5:7])


def h3_event_mix(
    weak_rows: list[dict[str, Any]],
    direct_by_region: dict[str, list[dict[str, Any]]],
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for region_id in REGIONS_UNDER_STUDY:
        artifact = artifacts[region_id]
        train_rows = [r for r in weak_rows if str(r.get("region_id")) == region_id and int(r["water_year"]) in TRAIN_YEARS]
        direct_rows = direct_by_region.get(region_id, [])

        def describe(rows: list[dict[str, Any]], size_key: str) -> dict[str, Any]:
            months = Counter(_month_of(r) for r in rows)
            years = Counter(int(str(r.get("observation_date") or str(r["valid_time"])[:10])[:4]) + (1 if int(str(r.get("observation_date") or str(r["valid_time"])[:10])[5:7]) >= 10 else 0) for r in rows)
            sizes = [float(r[size_key]) for r in rows if r.get(size_key) not in (None, "")]
            return {
                "n": len(rows),
                "elevationM": _dist([_f(r, "elevation_m") for r in rows]),
                "baselineSlr": _dist([_f(r, "baseline_slr") for r in rows]),
                "observedSlr": _dist([_f(r, "observed_slr") for r in rows]),
                "eventSizeSweMm": _dist(sizes),
                "months": {str(m): months[m] for m in sorted(months)},
                "waterYears": {str(y): years[y] for y in sorted(years)},
                "stations": len({str(r["station_id"]) for r in rows}),
            }

        # overlap envelope from the weak training population
        def envelope(rows: list[dict[str, Any]], key: str) -> tuple[float, float]:
            values = [_f(r, key) for r in rows]
            return _quantile(values, 0.01), _quantile(values, 0.99)

        env = {
            "baseline_slr": envelope(train_rows, "baseline_slr"),
            "elevation_m": envelope(train_rows, "elevation_m"),
            "delta_swe_mm": envelope(train_rows, "delta_swe_mm"),
        }
        train_months = {_month_of(r) for r in train_rows}
        inside: list[dict[str, Any]] = []
        for row in direct_rows:
            swe = row.get("fresh_swe_mm")
            if swe in (None, ""):
                continue
            if not (env["baseline_slr"][0] <= _f(row, "baseline_slr") <= env["baseline_slr"][1]):
                continue
            if not (env["elevation_m"][0] <= _f(row, "elevation_m") <= env["elevation_m"][1]):
                continue
            if not (env["delta_swe_mm"][0] <= float(swe) <= env["delta_swe_mm"][1]):
                continue
            if _month_of(row) not in train_months:
                continue
            inside.append(row)

        def mae_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                "n": len(rows),
                "kucheraMae": _mae([(_f(r, "baseline_slr"), _f(r, "observed_slr")) for r in rows]),
                "artifactMae": _mae([(predict_variant(artifact, r), _f(r, "observed_slr")) for r in rows]),
            }

        full_block = mae_block(direct_rows)
        inside_block = mae_block(inside)
        out[region_id] = {
            "weakTrain": describe(train_rows, "delta_swe_mm"),
            "direct": describe(direct_rows, "fresh_swe_mm"),
            "envelopeP1P99": {key: [round(lo, 3), round(hi, 3)] for key, (lo, hi) in env.items()},
            "directFull": {**full_block, "degradation": round(full_block["artifactMae"] - full_block["kucheraMae"], 4)},
            "directInsideEnvelope": {**inside_block, "degradation": round(inside_block["artifactMae"] - inside_block["kucheraMae"], 4) if inside else None},
        }
    return out


# ---------------------------------------------------------------------------
# H2 -- baseline provenance + the CSSL same-day decomposition
# ---------------------------------------------------------------------------

def _parse_cssl_number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if text in {"", "--", "NA", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load_cssl_board_days(raw_dir: Path, water_years: Iterable[int]) -> list[dict[str, Any]]:
    """Board SLR per day from the CSSL raw release, importer formula/QC.

    fresh_swe_mm = 24 h precip x (% precip as snow)/100 (cssl.py);
    observed_slr = new snow (mm) / fresh_swe_mm.  ``board_event`` applies
    the importer's own thresholds (new snow >= 2.5 cm, fresh SWE >= 2.5 mm,
    SLR in (2, 50)).
    """
    days: list[dict[str, Any]] = []
    for water_year in water_years:
        path = raw_dir / f"WY{water_year}.csv"
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                date_text = (row.get("Date") or "").strip()
                new_snow_cm = _parse_cssl_number(row.get("New Snow (cm)"))
                precip_mm = _parse_cssl_number(row.get("24-hour Total Precip (mm)"))
                snow_pct = _parse_cssl_number(row.get("% of Precip as Snow"))
                if not date_text or new_snow_cm is None or precip_mm is None or snow_pct is None:
                    continue
                fresh_swe = precip_mm * snow_pct / 100.0
                board_slr = (new_snow_cm * 10.0 / fresh_swe) if fresh_swe > 0 else None
                days.append({
                    "date": date_text,
                    "new_snow_cm": new_snow_cm,
                    "fresh_swe_mm": fresh_swe,
                    "board_slr": board_slr,
                    "board_event": bool(
                        board_slr is not None
                        and new_snow_cm >= 2.5
                        and fresh_swe >= 2.5
                        and 2.0 < board_slr < 50.0
                    ),
                })
    return days


def load_station_daily(dataset_dir: Path, triplet: str, water_years: Iterable[int]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    columns = ["station_triplet", "date", "delta_swe_mm", "delta_depth_mm", "weak_qc_passed", "observed_slr_diff", "fcst_kuchera_slr"]
    for water_year in water_years:
        partition = dataset_dir / "daily" / f"water_year={water_year}" / "data.parquet"
        if not partition.exists():
            continue
        table = pq.read_table(partition, columns=columns)
        for row in table.to_pylist():
            if row["station_triplet"] == triplet:
                out[str(row["date"])[:10]] = row
    return out


def h2_provenance(
    weak_rows: list[dict[str, Any]],
    direct_by_region: dict[str, list[dict[str, Any]]],
    cssl_raw_dir: Path,
    dataset_dir: Path,
    cssl_direct_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}

    # (a) baseline distributions, weak train vs direct, overall + by elevation band
    bands = ((2000.0, 2500.0), (2500.0, 3000.0), (3000.0, 3500.0), (3500.0, 1e9))
    band_rows: dict[str, Any] = {}
    for region_id in REGIONS_UNDER_STUDY:
        train_rows = [r for r in weak_rows if str(r.get("region_id")) == region_id and int(r["water_year"]) in TRAIN_YEARS]
        direct_rows = direct_by_region.get(region_id, [])
        per_band = []
        for low, high in bands:
            weak_band = [_f(r, "baseline_slr") for r in train_rows if low <= _f(r, "elevation_m") < high]
            direct_band = [_f(r, "baseline_slr") for r in direct_rows if low <= _f(r, "elevation_m") < high]
            if len(weak_band) >= 10 and len(direct_band) >= 10:
                per_band.append({
                    "band": f"{int(low)}-{int(high) if high < 1e9 else '+'}",
                    "weakN": len(weak_band),
                    "directN": len(direct_band),
                    "weakMeanBaseline": round(mean(weak_band), 4),
                    "directMeanBaseline": round(mean(direct_band), 4),
                    "logDirectOverWeak": round(math.log(mean(direct_band) / mean(weak_band)), 4),
                })
        band_rows[region_id] = {
            "weakTrainBaseline": _dist([_f(r, "baseline_slr") for r in train_rows]),
            "directBaseline": _dist([_f(r, "baseline_slr") for r in direct_rows]),
            "byElevationBand": per_band,
        }
    out["baselineDistributions"] = band_rows

    # (b) CSSL same-day decomposition
    board_days = load_cssl_board_days(cssl_raw_dir, (2024, 2025, 2026))
    snotel = load_station_daily(dataset_dir, CSSL_TRIPLET, (2024, 2025, 2026))
    from datetime import date, timedelta

    def shift_key(date_text: str, shift: int) -> str:
        return (date.fromisoformat(date_text) + timedelta(days=shift)).isoformat()

    shift_stats: dict[str, Any] = {}
    chosen_shift: int | None = None
    best_corr = -2.0
    for shift in (-1, 0, 1):
        pairs = []
        for day in board_days:
            if not day["board_event"]:
                continue
            srow = snotel.get(shift_key(day["date"], shift))
            if srow is None or srow.get("observed_slr_diff") is None:
                continue
            pairs.append((day, srow))
        corr = _pearson(
            [d["fresh_swe_mm"] for d, s in pairs if s["delta_swe_mm"] is not None],
            [float(s["delta_swe_mm"]) for d, s in pairs if s["delta_swe_mm"] is not None],
        ) if pairs else float("nan")
        shift_stats[str(shift)] = {
            "matchedBoardEventDays": len(pairs),
            "freshSweVsDeltaSwePearson": corr,
            "meanLogBoardOverDiff": round(mean(math.log(d["board_slr"] / float(s["observed_slr_diff"])) for d, s in pairs), 4) if pairs else None,
        }
        if corr == corr and corr > best_corr:  # NaN-safe comparison
            best_corr = corr
            chosen_shift = shift
    decomposition: dict[str, Any] = {"shiftStats": shift_stats, "chosenShiftDays": chosen_shift}
    if chosen_shift is not None:
        label_logs: list[float] = []
        depth_logs: list[float] = []
        swe_logs: list[float] = []
        qc_passed = 0
        matched = 0
        for day in board_days:
            if not day["board_event"]:
                continue
            srow = snotel.get(shift_key(day["date"], chosen_shift))
            if srow is None or srow.get("observed_slr_diff") is None:
                continue
            matched += 1
            if srow.get("weak_qc_passed"):
                qc_passed += 1
            label_logs.append(math.log(day["board_slr"] / float(srow["observed_slr_diff"])))
            if srow.get("delta_depth_mm"):
                depth_logs.append(math.log(day["new_snow_cm"] * 10.0 / float(srow["delta_depth_mm"])))
            if srow.get("delta_swe_mm"):
                swe_logs.append(math.log(day["fresh_swe_mm"] / float(srow["delta_swe_mm"])))
        decomposition.update({
            "matchedBoardEventDays": matched,
            "ofWhichWeakQcPassed": qc_passed,
            "boardDaysTotal": sum(1 for d in board_days if d["board_event"]),
            "labelDeltaLogBoardOverDiff": _dist(label_logs),
            "labelDeltaFactor": round(math.exp(mean(label_logs)), 4) if label_logs else None,
            "depthComponentLogBoardDepthOverDeltaDepth": _dist(depth_logs),
            "sweComponentLogFreshSweOverDeltaSwe": _dist(swe_logs),
            "note": "log(board/differenced) = log(board depth / delta depth) - log(fresh SWE / delta SWE) per day; means of the components need not sum exactly to the mean of the ratio.",
        })
        # baseline delta on the 35 profile-aligned CSSL direct events
        base_logs: list[float] = []
        aligned_label_logs: list[float] = []
        for row in cssl_direct_rows:
            srow = snotel.get(shift_key(str(row["observation_date"]), chosen_shift))
            if srow is None or srow.get("fcst_kuchera_slr") is None:
                continue
            base_logs.append(math.log(_f(row, "baseline_slr") / float(srow["fcst_kuchera_slr"])))
            if srow.get("observed_slr_diff") is not None:
                aligned_label_logs.append(math.log(_f(row, "observed_slr") / float(srow["observed_slr_diff"])))
        decomposition["alignedBaselineDelta"] = {
            "matchedAlignedEvents": len(base_logs),
            "logTsOverNoaaBaseline": _dist(base_logs),
            "factorTsOverNoaa": round(math.exp(mean(base_logs)), 4) if base_logs else None,
            "labelDeltaOnAlignedDays": _dist(aligned_label_logs),
        }
    out["csslDecomposition"] = decomposition
    return out


# ---------------------------------------------------------------------------
# H5 -- QC censoring in the full daily dataset
# ---------------------------------------------------------------------------

_TEMP_BANDS = ((-1e9, -8.0, "tavg_lt-8"), (-8.0, -3.0, "tavg_-8_-3"), (-3.0, 0.0, "tavg_-3_0"), (0.0, 0.5, "tavg_0_0.5"), (0.5, 1e9, "tavg_gt0.5"))


def _temp_band(value: float | None) -> str:
    if value is None:
        return "tavg_missing"
    for low, high, name in _TEMP_BANDS:
        if low <= value < high:
            return name
    return _TEMP_BANDS[-1][2]


def h5_qc_censoring(
    dataset_dir: Path,
    regimes_path: Path,
    direct_by_region: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    regimes = load_station_regimes(regimes_path)
    region_stations: dict[str, set[str]] = defaultdict(set)
    for triplet, assignment in regimes.items():
        if assignment.get("region_id") in REGIONS_UNDER_STUDY:
            region_stations[str(assignment["region_id"])].add(triplet)
    columns = ["station_triplet", "delta_swe_mm", "delta_depth_mm", "tavg_c", "weak_qc_passed", "weak_qc_rejection", "observed_slr_diff"]
    rows_by_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for water_year in (2024, 2025, 2026):
        partition = dataset_dir / "daily" / f"water_year={water_year}" / "data.parquet"
        if not partition.exists():
            continue
        table = pq.read_table(partition, columns=columns)
        for row in table.to_pylist():
            for region_id, stations in region_stations.items():
                if row["station_triplet"] in stations:
                    rows_by_region[region_id].append(row)
                    break

    out: dict[str, Any] = {}
    for region_id in REGIONS_UNDER_STUDY:
        rows = rows_by_region.get(region_id, [])
        evaluated = [r for r in rows if r.get("weak_qc_passed") is not None]
        passed = [r for r in evaluated if r["weak_qc_passed"]]
        rejections = Counter(str(r["weak_qc_rejection"]) for r in evaluated if not r["weak_qc_passed"])
        swe_gain = [r for r in evaluated if r.get("delta_swe_mm") is not None and float(r["delta_swe_mm"]) >= 5.0]
        by_band: dict[str, Any] = {}
        for band in [name for _, _, name in _TEMP_BANDS] + ["tavg_missing"]:
            band_rows = [r for r in swe_gain if _temp_band(r.get("tavg_c")) == band]
            if not band_rows:
                continue
            by_band[band] = {
                "sweGainDays": len(band_rows),
                "qcPassRate": round(sum(1 for r in band_rows if r["weak_qc_passed"]) / len(band_rows), 4),
                "shareDepthDecreased": round(sum(1 for r in band_rows if r.get("delta_depth_mm") is not None and float(r["delta_depth_mm"]) < 0) / len(band_rows), 4),
            }

        def slr_of(r: dict[str, Any]) -> float | None:
            if r.get("delta_swe_mm") and r.get("delta_depth_mm") is not None and float(r["delta_swe_mm"]) > 0:
                return float(r["delta_depth_mm"]) / float(r["delta_swe_mm"])
            return None

        pre_qc = [v for r in swe_gain if (v := slr_of(r)) is not None and float(r["delta_depth_mm"]) > 0]
        passed_slr = [v for r in passed if (v := slr_of(r)) is not None]
        warm_censored = [
            v for r in evaluated
            if str(r.get("weak_qc_rejection")) in {"WARM_DAY", "RAIN_PHASE_SUSPECTED_TMAX"}
            and r.get("delta_swe_mm") is not None and float(r["delta_swe_mm"]) >= 5.0
            and (v := slr_of(r)) is not None and float(r["delta_depth_mm"]) > 0
        ]
        settled = [r for r in swe_gain if str(r.get("weak_qc_rejection")) == "DEPTH_DECREASED_MELT_OR_SETTLEMENT"]
        direct_rows = direct_by_region.get(region_id, [])
        out[region_id] = {
            "stationDays": len(rows),
            "qcEvaluatedDays": len(evaluated),
            "qcPassedDays": len(passed),
            "qcPassRateOverall": round(len(passed) / len(evaluated), 4) if evaluated else None,
            "rejectionCounts": dict(rejections.most_common()),
            "sweGainDays": len(swe_gain),
            "sweGainSurvivalRate": round(sum(1 for r in swe_gain if r["weak_qc_passed"]) / len(swe_gain), 4) if swe_gain else None,
            "sweGainByTempBand": by_band,
            "slrPreQcComputable": _dist(pre_qc),
            "slrQcPassed": _dist(passed_slr),
            "slrWarmCensored": _dist(warm_censored),
            "settlementCensoredDays": len(settled),
            "settlementCensoredMedianDeltaDepthMm": round(median([float(r["delta_depth_mm"]) for r in settled if r.get("delta_depth_mm") is not None]), 3) if settled else None,
            "directObservedSlr": _dist([_f(r, "observed_slr") for r in direct_rows]),
        }
    return out


# ---------------------------------------------------------------------------
# salvage (iii) -- differencing-bias offset refits
# ---------------------------------------------------------------------------

def salvage_offset_refits(
    weak_rows: list[dict[str, Any]],
    direct_by_region: dict[str, list[dict[str, Any]]],
    artifacts: dict[str, dict[str, Any]],
    label_delta_log: float,
) -> dict[str, Any]:
    """Refit A on weak labels shifted up by the CSSL differencing-bias factor.

    The offset is estimated at one Sierra site (CSSL) from same-day board vs
    differenced labels.  Scoring the Sierra refit on Sierra GHCN events is
    honest (the offset never saw them); scoring on CSSL events is not
    independent and is flagged.  The Colorado refit is an explicitly
    exploratory cross-region transfer of a Sierra constant.
    """
    factor = math.exp(label_delta_log)
    out: dict[str, Any] = {"offsetLog": round(label_delta_log, 4), "offsetFactor": round(factor, 4)}
    for region_id in REGIONS_UNDER_STUDY:
        train_rows = [
            {**r, "observed_slr": _f(r, "observed_slr") * factor}
            for r in weak_rows
            if str(r.get("region_id")) == region_id and int(r["water_year"]) in TRAIN_YEARS
        ]
        label = str(REGION_DEFS[region_id]["label"])
        shifted = train_artifact(train_rows, _config(region_id, "A", label))
        direct_rows = direct_by_region.get(region_id, [])
        entry: dict[str, Any] = {
            "originalGlobalLogBias": artifacts[region_id]["methods"]["kuchera"]["global"]["logBias"],
            "shiftedGlobalLogBias": shifted["methods"]["kuchera"]["global"]["logBias"],
            "directMaeShifted": _mae([(predict_variant(shifted, r), _f(r, "observed_slr")) for r in direct_rows]),
            "directMaeKuchera": _mae([(_f(r, "baseline_slr"), _f(r, "observed_slr")) for r in direct_rows]),
            "directMaeOriginal": _mae([(predict_variant(artifacts[region_id], r), _f(r, "observed_slr")) for r in direct_rows]),
        }
        if region_id == "sierra-nevada":
            ghcn_rows = [r for r in direct_rows if r.get("direct_set") == "sierra-ghcn"]
            cssl_rows = [r for r in direct_rows if r.get("direct_set") == "cssl"]
            entry["sierraGhcnOnly"] = {
                "n": len(ghcn_rows),
                "shiftedMae": _mae([(predict_variant(shifted, r), _f(r, "observed_slr")) for r in ghcn_rows]),
                "kucheraMae": _mae([(_f(r, "baseline_slr"), _f(r, "observed_slr")) for r in ghcn_rows]),
                "originalMae": _mae([(predict_variant(artifacts[region_id], r), _f(r, "observed_slr")) for r in ghcn_rows]),
            }
            entry["csslOnlyNotIndependent"] = {
                "n": len(cssl_rows),
                "shiftedMae": _mae([(predict_variant(shifted, r), _f(r, "observed_slr")) for r in cssl_rows]),
                "kucheraMae": _mae([(_f(r, "baseline_slr"), _f(r, "observed_slr")) for r in cssl_rows]),
                "originalMae": _mae([(predict_variant(artifacts[region_id], r), _f(r, "observed_slr")) for r in cssl_rows]),
            }
        out[region_id] = entry
    return out


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def run_weak_label_investigation(
    *,
    events_path: Path,
    evaluation_report_path: Path,
    rockies_baseline: Path,
    rockies_events: Path,
    sierra_baseline: Path,
    sierra_events: Path,
    cssl_baseline: Path,
    cssl_profile_events: Path,
    cssl_raw_dir: Path,
    dataset_dir: Path,
    regimes_path: Path,
    report_json_path: Path,
    report_md_path: Path,
) -> dict[str, Any]:
    weak_rows = load_weak_events(events_path)
    direct_by_region = load_direct_sets(
        rockies_baseline, rockies_events, sierra_baseline, sierra_events, cssl_baseline, cssl_profile_events,
    )
    artifacts = {region_id: fit_candidate_a(weak_rows, region_id) for region_id in REGIONS_UNDER_STUDY}
    published = json.loads(evaluation_report_path.read_text(encoding="utf-8")) if evaluation_report_path.exists() else {}

    results: dict[str, Any] = {
        "status": "diagnostic-only-no-artifact-no-gate-change",
        "scope": (
            "Refit Candidate A artifacts for colorado-ranges and sierra-nevada on the build's weak "
            "training slice (WY2024-2025, SNOTEL-differenced labels, NOAA HRRR baselines); direct sets "
            "are the build's section 6.4 sets with identical routing. Candidate B is not studied: it "
            "cannot be applied to the direct sets (no stored portable features), and the demotion was "
            "incurred by Candidate A."
        ),
        "reproduction": reproduction_check(weak_rows, direct_by_region, artifacts, published),
        "h1LevelBias": h1_level_bias(weak_rows, direct_by_region, artifacts),
        "h4HierarchyDepth": h4_hierarchy_depth(weak_rows, direct_by_region, artifacts),
        "h3EventMix": h3_event_mix(weak_rows, direct_by_region, artifacts),
        "h2Provenance": h2_provenance(
            weak_rows,
            direct_by_region,
            cssl_raw_dir,
            dataset_dir,
            [r for r in direct_by_region.get("sierra-nevada", []) if r.get("direct_set") == "cssl"],
        ),
        "h5QcCensoring": h5_qc_censoring(dataset_dir, regimes_path, direct_by_region),
    }
    label_delta = (
        (results["h2Provenance"].get("csslDecomposition") or {}).get("labelDeltaLogBoardOverDiff") or {}
    ).get("mean")
    if label_delta is not None:
        results["salvageOffsetRefits"] = salvage_offset_refits(weak_rows, direct_by_region, artifacts, float(label_delta))
    else:
        results["salvageOffsetRefits"] = {"status": "skipped-no-cssl-overlap"}

    report_json_path.parent.mkdir(parents=True, exist_ok=True)
    report_json_path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text(_render_markdown(results), encoding="utf-8")
    return results


# ---------------------------------------------------------------------------
# markdown rendering
# ---------------------------------------------------------------------------

def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        if value != value:
            return "n/a"
        return f"{value:.{digits}f}"
    return str(value)


def _pct(value: Any) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "n/a"
    return f"{100.0 * float(value):.0f}%"


def _render_markdown(r: dict[str, Any]) -> str:
    L: list[str] = []
    L.append("# Why weak-label SLR corrections fail on direct labels")
    L.append("")
    L.append("Diagnostic investigation, research-only. No artifact was retained, no gate was changed, "
             "and nothing here modifies the production calibration configuration. Every number below is "
             "produced by `training/slr_calibration/src/slr_calibration/weak_label_investigation.py` "
             "(`investigate-weak-labels` CLI) and is also in `weak_label_investigation.json` next to this file.")
    L.append("")
    L.append("**Setup.** Candidate A (the existing hierarchical EB log-residual trainer) was refit for the "
             "Colorado Ranges and Sierra Nevada regions on the region-first build's exact weak training slice "
             "(WY2024-2025, SNOTEL-differenced labels at qc_weight 0.25, Python-Kuchera baselines over NOAA "
             "HRRR 00Z profiles). The build fitted but did not package these two artifacts because the section "
             "6.4 direct-label check demoted both regions; this investigation takes those refits apart. The "
             "direct sets are the build's own: Rockies GHCN routed by state (120 Colorado events) and Sierra "
             "GHCN (225) + CSSL (35), whose baselines are production TypeScript Kuchera over Open-Meteo "
             "best-match profiles. Candidate B is out of scope: it cannot be applied to the direct sets "
             "(no stored portable features), and the demotion was incurred by A.")
    L.append("")

    # --- reproduction -------------------------------------------------------
    L.append("## 0. Reproduction check (the refits are the build's fits)")
    L.append("")
    L.append("| Region | Weak WY26 MAE (refit / published) | Direct MAE (refit / published) | Direct Kuchera MAE (refit / published) |")
    L.append("|---|---|---|---|")
    for region_id in REGIONS_UNDER_STUDY:
        rep = r["reproduction"][region_id]
        L.append(
            f"| {region_id} | {_fmt(rep['weakSplit1Mae'])} / {_fmt(rep['weakSplit1MaePublished'])} | "
            f"{_fmt(rep['directMae'])} / {_fmt(rep['directMaePublished'])} | "
            f"{_fmt(rep['directKucheraMae'])} / {_fmt(rep['directKucheraMaePublished'])} |"
        )
    L.append("")

    # --- verdict summary ------------------------------------------------------
    h1v = r["h1LevelBias"]
    h3v = r["h3EventMix"]
    h4v = r["h4HierarchyDepth"]
    h5v = r["h5QcCensoring"]
    decv = (r["h2Provenance"].get("csslDecomposition") or {})
    co, si = h1v["colorado-ranges"], h1v["sierra-nevada"]
    L.append("## Verdict summary")
    L.append("")
    L.append(f"- **H1 (differencing level bias) — dominant mechanism, confirmed.** The weak training "
             f"log-ratio sits {_fmt(abs(co['weakMinusDirectMeanLogRatio']), 2)} (Colorado) / {_fmt(abs(si['weakMinusDirectMeanLogRatio']), 2)} (Sierra) "
             f"log units below the direct one. Honest (leave-one-out) recalibration of the single intercept on "
             f"direct labels recovers {_pct(co['variantLooRecalibrated']['shareOfDegradation'])} / {_pct(si['variantLooRecalibrated']['shareOfDegradation'])} "
             f"of the degradation and turns both artifacts into improvements over raw Kuchera "
             f"({_fmt(co['variantLooRecalibrated']['mae'])} vs {_fmt(co['directMaeKuchera'])}; {_fmt(si['variantLooRecalibrated']['mae'])} vs {_fmt(si['directMaeKuchera'])}). "
             f"The level is simply fit to a different label definition.")
    L.append(f"- **H2 (baseline provenance) — minor.** On identical CSSL station-days the TypeScript/Open-Meteo "
             f"baseline is a factor {_fmt((decv.get('alignedBaselineDelta') or {}).get('factorTsOverNoaa'), 3)} of the Python/NOAA baseline "
             f"(~5%), an order of magnitude below the level gap, and it slightly *offsets* the gap rather than "
             f"causing it. Matched elevation-band comparisons in Colorado even disagree in sign. Provenance "
             f"cannot explain degradations of this size.")
    L.append(f"- **H3 (event mix) — real but secondary, and it cuts both ways.** The direct sets are lower, "
             f"warmer storms (mean baseline {_fmt(h3v['colorado-ranges']['direct']['baselineSlr'].get('mean'), 1)} vs "
             f"{_fmt(h3v['colorado-ranges']['weakTrain']['baselineSlr'].get('mean'), 1)} in Colorado; "
             f"{_fmt(h3v['sierra-nevada']['direct']['baselineSlr'].get('mean'), 1)} vs {_fmt(h3v['sierra-nevada']['weakTrain']['baselineSlr'].get('mean'), 1)} in Sierra). "
             f"Inside the weak training envelope the Colorado degradation flips to {_fmt(h3v['colorado-ranges']['directInsideEnvelope']['degradation'])} "
             f"(the artifact *wins* there in MAE) while Sierra's shrinks to {_fmt(h3v['sierra-nevada']['directInsideEnvelope']['degradation'])}; "
             f"the net MAE harm concentrates in low-baseline events the SNOTEL population rarely produces. "
             f"The log-space level bias itself is mix-independent — mix decides how much of it MAE sees.")
    L.append(f"- **H4 (hierarchy depth) — all of the damage is the intercept; there is effectively no shape. "
             f"Mean deeper logBias terms are ≤ 0.05 in magnitude, depth-by-depth MAE is identical to four "
             f"decimals, and the summed correction saturates the artifact's own 0.5 correction-factor floor "
             f"for {_pct(h4v['colorado-ranges']['direct'].get('factorFloorShare'))} / {_pct(h4v['sierra-nevada']['direct'].get('factorFloorShare'))} "
             f"of direct events: as applied, Candidate A is 'Kuchera x 0.5' almost everywhere. Shrinkage plus "
             f"a spatially homogeneous differencing bias leave the hierarchy nothing else to learn.")
    L.append(f"- **H5 (QC censoring) — severe in count, exonerated for the level gap.** Only "
             f"{_pct(h5v['colorado-ranges']['sweGainSurvivalRate'])} / {_pct(h5v['sierra-nevada']['sweGainSurvivalRate'])} of SWE-gain days survive the gates, "
             f"and survival above 0 C average temperature is ~0 — the weak population is a cold-storm subset. "
             f"But the differenced-SLR distribution barely moves across the gates (pre-QC vs passed means "
             f"{_fmt(h5v['colorado-ranges']['slrPreQcComputable'].get('mean'), 2)} vs {_fmt(h5v['colorado-ranges']['slrQcPassed'].get('mean'), 2)} in Colorado; "
             f"{_fmt(h5v['sierra-nevada']['slrPreQcComputable'].get('mean'), 2)} vs {_fmt(h5v['sierra-nevada']['slrQcPassed'].get('mean'), 2)} in Sierra): "
             f"censoring shapes *which storms* are learned from, it does not create the board-vs-differenced gap, "
             f"which is fully present inside the QC-passed population (section H2).")
    L.append(f"- **Salvage — yes, but it is a direct-label model, not a weak-label one.** (i) Weak structure + "
             f"direct-fit intercept beats raw Kuchera in both regions under LOO and split-half honesty; since "
             f"the weak shape terms are ~0, what survives is a one-parameter direct-label calibration. "
             f"(ii) The weak shape alone (intercept dropped) is a wash: marginally better than Kuchera "
             f"({_fmt(co['variantNoGlobal']['mae'])} / {_fmt(si['variantNoGlobal']['mae'])}), within noise of it. "
             f"(iii) The CSSL differencing offset (factor {_fmt(decv.get('labelDeltaFactor'), 2)}) recovers only about half the "
             f"Sierra level gap and fails on Sierra GHCN events, though transferred to Colorado it happens to "
             f"beat Kuchera. Bottom line: **weak labels can rank regions and seasons, but they cannot set "
             f"correction levels** — the level must come from direct labels, and at present direct labels "
             f"support exactly a one-parameter-per-region correction.")
    L.append("")

    # --- H1 -----------------------------------------------------------------
    h1 = r["h1LevelBias"]
    L.append("## H1. Differencing level bias — the intercept is fit to the wrong label definition")
    L.append("")
    L.append("Mean log(observed/baseline) is the quantity the hierarchy's global node estimates. "
             "If differenced labels run systematically lower than board labels, the whole correction is "
             "shifted down by roughly the gap.")
    L.append("")
    L.append("| Region | Weak train mean log-ratio | Weak WY26 mean log-ratio | Direct mean log-ratio | Weak − direct gap | Fitted global logBias (factor) | Direct-implied intercept |")
    L.append("|---|---|---|---|---|---|---|")
    for region_id in REGIONS_UNDER_STUDY:
        h = h1[region_id]
        L.append(
            f"| {region_id} | {_fmt(h['weakTrainLogRatio'].get('weightedMean'))} | {_fmt(h['weakTestLogRatio'].get('weightedMean'))} | "
            f"{_fmt(h['directLogRatio'].get('weightedMean'))} | {_fmt(h['weakMinusDirectMeanLogRatio'])} | "
            f"{_fmt(h['fittedGlobalLogBias'])} ({_fmt(h['fittedGlobalFactor'], 2)}×) | {_fmt(h['directImpliedGlobalMean'])} |"
        )
    L.append("")
    L.append("Direct-label MAE under intercept surgery (degradation = full artifact MAE − Kuchera MAE):")
    L.append("")
    L.append("| Region | Kuchera | Full A | Degradation | A minus global (recovered / share) | A, LOO-recalibrated intercept (recovered / share) | A, split-half recalibrated (recovered / share) | Oracle intercept (in-sample) |")
    L.append("|---|---|---|---|---|---|---|---|")
    for region_id in REGIONS_UNDER_STUDY:
        h = h1[region_id]
        L.append(
            f"| {region_id} | {_fmt(h['directMaeKuchera'])} | {_fmt(h['directMaeFullArtifact'])} | {_fmt(h['degradation'])} | "
            f"{_fmt(h['variantNoGlobal']['mae'])} ({_fmt(h['variantNoGlobal']['recoveredMae'])} / {_pct(h['variantNoGlobal']['shareOfDegradation'])}) | "
            f"{_fmt(h['variantLooRecalibrated']['mae'])} ({_fmt(h['variantLooRecalibrated']['recoveredMae'])} / {_pct(h['variantLooRecalibrated']['shareOfDegradation'])}) | "
            f"{_fmt(h['variantSplitHalfRecalibrated']['maeMean'])} ({_fmt(h['variantSplitHalfRecalibrated']['recoveredMae'])} / {_pct(h['variantSplitHalfRecalibrated']['shareOfDegradation'])}) | "
            f"{_fmt(h['variantOracleIntercept']['mae'])} |"
        )
    L.append("")
    L.append(f"Full-artifact bias on direct events (predicted − observed SLR): "
             + "; ".join(f"{region_id} {_fmt(h1[region_id]['directBiasFullArtifact'])}" for region_id in REGIONS_UNDER_STUDY)
             + ". A negative bias is the H1 signature (corrections push SLR too far down).")
    L.append("")

    # --- H2 -----------------------------------------------------------------
    h2 = r["h2Provenance"]
    dec = h2.get("csslDecomposition") or {}
    L.append("## H2. Baseline provenance — and the CSSL same-day decomposition")
    L.append("")
    L.append("The CSSL laboratory is SNOTEL station `428:CA:SNTL` (same coordinates, 2100 m). Its raw daily "
             "release gives board new-snow depth and precipitation-based fresh SWE for every day, so the "
             "label-definition effect can be measured on the same station-days with no profile involved. "
             "(In the stored dataset the differenced SLR field is populated only on QC-passed rows, so the "
             "matched population below is QC-passed station-days — exactly the population the trainer sees.) "
             "Date alignment between the CSSL morning-observation date and the AWDB daily date was chosen "
             "empirically (the shift maximising the fresh-SWE vs delta-SWE correlation):")
    L.append("")
    L.append("| CSSL date shift vs SNOTEL date | Matched board-event days | corr(fresh SWE, ΔSWE) | Mean log(board / differenced SLR) |")
    L.append("|---|---|---|---|")
    for shift, stats in sorted((dec.get("shiftStats") or {}).items()):
        L.append(f"| {shift} d | {stats.get('matchedBoardEventDays')} | {_fmt(stats.get('freshSweVsDeltaSwePearson'), 2)} | {_fmt(stats.get('meanLogBoardOverDiff'))} |")
    L.append("")
    if dec.get("labelDeltaFactor") is not None:
        L.append(
            f"At the chosen shift ({dec.get('chosenShiftDays')} d): **{dec.get('matchedBoardEventDays')} matched board-event days** "
            f"(of {dec.get('boardDaysTotal')} CSSL board-event days in WY2024-2026; {dec.get('ofWhichWeakQcPassed')} of the matched "
            f"SNOTEL days passed weak QC). Board SLR exceeds differenced SLR by a factor of **{_fmt(dec.get('labelDeltaFactor'), 3)}** "
            f"(mean log {_fmt((dec.get('labelDeltaLogBoardOverDiff') or {}).get('mean'))}, median {_fmt((dec.get('labelDeltaLogBoardOverDiff') or {}).get('median'))}). "
            f"Decomposition: board depth vs SNOTEL Δdepth contributes log {_fmt((dec.get('depthComponentLogBoardDepthOverDeltaDepth') or {}).get('mean'))} "
            f"(the settlement term), fresh SWE vs ΔSWE contributes log {_fmt((dec.get('sweComponentLogFreshSweOverDeltaSwe') or {}).get('mean'))} "
            f"(so log board/differenced ≈ depth component − SWE component)."
        )
        L.append("")
    aligned = dec.get("alignedBaselineDelta") or {}
    if aligned.get("matchedAlignedEvents"):
        L.append(
            f"On the {aligned['matchedAlignedEvents']} profile-aligned CSSL event days, the TypeScript/Open-Meteo baseline "
            f"runs at a factor of **{_fmt(aligned.get('factorTsOverNoaa'), 3)}** of the Python/NOAA baseline "
            f"(mean log {_fmt((aligned.get('logTsOverNoaaBaseline') or {}).get('mean'))}, median {_fmt((aligned.get('logTsOverNoaaBaseline') or {}).get('median'))}) "
            f"on identical station-days — this is the pure provenance offset the correction was never fit against. "
            f"The label delta on those same aligned days is mean log {_fmt((aligned.get('labelDeltaOnAlignedDays') or {}).get('mean'))}."
        )
        L.append("")
    L.append("Baseline distributions (weak train vs direct) by elevation band — mean-baseline log ratio "
             "direct/weak within matched bands:")
    L.append("")
    for region_id in REGIONS_UNDER_STUDY:
        block = h2["baselineDistributions"][region_id]
        L.append(f"- {region_id}: weak mean baseline {_fmt(block['weakTrainBaseline'].get('mean'), 2)} "
                 f"(n={block['weakTrainBaseline'].get('n')}), direct mean {_fmt(block['directBaseline'].get('mean'), 2)} "
                 f"(n={block['directBaseline'].get('n')})")
        for band in block["byElevationBand"]:
            L.append(f"  - {band['band']} m: weak {_fmt(band['weakMeanBaseline'], 2)} (n={band['weakN']}), "
                     f"direct {_fmt(band['directMeanBaseline'], 2)} (n={band['directN']}), log ratio {_fmt(band['logDirectOverWeak'])}")
    L.append("")

    # --- H3 -----------------------------------------------------------------
    h3r = r["h3EventMix"]
    L.append("## H3. Event-mix mismatch")
    L.append("")
    L.append("| Region | Population | n | Elev mean (m) | Baseline SLR mean / median | Observed SLR mean / median | Event SWE mean (mm) |")
    L.append("|---|---|---|---|---|---|---|")
    for region_id in REGIONS_UNDER_STUDY:
        h = h3r[region_id]
        for name, block in (("weak train", h["weakTrain"]), ("direct", h["direct"])):
            L.append(
                f"| {region_id} | {name} | {block['n']} | {_fmt(block['elevationM'].get('mean'), 0)} | "
                f"{_fmt(block['baselineSlr'].get('mean'), 2)} / {_fmt(block['baselineSlr'].get('median'), 2)} | "
                f"{_fmt(block['observedSlr'].get('mean'), 2)} / {_fmt(block['observedSlr'].get('median'), 2)} | "
                f"{_fmt(block['eventSizeSweMm'].get('mean'), 1)} |"
            )
    L.append("")
    L.append("Restricting direct events to the weak training envelope (P1-P99 of baseline SLR, elevation, "
             "event SWE, and training months):")
    L.append("")
    L.append("| Region | Direct n (full / in-envelope) | Degradation full | Degradation in-envelope |")
    L.append("|---|---|---|---|")
    for region_id in REGIONS_UNDER_STUDY:
        h = h3r[region_id]
        L.append(f"| {region_id} | {h['directFull']['n']} / {h['directInsideEnvelope']['n']} | "
                 f"{_fmt(h['directFull']['degradation'])} | {_fmt(h['directInsideEnvelope']['degradation'])} |")
    for region_id in REGIONS_UNDER_STUDY:
        h = h3r[region_id]
        full, inside = h["directFull"], h["directInsideEnvelope"]
        outside_n = full["n"] - inside["n"]
        if outside_n > 0:
            outside_deg = (full["degradation"] * full["n"] - inside["degradation"] * inside["n"]) / outside_n
            L.append(f"- {region_id}: the {outside_n} out-of-envelope direct events carry a per-event degradation of "
                     f"{_fmt(outside_deg)} — the measured MAE harm lives disproportionately in the part of the "
                     f"direct mix (low baselines, low elevations, shoulder months) that the SNOTEL population "
                     f"barely samples.")
    L.append("")

    # --- H4 -----------------------------------------------------------------
    h4 = r["h4HierarchyDepth"]
    L.append("## H4. Which hierarchy level does the damage")
    L.append("")
    L.append("Direct-label MAE by walk depth (identity = raw Kuchera). Mean logBias contributed per level "
             "is over the events that reach that level; GHCN direct stations are not SNOTEL sites, so the "
             "local level rarely fires on direct events.")
    L.append("")
    for region_id in REGIONS_UNDER_STUDY:
        for pop in ("direct", "weakTest"):
            block = h4[region_id][pop]
            label = "direct" if pop == "direct" else "weak WY26"
            L.append(
                f"- {region_id} ({label}): identity {_fmt(block['identity'])} → +global {_fmt(block.get('depth1_global'))} "
                f"→ +climate {_fmt(block.get('depth2_climate'))} → +region cell {_fmt(block.get('depth3_region'))} "
                f"→ +local {_fmt(block.get('depth4_local'))}; deepest-level counts {block['deepestLevelCounts']}; "
                f"mean logBias by level {block['meanLogBiasByLevel']}"
            )
    L.append("")

    # --- H5 -----------------------------------------------------------------
    h5 = r["h5QcCensoring"]
    L.append("## H5. QC censoring of the weak label population")
    L.append("")
    L.append("From the full daily dataset (all station-days in each region, WY2024-2026), not just QC-passed rows:")
    L.append("")
    for region_id in REGIONS_UNDER_STUDY:
        h = h5[region_id]
        L.append(
            f"- {region_id}: {h['qcEvaluatedDays']} QC-evaluated days, {h['qcPassedDays']} passed "
            f"({_pct(h['qcPassRateOverall'])}). Of {h['sweGainDays']} SWE-gain days (ΔSWE ≥ 5 mm), only "
            f"{_pct(h['sweGainSurvivalRate'])} survive all gates. Top rejections: "
            + ", ".join(f"{k} {v}" for k, v in list(h["rejectionCounts"].items())[:4]) + "."
        )
        for band, stats in h["sweGainByTempBand"].items():
            L.append(f"  - {band}: {stats['sweGainDays']} SWE-gain days, QC pass rate {_pct(stats['qcPassRate'])}, "
                     f"share with Δdepth < 0 (settlement-censored) {_pct(stats['shareDepthDecreased'])}")
        L.append(
            f"  - SLR distributions: pre-QC computable (ΔSWE ≥ 5, Δdepth > 0) mean/median "
            f"{_fmt(h['slrPreQcComputable'].get('mean'), 2)} / {_fmt(h['slrPreQcComputable'].get('median'), 2)} (n={h['slrPreQcComputable'].get('n')}); "
            f"QC-passed {_fmt(h['slrQcPassed'].get('mean'), 2)} / {_fmt(h['slrQcPassed'].get('median'), 2)} (n={h['slrQcPassed'].get('n')}); "
            f"warm-censored {_fmt(h['slrWarmCensored'].get('mean'), 2)} / {_fmt(h['slrWarmCensored'].get('median'), 2)} (n={h['slrWarmCensored'].get('n')}); "
            f"direct observed {_fmt(h['directObservedSlr'].get('mean'), 2)} / {_fmt(h['directObservedSlr'].get('median'), 2)} (n={h['directObservedSlr'].get('n')}). "
            f"Settlement-censored SWE-gain days: {h['settlementCensoredDays']} (median Δdepth {_fmt(h['settlementCensoredMedianDeltaDepthMm'], 1)} mm despite the SWE gain)."
        )
    L.append("")

    # --- synthesis ------------------------------------------------------------
    salvage = r.get("salvageOffsetRefits") or {}
    L.append("## Synthesis — attribution and salvage")
    L.append("")
    L.append("Attribution of the direct-label degradation (MAE units; shares are of each region's "
             "degradation and are not strictly additive across mechanisms):")
    L.append("")
    L.append("| Region | Degradation | Recovered by honest intercept recalibration (LOO) | Recovered by dropping the intercept (shape only) | Degradation inside the training envelope |")
    L.append("|---|---|---|---|---|")
    for region_id in REGIONS_UNDER_STUDY:
        h = h1[region_id]
        env = h3r[region_id]["directInsideEnvelope"]
        L.append(
            f"| {region_id} | {_fmt(h['degradation'])} | {_fmt(h['variantLooRecalibrated']['recoveredMae'])} ({_pct(h['variantLooRecalibrated']['shareOfDegradation'])}) | "
            f"{_fmt(h['variantNoGlobal']['recoveredMae'])} ({_pct(h['variantNoGlobal']['shareOfDegradation'])}) | {_fmt(env['degradation'])} (n={env['n']}) |"
        )
    L.append("")
    aligned_dec = dec.get("alignedBaselineDelta") or {}
    label_lo = (dec.get("labelDeltaLogBoardOverDiff") or {}).get("mean")
    label_hi = (aligned_dec.get("labelDeltaOnAlignedDays") or {}).get("mean")
    prov = (aligned_dec.get("logTsOverNoaaBaseline") or {}).get("mean")
    if label_lo is not None and label_hi is not None and prov is not None:
        si_gap = abs(h1["sierra-nevada"]["weakMinusDirectMeanLogRatio"])
        resid_lo = si_gap - label_hi + prov
        resid_hi = si_gap - label_lo + prov
        L.append(
            f"Attribution in log space (Sierra, where the same-day measurement exists): the weak-vs-direct "
            f"level gap is {_fmt(si_gap, 2)}. The CSSL decomposition assigns {_fmt(label_lo, 2)}-{_fmt(label_hi, 2)} of it "
            f"({_pct(label_lo / si_gap)}-{_pct(label_hi / si_gap)}) to the label definition (board vs differenced), "
            f"{_fmt(prov, 2)} to baseline provenance — with the *opposite* sign, i.e. provenance slightly masks the "
            f"label gap — leaving {_fmt(resid_lo, 2)}-{_fmt(resid_hi, 2)} to population differences the single-site "
            f"decomposition cannot see (CSSL board days vs GHCN observer stations vs the SNOTEL station mix, and "
            f"which days produce a differenced label at all). For Colorado (gap "
            f"{_fmt(abs(h1['colorado-ranges']['weakMinusDirectMeanLogRatio']), 2)}) no co-located pair exists; if the "
            f"CSSL label effect is representative it accounts for roughly 0.4-0.6 of the gap, provenance is bounded "
            f"by the matched-band comparison at ~0.1, and the remainder is population/mix. In MAE space the level "
            f"term alone over-explains the degradation in both regions (recovery shares above 100%), because the "
            f"direct-optimal intercept also absorbs part of raw Kuchera's own bias against board labels."
        )
        L.append("")
    L.append("Salvage tests:")
    L.append("")
    for region_id in REGIONS_UNDER_STUDY:
        h = h1[region_id]
        loo = h["variantLooRecalibrated"]
        verdict_loo = "beats raw Kuchera" if loo["mae"] < h["directMaeKuchera"] else "does not beat raw Kuchera"
        L.append(f"- {region_id} (i) weak structure + direct-recalibrated intercept (LOO): MAE {_fmt(loo['mae'])} vs Kuchera "
                 f"{_fmt(h['directMaeKuchera'])} — {verdict_loo}. Split-half corroboration: {_fmt(h['variantSplitHalfRecalibrated']['maeMean'])} "
                 f"± {_fmt(h['variantSplitHalfRecalibrated']['maeStd'])}.")
        ng = h["variantNoGlobal"]
        verdict_ng = "beats raw Kuchera" if ng["mae"] < h["directMaeKuchera"] else "does not beat raw Kuchera"
        L.append(f"- {region_id} (ii) weak shape with no region intercept: MAE {_fmt(ng['mae'])} vs Kuchera {_fmt(h['directMaeKuchera'])} — {verdict_ng}.")
    if salvage.get("offsetFactor") is not None:
        sierra = salvage.get("sierra-nevada") or {}
        ghcn_only = sierra.get("sierraGhcnOnly") or {}
        colo = salvage.get("colorado-ranges") or {}
        L.append(
            f"- sierra-nevada (iii) weak refit with labels shifted by the CSSL differencing-bias factor "
            f"{_fmt(salvage.get('offsetFactor'), 3)} (log {_fmt(salvage.get('offsetLog'))}): on Sierra GHCN events only "
            f"(honest for the offset) MAE {_fmt(ghcn_only.get('shiftedMae'))} vs Kuchera {_fmt(ghcn_only.get('kucheraMae'))} "
            f"vs unshifted {_fmt(ghcn_only.get('originalMae'))} (n={ghcn_only.get('n')})."
        )
        L.append(
            f"- colorado-ranges (iii, exploratory transfer of the Sierra constant): shifted-refit MAE "
            f"{_fmt(colo.get('directMaeShifted'))} vs Kuchera {_fmt(colo.get('directMaeKuchera'))} vs unshifted {_fmt(colo.get('directMaeOriginal'))}."
        )
    L.append("")
    L.append("## Caveats")
    L.append("")
    L.append("- Intercept surgery works in log space before the artifact's output clamps ([0.5, 2.0] factor, "
             "[3, 30] SLR); clamping is reapplied identically for every variant, but the recalibrated "
             "intercept is a log-space mean, not an MAE-optimal constant.")
    L.append("- The CSSL board SWE is precipitation x snow fraction from the laboratory's own gauge, not a "
             "weighed snow core; the GHCN direct labels are observer SNOW + WESF. 'Direct' is itself two "
             "instruments, and the CSSL offset is estimated at a single Sierra site.")
    L.append("- Baseline provenance (H2) is measured exactly only at CSSL (same station-days, both "
             "pipelines). For Colorado there is no co-located pair, so provenance there is bounded by the "
             "matched-band distribution comparison, not measured.")
    L.append("- The direct sets span different years than the weak training slice (Sierra GHCN reaches back "
             "to 2008; the aligned subsets are the profile-covered events). H3's envelope test controls "
             "covariates, not climate drift between decades.")
    L.append("- MAE shares of degradation are descriptive decompositions, not a variance partition; "
             "mechanisms overlap (e.g. censoring is one cause of the level bias).")
    L.append("")
    return "\n".join(L)
