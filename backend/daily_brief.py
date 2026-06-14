"""
Daily Brief — top movers with movement context and heuristic verdicts.

Reads from BigQuery (movement_context_daily + daily_prices) when
MCP_DATA_BACKEND=bigquery; falls back to market_intel live movers.
"""
from __future__ import annotations

import asyncio
import argparse
import json
import logging
import os
import re
import threading
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEAL_SPIKE_PATTERNS = re.compile(
    r"\b(acquisition|merger|buyout|deal|contract|partnership|takeover|"
    r"agreement|awarded|strategic)\b",
    re.I,
)

# Headlines like "CRM SEC 8-K — 2026-06-01" are data-source labels, not investor rationale.
FILING_STUB_PATTERN = re.compile(
    r"^\s*[A-Z]{1,5}\s+SEC\s+(8-K|10-K|10-Q|6-K|S-1)\s*[—–-]\s*\d{4}-\d{2}-\d{2}\s*$",
    re.I,
)
CORPORATE_ACTION_STUB = re.compile(
    r"^\s*[A-Z]{1,5}\s+(dividends?|split|spin-?off)\s*[—–-]\s*\d{4}-\d{2}-\d{2}\s*$",
    re.I,
)

VERDICT_ORDER = ("Strong Buy", "Buy", "Hold", "Sell")


def _backend_type() -> str:
    return os.environ.get("MCP_DATA_BACKEND", "duckdb").lower()


def _adjust_weekend_to_friday(d: date) -> date:
    from datetime import timedelta
    if d.weekday() == 5: # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6: # Sunday
        return d - timedelta(days=2)
    return d


def get_latest_trade_date() -> Optional[date]:
    from backend.mcp_server.backend import backend
    try:
        rows = backend().query("SELECT MAX(trade_date) AS d FROM daily_prices WHERE close IS NOT NULL")
        if rows and rows[0].get("d") is not None:
            val = rows[0]["d"]
            if isinstance(val, date):
                td = val
            else:
                import pandas as pd
                td = pd.Timestamp(val).date()
            return _adjust_weekend_to_friday(td)
    except Exception as e:
        logger.warning("[DailyBrief] failed to get latest trade date: %s", e)
    return None


def classify_company_preset(metrics: Dict[str, Any]) -> str:
    rev_growth = metrics.get("revenue_growth_pct", 0) or 0
    div_yield = metrics.get("dividend_yield_pct", 0) or 0
    
    # 1. High Growth Profile
    if rev_growth >= 15.0:
        return "growth"
    # 2. Mature Dividend Cash Cow
    elif div_yield >= 3.0:
        return "income"
    # 3. Standard/Value Leader
    else:
        return "value"


def scorecard_verdict_mapping(signal: str) -> str:
    sig = (signal or "").lower()
    if sig in ("exceptional", "strong buy"):
        return "Strong Buy"
    elif sig == "favorable":
        return "Buy"
    elif sig == "balanced":
        return "Hold"
    else:
        return "Sell"


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


def _headline_is_metadata_stub(headline: str) -> bool:
    hl = (headline or "").strip()
    if not hl:
        return True
    if FILING_STUB_PATTERN.match(hl) or CORPORATE_ACTION_STUB.match(hl):
        return True
    if re.match(r"^\s*[A-Z]{1,5}\s+SEC\s+\S+\s*[—–-]\s*\d{4}-\d{2}-\d{2}\s*$", hl, re.I):
        return True
    return False


def _substantive_headline(headline: str) -> str:
    """Return headline only when it carries real news, not a filing/date stub."""
    hl = (headline or "").strip()
    if not hl or _headline_is_metadata_stub(hl):
        return ""
    return hl[:120]


def _fmt_move_pct(ret: float) -> str:
    sign = "+" if ret >= 0 else ""
    return f"{sign}{ret:.1f}%"


def _build_one_line_reason(
    row: Dict[str, Any],
    bucket: str,
    verdict: str,
    *,
    adjustment_note: Optional[str] = None,
) -> str:
    """Concise 2–5 word rationale for the verdict."""
    if adjustment_note == "value_spike_override":
        return "Event spike, reassess value"

    cat_status = row.get("catalyst_status") or "no_catalyst"
    category = (row.get("primary_cause_category") or "").lower()
    headline = (row.get("primary_cause_headline") or "").strip()
    z = row.get("return_zscore_60d")
    z_val = float(z) if z is not None else None
    rv = row.get("relative_volume")
    rv_val = float(rv) if rv is not None else None
    ret = float(row.get("daily_return_pct") or 0)

    # Use substantive headline snippet when available (≤8 words)
    substantive = _substantive_headline(headline)
    if not substantive and category == "sec_filing" and headline:
        m = re.match(r"^([A-Z]+)\s+SEC\s+(\S+)", headline, re.I)
        if m:
            substantive = f"SEC Form {m[2]}"
        else:
            substantive = "SEC Filing"

    if substantive and category in ("news", "earnings", "sec_filing"):
        words = substantive.split()
        return f"{verdict} catalyst: " + " ".join(words[:8])

    if bucket == "gainer":
        if verdict == "Strong Buy":
            if rv_val and rv_val >= 2.0:
                return "High-volume catalyst breakout"
            return "Strong catalyst rally"
        if verdict == "Buy":
            return "Catalyst-supported upside"
        if verdict == "Sell":
            return "Overextended, no catalyst"
        # Hold
        if substantive:
            words = substantive.split()
            return " ".join(words[:6])
        if cat_status == "no_catalyst":
            return "Drifting, await catalyst"
        return "Monitor follow-through"

    # loser bucket
    if verdict == "Buy":
        if z_val is not None and z_val <= -2.5:
            return "Deeply oversold, bounce likely"
        return "Oversold bounce setup"
    if verdict == "Sell":
        if substantive:
            words = substantive.split()
            return " ".join(words[:6])
        return "Negative catalyst, avoid"
    # Hold
    if ret <= -6:
        return "Sharp drop, watch closely"
    if category == "corporate_action":
        return "Corporate action decline"
    if z_val is not None and abs(z_val) >= 1.8:
        return "Oversold vs 60-day band"
    if ret <= -3:
        return "No clear driver"
    return "Broad market move"


