"""
Phase 3 — Ingest quarterly financial statements for all S&P 500 stocks.

Fetches income statement, balance sheet, and cash flow statement via yfinance.
Merges into a single wide dataframe per ticker with key metrics computed.
Stores one Parquet file per ticker.

Usage:
    python -m backend.data_lake.ingest_fundamentals --dry-run
    python -m backend.data_lake.ingest_fundamentals --tickers AAPL,MSFT
    python -m backend.data_lake.ingest_fundamentals
"""
import argparse
import logging
import time
import os

import pandas as pd
import yfinance as yf

from . import config
from . import checkpoint

logger = logging.getLogger(__name__)
PHASE = "fundamentals"

# Key rows to extract from yfinance financial statements
INCOME_KEYS = [
    "Total Revenue", "Gross Profit", "Operating Income", "Net Income",
    "Basic EPS", "Diluted EPS", "EBITDA", "Total Expenses",
]
BALANCE_KEYS = [
    "Total Assets", "Total Liabilities Net Minority Interest",
    "Total Debt", "Cash And Cash Equivalents", "Stockholders Equity",
    "Common Stock Equity", "Net Tangible Assets",
]
CASHFLOW_KEYS = [
    "Operating Cash Flow", "Free Cash Flow", "Capital Expenditure",
    "Cash Dividends Paid", "Repurchase Of Capital Stock",
]


def _extract_rows(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Extract specific rows from a yfinance financial statement (rows=items, cols=dates)."""
    if df is None or df.empty:
        return pd.DataFrame()
    available = [k for k in keys if k in df.index]
    if not available:
        return pd.DataFrame()
    subset = df.loc[available].T
    subset.index.name = "date"
    return subset


def _compute_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """Derive financial ratios from merged statement data."""
    if df.empty:
        return df
    df = df.copy()
    if "Total Revenue" in df.columns and "Gross Profit" in df.columns:
        df["gross_margin"] = (df["Gross Profit"] / df["Total Revenue"]).round(4)
    if "Total Revenue" in df.columns and "Operating Income" in df.columns:
        df["operating_margin"] = (df["Operating Income"] / df["Total Revenue"]).round(4)
    if "Total Revenue" in df.columns and "Net Income" in df.columns:
        df["net_margin"] = (df["Net Income"] / df["Total Revenue"]).round(4)
    if "Net Income" in df.columns and "Stockholders Equity" in df.columns:
        equity = df["Stockholders Equity"].replace(0, float("nan"))
        df["roe"] = (df["Net Income"] / equity).round(4)
    if "Total Debt" in df.columns and "Cash And Cash Equivalents" in df.columns:
        debt = df["Total Debt"].replace(0, float("nan"))
        df["cash_to_debt"] = (df["Cash And Cash Equivalents"] / debt).round(4)
    return df


def ingest_ticker(ticker: str) -> pd.DataFrame | None:
    """Fetch and merge quarterly financials for a single ticker."""
    try:
        stock = yf.Ticker(config.yfinance_symbol(ticker))

        income = _extract_rows(stock.quarterly_income_stmt, INCOME_KEYS)
        balance = _extract_rows(stock.quarterly_balance_sheet, BALANCE_KEYS)
        cashflow = _extract_rows(stock.quarterly_cashflow, CASHFLOW_KEYS)

        frames = [f for f in [income, balance, cashflow] if not f.empty]
        if not frames:
            logger.warning("[%s] No financial data available", ticker)
            return None

        merged = pd.concat(frames, axis=1)
        merged = _compute_ratios(merged)
        merged["ticker"] = ticker
        return merged
    except Exception as e:
        logger.warning("[%s] Failed: %s", ticker, e)
        return None


def run(tickers: list[str], dry_run: bool = False) -> dict:
    """Main entry point for fundamentals ingestion."""
    config.ensure_dirs()
    remaining = checkpoint.get_remaining(PHASE, tickers)
    logger.info("[Fundamentals] %d tickers total, %d remaining", len(tickers), len(remaining))

    if dry_run:
        logger.info("[DRY RUN] Would fetch fundamentals for %d tickers", len(remaining))
        return {"phase": PHASE, "total": len(remaining), "ingested": 0, "dry_run": True}

    ingested = 0
    errors = []

    for i, ticker in enumerate(remaining):
        df = ingest_ticker(ticker)
        if df is not None and not df.empty:
            out_path = os.path.join(config.FUNDAMENTALS_DIR, f"{ticker}.parquet")
            df.to_parquet(out_path, index=True)
            ingested += 1
            checkpoint.mark_done(PHASE, ticker)
        else:
            errors.append(ticker)

        if (i + 1) % 50 == 0:
            logger.info("Progress: %d / %d tickers", i + 1, len(remaining))

        time.sleep(config.YFINANCE_SLEEP_BETWEEN_TICKERS)

    logger.info("[Fundamentals] Done: %d ingested, %d errors", ingested, len(errors))
    return {"phase": PHASE, "ingested": ingested, "errors": errors}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ingest quarterly financials")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tickers", type=str, default=None)
    args = parser.parse_args()

    ticker_list = config.get_tickers(args.tickers, args.dry_run)
    run(ticker_list, dry_run=args.dry_run)
