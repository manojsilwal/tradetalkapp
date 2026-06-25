"""Sequential decision-terminal prewarm for cron / GitHub Actions."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from .ingress_models import validate_ticker_query

logger = logging.getLogger(__name__)

# Top-liquidity S&P names — keeps cron runtime bounded while covering most user traffic.
PREWARM_DEFAULT_TICKERS: List[str] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "AMZN",
    "META",
    "TSLA",
    "BRK.B",
    "JPM",
    "V",
    "UNH",
    "XOM",
    "JNJ",
    "WMT",
    "MA",
    "PG",
    "HD",
    "CVX",
    "MRK",
    "ABBV",
]


async def run_verdict_prewarm(
    *,
    tickers: Optional[List[str]],
    execute_analyze,
    tool_registry,
    poly_connector,
    llm_client,
) -> Dict[str, Any]:
    from .decision_terminal import run_decision_terminal_request

    raw = tickers if tickers else PREWARM_DEFAULT_TICKERS
    syms = [validate_ticker_query(t) for t in raw if (t or "").strip()]
    results: List[Dict[str, Any]] = []
    cache_hits = 0
    cold_runs = 0
    brain_prewarm_ok = 0

    # Brain snapshot prewarm — warms serve_ticker caches when brain serving is on.
    try:
        from .brain.serving import serving_enabled, serve_ticker

        if serving_enabled():
            for sym in syms:
                try:
                    br = await asyncio.to_thread(serve_ticker, sym, emit=False)
                    if br.get("status") not in ("no_snapshot", "error", None):
                        brain_prewarm_ok += 1
                except Exception as exc:
                    logger.debug("[verdict_prewarm] brain prewarm %s skipped: %s", sym, exc)
    except Exception as exc:
        logger.debug("[verdict_prewarm] brain prewarm block skipped: %s", exc)

    for sym in syms:
        t0 = time.perf_counter()
        row: Dict[str, Any] = {"ticker": sym}
        try:
            payload = await run_decision_terminal_request(
                sym,
                None,
                None,
                execute_analyze=execute_analyze,
                tool_registry=tool_registry,
                poly_connector=poly_connector,
                llm_client=llm_client,
                force=False,
            )
            dur = round(time.perf_counter() - t0, 2)
            from_cache = bool(getattr(payload, "verdict_from_cache", False))
            if from_cache:
                cache_hits += 1
            else:
                cold_runs += 1
            row.update(
                {
                    "ok": True,
                    "duration_s": dur,
                    "verdict_from_cache": from_cache,
                }
            )
            logger.info(
                "[verdict_prewarm] %s ok duration_s=%.2f from_cache=%s",
                sym,
                dur,
                from_cache,
            )
        except Exception as exc:
            row.update({"ok": False, "duration_s": round(time.perf_counter() - t0, 2), "error": str(exc)})
            logger.warning("[verdict_prewarm] %s failed: %s", sym, exc)
        results.append(row)

    return {
        "tickers_requested": len(syms),
        "brain_prewarm_ok": brain_prewarm_ok,
        "cache_hits": cache_hits,
        "cold_runs": cold_runs,
        "results": results,
    }
