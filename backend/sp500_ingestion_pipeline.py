from __future__ import annotations

"""
sp500_ingestion_pipeline.py
----------------------------
Daily ingestion pipeline for S&P 500 fundamentals and sector analysis.
Writes natural-language narratives into the KnowledgeStore so all agents
can RAG-retrieve up-to-date fundamental context.

Schedule: Run once daily after market close (e.g. 6pm ET).

Usage (manual trigger):
    POST /sp500-ingest
Or automatically called from daily_pipeline.py scheduler.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── S&P 500 Sector ETF Map ────────────────────────────────────────────────────
SECTOR_ETFS = {
    "Technology":             "XLK",
    "Financials":             "XLF",
    "Healthcare":             "XLV",
    "Consumer Discretionary": "XLY",
    "Consumer Staples":       "XLP",
    "Industrials":            "XLI",
    "Energy":                 "XLE",
    "Materials":              "XLB",
    "Real Estate":            "XLRE",
    "Utilities":              "XLU",
    "Communication Services": "XLC",
}

# ── Core Companies to prioritize (always ingest these) ───────────────────────
PRIORITY_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "BRK-B",
    "JPM", "V", "MA", "UNH", "JNJ", "XOM", "CVX", "HD", "PG", "KO",
    "AVGO", "LLY", "PEP", "COST", "MRK", "ABBV", "TMO", "ABT", "ORCL",
    "ACN", "AMD", "INTU", "QCOM", "TXN", "HON", "UPS", "CAT", "GE",
    "NEE", "DUK", "SHW", "LIN", "APD", "FCX", "NEM", "BHP", "VALE",
]


def _yfinance_available() -> bool:
    try:
        import yfinance  # noqa: F401
        return True
    except ImportError:
        return False


def _build_fundamental_narrative(ticker: str, info: dict) -> tuple[str, dict]:
    """
    Convert raw yFinance info dict into a natural-language narrative string
    suitable for embedding. Returns (narrative_text, clean_metadata).
    
    IMPORTANT: We never embed raw numbers directly into the vector store.
    We convert them to plain English so the embedding model encodes semantics,
    not arithmetic.
    """
    name        = info.get("longName") or ticker
    sector      = info.get("sector") or "Unknown"
    industry    = info.get("industry") or "Unknown"
    pe          = info.get("trailingPE") or 0.0
    forward_pe  = info.get("forwardPE") or 0.0
    eps         = info.get("trailingEps") or 0.0
    market_cap  = (info.get("marketCap") or 0) / 1e9   # convert to billions
    rev_growth  = (info.get("revenueGrowth") or 0) * 100
    profit_margin = (info.get("profitMargins") or 0) * 100
    debt_equity = info.get("debtToEquity") or 0.0
    roe         = (info.get("returnOnEquity") or 0) * 100
    beta        = info.get("beta") or 1.0
    dividend    = (info.get("dividendYield") or 0) * 100
    analyst_rec = info.get("recommendationKey") or "none"

    # ── Categorize PE ratio contextually ──────────────────────────────────────
    if pe <= 0:
        pe_desc = "currently not profitable (negative or zero earnings)"
    elif pe < 10:
        pe_desc = f"trades at a deeply discounted P/E of {pe:.1f}x, suggesting value or distress"
    elif pe < 20:
        pe_desc = f"trades at a reasonable P/E of {pe:.1f}x, in line with historical averages"
    elif pe < 35:
        pe_desc = f"trades at a growth premium P/E of {pe:.1f}x"
    else:
        pe_desc = f"trades at an elevated P/E of {pe:.1f}x, implying very high growth expectations"

    # ── Categorize revenue growth ──────────────────────────────────────────────
    if rev_growth > 20:
        growth_desc = f"strong revenue growth of {rev_growth:.1f}% YoY"
    elif rev_growth > 5:
        growth_desc = f"moderate revenue growth of {rev_growth:.1f}% YoY"
    elif rev_growth >= 0:
        growth_desc = f"flat revenue growth of {rev_growth:.1f}% YoY"
    else:
        growth_desc = f"declining revenue of {rev_growth:.1f}% YoY"

    # ── Categorize leverage ───────────────────────────────────────────────────
    if debt_equity <= 0:
        leverage_desc = "net cash position (no meaningful debt)"
    elif debt_equity < 50:
        leverage_desc = f"conservative leverage (D/E {debt_equity:.0f}%)"
    elif debt_equity < 150:
        leverage_desc = f"moderate leverage (D/E {debt_equity:.0f}%)"
    else:
        leverage_desc = f"high leverage (D/E {debt_equity:.0f}%), worth monitoring"

    # ── Analyst sentiment ─────────────────────────────────────────────────────
    rec_map = {
        "strong_buy": "analysts strongly recommend buying",
        "buy": "analysts recommend buying",
        "hold": "analysts recommend holding",
        "underperform": "analysts are cautious",
        "sell": "analysts recommend selling",
    }
    rec_desc = rec_map.get(analyst_rec, "no consensus analyst rating available")

    # ── Assemble final narrative ───────────────────────────────────────────────
    narrative = (
        f"{name} ({ticker}) is a {sector} company in the {industry} industry "
        f"with a market cap of ${market_cap:.1f}B. "
        f"It {pe_desc}. "
        f"The company shows {growth_desc}, "
        f"with profit margins of {profit_margin:.1f}% and ROE of {roe:.1f}%. "
        f"Balance sheet shows {leverage_desc}. "
        f"Beta is {beta:.2f} vs the market. "
    )

    if dividend > 0:
        narrative += f"It pays a dividend yield of {dividend:.2f}%. "

    if forward_pe > 0 and pe > 0:
        if forward_pe < pe:
            narrative += f"Forward P/E of {forward_pe:.1f}x suggests earnings are expected to grow. "
        else:
            narrative += f"Forward P/E of {forward_pe:.1f}x is above trailing, implying margin pressure or uncertainty. "

    narrative += f"Current analyst consensus: {rec_desc}."

    metadata = {
        "sector":       sector,
        "pe_ratio":     pe,
        "eps":          eps,
        "market_cap_b": market_cap,
    }
    return narrative.strip(), metadata


def _build_sector_narrative(sector_name: str, etf_ticker: str,
                             etf_info: dict, week_return_pct: float) -> str:
    """Build a natural-language sector rotation narrative from ETF data."""
    if week_return_pct > 3:
        momentum = f"strong outperformer with {week_return_pct:.1f}% weekly gain"
    elif week_return_pct > 1:
        momentum = f"mild outperformer with {week_return_pct:.1f}% weekly gain"
    elif week_return_pct > -1:
        momentum = f"broadly flat, up {week_return_pct:.1f}% on the week"
    elif week_return_pct > -3:
        momentum = f"mild underperformer, down {abs(week_return_pct):.1f}% on the week"
    else:
        momentum = f"significant underperformer, down {abs(week_return_pct):.1f}% on the week"

    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    return (
        f"As of {today}, the S&P 500 {sector_name} sector (ETF: {etf_ticker}) is a {momentum}. "
        f"This sector is monitored for rotation signals relative to the broader S&P 500. "
        f"Investors seeking {sector_name.lower()} exposure should weigh this momentum "
        f"alongside interest rate sensitivity and macro regime context."
    )


async def _fetch_ticker_info(ticker: str) -> Optional[dict]:
    """Async wrapper around yFinance ticker info fetch."""
    try:
        import yfinance as yf
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: yf.Ticker(ticker).info)
        return info if isinstance(info, dict) and info.get("symbol") else None
    except Exception as e:
        msg = str(e).lower()
        # Yahoo often returns 401/429/NoneType from datacenter IPs — log quietly
        if any(
            x in msg
            for x in ("nonetype", "401", "429", "unauthorized", "rate", "too many", "crumb")
        ):
            logger.debug("[SP500Pipeline] fetch %s (suppressed): %s", ticker, e)
        else:
            logger.warning("[SP500Pipeline] Failed to fetch %s: %s", ticker, e)
        return None


async def _fetch_week_return(etf_ticker: str) -> float:
    """Fetch 5-day return for a sector ETF."""
    try:
        import yfinance as yf
        loop = asyncio.get_event_loop()
        hist = await loop.run_in_executor(
            None,
            lambda: yf.Ticker(etf_ticker).history(period="5d")
        )
        if hist.empty or len(hist) < 2:
            return 0.0
        start_price = hist["Close"].iloc[0]
        end_price   = hist["Close"].iloc[-1]
        if start_price == 0:
            return 0.0
        return ((end_price - start_price) / start_price) * 100
    except Exception as e:
        msg = str(e).lower()
        if any(x in msg for x in ("401", "429", "unauthorized", "rate", "too many", "crumb")):
            logger.debug("[SP500Pipeline] ETF return %s (suppressed): %s", etf_ticker, e)
        else:
            logger.warning("[SP500Pipeline] Failed to fetch return for %s: %s", etf_ticker, e)
        return 0.0


def _default_batch_size() -> int:
    try:
        return max(1, int(os.environ.get("SP500_BATCH_SIZE", "5")))
    except ValueError:
        return 5


async def run_sp500_ingestion(
    tickers: Optional[list[str]] = None,
    batch_size: int | None = None,
) -> dict:
    """
    Main entry point. Fetches fundamentals for each ticker and sector ETF,
    converts to narratives, and upserts into the KnowledgeStore.

    Args:
        tickers: List of tickers to process. Defaults to PRIORITY_TICKERS.
        batch_size: Number of tickers to process concurrently.

    Returns:
        Summary dict with counts and any errors.
    """
    from .knowledge_store import get_knowledge_store

    if not _yfinance_available():
        logger.error("[SP500Pipeline] yfinance not installed. Run: pip install yfinance")
        return {"error": "yfinance not installed", "tickers_written": 0, "sectors_written": 0}

    store = get_knowledge_store()
    target_tickers = tickers or PRIORITY_TICKERS
    if batch_size is None:
        batch_size = _default_batch_size()
    tickers_written = 0
    tickers_failed  = 0

    logger.info(f"[SP500Pipeline] Starting fundamentals ingestion for {len(target_tickers)} tickers...")

    # ── Process tickers in async batches ──────────────────────────────────────
    for i in range(0, len(target_tickers), batch_size):
        batch = target_tickers[i : i + batch_size]
        results = await asyncio.gather(*[_fetch_ticker_info(t) for t in batch])

        for ticker, info in zip(batch, results):
            if not info:
                tickers_failed += 1
                continue
            try:
                narrative, meta = _build_fundamental_narrative(ticker, info)
                store.upsert_sp500_fundamental(
                    ticker       = ticker,
                    sector       = meta["sector"],
                    narrative    = narrative,
                    pe_ratio     = meta["pe_ratio"],
                    eps          = meta["eps"],
                    market_cap_b = meta["market_cap_b"],
                )
                tickers_written += 1
                logger.debug(f"[SP500Pipeline] ✅ {ticker}")
            except Exception as e:
                logger.warning(f"[SP500Pipeline] Error processing {ticker}: {e}")
                tickers_failed += 1

        # Sleep between batches — Yahoo aggressively rate-limits cloud IPs
        await asyncio.sleep(float(os.environ.get("SP500_BATCH_SLEEP_SEC", "2.0")))

    # ── Process sector ETFs ────────────────────────────────────────────────────
    logger.info("[SP500Pipeline] Starting sector rotation ingestion...")
    sectors_written = 0
    sectors_failed  = 0

    # Stagger sector ETF fetches (parallel blast triggers Yahoo rate limits on cloud IPs)
    etf_list = list(SECTOR_ETFS.values())
    week_returns: list[float] = []
    sector_chunk = max(1, int(os.environ.get("SP500_SECTOR_CONCURRENCY", "2")))
    for j in range(0, len(etf_list), sector_chunk):
        chunk = etf_list[j : j + sector_chunk]
        part = await asyncio.gather(*[_fetch_week_return(e) for e in chunk])
        week_returns.extend(part)
        await asyncio.sleep(0.5)

    for (sector_name, etf_ticker), week_return in zip(SECTOR_ETFS.items(), week_returns):
        try:
            etf_info = await _fetch_ticker_info(etf_ticker) or {}
            narrative = _build_sector_narrative(sector_name, etf_ticker, etf_info, week_return)
            store.upsert_sp500_sector_analysis(
                sector_name    = sector_name,
                etf_ticker     = etf_ticker,
                narrative      = narrative,
                week_return_pct = week_return,
            )
            sectors_written += 1
            logger.debug(f"[SP500Pipeline] ✅ Sector: {sector_name} ({week_return:+.1f}%)")
        except Exception as e:
            logger.warning(f"[SP500Pipeline] Error processing sector {sector_name}: {e}")
            sectors_failed += 1

    summary = {
        "tickers_written": tickers_written,
        "tickers_failed":  tickers_failed,
        "sectors_written": sectors_written,
        "sectors_failed":  sectors_failed,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
    }
    logger.info(f"[SP500Pipeline] Complete: {summary}")
    return summary
