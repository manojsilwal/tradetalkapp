"""Standardizer fit on TRAIN data only, persisted with the model artifact.

Imputes missing values with the training mean, then z-scores. Storing the scaler
with the model guarantees inference applies the exact same transform.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np


class StandardScaler:
    def __init__(self) -> None:
        self.mean_: np.ndarray | None = None
        self.std_: np.ndarray | None = None
        self.feature_list_: List[str] | None = None

    def fit(self, X: np.ndarray, feature_list: List[str]) -> "StandardScaler":
        X = np.asarray(X, dtype=float)
        # Column means ignoring NaN; columns that are all-NaN -> 0 mean.
        means = np.nanmean(np.where(np.isnan(X), np.nan, X), axis=0)
        means = np.where(np.isnan(means), 0.0, means)
        # Impute then std.
        Xi = self._impute(X, means)
        stds = np.std(Xi, axis=0)
        stds = np.where(stds == 0, 1.0, stds)
        self.mean_ = means
        self.std_ = stds
        self.feature_list_ = list(feature_list)
        return self

    @staticmethod
    def _impute(X: np.ndarray, means: np.ndarray) -> np.ndarray:
        Xi = X.copy()
        idx = np.where(np.isnan(Xi))
        if idx[0].size:
            Xi[idx] = np.take(means, idx[1])
        return Xi

    def transform(self, X: np.ndarray) -> np.ndarray:
        assert self.mean_ is not None and self.std_ is not None, "scaler not fit"
        X = np.asarray(X, dtype=float)
        Xi = self._impute(X, self.mean_)
        return (Xi - self.mean_) / self.std_

    def fit_transform(self, X: np.ndarray, feature_list: List[str]) -> np.ndarray:
        return self.fit(X, feature_list).transform(X)

    def to_dict(self) -> Dict:
        return {
            "mean": self.mean_.tolist() if self.mean_ is not None else None,
            "std": self.std_.tolist() if self.std_ is not None else None,
            "feature_list": self.feature_list_,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "StandardScaler":
        s = cls()
        s.mean_ = np.asarray(d["mean"], dtype=float) if d.get("mean") is not None else None
        s.std_ = np.asarray(d["std"], dtype=float) if d.get("std") is not None else None
        s.feature_list_ = d.get("feature_list")
        return s
