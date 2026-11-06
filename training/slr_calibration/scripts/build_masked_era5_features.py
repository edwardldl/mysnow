"""Correction study, diagnostic stage: ERA5-masked feature build.

Builds, for the item-7 reduced cohort's 5,340 complete paired events, an
ERA5 feature set in which the convective-split source variables
(cp / lsp) are absent from the accumu series - represented EXACTLY as
the production HRRR adapter represents their absence (every hour's
cp_mm / lsp_mm is None, tp_mm untouched), passed through the standard
v3 builders (v3_features.build_event_v3_features). This is the L_E,m arm
of the masked-ERA5 decomposition prescribed by Codex round 9:

    L_H - L_E = (L_E,m - L_E) + (L_H - L_E,m)

Proof obligations discharged here, before any scoring:
* PARITY: the same rebuild WITHOUT masking reproduces the stored
  features_v3.parquet derived features for these events exactly.
* LOCALITY: the masked rebuild differs from the unmasked rebuild only
  in the regime bundle (convective_frac, lsp_total_mm, cp_total_mm),
  whose masked values are None.

Output: model-v3-hrrr-correction/masked/features_hrrr_v3.parquet in the
item-7 runner's arm-parquet schema (so the item runner's own stage_score
can score it unchanged), plus masked_build_summary.json.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "training" / "slr_calibration" / "src"))

from slr_calibration.gold_profiles import ERA5_SPEC  # noqa: E402
from slr_calibration.gold_profiles_sp import load_modelling_events  # noqa: E402
from slr_calibration.gold_profiles_v3 import V3_DIR_NAME  # noqa: E402
from slr_calibration.gold_profiles_v3_sfc import SFC_FIELDS  # noqa: E402
from slr_calibration.pressure_features import _load_profile_hours  # noqa: E402
from slr_calibration.v3_features import (  # noqa: E402
    DERIVED_FEATURES,
    _ACCUMU_FIELDS,
    _load_pl_chunks,
    _load_series_chunks,
    build_event_v3_features,
)

BASE = REPO / "data" / "slr-training-data" / "screened-v2" / "model-v1"
POOL = REPO / "data" / "slr-training-data" / "screened-v2"
FEATURES = BASE / "model-v3-final" / "features_v3.parquet"
V2_FEATURES = BASE / "era5-expanded-v2" / "features.parquet"
REDUCED_FEATURES = BASE / "model-v3-hrrr-transfer" / "reduced" / "features_hrrr_v3.parquet"
OUT = BASE / "model-v3-hrrr-correction" / "masked"

REGIME = ["convective_frac", "lsp_total_mm", "cp_total_mm"]


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def _missing(v: Any) -> bool:
    if v is None:
        return True
    try:
        import math

        return isinstance(v, float) and math.isnan(v)
    except (TypeError, ValueError):
        return False


def _close(a: Any, b: Any) -> bool:
    if _missing(a) and _missing(b):
        return True
    if _missing(a) or _missing(b):
        return False
    try:
        return abs(float(a) - float(b)) <= 1e-9
    except (TypeError, ValueError):
        return a == b


def main() -> int:
    reduced = pd.read_parquet(REDUCED_FEATURES)
    complete_ids = [str(e) for e in reduced.loc[reduced["hrrr_complete"], "event_id"]]
    lead_band = {
        str(r.event_id): str(r.lead_band)
        for r in reduced[["event_id", "lead_band"]].itertuples(index=False)
    }
    log(f"reduced complete paired events: {len(complete_ids)}")
    wanted = set(complete_ids)

    era = pd.read_parquet(FEATURES)
    era_by_id = era.set_index("event_id")
    missing = [e for e in complete_ids if e not in era_by_id.index]
    if missing:
        raise RuntimeError(f"{len(missing)} reduced events absent from ERA5 frame")
    status_counts = era_by_id.loc[complete_ids, "v3_status"].value_counts().to_dict()
    log(f"ERA5 v3_status of the cohort: {status_counts}")

    events = load_modelling_events(POOL, V2_FEATURES)
    events = [e for e in events if str(e["event_id"]) in wanted]
    if len(events) != len(complete_ids):
        raise RuntimeError(f"modelling events {len(events)} != cohort {len(complete_ids)}")
    by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        by_station[str(e["station_id"])].append(e)
    log(f"cohort spans {len(by_station)} stations")

    v3_dir = BASE / V3_DIR_NAME
    parity_bad: list[tuple[str, str, Any, Any]] = []
    parity_checked = 0
    diff_features: dict[str, int] = defaultdict(int)
    masked_nonnull_regime = 0
    masked_by_id: dict[str, dict[str, Any]] = {}
    counts_bad: list[str] = []

    for si, station_id in enumerate(sorted(by_station)):
        profile_hours = _load_profile_hours(BASE / ERA5_SPEC.cache_dir_name, station_id)
        w_series = _load_pl_chunks(v3_dir / "pl_chunks", station_id, "w")
        q_series = _load_pl_chunks(v3_dir / "pl_chunks_q", station_id, "q")
        accumu_series = _load_series_chunks(v3_dir / "accumu_chunks", station_id, _ACCUMU_FIELDS)
        sfc_series = _load_series_chunks(v3_dir / "sfc_chunks", station_id, SFC_FIELDS)
        masked_accumu = {
            instant: {"tp_mm": row.get("tp_mm"), "cp_mm": None, "lsp_mm": None}
            for instant, row in accumu_series.items()
        }
        for event in sorted(by_station[station_id], key=lambda e: e["event_id"]):
            eid = str(event["event_id"])
            unmasked, _c1 = build_event_v3_features(
                event, profile_hours, w_series, q_series, accumu_series, sfc_series
            )
            masked, counts = build_event_v3_features(
                event, profile_hours, w_series, q_series, masked_accumu, sfc_series
            )
            stored = era_by_id.loc[eid]
            for name in DERIVED_FEATURES:
                parity_checked += 1
                if not _close(unmasked[name], stored[name]):
                    parity_bad.append((eid, name, unmasked[name], stored[name]))
                if not _close(masked[name], unmasked[name]):
                    diff_features[name] += 1
            for name in REGIME:
                if masked[name] is not None:
                    masked_nonnull_regime += 1
            if counts["accumu"] != 24 or counts["profile"] != 24:
                counts_bad.append(eid)
            masked_by_id[eid] = masked
        if (si + 1) % 100 == 0:
            log(f"stations {si + 1}/{len(by_station)} done")

    log(f"parity: {parity_checked} derived values checked, {len(parity_bad)} mismatches")
    for bad in parity_bad[:10]:
        log(f"  parity mismatch: {bad}")
    log(f"masked-vs-unmasked differing features: {dict(diff_features)}")
    log(f"masked regime values non-None (must be 0): {masked_nonnull_regime}")
    log(f"events with masked accumu/profile counts != 24: {len(counts_bad)}")
    if parity_bad:
        raise RuntimeError("PARITY FAILED - masked build cannot be trusted")
    extra = set(diff_features) - set(REGIME)
    if extra:
        raise RuntimeError(f"masking changed features beyond the regime bundle: {extra}")
    if masked_nonnull_regime:
        raise RuntimeError("masked regime features are not all None")

    rows = []
    era_cols = [c for c in era.columns if c not in ("v3_status", "event_id")]
    for eid in complete_ids:
        row = {"event_id": eid}
        row.update({c: era_by_id.loc[eid, c] for c in era_cols})
        masked = masked_by_id[eid]
        for name in DERIVED_FEATURES:
            row[name] = masked[name]
        row["hrrr_source_counts"] = json.dumps(
            {"arm": "era5-masked-convective-split", "source": "era5-chunks-cp-lsp-none"}
        )
        row["lead_band"] = lead_band[eid]
        row["hrrr_complete"] = True
        rows.append(row)
    frame = pd.DataFrame(rows)
    # Mirror the reduced arm-parquet column order.
    order = [c for c in reduced.columns if c in frame.columns]
    frame = frame[order]
    OUT.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(OUT / "features_hrrr_v3.parquet", index=False)
    summary = {
        "arm": "era5-masked (L_E,m)",
        "n_events": len(complete_ids),
        "parity": {
            "derived_values_checked": parity_checked,
            "mismatches": len(parity_bad),
            "examples": [list(map(str, b)) for b in parity_bad[:10]],
        },
        "masked_diff_features": dict(diff_features),
        "masked_regime_nonnull": masked_nonnull_regime,
        "counts_not_24": len(counts_bad),
        "era5_v3_status_of_cohort": {str(k): int(v) for k, v in status_counts.items()},
    }
    (OUT / "masked_build_summary.json").write_text(json.dumps(summary, indent=2))
    log(f"wrote {OUT / 'features_hrrr_v3.parquet'} ({len(frame)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
