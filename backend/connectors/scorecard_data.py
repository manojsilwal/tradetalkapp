"""
Scorecard Data Connector — sources the objective data needed by the
Risk-Return-Ratio methodology (backend/scorecard.py).

Primary source: yfinance ``Ticker.info`` + historical prices / quarterly
earnings. Insider series (for the SITG scorer persona context) is pulled from
``Ticker.insider_transactions``; the data lake's
:mod:`backend.data_lake.ingest_events` uses the same yfinance endpoint, so
behavior is symmetric whether or not the lake has already harvested it.

This connector deliberately does NOT assign the subjective SITG or Execution
Risk scores — those are the LLM personas' job. We simply surface the *signals*
they need (open-market buys vs sells in the last 12 months, ownership %, CEO
name) so the personas have auditable context.

All I/O happens under :func:`fetch_scorecard_data` which runs the blocking
yfinance calls in a thread. Per-ticker failures collapse to
:func:`_empty_scorecard_fields` so a single bad ticker never kills a basket.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Output shape ─────────────────────────────────────────────────────────────

@dataclass
class ScorecardData:
    """
    Per-ticker bundle for the scorecard methodology. Fields match the Step 0
    data table in the spec plus CEO/insider signals for the SITG persona.

    Numeric fields default to 0 (not None) so the downstream math can run
    without explicit null guards. ``fields_missing`` lists any metrics the
    connector couldn't resolve — the LLM personas read this list and flag
    reduced confidence.
    """
    ticker: str
    company_name: str
    sector: str
    industry: str

    current_price: float
    forward_pe: Optional[float]
    historical_avg_pe: Optional[float]  # 5y average trailing PE as proxy
    beta: float
    eps_growth_pct: float          # consensus forward EPS growth (%)
    revenue_growth_pct: float      # TTM revenue growth (%)
    pt_upside_pct: float           # (targetMean / price - 1) * 100
    dividend_yield_pct: float
    debt_to_equity: float          # yfinance reports in % scale; we carry raw

    # SITG context (LLM-facing; math doesn't read these)
    ceo_name: str
    insider_buy_count_12m: int
    insider_sell_count_12m: int
    insider_net_shares_12m: float  # positive = net accumulation
    held_percent_insiders: float   # yfinance heldPercentInsiders (0..1)

    fields_missing: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Public entrypoints ───────────────────────────────────────────────────────

async def fetch_scorecard_data(ticker: str) -> ScorecardData:
    """Fetch one ticker. Never raises — on total failure returns empty fields."""
    return await asyncio.to_thread(_sync_fetch, ticker)


async def fetch_basket(tickers: List[str]) -> List[ScorecardData]:
    """Fetch a basket concurrently. Order is preserved."""
    tasks = [fetch_scorecard_data(t) for t in tickers]
    return await asyncio.gather(*tasks, return_exceptions=False)


# ── Implementation ───────────────────────────────────────────────────────────

def _sync_fetch(ticker: str) -> ScorecardData:
    sym = ticker.strip().upper()
    missing: List[str] = []
    try:
        import yfinance as yf
    except Exception as e:
        logger.warning("[ScorecardData] yfinance unavailable for %s: %s", sym, e)
        return _empty_scorecard_fields(sym, missing=["yfinance"])

    try:
        t = yf.Ticker(sym)
        info = t.info or {}
    except Exception as e:
        logger.warning("[ScorecardData] info fetch failed for %s: %s", sym, e)
        return _empty_scorecard_fields(sym, missing=["info"])

    current_price = _as_float(info.get("currentPrice") or info.get("regularMarketPrice"), default=0.0)
    forward_pe = _as_float_or_none(info.get("forwardPE"))
    if forward_pe is None:
        missing.append("forward_pe")

    beta = _as_float(info.get("beta"), default=1.0)
    # yfinance returns revenueGrowth as a ratio (0.08 = 8%).
    revenue_growth_pct = _as_float(info.get("revenueGrowth"), default=0.0) * 100.0
    # earningsGrowth is quarterly; earningsQuarterlyGrowth is another flavor.
    # Prefer "earningsGrowth" when present; fall back to 0.
    eps_growth_pct = _as_float(info.get("earningsGrowth"), default=0.0) * 100.0
    if info.get("earningsGrowth") is None and info.get("earningsQuarterlyGrowth") is not None:
        eps_growth_pct = _as_float(info.get("earningsQuarterlyGrowth"), default=0.0) * 100.0
    if eps_growth_pct == 0.0:
        missing.append("eps_growth")

    tgt = _as_float_or_none(info.get("targetMeanPrice"))
    pt_upside_pct = 0.0
    if tgt is not None and current_price > 0:
        pt_upside_pct = (tgt / current_price - 1.0) * 100.0
    else:
        missing.append("analyst_pt")

    dividend_yield_pct = _as_float(info.get("dividendYield"), default=0.0) * 100.0

    # yfinance debtToEquity is reported as a percentage (e.g. 146 means 1.46x).
    # Normalize to ratio scale so Step 2d math matches the methodology (NEE D/E = 1.46).
    raw_de = _as_float_or_none(info.get("debtToEquity"))
    if raw_de is None:
        debt_to_equity = 0.0
        missing.append("debt_to_equity")
    else:
        debt_to_equity = raw_de / 100.0 if raw_de > 10.0 else raw_de

    historical_avg_pe = _historical_avg_trailing_pe(t)
    if historical_avg_pe is None:
        missing.append("historical_avg_pe")

    ceo_name = _extract_ceo_name(info)
    held_pct_insiders = _as_float(info.get("heldPercentInsiders"), default=0.0)
    ins_buys, ins_sells, ins_net_shares = _insider_activity_12m(t)

    return ScorecardData(
        ticker=sym,
        company_name=str(info.get("longName") or info.get("shortName") or sym),
        sector=str(info.get("sector") or "Unknown"),
        industry=str(info.get("industry") or "Unknown"),
        current_price=round(current_price, 2),
        forward_pe=round(forward_pe, 2) if forward_pe is not None else None,
        historical_avg_pe=round(historical_avg_pe, 2) if historical_avg_pe is not None else None,
        beta=round(beta, 3),
        eps_growth_pct=round(eps_growth_pct, 2),
        revenue_growth_pct=round(revenue_growth_pct, 2),
        pt_upside_pct=round(pt_upside_pct, 2),
        dividend_yield_pct=round(dividend_yield_pct, 3),
        debt_to_equity=round(debt_to_equity, 3),
        ceo_name=ceo_name,
        insider_buy_count_12m=ins_buys,
        insider_sell_count_12m=ins_sells,
        insider_net_shares_12m=round(ins_net_shares, 0),
        held_percent_insiders=round(held_pct_insiders, 4),
        fields_missing=missing,
    )


def _historical_avg_trailing_pe(ticker) -> Optional[float]:
    """
    Proxy for the spec's "5-year average forward P/E": compute the average
    trailing P/E over the last 5 years using quarterly EPS history and monthly
    close prices. yfinance does not expose historical forward estimates on the
    free tier, so trailing PE is the best-available reproducible proxy.

    Returns None if we cannot derive a meaningful average.
    """
    try:
        # 5 years of monthly closes — small dataset, very fast.
        hist = ticker.history(period="5y", interval="1mo")
        if hist is None or hist.empty:
            return None
        # TTM EPS per quarter end, from quarterly financials.
        qe = getattr(ticker, "quarterly_earnings", None)
        if qe is None or getattr(qe, "empty", True):
            # Fallback: use current trailing PE as the historical anchor.
            info = ticker.info or {}
            pe = info.get("trailingPE")
            return float(pe) if pe else None
    except Exception as e:
        logger.debug("[ScorecardData] hist PE derivation failed: %s", e)
        return None

    try:
        import pandas as pd  # noqa: F401
        import numpy as np

        # Rolling TTM EPS at each quarter end (sum of last 4 quarters).
        eps_col = "Earnings"  # EPS per share in most yfinance responses
        if eps_col not in qe.columns:
            return None
        qe_sorted = qe.sort_index()
        ttm_eps = qe_sorted[eps_col].rolling(4).sum().dropna()
        if ttm_eps.empty:
            return None

        pes = []
        for ts, eps in ttm_eps.items():
            if eps <= 0:
                continue
            # Nearest monthly close after the quarter end.
            try:
                close_series = hist["Close"]
                close_idx = close_series.index[close_series.index >= ts.tz_localize(None) if hasattr(ts, "tz_localize") else close_series.index >= ts]
                if len(close_idx) == 0:
                    continue
                close = float(close_series.loc[close_idx[0]])
            except Exception:
                continue
            if close <= 0:
                continue
            pe = close / float(eps)
            if 0 < pe < 500:  # sanity cap
                pes.append(pe)
        if not pes:
            return None
        avg = float(np.mean(pes))
        return avg if avg > 0 else None
    except Exception as e:
        logger.debug("[ScorecardData] hist PE average failed: %s", e)
        return None


def _extract_ceo_name(info: Dict[str, Any]) -> str:
    """Pull the CEO/President name from yfinance's ``companyOfficers`` list."""
    officers = info.get("companyOfficers") or []
    for o in officers:
        title = str(o.get("title") or "").lower()
        if "ceo" in title or "chief executive" in title:
            return str(o.get("name") or "").strip()
    for o in officers:
        title = str(o.get("title") or "").lower()
        if "president" in title or "founder" in title:
            return str(o.get("name") or "").strip()
    return ""


