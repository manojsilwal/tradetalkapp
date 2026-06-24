"""Model-agnostic classifiers for outperformance probability (pure NumPy).

Design decision (docs Section 03): the brain is model-agnostic. We ship two
candidates with one interface so the "best validated model wins" and can be
swapped behind the inference contract without touching callers:

  - LogisticRegressionNP      : linear baseline
  - FinancialRankingNet       : 1-hidden-layer MLP (the "neural net")

Both train on already-scaled features (see scaler.py), expose predict_proba,
and serialize to/from plain dicts for the model registry. No torch/sklearn.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np


def _sigmoid(z: np.ndarray) -> np.ndarray:
    # Numerically stable sigmoid.
    out = np.empty_like(z, dtype=float)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


class BaseModel:
    name = "base"

    def fit(self, X: np.ndarray, y: np.ndarray) -> "BaseModel":  # pragma: no cover - abstract
        raise NotImplementedError

    def predict_proba(self, X: np.ndarray) -> np.ndarray:  # pragma: no cover - abstract
        raise NotImplementedError

    def to_dict(self) -> Dict:  # pragma: no cover - abstract
        raise NotImplementedError

    @classmethod
    def from_dict(cls, d: Dict) -> "BaseModel":  # pragma: no cover - abstract
        raise NotImplementedError


class LogisticRegressionNP(BaseModel):
    name = "logreg"

    def __init__(self, lr: float = 0.1, epochs: int = 400, l2: float = 1e-4, seed: int = 7):
        self.lr = lr
        self.epochs = epochs
        self.l2 = l2
        self.seed = seed
        self.w: Optional[np.ndarray] = None
        self.b: float = 0.0

    def fit(self, X: np.ndarray, y: np.ndarray) -> "LogisticRegressionNP":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        n, d = X.shape
        rng = np.random.default_rng(self.seed)
        self.w = rng.normal(0.0, 0.01, size=d)
        self.b = 0.0
        for _ in range(self.epochs):
            p = _sigmoid(X @ self.w + self.b)
            grad = p - y
            gw = X.T @ grad / n + self.l2 * self.w
            gb = float(np.mean(grad))
            self.w -= self.lr * gw
            self.b -= self.lr * gb
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        assert self.w is not None, "model not fit"
        X = np.asarray(X, dtype=float)
        return _sigmoid(X @ self.w + self.b)

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "w": self.w.tolist() if self.w is not None else None,
            "b": float(self.b),
            "config": {"lr": self.lr, "epochs": self.epochs, "l2": self.l2, "seed": self.seed},
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "LogisticRegressionNP":
        cfg = d.get("config", {})
        m = cls(**cfg)
        m.w = np.asarray(d["w"], dtype=float) if d.get("w") is not None else None
        m.b = float(d.get("b", 0.0))
        return m


class FinancialRankingNet(BaseModel):
    """1-hidden-layer feedforward net: input -> H (ReLU) -> 1 (sigmoid)."""

    name = "finrank-net"

    def __init__(self, hidden: int = 32, lr: float = 0.05, epochs: int = 600,
                 l2: float = 1e-4, batch_size: int = 64, seed: int = 13):
        self.hidden = hidden
        self.lr = lr
        self.epochs = epochs
        self.l2 = l2
        self.batch_size = batch_size
        self.seed = seed
        self.W1 = self.b1 = self.W2 = self.b2 = None

    def _init_params(self, d: int) -> None:
        rng = np.random.default_rng(self.seed)
        # He initialization for ReLU.
        self.W1 = rng.normal(0.0, np.sqrt(2.0 / d), size=(d, self.hidden))
        self.b1 = np.zeros(self.hidden)
        self.W2 = rng.normal(0.0, np.sqrt(2.0 / self.hidden), size=(self.hidden, 1))
        self.b2 = np.zeros(1)

    def _forward(self, X: np.ndarray):
        z1 = X @ self.W1 + self.b1
        a1 = np.maximum(z1, 0.0)
        z2 = a1 @ self.W2 + self.b2
        p = _sigmoid(z2)
        return z1, a1, z2, p

    def fit(self, X: np.ndarray, y: np.ndarray) -> "FinancialRankingNet":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float).reshape(-1, 1)
        n, d = X.shape
        self._init_params(d)
        rng = np.random.default_rng(self.seed + 1)
        bs = min(self.batch_size, n)
        for _ in range(self.epochs):
            perm = rng.permutation(n)
            for start in range(0, n, bs):
                idx = perm[start:start + bs]
                xb, yb = X[idx], y[idx]
                m = xb.shape[0]
                z1, a1, z2, p = self._forward(xb)
                dz2 = (p - yb) / m
                dW2 = a1.T @ dz2 + self.l2 * self.W2
                db2 = dz2.sum(axis=0)
                da1 = dz2 @ self.W2.T
                dz1 = da1 * (z1 > 0)
                dW1 = xb.T @ dz1 + self.l2 * self.W1
                db1 = dz1.sum(axis=0)
                self.W2 -= self.lr * dW2
                self.b2 -= self.lr * db2
                self.W1 -= self.lr * dW1
                self.b1 -= self.lr * db1
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        assert self.W1 is not None, "model not fit"
        X = np.asarray(X, dtype=float)
        _, _, _, p = self._forward(X)
        return p.reshape(-1)

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "W1": self.W1.tolist(), "b1": self.b1.tolist(),
            "W2": self.W2.tolist(), "b2": self.b2.tolist(),
            "config": {
                "hidden": self.hidden, "lr": self.lr, "epochs": self.epochs,
                "l2": self.l2, "batch_size": self.batch_size, "seed": self.seed,
            },
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "FinancialRankingNet":
        m = cls(**d.get("config", {}))
        m.W1 = np.asarray(d["W1"], dtype=float)
        m.b1 = np.asarray(d["b1"], dtype=float)
        m.W2 = np.asarray(d["W2"], dtype=float)
        m.b2 = np.asarray(d["b2"], dtype=float)
        return m


_REGISTRY = {
    LogisticRegressionNP.name: LogisticRegressionNP,
    FinancialRankingNet.name: FinancialRankingNet,
}


def build_model(name: str, **config) -> BaseModel:
    if name not in _REGISTRY:
        raise ValueError(f"unknown model {name!r}; have {sorted(_REGISTRY)}")
    return _REGISTRY[name](**config)


def model_from_dict(d: Dict) -> BaseModel:
    name = d.get("name")
    if name not in _REGISTRY:
        raise ValueError(f"unknown model {name!r}")
    return _REGISTRY[name].from_dict(d)
