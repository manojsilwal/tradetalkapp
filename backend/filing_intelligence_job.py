"""Batch filing intelligence pipeline — SEC Atom → FinCrawler → LLM → Postgres."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import List, Set

from .connectors.filing_intelligence import (
    enabled,
    extract_from_filing_async,
    is_stale,
    ttl_days,
    upsert_filing_intelligence,
)
from .fincrawler_client import fc
from .paper_portfolio import get_all_unique_portfolio_tickers, get_filing_intelligence_record
from .sec_filing_job import fetch_recent_filing_tickers

logger = logging.getLogger(__name__)


async def _resolve_universe(recent_filings: Set[str]) -> List[str]:
    mode = os.environ.get("FILING_INTELLIGENCE_UNIVERSE", "recent_filings_only").strip().lower()
    tickers: Set[str] = set(recent_filings)
    if mode in ("portfolio", "sp500"):
        try:
            tickers |= {t.upper() for t in get_all_unique_portfolio_tickers()}
        except Exception:
            pass
    if mode == "sp500":
        try:
            from .mcp_server.backend import backend

            rows = backend().query(
                "SELECT DISTINCT symbol FROM daily_prices ORDER BY symbol LIMIT 600"
            )
            tickers |= {str(r["symbol"]).upper() for r in rows if r.get("symbol")}
        except Exception as exc:  # noqa: BLE001
            logger.debug("[filing_intelligence_job] sp500 universe failed: %s", exc)
    return sorted(tickers)


async def run_filing_intelligence_job() -> dict:
    if not enabled():
        return {"ok": True, "skipped": True, "reason": "FILING_INTELLIGENCE_ENABLE=0"}

    logger.info("[filing_intelligence_job] Starting batch extraction...")
    recent_10q = await fetch_recent_filing_tickers(days=1, form="10-Q")
    recent_10k = await fetch_recent_filing_tickers(days=1, form="10-K")
    recent = recent_10q | recent_10k
    tickers = await _resolve_universe(recent)
    if not tickers:
        logger.info("[filing_intelligence_job] No tickers in universe.")
        return {"ok": True, "processed": 0, "skipped": 0, "failed": 0}

    concurrency = max(1, int(os.environ.get("FILING_INTELLIGENCE_CONCURRENCY", "4")))
    sem = asyncio.Semaphore(concurrency)
    processed = skipped = failed = 0

    async def _one(ticker: str) -> None:
        nonlocal processed, skipped, failed
        async with sem:
            try:
                cached = get_filing_intelligence_record(ticker)
                if cached and not is_stale(cached) and ticker not in recent:
                    skipped += 1
                    return
                if not fc.enabled:
                    failed += 1
                    return
                text = await fc.get_sec_filing(ticker, form="10-K", max_chars=12000)
                form = "10-K"
                if not text or text.startswith("SEC filing unavailable"):
                    text = await fc.get_sec_filing(ticker, form="10-Q", max_chars=12000)
                    form = "10-Q"
                if not text or text.startswith("SEC filing unavailable"):
                    skipped += 1
                    return
                record = await extract_from_filing_async(ticker, text, filing_form=form)
                if not record:
                    skipped += 1
                    return
                record["ttl_days"] = 1 if ticker in recent else ttl_days()
                upsert_filing_intelligence(record)
                try:
                    from .connectors.filing_intelligence import index_filing_narrative

                    index_filing_narrative(record)
                except Exception:  # noqa: BLE001
                    pass
                processed += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("[filing_intelligence_job] %s failed: %s", ticker, exc)
                failed += 1

    await asyncio.gather(*[_one(t) for t in tickers])
    logger.info(
        "[filing_intelligence_job] Done processed=%d skipped=%d failed=%d",
        processed, skipped, failed,
    )
    return {"ok": True, "processed": processed, "skipped": skipped, "failed": failed}
