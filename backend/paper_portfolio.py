"""
Paper Portfolio — virtual $10,000 per user.
"""
import sqlite3
import json
import os
import time
import threading
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .portfolio_holdings_reconcile import (
    aggregate_open_long_positions,
    normalize_extracted_holdings,
    normalize_ticker,
)
from .progress_db import resolve_progress_db_path

DB_PATH = resolve_progress_db_path()
_local  = threading.local()
STARTING_CASH = 10_000.0
SPY_TICKER    = "SPY"
UNKNOWN_CATEGORY = "Unknown"
_PERF_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_PERF_CACHE_TTL_SEC = 45.0


def _use_postgres() -> bool:
    try:
        from .postgres_config import postgres_enabled

        return postgres_enabled()
    except Exception:
        return False


def _get_conn():
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_portfolio_db():
    if _use_postgres():
        from . import paper_portfolio_pg as pg
        import logging

        try:
            pg.init_schema()
            pg.migrate_from_sqlite_if_needed()
        except Exception as exc:
            logging.getLogger(__name__).error(
                "[paper_portfolio] Postgres init failed — falling back to SQLite: %s", exc
            )
        else:
            return
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS paper_positions (
            id           TEXT NOT NULL,
            user_id      TEXT NOT NULL,
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
            realised_pnl REAL DEFAULT NULL,
            PRIMARY KEY (id, user_id)
        );
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            user_id         TEXT NOT NULL,
            snapshot_date   TEXT NOT NULL,
            portfolio_value REAL NOT NULL,
            spy_value       REAL NOT NULL,
            positions_json  TEXT NOT NULL,
            recorded_at     REAL NOT NULL,
            PRIMARY KEY (user_id, snapshot_date)
        );
        CREATE TABLE IF NOT EXISTS stocks (
            ticker         TEXT PRIMARY KEY,
            ceo_name       TEXT,
            sitg_score     REAL,
            ceo_base_salary REAL,
            sitg_value      REAL,
            sitg_multiple   REAL,
            sitg_percentile_tier TEXT,
            insider_buy_count_12m INTEGER,
            insider_sell_count_12m INTEGER,
            insider_net_shares_12m REAL,
            held_percent_insiders REAL,
            updated_at     REAL
        );
    """)
    _ensure_position_metadata_columns(conn)
    conn.commit()
    try:
        from . import portfolio_memory as pm

        pm.init_portfolio_memory_db()
    except Exception:
        pass


def _ensure_position_metadata_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(paper_positions)").fetchall()
    }
    columns = {
        "sector": "TEXT DEFAULT 'Unknown'",
        "market_cap": "REAL DEFAULT NULL",
        "cap_bucket": "TEXT DEFAULT 'Unknown'",
        "asset_type": "TEXT DEFAULT 'Unknown'",
    }
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE paper_positions ADD COLUMN {name} {ddl}")


def _classify_market_cap(market_cap: Optional[float]) -> str:
    if not market_cap or market_cap <= 0:
        return UNKNOWN_CATEGORY
    if market_cap >= 200_000_000_000:
        return "Mega Cap"
    if market_cap >= 10_000_000_000:
        return "Large Cap"
    if market_cap >= 2_000_000_000:
        return "Mid Cap"
    if market_cap >= 300_000_000:
        return "Small Cap"
    return "Micro Cap"


def _portfolio_category_from_info(info: Dict[str, Any]) -> Dict[str, Any]:
    quote_type = str(info.get("quoteType") or info.get("typeDisp") or "").upper()
    sector = info.get("sector") or info.get("category") or UNKNOWN_CATEGORY
    market_cap = info.get("marketCap")
    try:
        market_cap = float(market_cap) if market_cap is not None else None
    except (TypeError, ValueError):
        market_cap = None
    asset_type = "ETF" if quote_type in {"ETF", "MUTUALFUND"} else (quote_type.title() or "Equity")
    return {
        "sector": str(sector or UNKNOWN_CATEGORY),
        "market_cap": market_cap,
        "cap_bucket": _classify_market_cap(market_cap),
        "asset_type": asset_type,
    }


def _fetch_ticker_profile(ticker: str) -> Dict[str, Any]:
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).info or {}
    except Exception:
        info = {}
    return _portfolio_category_from_info(info)


def add_position(
    user_id: str,
    ticker: str,
    direction: str,
    allocated: Optional[float] = None,
    source: str = "manual",
    note: str = "",
    *,
    price: Optional[float] = None,
    shares: Optional[float] = None,
) -> Dict[str, Any]:
    if _use_postgres():
        from . import paper_portfolio_pg as pg

        return pg.add_position(
            user_id, ticker, direction, allocated, source, note, price=price, shares=shares
        )
    ticker = normalize_ticker(ticker)
    if not ticker:
        return {"error": "Ticker is required"}
    try:
        if price is None:
            import yfinance as yf

            price = float(yf.Ticker(ticker).fast_info["lastPrice"])
        else:
            price = float(price)
    except Exception:
        return {"error": f"Could not fetch price for {ticker}"}
    if price <= 0:
        return {"error": "Price must be positive"}
    if shares is not None:
        shares = round(float(shares), 6)
        if shares <= 0:
            return {"error": "Shares must be positive"}
        allocated = round(shares * price, 6)
    else:
        allocated = float(allocated if allocated is not None else 1000.0)
        if allocated <= 0:
            return {"error": "Amount must be positive"}
        shares = round(allocated / price, 6)
    profile = _fetch_ticker_profile(ticker)
    pos_id = f"{ticker}_{int(time.time() * 1000)}"
    conn   = _get_conn()
    conn.execute("""
        INSERT INTO paper_positions
        (id, user_id, ticker, direction, entry_price, entry_date, shares, allocated, source, note,
         sector, market_cap, cap_bucket, asset_type)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (pos_id, user_id, ticker.upper(), direction.upper(), price,
          date.today().isoformat(), shares, allocated, source, note,
          profile["sector"], profile["market_cap"], profile["cap_bucket"], profile["asset_type"]))
    conn.commit()
    invalidate_portfolio_performance_cache(user_id)
    result = {"id": pos_id, "ticker": ticker.upper(), "direction": direction.upper(),
              "entry_price": price, "entry_date": date.today().isoformat(),
              "shares": shares, "allocated": allocated, **profile}
    try:
        from . import portfolio_memory as pm

        pm.emit_position_added(
            user_id,
            ticker.upper(),
            shares=shares,
            entry_price=price,
            sector=profile.get("sector", ""),
            source=source,
        )
    except Exception:
        pass
    return result


