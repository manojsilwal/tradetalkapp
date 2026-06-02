"""
Daily Brief — top movers with movement context and heuristic verdicts.

Reads from BigQuery (movement_context_daily + daily_prices) when
MCP_DATA_BACKEND=bigquery; falls back to market_intel live movers.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEAL_SPIKE_PATTERNS = re.compile(
    r"\b(acquisition|merger|buyout|deal|contract|partnership|takeover|"
    r"agreement|awarded|strategic)\b",
    re.I,
)

VERDICT_ORDER = ("Strong Buy", "Buy", "Hold", "Sell")


def _backend_type() -> str:
    return os.environ.get("MCP_DATA_BACKEND", "duckdb").lower()


def get_latest_trade_date() -> Optional[date]:
    if _backend_type() != "bigquery":
        return None
    from backend.data_lake.daily_market_update import get_bq_last_trade_date

    return get_bq_last_trade_date()


def _value_spike_override(
    bucket: str,
    category: str,
    headline: str,
    daily_return_pct: float,
) -> bool:
    if bucket != "gainer" or daily_return_pct is None or daily_return_pct < 2.0:
        return False
    cat = (category or "").lower()
    if cat not in ("earnings", "corporate_action", "news", "sec_filing"):
        return False
    return bool(DEAL_SPIKE_PATTERNS.search(headline or ""))


def heuristic_verdict(row: Dict[str, Any], bucket: str) -> Dict[str, str]:
    """Fast verdict from precomputed movement fields (no LLM)."""
    ret = float(row.get("daily_return_pct") or 0)
    cat = row.get("catalyst_status") or "no_catalyst"
    headline = row.get("primary_cause_headline") or ""
    category = row.get("primary_cause_category") or ""
    z = row.get("return_zscore_60d")
    z_val = float(z) if z is not None else 0.0
    hl = headline.strip()

    if _value_spike_override(bucket, category, hl, ret):
        return {
            "verdict": "Hold",
            "one_line_reason": (
                "Event-driven spike (deal/news); reassess fair value before selling."
            ),
            "adjustment_note": "value_spike_override",
        }

    if bucket == "gainer":
        if cat == "symbol_specific" and ret >= 4:
            return {"verdict": "Strong Buy", "one_line_reason": hl or "Strong catalyst-led rally"}
        if cat in ("symbol_specific", "macro_only") and ret >= 1.5:
            return {"verdict": "Buy", "one_line_reason": hl or "Supported by identifiable catalyst"}
        if ret >= 6 and cat == "no_catalyst":
            return {
                "verdict": "Sell",
                "one_line_reason": "Large gain without catalyst — possible overextension",
            }
        return {"verdict": "Hold", "one_line_reason": hl or "Watch for follow-through"}

    # loser bucket
    if z_val <= -2.0 and cat == "no_catalyst":
        return {"verdict": "Buy", "one_line_reason": "Oversold vs 60-day volatility band"}
    if cat == "symbol_specific" and any(
        x in hl.lower() for x in ("downgrade", "miss", "cut", "layoff", "probe", "fraud")
    ):
        return {"verdict": "Sell", "one_line_reason": hl or "Negative company-specific catalyst"}
    if ret <= -6:
        return {"verdict": "Hold", "one_line_reason": hl or "Sharp drawdown — verify fundamentals"}
    return {"verdict": "Hold", "one_line_reason": hl or "Monitor for stabilization"}


def _is_compelling(row: Dict[str, Any]) -> bool:
    z = row.get("return_zscore_60d")
    rv = row.get("relative_volume")
    cat = row.get("catalyst_status")
    try:
        if z is not None and abs(float(z)) >= 1.8:
            return True
        if rv is not None and float(rv) >= 1.5:
            return True
        if cat in ("symbol_specific", "macro_only"):
            return True
    except (TypeError, ValueError):
        pass
    return False


def _normalize_row(raw: Dict[str, Any], bucket: str, rank: int) -> Dict[str, Any]:
    verdict_info = heuristic_verdict(raw, bucket)
    out = {
        "rank": rank,
        "bucket": bucket,
        "symbol": raw.get("symbol"),
        "trade_date": _iso_date(raw.get("trade_date")),
        "daily_return_pct": _num(raw.get("daily_return_pct")),
        "close": _num(raw.get("close")),
        "volume": raw.get("volume"),
        "relative_volume": _num(raw.get("relative_volume")),
        "return_zscore_60d": _num(raw.get("return_zscore_60d")),
        "market_regime": raw.get("market_regime"),
        "catalyst_status": raw.get("catalyst_status"),
        "primary_cause_category": raw.get("primary_cause_category"),
        "primary_cause_headline": raw.get("primary_cause_headline"),
        "primary_cause_weight": _num(raw.get("primary_cause_weight")),
        "verdict": verdict_info["verdict"],
        "one_line_reason": verdict_info["one_line_reason"],
        "adjustment_note": verdict_info.get("adjustment_note"),
        "verdict_tier": "heuristic",
        "is_compelling": _is_compelling(raw),
    }
    return out


def _iso_date(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, date):
        return val.isoformat()
    if hasattr(val, "isoformat"):
        return val.isoformat()[:10]
    return str(val)[:10]


def _num(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return round(float(val), 4)
    except (TypeError, ValueError):
        return None


def _fetch_movers_from_bq(trade_date: date, n_losers: int, n_gainers: int) -> List[Dict[str, Any]]:
    from backend.mcp_server.backend import backend
    from backend.mcp_server.bq_schema import FULL_DATASET

    ds = FULL_DATASET
    td = trade_date.isoformat()
    base_sql = f"""
        SELECT
            p.symbol,
            p.trade_date,
            p.close,
            p.volume,
            p.daily_return_pct,
            f.relative_volume,
            f.return_zscore_60d,
            COALESCE(c.market_regime, f.market_regime) AS market_regime,
            c.catalyst_status,
            c.primary_cause_category,
            c.primary_cause_headline,
            c.primary_cause_weight
        FROM `{ds}.daily_prices` p
        LEFT JOIN `{ds}.daily_movement_features` f
          ON p.symbol = f.symbol AND p.trade_date = f.trade_date
        LEFT JOIN `{ds}.movement_context_daily` c
          ON p.symbol = c.symbol AND p.trade_date = c.trade_date
        WHERE p.trade_date = DATE '{td}'
          AND p.close IS NOT NULL
          AND p.daily_return_pct IS NOT NULL
    """
    losers = backend().query(
        base_sql + f" ORDER BY p.daily_return_pct ASC LIMIT {int(n_losers)}"
    )
    gainers = backend().query(
        base_sql + f" ORDER BY p.daily_return_pct DESC LIMIT {int(n_gainers)}"
    )
    rows: List[Dict[str, Any]] = []
    for i, r in enumerate(losers, start=1):
        rows.append(_normalize_row(r, "loser", i))
    for i, r in enumerate(gainers, start=1):
        rows.append(_normalize_row(r, "gainer", i))
    return rows


def _fetch_movers_from_intel(n_losers: int, n_gainers: int) -> List[Dict[str, Any]]:
    from backend import market_intel

    snap = market_intel.get_live_movers_snapshot()
    rows: List[Dict[str, Any]] = []
    today = date.today().isoformat()
    for bucket, key, limit in (
        ("loser", "losers", n_losers),
        ("gainer", "gainers", n_gainers),
    ):
        for i, m in enumerate((snap.get(key) or [])[:limit], start=1):
            raw = {
                "symbol": m.get("sym"),
                "trade_date": today,
                "close": m.get("price"),
                "daily_return_pct": m.get("pct"),
                "catalyst_status": "no_catalyst",
            }
            rows.append(_normalize_row(raw, bucket, i))
    return rows


def build_daily_brief(
    trade_date: Optional[date] = None,
    n_losers: int = 20,
    n_gainers: int = 10,
) -> Dict[str, Any]:
    td = trade_date or get_latest_trade_date()
    source = "bigquery"
    rows: List[Dict[str, Any]] = []

    if td and _backend_type() == "bigquery":
        try:
            rows = _fetch_movers_from_bq(td, n_losers, n_gainers)
        except Exception as e:
            logger.warning("[DailyBrief] BQ fetch failed: %s", e)
            rows = []

    if not rows:
        source = "market_intel"
        rows = _fetch_movers_from_intel(n_losers, n_gainers)
        td = td or date.today()

    losers = [r for r in rows if r["bucket"] == "loser"]
    gainers = [r for r in rows if r["bucket"] == "gainer"]
    compelling = [r for r in rows if r.get("is_compelling")]

    return {
        "trade_date": _iso_date(td),
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "losers": losers,
        "gainers": gainers,
        "compelling": compelling[:15],
        "rows": losers + gainers,
    }
