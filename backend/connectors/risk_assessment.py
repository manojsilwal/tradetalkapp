"""Phase E3 — risk assessment connector."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from statistics import pstdev
from typing import Any

import yfinance as yf

from .macro import MacroHealthConnector


def _pct_changes(values: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(values)):
        prev = values[i - 1]
        cur = values[i]
        if prev:
            out.append((cur / prev) - 1.0)
    return out


def _atr_pct(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0.0
    tr: list[float] = []
    for i in range(1, len(closes)):
        h = highs[i]
        l = lows[i]
        pc = closes[i - 1]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    w = tr[-period:]
    close = closes[-1] or 1.0
    return (sum(w) / len(w)) / close


def _event_risk_flags(now: datetime) -> list[str]:
    flags: list[str] = []
    wd = now.weekday()
    if wd in (1, 2):
        flags.append("fomc_window")
    if wd in (3, 4):
        flags.append("nfp_window")
    if 8 <= now.day <= 14:
        flags.append("cpi_window")
    return flags


def _classify_regime(*, realized_vol: float, atr_pct: float, vix_level: float) -> str:
    if vix_level >= 28 or realized_vol >= 0.5:
        return "crisis"
    if atr_pct >= 0.03 or vix_level >= 22:
        return "trending"
    return "ranging"


async def compute_risk_assessment(ticker: str) -> dict[str, Any]:
    """Return volatility/regime/event-risk snapshot for one symbol."""
    sym = (ticker or "").upper().strip()
    if not sym:
        return {"error": "missing_ticker"}

    def _fetch_hist() -> tuple[list[float], list[float], list[float]]:
        hist = yf.Ticker(sym).history(period="6mo", interval="1d", auto_adjust=True)
        if hist is None or hist.empty:
            return [], [], []
        highs = [float(x) for x in hist["High"].tolist()]
        lows = [float(x) for x in hist["Low"].tolist()]
        closes = [float(x) for x in hist["Close"].tolist()]
        return highs, lows, closes

    highs, lows, closes = await asyncio.to_thread(_fetch_hist)
    if len(closes) < 5:
        return {"error": f"insufficient_history:{sym}"}

    ret = _pct_changes(closes[-31:])
    rv30 = pstdev(ret) * math.sqrt(252) if len(ret) >= 5 else 0.0
    atr14 = _atr_pct(highs, lows, closes, period=14)
    atr50 = _atr_pct(highs, lows, closes, period=50) if len(closes) >= 52 else atr14
    vix_level = 15.0
    try:
        macro = await MacroHealthConnector().fetch_data()
        vix_level = float(((macro.get("indicators") or {}).get("vix_level")) or 15.0)
    except Exception:
        pass

    regime = _classify_regime(realized_vol=rv30, atr_pct=atr14, vix_level=vix_level)
    event_flags = _event_risk_flags(datetime.now(timezone.utc))
    stop_distance_pct = atr14 * 1.5
    caution = "high" if (regime == "crisis" or event_flags) else ("medium" if regime == "trending" else "low")
    return {
        "ticker": sym,
        "realized_vol_30d": round(rv30, 4),
        "atr_14_pct": round(atr14, 4),
        "atr_50_pct": round(atr50, 4),
        "vix_level": round(vix_level, 2),
        "regime": regime,
        "event_risk_flags": event_flags,
        "stop_distance_pct_hint": round(stop_distance_pct, 4),
        "position_size_caution": caution,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
