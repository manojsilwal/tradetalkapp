"""Yahoo Finance parity checks for deterministic API fields vs **Yahoo chart API**.

References use the same endpoint as production Playwright parity
(``query1.finance.yahoo.com/v8/finance/chart/...``), implemented in
:mod:`backend.connectors.yahoo_chart_reference`.

Fundamental percentage rows on ``/metrics/{ticker}`` still use ``yfinance``
``Ticker.info`` (chart JSON does not expose gross margin / ROE).

Env:
  MARKET_PARITY_TICKERS=SPY,AAPL,MSFT   (default)
"""
from __future__ import annotations

import os
import re
import unittest

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

import yfinance as yf
from fastapi.testclient import TestClient

from backend.connectors.yahoo_chart_reference import (
    YahooChartQuote,
    fetch_yahoo_chart_quotes,
)
from backend.main import app

# Align with ``tests/e2e/parity.spec.ts``
PRICE_TOLERANCE_PCT = 0.01
PRICE_TOLERANCE_ABS = 1.0
MACRO_TOLERANCE_PCT = 0.05
PCT_POINT_TOLERANCE = 0.75

MACRO_SECTOR_SYMBOLS = ["XLK", "XLF", "XLV", "XLE", "XLC", "XLRE", "XME"]
MACRO_FLOW_SYMBOLS = ["SPY", "EFA", "EWJ", "TLT", "GLD", "BIL"]


def _env_tickers():
    raw = os.environ.get("MARKET_PARITY_TICKERS", "SPY,AAPL,MSFT")
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def _extract_number(text: str):
    if not text or text == "N/A":
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group(0)) if m else None


def _price_tol(expected: float) -> float:
    return max(PRICE_TOLERANCE_ABS, abs(expected) * PRICE_TOLERANCE_PCT)


def _macro_tol(expected: float) -> float:
    return max(0.01, abs(expected) * MACRO_TOLERANCE_PCT)


class TestMarketDataParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import backend.rate_limiter as rl

        rl.RATE_LIMIT_ENABLED = False
        with rl._lock:
            rl._hits.clear()
        cls.client = TestClient(app)
        cls.tickers = _env_tickers()

        chart_symbols = sorted(
            set(cls.tickers)
            | {"^VIX"}
            | set(MACRO_SECTOR_SYMBOLS)
            | set(MACRO_FLOW_SYMBOLS)
            | {"GC=F", "DX-Y.NYB"}
        )
        try:
            cls.chart: dict[str, YahooChartQuote] = fetch_yahoo_chart_quotes(chart_symbols)
        except Exception as e:
            raise RuntimeError(
                "Yahoo chart reference fetch failed. Check network and Yahoo availability."
            ) from e

        cls._fundamentals: dict[str, dict] = {}
        try:
            for ticker in cls.tickers:
                t = yf.Ticker(ticker)
                info = t.info or {}
                cls._fundamentals[ticker] = {
                    "gross_margins_pct": float(info["grossMargins"] * 100.0)
                    if info.get("grossMargins") is not None
                    else None,
                    "roe_pct": float(info["returnOnEquity"] * 100.0)
                    if info.get("returnOnEquity") is not None
                    else None,
                }
        except Exception as e:
            raise RuntimeError(
                "yfinance fundamentals fetch failed for metrics parity."
            ) from e

    def test_decision_terminal_current_price_tracks_yahoo_chart(self):
        for ticker in self.tickers:
            with self.subTest(ticker=ticker):
                ref = self.chart[ticker].regular_market_price
                r = self.client.get("/decision-terminal", params={"ticker": ticker})
                self.assertEqual(r.status_code, 200, r.text)
                payload = r.json()
                actual = payload["valuation"]["current_price_usd"]
                self.assertIsNotNone(actual)
                tol = _price_tol(ref)
                diff = abs(float(actual) - ref)
                self.assertLessEqual(
                    diff,
                    tol,
                    msg=f"{ticker}: app={actual} yahoo_chart={ref} tol={tol}",
                )

    def test_metrics_endpoint_stable_percent_fields_track_yahoo(self):
        for ticker in self.tickers:
            with self.subTest(ticker=ticker):
                r = self.client.get(f"/metrics/{ticker}")
                self.assertEqual(r.status_code, 200, r.text)
                metrics = r.json()["metrics"]
                refs = self._fundamentals[ticker]

                gm_expected = refs["gross_margins_pct"]
                gm_actual = _extract_number(metrics.get("gross_margins", {}).get("current", ""))
                if gm_expected is not None and gm_actual is not None:
                    self.assertLessEqual(abs(gm_actual - gm_expected), 2.0)

                roe_expected = refs["roe_pct"]
                roe_actual = _extract_number(metrics.get("roic_roe", {}).get("current", ""))
                if roe_expected is not None and roe_actual is not None:
                    self.assertLessEqual(abs(roe_actual - roe_expected), 2.0)

    def test_macro_vix_tracks_yahoo_chart(self):
        r = self.client.get("/macro")
        self.assertEqual(r.status_code, 200, r.text)
        app_vix = r.json()["vix_level"]
        ref = self.chart["^VIX"].regular_market_price
        tol = _macro_tol(ref)
        self.assertLessEqual(
            abs(float(app_vix) - ref),
            tol,
            msg=f"VIX app={app_vix} yahoo_chart={ref} tol={tol}",
        )

    def test_macro_sector_daily_change_tracks_yahoo_chart(self):
        r = self.client.get("/macro")
        self.assertEqual(r.status_code, 200, r.text)
        sectors = r.json()["sectors"]
        by_sym = {s["symbol"]: s["daily_change_pct"] for s in sectors}
        for sym in MACRO_SECTOR_SYMBOLS:
            with self.subTest(symbol=sym):
                y = self.chart[sym]
                self.assertIsNotNone(
                    y.change_pct,
                    msg=f"{sym}: Yahoo chart did not provide change_pct",
                )
                app_pct = by_sym.get(sym)
                self.assertIsNotNone(app_pct, msg=f"missing sector {sym} in /macro")
                diff = abs(float(app_pct) - float(y.change_pct))
                self.assertLessEqual(
                    diff,
                    PCT_POINT_TOLERANCE,
                    msg=f"{sym}: app={app_pct}% yahoo={y.change_pct}% tol={PCT_POINT_TOLERANCE}pp",
                )

    def test_macro_capital_flow_daily_change_tracks_yahoo_chart(self):
        r = self.client.get("/macro")
        self.assertEqual(r.status_code, 200, r.text)
        flows = r.json()["capital_flows"]
        by_asset = {f["asset"]: f["daily_change_pct"] for f in flows}
        for sym in MACRO_FLOW_SYMBOLS:
            with self.subTest(asset=sym):
                y = self.chart[sym]
                self.assertIsNotNone(y.change_pct, msg=f"{sym}: Yahoo chart missing change_pct")
                app_pct = by_asset.get(sym)
                self.assertIsNotNone(app_pct, msg=f"missing flow {sym} in /macro")
                diff = abs(float(app_pct) - float(y.change_pct))
                self.assertLessEqual(
                    diff,
                    PCT_POINT_TOLERANCE,
                    msg=f"{sym}: app={app_pct}% yahoo={y.change_pct}% tol={PCT_POINT_TOLERANCE}pp",
                )

    def test_gold_advisor_context_prices_track_yahoo_chart(self):
        """Numeric ``context.macro`` only (LLM briefing is not compared to Yahoo)."""
        r = self.client.get("/advisor/gold")
        self.assertEqual(r.status_code, 200, r.text)
        macro = r.json()["context"]["macro"]

        gold_app = macro.get("gold_futures_last_usd")
        self.assertIsNotNone(gold_app)
        g_ref = self.chart["GC=F"].regular_market_price
        self.assertLessEqual(
            abs(float(gold_app) - g_ref),
            _macro_tol(g_ref),
            msg=f"GC=F app={gold_app} yahoo_chart={g_ref}",
        )

        dxy_app = macro.get("dxy_spot")
        self.assertIsNotNone(dxy_app)
        d_ref = self.chart["DX-Y.NYB"].regular_market_price
        self.assertLessEqual(
            abs(float(dxy_app) - d_ref),
            _macro_tol(d_ref),
            msg=f"DXY app={dxy_app} yahoo_chart={d_ref}",
        )

        vix_app = macro.get("vix")
        self.assertIsNotNone(vix_app)
        v_ref = self.chart["^VIX"].regular_market_price
        self.assertLessEqual(
            abs(float(vix_app) - v_ref),
            _macro_tol(v_ref),
            msg=f"VIX context app={vix_app} yahoo_chart={v_ref}",
        )


if __name__ == "__main__":
    unittest.main()