def _spy_benchmark_return_pct(first_date_iso: str) -> Optional[float]:
    """
    SPY return % from the portfolio's earliest open position date through the latest quote.

    Uses prior session close when all positions were opened today (imports / same-day adds).
    """
    try:
        import yfinance as yf

        since = date.fromisoformat(str(first_date_iso)[:10])
        today = date.today()
        ticker = yf.Ticker(SPY_TICKER)

        if since >= today:
            hist = ticker.history(period="10d", auto_adjust=True)
            if hist is None or hist.empty:
                return None
            if len(hist) >= 2:
                spy_entry = float(hist["Close"].iloc[-2])
            else:
                spy_entry = float(hist["Close"].iloc[0])
        else:
            end = today + timedelta(days=1)
            hist = ticker.history(start=since, end=end, auto_adjust=True)
            if hist is None or hist.empty:
                return None
            spy_entry = float(hist["Close"].iloc[0])

        spy_now = float(hist["Close"].iloc[-1])
        try:
            live = ticker.fast_info.get("lastPrice")
            if live is not None:
                v = float(live)
                if v > 0:
                    spy_now = v
        except Exception:
            pass

        if spy_entry <= 0:
            return None
        return ((spy_now - spy_entry) / spy_entry) * 100
    except Exception:
        return None


