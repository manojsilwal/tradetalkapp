"""
Investor-first Gold Advisor — daily snapshot (not streaming).
Assembles macro + FRED real yields + XAU technicals + light news sentiment, then LLM briefing.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .fred_series import fetch_fred_latest_sync
from .gold_technicals import compute_gold_technicals

logger = logging.getLogger(__name__)

GOLD_FUTURES = "GC=F"  # COMEX gold front month — common retail proxy
DXY_TICKER = "DX-Y.NYB"


def _keyword_sentiment(headlines: List[str]) -> float:
    """Crude -1..+1 score from headline text (MVP; replace with FinBERT later)."""
    if not headlines:
        return 0.0
    pos_w = (
        "rally", "surge", "gain", "high", "record", "safe haven", "haven", "bull",
        "rise", "jump", "strong", "support", "breakout",
    )
    neg_w = (
        "fall", "drop", "crash", "plunge", "bear", "pressure", "hawk", "selloff",
        "weak", "low", "breakdown", "slide", "cuts demand",
    )
    score = 0.0
    for h in headlines:
        t = (h or "").lower()
        score += sum(0.15 for w in pos_w if w in t)
        score -= sum(0.15 for w in neg_w if w in t)
    return max(-1.0, min(1.0, score / max(1, len(headlines))))


def _fetch_news_headlines_sync() -> Tuple[List[str], str]:
    key = os.environ.get("NEWSAPI_KEY", "").strip()
    if not key:
        return [], "Set NEWSAPI_KEY for headline sentiment (optional)."
    try:
        import requests

        url = "https://newsapi.org/v2/everything"
        params = {
            "q": "(gold OR XAU OR \"federal reserve\" OR FOMC OR inflation) AND (fed OR rates OR dollar OR commodity)",
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 5,
            "apiKey": key,
        }
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        data = r.json()
        arts = data.get("articles") or []
        titles = [a.get("title") or "" for a in arts if a.get("title")]
        return titles[:5], "newsapi.org"
    except Exception as e:
        logger.debug("[GoldAdvisor] NewsAPI: %s", e)
        return [], f"News unavailable: {e}"


def _sync_yfinance_gold_dxy() -> Dict[str, Any]:
    import yfinance as yf

    out: Dict[str, Any] = {"gold_last": None, "gold_currency": "USD", "dxy_last": None, "ohlc": None}
    try:
        g = yf.Ticker(GOLD_FUTURES)
        hist = g.history(period="1y", interval="1d", auto_adjust=True)
        if hist is not None and not hist.empty:
            out["ohlc"] = hist[["Open", "High", "Low", "Close"]].copy()
            out["gold_last"] = float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning("[GoldAdvisor] Gold OHLC fetch failed: %s", e)

    try:
        d = yf.Ticker(DXY_TICKER)
        dh = d.history(period="5d", interval="1d", auto_adjust=True)
        if dh is not None and not dh.empty:
            out["dxy_last"] = float(dh["Close"].iloc[-1])
    except Exception as e:
        logger.debug("[GoldAdvisor] DXY fetch: %s", e)

    return out


def _investor_event_hints() -> List[str]:
    """Static reminders for long-term holders (not a live economic calendar)."""
    return [
        "Watch scheduled US CPI and jobs reports — they often move real yields and gold.",
        "FOMC meetings and Fed chair speeches can repricing rate-cut odds quickly.",
        "CFTC Commitments of Traders (Fridays) show speculative positioning extremes in gold futures.",
    ]


async def build_gold_advisor_payload(macro_connector) -> Dict[str, Any]:
    """
    Fetch all deterministic inputs. Macro connector provides VIX (and any merged FRED fields).
    """
    loop = asyncio.get_event_loop()

    macro_task = asyncio.create_task(macro_connector.fetch_data())
    yf_task = loop.run_in_executor(None, _sync_yfinance_gold_dxy)
    fred_tips_task = loop.run_in_executor(None, lambda: fetch_fred_latest_sync("DFII10"))
    fred_10y_task = loop.run_in_executor(None, lambda: fetch_fred_latest_sync("DGS10"))
    news_task = loop.run_in_executor(None, _fetch_news_headlines_sync)

    macro, yf_bundle, tips10, nom10, news_pack = await asyncio.gather(
        macro_task, yf_task, fred_tips_task, fred_10y_task, news_task,
    )

    ind = macro.get("indicators") or {}
    vix = ind.get("vix_level")

    headlines, news_source = news_pack
    sentiment_score = _keyword_sentiment(headlines)

    ohlc_df = yf_bundle.get("ohlc")
    if isinstance(ohlc_df, pd.DataFrame):
        technicals = compute_gold_technicals(ohlc_df)
    else:
        technicals = {"error": "no_ohlc", "bars": 0}

    payload: Dict[str, Any] = {
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "investor_note": (
            "Snapshot for long-term allocation context, not intraday trading. "
            "Not personalized financial advice."
        ),
        "macro": {
            "vix": vix,
            "ten_year_tips_real_yield_pct": tips10,
            "ten_year_nominal_treasury_pct": nom10,
            "dxy_spot": yf_bundle.get("dxy_last"),
            "gold_futures_last_usd": yf_bundle.get("gold_last"),
            "gold_symbol": GOLD_FUTURES,
        },
        "technicals_daily": technicals,
        "sentiment": {
            "score_neg1_to_pos1": round(sentiment_score, 3),
            "headlines": headlines,
            "source": news_source,
        },
        "calendar_hints": _investor_event_hints(),
    }
    return payload


async def run_gold_advisor(macro_connector, llm_client) -> Dict[str, Any]:
    context = await build_gold_advisor_payload(macro_connector)
    briefing = await llm_client.generate_gold_briefing(context)
    return {
        "context": context,
        "briefing": briefing,
    }
