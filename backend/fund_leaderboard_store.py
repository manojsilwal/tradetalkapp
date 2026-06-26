"""
Persistence layer for the Fund Leaderboard (13F clone-return pipeline).

Dual-backend (SQLite local / Postgres prod) following the pattern used in
``backend/portfolio_snapshots_job.py``. Schema is portable across both engines:
TEXT primary keys (uuid hex), ISO-8601 TEXT dates, REAL numerics, and JSON stored
as TEXT. We deliberately do NOT use Postgres-only UUID/JSONB/TIMESTAMPTZ types so
the exact same DDL/queries run on SQLite.

Tables (mirrors backend/migrations/fund_leaderboard_schema.sql, simplified):
- fund_master                 — manager registry (cik, name, type, strategy tags)
- sec_filings                 — per-quarter 13F-HR filings
- thirteen_f_holdings         — normalized holdings per filing (ticker/weight/value)
- fund_return_metrics         — computed CAGR/alpha/sharpe/... per fund+mode+period
- fund_leaderboard_snapshots  — ranked, presentable rows (metrics_json) per run
- cusip_ticker_cache          — CUSIP -> ticker/sector resolution cache (OpenFIGI)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_local = threading.local()

METHODOLOGY_VERSION = "fund-leaderboard-v1.0"
DEFAULT_MODE = "13f_investable"


# ── DB path / connection ──────────────────────────────────────────────────────

def resolve_db_path() -> str:
    explicit = os.environ.get("FUND_LEADERBOARD_DB_PATH", "").strip()
    if explicit:
        parent = os.path.dirname(explicit)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return explicit
    data_dir = os.environ.get("TRADETALK_DATA_DIR", "").strip()
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "fund_leaderboard.db")
    return os.path.join(_BACKEND_DIR, "fund_leaderboard.db")


def _use_postgres() -> bool:
    try:
        from .postgres_config import postgres_enabled

        return postgres_enabled()
    except Exception:
        return False


def _pg_connect():
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from .postgres_config import postgres_dsn

    return psycopg2.connect(postgres_dsn(), cursor_factory=RealDictCursor)


def _sqlite_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        conn = sqlite3.connect(resolve_db_path(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return _local.conn


def _ph(sql: str) -> str:
    """Convert ``?`` placeholders to ``%s`` for psycopg2."""
    return sql.replace("?", "%s") if _use_postgres() else sql


@contextmanager
def _cursor(commit: bool = False):
    """Yield a (conn, cursor) tuple for either backend."""
    if _use_postgres():
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                yield conn, cur
            if commit:
                conn.commit()
        finally:
            conn.close()
    else:
        conn = _sqlite_conn()
        cur = conn.cursor()
        try:
            yield conn, cur
            if commit:
                conn.commit()
        finally:
            cur.close()


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return dict(row)  # sqlite3.Row supports dict()


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _loads(val: Any, default: Any) -> Any:
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


# ── Schema ──────────────────────────────────────────────────────────────────

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS fund_master (
        fund_id TEXT PRIMARY KEY,
        cik TEXT UNIQUE,
        display_name TEXT NOT NULL,
        manager_type TEXT,
        strategy_tags TEXT DEFAULT '[]',
        include_in_leaderboard INTEGER DEFAULT 1,
        is_index_manager INTEGER DEFAULT 0,
        latest_aum_usd REAL,
        created_at TEXT,
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sec_filings (
        filing_id TEXT PRIMARY KEY,
        fund_id TEXT,
        cik TEXT NOT NULL,
        accession_number TEXT NOT NULL,
        form_type TEXT,
        report_period TEXT,
        filing_date TEXT,
        filing_url TEXT,
        total_market_value_usd REAL,
        created_at TEXT,
        UNIQUE(cik, accession_number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS thirteen_f_holdings (
        holding_id TEXT PRIMARY KEY,
        filing_id TEXT,
        fund_id TEXT,
        report_period TEXT,
        issuer_name TEXT,
        cusip TEXT,
        ticker TEXT,
        sector TEXT,
        shares REAL,
        market_value_usd REAL,
        holding_weight REAL,
        put_call TEXT,
        mapping_status TEXT DEFAULT 'unmapped',
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fund_return_metrics (
        id TEXT PRIMARY KEY,
        fund_id TEXT,
        mode TEXT NOT NULL,
        period TEXT NOT NULL,
        as_of_date TEXT NOT NULL,
        cagr REAL,
        roic_proxy REAL,
        alpha_vs_sp500 REAL,
        sharpe REAL,
        sortino REAL,
        max_drawdown REAL,
        positive_quarter_rate REAL,
        cumulative_return REAL,
        benchmark_cumulative_return REAL,
        data_confidence_score REAL,
        series_json TEXT,
        methodology_version TEXT,
        calculated_at TEXT,
        UNIQUE(fund_id, mode, period, methodology_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fund_leaderboard_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        as_of_date TEXT NOT NULL,
        latest_report_period TEXT,
        fund_id TEXT,
        mode TEXT NOT NULL,
        rank INTEGER,
        leaderboard_score REAL,
        methodology_version TEXT,
        metrics_json TEXT,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cusip_ticker_cache (
        cusip TEXT PRIMARY KEY,
        ticker TEXT,
        name TEXT,
        sector TEXT,
        mapping_status TEXT,
        updated_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_lb_mode_rank ON fund_leaderboard_snapshots(mode, rank)",
    "CREATE INDEX IF NOT EXISTS idx_holdings_filing ON thirteen_f_holdings(filing_id)",
    "CREATE INDEX IF NOT EXISTS idx_filings_fund ON sec_filings(fund_id, report_period)",
]


def init_schema() -> None:
    """Idempotently create all tables. Safe to call at startup and in tests."""
    with _cursor(commit=True) as (_conn, cur):
        for ddl in _DDL:
            cur.execute(ddl)
    logger.info("[FundLeaderboard] schema initialized (postgres=%s)", _use_postgres())


# ── Fund master ───────────────────────────────────────────────────────────────

def upsert_fund(
    cik: str,
    display_name: str,
    manager_type: Optional[str] = None,
    strategy_tags: Optional[List[str]] = None,
    is_index_manager: bool = False,
    latest_aum_usd: Optional[float] = None,
) -> str:
    """Insert or update a manager by CIK. Returns the fund_id."""
    cik = str(cik).strip()
    existing = get_fund_by_cik(cik)
    now = _now()
    tags = json.dumps(strategy_tags or [])
    if existing:
        fund_id = existing["fund_id"]
        with _cursor(commit=True) as (_c, cur):
            cur.execute(
                _ph(
                    """
                    UPDATE fund_master
                    SET display_name=?, manager_type=COALESCE(?, manager_type),
                        strategy_tags=?, is_index_manager=?, latest_aum_usd=COALESCE(?, latest_aum_usd),
                        updated_at=?
                    WHERE fund_id=?
                    """
                ),
                (display_name, manager_type, tags, 1 if is_index_manager else 0,
                 latest_aum_usd, now, fund_id),
            )
        return fund_id

    fund_id = _new_id()
    with _cursor(commit=True) as (_c, cur):
        cur.execute(
            _ph(
                """
                INSERT INTO fund_master
                (fund_id, cik, display_name, manager_type, strategy_tags,
                 include_in_leaderboard, is_index_manager, latest_aum_usd, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """
            ),
            (fund_id, cik, display_name, manager_type, tags, 1,
             1 if is_index_manager else 0, latest_aum_usd, now, now),
        )
    return fund_id


def get_fund_by_cik(cik: str) -> Optional[Dict[str, Any]]:
    with _cursor() as (_c, cur):
        cur.execute(_ph("SELECT * FROM fund_master WHERE cik=?"), (str(cik).strip(),))
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def get_fund(fund_id: str) -> Optional[Dict[str, Any]]:
    with _cursor() as (_c, cur):
        cur.execute(_ph("SELECT * FROM fund_master WHERE fund_id=?"), (fund_id,))
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def count_funds() -> int:
    with _cursor() as (_c, cur):
        cur.execute("SELECT COUNT(*) AS n FROM fund_master")
        row = cur.fetchone()
    d = _row_to_dict(row)
    return int(d.get("n", 0) or 0)


# ── Filings + holdings ─────────────────────────────────────────────────────────

def upsert_filing(
    fund_id: str,
    cik: str,
    accession_number: str,
    form_type: str,
    report_period: str,
    filing_date: str,
    filing_url: str,
    total_market_value_usd: Optional[float] = None,
) -> str:
    existing = None
    with _cursor() as (_c, cur):
        cur.execute(
            _ph("SELECT filing_id FROM sec_filings WHERE cik=? AND accession_number=?"),
            (str(cik).strip(), accession_number),
        )
        existing = cur.fetchone()
    if existing:
        filing_id = _row_to_dict(existing)["filing_id"]
        with _cursor(commit=True) as (_c, cur):
            cur.execute(
                _ph(
                    """
                    UPDATE sec_filings
                    SET fund_id=?, form_type=?, report_period=?, filing_date=?,
                        filing_url=?, total_market_value_usd=COALESCE(?, total_market_value_usd)
                    WHERE filing_id=?
                    """
                ),
                (fund_id, form_type, report_period, filing_date, filing_url,
                 total_market_value_usd, filing_id),
            )
        return filing_id

    filing_id = _new_id()
    with _cursor(commit=True) as (_c, cur):
        cur.execute(
            _ph(
                """
                INSERT INTO sec_filings
                (filing_id, fund_id, cik, accession_number, form_type, report_period,
                 filing_date, filing_url, total_market_value_usd, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """
            ),
            (filing_id, fund_id, str(cik).strip(), accession_number, form_type,
             report_period, filing_date, filing_url, total_market_value_usd, _now()),
        )
    return filing_id


def replace_holdings(
    filing_id: str,
    fund_id: str,
    report_period: str,
    holdings: List[Dict[str, Any]],
) -> int:
    """Delete + reinsert holdings for a filing (idempotent re-ingest)."""
    now = _now()
    with _cursor(commit=True) as (_c, cur):
        cur.execute(_ph("DELETE FROM thirteen_f_holdings WHERE filing_id=?"), (filing_id,))
        for h in holdings:
            cur.execute(
                _ph(
                    """
                    INSERT INTO thirteen_f_holdings
                    (holding_id, filing_id, fund_id, report_period, issuer_name, cusip,
                     ticker, sector, shares, market_value_usd, holding_weight, put_call,
                     mapping_status, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """
                ),
                (
                    _new_id(), filing_id, fund_id, report_period,
                    h.get("issuer_name"), h.get("cusip"), h.get("ticker"),
                    h.get("sector"), h.get("shares"), h.get("market_value_usd"),
                    h.get("holding_weight"), h.get("put_call"),
                    h.get("mapping_status", "unmapped"), now,
                ),
            )
    return len(holdings)


def get_filings_for_fund(fund_id: str, limit: int = 24) -> List[Dict[str, Any]]:
    with _cursor() as (_c, cur):
        cur.execute(
            _ph(
                """
                SELECT * FROM sec_filings
                WHERE fund_id=? AND form_type IN ('13F-HR','13F-HR/A')
                ORDER BY report_period DESC
                LIMIT ?
                """
            ),
            (fund_id, limit),
        )
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def get_holdings_for_filing(filing_id: str) -> List[Dict[str, Any]]:
    with _cursor() as (_c, cur):
        cur.execute(
            _ph("SELECT * FROM thirteen_f_holdings WHERE filing_id=? ORDER BY market_value_usd DESC"),
            (filing_id,),
        )
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def get_latest_filing(fund_id: str) -> Optional[Dict[str, Any]]:
    filings = get_filings_for_fund(fund_id, limit=1)
    return filings[0] if filings else None


# ── CUSIP -> ticker cache ───────────────────────────────────────────────────────

def cache_get_ticker(cusip: str) -> Optional[Dict[str, Any]]:
    if not cusip:
        return None
    with _cursor() as (_c, cur):
        cur.execute(_ph("SELECT * FROM cusip_ticker_cache WHERE cusip=?"), (cusip,))
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def cache_put_ticker(
    cusip: str,
    ticker: Optional[str],
    name: Optional[str] = None,
    sector: Optional[str] = None,
    mapping_status: str = "mapped",
) -> None:
    if not cusip:
        return
    with _cursor(commit=True) as (_c, cur):
        cur.execute(
            _ph(
                """
                INSERT INTO cusip_ticker_cache (cusip, ticker, name, sector, mapping_status, updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(cusip) DO UPDATE SET
                    ticker=excluded.ticker, name=excluded.name, sector=excluded.sector,
                    mapping_status=excluded.mapping_status, updated_at=excluded.updated_at
                """
            ),
            (cusip, ticker, name, sector, mapping_status, _now()),
        )


# ── Return metrics ──────────────────────────────────────────────────────────────

def upsert_return_metrics(
    fund_id: str,
    mode: str,
    period: str,
    as_of_date: str,
    metrics: Dict[str, Any],
    data_confidence_score: Optional[float] = None,
    series: Optional[List[Dict[str, Any]]] = None,
) -> None:
    with _cursor(commit=True) as (_c, cur):
        cur.execute(
            _ph(
                """
                INSERT INTO fund_return_metrics
                (id, fund_id, mode, period, as_of_date, cagr, roic_proxy, alpha_vs_sp500,
                 sharpe, sortino, max_drawdown, positive_quarter_rate, cumulative_return,
                 benchmark_cumulative_return, data_confidence_score, series_json,
                 methodology_version, calculated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(fund_id, mode, period, methodology_version) DO UPDATE SET
                    as_of_date=excluded.as_of_date, cagr=excluded.cagr,
                    roic_proxy=excluded.roic_proxy, alpha_vs_sp500=excluded.alpha_vs_sp500,
                    sharpe=excluded.sharpe, sortino=excluded.sortino,
                    max_drawdown=excluded.max_drawdown,
                    positive_quarter_rate=excluded.positive_quarter_rate,
                    cumulative_return=excluded.cumulative_return,
                    benchmark_cumulative_return=excluded.benchmark_cumulative_return,
                    data_confidence_score=excluded.data_confidence_score,
                    series_json=excluded.series_json, calculated_at=excluded.calculated_at
                """
            ),
            (
                _new_id(), fund_id, mode, period, as_of_date,
                metrics.get("cagr"), metrics.get("roicProxy"), metrics.get("alphaVsBenchmark"),
                metrics.get("sharpe"), metrics.get("sortino"), metrics.get("maxDrawdown"),
                metrics.get("positiveQuarterRate"), metrics.get("cumulativeReturn"),
                metrics.get("benchmarkCumulativeReturn"), data_confidence_score,
                json.dumps(series or []), METHODOLOGY_VERSION, _now(),
            ),
        )


def get_fund_returns(fund_id: str, mode: str = DEFAULT_MODE, period: str = "5Y") -> Optional[Dict[str, Any]]:
    with _cursor() as (_c, cur):
        cur.execute(
            _ph(
                """
                SELECT * FROM fund_return_metrics
                WHERE fund_id=? AND mode=? AND period=?
                ORDER BY calculated_at DESC LIMIT 1
                """
            ),
            (fund_id, mode, period),
        )
        row = cur.fetchone()
    if not row:
        return None
    d = _row_to_dict(row)
    return {
        "fundId": fund_id,
        "mode": mode,
        "period": period,
        "benchmark": "SPY",
        "metrics": {
            "cagr": d.get("cagr"),
            "roicProxy": d.get("roic_proxy"),
            "alphaVsBenchmark": d.get("alpha_vs_sp500"),
            "sharpe": d.get("sharpe"),
            "sortino": d.get("sortino"),
            "maxDrawdown": d.get("max_drawdown"),
            "positiveQuarterRate": d.get("positive_quarter_rate"),
            "dataConfidenceScore": d.get("data_confidence_score"),
        },
        "series": _loads(d.get("series_json"), []),
    }


# ── Leaderboard snapshots ────────────────────────────────────────────────────────

def write_leaderboard_snapshot(
    as_of_date: str,
    latest_report_period: Optional[str],
    mode: str,
    ranked_rows: List[Dict[str, Any]],
) -> int:
    """Replace the leaderboard snapshot for a given mode with a fresh ranking.

    ``ranked_rows`` are presentable UI rows (already ranked); we persist each as
    metrics_json keyed by rank so the router can read them back verbatim.
    """
    now = _now()
    with _cursor(commit=True) as (_c, cur):
        cur.execute(_ph("DELETE FROM fund_leaderboard_snapshots WHERE mode=?"), (mode,))
        for row in ranked_rows:
            cur.execute(
                _ph(
                    """
                    INSERT INTO fund_leaderboard_snapshots
                    (snapshot_id, as_of_date, latest_report_period, fund_id, mode, rank,
                     leaderboard_score, methodology_version, metrics_json, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """
                ),
                (
                    _new_id(), as_of_date, latest_report_period, row.get("fundId"),
                    mode, row.get("rank"), row.get("leaderboardScore"),
                    METHODOLOGY_VERSION, json.dumps(row), now,
                ),
            )
    return len(ranked_rows)


def get_leaderboard(
    mode: str = DEFAULT_MODE,
    limit: int = 50,
    offset: int = 0,
    min_confidence: int = 0,
) -> Dict[str, Any]:
    with _cursor() as (_c, cur):
        cur.execute(
            _ph(
                """
                SELECT as_of_date, latest_report_period, metrics_json
                FROM fund_leaderboard_snapshots
                WHERE mode=?
                ORDER BY rank ASC
                """
            ),
            (mode,),
        )
        rows = cur.fetchall()

    parsed = [_row_to_dict(r) for r in rows]
    as_of = parsed[0]["as_of_date"] if parsed else None
    latest_period = parsed[0]["latest_report_period"] if parsed else None

    leaderboard_rows: List[Dict[str, Any]] = []
    for r in parsed:
        row = _loads(r.get("metrics_json"), {})
        if not row:
            continue
        if (row.get("dataConfidenceScore") or 0) < min_confidence:
            continue
        leaderboard_rows.append(row)

    sliced = leaderboard_rows[offset: offset + limit]
    return {
        "asOfDate": as_of,
        "latestReportPeriod": latest_period,
        "methodologyVersion": METHODOLOGY_VERSION,
        "mode": mode,
        "disclaimer": (
            "13F-derived returns are partial public long-book estimates, not actual "
            "fund returns. They exclude shorts, leverage, cash, and non-U.S. holdings."
        ),
        "rows": sliced,
    }


def get_fund_portfolio_latest(fund_id: str) -> Optional[Dict[str, Any]]:
    fund = get_fund(fund_id)
    filing = get_latest_filing(fund_id)
    if not fund or not filing:
        return None
    holdings = get_holdings_for_filing(filing["filing_id"])
    total_mv = filing.get("total_market_value_usd") or sum(
        (h.get("market_value_usd") or 0) for h in holdings
    )
    mapped_mv = sum((h.get("market_value_usd") or 0) for h in holdings if h.get("ticker"))

    sectors: Dict[str, Dict[str, Any]] = {}
    for h in holdings:
        sec = h.get("sector") or "Unknown"
        agg = sectors.setdefault(sec, {"sector": sec, "marketValueUsd": 0.0, "holdingsCount": 0})
        agg["marketValueUsd"] += h.get("market_value_usd") or 0
        agg["holdingsCount"] += 1
    sector_alloc = []
    for s in sectors.values():
        s["weight"] = (s["marketValueUsd"] / total_mv) if total_mv else 0.0
        sector_alloc.append(s)
    sector_alloc.sort(key=lambda x: x["marketValueUsd"], reverse=True)

    return {
        "fundId": fund_id,
        "fundName": fund.get("display_name"),
        "reportPeriod": filing.get("report_period"),
        "filingDate": filing.get("filing_date"),
        "filingUrl": filing.get("filing_url"),
        "totalMarketValueUsd": total_mv,
        "mappedMarketValuePct": (mapped_mv / total_mv) if total_mv else 0.0,
        "sectorAllocation": sector_alloc,
        "holdings": [
            {
                "ticker": h.get("ticker"),
                "companyName": h.get("issuer_name"),
                "cusip": h.get("cusip"),
                "sector": h.get("sector"),
                "shares": h.get("shares"),
                "marketValueUsd": h.get("market_value_usd"),
                "weight": h.get("holding_weight"),
                "mappingStatus": h.get("mapping_status"),
            }
            for h in holdings
        ],
    }


def get_fund_quarterly_report(fund_id: str) -> Optional[Dict[str, Any]]:
    portfolio = get_fund_portfolio_latest(fund_id)
    if not portfolio:
        return None
    holdings = portfolio["holdings"]
    top10 = sorted(holdings, key=lambda h: (h.get("weight") or 0), reverse=True)[:10]
    top10_weight = sum((h.get("weight") or 0) for h in top10)
    top_sector = portfolio["sectorAllocation"][0] if portfolio["sectorAllocation"] else {}
    return {
        "fundId": fund_id,
        "reportPeriod": portfolio.get("reportPeriod"),
        "filingDate": portfolio.get("filingDate"),
        "filingType": "13F-HR",
        "filingUrl": portfolio.get("filingUrl"),
        "totalMarketValueUsd": portfolio.get("totalMarketValueUsd"),
        "numberOfHoldings": len(holdings),
        "topSector": top_sector.get("sector"),
        "topSectorWeight": top_sector.get("weight"),
        "top10HoldingsWeight": top10_weight,
        "summary": (
            f"Latest public 13F portfolio holds {len(holdings)} positions, "
            f"concentrated in {top_sector.get('sector', 'N/A')}."
        ),
        "qualityWarnings": [
            "13F excludes shorts, options exposure detail, cash, and non-U.S. holdings.",
        ],
    }
