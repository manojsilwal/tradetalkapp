"""Build brain feature rows and training panels from the BigQuery data lake.

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


def build_inference_rows(lookback_days: int = 420,
                         symbols: Optional[Sequence[str]] = None,
                         min_history: int = 130,
                         fundamentals_by_symbol: Optional[Dict[str, Dict]] = None
                         ) -> List[Dict]:
    """Per-ticker payloads for snapshotting as of the latest available date.

    Each item: {ticker, as_of_date, prices, sector_prices, fundamentals,
    feature_row}. Tickers with fewer than ``min_history`` closes are skipped.
    """
    series = load_price_series(lookback_days, symbols)
    if not series:
        return []
    index_by_date = _index_by_date(series)
    fundamentals_by_symbol = fundamentals_by_symbol or {}

    out: List[Dict] = []
    for sym, bucket in series.items():
        closes = bucket["closes"]
        dates = bucket["dates"]
        if len(closes) < min_history:
            continue
        bench = _benchmark_for(dates, series, index_by_date)
        fundamentals = fundamentals_by_symbol.get(sym, {})
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