def _insider_activity_12m(ticker) -> tuple[int, int, float]:
    """
    Count open-market buys (Form 4 code 'P') vs sells in the last 365 days.

    Returns ``(buys, sells, net_shares)``. Net shares is buys - sells so a
    positive value means the CEO/officers are accumulating.

    yfinance's ``insider_transactions`` returns heterogeneous columns; the
    data lake's :func:`_normalize_insider_df` does the same mapping we need
    here, but we stay self-contained so this connector works without the
    lake pipeline.
    """
    try:
        df = ticker.insider_transactions
    except Exception:
        return 0, 0, 0.0
    if df is None or getattr(df, "empty", True):
        return 0, 0, 0.0
    try:
        import pandas as pd

        cols = {str(c).lower(): c for c in df.columns}
        date_col = next((cols[k] for k in cols if "start" in k and "date" in k), None)
        tx_col = next((cols[k] for k in cols if "transaction" in k or k == "position" or "text" in k), None)
        sh_col = next((cols[k] for k in cols if "share" in k), None)
        if not date_col:
            return 0, 0, 0.0
        cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=365)
        dcol = pd.to_datetime(df[date_col], errors="coerce").dt.tz_localize(None)
        mask = dcol >= cutoff
        recent = df[mask]
        if recent.empty:
            return 0, 0, 0.0

        buys = sells = 0
        net = 0.0
        for _, row in recent.iterrows():
            tx = str(row.get(tx_col, "") if tx_col else "").lower()
            sh = row.get(sh_col, 0) if sh_col else 0
            try:
                sh_val = float(sh)
            except (TypeError, ValueError):
                sh_val = 0.0
            if "purchase" in tx or "buy" in tx or "acquisition" in tx or "award" in tx:
                buys += 1
                net += sh_val
            elif "sale" in tx or "sell" in tx or "disposition" in tx:
                sells += 1
                net -= sh_val
        return buys, sells, net
    except Exception as e:
        logger.debug("[ScorecardData] insider parse failed: %s", e)
        return 0, 0, 0.0


# ── Safe casting helpers ─────────────────────────────────────────────────────

def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN guard (NaN != NaN)
        return default
    return f


def _as_float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def _empty_scorecard_fields(ticker: str, *, missing: Optional[List[str]] = None) -> ScorecardData:
    return ScorecardData(
        ticker=ticker,
        company_name=ticker,
        sector="Unknown",
        industry="Unknown",
        current_price=0.0,
        forward_pe=None,
        historical_avg_pe=None,
        beta=1.0,
        eps_growth_pct=0.0,
        revenue_growth_pct=0.0,
        pt_upside_pct=0.0,
        dividend_yield_pct=0.0,
        debt_to_equity=0.0,
        ceo_name="",
        insider_buy_count_12m=0,
        insider_sell_count_12m=0,
        insider_net_shares_12m=0.0,
        held_percent_insiders=0.0,
        fields_missing=list(missing or ["all"]),
    )


__all__ = [
    "ScorecardData",
    "fetch_scorecard_data",
    "fetch_basket",
]
