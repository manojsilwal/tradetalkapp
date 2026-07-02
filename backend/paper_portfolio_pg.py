"""Paper portfolio persistence on Cloud SQL Postgres."""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import paper_portfolio as pp
from .portfolio_holdings_reconcile import normalize_ticker
from .postgres_config import postgres_connection_kwargs, postgres_dsn, postgres_enabled
from .progress_db import resolve_progress_db_path

logger = logging.getLogger(__name__)

_local = None


def enabled() -> bool:
    return postgres_enabled()


def _connect():
    import psycopg2
    from psycopg2.extras import RealDictCursor

    return psycopg2.connect(postgres_dsn(), cursor_factory=RealDictCursor)


def _get_conn():
    global _local
    import threading

    if _local is None:
        _local = threading.local()
    if not hasattr(_local, "conn") or _local.conn.closed:
        _local.conn = _connect()
    return _local.conn


def init_schema() -> None:
    mig_dir = Path(__file__).resolve().parent / "migrations" / "postgres"
    conn = _get_conn()
    with conn.cursor() as cur:
        for name in (
            "001_paper_portfolio.sql",
            "002_portfolio_memory.sql",
            "003_snapshot_spy_return.sql",
            "006_stocks_sec_info.sql",
            "007_stocks_revenue_engine.sql",
            "008_page_feedback.sql",
            "009_filing_intelligence.sql",
        ):
            cur.execute((mig_dir / name).read_text(encoding="utf-8"))
    conn.commit()
    logger.info("[paper_portfolio_pg] schema ready on %s", postgres_connection_kwargs()["host"])


def migrate_from_sqlite_if_needed() -> None:
    """One-time copy from local progress.db when Postgres has no positions."""
    sqlite_path = resolve_progress_db_path()
    if not Path(sqlite_path).is_file():
        return
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM paper_positions")
        row = cur.fetchone()
        if row and int(row["n"]) > 0:
            return
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    try:
        rows = src.execute("SELECT * FROM paper_positions").fetchall()
    except sqlite3.OperationalError:
        src.close()
        logger.info("[paper_portfolio_pg] SQLite has no paper_positions table — nothing to migrate")
        return
    src.close()
    if not rows:
        return
    cols = [
        "id", "user_id", "ticker", "direction", "entry_price", "entry_date", "shares",
        "allocated", "source", "note", "closed", "exit_price", "exit_date", "realised_pnl",
        "sector", "market_cap", "cap_bucket", "asset_type",
    ]
    with conn.cursor() as cur:
        for r in rows:
            d = dict(r)
            for c in ("sector", "cap_bucket", "asset_type"):
                d.setdefault(c, "Unknown")
            cur.execute(
                f"""
                INSERT INTO paper_positions ({", ".join(cols)})
                VALUES ({", ".join("%s" for _ in cols)})
                ON CONFLICT (id, user_id) DO NOTHING
                """,
                [d.get(c) for c in cols],
            )
    conn.commit()
    logger.info("[paper_portfolio_pg] migrated %d positions from SQLite", len(rows))
    pp.invalidate_portfolio_performance_cache()


def _row_to_dict(row) -> Dict[str, Any]:
    if row is None:
        return {}
    return dict(row)


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
    return _pg_add_position_impl(
        user_id, ticker, direction, allocated, source, note, price=price, shares=shares
    )


def get_positions(user_id: str, include_closed: bool = False) -> List[Dict[str, Any]]:
    conn = _get_conn()
    with conn.cursor() as cur:
        if include_closed:
            cur.execute(
                "SELECT * FROM paper_positions WHERE user_id=%s ORDER BY entry_date DESC",
                (user_id,),
            )
        else:
            cur.execute(
                "SELECT * FROM paper_positions WHERE user_id=%s AND closed=0 ORDER BY entry_date DESC",
                (user_id,),
            )
        return [_row_to_dict(r) for r in cur.fetchall()]


