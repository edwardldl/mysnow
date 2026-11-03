"""Frozen split manifests and a keyed prediction cache for judged experiments.

Queue item 1 (see data/.../model-v3-queue1/DESIGN.md).  The v3 judging
stored aggregate metrics only, so paired candidate-vs-incumbent judging
was impossible without refitting the incumbent on every experiment.
This module freezes the judging splits as materialised manifests and
stores per-event fold predictions under a content key:

    {cohort hash, feature-set hash, split id, manifest hash,
     estimator spec, EB spec, fit seed, library versions}

Retrieval recomputes the key from the request spec and returns
predictions only on exact key equality; any mismatch is a miss.
Derived models (ensembles, tail-shrink) are resolved from cached
components at read time and are never stored as if fitted.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

REGIONS: tuple[str, ...] = (
    "alaska",
    "great-lakes",
    "northeast",
    "pacific-northwest",
    "rockies",
    "sierra-nevada",
)
RANDOM_REPS = 20

# Canonical EB spec for the hierarchical log-residual layer, mirroring
# trainer.TrainingConfig defaults used by RFEBModel / GBMEBModel.
EB_HIERARCHICAL: dict[str, Any] = {
    "kind": "hierarchical-log-residual-eb",
    "prior_ess": 30.0,
    "min_ess": 5.0,
    "factor_clamp": [0.5, 2.0],
    "slr_clamp": [3.0, 30.0],
    "region_resolution": 4,
    "climate_resolution": 2,
}

_RF_SPEC = "RandomForestRegressor(n_estimators=500,sklearn_defaults)"
_GBM_SPEC = "HistGradientBoostingRegressor(sklearn_defaults)"

MODEL_SPECS: dict[str, dict[str, Any]] = {
    "rf": {"kind": "fitted", "estimator": _RF_SPEC, "eb": None},
    "gbm": {"kind": "fitted", "estimator": _GBM_SPEC, "eb": None},
    "rf_eb": {"kind": "fitted", "estimator": _RF_SPEC, "eb": EB_HIERARCHICAL},
    "gbm_eb": {"kind": "fitted", "estimator": _GBM_SPEC, "eb": EB_HIERARCHICAL},
    "kuchera": {"kind": "fitted", "estimator": "baseline_slr(column)", "eb": None},
    "ens_eb": {"kind": "derived", "rule": "mean", "derived_from": ("rf_eb", "gbm_eb")},
    "ens_rg": {"kind": "derived", "rule": "mean", "derived_from": ("rf_eb", "gbm")},
    "tail_rf_eb": {"kind": "derived", "rule": "tail_shrink", "derived_from": ("rf", "rf_eb")},
}


def library_versions() -> dict[str, str]:
    import scipy
    import sklearn

    return {
        "sklearn": sklearn.__version__,
        "scipy": scipy.__version__,
        "numpy": np.__version__,
        "pandas": pd.__version__,
    }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cohort_hash(features_path: Path) -> str:
    """SHA-256 of the features parquet file bytes."""
    h = hashlib.sha256()
    with open(features_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def feature_hash(columns: Iterable[str]) -> str:
    return sha256_bytes(json.dumps(list(columns)).encode("utf-8"))


def split_instances(
    regions: tuple[str, ...] = REGIONS, random_reps: int = RANDOM_REPS
) -> list[dict[str, Any]]:
    """The frozen split-instance table (ids, families, fit seeds)."""
    instances: list[dict[str, Any]] = []
    for rep in range(random_reps):
        instances.append(
            {"split_id": f"random60_40_rep{rep}", "family": "randomised",
             "fit_seed": 1000 + rep, "rep": rep}
        )
    for region in regions:
        instances.append(
            {"split_id": f"loro_{region}", "family": "loro",
             "fit_seed": 2000, "region": region}
        )
    instances.append(
        {"split_id": "temporalWY2026", "family": "temporal", "fit_seed": 3000}
    )
    instances.append(
        {"split_id": "unseenStations", "family": "stations", "fit_seed": 4000}
    )
    return instances


def build_manifests(frame: pd.DataFrame, instances: list[dict[str, Any]]) -> dict[str, Any]:
    """Materialise test membership for every split instance.

    Uses gold_model's own split functions on the given modelling frame,
    so membership is exactly what the judging runs used.  Returns
    {split_id: {family, fit_seed, train_n, test_n, test_event_ids}}.
    """
    from . import gold_model

    manifests: dict[str, Any] = {}
    for inst in instances:
        split_id = inst["split_id"]
        if inst["family"] == "randomised":
            train, test = gold_model.split_random(frame, seed=1000 + inst["rep"])
        elif inst["family"] == "loro":
            train, test = gold_model.split_loro(frame, inst["region"])
        elif inst["family"] == "temporal":
            train, test = gold_model.split_temporal(frame)
        elif inst["family"] == "stations":
            train, test = gold_model.split_stations(frame, seed=4000)
        else:  # pragma: no cover - defensive
            raise ValueError(f"unknown family {inst['family']}")
        manifests[split_id] = {
            "family": inst["family"],
            "fit_seed": inst["fit_seed"],
            "train_n": int(len(train)),
            "test_n": int(len(test)),
            "test_event_ids": [str(e) for e in test["event_id"].tolist()],
        }
    return manifests


def manifest_hash(manifests: dict[str, Any]) -> str:
    payload = {
        split_id: entry["test_event_ids"] for split_id, entry in sorted(manifests.items())
    }
    return sha256_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"))


class PredictionCache:
    """Parquet-backed store of per-event fold predictions, keyed by spec."""

    def __init__(
        self,
        root: Path,
        *,
        cohort: str,
        features: str,
        manifests: str,
        libraries: dict[str, str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.cohort = cohort
        self.features = features
        self.manifests = manifests
        self.libraries = libraries if libraries is not None else library_versions()

    # -- key handling ----------------------------------------------------
    def key_payload(self, model: str, split_id: str, seed: int) -> dict[str, Any]:
        spec = MODEL_SPECS[model]
        if spec["kind"] != "fitted":
            raise ValueError(f"model {model} is derived; it has no cache key")
        return {
            "cohort_sha256": self.cohort,
            "feature_sha256": self.features,
            "split_id": split_id,
            "manifest_sha256": self.manifests,
            "estimator": spec["estimator"],
            "eb": spec["eb"],
            "seed": int(seed),
            "libraries": self.libraries,
        }

    def key_for(self, model: str, split_id: str, seed: int) -> str:
        payload = self.key_payload(model, split_id, seed)
        return sha256_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"))

    def _paths(self, key: str) -> tuple[Path, Path]:
        sub = self.root / key[:2]
        return sub / f"{key}.parquet", sub / f"{key}.json"

    # -- storage ---------------------------------------------------------
    def put(
        self,
        model: str,
        split_id: str,
        seed: int,
        event_ids: list[str],
        predictions: np.ndarray,
        *,
        origin: str = "fitted",
    ) -> str:
        key = self.key_for(model, split_id, seed)
        pq_path, json_path = self._paths(key)
        pq_path.parent.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(
            {"event_id": list(event_ids), "prediction": np.asarray(predictions, dtype=float)}
        )
        frame.to_parquet(pq_path, index=False)
        record = {
            "key": key,
            "payload": self.key_payload(model, split_id, seed),
            "model": model,
            "split_id": split_id,
            "n": int(len(frame)),
            "origin": origin,
        }
        json_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return key

    def get(
        self, model: str, split_id: str, seed: int, expected_event_ids: list[str]
    ) -> np.ndarray | None:
        """Predictions aligned to expected_event_ids, or None on any mismatch."""
        spec = MODEL_SPECS[model]
        if spec["kind"] == "derived":
            return self._resolve_derived(model, split_id, seed, expected_event_ids)
        key = self.key_for(model, split_id, seed)
        pq_path, json_path = self._paths(key)
        if not pq_path.exists() or not json_path.exists():
            return None
        record = json.loads(json_path.read_text(encoding="utf-8"))
        # Exact-key discipline: the stored payload must rehash to the
        # stored key, and the stored key must equal the requested key.
        rehashed = sha256_bytes(
            json.dumps(record["payload"], sort_keys=True).encode("utf-8")
        )
        if rehashed != record["key"] or record["key"] != key:
            return None
        frame = pd.read_parquet(pq_path)
        stored_ids = [str(e) for e in frame["event_id"].tolist()]
        if stored_ids != list(expected_event_ids):
            return None
        return frame["prediction"].to_numpy(dtype=float)

    def _resolve_derived(
        self, model: str, split_id: str, seed: int, expected_event_ids: list[str]
    ) -> np.ndarray | None:
        spec = MODEL_SPECS[model]
        parts: list[np.ndarray] = []
        for component in spec["derived_from"]:
            values = self.get(component, split_id, seed, expected_event_ids)
            if values is None:
                return None
            parts.append(values)
        if spec["rule"] == "mean":
            return (parts[0] + parts[1]) / 2.0
        if spec["rule"] == "tail_shrink":
            from .gold_model import tail_shrink_predictions

            return tail_shrink_predictions(parts[0], parts[1])
        raise ValueError(f"unknown derived rule {spec['rule']}")  # pragma: no cover
