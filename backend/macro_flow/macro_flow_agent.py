"""CMF, RS vs SPY, category-level aggregates."""
from __future__ import annotations

import logging
import json
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from .weights_config import get_macro_flow_blend_weights

logger = logging.getLogger(__name__)


def chaikin_money_flow(df: pd.DataFrame, n: int = 21) -> float:
    """Last-bar CMF in [-1, 1] (approx); 0 if insufficient data."""
    if df is None or len(df) < n + 1:
        return 0.0
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    vol = df["Volume"].astype(float).replace(0, np.nan)
    rng = (high - low).replace(0, np.nan)
    mfm = ((close - low) - (high - close)) / rng
    mfm = mfm.fillna(0.0)
    mfv = mfm * vol.fillna(0.0)
    tail_mfv = mfv.iloc[-n:].sum()
    tail_vol = vol.iloc[-n:].sum()
    if tail_vol == 0 or np.isnan(tail_vol):
        return 0.0
    v = float(tail_mfv / tail_vol)
    return max(-1.0, min(1.0, v))


def _norm_path(close: pd.Series) -> pd.Series:
    c0 = float(close.iloc[0])
    if c0 == 0 or np.isnan(c0):
        return pd.Series(np.ones(len(close)), index=close.index)
    return close.astype(float) / c0


def category_rs_metrics(
    weights: List[Tuple[str, float]],
    frames: Dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    lag_bars: int = 5,
) -> Tuple[float, float, float]:
    """
    Weighted synthetic rel strength vs SPY.
    Returns (rs_ratio_latest, rs_momentum, flow_component_from_rs).
    """
    if spy is None or spy.empty or "Close" not in spy.columns:
        return 1.0, 0.0, 0.0
    spy_n = _norm_path(spy["Close"])

    acc = None
    wsum = 0.0
    for sym, w in weights:
        d = frames.get(sym)
        if d is None or d.empty or "Close" not in d.columns:
            continue
        # align length to min common index with SPY by position (assume same calendar from yf)
        m = min(len(d), len(spy))
        if m < 3:
            continue
        c = d["Close"].astype(float).iloc[-m:].reset_index(drop=True)
        s = spy["Close"].astype(float).iloc[-m:].reset_index(drop=True)
        cat_n = _norm_path(c)
        ratio_ts = cat_n / s.replace(0, np.nan)
        ratio_ts = ratio_ts.fillna(1.0)
        part = w * ratio_ts
        acc = part if acc is None else acc + part
        wsum += w
    if acc is None or wsum <= 0:
        return 1.0, 0.0, 0.0
    combined = acc / wsum
    rr = float(combined.iloc[-1])
    prev = int(max(0, len(combined) - 1 - lag_bars))
    rr_prev = float(combined.iloc[prev]) if prev < len(combined) else rr
    momentum = rr - rr_prev
    rs_flow = float(np.tanh(momentum * 5.0))
    return rr, momentum, rs_flow


def top_movers_for_category(
    weights: List[Tuple[str, float]],
    frames: Dict[str, pd.DataFrame],
    *,
    k: int = 3,
) -> List[Dict[str, Any]]:
    """Largest |interval return| × weight contributors in the category."""
    scored: List[Tuple[str, float, float]] = []
    for sym, w in weights:
        d = frames.get(sym)
        if d is None or len(d) < 2 or "Close" not in d.columns:
            continue
        c0 = float(d["Close"].iloc[0])
        c1 = float(d["Close"].iloc[-1])
        if c0 == 0 or np.isnan(c0) or np.isnan(c1):
            continue
        pct = (c1 - c0) / c0 * 100.0
        scored.append((sym, abs(pct) * float(w), pct))
    scored.sort(key=lambda x: -x[1])
    return [{"symbol": s, "period_change_pct": round(p, 2)} for s, _, p in scored[:k]]


def aggregate_category_flow(
    category_id: str,
    weights: List[Tuple[str, float]],
    frames: Dict[str, pd.DataFrame],
    spy: pd.DataFrame,
) -> Dict[str, float]:
    cmfs = []
    ww = []
    for sym, w in weights:
        d = frames.get(sym)
        if d is None or d.empty:
            continue
        cmfs.append(chaikin_money_flow(d))
        ww.append(w)
    if not ww:
        cmf_cat = 0.0
    else:
        wsum = sum(ww)
        cmf_cat = sum(c * w for c, w in zip(cmfs, ww)) / wsum

    rr, mom, rs_flow = category_rs_metrics(weights, frames, spy)
    cfg = get_macro_flow_blend_weights()
    cw = float(cfg.get("cmf_weight", 1.2))
    rw = float(cfg.get("rs_weight", 0.8))
    # blend into single flow_score in [-1,1]
    flow_score = float(np.tanh(cw * cmf_cat + rw * rs_flow))
    confidence = min(1.0, 0.35 + 0.15 * len(ww) + 0.25 * abs(flow_score))
    movers = top_movers_for_category(weights, frames, k=3)
    return {
        "category_id": category_id,
        "cmf": float(cmf_cat),
        "rs_ratio": float(rr),
        "rs_momentum": float(mom),
        "flow_score": float(flow_score),
        "confidence": float(confidence),
        "top_movers": movers,
    }