def heuristic_verdict(row: Dict[str, Any], bucket: str) -> Dict[str, str]:
    """Fast verdict from precomputed movement fields (no LLM)."""
    ret = float(row.get("daily_return_pct") or 0)
    cat = row.get("catalyst_status") or "no_catalyst"
    headline = row.get("primary_cause_headline") or ""
    category = row.get("primary_cause_category") or ""
    z = row.get("return_zscore_60d")
    z_val = float(z) if z is not None else 0.0

    if _value_spike_override(bucket, category, headline, ret):
        verdict = "Hold"
        return {
            "verdict": verdict,
            "one_line_reason": _build_one_line_reason(
                row, bucket, verdict, adjustment_note="value_spike_override"
            ),
            "adjustment_note": "value_spike_override",
        }

    if bucket == "gainer":
        if cat == "symbol_specific" and ret >= 4:
            verdict = "Strong Buy"
        elif cat in ("symbol_specific", "macro_only") and ret >= 1.5:
            verdict = "Buy"
        elif ret >= 6 and cat == "no_catalyst":
            verdict = "Sell"
        else:
            verdict = "Hold"
        return {
            "verdict": verdict,
            "one_line_reason": _build_one_line_reason(row, bucket, verdict),
        }

    # loser bucket
    if z_val <= -2.0 and cat == "no_catalyst":
        verdict = "Buy"
    elif cat == "symbol_specific" and any(
        x in headline.lower() for x in ("downgrade", "miss", "cut", "layoff", "probe", "fraud")
    ):
        verdict = "Sell"
    elif ret <= -6:
        verdict = "Hold"
    else:
        verdict = "Hold"

    return {
        "verdict": verdict,
        "one_line_reason": _build_one_line_reason(row, bucket, verdict),
    }


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
                "volume": m.get("volume", 0),
                "relative_volume": m.get("relative_volume", 1.0),
                "return_zscore_60d": m.get("return_zscore_60d", 0.0),
                "catalyst_status": m.get("catalyst_status", "no_catalyst"),
                "primary_cause_category": m.get("primary_cause_category", "none"),
                "primary_cause_headline": m.get("primary_cause_headline", ""),
                "primary_cause_weight": m.get("primary_cause_weight", 0.0),
                "market_regime": m.get("market_regime", "Balanced"),
            }
            rows.append(_normalize_row(raw, bucket, i))
    return rows


def _payload_from_rows(
    rows: List[Dict[str, Any]],
    td: date,
    source: str,
    verdict_tier: str = "heuristic",
) -> Dict[str, Any]:
    for r in rows:
        r.setdefault("verdict_tier", verdict_tier)
    losers = [r for r in rows if r["bucket"] == "loser"]
    gainers = [r for r in rows if r["bucket"] == "gainer"]
    compelling = [r for r in rows if r.get("is_compelling")]
    return {
        "trade_date": _iso_date(td),
        "source": source,
        "verdict_tier": verdict_tier,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "losers": losers,
        "gainers": gainers,
        "compelling": compelling[:15],
        "rows": rows,
    }


def _row_to_bq_record(row: Dict[str, Any], trade_date: str, updated_at: str) -> Dict[str, Any]:
    return {
        "trade_date": trade_date,
        "bucket": row.get("bucket"),
        "rank": int(row.get("rank") or 0),
        "symbol": row.get("symbol"),
        "daily_return_pct": row.get("daily_return_pct"),
        "close": row.get("close"),
        "volume": row.get("volume"),
        "relative_volume": row.get("relative_volume"),
        "return_zscore_60d": row.get("return_zscore_60d"),
        "market_regime": row.get("market_regime"),
        "catalyst_status": row.get("catalyst_status"),
        "primary_cause_category": row.get("primary_cause_category"),
        "primary_cause_headline": row.get("primary_cause_headline"),
        "primary_cause_weight": row.get("primary_cause_weight"),
        "verdict": row.get("verdict"),
        "one_line_reason": row.get("one_line_reason"),
        "adjustment_note": row.get("adjustment_note"),
        "verdict_tier": row.get("verdict_tier", "heuristic"),
        "scorecard_signal": row.get("scorecard_signal"),
        "scorecard_ratio": row.get("scorecard_ratio"),
        "valuation_pct_vs_fair": row.get("valuation_pct_vs_fair"),
        "is_compelling": bool(row.get("is_compelling")),
        # New columns
        "preset": row.get("preset"),
        "revenue_growth_pct": row.get("revenue_growth_pct"),
        "eps_growth_pct": row.get("eps_growth_pct"),
        "dividend_yield_pct": row.get("dividend_yield_pct"),
        "debt_to_equity": row.get("debt_to_equity"),
        "beta": row.get("beta"),
        "updated_at": updated_at,
    }


