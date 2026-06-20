import asyncio
import logging
import time
import urllib.request
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Set

from .paper_portfolio import get_all_unique_portfolio_tickers, upsert_stock_sec_info, get_stock_sec_info
from .connectors.scorecard_data import fetch_scorecard_data
from .connectors.backtest_data import _load_cik_map, _CIK_MAP
from .deps import llm_client
from .fincrawler_client import fc

logger = logging.getLogger(__name__)


async def fetch_recent_filing_tickers(days: int = 1) -> Set[str]:
    """
    Query the SEC Atom feed of the 100 most recent 10-Q filings
    and return the uppercase ticker symbols of companies that filed
    within the last `days` days.
    """
    logger.info("[sec_filing_job] Querying SEC 10-Q Atom feed for recent filings...")
    try:
        _load_cik_map()
        cik_to_ticker = {cik: ticker for ticker, cik in _CIK_MAP.items()}
    except Exception as e:
        logger.error("[sec_filing_job] Failed to load CIK-to-ticker map: %s", e)
        return set()

    url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=10-Q&count=100&output=atom"
    headers = {"User-Agent": "TradeTalk Backtest contact@tradetalk.app"}
    req = urllib.request.Request(url, headers=headers)

    try:
        loop = asyncio.get_event_loop()
        def _fetch():
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.read()
        content = await loop.run_in_executor(None, _fetch)
    except Exception as e:
        logger.error("[sec_filing_job] Failed to fetch SEC Atom feed: %s", e)
        return set()

    try:
        root = ET.fromstring(content)
    except Exception as e:
        logger.error("[sec_filing_job] Failed to parse Atom XML: %s", e)
        return set()

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    recent_tickers = set()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    for entry in root.findall("atom:entry", ns):
        title_elem = entry.find("atom:title", ns)
        updated_elem = entry.find("atom:updated", ns)
        if title_elem is not None and updated_elem is not None:
            title = title_elem.text or ""
            updated_str = updated_elem.text or ""
            m = re.search(r"\((\d+)\)", title)
            if m:
                cik = m.group(1).zfill(10)
                ticker = cik_to_ticker.get(cik)
                if ticker:
                    try:
                        dt = datetime.fromisoformat(updated_str)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        else:
                            dt = dt.astimezone(timezone.utc)
                        if dt >= cutoff:
                            recent_tickers.add(ticker.upper())
                    except Exception as err:
                        logger.warning("[sec_filing_job] Error parsing timestamp %s: %s", updated_str, err)
                        recent_tickers.add(ticker.upper())

    logger.info("[sec_filing_job] Found %d tickers with recent 10-Q filings: %s", len(recent_tickers), recent_tickers)
    return recent_tickers


async def run_sec_filing_job() -> dict:
    """
    Daily ingestion job for SEC/SITG/insider transaction metrics.
    Fetches held tickers, gathers financials & DEF 14A proxy texts,
    asks the LLM to score/estimate CEO base salary & stock holdings,
    and updates the database cache table (stocks).
    """
    logger.info("[sec_filing_job] Starting SEC filing & insider transaction ingestion...")
    
    try:
        tickers = get_all_unique_portfolio_tickers()
    except Exception as e:
        logger.error("[sec_filing_job] Failed to fetch portfolio tickers: %s", e)
        return {"ok": False, "error": f"Failed to fetch portfolio tickers: {e}", "processed": 0}

    if not tickers:
        logger.info("[sec_filing_job] No active portfolio tickers found. Skipping.")
        return {"ok": True, "processed": 0, "message": "No portfolio tickers found"}

    # Query SEC 10-Q Atom feed for recent filings (last 1 day)
    recent_filing_tickers = await fetch_recent_filing_tickers(days=1)

    processed = 0
    failed = 0
    skipped = 0

    # Limit concurrency to avoid slamming yfinance / LLM / FinCrawler
    sem = asyncio.Semaphore(2)

    async def _process_one(ticker: str):
        nonlocal processed, failed, skipped
        async with sem:
            try:
                # Check cache first
                cached = get_stock_sec_info(ticker)
                # Skip if already cached and has no recent filings in feed
                if cached and ticker.upper() not in recent_filing_tickers:
                    logger.info("[sec_filing_job] Skipping ticker %s (already cached and no recent 10-Q filing)", ticker)
                    skipped += 1
                    return

                logger.info("[sec_filing_job] Processing ticker: %s", ticker)
                # Fetch yfinance/scorecard fundamentals
                data = await fetch_scorecard_data(ticker)

                # Fetch DEF 14A proxy filing text
                proxy_context = ""
                if fc.enabled:
                    try:
                        proxy_context = await fc.get_sec_filing(data.ticker, form="DEF 14A", max_chars=8000)
                    except Exception as e:
                        logger.warning("[sec_filing_job] failed to fetch DEF 14A for %s: %s", data.ticker, e)

                ctx = {
                    "ticker": data.ticker,
                    "company_name": data.company_name,
                    "sector": data.sector,
                    "industry": data.industry,
                    "ceo_name": data.ceo_name,
                    "insider_buy_count_12m": data.insider_buy_count_12m,
                    "insider_sell_count_12m": data.insider_sell_count_12m,
                    "insider_net_shares_12m": data.insider_net_shares_12m,
                    "held_percent_insiders": data.held_percent_insiders,
                    "proxy_context": proxy_context,
                }

                # Trigger LLM estimation
                out = await llm_client.generate_sitg_score(data.ticker, ctx)
                
                # Extract values
                ceo_name = out.get("ceo_name") or data.ceo_name or ""
                sitg_score = float(out.get("sitg_score", 3.0))
                salary = out.get("ceo_base_salary")
                value = out.get("sitg_value")
                multiple = None
                tier = None

                # Enforce Python calculations & threshold tier categorization
                if salary is not None and value is not None and salary > 0:
                    multiple = value / salary
                    llm_tier = out.get("sitg_percentile_tier") or ""
                    
                    if multiple >= 100:
                        tier = llm_tier if "founder" in llm_tier.lower() or "top 10" in llm_tier.lower() else "Founder-Level SITG"
                    elif multiple >= 5:
                        tier = llm_tier if "above" in llm_tier.lower() or "most" in llm_tier.lower() else "Most S&P 500 CEOs"
                    else:
                        tier = "Below Average SITG"
                    multiple = round(multiple, 2)

                # Persist to stocks table
                upsert_stock_sec_info(
                    ticker=data.ticker,
                    ceo_name=ceo_name,
                    sitg_score=sitg_score,
                    ceo_base_salary=salary,
                    sitg_value=value,
                    sitg_multiple=multiple,
                    sitg_percentile_tier=tier,
                    insider_buy_count_12m=data.insider_buy_count_12m,
                    insider_sell_count_12m=data.insider_sell_count_12m,
                    insider_net_shares_12m=data.insider_net_shares_12m,
                    held_percent_insiders=data.held_percent_insiders,
                )

                processed += 1
                logger.info("[sec_filing_job] Successfully updated info for ticker: %s", ticker)

            except Exception as e:
                failed += 1
                logger.warning("[sec_filing_job] Failed to process ticker %s: %s", ticker, e)

            # Sleep between tickers
            await asyncio.sleep(1.0)

    # Gather tasks
    tasks = [_process_one(t) for t in tickers]
    await asyncio.gather(*tasks)

    summary = {
        "ok": True,
        "processed": processed,
        "failed": failed,
        "skipped": skipped,
        "total": len(tickers),
        "timestamp": time.time(),
    }
    logger.info("[sec_filing_job] Job completed. Summary: %s", summary)
    return summary
