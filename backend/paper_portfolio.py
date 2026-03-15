"""
Paper Portfolio — virtual $10,000 investment tracking.

Users add positions after running a debate or valuation. The app tracks
daily P&L vs SPY using yFinance and awards XP when picks beat the market.
"""
import sqlite3
import json
import os
import time
import threading
from datetime import date, datetime
from typing import Any, Dict, List, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "progress.db")
_local  = threading.local()

STARTING_CASH = 10_000.0
SPY_TICKER    = "SPY"


def _get_conn():
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_portfolio_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS paper_positions (
            id           TEXT PRIMARY KEY,
            ticker       TEXT NOT NULL,
            direction    TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
            entry_price  REAL NOT NULL,
            entry_date   TEXT NOT NULL,
            shares       REAL NOT NULL,
            allocated    REAL NOT NULL,
            source       TEXT DEFAULT 'manual',
            note         TEXT DEFAULT '',
            closed       INTEGER DEFAULT 0,
            exit_price   REAL DEFAULT NULL,
            exit_date    TEXT DEFAULT NULL,
            realised_pnl REAL DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            snapshot_date  TEXT PRIMARY KEY,
            portfolio_value REAL NOT NULL,
            spy_value       REAL NOT NULL,
            positions_json  TEXT NOT NULL,
            recorded_at     REAL NOT NULL
        );
    """)
    conn.commit()


def add_position(ticker: str, direction: str, allocated: float,
                 source: str = "manual", note: str = "") -> Dict[str, Any]:
    """Open a new paper position. Fetches current price from yFinance."""
    try:
        import yfinance as yf
        price = float(yf.Ticker(ticker).fast_info["lastPrice"])
    except Exception:
        return {"error": f"Could not fetch price for {ticker}"}

    shares = round(allocated / price, 6)
    pos_id = f"{ticker}_{int(time.time())}"
    conn   = _get_conn()
    conn.execute("""
        INSERT INTO paper_positions
        (id, ticker, direction, entry_price, entry_date, shares, allocated, source, note)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (pos_id, ticker.upper(), direction.upper(), price,
          date.today().isoformat(), shares, allocated, source, note))
    conn.commit()
    return {
        "id":          pos_id,
        "ticker":      ticker.upper(),
        "direction":   direction.upper(),
        "entry_price": price,
        "entry_date":  date.today().isoformat(),
        "shares":      shares,
        "allocated":   allocated,
    }


def get_positions(include_closed: bool = False) -> List[Dict[str, Any]]:
    conn = _get_conn()
    q = "SELECT * FROM paper_positions" if include_closed else \
        "SELECT * FROM paper_positions WHERE closed=0"
    rows = conn.execute(q + " ORDER BY entry_date DESC").fetchall()
    return [dict(r) for r in rows]


def get_portfolio_performance() -> Dict[str, Any]:
    """
    Fetch current prices for all open positions, compute P&L, compare to SPY.
    """
    positions = get_positions()
    if not positions:
        return {"positions": [], "total_value": STARTING_CASH,
                "total_pnl": 0.0, "total_pnl_pct": 0.0,
                "spy_pnl_pct": 0.0, "beating_spy": False}

    try:
        import yfinance as yf
        tickers_needed = list({p["ticker"] for p in positions} | {SPY_TICKER})
        prices: Dict[str, float] = {}
        for t in tickers_needed:
            try:
                prices[t] = float(yf.Ticker(t).fast_info["lastPrice"])
            except Exception:
                prices[t] = 0.0
    except Exception:
        prices = {}

    total_cost     = sum(p["allocated"] for p in positions)
    total_value    = 0.0
    enriched       = []

    # SPY baseline: weighted average entry-day SPY price
    spy_now = prices.get(SPY_TICKER, 0.0)
    first_date = min(p["entry_date"] for p in positions) if positions else date.today().isoformat()
    try:
        import yfinance as yf
        spy_hist = yf.Ticker(SPY_TICKER).history(start=first_date, period="2d")
        spy_entry = float(spy_hist["Close"].iloc[0]) if not spy_hist.empty else spy_now
    except Exception:
        spy_entry = spy_now

    spy_pnl_pct = ((spy_now - spy_entry) / spy_entry * 100) if spy_entry else 0.0

    for p in positions:
        cur = prices.get(p["ticker"], p["entry_price"])
        if p["direction"] == "LONG":
            pos_value = p["shares"] * cur
            pnl_dollar = pos_value - p["allocated"]
        else:
            pnl_dollar = p["allocated"] - (p["shares"] * cur)
            pos_value  = p["allocated"] + pnl_dollar

        pnl_pct = (pnl_dollar / p["allocated"] * 100) if p["allocated"] else 0.0
        total_value += pos_value
        enriched.append({
            **p,
            "current_price": cur,
            "current_value": round(pos_value, 2),
            "pnl_dollar":    round(pnl_dollar, 2),
            "pnl_pct":       round(pnl_pct, 2),
        })

    total_pnl     = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0.0

    return {
        "positions":      enriched,
        "total_value":    round(total_value, 2),
        "starting_cash":  STARTING_CASH,
        "total_pnl":      round(total_pnl, 2),
        "total_pnl_pct":  round(total_pnl_pct, 2),
        "spy_pnl_pct":    round(spy_pnl_pct, 2),
        "beating_spy":    total_pnl_pct > spy_pnl_pct,
    }


def close_position(position_id: str) -> Dict[str, Any]:
    """Close a position, realise P&L."""
    conn = _get_conn()
    row  = conn.execute(
        "SELECT * FROM paper_positions WHERE id=? AND closed=0", (position_id,)
    ).fetchone()
    if not row:
        return {"error": "Position not found or already closed"}

    try:
        import yfinance as yf
        exit_price = float(yf.Ticker(row["ticker"]).fast_info["lastPrice"])
    except Exception:
        return {"error": "Could not fetch exit price"}

    if row["direction"] == "LONG":
        pnl = (exit_price - row["entry_price"]) * row["shares"]
    else:
        pnl = (row["entry_price"] - exit_price) * row["shares"]

    conn.execute("""
        UPDATE paper_positions
        SET closed=1, exit_price=?, exit_date=?, realised_pnl=?
        WHERE id=?
    """, (exit_price, date.today().isoformat(), round(pnl, 2), position_id))
    conn.commit()
    return {"closed": True, "realised_pnl": round(pnl, 2), "exit_price": exit_price}
