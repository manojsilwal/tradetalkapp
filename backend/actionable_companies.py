"""
Actionable Companies — async S&P 500 batch screener for the home page.

Pipeline (triggered by the "Actionable Companies" button on /):

  1. ``POST /actionable-companies/run`` returns **202 Accepted** immediately and
     hands the scan to an asyncio background task (single in-process worker;
     concurrent triggers are rejected while a scan is running).
  2. The worker chunks the S&P 500 universe (default 15 tickers/chunk), fetches
     one *batched* yfinance price download per chunk plus per-ticker
     fundamentals through a bounded thread pool, and scores every company with
     a deterministic multi-pillar metrics suite (quality / cash-flow health /
     balance-sheet / growth / valuation / momentum). No LLM in the hot path.
  3. Fundamentals are cached per-ticker for 1 hour (``connector_cache``) and
     the finished snapshot is upserted into SQLite. A re-run within
     ``ACTIONABLE_CACHE_TTL_S`` (default 3600s) returns the cached snapshot
     instead of re-hitting Yahoo — fundamentals don't change intra-hour.
  4. Per-ticker narratives are upserted into the RAG knowledge store
     (``sp500_fundamentals_narratives``) so future debates / chat can retrieve
     them, and top actionable verdicts are emitted to the Decision-Outcome
     Ledger (AGENTS.md Harness Phase 2 rule). Both are best-effort: failures
     never break the scan.

Truthful-data contract: companies whose metric coverage is below
``MIN_COVERAGE`` are excluded from scoring (counted in ``skipped``) instead of
being scored against fabricated defaults.

Env knobs:
  ACTIONABLE_DB_PATH            explicit SQLite file (tests use a temp file)
  ACTIONABLE_CACHE_TTL_S        snapshot freshness window (default 3600)
  ACTIONABLE_CHUNK_SIZE         tickers per processing chunk (default 15)
  ACTIONABLE_MAX_CONCURRENCY    parallel fundamentals fetches per chunk (default 10)
  ACTIONABLE_INTER_CHUNK_DELAY_S pause between chunks (default 0.5)
  ACTIONABLE_RAG_ENABLE         "0" disables knowledge-store writes
  ACTIONABLE_LEDGER_TOP_N       max actionable rows emitted to the ledger (default 15)
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))

MIN_COVERAGE = 0.40  # minimum fraction of metric inputs required to score

VERDICTS = ("Strong Buy", "Buy", "Hold", "Sell", "Strong Sell")
ACTIONABLE_VERDICTS = ("Strong Buy", "Buy", "Sell", "Strong Sell")

PILLAR_WEIGHTS: Dict[str, float] = {
    "quality": 0.22,
    "cash_flow": 0.18,
    "growth": 0.14,
    "valuation": 0.16,
    "momentum": 0.20,
    "balance_sheet": 0.10,
}


def _cache_ttl_s() -> int:
    return int(os.environ.get("ACTIONABLE_CACHE_TTL_S", "3600") or "3600")


def _chunk_size() -> int:
    return max(1, int(os.environ.get("ACTIONABLE_CHUNK_SIZE", "15") or "15"))


def _max_concurrency() -> int:
    return max(1, int(os.environ.get("ACTIONABLE_MAX_CONCURRENCY", "10") or "10"))


def _inter_chunk_delay_s() -> float:
    return float(os.environ.get("ACTIONABLE_INTER_CHUNK_DELAY_S", "0.5") or "0.5")


def _rag_enabled() -> bool:
    return os.environ.get("ACTIONABLE_RAG_ENABLE", "1").strip() != "0"


def _ledger_top_n() -> int:
    return max(0, int(os.environ.get("ACTIONABLE_LEDGER_TOP_N", "15") or "15"))


def _db_path() -> str:
    explicit = os.environ.get("ACTIONABLE_DB_PATH", "").strip()
    if explicit:
        parent = os.path.dirname(explicit)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return explicit
    data_dir = os.environ.get("TRADETALK_DATA_DIR", "").strip()
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "actionable.db")
    return os.path.join(_BACKEND_DIR, "actionable.db")


# ── SQLite persistence (snapshot upsert + read) ──────────────────────────────

_db_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS actionable_snapshots (
               snapshot_id   TEXT PRIMARY KEY,
               created_at    REAL NOT NULL,
               universe_size INTEGER NOT NULL,
               scored        INTEGER NOT NULL,
               skipped       INTEGER NOT NULL,
               meta_json     TEXT NOT NULL DEFAULT '{}'
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS actionable_rows (
               snapshot_id  TEXT NOT NULL,
               ticker       TEXT NOT NULL,
               score        REAL NOT NULL,
               verdict      TEXT NOT NULL,
               actionable   INTEGER NOT NULL,
               payload_json TEXT NOT NULL,
               PRIMARY KEY (snapshot_id, ticker)
           )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_actionable_rows_score "
        "ON actionable_rows (snapshot_id, actionable, score DESC)"
    )
    return conn


def persist_snapshot(
    snapshot_id: str,
    rows: List[Dict[str, Any]],
    *,
    universe_size: int,
    skipped: int,
    created_at: Optional[float] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> int:
    """Upsert a full scan result. Replaces any prior rows for the snapshot id."""
    ts = created_at if created_at is not None else time.time()
    with _db_lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM actionable_rows WHERE snapshot_id = ?", (snapshot_id,))
            conn.execute(
                "INSERT OR REPLACE INTO actionable_snapshots "
                "(snapshot_id, created_at, universe_size, scored, skipped, meta_json) "
                "VALUES (?,?,?,?,?,?)",
                (snapshot_id, ts, universe_size, len(rows), skipped, json.dumps(meta or {})),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO actionable_rows "
                "(snapshot_id, ticker, score, verdict, actionable, payload_json) "
                "VALUES (?,?,?,?,?,?)",
                [
                    (
                        snapshot_id,
                        r["ticker"],
                        float(r["score"]),
                        r["verdict"],
                        1 if r.get("actionable") else 0,
                        json.dumps(r, default=str),
                    )
                    for r in rows
                ],
            )
            conn.commit()
            return len(rows)
        finally:
            conn.close()


def latest_snapshot_meta() -> Optional[Dict[str, Any]]:
    with _db_lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM actionable_snapshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    return {
        "snapshot_id": row["snapshot_id"],
        "created_at": float(row["created_at"]),
        "universe_size": int(row["universe_size"]),
        "scored": int(row["scored"]),
        "skipped": int(row["skipped"]),
        "meta": json.loads(row["meta_json"] or "{}"),
    }


def load_snapshot_rows(
    snapshot_id: str,
    *,
    limit: int = 25,
    actionable_only: bool = True,
) -> List[Dict[str, Any]]:
    """Top rows by composite score (desc) — what the frontend renders."""
    sql = "SELECT payload_json FROM actionable_rows WHERE snapshot_id = ?"
    args: List[Any] = [snapshot_id]
    if actionable_only:
        sql += " AND actionable = 1"
    sql += " ORDER BY score DESC LIMIT ?"
    args.append(int(limit))
    with _db_lock:
        conn = _connect()
        try:
            raw = conn.execute(sql, args).fetchall()
        finally:
            conn.close()
    return [json.loads(r["payload_json"]) for r in raw]


def fresh_snapshot_meta(ttl_s: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Latest snapshot if it is younger than the cache TTL, else ``None``."""
    meta = latest_snapshot_meta()
    if not meta:
        return None
    ttl = ttl_s if ttl_s is not None else _cache_ttl_s()
    if time.time() - meta["created_at"] > ttl:
        return None
    return meta