def _pg_add_position_impl(
    user_id: str,
    ticker: str,
    direction: str,
    allocated: Optional[float],
    source: str,
    note: str,
    price: Optional[float],
    shares: Optional[float],
) -> Dict[str, Any]:
    """Internal insert used when routing from paper_portfolio.add_position."""
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
    profile = pp._fetch_ticker_profile(ticker)
    pos_id = f"{ticker}_{int(time.time() * 1000)}"
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper_positions
            (id, user_id, ticker, direction, entry_price, entry_date, shares, allocated,
             source, note, sector, market_cap, cap_bucket, asset_type)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                pos_id, user_id, ticker.upper(), direction.upper(), price,
                date.today().isoformat(), shares, allocated, source, note,
                profile["sector"], profile["market_cap"], profile["cap_bucket"], profile["asset_type"],
            ),
        )
    conn.commit()
    pp.invalidate_portfolio_performance_cache(user_id)
    result = {
        "id": pos_id, "ticker": ticker.upper(), "direction": direction.upper(),
        "entry_price": price, "entry_date": date.today().isoformat(),
        "shares": shares, "allocated": allocated, **profile,
    }
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


def _quiet_close_open_long(conn, user_id: str, ticker: str, today_iso: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM paper_positions
            WHERE user_id=%s AND ticker=%s AND closed=0 AND direction='LONG'
            """,
            (user_id, ticker),
        )
        rows = cur.fetchall()
        for r in rows:
            cur.execute(
                """
                UPDATE paper_positions
                SET closed=1, exit_price=entry_price, exit_date=%s, realised_pnl=0
                WHERE id=%s AND user_id=%s
                """,
                (today_iso, r["id"], user_id),
            )
    return len(rows)


def _insert_explicit_long(
    conn,
    user_id: str,
    ticker: str,
    shares: float,
    entry_price: float,
    source: str,
    note: str,
    today_iso: str,
) -> str:
    profile = pp._fetch_ticker_profile(ticker)
    pos_id = f"{ticker}_{int(time.time() * 1000)}"
    allocated = round(float(shares) * float(entry_price), 6)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper_positions
            (id, user_id, ticker, direction, entry_price, entry_date, shares, allocated,
             source, note, sector, market_cap, cap_bucket, asset_type)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                pos_id, user_id, ticker, "LONG", float(entry_price), today_iso,
                float(shares), allocated, source, note or "",
                profile["sector"], profile["market_cap"], profile["cap_bucket"], profile["asset_type"],
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
    from .portfolio_holdings_reconcile import aggregate_open_long_positions, normalize_extracted_holdings

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
            entry = pp._resolve_import_entry_price(t, float(ac_raw) if ac_raw is not None else None)
        except ValueError as e:
            errors.append({"ticker": t, "error": str(e)})
            continue
        _quiet_close_open_long(conn, user_id, t, today)
        _insert_explicit_long(conn, user_id, t, float(sh_raw), entry, source, note, today)
        applied.append(t)

    conn.commit()
    pp.invalidate_portfolio_performance_cache(user_id)
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
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM paper_positions WHERE id=%s AND user_id=%s AND closed=0",
            (position_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        return {"error": "Position not found or already closed"}
    row = _row_to_dict(row)
    try:
        import yfinance as yf

        exit_price = float(yf.Ticker(row["ticker"]).fast_info["lastPrice"])
    except Exception:
        return {"error": "Could not fetch exit price"}
    if row["direction"] == "LONG":
        pnl = (exit_price - row["entry_price"]) * row["shares"]
    else:
        pnl = (row["entry_price"] - exit_price) * row["shares"]
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE paper_positions
            SET closed=1, exit_price=%s, exit_date=%s, realised_pnl=%s
            WHERE id=%s AND user_id=%s
            """,
            (exit_price, date.today().isoformat(), round(pnl, 2), position_id, user_id),
        )
    conn.commit()
    pp.invalidate_portfolio_performance_cache(user_id)
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
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ticker FROM paper_positions WHERE closed = 0")
        return [row["ticker"] for row in cur.fetchall()]


