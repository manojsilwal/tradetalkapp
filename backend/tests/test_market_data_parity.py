"""Best-effort Yahoo Finance parity checks for deterministic API fields.

These tests intentionally verify only falsifiable numeric fields, not LLM prose.
They are designed for scheduled/manual execution because live market data is time-sensitive.

Run (explicit):
  RUN_MARKET_PARITY=1 PYTHONPATH=. python -m unittest backend.tests.test_market_data_parity -v

Or run this file directly (sets RUN_MARKET_PARITY automatically):
  PYTHONPATH=. python backend/tests/test_market_data_parity.py

Optional env:
  MARKET_PARITY_TICKERS=SPY,AAPL,MSFT   (default: SPY,AAPL,MSFT)
  MARKET_PARITY_REQUIRE_NETWORK=1       (fail instead of skip if Yahoo reference fetch fails)
"""
import os
import re
import unittest

import yfinance as yf
from fastapi.testclient import TestClient

from backend.main import app


def _env_tickers():
    raw = os.environ.get("MARKET_PARITY_TICKERS", "SPY,AAPL,MSFT")
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def _extract_number(text: str):
    if not text or text == "N/A":
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group(0)) if m else None


class TestMarketDataParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        enabled = os.environ.get("RUN_MARKET_PARITY", "").strip().lower() in ("1", "true", "yes")
        if not enabled:
            raise unittest.SkipTest(
                "Set RUN_MARKET_PARITY=1 to run Yahoo parity checks "
                "(slow, live Yahoo + full /decision-terminal per ticker)."
            )
        cls.client = TestClient(app)
        cls.tickers = _env_tickers()
        cls.refs = {}
        try:
            for ticker in cls.tickers:
                t = yf.Ticker(ticker)
                fi = t.fast_info
                info = t.info or {}
                price = fi.get("lastPrice") or fi.get("regularMarketPrice") or info.get("currentPrice")
                if price is None:
                    raise RuntimeError(f"Missing price for {ticker}")
                cls.refs[ticker] = {
                    "price": float(price),
                    "gross_margins_pct": float(info["grossMargins"] * 100.0) if info.get("grossMargins") is not None else None,
                    "roe_pct": float(info["returnOnEquity"] * 100.0) if info.get("returnOnEquity") is not None else None,
                }
        except Exception as e:
            if os.environ.get("MARKET_PARITY_REQUIRE_NETWORK") == "1":
                raise
            raise unittest.SkipTest(f"Skipping live parity checks: {e}") from e

    def assertClosePctOrAbs(self, actual: float, expected: float, *, pct: float = 0.02, abs_floor: float = 2.0):
        tol = max(abs_floor, abs(expected) * pct)
        self.assertLessEqual(abs(actual - expected), tol, msg=f"actual={actual} expected={expected} tol={tol}")

    def test_decision_terminal_current_price_tracks_yahoo(self):
        for ticker in self.tickers:
            with self.subTest(ticker=ticker):
                r = self.client.get("/decision-terminal", params={"ticker": ticker})
                self.assertEqual(r.status_code, 200, r.text)
                payload = r.json()
                actual = payload["valuation"]["current_price_usd"]
                self.assertIsNotNone(actual)
                self.assertClosePctOrAbs(float(actual), self.refs[ticker]["price"])

    def test_metrics_endpoint_stable_percent_fields_track_yahoo(self):
        for ticker in self.tickers:
            with self.subTest(ticker=ticker):
                r = self.client.get(f"/metrics/{ticker}")
                self.assertEqual(r.status_code, 200, r.text)
                metrics = r.json()["metrics"]

                gm_expected = self.refs[ticker]["gross_margins_pct"]
                gm_actual = _extract_number(metrics.get("gross_margins", {}).get("current", ""))
                if gm_expected is not None and gm_actual is not None:
                    self.assertLessEqual(abs(gm_actual - gm_expected), 2.0)

                roe_expected = self.refs[ticker]["roe_pct"]
                roe_actual = _extract_number(metrics.get("roic_roe", {}).get("current", ""))
                if roe_expected is not None and roe_actual is not None:
                    self.assertLessEqual(abs(roe_actual - roe_expected), 2.0)


if __name__ == "__main__":
    os.environ.setdefault("RUN_MARKET_PARITY", "1")
    unittest.main()
