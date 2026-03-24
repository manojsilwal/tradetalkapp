"""
Deterministic daily technicals for gold (investor snapshot — not tick data).
Computed in pandas; LLM receives only the final numbers.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_g = gain.rolling(period, min_periods=period).mean()
    avg_l = loss.rolling(period, min_periods=period).mean()
    rs = avg_g / avg_l.replace(0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    v = out.iloc[-1]
    return float(v) if pd.notna(v) else float("nan")


def _macd_hist(close: pd.Series) -> tuple[float, float, float]:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal
    i = -1
    return (
        float(macd_line.iloc[i]) if pd.notna(macd_line.iloc[i]) else float("nan"),
        float(signal.iloc[i]) if pd.notna(signal.iloc[i]) else float("nan"),
        float(hist.iloc[i]) if pd.notna(hist.iloc[i]) else float("nan"),
    )


def _bollinger(close: pd.Series, period: int = 20, n_std: float = 2.0) -> Dict[str, float]:
    mid = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std()
    upper = mid + n_std * std
    lower = mid - n_std * std
    c, u, l = close.iloc[-1], upper.iloc[-1], lower.iloc[-1]
    width = u - l
    pct_b = (c - l) / width if width and pd.notna(width) and width > 0 else 0.5
    return {
        "bb_middle": float(mid.iloc[-1]) if pd.notna(mid.iloc[-1]) else float("nan"),
        "bb_upper": float(u) if pd.notna(u) else float("nan"),
        "bb_lower": float(l) if pd.notna(l) else float("nan"),
        "bb_percent_b": float(min(1.0, max(0.0, pct_b))),
    }


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["High"], df["Low"], df["Close"]
    prev_c = c.shift(1)
    tr = pd.concat(
        [
            (h - l),
            (h - prev_c).abs(),
            (l - prev_c).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(period, min_periods=period).mean()
    v = atr.iloc[-1]
    return float(v) if pd.notna(v) else float("nan")


def _classic_pivots(prev: pd.Series) -> Dict[str, float]:
    """Prior session H/L/C classic pivots."""
    h, l, c = float(prev["High"]), float(prev["Low"]), float(prev["Close"])
    p = (h + l + c) / 3.0
    r1 = 2 * p - l
    s1 = 2 * p - h
    r2 = p + (h - l)
    s2 = p - (h - l)
    return {"pivot": p, "r1": r1, "r2": r2, "s1": s1, "s2": s2}


def compute_gold_technicals(ohlc: pd.DataFrame) -> Dict[str, Any]:
    """
    Expects columns Open, High, Low, Close; daily bars, oldest first.
    """
    if ohlc is None or len(ohlc) < 30:
        return {"error": "insufficient_bars", "bars": len(ohlc) if ohlc is not None else 0}

    df = ohlc.copy()
    for col in ("Open", "High", "Low", "Close"):
        if col not in df.columns:
            return {"error": f"missing_column_{col}"}

    close = df["Close"].astype(float)
    last = float(close.iloc[-1])
    prev_day = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]

    rsi_v = _rsi(close, 14)
    macd_line, macd_signal, macd_hist = _macd_hist(close)
    bb = _bollinger(close, 20, 2.0)
    atr_v = _atr(df, 14)
    pivots = _classic_pivots(prev_day)

    # Simple trend label from MA50 vs MA200 if enough data
    ma50 = close.rolling(50, min_periods=50).mean().iloc[-1]
    ma200 = close.rolling(200, min_periods=200).mean().iloc[-1] if len(close) >= 200 else float("nan")
    trend = "mixed"
    if pd.notna(ma50) and pd.notna(ma200):
        if last > ma50 > ma200:
            trend = "bullish_structure"
        elif last < ma50 < ma200:
            trend = "bearish_structure"

    return {
        "last_close": round(last, 2),
        "rsi_14": round(rsi_v, 2) if pd.notna(rsi_v) else None,
        "macd_line": round(macd_line, 4) if pd.notna(macd_line) else None,
        "macd_signal": round(macd_signal, 4) if pd.notna(macd_signal) else None,
        "macd_histogram": round(macd_hist, 4) if pd.notna(macd_hist) else None,
        "bollinger": {k: (round(v, 4) if isinstance(v, float) and pd.notna(v) else v) for k, v in bb.items()},
        "atr_14": round(atr_v, 4) if pd.notna(atr_v) else None,
        "classic_pivots": {k: round(v, 2) for k, v in pivots.items()},
        "ma50": round(float(ma50), 2) if pd.notna(ma50) else None,
        "ma200": round(float(ma200), 2) if pd.notna(ma200) else None,
        "trend_structure": trend,
        "bars_used": len(df),
    }