def get_positions(user_id: str, include_closed: bool = False) -> List[Dict[str, Any]]:
    if _use_postgres():
        from . import paper_portfolio_pg as pg

        return pg.get_positions(user_id, include_closed=include_closed)
    conn = _get_conn()
    if include_closed:
        rows = conn.execute("SELECT * FROM paper_positions WHERE user_id=? ORDER BY entry_date DESC", (user_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM paper_positions WHERE user_id=? AND closed=0 ORDER BY entry_date DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def invalidate_portfolio_performance_cache(user_id: str | None = None) -> None:
    """Drop cached performance snapshot after portfolio mutations."""
    if user_id:
        _PERF_CACHE.pop(user_id, None)
    else:
        _PERF_CACHE.clear()


def _fetch_last_prices_batch(tickers: List[str]) -> Dict[str, float]:
    """Fetch last prices for many tickers in one yfinance call (fallback: per-ticker)."""
    if not tickers:
        return {}
    prices: Dict[str, float] = {}
    unique = list(dict.fromkeys(str(t).strip().upper() for t in tickers if t))

    # Reuse hot L1 cache quotes when available (avoids network for mag7/ETFs).
    try:
        from . import market_l1_cache

        snap = market_l1_cache.get_snapshot() or {}
        quote_map = dict((snap.get("quotes") or {}))
        quote_map.update((snap.get("sector_etfs") or {}))
        for sym in unique:
            cached = quote_map.get(sym)
            if cached is not None:
                try:
                    prices[sym] = float(cached)
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass

    remaining = [t for t in unique if t not in prices]
    if remaining:
        try:
            import yfinance as yf

            raw = yf.download(
                " ".join(remaining),
                period="1d",
                interval="1d",
                group_by="ticker",
                progress=False,
                threads=True,
            )
            if raw is not None and not getattr(raw, "empty", True):
                if len(remaining) == 1:
                    sym = remaining[0]
                    try:
                        prices[sym] = float(raw["Close"].iloc[-1])
                    except Exception:
                        pass
                else:
                    for sym in remaining:
                        try:
                            val = float(raw[sym]["Close"].iloc[-1])
                            if val == val:  # skip NaN
                                prices[sym] = val
                        except Exception:
                            pass
        except Exception:
            pass

    for sym in unique:
        if sym in prices:
            continue
        try:
            import yfinance as yf

            prices[sym] = float(yf.Ticker(sym).fast_info["lastPrice"])
        except Exception:
            prices[sym] = 0.0
    return prices


def get_portfolio_performance(user_id: str, *, use_cache: bool = True) -> Dict[str, Any]:
    if use_cache:
        cached = _PERF_CACHE.get(user_id)
        if cached and (time.time() - cached[0]) < _PERF_CACHE_TTL_SEC:
            return cached[1]

    positions = get_positions(user_id)
    if not positions:
        empty = {"positions": [], "total_value": STARTING_CASH, "total_pnl": 0.0,
                "total_pnl_pct": 0.0, "spy_pnl_pct": 0.0, "beating_spy": False,
                "starting_cash": STARTING_CASH,
                "analysis": {"by_sector": {}, "by_cap_bucket": {}, "by_asset_type": {}}}
        _PERF_CACHE[user_id] = (time.time(), empty)
        return empty
    try:
        tickers_needed = list({p["ticker"] for p in positions})
        prices = _fetch_last_prices_batch(tickers_needed)
    except Exception:
        prices = {}

    total_cost  = sum(p["allocated"] for p in positions)
    total_value = 0.0
    first_date  = min(p["entry_date"] for p in positions) if positions else date.today().isoformat()
    spy_pnl_raw = _spy_benchmark_return_pct(first_date)
    spy_pnl_pct = round(spy_pnl_raw, 2) if spy_pnl_raw is not None else None

    enriched = []
    by_sector: Dict[str, float] = {}
    by_cap_bucket: Dict[str, float] = {}
    by_asset_type: Dict[str, float] = {}
    for p in positions:
        cur = prices.get(p["ticker"], p["entry_price"])
        if p["direction"] == "LONG":
            pos_value  = p["shares"] * cur
            pnl_dollar = pos_value - p["allocated"]
        else:
            pnl_dollar = p["allocated"] - (p["shares"] * cur)
            pos_value  = p["allocated"] + pnl_dollar
        pnl_pct = (pnl_dollar / p["allocated"] * 100) if p["allocated"] else 0.0
        total_value += pos_value
        sector = p.get("sector") or UNKNOWN_CATEGORY
        cap_bucket = p.get("cap_bucket") or UNKNOWN_CATEGORY
        asset_type = p.get("asset_type") or UNKNOWN_CATEGORY
        by_sector[sector] = by_sector.get(sector, 0.0) + pos_value
        by_cap_bucket[cap_bucket] = by_cap_bucket.get(cap_bucket, 0.0) + pos_value
        by_asset_type[asset_type] = by_asset_type.get(asset_type, 0.0) + pos_value
        enriched.append({**p, "current_price": cur, "current_value": round(pos_value, 2),
                         "pnl_dollar": round(pnl_dollar, 2), "pnl_pct": round(pnl_pct, 2)})

    total_pnl     = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0.0
    result = {
        "positions":     enriched,
        "total_value":   round(total_value, 2),
        "starting_cash": STARTING_CASH,
        "total_pnl":     round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "spy_pnl_pct":   spy_pnl_pct,
        "spy_benchmark_available": spy_pnl_pct is not None,
        "beating_spy":   (
            total_pnl_pct > spy_pnl_pct if spy_pnl_pct is not None else False
        ),
        "analysis": {
            "by_sector": {k: round(v, 2) for k, v in sorted(by_sector.items())},
            "by_cap_bucket": {k: round(v, 2) for k, v in sorted(by_cap_bucket.items())},
            "by_asset_type": {k: round(v, 2) for k, v in sorted(by_asset_type.items())},
        },
    }
    _PERF_CACHE[user_id] = (time.time(), result)
    return result


def _quiet_close_open_long(conn: sqlite3.Connection, user_id: str, ticker: str, today_iso: str) -> int:
    """Close all open LONG rows for ticker at breakeven (for import replace)."""
    rows = conn.execute(
        """
        SELECT id FROM paper_positions
        WHERE user_id=? AND ticker=? AND closed=0 AND direction='LONG'
        """,
        (user_id, ticker),
    ).fetchall()
    for r in rows:
        conn.execute(
            """
            UPDATE paper_positions
            SET closed=1, exit_price=entry_price, exit_date=?, realised_pnl=0
            WHERE id=? AND user_id=?
            """,
            (today_iso, r["id"], user_id),
        )
    return len(rows)


def _resolve_import_entry_price(ticker: str, avg_cost: Optional[float]) -> float:
    if avg_cost is not None and float(avg_cost) > 0:
        return float(avg_cost)
    try:
        import yfinance as yf

        return float(yf.Ticker(ticker).fast_info["lastPrice"])
    except Exception as exc:
        raise ValueError(f"Could not resolve price for {ticker}: {exc}") from exc


def _insert_explicit_long(
    conn: sqlite3.Connection,
    user_id: str,
    ticker: str,
    shares: float,
    entry_price: float,
    source: str,
    note: str,
    today_iso: str,
) -> str:
    profile = _fetch_ticker_profile(ticker)
    pos_id = f"{ticker}_{int(time.time() * 1000)}"
    allocated = round(float(shares) * float(entry_price), 6)
    conn.execute(
        """
        INSERT INTO paper_positions
        (id, user_id, ticker, direction, entry_price, entry_date, shares, allocated, source, note,
         sector, market_cap, cap_bucket, asset_type)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            pos_id,
            user_id,
            ticker,
            "LONG",
            float(entry_price),
            today_iso,
            float(shares),
            allocated,
            source,
            note or "",
            profile["sector"],
            profile["market_cap"],
            profile["cap_bucket"],
            profile["asset_type"],
        ),
    )
    return pos_id


def apply_holdings_import(
    user_id: str,
    raw_items: List[Dict[str, Any]],
    *,
    full_snapshot: bool = False,
    source: str = "holdings_import",
    note: str = "",
) -> Dict[str, Any]:
    """
    Replace open LONG positions from an imported holdings list (per-ticker upsert).
    When full_snapshot is True, open LONG tickers missing from the list are closed.
    """
    if _use_postgres():
        from . import paper_portfolio_pg as pg

        return pg.apply_holdings_import(
            user_id, raw_items, full_snapshot=full_snapshot, source=source, note=note
        )
    items = normalize_extracted_holdings(raw_items)
    errors: List[Dict[str, Any]] = []
    applied: List[str] = []
    conn = _get_conn()
    today = date.today().isoformat()
    current = aggregate_open_long_positions(get_positions(user_id, include_closed=False))
    prior_aggregated = dict(current)
    item_tickers = {normalize_ticker(str(i.get("ticker") or "")) for i in items}
    removed_tickers: List[str] = []

    if full_snapshot:
        for t in list(current.keys()):
            if t not in item_tickers:
                removed_tickers.append(t)
                _quiet_close_open_long(conn, user_id, t, today)

    for it in items:
        t = normalize_ticker(str(it.get("ticker") or ""))
        if not t:
            continue
        sh_raw, ac_raw = it.get("shares"), it.get("avg_cost")
        if sh_raw is None or float(sh_raw) <= 0:
            errors.append({"ticker": t, "error": "shares must be provided and positive"})
            continue
        try:
            entry = _resolve_import_entry_price(t, float(ac_raw) if ac_raw is not None else None)
        except ValueError as e:
            errors.append({"ticker": t, "error": str(e)})
            continue
        _quiet_close_open_long(conn, user_id, t, today)
        _insert_explicit_long(
            conn, user_id, t, float(sh_raw), entry, source, note, today
        )
        applied.append(t)

    conn.commit()
    invalidate_portfolio_performance_cache(user_id)
    items_by_ticker = {
        normalize_ticker(str(it.get("ticker") or "")): {
            "shares": float(it["shares"]) if it.get("shares") is not None else 0.0,
            "avg_cost": float(it["avg_cost"]) if it.get("avg_cost") is not None else None,
        }
        for it in items
        if normalize_ticker(str(it.get("ticker") or ""))
    }
    try:
        from . import portfolio_memory as pm

        pm.emit_import_events(
            user_id,
            applied_tickers=applied,
            removed_tickers=removed_tickers,
            items_by_ticker=items_by_ticker,
            prior_aggregated=prior_aggregated,
            source=source,
            full_snapshot=full_snapshot,
        )
    except Exception:
        pass
    return {"applied": applied, "errors": errors}


def close_position(user_id: str, position_id: str) -> Dict[str, Any]:
    if _use_postgres():
        from . import paper_portfolio_pg as pg

        return pg.close_position(user_id, position_id)
    conn = _get_conn()
    row  = conn.execute(
        "SELECT * FROM paper_positions WHERE id=? AND user_id=? AND closed=0",
        (position_id, user_id)
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
        WHERE id=? AND user_id=?
    """, (exit_price, date.today().isoformat(), round(pnl, 2), position_id, user_id))
    conn.commit()
    invalidate_portfolio_performance_cache(user_id)
    result = {"closed": True, "realised_pnl": round(pnl, 2), "exit_price": exit_price}
    try:
        from . import portfolio_memory as pm

        pm.emit_position_removed(
            user_id,
            row["ticker"],
            reason="closed",
            realised_pnl=round(pnl, 2),
        )
    except Exception:
        pass
    return result


def get_all_unique_portfolio_tickers() -> List[str]:
    if _use_postgres():
        try:
            from . import paper_portfolio_pg as pg
            return pg.get_all_unique_portfolio_tickers()
        except Exception:
            pass
    conn = _get_conn()
    rows = conn.execute("SELECT DISTINCT ticker FROM paper_positions WHERE closed = 0").fetchall()
    return [row["ticker"] for row in rows]


def upsert_stock_sec_info(
    ticker: str,
    ceo_name: str,
    sitg_score: float,
    ceo_base_salary: Optional[float],
    sitg_value: Optional[float],
    sitg_multiple: Optional[float],
    sitg_percentile_tier: Optional[str],
    insider_buy_count_12m: int,
    insider_sell_count_12m: int,
    insider_net_shares_12m: float,
    held_percent_insiders: float,
) -> None:
    if _use_postgres():
        try:
            from . import paper_portfolio_pg as pg
            pg.upsert_stock_sec_info(
                ticker, ceo_name, sitg_score, ceo_base_salary, sitg_value, sitg_multiple,
                sitg_percentile_tier, insider_buy_count_12m, insider_sell_count_12m,
                insider_net_shares_12m, held_percent_insiders
            )
            return
        except Exception:
            pass
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO stocks (
            ticker, ceo_name, sitg_score, ceo_base_salary, sitg_value, sitg_multiple,
            sitg_percentile_tier, insider_buy_count_12m, insider_sell_count_12m,
            insider_net_shares_12m, held_percent_insiders, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            ceo_name=excluded.ceo_name,
            sitg_score=excluded.sitg_score,
            ceo_base_salary=excluded.ceo_base_salary,
            sitg_value=excluded.sitg_value,
            sitg_multiple=excluded.sitg_multiple,
            sitg_percentile_tier=excluded.sitg_percentile_tier,
            insider_buy_count_12m=excluded.insider_buy_count_12m,
            insider_sell_count_12m=excluded.insider_sell_count_12m,
            insider_net_shares_12m=excluded.insider_net_shares_12m,
            held_percent_insiders=excluded.held_percent_insiders,
            updated_at=excluded.updated_at
        """,
        (
            ticker.upper(), ceo_name, sitg_score, ceo_base_salary, sitg_value, sitg_multiple,
            sitg_percentile_tier, insider_buy_count_12m, insider_sell_count_12m,
            insider_net_shares_12m, held_percent_insiders, time.time()
        )
    )
    conn.commit()


def get_stock_sec_info(ticker: str) -> Optional[Dict[str, Any]]:
    if _use_postgres():
        try:
            from . import paper_portfolio_pg as pg
            return pg.get_stock_sec_info(ticker)
        except Exception:
            pass
    conn = _get_conn()
    row = conn.execute("SELECT * FROM stocks WHERE ticker = ?", (ticker.upper(),)).fetchone()
    if row:
        return dict(row)
    return None
