"""Build brain feature rows and training panels from the BigQuery data lake.

Fundamentals (ROIC, PE, EV/EBITDA, FCF yield, margins, etc.) are loaded from
the connectors / yfinance cache so the brain model sees the full feature set,
not just price-derived momentum+risk. This runs once per nightly pipeline run
and is expensive, so results are cached on the module for reuse across all tickers
processed in a single job run.

Reads ``daily_prices`` (the shared ingestion substrate) via the existing
``mcp_server.backend`` dual backend (BigQuery in prod, DuckDB locally) and
assembles:

  * ``build_inference_rows`` — per-ticker price tails + feature rows for the
    latest as-of date, consumed by the nightly snapshot job.
  * ``build_training_panel`` — a purged, point-in-time training panel
    (rows/y/excess/dates/tickers) consumed by ``pipeline.train_and_register``.

The benchmark is an equal-weight index built from the same universe (or SPY/
^GSPC if present), date-aligned per ticker so forward labels stay leak-free.
Everything degrades to empty structures when the backend is unavailable so the
job and tests never hard-fail offline.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence

from .. import DEFAULT_HORIZON_DAYS
from .. import features as feat
from .. import labels as lbl

logger = logging.getLogger(__name__)

_BENCHMARK_CANDIDATES = ("SPY", "^GSPC", "VOO", "IVV")

# Module-level fundamentals cache — populated once per job run by
# load_fundamentals_bulk() and reused across all tickers.
_fundamentals_cache: Optional[Dict[str, Dict]] = None


def _map_yf_fundamentals(info: Dict) -> Dict:
    """Map yfinance .info dict → brain feature passthrough keys."""
    def _pct(v):
        return float(v) * 100.0 if v is not None else None

    trailing_pe = info.get("trailingPE")
    forward_pe = info.get("forwardPE")
    pe = trailing_pe if trailing_pe is not None else forward_pe
    ev = info.get("enterpriseValue") or 0
    ebitda = info.get("ebitda") or 0
    ev_ebitda = (ev / ebitda) if (ev and ebitda and ebitda > 0) else None
    market_cap = info.get("marketCap") or 0
    fcf = info.get("freeCashflow") or 0
    fcf_yield = (fcf / market_cap) if (fcf and market_cap > 0) else None
    rev = info.get("totalRevenue") or 0
    fcf_margin = (fcf / rev) if (fcf and rev > 0) else None
    return {
        "revenue_growth_yoy": _pct(info.get("revenueGrowth")),
        "gross_margin": _pct(info.get("grossMargins")),
        "operating_margin": _pct(info.get("operatingMargins")),
        "net_margin": _pct(info.get("profitMargins")),
        "fcf_margin": fcf_margin,
        "roic": _pct(info.get("returnOnAssets")),  # best proxy from yf.info
        "debt_to_equity": info.get("debtToEquity"),
        "fcf_yield": fcf_yield,
        "pe_ratio": pe,
        "ev_ebitda": ev_ebitda,
    }


def load_fundamentals_bulk(tickers: Optional[Sequence[str]] = None,
                           max_workers: int = 8) -> Dict[str, Dict]:
    """Return {ticker: fundamentals_dict} for all tickers in one batched yfinance call.

    Cached at the module level so the nightly job calls this once and reuses it.
    Falls back gracefully to an empty dict per ticker on any per-ticker error.
    """
    global _fundamentals_cache
    if _fundamentals_cache is not None:
        return _fundamentals_cache

    if not tickers:
        _fundamentals_cache = {}
        return _fundamentals_cache

    import concurrent.futures
    result: Dict[str, Dict] = {}

    def _fetch_one(sym: str) -> tuple:
        try:
            import yfinance as yf
            info = yf.Ticker(sym).info or {}
            return sym, _map_yf_fundamentals(info)
        except Exception as e:  # noqa: BLE001
            logger.debug("[bq_panel] fundamentals failed for %s: %s", sym, e)
            return sym, {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        for sym, fund in pool.map(_fetch_one, tickers):
            result[sym] = fund

    _fundamentals_cache = result
    logger.info("[bq_panel] loaded fundamentals for %d tickers", len(result))
    return result


def reset_fundamentals_cache() -> None:
    """Call between job runs to force a fresh fundamentals load."""
    global _fundamentals_cache
    _fundamentals_cache = None


def _query(sql: str) -> List[Dict]:
    try:
        from ...mcp_server.backend import backend
        return backend().query(sql)
    except Exception as e:  # noqa: BLE001 - degrade offline
        logger.warning("[brain.bq_panel] query failed: %s", e)
        return []


def load_price_series(lookback_days: int = 420,
                      symbols: Optional[Sequence[str]] = None) -> Dict[str, Dict[str, list]]:
    """Return {symbol: {"dates": [iso...], "closes": [float...]}} ascending by date.

    ``lookback_days`` is an integer we control (safe to inline). When ``symbols``
    is given the result is filtered to that universe in Python.
    """
    lookback_days = max(1, int(lookback_days))
    sql = (
        "SELECT symbol, trade_date, close FROM daily_prices "
        f"WHERE trade_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {lookback_days} DAY) "
        "AND close IS NOT NULL ORDER BY symbol, trade_date"
    )
    rows = _query(sql)
    keep = {s.upper() for s in symbols} if symbols else None
    series: Dict[str, Dict[str, list]] = {}
    for r in rows:
        sym = str(r.get("symbol", "")).upper()
        if not sym or (keep is not None and sym not in keep):
            continue
        close = r.get("close")
        if close is None:
            continue
        bucket = series.setdefault(sym, {"dates": [], "closes": []})
        bucket["dates"].append(str(r.get("trade_date")))
        bucket["closes"].append(float(close))
    return series


def _index_by_date(series: Dict[str, Dict[str, list]]) -> Dict[str, float]:
    """Equal-weight, rebased index value per date across the universe."""
    accum: Dict[str, list] = {}
    for bucket in series.values():
        closes = bucket["closes"]
        if not closes:
            continue
        base = closes[0]
        if not base:
            continue
        for d, c in zip(bucket["dates"], closes):
            accum.setdefault(d, []).append(c / base)
    return {d: (sum(vals) / len(vals)) * 100.0 for d, vals in accum.items() if vals}


def _benchmark_for(symbol_dates: Sequence[str], series: Dict[str, Dict[str, list]],
                   index_by_date: Dict[str, float]) -> List[float]:
    """Date-aligned benchmark series for one ticker (prefers a real index ETF)."""
    for cand in _BENCHMARK_CANDIDATES:
        b = series.get(cand)
        if b:
            by_date = dict(zip(b["dates"], b["closes"]))
            if all(d in by_date for d in symbol_dates):
                return [by_date[d] for d in symbol_dates]
    # Equal-weight fallback (index covers the union of dates).
    return [index_by_date.get(d, 100.0) for d in symbol_dates]


def load_filing_intelligence_bulk(tickers: Optional[Sequence[str]] = None) -> Dict[str, Dict]:
    """Read cached filing intelligence → brain passthrough feature keys."""
    if not tickers:
        return {}
    try:
        from ...connectors.filing_intelligence import get_filing_intelligence_bulk, to_brain_fundamentals

        records = get_filing_intelligence_bulk(list(tickers))
        return {sym: to_brain_fundamentals(rec) for sym, rec in records.items()}
    except Exception as exc:  # noqa: BLE001
        logger.debug("[bq_panel] filing intelligence bulk load failed: %s", exc)
        return {}


def build_inference_rows(lookback_days: int = 420,
                         symbols: Optional[Sequence[str]] = None,
                         min_history: int = 130,
                         fundamentals_by_symbol: Optional[Dict[str, Dict]] = None,
                         load_fundamentals: bool = True,
                         ) -> List[Dict]:
    """Per-ticker payloads for snapshotting as of the latest available date.

    Each item: {ticker, as_of_date, prices, sector_prices, fundamentals,
    feature_row}. Tickers with fewer than ``min_history`` closes are skipped.
    """
    series = load_price_series(lookback_days, symbols)
    if not series:
        return []
    index_by_date = _index_by_date(series)
    if fundamentals_by_symbol is None and load_fundamentals:
        fundamentals_by_symbol = load_fundamentals_bulk(list(series.keys()))
    fundamentals_by_symbol = fundamentals_by_symbol or {}
    filing_by_symbol = load_filing_intelligence_bulk(list(series.keys()))

    out: List[Dict] = []
    for sym, bucket in series.items():
        closes = bucket["closes"]
        dates = bucket["dates"]
        if len(closes) < min_history:
            continue
        bench = _benchmark_for(dates, series, index_by_date)
        fundamentals = {
            **fundamentals_by_symbol.get(sym, {}),
            **filing_by_symbol.get(sym, {}),
        }
        feature_row = feat.build_feature_row(closes, bench, fundamentals)
        out.append({
            "ticker": sym,
            "as_of_date": dates[-1],
            "prices": closes,
            "sector_prices": bench,
            "fundamentals": fundamentals,
            "feature_row": feature_row,
        })
    return out


def build_training_panel(lookback_days: int = 1500,
                         horizon_days: int = DEFAULT_HORIZON_DAYS,
                         anchor_step: int = 21,
                         symbols: Optional[Sequence[str]] = None,
                         min_history: int = 260) -> Dict:
    """Assemble a purged point-in-time training panel from BigQuery prices.

    Anchors are spaced ``anchor_step`` trading days apart per ticker; each anchor
    needs a complete forward ``horizon_days`` window to produce a label.
    """
    series = load_price_series(lookback_days, symbols)
    panel_obs: List[Dict] = []
    index_by_date = _index_by_date(series)
    for sym, bucket in series.items():
        closes = bucket["closes"]
        dates = bucket["dates"]
        n = len(closes)
        if n < min_history:
            continue
        bench = _benchmark_for(dates, series, index_by_date)
        last_anchor = n - horizon_days - 1
        idx = max(min_history - 1, 0)
        while idx <= last_anchor:
            panel_obs.append({
                "ticker": sym,
                "date": dates[idx],
                "prices": closes,
                "benchmark": bench,
                "as_of_idx": idx,
                "fundamentals": {},
            })
            idx += max(1, int(anchor_step))

    if not panel_obs:
        return {"rows": [], "y": [], "excess": [], "dates": [], "tickers": []}

    feature_rows = feat.build_features_panel(panel_obs)
    label_rows = lbl.build_labels_panel(panel_obs, horizon_days)

    rows, y, excess, dates, tickers = [], [], [], [], []
    for obs, frow, label in zip(panel_obs, feature_rows, label_rows):
        if label is None:
            continue
        feature_only = {k: v for k, v in frow.items() if k not in ("ticker", "date")}
        rows.append(feature_only)
        y.append(1 if label["outperformed_benchmark"] else 0)
        excess.append(label["future_excess_return"])
        dates.append(obs["as_of_idx"])
        tickers.append(obs["ticker"])

    return {"rows": rows, "y": y, "excess": excess, "dates": dates, "tickers": tickers}
