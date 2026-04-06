#!/usr/bin/env python3
"""
Print a Yahoo Finance reference snapshot (JSON) for manual QA / spreadsheets.

Uses yfinance only — no TradeTalk server. Aligns fields with
backend/tests/test_market_data_parity.py for the same tickers.

Usage:
  python3 scripts/qa_yahoo_reference.py SPY AAPL MSFT
  python3 scripts/qa_yahoo_reference.py --pretty
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone


def _snapshot(ticker: str) -> dict:
    import yfinance as yf

    t = yf.Ticker(ticker.upper())
    fi = t.fast_info
    info = t.info or {}
    price = fi.get("lastPrice") or fi.get("regularMarketPrice") or info.get("currentPrice")
    gm = info.get("grossMargins")
    roe = info.get("returnOnEquity")
    return {
        "ticker": ticker.upper(),
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "price": float(price) if price is not None else None,
        "gross_margins_pct": float(gm * 100.0) if gm is not None else None,
        "roe_pct": float(roe * 100.0) if roe is not None else None,
        "shortName": info.get("shortName"),
        "currency": info.get("currency"),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Yahoo Finance JSON snapshot for QA (yfinance).")
    p.add_argument(
        "tickers",
        nargs="*",
        default=["SPY", "AAPL", "MSFT"],
        help="Ticker symbols (default: SPY AAPL MSFT)",
    )
    p.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = p.parse_args()

    tickers = [x.strip().upper() for x in args.tickers if x and str(x).strip()]
    if not tickers:
        print("No tickers given.", file=sys.stderr)
        return 1

    out = {"source": "yfinance", "tickers": [_snapshot(t) for t in tickers]}
    if args.pretty:
        print(json.dumps(out, indent=2))
    else:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
