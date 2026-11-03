"""Alpine external benchmark (queue item 4): Alta-Collins + SLF pairs.

MEASUREMENT ONLY.  This module forms gold-definition new-snow /
new-snow-water pairs from two independently measured mountain
datasets, applies the gold hygiene screens, and scores packaged
models on the paired ERA5 feature rows built by
``alpine_profiles``.  Nothing here trains anything; training on
these data is a separate, unauthorised step (see the benchmark
DESIGN.md, pre-registered before any download).

Sources
-------
* Alta-Collins (CLN), Utah, 2,945 m -- Wasserstein & Steenburgh,
  Hive (University of Utah), DOI 10.7278/S50d-nsy5-8bje,
  **CC BY-NC 4.0**.  The CSV's ``Time (LST)`` is the START of a
  12 h observing period (dataset readme); records start at 04:00
  and 16:00 LST.  LST is the dataset's stated convention and is
  taken as fixed UTC-07:00.  LPE was core-based "since at least
  2002"; earlier pairs are era-excluded.
* SLF manual sites, Switzerland -- EnviDat DOI
  10.16904/envidat.590 v1.0, **CC BY 4.0**.  HN (24 h new snow, cm)
  and HNW (its water equivalent, mm) share timestamps that are the
  END of the observation period in fixed CET (UTC+01:00);
  missing = -9999.

Pair SLR is always a ratio of sums / direct daily ratio of the
measured quantities -- never a mean of sub-period ratios.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants (pre-registered in the benchmark DESIGN.md)
# ---------------------------------------------------------------------------

ALTA_LICENSE = "CC BY-NC 4.0"
ALTA_DOI = "10.7278/S50d-nsy5-8bje"
ALTA_LATITUDE = 40.57607
ALTA_LONGITUDE = -111.63889
ALTA_ELEVATION_M = 2945.0
ALTA_STATION_ID = "ALTA-CLN"
#: Window end must be on/after this LST instant (core-based LPE era).
ALTA_ERA_CUT_LST = datetime(2002, 1, 1, tzinfo=timezone(timedelta(hours=-7)))

SLF_LICENSE = "CC BY 4.0"
SLF_DOI = "10.16904/envidat.590"
SLF_MISSING = -9999.0

LST = timezone(timedelta(hours=-7), "LST")
CET = timezone(timedelta(hours=1), "CET")

SNOW_MIN_CM = 5.09
LIQUID_MIN_MM = 2.79
SLR_MIN = 2.0
SLR_MAX = 50.0

ALTA_MIN_VIABLE_PAIRS = 300
SLF_MIN_VIABLE_PAIRS_1500 = 1000
SLF_CONTROL_CAP = 2000

ALTA_RESERVED_SEASONS = (2022, 2023)
SLF_RESERVED_SEASONS = (2021, 2022)

PAIR_COLUMNS = (
    "pair_id", "source", "station_id", "station_name", "latitude",
    "longitude", "elevation_m", "stage", "window_start_utc",
    "window_end_utc", "window_end_local", "season_ending_year",
    "reserved", "snow_mm", "liquid_mm", "observed_slr", "license",
)


def season_ending_year(local_end: datetime) -> int:
    """Cool-season label (Oct 1 - Apr 30) by ending calendar year."""
    return local_end.year + 1 if local_end.month >= 10 else local_end.year


def apply_hygiene(
    snow_cm: float, liquid_mm: float
) -> tuple[float | None, str | None, bool]:
    """Gold-style screens at the 24 h pair level.

    Returns (slr, reject_reason, is_exact10).  Rejection reasons are
    counted by the callers' funnels.  Exact-10 ratios are KEPT
    (documented deviation: neither source uses the GHCN placeholder
    convention) and merely flagged.
    """
    if snow_cm < SNOW_MIN_CM:
        return None, "snow_below_floor", False
    if liquid_mm < LIQUID_MIN_MM:
        return None, "liquid_below_floor", False
    slr = (snow_cm * 10.0) / liquid_mm
    if slr < SLR_MIN or slr > SLR_MAX:
        return None, "slr_out_of_range", False
    return slr, None, slr == 10.0


# ---------------------------------------------------------------------------
# Alta-Collins
# ---------------------------------------------------------------------------


def parse_alta_records(path: Path) -> tuple[dict[datetime, tuple[float | None, float | None]], Counter]:
    """Parse the CLN CSV into {period-start (LST): (snow_in, lpe_in)}."""
    stats: Counter = Counter()
    records: dict[datetime, tuple[float | None, float | None]] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        time_col = next((f for f in fields if f.strip().startswith("Time")), None)
        snow_col = next((f for f in fields if "Snow" in f), None)
        lpe_col = next((f for f in fields if "LPE" in f), None)
        if not (time_col and snow_col and lpe_col):
            raise ValueError(f"Alta CSV has unexpected header: {fields}")
        for row in reader:
            stats["rows"] += 1
            try:
                start = datetime.strptime(row[time_col].strip(), "%Y-%m-%d %H")
            except (ValueError, AttributeError):
                stats["unparseable_time"] += 1
                continue
            start = start.replace(tzinfo=LST)
            if start in records:
                stats["duplicate_start"] += 1
                continue

            def _val(col: str) -> float | None:
                raw = (row.get(col) or "").strip()
                if not raw:
                    return None
                try:
                    value = float(raw)
                except ValueError:
                    return None
                return value if math.isfinite(value) else None

            records[start] = (_val(snow_col), _val(lpe_col))
    return records, stats


def form_alta_pairs(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Form screened 24 h pairs ending 04:00 LST from the CLN CSV."""
    records, stats = parse_alta_records(path)
    funnel: Counter = Counter(stats)
    pairs: list[dict[str, Any]] = []
    for start in sorted(records):
        if start.hour != 4:
            continue
        funnel["candidate_windows"] += 1
        first = records[start]
        second = records.get(start + timedelta(hours=12))
        if second is None or any(v is None for v in (*first, *second)):
            funnel["incomplete_pair"] += 1
            continue
        if any(v < 0 for v in (*first, *second)):
            funnel["negative_value"] += 1
            continue
        end_lst = start + timedelta(hours=24)
        if end_lst < ALTA_ERA_CUT_LST:
            funnel["era_excluded_pre2002"] += 1
            continue
        snow_cm = (first[0] + second[0]) * 2.54
        liquid_mm = (first[1] + second[1]) * 25.4
        slr, reason, exact10 = apply_hygiene(snow_cm, liquid_mm)
        if reason is not None:
            funnel[reason] += 1
            continue
        if exact10:
            funnel["exact10_kept"] += 1
        season = season_ending_year(end_lst)
        pairs.append({
            "pair_id": f"alta-cln-{end_lst:%Y%m%d}",
            "source": "alta",
            "station_id": ALTA_STATION_ID,
            "station_name": "Alta-Collins (CLN)",
            "latitude": ALTA_LATITUDE,
            "longitude": ALTA_LONGITUDE,
            "elevation_m": ALTA_ELEVATION_M,
            "stage": "single",
            "window_start_utc": (start.astimezone(timezone.utc)).isoformat(),
            "window_end_utc": (end_lst.astimezone(timezone.utc)).isoformat(),
            "window_end_local": end_lst.isoformat(),
            "season_ending_year": season,
            "reserved": season in ALTA_RESERVED_SEASONS,
            "snow_mm": round(snow_cm * 10.0, 4),
            "liquid_mm": round(liquid_mm, 4),
            "observed_slr": round(slr, 6),
            "license": ALTA_LICENSE,
        })
    funnel["accepted"] = len(pairs)
    return pairs, dict(funnel)