def upsert_stock_sec_info(
    ticker: str,
    ceo_name: Optional[str] = None,
    sitg_score: Optional[float] = None,
    ceo_base_salary: Optional[float] = None,
    sitg_value: Optional[float] = None,
    sitg_multiple: Optional[float] = None,
    sitg_percentile_tier: Optional[str] = None,
    insider_buy_count_12m: Optional[int] = None,
    insider_sell_count_12m: Optional[int] = None,
    insider_net_shares_12m: Optional[float] = None,
    held_percent_insiders: Optional[float] = None,
    financial_traction_score: Optional[float] = None,
    customer_adoption_score: Optional[float] = None,
    management_commitment_score: Optional[float] = None,
    market_opportunity_score: Optional[float] = None,
    monetization_clarity_score: Optional[float] = None,
    execution_capacity_score: Optional[float] = None,
    new_revenue_engine_score: Optional[float] = None,
) -> None:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stocks (
                ticker, ceo_name, sitg_score, ceo_base_salary, sitg_value, sitg_multiple,
                sitg_percentile_tier, insider_buy_count_12m, insider_sell_count_12m,
                insider_net_shares_12m, held_percent_insiders, financial_traction_score,
                customer_adoption_score, management_commitment_score, market_opportunity_score,
                monetization_clarity_score, execution_capacity_score, new_revenue_engine_score, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker) DO UPDATE SET
                ceo_name=COALESCE(EXCLUDED.ceo_name, ceo_name),
                sitg_score=COALESCE(EXCLUDED.sitg_score, sitg_score),
                ceo_base_salary=COALESCE(EXCLUDED.ceo_base_salary, ceo_base_salary),
                sitg_value=COALESCE(EXCLUDED.sitg_value, sitg_value),
                sitg_multiple=COALESCE(EXCLUDED.sitg_multiple, sitg_multiple),
                sitg_percentile_tier=COALESCE(EXCLUDED.sitg_percentile_tier, sitg_percentile_tier),
                insider_buy_count_12m=COALESCE(EXCLUDED.insider_buy_count_12m, insider_buy_count_12m),
                insider_sell_count_12m=COALESCE(EXCLUDED.insider_sell_count_12m, insider_sell_count_12m),
                insider_net_shares_12m=COALESCE(EXCLUDED.insider_net_shares_12m, insider_net_shares_12m),
                held_percent_insiders=COALESCE(EXCLUDED.held_percent_insiders, held_percent_insiders),
                financial_traction_score=COALESCE(EXCLUDED.financial_traction_score, financial_traction_score),
                customer_adoption_score=COALESCE(EXCLUDED.customer_adoption_score, customer_adoption_score),
                management_commitment_score=COALESCE(EXCLUDED.management_commitment_score, management_commitment_score),
                market_opportunity_score=COALESCE(EXCLUDED.market_opportunity_score, market_opportunity_score),
                monetization_clarity_score=COALESCE(EXCLUDED.monetization_clarity_score, monetization_clarity_score),
                execution_capacity_score=COALESCE(EXCLUDED.execution_capacity_score, execution_capacity_score),
                new_revenue_engine_score=COALESCE(EXCLUDED.new_revenue_engine_score, new_revenue_engine_score),
                updated_at=EXCLUDED.updated_at
            """,
            (
                ticker.upper(), ceo_name, sitg_score, ceo_base_salary, sitg_value, sitg_multiple,
                sitg_percentile_tier, insider_buy_count_12m, insider_sell_count_12m,
                insider_net_shares_12m, held_percent_insiders, financial_traction_score,
                customer_adoption_score, management_commitment_score, market_opportunity_score,
                monetization_clarity_score, execution_capacity_score, new_revenue_engine_score, time.time()
            )
        )
    conn.commit()


def get_stock_sec_info(ticker: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM stocks WHERE ticker = %s", (ticker.upper(),))
        row = cur.fetchone()
        if row:
            return dict(row)
    return None


def upsert_filing_intelligence_record(record: Dict[str, Any]) -> None:
    import json as _json

    ticker = (record.get("ticker") or "").upper().strip()
    if not ticker:
        return
    conn = _get_conn()
    end_market = record.get("end_market_exposure") or {}
    tags = record.get("thematic_tags") or []
    citations = record.get("citations") or []
    raw = record.get("raw_extract_json") or record
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO filing_intelligence (
                ticker, as_of_date, filing_form, filing_risk_score, management_tone_score,
                new_product_expansion_score, customer_concentration_score, demand_visibility_score,
                order_backlog_usd, backlog_growth_yoy_pct, book_to_bill_ratio, recurring_revenue_pct,
                top_customer_concentration_pct, end_market_exposure_json, primary_moat_driver,
                thematic_tags_json, demand_visibility_summary, citations_json, raw_extract_json,
                extracted_at_utc, source
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (ticker) DO UPDATE SET
                as_of_date = EXCLUDED.as_of_date,
                filing_form = EXCLUDED.filing_form,
                filing_risk_score = EXCLUDED.filing_risk_score,
                management_tone_score = EXCLUDED.management_tone_score,
                new_product_expansion_score = EXCLUDED.new_product_expansion_score,
                customer_concentration_score = EXCLUDED.customer_concentration_score,
                demand_visibility_score = EXCLUDED.demand_visibility_score,
                order_backlog_usd = EXCLUDED.order_backlog_usd,
                backlog_growth_yoy_pct = EXCLUDED.backlog_growth_yoy_pct,
                book_to_bill_ratio = EXCLUDED.book_to_bill_ratio,
                recurring_revenue_pct = EXCLUDED.recurring_revenue_pct,
                top_customer_concentration_pct = EXCLUDED.top_customer_concentration_pct,
                end_market_exposure_json = EXCLUDED.end_market_exposure_json,
                primary_moat_driver = EXCLUDED.primary_moat_driver,
                thematic_tags_json = EXCLUDED.thematic_tags_json,
                demand_visibility_summary = EXCLUDED.demand_visibility_summary,
                citations_json = EXCLUDED.citations_json,
                raw_extract_json = EXCLUDED.raw_extract_json,
                extracted_at_utc = EXCLUDED.extracted_at_utc,
                source = EXCLUDED.source
            """,
            (
                ticker,
                record.get("as_of_date"),
                record.get("filing_form"),
                record.get("filing_risk_score"),
                record.get("management_tone_score"),
                record.get("new_product_expansion_score"),
                record.get("customer_concentration_score"),
                record.get("demand_visibility_score"),
                record.get("order_backlog_usd"),
                record.get("backlog_growth_yoy_pct"),
                record.get("book_to_bill_ratio"),
                record.get("recurring_revenue_pct"),
                record.get("top_customer_concentration_pct"),
                _json.dumps(end_market),
                record.get("primary_moat_driver"),
                _json.dumps(tags),
                record.get("demand_visibility_summary"),
                _json.dumps(citations),
                _json.dumps(raw, default=str),
                record.get("extracted_at_utc"),
                record.get("source") or "fincrawler",
            ),
        )
    conn.commit()


def get_filing_intelligence_record(ticker: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM filing_intelligence WHERE ticker = %s", (ticker.upper(),))
        row = cur.fetchone()
        if row:
            return dict(row)
    return None


def get_filing_intelligence_bulk(tickers: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    syms = [(t or "").upper().strip() for t in tickers if (t or "").strip()]
    if not syms:
        return {}
    conn = _get_conn()
    out: Dict[str, Dict[str, Any]] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM filing_intelligence WHERE ticker = ANY(%s)",
            (syms,),
        )
        for row in cur.fetchall():
            d = dict(row)
            out[d["ticker"]] = d
    return out