def persist_snapshot(payload: Dict[str, Any]) -> int:
    """Upsert daily brief rows into daily_brief_snapshot table."""
    if _backend_type() not in ("bigquery", "duckdb"):
        return 0
    from backend.mcp_server.backend import backend
    from backend.mcp_server.bq_schema import FULL_DATASET

    trade_date = payload.get("trade_date")
    if not trade_date:
        return 0

    updated_at = datetime.now(timezone.utc).isoformat()
    rows = payload.get("rows") or []
    if not rows:
        return 0

    table_ref = f"`{FULL_DATASET}.daily_brief_snapshot`" if _backend_type() == "bigquery" else "daily_brief_snapshot"
    
    try:
        backend().execute(
            f"DELETE FROM {table_ref} "
            f"WHERE trade_date = DATE '{trade_date}'"
        )
    except Exception as e:
        logger.debug("[DailyBrief] Delete snapshot records failed: %s", e)

    records = [_row_to_bq_record(r, trade_date, updated_at) for r in rows]
    batch = 100
    total = 0
    for i in range(0, len(records), batch):
        total += backend().insert_rows("daily_brief_snapshot", records[i : i + batch])
    logger.info("[DailyBrief] Persisted %d snapshot rows for %s", total, trade_date)
    return total


def load_snapshot(trade_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
    if _backend_type() not in ("bigquery", "duckdb"):
        return None
    from backend.mcp_server.backend import backend
    from backend.mcp_server.bq_schema import FULL_DATASET

    if trade_date is None:
        trade_date = get_latest_trade_date()
    if trade_date is None:
        return None

    td = trade_date.isoformat()
    table_ref = f"`{FULL_DATASET}.daily_brief_snapshot`" if _backend_type() == "bigquery" else "daily_brief_snapshot"
    sql = f"""
        SELECT *
        FROM {table_ref}
        WHERE trade_date = DATE '{td}'
        ORDER BY symbol
    """
    try:
        raw = backend().query(sql)
    except Exception as e:
        logger.debug("[DailyBrief] Load snapshot failed: %s", e)
        return None

    if not raw:
        return None

    rows: List[Dict[str, Any]] = []
    tier = "heuristic"
    updated_at = None
    source = "database_snapshot"
    for r in raw:
        tier = r.get("verdict_tier") or tier
        updated_at = r.get("updated_at") or updated_at
        rows.append({
            "rank": r.get("rank"),
            "bucket": r.get("bucket"),
            "symbol": r.get("symbol"),
            "trade_date": _iso_date(r.get("trade_date")),
            "daily_return_pct": _num(r.get("daily_return_pct")),
            "close": _num(r.get("close")),
            "volume": r.get("volume"),
            "relative_volume": _num(r.get("relative_volume")),
            "return_zscore_60d": _num(r.get("return_zscore_60d")),
            "market_regime": r.get("market_regime"),
            "catalyst_status": r.get("catalyst_status"),
            "primary_cause_category": r.get("primary_cause_category"),
            "primary_cause_headline": r.get("primary_cause_headline"),
            "primary_cause_weight": _num(r.get("primary_cause_weight")),
            "verdict": r.get("verdict"),
            "one_line_reason": r.get("one_line_reason"),
            "adjustment_note": r.get("adjustment_note"),
            "verdict_tier": r.get("verdict_tier"),
            "scorecard_signal": r.get("scorecard_signal"),
            "scorecard_ratio": _num(r.get("scorecard_ratio")),
            "valuation_pct_vs_fair": _num(r.get("valuation_pct_vs_fair")),
            "is_compelling": bool(r.get("is_compelling")),
            # New columns
            "preset": r.get("preset"),
            "revenue_growth_pct": _num(r.get("revenue_growth_pct")),
            "eps_growth_pct": _num(r.get("eps_growth_pct")),
            "dividend_yield_pct": _num(r.get("dividend_yield_pct")),
            "debt_to_equity": _num(r.get("debt_to_equity")),
            "beta": _num(r.get("beta")),
        })
    return _payload_from_rows(rows, trade_date, source, verdict_tier=tier)


def _compute_movers(
    trade_date: Optional[date],
    n_losers: int,
    n_gainers: int,
) -> tuple[Optional[date], str, List[Dict[str, Any]]]:
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

    if td:
        td = _adjust_weekend_to_friday(td)

    return td, source, rows


def overlay_realtime_quotes(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    On trading days, overlay live Yahoo Finance quotes on top of
    EOD-based daily brief data.  Mutates payload in-place and adds a
    ``realtime_overlay`` flag so the frontend can show a "Live" badge.
    Active from 4 AM to midnight ET on weekdays; off on weekends.
    """
    from backend.market_intel import needs_realtime_overlay, fetch_realtime_quotes

    if not needs_realtime_overlay():
        payload["realtime_overlay"] = False
        return payload

    rows = payload.get("rows") or []
    symbols = [r["symbol"] for r in rows if r.get("symbol")]
    if not symbols:
        payload["realtime_overlay"] = False
        return payload

    try:
        quotes = fetch_realtime_quotes(symbols)
    except Exception as e:
        logger.warning("[DailyBrief] RT quote overlay failed: %s", e)
        payload["realtime_overlay"] = False
        return payload

    if not quotes:
        payload["realtime_overlay"] = False
        return payload

    overlaid = 0
    for row in rows:
        sym = row.get("symbol", "").upper()
        q = quotes.get(sym)
        if q:
            row["close"] = q["price"]
            row["daily_return_pct"] = q["pct"]
            row["_rt_previous_close"] = q["previous_close"]
            overlaid += 1

    # Also update losers / gainers / compelling sub-lists (they share row refs)
    if payload.get("losers"):
        for r in payload["losers"]:
            q = quotes.get((r.get("symbol") or "").upper())
            if q:
                r["close"] = q["price"]
                r["daily_return_pct"] = q["pct"]
    if payload.get("gainers"):
        for r in payload["gainers"]:
            q = quotes.get((r.get("symbol") or "").upper())
            if q:
                r["close"] = q["price"]
                r["daily_return_pct"] = q["pct"]
    if payload.get("compelling"):
        for r in payload["compelling"]:
            q = quotes.get((r.get("symbol") or "").upper())
            if q:
                r["close"] = q["price"]
                r["daily_return_pct"] = q["pct"]

    payload["realtime_overlay"] = overlaid > 0
    payload["rt_overlay_count"] = overlaid
    logger.info("[DailyBrief] RT overlay: %d/%d symbols updated", overlaid, len(symbols))
    return payload


STATIC_TICKER_METADATA_FALLBACKS: Dict[str, Dict[str, Any]] = {
    "AAPL": {
        "company_name": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "market_cap": 3400000000000,
        "pe_ratio": 30.5,
        "forward_pe": 28.0,
        "insider_sentiment": "0.1% Insiders",
    },
    "MSFT": {
        "company_name": "Microsoft Corporation",
        "sector": "Technology",
        "industry": "Software—Infrastructure",
        "market_cap": 3200000000000,
        "pe_ratio": 35.2,
        "forward_pe": 32.5,
        "insider_sentiment": "0.1% Insiders",
    },
    "NVDA": {
        "company_name": "NVIDIA Corporation",
        "sector": "Technology",
        "industry": "Semiconductors",
        "market_cap": 3100000000000,
        "pe_ratio": 65.4,
        "forward_pe": 40.0,
        "insider_sentiment": "4.2% Insiders",
    },
    "AMZN": {
        "company_name": "Amazon.com, Inc.",
        "sector": "Consumer Cyclical",
        "industry": "Internet Retail",
        "market_cap": 1900000000000,
        "pe_ratio": 40.1,
        "forward_pe": 35.0,
        "insider_sentiment": "9.7% Insiders",
    },
    "GOOGL": {
        "company_name": "Alphabet Inc.",
        "sector": "Communication Services",
        "industry": "Internet Content & Information",
        "market_cap": 2200000000000,
        "pe_ratio": 26.3,
        "forward_pe": 22.0,
        "insider_sentiment": "0.1% Insiders",
    },
    "GOOG": {
        "company_name": "Alphabet Inc.",
        "sector": "Communication Services",
        "industry": "Internet Content & Information",
        "market_cap": 2200000000000,
        "pe_ratio": 26.3,
        "forward_pe": 22.0,
        "insider_sentiment": "0.1% Insiders",
    },
    "META": {
        "company_name": "Meta Platforms, Inc.",
        "sector": "Communication Services",
        "industry": "Internet Content & Information",
        "market_cap": 1200000000000,
        "pe_ratio": 25.8,
        "forward_pe": 21.0,
        "insider_sentiment": "13.5% Insiders",
    },
    "TSLA": {
        "company_name": "Tesla, Inc.",
        "sector": "Consumer Cyclical",
        "industry": "Auto Manufacturers",
        "market_cap": 600000000000,
        "pe_ratio": 55.0,
        "forward_pe": 45.0,
        "insider_sentiment": "13.0% Insiders",
    },
    "AJG": {
        "company_name": "Arthur J. Gallagher & Co.",
        "sector": "Financial Services",
        "industry": "Insurance Brokers",
        "market_cap": 60000000000,
        "pe_ratio": 28.2,
        "forward_pe": 25.0,
        "insider_sentiment": "1.2% Insiders",
    },
    "EBAY": {
        "company_name": "eBay Inc.",
        "sector": "Consumer Cyclical",
        "industry": "Internet Retail",
        "market_cap": 25000000000,
        "pe_ratio": 15.4,
        "forward_pe": 12.0,
        "insider_sentiment": "0.2% Insiders",
    },
    "DLTR": {
        "company_name": "Dollar Tree, Inc.",
        "sector": "Consumer Defensive",
        "industry": "Discount Stores",
        "market_cap": 18000000000,
        "pe_ratio": 18.1,
        "forward_pe": 14.0,
        "insider_sentiment": "0.3% Insiders",
    },
    "ADBE": {
        "company_name": "Adobe Inc.",
        "sector": "Technology",
        "industry": "Application Software",
        "market_cap": 220000000000,
        "pe_ratio": 30.2,
        "forward_pe": 26.0,
        "insider_sentiment": "0.1% Insiders",
    },
    "LEN": {
        "company_name": "Lennar Corporation",
        "sector": "Consumer Cyclical",
        "industry": "Homebuilding",
        "market_cap": 45000000000,
        "pe_ratio": 10.5,
        "forward_pe": 9.5,
        "insider_sentiment": "1.5% Insiders",
    },
    "FOX": {
        "company_name": "Fox Corporation",
        "sector": "Communication Services",
        "industry": "Broadcasting",
        "market_cap": 18000000000,
        "pe_ratio": 12.5,
        "forward_pe": 11.0,
        "insider_sentiment": "0.8% Insiders",
    },
    "DOV": {
        "company_name": "Dover Corporation",
        "sector": "Industrials",
        "industry": "Specialty Industrial Machinery",
        "market_cap": 29300000000,
        "pe_ratio": 27.2,
        "forward_pe": 24.0,
        "insider_sentiment": "1.3% Insiders",
    },
    "SPY": {
        "company_name": "SPDR S&P 500 ETF Trust",
        "sector": "Exchange Traded Funds",
        "industry": "Exchange Traded Fund",
        "market_cap": 500000000000,
        "pe_ratio": 0.0,
        "forward_pe": 0.0,
        "insider_sentiment": "0.0% Insiders",
    },
    "QQQ": {
        "company_name": "Invesco QQQ Trust",
        "sector": "Exchange Traded Funds",
        "industry": "Exchange Traded Fund",
        "market_cap": 250000000000,
        "pe_ratio": 0.0,
        "forward_pe": 0.0,
        "insider_sentiment": "0.0% Insiders",
    },
    "IJR": {
        "company_name": "iShares Core S&P Small-Cap ETF",
        "sector": "Exchange Traded Funds",
        "industry": "Exchange Traded Fund",
        "market_cap": 80000000000,
        "pe_ratio": 0.0,
        "forward_pe": 0.0,
        "insider_sentiment": "0.0% Insiders",
    },
    "GLD": {
        "company_name": "SPDR Gold Shares",
        "sector": "Exchange Traded Funds",
        "industry": "Exchange Traded Fund",
        "market_cap": 75000000000,
        "pe_ratio": 0.0,
        "forward_pe": 0.0,
        "insider_sentiment": "0.0% Insiders",
    },
}


def enrich_daily_brief_rows(rows: List[Dict[str, Any]]) -> None:
    """Enrich daily brief rows with company metadata (industry, market cap, P/E, insider sentiment) from yfinance."""
    if not rows:
        return
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor
    from backend.connector_cache import get_cached, set_cached

    symbols = list({r["symbol"].upper() for r in rows if r.get("symbol")})
    if not symbols:
        return

    # Check cache first
    needed_symbols = []
    symbol_metadata = {}
    for sym in symbols:
        cached = get_cached("daily_brief_enrich", sym, ttl=86400)  # cache for 24 hours
        if cached:
            symbol_metadata[sym] = cached
        else:
            needed_symbols.append(sym)

    # Fetch from yfinance in parallel for missing symbols
    if needed_symbols:
        def fetch_info(sym: str) -> Optional[tuple[str, Optional[Dict[str, Any]]]]:
            try:
                t = yf.Ticker(sym)
                info = t.info or {}
                held = info.get("heldPercentInsiders")
                insider_sentiment = f"{held * 100:.1f}% Insiders" if held is not None else None
                
                # Check for rate-limiting or empty info
                if not info.get("industry"):
                    try:
                        from backend.deps import knowledge_store
                        rag_data = knowledge_store.get_sp500_fundamental(sym)
                        if rag_data:
                            return sym, {
                                "industry": rag_data["industry"],
                                "market_cap": rag_data["market_cap"],
                                "pe_ratio": rag_data["pe_ratio"],
                                "forward_pe": rag_data["forward_pe"],
                                "insider_sentiment": rag_data["insider_sentiment"]
                            }
                    except Exception:
                        pass
                    if sym in STATIC_TICKER_METADATA_FALLBACKS:
                        fb = STATIC_TICKER_METADATA_FALLBACKS[sym]
                        return sym, {
                            "industry": fb["industry"],
                            "market_cap": fb["market_cap"],
                            "pe_ratio": fb["pe_ratio"],
                            "forward_pe": fb["forward_pe"],
                            "insider_sentiment": fb["insider_sentiment"]
                        }
                
                res = {
                    "industry": info.get("industry") or "Unknown",
                    "market_cap": info.get("marketCap"),
                    "pe_ratio": info.get("trailingPE") or info.get("forwardPE"),
                    "forward_pe": info.get("forwardPE") or info.get("trailingPE"),
                    "insider_sentiment": insider_sentiment or "N/A"
                }
                return sym, res
            except Exception as e:
                logger.warning("[DailyBriefEnrich] failed to fetch %s: %s", sym, e)
                try:
                    from backend.deps import knowledge_store
                    rag_data = knowledge_store.get_sp500_fundamental(sym)
                    if rag_data:
                        return sym, {
                            "industry": rag_data["industry"],
                            "market_cap": rag_data["market_cap"],
                            "pe_ratio": rag_data["pe_ratio"],
                            "forward_pe": rag_data["forward_pe"],
                            "insider_sentiment": rag_data["insider_sentiment"]
                        }
                except Exception:
                    pass
                # Return static fallback if available
                if sym in STATIC_TICKER_METADATA_FALLBACKS:
                    fb = STATIC_TICKER_METADATA_FALLBACKS[sym]
                    return sym, {
                        "industry": fb["industry"],
                        "market_cap": fb["market_cap"],
                        "pe_ratio": fb["pe_ratio"],
                        "forward_pe": fb["forward_pe"],
                        "insider_sentiment": fb["insider_sentiment"]
                    }
                return sym, None

        with ThreadPoolExecutor(max_workers=min(len(needed_symbols), 10)) as executor:
            results = executor.map(fetch_info, needed_symbols)
            for res in results:
                if res:
                    sym, data = res
                    if data:
                        symbol_metadata[sym] = data
                        set_cached("daily_brief_enrich", data, sym)

    # Populate rows
    for r in rows:
        sym = r.get("symbol", "").upper()
        meta = symbol_metadata.get(sym)
        if meta:
            r["industry"] = meta.get("industry") or r.get("industry") or "Unknown"
            r["market_cap"] = meta.get("market_cap") or r.get("market_cap")
            r["pe_ratio"] = meta.get("pe_ratio") or r.get("pe_ratio")
            r["forward_pe"] = meta.get("forward_pe") or r.get("forward_pe")
            r["insider_sentiment"] = meta.get("insider_sentiment") or r.get("insider_sentiment") or "N/A"
        else:
            try:
                from backend.deps import knowledge_store
                rag_data = knowledge_store.get_sp500_fundamental(sym)
                if rag_data:
                    r["industry"] = rag_data["industry"]
                    r["market_cap"] = rag_data["market_cap"]
                    r["pe_ratio"] = rag_data["pe_ratio"]
                    r["forward_pe"] = rag_data["forward_pe"]
                    r["insider_sentiment"] = rag_data["insider_sentiment"]
                    continue
            except Exception:
                pass
            if sym in STATIC_TICKER_METADATA_FALLBACKS:
                fb = STATIC_TICKER_METADATA_FALLBACKS[sym]
                r["industry"] = fb["industry"]
                r["market_cap"] = fb["market_cap"]
                r["pe_ratio"] = fb["pe_ratio"]
                r["forward_pe"] = fb["forward_pe"]
                r["insider_sentiment"] = fb["insider_sentiment"]


def build_daily_brief(
    trade_date: Optional[date] = None,
    n_losers: int = 20,
    n_gainers: int = 10,
    *,
    use_snapshot: bool = True,
    persist: bool = False,
) -> Dict[str, Any]:
    td = trade_date or get_latest_trade_date()
    if td:
        td = _adjust_weekend_to_friday(td)
    if use_snapshot and td:
        cached = load_snapshot(td)
        if cached:
            cached["from_snapshot"] = True
            enrich_daily_brief_rows(cached.get("rows", []))
            # Re-sync sublists
            cached["losers"] = [r for r in cached["rows"] if r["bucket"] == "loser"]
            cached["gainers"] = [r for r in cached["rows"] if r["bucket"] == "gainer"]
            cached["compelling"] = [r for r in cached["rows"] if r.get("is_compelling")]
            return cached

    td, source, rows = _compute_movers(trade_date, n_losers, n_gainers)
    payload = _payload_from_rows(rows, td or date.today(), source, verdict_tier="heuristic")
    payload["from_snapshot"] = False
    enrich_daily_brief_rows(payload.get("rows", []))
    # Re-sync sublists
    payload["losers"] = [r for r in payload["rows"] if r["bucket"] == "loser"]
    payload["gainers"] = [r for r in payload["rows"] if r["bucket"] == "gainer"]
    payload["compelling"] = [r for r in payload["rows"] if r.get("is_compelling")]

    if persist and _backend_type() == "bigquery":
        persist_snapshot(payload)
    return payload


def materialize_heuristic_snapshot(
    trade_date: Optional[date] = None,
    n_losers: int = 20,
    n_gainers: int = 10,
) -> Dict[str, Any]:
    """Build heuristic brief and persist to daily_brief_snapshot (cron hook)."""
    payload = build_daily_brief(
        trade_date=trade_date,
        n_losers=n_losers,
        n_gainers=n_gainers,
        use_snapshot=False,
        persist=True,
    )
    return {"persisted_rows": len(payload.get("rows") or []), **payload}


def _valuation_pct_from_data(d) -> Optional[float]:
    if d.forward_pe and d.historical_avg_pe and d.historical_avg_pe > 0:
        return round((float(d.forward_pe) / float(d.historical_avg_pe) - 1.0) * 100.0, 2)
    return None


async def enrich_rows_quant(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach scorecard signal/ratio and valuation stretch (no per-ticker LLM)."""
    symbols = [r["symbol"] for r in rows if r.get("symbol")]
    if not symbols:
        return rows

    from backend.connectors.scorecard_data import fetch_basket
    from backend.scorecard import ScorecardInput, score_basket

    data_rows = await fetch_basket(symbols)
    by_ticker = {d.ticker: d for d in data_rows}
    inputs = [
        ScorecardInput(
            ticker=d.ticker,
            eps_growth_pct=d.eps_growth_pct,
            revenue_growth_pct=d.revenue_growth_pct,
            pt_upside_pct=d.pt_upside_pct,
            dividend_yield_pct=d.dividend_yield_pct,
            forward_pe=d.forward_pe,
            historical_avg_pe=d.historical_avg_pe,
            beta=d.beta,
            exec_risk_score=5.0,
            debt_to_equity=d.debt_to_equity,
            sitg_score=3.0,
            ceo_name=d.ceo_name,
            sitg_archetype="",
        )
        for d in data_rows
    ]
    basket = score_basket(inputs, preset="balanced") if inputs else None
    scored = {r.ticker: r for r in (basket.rows if basket else [])}

    out = []
    for row in rows:
        sym = row.get("symbol")
        d = by_ticker.get(sym)
        sc = scored.get(sym)
        enriched = dict(row)
        if sc:
            enriched["scorecard_signal"] = sc.signal
            enriched["scorecard_ratio"] = round(sc.ratio, 4)
        if d:
            enriched["valuation_pct_vs_fair"] = _valuation_pct_from_data(d)
        out.append(enriched)
    return out


async def apply_deep_verdicts(rows: List[Dict[str, Any]], llm_client) -> List[Dict[str, Any]]:
    """Single batched LLM pass to refine verdicts."""
    try:
        llm_rows = await llm_client.generate_daily_brief_batch(rows)
    except Exception as e:
        logger.warning("[DailyBrief] deep LLM batch failed: %s", e)
        return rows

    by_sym = {str(x.get("symbol", "")).upper(): x for x in llm_rows if x.get("symbol")}
    out = []
    for row in rows:
        sym = str(row.get("symbol", "")).upper()
        patch = by_sym.get(sym)
        merged = dict(row)
        if patch:
            v = patch.get("verdict")
            if v in VERDICT_ORDER:
                merged["verdict"] = v
            if patch.get("one_line_reason"):
                merged["one_line_reason"] = str(patch["one_line_reason"])[:500]
            merged["verdict_tier"] = "deep"
        out.append(merged)
    return out


def _fetch_all_symbols_from_db(trade_date: date) -> List[Dict[str, Any]]:
    from backend.mcp_server.backend import backend
    from backend.mcp_server.bq_schema import FULL_DATASET

    ds = FULL_DATASET
    td = trade_date.isoformat()
    is_bq = _backend_type() == "bigquery"
    
    dp_ref = f"`{ds}.daily_prices`" if is_bq else "daily_prices"
    dmf_ref = f"`{ds}.daily_movement_features`" if is_bq else "daily_movement_features"
    mcd_ref = f"`{ds}.movement_context_daily`" if is_bq else "movement_context_daily"

    try:
        sql = f"""
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
            FROM {dp_ref} p
            LEFT JOIN {dmf_ref} f
              ON p.symbol = f.symbol AND p.trade_date = f.trade_date
            LEFT JOIN {mcd_ref} c
              ON p.symbol = c.symbol AND p.trade_date = c.trade_date
            WHERE p.trade_date = DATE '{td}'
        """
        return backend().query(sql)
    except Exception as e:
        logger.debug("[DailyBrief] Full join query failed, trying daily_prices query only: %s", e)
        try:
            sql_fallback = f"""
                SELECT
                    p.symbol,
                    p.trade_date,
                    p.close,
                    p.volume,
                    p.daily_return_pct
                FROM {dp_ref} p
                WHERE p.trade_date = DATE '{td}'
            """
            return backend().query(sql_fallback)
        except Exception as e_fallback:
            logger.warning("[DailyBrief] Fallback daily_prices query failed: %s", e_fallback)
            return []


async def run_sp500_screener_pipeline(trade_date: date, llm_client) -> Dict[str, Any]:
    from backend.connectors.scorecard_data import fetch_basket
    from backend.scorecard import ScorecardInput, score_basket
    
    # 1. Resolve symbols
    try:
        from backend.market_intel import _get_sp500_universe
        symbols = _get_sp500_universe()
    except Exception:
        from backend.data_lake.config import SP500_TICKERS
        symbols = list(SP500_TICKERS)
        
    if not symbols:
        raise ValueError("No S&P 500 symbols found")

    # 2. Fetch scorecard data in throttled chunks
    _set_deep_job(status="running", progress=10, message="Fetching S&P 500 scorecard metrics...")
    data_rows = []
    chunk_size = 50
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i : i + chunk_size]
        _set_deep_job(message=f"Fetching metrics: {i}/{len(symbols)} symbols", progress=10 + int((i / len(symbols)) * 25))
        chunk_data = await fetch_basket(chunk)
        data_rows.extend(chunk_data)
        await asyncio.sleep(0.05)

    # 3. Fetch daily price/movement details for all symbols from DB for the target date
    _set_deep_job(progress=40, message="Loading daily price and movement context...")
    db_movements = {}
    try:
        db_rows = _fetch_all_symbols_from_db(trade_date)
        db_movements = {r["symbol"].upper(): r for r in db_rows if r.get("symbol")}
    except Exception as e:
        logger.warning("[DailyBrief] DB daily movement fetch failed: %s", e)

    # 4. Partition tickers into growth/income/value baskets
    _set_deep_job(progress=45, message="Segmenting and scoring baskets...")
    inputs = []
    for d in data_rows:
        inputs.append(
            ScorecardInput(
                ticker=d.ticker,
                eps_growth_pct=d.eps_growth_pct,
                revenue_growth_pct=d.revenue_growth_pct,
                pt_upside_pct=d.pt_upside_pct,
                dividend_yield_pct=d.dividend_yield_pct,
                forward_pe=d.forward_pe,
                historical_avg_pe=d.historical_avg_pe,
                beta=d.beta,
                exec_risk_score=5.0, # baseline
                debt_to_equity=d.debt_to_equity,
                sitg_score=3.0,      # baseline
                ceo_name=d.ceo_name,
                sitg_archetype="",
            )
        )
        
    growth_basket = []
    income_basket = []
    value_basket = []
    
    for inp in inputs:
        preset = classify_company_preset({
            "revenue_growth_pct": inp.revenue_growth_pct,
            "dividend_yield_pct": inp.dividend_yield_pct
        })
        if preset == "growth":
            growth_basket.append(inp)
        elif preset == "income":
            income_basket.append(inp)
        else:
            value_basket.append(inp)

    # 5. Run scorecard engine on each sub-basket
    scored_by_ticker = {}
    
    if growth_basket:
        res = score_basket(growth_basket, preset="growth")
        for r in res.rows:
            scored_by_ticker[r.ticker] = (r, "growth")
            
    if income_basket:
        res = score_basket(income_basket, preset="income")
        for r in res.rows:
            scored_by_ticker[r.ticker] = (r, "income")
            
    if value_basket:
        res = score_basket(value_basket, preset="value")
        for r in res.rows:
            scored_by_ticker[r.ticker] = (r, "value")

    # 6. Build raw output rows
    raw_rows = []
    for d in data_rows:
        ticker = d.ticker
        mv = db_movements.get(ticker.upper()) or {}
        sc_tuple = scored_by_ticker.get(ticker)
        
        sc_row = sc_tuple[0] if sc_tuple else None
        preset = sc_tuple[1] if sc_tuple else "value"
        
        sig = sc_row.signal if sc_row else "Balanced"
        ratio = sc_row.ratio if sc_row else 1.0
        
        verdict = scorecard_verdict_mapping(sig)
        
        row = {
            "rank": 0, # Not ranked for the full screener
            "bucket": mv.get("bucket") or ("gainer" if (mv.get("daily_return_pct") or 0.0) >= 0 else "loser"),
            "symbol": ticker,
            "trade_date": trade_date.isoformat(),
            "daily_return_pct": _num(mv.get("daily_return_pct")) or 0.0,
            "close": _num(mv.get("close") or d.current_price),
            "volume": mv.get("volume") or 0,
            "relative_volume": _num(mv.get("relative_volume")) or 1.0,
            "return_zscore_60d": _num(mv.get("return_zscore_60d")) or 0.0,
            "market_regime": mv.get("market_regime") or "Balanced",
            "catalyst_status": mv.get("catalyst_status") or "no_catalyst",
            "primary_cause_category": mv.get("primary_cause_category") or "news",
            "primary_cause_headline": mv.get("primary_cause_headline") or "",
            "primary_cause_weight": _num(mv.get("primary_cause_weight")) or 0.0,
            "verdict": verdict,
            "one_line_reason": sc_row.action if sc_row else "Hold, monitor catalysts",
            "adjustment_note": "scorecard_screener",
            "verdict_tier": "heuristic",
            "scorecard_signal": sig,
            "scorecard_ratio": _num(ratio),
            "valuation_pct_vs_fair": _num(d.forward_pe / d.historical_avg_pe - 1.0) * 100.0 if (d.forward_pe and d.historical_avg_pe) else None,
            "is_compelling": sig in ("Exceptional", "Strong buy"),
            # New columns
            "preset": preset,
            "revenue_growth_pct": _num(d.revenue_growth_pct),
            "eps_growth_pct": _num(d.eps_growth_pct),
            "dividend_yield_pct": _num(d.dividend_yield_pct),
            "debt_to_equity": _num(d.debt_to_equity),
            "beta": _num(d.beta),
        }
        raw_rows.append(row)

    # 7. Run batch LLM verdict pipeline on actionable signals (verdict IN ("Strong Buy", "Buy", "Sell"))
    actionable_rows = [r for r in raw_rows if r["verdict"] in ("Strong Buy", "Buy", "Sell")]
    _set_deep_job(progress=70, message=f"Running batch LLM verdicts for {len(actionable_rows)} actionable tickers...")
    
    # We run in chunks of 30
    llm_rows = []
    chunk_size = 30
    for i in range(0, len(actionable_rows), chunk_size):
        chunk = actionable_rows[i : i + chunk_size]
        _set_deep_job(message=f"LLM verdicts: {i}/{len(actionable_rows)} tickers", progress=70 + int((i / len(actionable_rows)) * 20))
        refined_chunk = await apply_deep_verdicts(chunk, llm_client)
        llm_rows.extend(refined_chunk)
        
    # Merge LLM results back
    llm_by_symbol = {r["symbol"].upper(): r for r in llm_rows}
    final_rows = []
    for r in raw_rows:
        patch = llm_by_symbol.get(r["symbol"].upper())
        if patch:
            merged = dict(r)
            merged["verdict"] = patch.get("verdict", r["verdict"])
            merged["one_line_reason"] = patch.get("one_line_reason", r["one_line_reason"])
            merged["verdict_tier"] = "deep"
            final_rows.append(merged)
        else:
            final_rows.append(r)

    # 8. Persist snapshot
    _set_deep_job(progress=90, message="Persisting S&P 500 snapshot...")
    payload = {
        "trade_date": trade_date.isoformat(),
        "source": "sp500_screener",
        "verdict_tier": "deep",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "rows": final_rows,
    }
    persist_snapshot(payload)
    # Emit daily_brief ingestion candidate
    try:
        from backend.ingestion_agent import emit_ingestion_candidate
        symbols_list = [r["symbol"] for r in final_rows if r.get("symbol")]
        asyncio.create_task(
            emit_ingestion_candidate(
                source_type="daily_brief",
                symbols=symbols_list,
                triggered_by="scheduler",
                raw_payload=payload,
                feed_source="sp500_screener",
                as_of_ts=payload["updated_at"],
            )
        )
    except Exception as e:
        logger.warning("[IngestionHook] Daily brief candidate failed: %s", e)
    return payload


# In-process deep-refresh job state (single worker)
_deep_job_lock = threading.Lock()
_deep_job: Dict[str, Any] = {
    "status": "idle",
    "trade_date": None,
    "progress": 0,
    "message": "",
    "error": None,
    "updated_at": None,
}


def get_deep_refresh_status() -> Dict[str, Any]:
    with _deep_job_lock:
        return dict(_deep_job)


def _set_deep_job(**kwargs: Any) -> None:
    with _deep_job_lock:
        _deep_job.update(kwargs)
        _deep_job["updated_at"] = datetime.now(timezone.utc).isoformat()


async def run_deep_refresh(
    trade_date: Optional[date] = None,
    n_losers: int = 20,
    n_gainers: int = 10,
    llm_client=None,
) -> Dict[str, Any]:
    from backend.deps import llm_client as default_llm

    llm = llm_client or default_llm
    if get_deep_refresh_status().get("status") == "running":
        return get_deep_refresh_status()

    _set_deep_job(status="running", progress=5, message="Resolving trade date", error=None)
    try:
        td = trade_date or get_latest_trade_date() or date.today()
        payload = await run_sp500_screener_pipeline(td, llm)
        
        _set_deep_job(
            status="done",
            progress=100,
            message="Deep refresh complete",
            trade_date=td.isoformat(),
            error=None,
        )
        payload["deep_refresh"] = get_deep_refresh_status()
        return payload
    except Exception as e:
        logger.exception("[DailyBrief] deep refresh failed")
        _set_deep_job(status="error", progress=100, message="Failed", error=str(e))
        raise


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Daily brief materialization")
    parser.add_argument("--materialize-heuristic", action="store_true")
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--losers", type=int, default=20)
    parser.add_argument("--gainers", type=int, default=10)
    args = parser.parse_args()
    td = date.fromisoformat(args.trade_date) if args.trade_date else None
    if args.materialize_heuristic:
        result = materialize_heuristic_snapshot(td, args.losers, args.gainers)
        print(result)
        return
    result = build_daily_brief(td, args.losers, args.gainers)
    print(json.dumps({"trade_date": result["trade_date"], "rows": len(result["rows"])}, indent=2))


if __name__ == "__main__":
    main()
