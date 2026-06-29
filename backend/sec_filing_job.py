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


async def fetch_recent_filing_tickers(days: int = 1, form: str = "10-Q") -> Set[str]:
    """
    Query the SEC Atom feed of the 100 most recent filings of type `form`
    and return the uppercase ticker symbols of companies that filed
    within the last `days` days.
    """
    logger.info("[sec_filing_job] Querying SEC %s Atom feed for recent filings...", form)
    try:
        _load_cik_map()
        cik_to_ticker = {cik: ticker for ticker, cik in _CIK_MAP.items()}
    except Exception as e:
        logger.error("[sec_filing_job] Failed to load CIK-to-ticker map: %s", e)
        return set()

    url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&count=100&output=atom"
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

    logger.info("[sec_filing_job] Found %d tickers with recent %s filings: %s", len(recent_tickers), form, recent_tickers)
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

    # Query SEC Atom feed for recent filings (last 1 day)
    recent_filing_tickers_10q = await fetch_recent_filing_tickers(days=1, form="10-Q")
    recent_filing_tickers_10k = await fetch_recent_filing_tickers(days=1, form="10-K")
    recent_filing_tickers = recent_filing_tickers_10q.union(recent_filing_tickers_10k)

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

                # Fetch 10-K and 10-Q contexts for new revenue engine scoring
                k_context = ""
                q_context = ""
                if fc.enabled:
                    try:
                        k_context = await fc.get_sec_filing(data.ticker, form="10-K", max_chars=8000)
                    except Exception as e:
                        logger.warning("[sec_filing_job] failed to fetch 10-K for %s: %s", data.ticker, e)
                    try:
                        q_context = await fc.get_sec_filing(data.ticker, form="10-Q", max_chars=8000)
                    except Exception as e:
                        logger.warning("[sec_filing_job] failed to fetch 10-Q for %s: %s", data.ticker, e)

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

                rev_ctx = {
                    "ticker": data.ticker,
                    "10_k_context": k_context,
                    "10_q_context": q_context,
                }

                # Trigger LLM estimation
                out, out_rev = await asyncio.gather(
                    llm_client.generate_sitg_score(data.ticker, ctx),
                    llm_client.generate_new_revenue_engine_score(data.ticker, rev_ctx)
                )
                
                # Extract values
                ceo_name = out.get("ceo_name") or data.ceo_name or ""
                sitg_score = float(out.get("sitg_score", 3.0))
                salary = out.get("ceo_base_salary")
                value = out.get("sitg_value")
                multiple = None
                tier = None

                try:
                    financial_traction = float(out_rev.get("financial_traction_score", 50))
                except (ValueError, TypeError):
                    financial_traction = 50.0

                try:
                    customer_adoption = float(out_rev.get("customer_adoption_score", 50))
                except (ValueError, TypeError):
                    customer_adoption = 50.0

                try:
                    management_commitment = float(out_rev.get("management_commitment_score", 50))
                except (ValueError, TypeError):
                    management_commitment = 50.0

                try:
                    market_opportunity = float(out_rev.get("market_opportunity_score", 50))
                except (ValueError, TypeError):
                    market_opportunity = 50.0

                try:
                    monetization_clarity = float(out_rev.get("monetization_clarity_score", 50))
                except (ValueError, TypeError):
                    monetization_clarity = 50.0

                try:
                    execution_capacity = float(out_rev.get("execution_capacity_score", 50))
                except (ValueError, TypeError):
                    execution_capacity = 50.0

                # Weighted total score calculation
                new_revenue_engine_score = (
                    0.30 * financial_traction +
                    0.20 * customer_adoption +
                    0.15 * management_commitment +
                    0.15 * market_opportunity +
                    0.10 * monetization_clarity +
                    0.10 * execution_capacity
                )

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
                    financial_traction_score=financial_traction,
                    customer_adoption_score=customer_adoption,
                    management_commitment_score=management_commitment,
                    market_opportunity_score=market_opportunity,
                    monetization_clarity_score=monetization_clarity,
                    execution_capacity_score=execution_capacity,
                    new_revenue_engine_score=new_revenue_engine_score,
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
