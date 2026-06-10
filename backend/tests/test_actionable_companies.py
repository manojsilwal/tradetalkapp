"""Offline tests for the Actionable Companies S&P 500 batch screener service."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from backend import actionable_companies as svc
from backend import connector_cache


def _good_fundamentals(ticker: str = "AAPL") -> dict:
    return {
        "ticker": ticker,
        "company_name": f"{ticker} Inc",
        "sector": "Technology",
        "industry": "Hardware",
        "market_cap": 3.0e12,
        "current_price": 200.0,
        "return_on_equity_pct": 28.0,
        "return_on_assets_pct": 14.0,
        "gross_margin_pct": 45.0,
        "operating_margin_pct": 30.0,
        "fcf_yield_pct": 4.5,
        "ocf_margin_pct": 28.0,
        "debt_to_equity": 0.8,
        "current_ratio": 1.4,
        "ebitda_to_debt": 0.9,
        "revenue_growth_pct": 12.0,
        "earnings_growth_pct": 15.0,
        "pe_stretch_pct": -8.0,
        "pt_upside_pct": 14.0,
        "trailing_pe": 28.0,
        "forward_pe": 25.0,
        "trailing_eps": 7.0,
        "dividend_yield_pct": 0.5,
    }


def _uptrend_closes(n: int = 260, start: float = 100.0) -> list:
    return [start * (1.0 + 0.002 * i) for i in range(n)]


class TestMetricMath(unittest.TestCase):
    def test_rsi_requires_15_closes(self):
        self.assertIsNone(svc.compute_rsi_14([100.0] * 14))

    def test_rsi_all_gains_is_100(self):
        closes = [100.0 + i for i in range(20)]
        self.assertEqual(svc.compute_rsi_14(closes), 100.0)

    def test_rsi_mixed_is_bounded(self):
        closes = [100, 102, 101, 103, 102, 104, 103, 105, 104, 106, 105, 107, 106, 108, 107, 109]
        rsi = svc.compute_rsi_14([float(c) for c in closes])
        self.assertIsNotNone(rsi)
        self.assertGreater(rsi, 50.0)
        self.assertLess(rsi, 100.0)

    def test_momentum_from_closes(self):
        closes = _uptrend_closes()
        m = svc.momentum_from_closes(closes)
        self.assertAlmostEqual(m["last_close"], closes[-1], places=3)
        self.assertGreater(m["ret_1m_pct"], 0)
        self.assertGreater(m["ret_3m_pct"], m["ret_1m_pct"])
        self.assertGreater(m["ret_6m_pct"], m["ret_3m_pct"])
        self.assertEqual(m["pct_of_52wk_high"], 100.0)  # uptrend → at the high
        self.assertIsNotNone(m["rsi_14"])

    def test_momentum_empty(self):
        m = svc.momentum_from_closes([])
        self.assertIsNone(m["last_close"])
        self.assertIsNone(m["rsi_14"])

    def test_verdict_thresholds(self):
        self.assertEqual(svc.verdict_from_score(80), "Strong Buy")
        self.assertEqual(svc.verdict_from_score(65), "Buy")
        self.assertEqual(svc.verdict_from_score(50), "Hold")
        self.assertEqual(svc.verdict_from_score(40), "Sell")
        self.assertEqual(svc.verdict_from_score(20), "Strong Sell")


class TestScoreCompany(unittest.TestCase):
    def test_strong_company_scores_high(self):
        momo = svc.momentum_from_closes(_uptrend_closes())
        out = svc.score_company(_good_fundamentals(), momo)
        self.assertFalse(out["insufficient_data"])
        self.assertGreaterEqual(out["score"], 60)
        self.assertIn(out["verdict"], ("Strong Buy", "Buy"))
        self.assertTrue(out["actionable"])
        self.assertGreaterEqual(out["coverage"], 0.9)
        for pillar in svc.PILLAR_WEIGHTS:
            self.assertIn(pillar, out["pillars"])

    def test_weak_company_scores_low(self):
        fund = _good_fundamentals("WEAK")
        fund.update({
            "return_on_equity_pct": -5.0,
            "return_on_assets_pct": -2.0,
            "gross_margin_pct": 8.0,
            "operating_margin_pct": -4.0,
            "fcf_yield_pct": -2.0,
            "ocf_margin_pct": -5.0,
            "debt_to_equity": 3.5,
            "current_ratio": 0.6,
            "ebitda_to_debt": 0.05,
            "revenue_growth_pct": -8.0,
            "earnings_growth_pct": -20.0,
            "pe_stretch_pct": 40.0,
            "pt_upside_pct": -15.0,
        })
        downtrend = [200.0 * (1.0 - 0.002 * i) for i in range(260)]
        out = svc.score_company(fund, svc.momentum_from_closes(downtrend))
        self.assertLess(out["score"], 45)
        self.assertIn(out["verdict"], ("Sell", "Strong Sell"))
        self.assertTrue(out["actionable"])  # sell-side signals are actionable too

    def test_insufficient_data_excluded(self):
        out = svc.score_company({"ticker": "EMPTY"}, svc.momentum_from_closes([]))
        self.assertTrue(out["insufficient_data"])
        self.assertIsNone(out["score"])
        self.assertFalse(out["actionable"])


class TestFundamentalsHourCache(unittest.TestCase):
    def test_fetch_fundamentals_uses_connector_cache(self):
        connector_cache.set_cached(svc._FUND_CACHE_CONNECTOR, {"ticker": "ZZZT", "cached": True}, "ZZZT")
        out = svc.fetch_fundamentals("ZZZT")  # would import yfinance on a miss
        self.assertTrue(out["cached"])


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ACTIONABLE_DB_PATH"] = os.path.join(self._tmp.name, "actionable.db")

    def tearDown(self):
        os.environ.pop("ACTIONABLE_DB_PATH", None)
        self._tmp.cleanup()

    def _row(self, ticker: str, score: float, verdict: str, actionable: bool = True) -> dict:
        return {
            "ticker": ticker,
            "company_name": f"{ticker} Inc",
            "sector": "Tech",
            "score": score,
            "verdict": verdict,
            "actionable": actionable,
            "coverage": 0.9,
            "pillars": {},
            "fundamentals": {},
            "momentum": {},
        }

    def test_upsert_and_load_sorted(self):
        rows = [
            self._row("AAA", 80.0, "Strong Buy"),
            self._row("BBB", 50.0, "Hold", actionable=False),
            self._row("CCC", 65.0, "Buy"),
        ]
        n = svc.persist_snapshot("snap1", rows, universe_size=3, skipped=0)
        self.assertEqual(n, 3)

        meta = svc.latest_snapshot_meta()
        self.assertEqual(meta["snapshot_id"], "snap1")
        self.assertEqual(meta["scored"], 3)

        top = svc.load_snapshot_rows("snap1", limit=10, actionable_only=True)
        self.assertEqual([r["ticker"] for r in top], ["AAA", "CCC"])  # Hold filtered, sorted desc

        everything = svc.load_snapshot_rows("snap1", limit=10, actionable_only=False)
        self.assertEqual(len(everything), 3)

    def test_fresh_snapshot_ttl(self):
        svc.persist_snapshot("snap_old", [self._row("AAA", 70.0, "Buy")], universe_size=1, skipped=0)
        self.assertIsNotNone(svc.fresh_snapshot_meta(ttl_s=3600))
        self.assertIsNone(svc.fresh_snapshot_meta(ttl_s=0))

    def test_reupsert_replaces_rows(self):
        svc.persist_snapshot("snap1", [self._row("AAA", 70.0, "Buy")], universe_size=1, skipped=0)
        svc.persist_snapshot("snap1", [self._row("ZZZ", 75.0, "Strong Buy")], universe_size=1, skipped=0)
        rows = svc.load_snapshot_rows("snap1", actionable_only=False)
        self.assertEqual([r["ticker"] for r in rows], ["ZZZ"])


class TestRagStorage(unittest.TestCase):
    def test_store_rows_to_rag_upserts_narratives(self):
        store = MagicMock()
        row = {
            "ticker": "AAPL",
            "score": 71.2,
            "verdict": "Buy",
            "coverage": 0.95,
            "pillars": {},
            "fundamentals": _good_fundamentals(),
            "momentum": {"ret_3m_pct": 9.1, "rsi_14": 58.0},
        }
        with patch("backend.knowledge_store.get_knowledge_store", return_value=store):
            written = svc._store_rows_to_rag([row])
        self.assertEqual(written, 1)
        store.upsert_sp500_fundamental.assert_called_once()
        kwargs = store.upsert_sp500_fundamental.call_args.kwargs
        self.assertEqual(kwargs["ticker"], "AAPL")
        self.assertIn("AAPL", kwargs["narrative"])
        self.assertIn("Buy", kwargs["narrative"])

    def test_rag_disabled_via_env(self):
        with patch.dict(os.environ, {"ACTIONABLE_RAG_ENABLE": "0"}):
            self.assertEqual(svc._store_rows_to_rag([{"ticker": "AAPL"}]), 0)


class TestScanPipeline(unittest.TestCase):
    """End-to-end worker run with mocked yfinance fetchers + temp DBs (offline)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ACTIONABLE_DB_PATH"] = os.path.join(self._tmp.name, "actionable.db")
        os.environ["ACTIONABLE_RAG_ENABLE"] = "0"
        os.environ["ACTIONABLE_INTER_CHUNK_DELAY_S"] = "0"
        os.environ["ACTIONABLE_CHUNK_SIZE"] = "2"
        # Decision ledger → temp sqlite (AGENTS.md producer-test contract)
        os.environ["DECISIONS_DB_PATH"] = os.path.join(self._tmp.name, "decisions.db")
        os.environ["DECISION_LEDGER_ENABLE"] = "1"
        os.environ["DECISION_BACKEND"] = "sqlite"
        from backend import decision_ledger as dl

        dl._reset_singleton_for_tests()
        svc._set_job(
            job_id=None, status="idle", progress=0, message="", processed=0,
            total=0, snapshot_id=None, cache_hit=False, error=None,
        )

    def tearDown(self):
        for key in (
            "ACTIONABLE_DB_PATH", "ACTIONABLE_RAG_ENABLE", "ACTIONABLE_INTER_CHUNK_DELAY_S",
            "ACTIONABLE_CHUNK_SIZE", "DECISIONS_DB_PATH", "DECISION_LEDGER_ENABLE",
            "DECISION_BACKEND",
        ):
            os.environ.pop(key, None)
        from backend import decision_ledger as dl

        dl._reset_singleton_for_tests()
        self._tmp.cleanup()

    def _run_scan(self, universe, fundamentals_mock, history_mock, **kwargs):
        with patch.object(svc, "get_universe", return_value=universe), \
             patch.object(svc, "fetch_fundamentals", side_effect=fundamentals_mock), \
             patch.object(svc, "fetch_chunk_history", side_effect=history_mock):
            return asyncio.run(svc.run_actionable_scan("job-test", **kwargs))

    def test_full_scan_persists_and_emits(self):
        universe = ["AAPL", "MSFT", "WEAK"]
        closes = {t: _uptrend_closes() for t in universe}

        def fundamentals(ticker):
            if ticker == "WEAK":
                return {"ticker": "WEAK"}  # insufficient coverage → skipped
            return _good_fundamentals(ticker)

        meta = self._run_scan(universe, fundamentals, lambda chunk: {t: closes[t] for t in chunk})

        self.assertEqual(meta["universe_size"], 3)
        self.assertEqual(meta["scored"], 2)
        self.assertEqual(meta["skipped"], 1)

        status = svc.get_job_status()
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["progress"], 100)
        self.assertFalse(status["cache_hit"])

        rows = svc.load_snapshot_rows(meta["snapshot_id"], actionable_only=False)
        self.assertEqual({r["ticker"] for r in rows}, {"AAPL", "MSFT"})
        for r in rows:
            self.assertIn(r["verdict"], svc.VERDICTS)
            self.assertIsNotNone(r["score"])

        # Decision-Outcome Ledger received the actionable verdicts
        from backend import decision_ledger as dl

        decisions = dl.get_ledger().list_decisions_since(0.0, decision_type="actionable_screen")
        self.assertGreaterEqual(len(decisions), 1)
        self.assertEqual(decisions[0].horizon_hint, "21d")
        self.assertEqual(
            decisions[0].source_route,
            "backend/actionable_companies.py::run_actionable_scan",
        )

    def test_second_run_within_hour_is_cache_hit(self):
        universe = ["AAPL"]
        closes = {"AAPL": _uptrend_closes()}
        fundamentals = MagicMock(side_effect=lambda t: _good_fundamentals(t))
        history = MagicMock(side_effect=lambda chunk: {t: closes[t] for t in chunk})

        first = self._run_scan(universe, fundamentals, history)
        calls_after_first = fundamentals.call_count
        self.assertEqual(calls_after_first, 1)

        second = self._run_scan(universe, fundamentals, history)
        self.assertEqual(second["snapshot_id"], first["snapshot_id"])  # reused snapshot
        self.assertEqual(fundamentals.call_count, calls_after_first)  # no re-fetch
        self.assertTrue(svc.get_job_status()["cache_hit"])

    def test_force_bypasses_snapshot_cache(self):
        universe = ["AAPL"]
        closes = {"AAPL": _uptrend_closes()}
        fundamentals = MagicMock(side_effect=lambda t: _good_fundamentals(t))
        history = MagicMock(side_effect=lambda chunk: {t: closes[t] for t in chunk})

        first = self._run_scan(universe, fundamentals, history)
        second = self._run_scan(universe, fundamentals, history, force=True)
        self.assertNotEqual(second["snapshot_id"], first["snapshot_id"])
        self.assertEqual(fundamentals.call_count, 2)


if __name__ == "__main__":
    unittest.main()
