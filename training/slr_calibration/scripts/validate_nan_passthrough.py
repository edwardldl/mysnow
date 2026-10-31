"""Design SS5 validation for the NaN pass-through serving policy.

The packaged ChampionModel refuses non-finite inputs by design, while
1,588 of the 57,376 training events carry NaN in >= 1 derived feature
(absent-layer semantics the trees consumed natively in training). The
pass-through policy may be adopted only if the packaged path, with the
finite-check relaxed, reproduces the harness composition exactly on
those rows.

Check performed here, on the model-v3 final feature table:
  Path A: ChampionModel.predict with champion.validate_event_frame
          monkeypatched to skip ONLY the finiteness clause (column
          contract still enforced).
  Path B: independent composition straight from the package contents --
          joblib.load of rf/gbm + gold_model._apply_eb over the sidecar's
          EB artifacts (the same composition the packaging build
          verification used on all 57,376 rows).
Rows: every feature-table row with >= 1 NaN among the 64 champion
features, plus a 500-row finite control sample. Pass criterion: max abs
diff <= 1e-9 on both sets and every prediction finite.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import slr_calibration.champion as champion_mod
from slr_calibration.champion import ChampionModel
from slr_calibration.gold_model import MAX_SLR, MIN_SLR, _apply_eb


def relaxed_validate(frame: pd.DataFrame, features: list[str], *, strict: bool = False) -> None:
    missing = [c for c in features if c not in frame.columns]
    if "region" not in frame.columns:
        missing.append("region")
    if missing:
        raise ValueError("missing columns: " + ", ".join(missing))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--champion-dir", required=True)
    ap.add_argument("--features", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    champion_dir = Path(args.champion_dir)
    sidecar = json.loads((champion_dir / "champion_sidecar.json").read_text())
    features: list[str] = sidecar["features"]

    frame = pd.read_parquet(args.features)
    values = frame[features].to_numpy(dtype=float)
    nan_rows = ~np.isfinite(values).all(axis=1)
    nan_idx = np.flatnonzero(nan_rows)
    finite_idx = np.flatnonzero(~nan_rows)
    rng = np.random.default_rng(20261031)
    control_idx = rng.choice(finite_idx, size=min(500, len(finite_idx)), replace=False)
    print(f"table rows={len(frame)} nan_rows={len(nan_idx)} control_rows={len(control_idx)}")

    model = ChampionModel.load(champion_dir)
    rf = joblib.load(champion_dir / "rf.joblib")
    gbm = joblib.load(champion_dir / "gbm.joblib")

    def path_a(sub: pd.DataFrame) -> np.ndarray:
        original = champion_mod.validate_event_frame
        champion_mod.validate_event_frame = relaxed_validate
        try:
            return np.asarray(model.predict(sub), dtype=float)
        finally:
            champion_mod.validate_event_frame = original

    def path_b(sub: pd.DataFrame) -> np.ndarray:
        prepared = sub.reset_index(drop=True).copy()
        if "site_id" not in prepared.columns:
            prepared["site_id"] = ""
        X = prepared[features].to_numpy(dtype=float)
        rf_frame = prepared.copy()
        rf_frame["rf_pred"] = np.clip(rf.predict(X), MIN_SLR, MAX_SLR)
        rf_eb = _apply_eb(model.rf_eb_artifact, rf_frame, baseline_column="rf_pred")
        gbm_frame = prepared.copy()
        gbm_frame["gbm_pred"] = np.clip(gbm.predict(X), MIN_SLR, MAX_SLR)
        gbm_eb = _apply_eb(model.gbm_eb_artifact, gbm_frame, baseline_column="gbm_pred")
        return (np.asarray(rf_eb, dtype=float) + np.asarray(gbm_eb, dtype=float)) / 2.0

    result: dict = {"nan_rows_in_table": int(len(nan_idx)), "tolerance": 1e-9}
    for name, idx in (("nan", nan_idx), ("finite_control", control_idx)):
        sub = frame.iloc[idx]
        a = path_a(sub)
        b = path_b(sub)
        diff = float(np.max(np.abs(a - b))) if len(idx) else 0.0
        result[name] = {
            "rows": int(len(idx)),
            "max_abs_diff": diff,
            "all_finite": bool(np.isfinite(a).all() and np.isfinite(b).all()),
            "pred_min": float(np.min(a)) if len(idx) else None,
            "pred_max": float(np.max(a)) if len(idx) else None,
        }
        print(name, result[name])
    result["verdict"] = (
        "PASS"
        if all(result[k]["max_abs_diff"] <= 1e-9 and result[k]["all_finite"]
               for k in ("nan", "finite_control"))
        else "FAIL"
    )
    Path(args.out).write_text(json.dumps(result, indent=1))
    print("verdict:", result["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