# ── Job state (poll target for the frontend) ─────────────────────────────────

_job_lock = threading.Lock()
_job: Dict[str, Any] = {
    "job_id": None,
    "status": "idle",  # idle | running | done | error
    "progress": 0,
    "message": "",
    "processed": 0,
    "total": 0,
    "snapshot_id": None,
    "cache_hit": False,
    "error": None,
    "updated_at": None,
}
_worker_task: Optional[asyncio.Task] = None  # keep a ref so the task isn't GC'd


def get_job_status() -> Dict[str, Any]:
    with _job_lock:
        return dict(_job)


def _set_job(**kwargs: Any) -> None:
    with _job_lock:
        _job.update(kwargs)
        _job["updated_at"] = datetime.now(timezone.utc).isoformat()


# ── Metric math (pure functions — unit tested offline) ──────────────────────


def compute_rsi_14(closes: Sequence[float]) -> Optional[float]:
    """14-period RSI (simple averages) on a daily close series."""
    clean = [float(c) for c in closes if c is not None and not math.isnan(float(c))]
    if len(clean) < 15:
        return None
    gains: List[float] = []
    losses: List[float] = []
    for prev, curr in zip(clean[-15:-1], clean[-14:]):
        delta = curr - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / 14.0
    avg_loss = sum(losses) / 14.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def momentum_from_closes(closes: Sequence[float]) -> Dict[str, Optional[float]]:
    """Momentum features from ~1y of daily closes (newest last)."""
    clean = [float(c) for c in closes if c is not None and not math.isnan(float(c))]
    out: Dict[str, Optional[float]] = {
        "last_close": None,
        "ret_1m_pct": None,
        "ret_3m_pct": None,
        "ret_6m_pct": None,
        "pct_of_52wk_high": None,
        "rsi_14": None,
    }
    if not clean:
        return out
    last = clean[-1]
    out["last_close"] = round(last, 4)

    def _ret(days: int) -> Optional[float]:
        if len(clean) <= days:
            return None
        base = clean[-(days + 1)]
        if base == 0:
            return None
        return round((last / base - 1.0) * 100.0, 2)

    out["ret_1m_pct"] = _ret(21)
    out["ret_3m_pct"] = _ret(63)
    out["ret_6m_pct"] = _ret(126)
    high = max(clean[-252:]) if clean else None
    if high:
        out["pct_of_52wk_high"] = round((last / high) * 100.0, 2)
    out["rsi_14"] = compute_rsi_14(clean)
    return out


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _linscore(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    """Map value linearly to 0–100 between lo (=0) and hi (=100)."""
    if value is None:
        return None
    if hi == lo:
        return None
    return _clamp((float(value) - lo) / (hi - lo) * 100.0)


def _avg(parts: List[Optional[float]]) -> Optional[float]:
    present = [p for p in parts if p is not None]
    if not present:
        return None
    return sum(present) / len(present)


def score_company(fund: Dict[str, Any], momo: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic multi-pillar score (0–100) + verdict for one company.

    Pillars address the gap analysis of the legacy metrics suite:
      quality        — moat proxies: ROE, ROA, gross & operating margin
      cash_flow      — FCF yield, operating-cash-flow margin (cash flow health)
      balance_sheet  — D/E, current ratio, debt service capacity
      growth         — revenue + earnings growth (capital reinvestment runway)
      valuation      — forward-PE compression vs trailing, analyst PT upside
      momentum       — RSI sweet-spot, 3M/6M returns, proximity to 52wk high

    Only metrics that are actually present are scored; ``coverage`` reports
    the fraction of inputs available (truthful-data contract).
    """
    inputs_present = 0
    inputs_total = 0

    def _track(value: Optional[float]) -> Optional[float]:
        nonlocal inputs_present, inputs_total
        inputs_total += 1
        if value is not None:
            inputs_present += 1
        return value

    roe = _track(fund.get("return_on_equity_pct"))
    roa = _track(fund.get("return_on_assets_pct"))
    gross = _track(fund.get("gross_margin_pct"))
    op_margin = _track(fund.get("operating_margin_pct"))
    quality = _avg([
        _linscore(roe, 0, 30),
        _linscore(roa, 0, 15),
        _linscore(gross, 10, 60),
        _linscore(op_margin, 0, 30),
    ])

    fcf_yield = _track(fund.get("fcf_yield_pct"))
    ocf_margin = _track(fund.get("ocf_margin_pct"))
    cash_flow = _avg([
        _linscore(fcf_yield, 0, 8),
        _linscore(ocf_margin, 0, 30),
    ])

    de = _track(fund.get("debt_to_equity"))
    current_ratio = _track(fund.get("current_ratio"))
    debt_service = _track(fund.get("ebitda_to_debt"))
    # Lower leverage is better: D/E 0 → 100, 2.5+ → 0
    de_score = None if de is None else _clamp(100.0 - (_linscore(de, 0, 2.5) or 0.0))
    balance_sheet = _avg([
        de_score,
        _linscore(current_ratio, 0.5, 2.5),
        _linscore(debt_service, 0, 1.0),
    ])

    rev_g = _track(fund.get("revenue_growth_pct"))
    eps_g = _track(fund.get("earnings_growth_pct"))
    growth = _avg([
        _linscore(rev_g, -5, 25),
        _linscore(eps_g, -10, 30),
    ])

    pe_stretch = _track(fund.get("pe_stretch_pct"))  # forward vs trailing premium
    pt_upside = _track(fund.get("pt_upside_pct"))
    # Negative stretch (forward earnings growing into the multiple) scores high
    stretch_score = (
        None if pe_stretch is None else _clamp(100.0 - (_linscore(pe_stretch, -30, 30) or 0.0))
    )
    valuation = _avg([
        stretch_score,
        _linscore(pt_upside, -10, 30),
    ])

    rsi = _track(momo.get("rsi_14"))
    ret_3m = _track(momo.get("ret_3m_pct"))
    ret_6m = _track(momo.get("ret_6m_pct"))
    pct_high = _track(momo.get("pct_of_52wk_high"))
    rsi_score: Optional[float] = None
    if rsi is not None:
        # Sweet spot 50–65 (confirmed uptrend, not overbought); fade extremes
        rsi_score = _clamp(100.0 - abs(rsi - 57.5) * (100.0 / 42.5))
    momentum = _avg([
        rsi_score,
        _linscore(ret_3m, -20, 25),
        _linscore(ret_6m, -30, 40),
        _linscore(pct_high, 60, 100),
    ])

    pillars: Dict[str, Optional[float]] = {
        "quality": quality,
        "cash_flow": cash_flow,
        "balance_sheet": balance_sheet,
        "growth": growth,
        "valuation": valuation,
        "momentum": momentum,
    }

    weighted = 0.0
    weight_used = 0.0
    for name, value in pillars.items():
        if value is None:
            continue
        w = PILLAR_WEIGHTS[name]
        weighted += w * value
        weight_used += w

    coverage = round(inputs_present / inputs_total, 3) if inputs_total else 0.0
    if weight_used == 0 or coverage < MIN_COVERAGE:
        return {
            "score": None,
            "verdict": None,
            "actionable": False,
            "coverage": coverage,
            "pillars": pillars,
            "insufficient_data": True,
        }

    score = round(weighted / weight_used, 2)
    verdict = verdict_from_score(score)
    return {
        "score": score,
        "verdict": verdict,
        "actionable": verdict in ACTIONABLE_VERDICTS,
        "coverage": coverage,
        "pillars": {k: (round(v, 2) if v is not None else None) for k, v in pillars.items()},
        "insufficient_data": False,
    }


def verdict_from_score(score: float) -> str:
    if score >= 72:
        return "Strong Buy"
    if score >= 60:
        return "Buy"
    if score >= 45:
        return "Hold"
    if score >= 35:
        return "Sell"
    return "Strong Sell"


# ── Live data fetch (yfinance; per-ticker 1h cache) ──────────────────────────

_FUND_CACHE_CONNECTOR = "actionable_fundamentals"


def _pct(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return round(float(value) * 100.0, 2)
    except (TypeError, ValueError):
        return None


def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def fetch_fundamentals(ticker: str) -> Dict[str, Any]:
    """
    Blocking single-ticker fundamentals snapshot from ``yf.Ticker.info``,
    normalized into the metric-suite input shape. Cached for 1 hour so
    intra-hour re-runs never re-hit Yahoo.
    """
    from . import connector_cache

    cached = connector_cache.get_cached(_FUND_CACHE_CONNECTOR, ticker, ttl=_cache_ttl_s())
    if cached is not None:
        return cached

    import yfinance as yf

    info = yf.Ticker(ticker).info or {}

    market_cap = _num(info.get("marketCap"))
    fcf = _num(info.get("freeCashflow"))
    ocf = _num(info.get("operatingCashflow"))
    revenue = _num(info.get("totalRevenue"))
    total_debt = _num(info.get("totalDebt"))
    ebitda = _num(info.get("ebitda"))
    fwd_pe = _num(info.get("forwardPE"))
    trail_pe = _num(info.get("trailingPE"))
    target = _num(info.get("targetMeanPrice"))
    price = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))
    de_raw = _num(info.get("debtToEquity"))

    out: Dict[str, Any] = {
        "ticker": ticker.upper(),
        "company_name": info.get("shortName") or info.get("longName") or ticker.upper(),
        "sector": info.get("sector") or "",
        "industry": info.get("industry") or "",
        "market_cap": market_cap,
        "current_price": price,
        "return_on_equity_pct": _pct(info.get("returnOnEquity")),
        "return_on_assets_pct": _pct(info.get("returnOnAssets")),
        "gross_margin_pct": _pct(info.get("grossMargins")),
        "operating_margin_pct": _pct(info.get("operatingMargins")),
        "fcf_yield_pct": round(fcf / market_cap * 100.0, 2) if (fcf and market_cap) else None,
        "ocf_margin_pct": round(ocf / revenue * 100.0, 2) if (ocf and revenue) else None,
        # yfinance reports debtToEquity on a % scale (e.g. 150 = 1.5x)
        "debt_to_equity": round(de_raw / 100.0, 3) if de_raw is not None else None,
        "current_ratio": _num(info.get("currentRatio")),
        "ebitda_to_debt": round(ebitda / total_debt, 3) if (ebitda and total_debt) else None,
        "revenue_growth_pct": _pct(info.get("revenueGrowth")),
        "earnings_growth_pct": _pct(info.get("earningsGrowth")),
        "pe_stretch_pct": round((fwd_pe / trail_pe - 1.0) * 100.0, 2)
        if (fwd_pe and trail_pe and trail_pe > 0)
        else None,
        "pt_upside_pct": round((target / price - 1.0) * 100.0, 2)
        if (target and price and price > 0)
        else None,
        "trailing_pe": trail_pe,
        "forward_pe": fwd_pe,
        "trailing_eps": _num(info.get("trailingEps")),
        "dividend_yield_pct": _num(info.get("dividendYield")),
    }
    connector_cache.set_cached(_FUND_CACHE_CONNECTOR, out, ticker)
    return out


def fetch_chunk_history(tickers: Sequence[str]) -> Dict[str, List[float]]:
    """
    One batched yfinance download for a chunk → close-series per ticker.
    Batched download (not per-ticker fan-out) is the rate-limit-safe pattern.
    """
    from .connectors import yfinance_batch

    raw = yfinance_batch.download_history(
        list(tickers),
        chunk_size=len(tickers) or 1,
        period="1y",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    series = yfinance_batch.close_series_by_ticker(raw, tickers)
    return {sym: [float(v) for v in s.tolist()] for sym, s in series.items()}


def get_universe() -> List[str]:
    try:
        from .market_intel import _get_sp500_universe

        return _get_sp500_universe()
    except Exception:
        from .data_lake.config import SP500_TICKERS

        return list(SP500_TICKERS)


# ── RAG + Decision Ledger side effects (best-effort) ─────────────────────────


def _build_narrative(row: Dict[str, Any]) -> str:
    f = row.get("fundamentals", {})
    m = row.get("momentum", {})
    parts = [
        f"{f.get('company_name', row['ticker'])} ({row['ticker']}) actionable-screen snapshot "
        f"on {datetime.now(timezone.utc).date()}.",
        f"Sector: {f.get('sector') or 'n/a'}.",
        f"Composite score {row.get('score')} → verdict {row.get('verdict')}.",
    ]
    if f.get("return_on_equity_pct") is not None:
        parts.append(f"ROE {f['return_on_equity_pct']}%.")
    if f.get("gross_margin_pct") is not None:
        parts.append(f"Gross margin {f['gross_margin_pct']}%.")
    if f.get("fcf_yield_pct") is not None:
        parts.append(f"FCF yield {f['fcf_yield_pct']}%.")
    if f.get("revenue_growth_pct") is not None:
        parts.append(f"Revenue growth {f['revenue_growth_pct']}%.")
    if m.get("ret_3m_pct") is not None:
        parts.append(f"3M return {m['ret_3m_pct']}%.")
    if m.get("rsi_14") is not None:
        parts.append(f"RSI(14) {m['rsi_14']}.")
    return " ".join(parts)


def _store_rows_to_rag(rows: List[Dict[str, Any]]) -> int:
    """Upsert per-ticker narratives into the knowledge store (1 doc/ticker/day)."""
    if not _rag_enabled() or not rows:
        return 0
    try:
        from .knowledge_store import get_knowledge_store

        store = get_knowledge_store()
    except Exception as e:
        logger.warning("[Actionable] knowledge store unavailable: %s", e)
        return 0
    written = 0
    for row in rows:
        try:
            f = row.get("fundamentals", {})
            market_cap = f.get("market_cap") or 0.0
            store.upsert_sp500_fundamental(
                ticker=row["ticker"],
                sector=f.get("sector") or "",
                narrative=_build_narrative(row),
                pe_ratio=f.get("trailing_pe") or 0.0,
                eps=f.get("trailing_eps") or 0.0,
                market_cap_b=round(market_cap / 1e9, 2) if market_cap else 0.0,
            )
            written += 1
        except Exception as e:
            logger.debug("[Actionable] RAG upsert failed for %s: %s", row.get("ticker"), e)
    return written


def _emit_ledger_decisions(rows: List[Dict[str, Any]], snapshot_id: str) -> int:
    """Emit top actionable verdicts to the Decision-Outcome Ledger (never raises)."""
    top_n = _ledger_top_n()
    if top_n == 0:
        return 0
    emitted = 0
    try:
        from . import decision_ledger as dl
        from .decision_ledger_registry import registry_attribution

        prompt_versions, snap_id, model = registry_attribution()
        actionable = sorted(
            (r for r in rows if r.get("actionable")),
            key=lambda r: r.get("score") or 0,
            reverse=True,
        )[:top_n]
        for row in actionable:
            evidence = []
            if _rag_enabled():
                try:
                    from .knowledge_store import get_knowledge_store

                    _, refs = get_knowledge_store().query_with_refs(
                        "sp500_fundamentals_narratives",
                        f"{row['ticker']} fundamentals actionable screen",
                        n_results=2,
                        where={"ticker": row["ticker"]},
                    )
                    evidence = [
                        dl.EvidenceRef(
                            chunk_id=ref.get("chunk_id", ""),
                            collection=ref.get("collection", ""),
                            relevance=(
                                round(1.0 - float(ref["distance"]), 4)
                                if ref.get("distance") is not None
                                else None
                            ),
                            rank=int(ref.get("rank", 0)),
                        )
                        for ref in refs
                        if ref.get("chunk_id")
                    ]
                except Exception:
                    evidence = []
            features = [
                dl.FeatureValue(name=f"pillar_{name}", value_num=value)
                for name, value in (row.get("pillars") or {}).items()
                if value is not None
            ]
            features.append(dl.FeatureValue(name="coverage", value_num=row.get("coverage")))
            dl.emit_decision(
                decision_type="actionable_screen",
                symbol=row["ticker"],
                horizon_hint="21d",
                verdict=row.get("verdict") or "",
                confidence=(row.get("score") or 0) / 100.0,
                output={
                    "snapshot_id": snapshot_id,
                    "score": row.get("score"),
                    "pillars": row.get("pillars"),
                    "coverage": row.get("coverage"),
                },
                source_route="backend/actionable_companies.py::run_actionable_scan",
                evidence=evidence,
                features=features,
                prompt_versions=prompt_versions,
                registry_snapshot_id=snap_id,
                model=model,
            )
            emitted += 1
    except Exception as e:
        logger.warning("[Actionable] ledger emit failed (non-fatal): %s", e)
    return emitted


# ── Async worker ─────────────────────────────────────────────────────────────


async def _process_chunk(chunk: List[str]) -> List[Dict[str, Any]]:
    """Score one chunk: 1 batched price download + bounded fundamentals fan-out."""
    closes_by_ticker: Dict[str, List[float]] = {}
    try:
        closes_by_ticker = await asyncio.to_thread(fetch_chunk_history, chunk)
    except Exception as e:
        logger.warning("[Actionable] chunk history failed (%s…): %s", chunk[0] if chunk else "", e)

    sem = asyncio.Semaphore(_max_concurrency())

    async def _one(ticker: str) -> Optional[Dict[str, Any]]:
        async with sem:
            try:
                fund = await asyncio.to_thread(fetch_fundamentals, ticker)
            except Exception as e:
                logger.debug("[Actionable] fundamentals failed for %s: %s", ticker, e)
                return None
        momo = momentum_from_closes(closes_by_ticker.get(ticker, []))
        scored = score_company(fund, momo)
        if scored.get("insufficient_data"):
            return None
        return {
            "ticker": ticker,
            "company_name": fund.get("company_name"),
            "sector": fund.get("sector"),
            "score": scored["score"],
            "verdict": scored["verdict"],
            "actionable": scored["actionable"],
            "coverage": scored["coverage"],
            "pillars": scored["pillars"],
            "fundamentals": fund,
            "momentum": momo,
        }

    results = await asyncio.gather(*(_one(t) for t in chunk))
    return [r for r in results if r is not None]


async def run_actionable_scan(job_id: str, *, force: bool = False) -> Dict[str, Any]:
    """
    Full S&P 500 scan. Returns the snapshot metadata. Sets job state along the
    way so ``GET /actionable-companies/status`` can drive the progress UI.
    """
    if not force:
        cached = fresh_snapshot_meta()
        if cached:
            _set_job(
                job_id=job_id,
                status="done",
                progress=100,
                message="Served from cached snapshot (fresh within the last hour).",
                snapshot_id=cached["snapshot_id"],
                cache_hit=True,
                processed=cached["scored"],
                total=cached["universe_size"],
                error=None,
            )
            return cached

    started = time.time()
    try:
        universe = get_universe()
        chunks = [universe[i : i + _chunk_size()] for i in range(0, len(universe), _chunk_size())]
        total = len(universe)
        _set_job(
            job_id=job_id,
            status="running",
            progress=1,
            message=f"Scanning {total} S&P 500 companies in {len(chunks)} chunks…",
            processed=0,
            total=total,
            snapshot_id=None,
            cache_hit=False,
            error=None,
        )

        rows: List[Dict[str, Any]] = []
        processed = 0
        delay = _inter_chunk_delay_s()
        for idx, chunk in enumerate(chunks):
            chunk_rows = await _process_chunk(chunk)
            rows.extend(chunk_rows)
            processed += len(chunk)
            _set_job(
                progress=max(1, min(94, int(processed / total * 92))),
                message=f"Scored {processed}/{total} companies ({len(rows)} with sufficient data)…",
                processed=processed,
            )
            # Stream fresh fundamentals into the RAG store as we go
            rag_written = await asyncio.to_thread(_store_rows_to_rag, chunk_rows)
            if rag_written:
                logger.debug("[Actionable] RAG upserts for chunk %d: %d", idx, rag_written)
            if delay > 0 and idx < len(chunks) - 1:
                await asyncio.sleep(delay)

        skipped = total - len(rows)
        snapshot_id = (
            f"scan_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
        )
        _set_job(progress=95, message="Persisting snapshot…")
        persist_snapshot(
            snapshot_id,
            rows,
            universe_size=total,
            skipped=skipped,
            meta={
                "duration_s": round(time.time() - started, 1),
                "chunk_size": _chunk_size(),
                "force": force,
            },
        )

        _set_job(progress=97, message="Emitting decisions to ledger…")
        emitted = await asyncio.to_thread(_emit_ledger_decisions, rows, snapshot_id)

        _set_job(
            status="done",
            progress=100,
            message=(
                f"Scan complete: {len(rows)} scored, {skipped} skipped "
                f"(insufficient data), {emitted} ledger decisions."
            ),
            snapshot_id=snapshot_id,
            error=None,
        )
        return latest_snapshot_meta() or {}
    except Exception as e:
        logger.exception("[Actionable] scan failed")
        _set_job(status="error", progress=100, message="Scan failed", error=str(e))
        raise


def start_scan_task(*, force: bool = False) -> Dict[str, Any]:
    """
    Schedule the scan on the running event loop and return immediately.
    Caller (router) must already have verified no scan is running.
    """
    global _worker_task
    job_id = uuid.uuid4().hex
    _set_job(
        job_id=job_id,
        status="running",
        progress=0,
        message="Queued S&P 500 actionable scan…",
        processed=0,
        total=0,
        snapshot_id=None,
        cache_hit=False,
        error=None,
    )

    async def _runner() -> None:
        try:
            await run_actionable_scan(job_id, force=force)
        except Exception:
            pass  # state already set to error inside run_actionable_scan

    _worker_task = asyncio.get_running_loop().create_task(_runner())
    return get_job_status()
