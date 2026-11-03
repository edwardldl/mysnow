"""Absolute-error GBM component for median experiment M (queue item 3).

Mirrors ``gold_model.GBMEBModel`` exactly, with two deliberate changes
(see data/.../model-v3-median/DESIGN.md):

* the tree is ``HistGradientBoostingRegressor(loss="absolute_error")``
  with every other setting identical to the incumbent GBM (sklearn
  defaults, ``random_state=seed``) — a conditional-median estimator
  for the MAE judging metric, in place of the squared-error mean;
* the EB layer fitted on the component's own station-grouped
  out-of-fold predictions is the **median** hierarchy
  (``median_eb.train_artifact_median``).

The existing mean EB (``trainer.train_artifact``) is fitted on the
same OOF residuals as well, purely for diagnostic D2; the promotion
candidate uses the median EB only.  This class is distinct from
``gold_model.MedianEBGBMModel``, which is an older bundle experiment
(quantile loss, 27-feature spec, per-region corrections).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .evaluation import apply_artifact
from .gold_model import (
    MAX_SLR,
    MIN_SLR,
    _apply_eb,
    _feature_columns,
    _station_grouped_folds,
    _trainer_rows,
)
from .median_eb import train_artifact_median
from .trainer import TrainingConfig, train_artifact

_SOURCE_LABEL = "screened-v2 gold events (queue item 3 median experiment)"


def _fit_ae_gbm(train: pd.DataFrame, seed: int, columns: list[str]):
    from sklearn.ensemble import HistGradientBoostingRegressor

    model = HistGradientBoostingRegressor(loss="absolute_error", random_state=seed)
    model.fit(train[columns].to_numpy(), train["observed_slr"].to_numpy())
    return model


class AbsoluteErrorGBMModel:
    """Median-objective GBM plus median-EB (and mean-EB for D2)."""

    def __init__(self, seed: int, columns: Any | None = None) -> None:
        self.seed = seed
        self.columns = _feature_columns(columns)

    def fit(self, train: pd.DataFrame) -> "AbsoluteErrorGBMModel":
        X = train[self.columns].to_numpy()
        y = train["observed_slr"].to_numpy()
        # Station-grouped 5-fold out-of-fold predictions for honest
        # residual fitting, exactly as GBMEBModel constructs them.
        folds = _station_grouped_folds(train, self.seed)
        oof = np.full(len(train), np.nan)
        for fold in range(5):
            mask = folds == fold
            if mask.sum() == 0 or (~mask).sum() == 0:
                continue
            model = _fit_ae_gbm(train[~mask], self.seed, self.columns)
            oof[mask] = model.predict(X[mask])
        if np.isnan(oof).any():
            oof[np.isnan(oof)] = train["baseline_slr"].to_numpy()[np.isnan(oof)]
        self.gbm = _fit_ae_gbm(train, self.seed, self.columns)
        residual_frame = train.copy()
        residual_frame["gbm_ae_oof"] = np.clip(oof, MIN_SLR, MAX_SLR)
        rows = _trainer_rows(residual_frame, baseline_column="gbm_ae_oof")
        self.eb_median_artifact = train_artifact_median(
            rows,
            TrainingConfig(
                artifact_id="gold-gbm-ae-median-eb-residual",
                source_label=_SOURCE_LABEL,
            ),
        )
        self.eb_mean_artifact = train_artifact(
            rows,
            TrainingConfig(
                artifact_id="gold-gbm-ae-mean-eb-residual",
                source_label=_SOURCE_LABEL,
            ),
        )
        return self

    def predict_base(self, test: pd.DataFrame) -> np.ndarray:
        """Clipped tree prediction, before any EB layer (diagnostic D1)."""
        X = test[self.columns].to_numpy()
        return np.clip(self.gbm.predict(X), MIN_SLR, MAX_SLR)

    def predict_parts(self, test: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """(clipped base prediction, median-EB-calibrated prediction)."""
        base = self.predict_base(test)
        frame = test.copy()
        frame["gbm_ae_pred"] = base
        return base, _apply_eb(self.eb_median_artifact, frame, baseline_column="gbm_ae_pred")

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        return self.predict_parts(test)[1]

    def predict_mean_eb(self, test: pd.DataFrame) -> np.ndarray:
        """Mean-EB-calibrated prediction (diagnostic D2)."""
        base = self.predict_base(test)
        frame = test.copy()
        frame["gbm_ae_pred"] = base
        return _apply_eb(self.eb_mean_artifact, frame, baseline_column="gbm_ae_pred")

    def hierarchy_median(self, test: pd.DataFrame) -> list[tuple[str, ...]]:
        """Apply-chain hierarchy per test row under the median EB.

        Used by diagnostic D3 to split events by whether the local
        (site) node was reached.  Mirrors gold_model._apply_eb's row
        construction, returning apply_artifact's hierarchy instead of
        its calibrated value.
        """
        base = self.predict_base(test)
        hierarchies: list[tuple[str, ...]] = []
        for i, record in enumerate(test.itertuples(index=False)):
            row = {
                "baseline_method": "kuchera",
                "latitude": float(record.latitude),
                "longitude": float(record.longitude),
                "site_id": record.site_id,
                "baseline_slr": float(base[i]),
            }
            hierarchies.append(apply_artifact(self.eb_median_artifact, row).hierarchy)
        return hierarchies
