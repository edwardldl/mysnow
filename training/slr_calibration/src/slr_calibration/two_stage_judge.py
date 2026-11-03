"""Two-stage judging on cached fold predictions (queue item 1).

Implements the pre-registered screen / full / qualification rules of
data/.../model-v3-queue1/DESIGN.md.  All statistics are computed from
per-event predictions aligned by event_id, so candidate and incumbent
are always compared on identical test events.

Family MAE definitions mirror the v3 judging exactly:
  randomised = mean of per-rep overall MAEs;
  LORO       = unweighted mean of the six per-region fold MAEs;
  temporal   = the temporalWY2026 fold MAE;
  stations   = the unseenStations fold MAE.
Gains are incumbent MAE - candidate MAE (positive = candidate better);
differences are candidate MAE - incumbent MAE (negative = better).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# Screen kill thresholds (DESIGN.md section 3).
SCREEN_LORO_GAIN_MIN = 0.02
SCREEN_REGION_DELTA_MIN = -0.05
SCREEN_SAMPLED_GAIN_MIN = -0.10
# Full adoption thresholds.
ADOPT_FOUR_FAMILY_GAIN_MIN = 0.03
ADOPT_LORO_GAIN_MIN = 0.02
ADOPT_REGION_DELTA_MIN = -0.05
ADOPT_RANDOM_GAIN_MIN = -0.02
SLICE_MIN_EVENTS = 200
SLICE_MAX_WORSENING = 0.20
# GBM-only qualification thresholds (differences, candidate - incumbent).
QUAL_FOUR_FAMILY_MAX = 0.02
QUAL_LORO_MAX = 0.00
QUAL_REGION_MAX = 0.05
QUAL_TEMPORAL_MAX = 0.03
QUAL_STATIONS_MAX = 0.03


def _mae(observed: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.mean(np.abs(predicted - observed)))


class Judge:
    """Computes family statistics and rule evaluations for cached predictions.

    `metadata` is a DataFrame indexed by event_id with columns
    observed_slr and region.  Predictions are passed as
    {split_id: pd.Series indexed by event_id}.
    """

    def __init__(self, metadata: pd.DataFrame, manifests: dict[str, Any]) -> None:
        self.metadata = metadata
        self.manifests = manifests

    # -- per-instance and family statistics ------------------------------
    def instance_mae(self, predictions: dict[str, pd.Series], split_id: str) -> float:
        series = predictions[split_id]
        observed = self.metadata.loc[series.index, "observed_slr"].to_numpy(dtype=float)
        return _mae(observed, series.to_numpy(dtype=float))

    def family_maes(self, predictions: dict[str, pd.Series]) -> dict[str, Any]:
        per_instance: dict[str, float] = {}
        families: dict[str, list[str]] = {}
        for split_id, entry in self.manifests.items():
            per_instance[split_id] = self.instance_mae(predictions, split_id)
            families.setdefault(entry["family"], []).append(split_id)
        family: dict[str, float] = {}
        for name, split_ids in families.items():
            if name == "loro":
                # Each LORO fold's test set is exactly one region, so the
                # unweighted mean of fold MAEs is the v3 definition.
                family[name] = float(np.mean([per_instance[s] for s in split_ids]))
            elif name == "randomised":
                family[name] = float(np.mean([per_instance[s] for s in split_ids]))
            else:
                family[name] = per_instance[split_ids[0]]
        loro_regions = {
            split_id.removeprefix("loro_"): per_instance[split_id]
            for split_id in families.get("loro", [])
        }
        four_family = float(
            np.mean([family["randomised"], family["loro"], family["temporal"], family["stations"]])
        )
        return {
            "per_instance": per_instance,
            "family": family,
            "loro_regions": loro_regions,
            "four_family_mean": four_family,
        }

    # -- comparisons -------------------------------------------------------
    def gains(
        self,
        incumbent: dict[str, pd.Series],
        candidate: dict[str, pd.Series],
    ) -> dict[str, Any]:
        inc = self.family_maes(incumbent)
        cand = self.family_maes(candidate)
        family_gain = {
            name: inc["family"][name] - cand["family"][name] for name in inc["family"]
        }
        region_gain = {
            region: inc["loro_regions"][region] - cand["loro_regions"][region]
            for region in inc["loro_regions"]
        }
        return {
            "incumbent": inc,
            "candidate": cand,
            "family_gain": family_gain,
            "four_family_gain": inc["four_family_mean"] - cand["four_family_mean"],
            "loro_region_gain": region_gain,
        }

    def screen(self, incumbent: dict[str, pd.Series], candidate: dict[str, pd.Series]) -> dict[str, Any]:
        """Screen stage: exact LORO + rep0/temporal/stations sampling."""
        inc, cand = incumbent, candidate
        pooled_loro_gain = self._loro_gain(inc, cand)
        region_gain = {
            region: self.instance_mae(inc, f"loro_{region}")
            - self.instance_mae(cand, f"loro_{region}")
            for region in self._regions()
        }
        sampled = {
            "randomised_rep0": self.instance_mae(inc, "random60_40_rep0")
            - self.instance_mae(cand, "random60_40_rep0"),
            "temporal": self.instance_mae(inc, "temporalWY2026")
            - self.instance_mae(cand, "temporalWY2026"),
            "stations": self.instance_mae(inc, "unseenStations")
            - self.instance_mae(cand, "unseenStations"),
        }
        scalar = float(
            np.mean([pooled_loro_gain, sampled["randomised_rep0"], sampled["temporal"], sampled["stations"]])
        )
        clauses: list[str] = []
        if pooled_loro_gain < SCREEN_LORO_GAIN_MIN:
            clauses.append(f"pooled LORO gain {pooled_loro_gain:.4f} < {SCREEN_LORO_GAIN_MIN}")
        worst_region = min(region_gain.values())
        if worst_region < SCREEN_REGION_DELTA_MIN:
            clauses.append(f"worst region gain {worst_region:.4f} < {SCREEN_REGION_DELTA_MIN}")
        for name, gain in sampled.items():
            if gain < SCREEN_SAMPLED_GAIN_MIN:
                clauses.append(f"sampled {name} gain {gain:.4f} < {SCREEN_SAMPLED_GAIN_MIN}")
        return {
            "pooled_loro_gain": pooled_loro_gain,
            "loro_region_gain": region_gain,
            "sampled_family_gain": sampled,
            "screen_scalar": scalar,
            "killed": bool(clauses),
            "kill_clauses": clauses,
        }

    def full(self, incumbent: dict[str, pd.Series], candidate: dict[str, pd.Series]) -> dict[str, Any]:
        """Full stage: the amended adoption rule of DESIGN.md section 3."""
        comp = self.gains(incumbent, candidate)
        fg = comp["family_gain"]
        clauses: list[str] = []
        if comp["four_family_gain"] < ADOPT_FOUR_FAMILY_GAIN_MIN:
            clauses.append(
                f"four-family gain {comp['four_family_gain']:.4f} < {ADOPT_FOUR_FAMILY_GAIN_MIN}"
            )
        if fg["loro"] < ADOPT_LORO_GAIN_MIN:
            clauses.append(f"pooled LORO gain {fg['loro']:.4f} < {ADOPT_LORO_GAIN_MIN}")
        worst_region = min(comp["loro_region_gain"].values())
        if worst_region < ADOPT_REGION_DELTA_MIN:
            clauses.append(f"worst region gain {worst_region:.4f} < {ADOPT_REGION_DELTA_MIN}")
        if fg["temporal"] < 0.0:
            clauses.append(f"temporal gain {fg['temporal']:.4f} < 0")
        if fg["stations"] < 0.0:
            clauses.append(f"stations gain {fg['stations']:.4f} < 0")
        if fg["randomised"] < ADOPT_RANDOM_GAIN_MIN:
            clauses.append(f"randomised gain {fg['randomised']:.4f} < {ADOPT_RANDOM_GAIN_MIN}")
        slices = self._slice_report(incumbent, candidate)
        for entry in slices:
            if entry["n"] >= SLICE_MIN_EVENTS and entry["mae_diff"] > SLICE_MAX_WORSENING:
                clauses.append(
                    f"slice {entry['family']}/{entry['slice']} worsens {entry['mae_diff']:.4f} "
                    f"> {SLICE_MAX_WORSENING} (n={entry['n']})"
                )
        comp["slices"] = slices
        comp["adopted"] = not clauses
        comp["failed_clauses"] = clauses
        return comp

    def qualification(
        self, incumbent: dict[str, pd.Series], candidate: dict[str, pd.Series]
    ) -> dict[str, Any]:
        """GBM-only qualification rule (DESIGN.md section 5); differences."""
        inc = self.family_maes(incumbent)
        cand = self.family_maes(candidate)
        diffs = {
            "four_family_mean": cand["four_family_mean"] - inc["four_family_mean"],
            "loro": cand["family"]["loro"] - inc["family"]["loro"],
            "temporal": cand["family"]["temporal"] - inc["family"]["temporal"],
            "stations": cand["family"]["stations"] - inc["family"]["stations"],
            "randomised": cand["family"]["randomised"] - inc["family"]["randomised"],
        }
        region_diffs = {
            region: cand["loro_regions"][region] - inc["loro_regions"][region]
            for region in inc["loro_regions"]
        }
        clauses: list[str] = []
        if diffs["four_family_mean"] > QUAL_FOUR_FAMILY_MAX:
            clauses.append(
                f"four-family diff {diffs['four_family_mean']:+.4f} > +{QUAL_FOUR_FAMILY_MAX}"
            )
        if diffs["loro"] > QUAL_LORO_MAX:
            clauses.append(f"pooled LORO diff {diffs['loro']:+.4f} > {QUAL_LORO_MAX:+.2f}")
        for region, diff in region_diffs.items():
            if diff > QUAL_REGION_MAX:
                clauses.append(f"region {region} diff {diff:+.4f} > +{QUAL_REGION_MAX}")
        if diffs["temporal"] > QUAL_TEMPORAL_MAX:
            clauses.append(f"temporal diff {diffs['temporal']:+.4f} > +{QUAL_TEMPORAL_MAX}")
        if diffs["stations"] > QUAL_STATIONS_MAX:
            clauses.append(f"stations diff {diffs['stations']:+.4f} > +{QUAL_STATIONS_MAX}")
        return {
            "incumbent": inc,
            "candidate": cand,
            "diffs": diffs,
            "loro_region_diffs": region_diffs,
            "qualified": not clauses,
            "failed_clauses": clauses,
        }

    # -- helpers -----------------------------------------------------------
    def _regions(self) -> list[str]:
        return [
            split_id.removeprefix("loro_")
            for split_id in self.manifests
            if split_id.startswith("loro_")
        ]

    def _loro_gain(
        self, incumbent: dict[str, pd.Series], candidate: dict[str, pd.Series]
    ) -> float:
        gains = [
            self.instance_mae(incumbent, f"loro_{region}")
            - self.instance_mae(candidate, f"loro_{region}")
            for region in self._regions()
        ]
        return float(np.mean(gains))

    def _slice_report(
        self, incumbent: dict[str, pd.Series], candidate: dict[str, pd.Series]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for split_id, family in (("temporalWY2026", "temporal"), ("unseenStations", "stations")):
            for name, mask_fn in (
                ("slr_lt_5", lambda obs: obs < 5),
                ("slr_gt_20", lambda obs: obs > 20),
            ):
                series_inc = incumbent[split_id]
                series_cand = candidate[split_id].reindex(series_inc.index)
                obs = self.metadata.loc[series_inc.index, "observed_slr"].to_numpy(dtype=float)
                mask = mask_fn(obs)
                entry = {"family": family, "slice": name, "n": int(mask.sum())}
                if mask.sum():
                    entry["mae_diff"] = _mae(obs[mask], series_cand.to_numpy(dtype=float)[mask]) - _mae(
                        obs[mask], series_inc.to_numpy(dtype=float)[mask]
                    )
                else:
                    entry["mae_diff"] = 0.0
                out.append(entry)
        return out


def spearman(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation (average ranks for ties)."""
    def ranks(values: list[float]) -> np.ndarray:
        order = np.argsort(values, kind="stable")
        result = np.empty(len(values), dtype=float)
        sorted_vals = np.asarray(values, dtype=float)[order]
        i = 0
        while i < len(values):
            j = i
            while j + 1 < len(values) and sorted_vals[j + 1] == sorted_vals[i]:
                j += 1
            result[order[i : j + 1]] = (i + j) / 2.0 + 1.0
            i = j + 1
        return result

    rx, ry = ranks(x), ranks(y)
    if np.std(rx) == 0 or np.std(ry) == 0:
        return 0.0
    return float(np.corrcoef(rx, ry)[0, 1])
