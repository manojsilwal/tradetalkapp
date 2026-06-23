"""End-to-end training pipeline: rows -> purged split -> scale -> fit -> validate
-> backtest -> register. Deterministic and offline.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

from . import DEFAULT_HORIZON_DAYS, FEATURE_LIST
from . import validation as val
from .backtest import run_backtest
from .model_registry import ModelRegistry
from .models import build_model
from .scaler import StandardScaler


def rows_to_matrix(rows: Sequence[Dict], feature_list: Sequence[str] = FEATURE_LIST) -> np.ndarray:
    """Build a (N, D) matrix in feature_list order; missing -> NaN."""
    out = np.full((len(rows), len(feature_list)), np.nan)
    for i, r in enumerate(rows):
        for j, f in enumerate(feature_list):
            v = r.get(f)
            if v is not None:
                out[i, j] = float(v)
    return out


def train_and_register(panel: Dict, version: str, registry: ModelRegistry,
                       model_name: str = "finrank-net", model_config: Optional[Dict] = None,
                       horizon_days: int = DEFAULT_HORIZON_DAYS, embargo: int = 1,
                       test_fraction: float = 0.3, top_n: int = 10,
                       cost_bps: float = 10.0, periods_per_year: int = 4) -> Dict:
    """Train one model on a panel and register it. Returns a summary dict.

    The split is purged + embargoed by period so 63-day-overlapping labels do not
    leak from train into test.
    """
    rows = panel["rows"]
    y = np.asarray(panel["y"], dtype=float)
    dates = np.asarray(panel["dates"])
    excess = np.asarray(panel["excess"], dtype=float)
    tickers = panel["tickers"]

    X = rows_to_matrix(rows)

    periods = sorted(set(dates.tolist()))
    n_test_periods = max(1, int(round(len(periods) * test_fraction)))
    test_start = periods[-n_test_periods]
    test_end = periods[-1]

    train_idx, test_idx = val.purged_time_split(
        dates, test_start, test_end, horizon_days=horizon_days, embargo=embargo
    )
    if train_idx.size == 0 or test_idx.size == 0:
        raise ValueError("empty train or test split; need more periods")

    scaler = StandardScaler().fit(X[train_idx], list(FEATURE_LIST))
    Xtr = scaler.transform(X[train_idx])
    Xte = scaler.transform(X[test_idx])

    model = build_model(model_name, **(model_config or {}))
    model.fit(Xtr, y[train_idx])

    proba_te = model.predict_proba(Xte)
    metrics_val = val.classification_report(y[test_idx], proba_te, k=top_n)

    bt = run_backtest(
        dates[test_idx], proba_te, excess[test_idx],
        [tickers[i] for i in test_idx],
        top_n=top_n, cost_bps=cost_bps, periods_per_year=periods_per_year,
    )

    metrics = {"validation": metrics_val, "backtest": bt}
    training_window = {
        "train_periods": [int(periods[0]), int(test_start - 1)],
        "test_periods": [int(test_start), int(test_end)],
        "embargo": embargo,
        "horizon_days": horizon_days,
        "n_train": int(train_idx.size),
        "n_test": int(test_idx.size),
    }
    path = registry.save(model, scaler, list(FEATURE_LIST), version,
                         metrics=metrics, training_window=training_window)

    return {
        "artifact_path": path,
        "model_name": model.name,
        "model_version": version,
        "metrics": metrics,
        "training_window": training_window,
    }