# ---------------------------------------------------------------------------
# SLF manual sites
# ---------------------------------------------------------------------------


def slf_stage(elevation_m: float) -> str:
    if elevation_m >= 1800.0:
        return "A"
    if elevation_m >= 1500.0:
        return "B"
    return "C"


def form_slf_pairs(extract_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Form screened daily HN/HNW pairs from the EnviDat v1 tables."""
    from .swiss import _finite, _read_observation_table, _read_stations

    extract_dir = Path(extract_dir)
    hn_stations, hn_rows = _read_observation_table(
        extract_dir / "HN" / "OBS-HN.txt", "new snow depth, observed", "cm")
    hnw_stations, hnw_rows = _read_observation_table(
        extract_dir / "HNW" / "OBS-HNW.txt", "new snow water equivalent, observed", "mm")
    stations = _read_stations(extract_dir / "STATION-LIST.txt")
    if hn_stations != hnw_stations or set(hn_stations) != set(stations):
        raise ValueError("HN, HNW, and station metadata station sets do not match")
    if len(hn_rows) != len(hnw_rows):
        raise ValueError("HN and HNW tables have different row counts")

    funnel: Counter = Counter()
    pairs: list[dict[str, Any]] = []
    for hn_row, hnw_row in zip(hn_rows, hnw_rows, strict=True):
        timestamp = hn_row["Time"]
        if timestamp != hnw_row["Time"]:
            raise ValueError("HN and HNW timestamps are not exactly aligned")
        end_cet = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CET)
        for station_id in hn_stations:
            funnel["cells"] += 1
            hn = _finite(hn_row[station_id])
            hnw = _finite(hnw_row[station_id])
            if hn == SLF_MISSING:
                hn = None
            if hnw == SLF_MISSING:
                hnw = None
            if hn is None and hnw is None:
                funnel["both_missing"] += 1
                continue
            if hn is None:
                funnel["hn_missing"] += 1
                continue
            if hnw is None:
                funnel["hnw_missing"] += 1
                continue
            if hn < 0 or hnw < 0:
                funnel["negative_value"] += 1
                continue
            slr, reason, exact10 = apply_hygiene(hn, hnw)
            if reason is not None:
                funnel[reason] += 1
                continue
            if exact10:
                funnel["exact10_kept"] += 1
            meta = stations[station_id]
            season = season_ending_year(end_cet)
            pairs.append({
                "pair_id": f"slf-{station_id}-{end_cet:%Y%m%d}",
                "source": "slf",
                "station_id": station_id,
                "station_name": station_id,
                "latitude": meta["latitude"],
                "longitude": meta["longitude"],
                "elevation_m": meta["elevation_m"],
                "stage": slf_stage(float(meta["elevation_m"])),
                "window_start_utc": (end_cet - timedelta(hours=24)).astimezone(timezone.utc).isoformat(),
                "window_end_utc": end_cet.astimezone(timezone.utc).isoformat(),
                "window_end_local": end_cet.isoformat(),
                "season_ending_year": season,
                "reserved": season in SLF_RESERVED_SEASONS,
                "snow_mm": round(hn * 10.0, 4),
                "liquid_mm": round(hnw, 4),
                "observed_slr": round(slr, 6),
                "license": SLF_LICENSE,
            })
    # Stage C control subsample (deterministic; stages A/B untouched).
    control = [p for p in pairs if p["stage"] == "C"]
    if len(control) > SLF_CONTROL_CAP:
        control_sorted = sorted(control, key=lambda p: (p["station_id"], p["window_end_utc"]))
        step = math.ceil(len(control_sorted) / SLF_CONTROL_CAP)
        kept = control_sorted[::step]
        funnel["control_subsample_excluded"] = len(control) - len(kept)
        kept_ids = {p["pair_id"] for p in kept}
        pairs = [p for p in pairs if p["stage"] != "C" or p["pair_id"] in kept_ids]
    else:
        funnel["control_subsample_excluded"] = 0
    funnel["accepted"] = len(pairs)
    funnel["accepted_ge1500"] = sum(1 for p in pairs if p["elevation_m"] >= 1500.0)
    return pairs, dict(funnel), stations


def load_pairs_csv(path: Path) -> list[dict[str, Any]]:
    """Read a pairs CSV back with numeric/bool coercions applied."""
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["latitude"] = float(row["latitude"])
        row["longitude"] = float(row["longitude"])
        row["elevation_m"] = float(row["elevation_m"])
        row["season_ending_year"] = int(row["season_ending_year"])
        row["reserved"] = row["reserved"] == "True"
        row["snow_mm"] = float(row["snow_mm"])
        row["liquid_mm"] = float(row["liquid_mm"])
        row["observed_slr"] = float(row["observed_slr"])
    return rows


def write_pairs_csv(pairs: list[dict[str, Any]], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(PAIR_COLUMNS))
        writer.writeheader()
        for pair in sorted(pairs, key=lambda p: (p["source"], p["station_id"], p["window_end_utc"])):
            writer.writerow({key: pair[key] for key in PAIR_COLUMNS})


def viability(alta_pairs: list[dict[str, Any]], slf_pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """The pre-registered kill condition, applied mechanically."""
    alta_n = len(alta_pairs)
    slf_1500 = sum(1 for p in slf_pairs if p["elevation_m"] >= 1500.0)
    return {
        "alta_pairs": alta_n,
        "alta_minimum": ALTA_MIN_VIABLE_PAIRS,
        "alta_viable": alta_n >= ALTA_MIN_VIABLE_PAIRS,
        "slf_pairs_ge1500m": slf_1500,
        "slf_minimum_ge1500m": SLF_MIN_VIABLE_PAIRS_1500,
        "slf_viable": slf_1500 >= SLF_MIN_VIABLE_PAIRS_1500,
        "benchmark_viable": alta_n >= ALTA_MIN_VIABLE_PAIRS or slf_1500 >= SLF_MIN_VIABLE_PAIRS_1500,
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

MODEL_LABELS = ("champion", "kuchera", "wall", "w1500")


def _metrics(rows: list[dict[str, Any]], pred_key: str) -> dict[str, Any]:
    """SLR + snowfall (observed-liquid) errors for one model column."""
    n = len(rows)
    if n == 0:
        return {"n": 0, "slr_mae": None, "slr_bias": None, "snowfall_mae_mm": None}
    slr_err = [float(r[pred_key]) - float(r["observed_slr"]) for r in rows]
    snow_err = [
        float(r[pred_key]) * float(r["liquid_mm"]) - float(r["snow_mm"]) for r in rows
    ]
    return {
        "n": n,
        "slr_mae": sum(abs(e) for e in slr_err) / n,
        "slr_bias": sum(slr_err) / n,
        "snowfall_mae_mm": sum(abs(e) for e in snow_err) / n,
    }


def _sign_test_p(rows: list[dict[str, Any]], key_a: str, key_b: str) -> float | None:
    """Two-sided paired sign test on |SLR error| (ties excluded)."""
    wins = losses = 0
    for r in rows:
        ea = abs(float(r[key_a]) - float(r["observed_slr"]))
        eb = abs(float(r[key_b]) - float(r["observed_slr"]))
        if ea < eb:
            wins += 1
        elif eb < ea:
            losses += 1
    if wins + losses == 0:
        return None
    from scipy.stats import binomtest

    return float(binomtest(wins, wins + losses, 0.5).pvalue)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Metric block for one slice of scored pairs."""
    out: dict[str, Any] = {}
    for label in MODEL_LABELS:
        out[label] = _metrics(rows, f"{label}_slr")
    out["champion_vs_kuchera_sign_test_p"] = _sign_test_p(rows, "champion_slr", "kuchera_slr")
    champ = out["champion"]["slr_mae"]
    kuch = out["kuchera"]["slr_mae"]
    out["champion_minus_kuchera_slr_mae"] = (
        champ - kuch if champ is not None and kuch is not None else None
    )
    return out


def score_benchmark(
    pairs: list[dict[str, Any]],
    features: Any,  # pd.DataFrame indexed/columned by pair_id
    champion_dir: Path,
    wall_dir: Path,
    w1500_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Score all four models on the identical champion-scored set.

    ``features`` must carry pair_id, the 64 champion feature columns,
    ``baseline_slr``, ``profile_status`` and the columns
    ``core_inputs_finite`` needs.  Returns (prediction rows, scoring
    summary dict).
    """
    import pandas as pd

    from .champion import ChampionModel
    from .model_serving import core_inputs_finite, predict_with_passthrough

    pair_by_id = {p["pair_id"]: p for p in pairs}
    champion = ChampionModel.load(Path(champion_dir))
    champ_features: list[str] = list(champion.sidecar["features"])

    frame = features.copy()
    frame["row_dict"] = frame.to_dict("records")
    scored_ids: list[str] = []
    funnel: Counter = Counter()
    for _, row in frame.iterrows():
        funnel["feature_rows"] += 1
        if row["pair_id"] not in pair_by_id:
            funnel["unknown_pair"] += 1
            continue
        if row["profile_status"] != "ok":
            funnel["profile_not_ok"] += 1
            continue
        if not core_inputs_finite(row["row_dict"], champ_features):
            funnel["core_inputs_not_finite"] += 1
            continue
        scored_ids.append(row["pair_id"])
    funnel["scored"] = len(scored_ids)
    if not scored_ids:
        return [], {"funnel": dict(funnel), "slices": {}}

    sub = frame[frame["pair_id"].isin(scored_ids)].set_index("pair_id").loc[scored_ids]
    regions = [("rockies" if pair_by_id[pid]["source"] == "alta" else "alpine-external")
               for pid in scored_ids]
    site_ids = [pair_by_id[pid]["station_id"] for pid in scored_ids]

    def _predict(model_dir: Path) -> dict[str, float]:
        model = ChampionModel.load(Path(model_dir))
        feats: list[str] = list(model.sidecar["features"])
        f = pd.DataFrame([{c: sub[c][pid] if c in sub.columns else None for c in feats}
                          for pid in scored_ids])
        f["region"] = regions
        f["site_id"] = site_ids
        preds = predict_with_passthrough(model, f)
        return {pid: float(p) for pid, p in zip(scored_ids, preds)}

    champ_pred = _predict(champion_dir)
    wall_pred = _predict(wall_dir)
    w1500_pred = _predict(w1500_dir)

    rows: list[dict[str, Any]] = []
    for pid in scored_ids:
        pair = pair_by_id[pid]
        rows.append({
            **{k: pair[k] for k in PAIR_COLUMNS},
            "champion_slr": champ_pred[pid],
            "kuchera_slr": float(sub["baseline_slr"][pid]),
            "wall_slr": wall_pred[pid],
            "w1500_slr": w1500_pred[pid],
        })

    slices: dict[str, Any] = {}

    def _add(name: str, subset: list[dict[str, Any]]) -> None:
        if subset:
            slices[name] = summarize(subset)

    _add("all", rows)
    for source in ("alta", "slf"):
        src_rows = [r for r in rows if r["source"] == source]
        _add(source, src_rows)
        _add(f"{source}:nonreserved", [r for r in src_rows if not r["reserved"]])
        _add(f"{source}:reserved", [r for r in src_rows if r["reserved"]])
        for station in sorted({r["station_id"] for r in src_rows}):
            _add(f"{source}:site:{station}", [r for r in src_rows if r["station_id"] == station])
        for stage in sorted({r["stage"] for r in src_rows}):
            _add(f"{source}:stage:{stage}", [r for r in src_rows if r["stage"] == stage])
    return rows, {"funnel": dict(funnel), "slices": slices}


def write_predictions_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        raise ValueError("no prediction rows to write")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(PAIR_COLUMNS) + [f"{label}_slr" for label in MODEL_LABELS]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r["source"], r["station_id"], r["window_end_utc"])):
            writer.writerow({key: row[key] for key in columns})


def dump_json(payload: Any, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
