"""Validation utilities: purged/embargoed splits + classification metrics.

63-day forward labels overlap heavily, so a naive split leaks the future. The
purged split removes training samples whose label window overlaps the test set,
plus an embargo gap (López de Prado style). See test_brain_validation.
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np


def purged_time_split(dates: np.ndarray, test_start: int, test_end: int,
                      horizon_days: int, embargo: int = 0) -> Tuple[np.ndarray, np.ndarray]:
    """Index-based purged split.

    ``dates`` is an integer time index per sample (e.g. day number). Samples in
    [test_start, test_end] are the test set. Training samples are everything
    whose label window [date, date + horizon] does not overlap the test window
    (expanded by ``embargo``).
    Returns (train_idx, test_idx).
    """
    dates = np.asarray(dates)
    n = dates.size
    all_idx = np.arange(n)
    test_mask = (dates >= test_start) & (dates <= test_end)
    test_idx = all_idx[test_mask]

    lo = test_start - embargo
    hi = test_end + embargo
    # A train sample at date d "sees" up to d + horizon. Exclude if that window
    # overlaps [lo, hi], or if the sample itself sits inside the embargo band.
    train_mask = np.ones(n, dtype=bool)
    train_mask &= ~test_mask
    label_end = dates + horizon_days
    overlap = (label_end >= lo) & (dates <= hi)
    train_mask &= ~overlap
    return all_idx[train_mask], test_idx


def roc_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """ROC AUC via the Mann-Whitney U statistic. Returns 0.5 if degenerate."""
    y_true = np.asarray(y_true).astype(int)
    y_score = np.asarray(y_score, dtype=float)
    pos = y_score[y_true == 1]
    neg = y_score[y_true == 0]
    if pos.size == 0 or neg.size == 0:
        return 0.5
    # rank-based AUC
    order = np.argsort(y_score, kind="mergesort")
    ranks = np.empty(y_score.size, dtype=float)
    ranks[order] = np.arange(1, y_score.size + 1)
    # average ranks for ties
    _assign_tie_ranks(y_score, ranks)
    sum_pos = ranks[y_true == 1].sum()
    auc = (sum_pos - pos.size * (pos.size + 1) / 2.0) / (pos.size * neg.size)
    return float(auc)


def _assign_tie_ranks(scores: np.ndarray, ranks: np.ndarray) -> None:
    order = np.argsort(scores, kind="mergesort")
    s = scores[order]
    i = 0
    n = s.size
    while i < n:
        j = i
        while j + 1 < n and s[j + 1] == s[i]:
            j += 1
        if j > i:
            avg = (ranks[order[i]] + ranks[order[j]]) / 2.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg
        i = j + 1


def brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.asarray(y_prob, dtype=float)
    return float(np.mean((y_prob - y_true) ** 2))


def precision_at_k(y_true: np.ndarray, y_score: np.ndarray, k: int) -> float:
    y_true = np.asarray(y_true).astype(int)
    y_score = np.asarray(y_score, dtype=float)
    if k <= 0 or y_score.size == 0:
        return 0.0
    k = min(k, y_score.size)
    top = np.argsort(-y_score)[:k]
    return float(y_true[top].mean())


def accuracy(y_true: np.ndarray, y_prob: np.ndarray, threshold: float = 0.5) -> float:
    y_true = np.asarray(y_true).astype(int)
    pred = (np.asarray(y_prob, dtype=float) >= threshold).astype(int)
    return float((pred == y_true).mean())


def classification_report(y_true: np.ndarray, y_prob: np.ndarray, k: int = 25) -> dict:
    return {
        "auc": round(roc_auc(y_true, y_prob), 4),
        "brier": round(brier_score(y_true, y_prob), 4),
        "accuracy": round(accuracy(y_true, y_prob), 4),
        "precision_at_k": round(precision_at_k(y_true, y_prob, k), 4),
        "n": int(np.asarray(y_true).size),
        "base_rate": round(float(np.asarray(y_true).astype(float).mean()), 4),
    }
