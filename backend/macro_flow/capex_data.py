"""Live CapEx from yfinance cash-flow statements for value-chain stages."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CAPEX_CACHE: Dict[str, Any] = {"payload": None, "expires_at": 0.0}
_CAPEX_CACHE_TTL_SEC = 6 * 3600  # refresh every 6 hours

# Public tickers per chain stage (CapEx summed within stage).
STAGE_TICKERS: Dict[str, Tuple[str, ...]] = {
    # AMZN is classified under hyperscaler only (avoid double-counting CapEx).
    "retail_industry": ("WMT", "COST", "CAT", "LLY", "TSLA", "JPM"),
    "hyperscaler": ("MSFT", "AMZN", "GOOGL", "META", "ORCL"),
    "semiconductor": ("NVDA", "AMD", "AVGO", "MRVL", "INTC", "QCOM", "TXN"),
    "foundry_infra": ("TSM", "ASML", "LRCX", "KLAC", "AMAT"),
    "materials": ("FCX", "NEM", "LIN", "APD", "ALB", "SQM"),
}

STAGE_LABELS: Dict[str, str] = {
    "retail_industry": "Retail / Industry",
    "hyperscaler": "Hyperscaler",
    "semiconductor": "Semiconductor",
    "foundry_infra": "Foundry / Equipment",
    "materials": "Materials / Minerals",
}

_CAPEX_ROW_NAMES = (
    "Capital Expenditure",
    "Capital Expenditures",
    "Purchase Of PPE",
    "Purchase Of Property, Plant And Equipment",
)


def _abs_capex(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return abs(v)


def _pick_capex_series(df) -> Optional[Any]:
    if df is None or df.empty:
        return None
    for name in _CAPEX_ROW_NAMES:
        if name in df.index:
            return df.loc[name]
    for idx in df.index:
        label = str(idx).lower()
        if "capital" in label and "expend" in label and "reported" not in label:
            return df.loc[idx]
    return None


_fx_cache: Dict[str, float] = {}


def _fx_to_usd(financial_currency: Optional[str]) -> float:
    """Multiply local-currency CapEx by this factor to get USD."""
    cur = (financial_currency or "USD").upper()
    if cur == "USD":
        return 1.0
    if cur in _fx_cache:
        return _fx_cache[cur]
    import yfinance as yf

    rate = 1.0
    for pair in (f"{cur}USD=X", f"USD{cur}=X"):
        try:
            hist = yf.Ticker(pair).history(period="5d")
            if hist is not None and not hist.empty:
                px = float(hist["Close"].iloc[-1])
                if pair.startswith("USD"):
                    rate = 1.0 / px if px else 1.0
                else:
                    rate = px
                break
        except Exception:
            continue
    _fx_cache[cur] = rate
    return rate


def _ticker_financial_currency(ticker: str) -> str:
    import yfinance as yf

    try:
        info = yf.Ticker(ticker).info or {}
        return str(info.get("financialCurrency") or info.get("currency") or "USD")
    except Exception:
        return "USD"


def _annual_capex_by_year(ticker: str, fx: float) -> Dict[str, float]:
    import yfinance as yf

    out: Dict[str, float] = {}
    try:
        stock = yf.Ticker(ticker)
        annual = stock.cashflow
        series = _pick_capex_series(annual)
        if series is None:
            return out
        for dt, val in series.items():
            try:
                year = str(dt.year) if hasattr(dt, "year") else str(dt)[:4]
            except Exception:
                continue
            capex = _abs_capex(val)
            if capex is not None:
                out[year] = out.get(year, 0.0) + capex * fx
    except Exception as e:
        logger.warning("[capex_data] annual %s: %s", ticker, e)
    return out


def _ttm_capex(ticker: str, fx: float) -> Tuple[Optional[float], Optional[str]]:
    """Return (ttm_usd, as_of_iso) from last 4 quarterly CapEx values."""
    import yfinance as yf

    try:
        stock = yf.Ticker(ticker)
        q = stock.quarterly_cashflow
        series = _pick_capex_series(q)
        if series is None or len(series) == 0:
            annual = stock.cashflow
            a_series = _pick_capex_series(annual)
            if a_series is None or len(a_series) == 0:
                return None, None
            latest = a_series.iloc[0]
            dt = a_series.index[0]
            capex = _abs_capex(latest)
            return (capex * fx if capex is not None else None), _fmt_date(dt)

        vals = []
        dates = []
        for dt, val in series.items():
            capex = _abs_capex(val)
            if capex is not None:
                vals.append(capex * fx)
                dates.append(dt)
            if len(vals) >= 4:
                break
        if not vals:
            return None, None
        as_of = _fmt_date(dates[0]) if dates else None
        return sum(vals[:4]), as_of
    except Exception as e:
        logger.warning("[capex_data] ttm %s: %s", ticker, e)
        return None, None


def _fmt_date(dt) -> Optional[str]:
    try:
        if hasattr(dt, "date"):
            return dt.date().isoformat()
        if hasattr(dt, "isoformat"):
            return str(dt)[:10]
    except Exception:
        pass
    return None


def _fetch_stage_capex_sync() -> Dict[str, Any]:
    stage_ids = list(STAGE_TICKERS.keys())
    years_set: set[str] = set()
    ticker_rows: List[Dict[str, Any]] = []

    ttm_by_stage: Dict[str, float] = {sid: 0.0 for sid in stage_ids}
    ttm_counts: Dict[str, int] = {sid: 0 for sid in stage_ids}
    annual_by_stage: Dict[str, Dict[str, float]] = {sid: {} for sid in stage_ids}
    as_of_dates: List[str] = []

    for sid, tickers in STAGE_TICKERS.items():
        for sym in tickers:
            fx = _fx_to_usd(_ticker_financial_currency(sym))
            ttm, as_of = _ttm_capex(sym, fx)
            if ttm is not None and ttm > 0:
                ttm_by_stage[sid] += ttm
                ttm_counts[sid] += 1
                if as_of:
                    as_of_dates.append(as_of)
                ticker_rows.append(
                    {
                        "stage_id": sid,
                        "ticker": sym,
                        "ttm_usd": round(ttm, 2),
                        "as_of": as_of,
                        "currency": _ticker_financial_currency(sym),
                    }
                )

            annual = _annual_capex_by_year(sym, fx)
            for year, val in annual.items():
                years_set.add(year)
                annual_by_stage[sid][year] = annual_by_stage[sid].get(year, 0.0) + val

    years = sorted(years_set)[-8:]  # last 8 fiscal years
    stage_totals = []
    for sid in stage_ids:
        timeline = [{"year": y, "usd": round(annual_by_stage[sid].get(y, 0.0), 2)} for y in years]
        stage_totals.append(
            {
                "id": sid,
                "name": STAGE_LABELS.get(sid, sid),
                "latest_usd": round(ttm_by_stage[sid], 2),
                "ticker_count": ttm_counts[sid],
                "timeline": timeline,
            }
        )

    return {
        "available": any(ttm_by_stage[sid] > 0 for sid in stage_ids),
        "unit": "USD",
        "metric": "capex_ttm",
        "basis": (
            "Trailing-twelve-month capital expenditure summed from yfinance quarterly/annual "
            "cash-flow statements for public tickers in each stage. CapEx is reported spend, "
            "not forward guidance."
        ),
        "source": "yfinance",
        "as_of": max(as_of_dates) if as_of_dates else None,
        "years": years,
        "latest_label": "TTM reported CapEx",
        "stage_totals": stage_totals,
        "tickers": ticker_rows,
    }


async def fetch_stage_capex_payload() -> Dict[str, Any]:
    import time

    now = time.time()
    cached = _CAPEX_CACHE.get("payload")
    if cached and now < float(_CAPEX_CACHE.get("expires_at") or 0):
        return cached
    payload = await asyncio.to_thread(_fetch_stage_capex_sync)
    _CAPEX_CACHE["payload"] = payload
    _CAPEX_CACHE["expires_at"] = now + _CAPEX_CACHE_TTL_SEC
    return payload


def build_flows_from_stage_capex(
    stage_totals: List[Dict[str, Any]],
    chain_edges: Tuple[Tuple[str, str, str], ...],
) -> List[Dict[str, Any]]:
    """Inter-stage flows = target stage TTM CapEx (capital deployed at that layer)."""
    by_id = {s["id"]: s for s in stage_totals}
    flows: List[Dict[str, Any]] = []
    for src_id, tgt_id, desc in chain_edges:
        src = by_id.get(src_id) or {}
        tgt = by_id.get(tgt_id) or {}
        latest = float(tgt.get("latest_usd") or 0.0)
        flows.append(
            {
                "from_id": src_id,
                "from_name": src.get("name") or src_id,
                "to_id": tgt_id,
                "to_name": tgt.get("name") or tgt_id,
                "latest_usd": round(latest, 2),
                "description": desc,
                "timeline": tgt.get("timeline") or [],
            }
        )
    max_flow = max((f["latest_usd"] for f in flows), default=1.0) or 1.0
    for f in flows:
        f["pct_of_peak"] = round(f["latest_usd"] / max_flow * 100.0, 1) if max_flow else 0.0
    return flows
