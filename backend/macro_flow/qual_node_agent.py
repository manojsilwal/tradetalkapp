"""MVP qualitative scores from yfinance Ticker.info (per entity)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_from_info(info: Dict[str, Any]) -> Dict[str, Any]:
    """Map sparse yfinance info fields to 0..1 qual heuristics."""
    if not info:
        return {
            "overall_qual": 0.5,
            "fundamental_band": "neutral",
            "moat_rating": 0,
            "earnings_quality": 0.5,
            "margin_trend": 0.5,
            "balance_sheet": 0.5,
        }

    def _f(key: str) -> float | None:
        v = info.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    pm = _f("profitMargins")
    eg = _f("earningsGrowth")
    de = _f("debtToEquity")
    roe = _f("returnOnEquity")

    margin_score = _clamp((pm or 0.08) / 0.25) if pm is not None else 0.55
    earn_score = _clamp(((eg or 0.1) + 0.2) / 0.5) if eg is not None else 0.5
    if de is None:
        bal = 0.55
    else:
        bal = _clamp(1.0 - min(de / 200.0, 1.0))
    roe_s = _clamp((roe or 0.12) / 0.25) if roe is not None else 0.5

    overall = float(_clamp(0.25 * margin_score + 0.25 * earn_score + 0.25 * bal + 0.25 * roe_s))
    if overall >= 0.65:
        band = "strong"
    elif overall <= 0.4:
        band = "weak"
    else:
        band = "neutral"

    moat = 2 if overall >= 0.7 else (1 if overall >= 0.55 else 0)

    return {
        "overall_qual": overall,
        "fundamental_band": band,
        "moat_rating": moat,
        "earnings_quality": earn_score,
        "margin_trend": margin_score,
        "balance_sheet": bal,
    }


def _one_ticker(sym: str) -> Tuple[str, Dict[str, Any]]:
    import yfinance as yf

    try:
        inf = yf.Ticker(sym).info or {}
    except Exception as e:
        logger.warning("[macro_flow] info %s: %s", sym, e)
        inf = {}
    return sym, score_from_info(inf)


async def fetch_entity_qual_scores(tickers: List[str]) -> Dict[str, Dict[str, Any]]:
    uniq = sorted({t.upper().strip() for t in tickers})

    def _all() -> Dict[str, Dict[str, Any]]:
        return dict(_one_ticker(s) for s in uniq)

    return await asyncio.to_thread(_all)


def aggregate_category_qual(
    weights: List[Tuple[str, float]],
    entity_scores: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    wsum = 0.0
    acc = 0.0
    bands = {"strong": 0.0, "neutral": 0.0, "weak": 0.0}
    moat_wide = 0.0
    for sym, w in weights:
        row = entity_scores.get(sym) or {}
        oq = float(row.get("overall_qual") or 0.5)
        acc += w * oq
        wsum += w
        b = str(row.get("fundamental_band") or "neutral")
        bands[b] = bands.get(b, 0.0) + w
        if int(row.get("moat_rating") or 0) >= 2:
            moat_wide += w
    if wsum <= 0:
        weighted = 0.5
    else:
        weighted = acc / wsum
    if weighted >= 0.62:
        fband = "strong"
    elif weighted <= 0.42:
        fband = "weak"
    else:
        fband = "neutral"
    return {
        "weighted_qual_score": float(weighted),
        "fundamental_band": fband,
        "moat_wide_pct": float(moat_wide / wsum) if wsum else 0.0,
        "coverage_pct": float(wsum),
    }
