"""
Daily Brief — top movers with movement context and heuristic verdicts.

Reads from BigQuery (movement_context_daily + daily_prices) when
MCP_DATA_BACKEND=bigquery; falls back to market_intel live movers.
"""
from __future__ import annotations

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
        "rows": losers + gainers,
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
        "updated_at": updated_at,
    }


def persist_snapshot(payload: Dict[str, Any]) -> int:
    """Upsert daily brief rows into BigQuery daily_brief_snapshot."""
    if _backend_type() != "bigquery":
        return 0
    trade_date = payload.get("trade_date")
    if not trade_date:
        return 0

    from backend.mcp_server.backend import backend
    from backend.mcp_server.bq_schema import FULL_DATASET

    updated_at = datetime.now(timezone.utc).isoformat()
    rows = payload.get("rows") or []
    if not rows:
        return 0

    backend().execute(
        f"DELETE FROM `{FULL_DATASET}.daily_brief_snapshot` "
        f"WHERE trade_date = DATE '{trade_date}'"
    )
    records = [_row_to_bq_record(r, trade_date, updated_at) for r in rows]
    batch = 100
    total = 0
    for i in range(0, len(records), batch):
        total += backend().insert_rows("daily_brief_snapshot", records[i : i + batch])
    logger.info("[DailyBrief] Persisted %d snapshot rows for %s", total, trade_date)
    return total


def load_snapshot(trade_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
    if _backend_type() != "bigquery":
        return None
    from backend.mcp_server.backend import backend
    from backend.mcp_server.bq_schema import FULL_DATASET

    if trade_date is None:
        trade_date = get_latest_trade_date()
    if trade_date is None:
        return None

    td = trade_date.isoformat()
    sql = f"""
        SELECT *
        FROM `{FULL_DATASET}.daily_brief_snapshot`
        WHERE trade_date = DATE '{td}'
        ORDER BY bucket, rank
    """
    raw = backend().query(sql)
    if not raw:
        return None

    rows: List[Dict[str, Any]] = []
    tier = "heuristic"
    updated_at = None
    source = "bigquery_snapshot"
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

    return td, source, rows


def build_daily_brief(
    trade_date: Optional[date] = None,
    n_losers: int = 20,
    n_gainers: int = 10,
    *,
    use_snapshot: bool = True,
    persist: bool = False,
) -> Dict[str, Any]:
    td = trade_date or get_latest_trade_date()
    if use_snapshot and td:
        cached = load_snapshot(td)
        if cached:
            cached["from_snapshot"] = True
            return cached

    td, source, rows = _compute_movers(trade_date, n_losers, n_gainers)
    payload = _payload_from_rows(rows, td or date.today(), source, verdict_tier="heuristic")
    payload["from_snapshot"] = False
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

    _set_deep_job(status="running", progress=5, message="Loading movers", error=None)
    try:
        base = build_daily_brief(
            trade_date=trade_date,
            n_losers=n_losers,
            n_gainers=n_gainers,
            use_snapshot=True,
            persist=False,
        )
        rows = list(base.get("rows") or [])
        _set_deep_job(progress=25, message="Scorecard + valuation enrichment")
        rows = await enrich_rows_quant(rows)
        _set_deep_job(progress=55, message="Batched LLM verdict refinement")
        rows = await apply_deep_verdicts(rows, llm)
        payload = _payload_from_rows(
            rows,
            date.fromisoformat(base["trade_date"]),
            base.get("source", "bigquery"),
            verdict_tier="deep",
        )
        _set_deep_job(progress=85, message="Persisting snapshot")
        if _backend_type() == "bigquery":
            persist_snapshot(payload)
        _set_deep_job(
            status="done",
            progress=100,
            message="Deep refresh complete",
            trade_date=base["trade_date"],
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
