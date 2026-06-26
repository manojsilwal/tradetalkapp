"""End-to-end engine test with mocked fetchers + temp DBs (offline)."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from backend.picks_shovels import data as ps_data
from backend.picks_shovels import engine as ps_engine
from backend.picks_shovels import store as ps_store


def _uptrend_closes(n=260, start=100.0, slope=0.002):
    return [start * (1.0 + slope * i) for i in range(n)]


def _fund(ticker, *, rev=20.0, gm=45.0, mcap=5e10):
    return {
        "ticker": ticker, "company_name": f"{ticker} Inc", "sector": "Technology",
        "industry": "Semis", "market_cap": mcap, "current_price": 100.0,
        "revenue_growth_pct": rev, "earnings_growth_pct": rev,
        "gross_margin_pct": gm, "operating_margin_pct": gm / 2.0,
        "fcf_yield_pct": 3.0, "forward_pe": 25.0, "debt_to_equity": 0.5,
    }


class TestEngine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["PICKS_SHOVELS_DB_PATH"] = os.path.join(self._tmp.name, "ps.db")
        os.environ["PICKS_SHOVELS_RAG_ENABLE"] = "0"
        os.environ["PICKS_SHOVELS_INTER_CHUNK_DELAY_S"] = "0"
        os.environ["PICKS_SHOVELS_CHUNK_SIZE"] = "2"
        os.environ["DECISIONS_DB_PATH"] = os.path.join(self._tmp.name, "decisions.db")
        os.environ["DECISION_LEDGER_ENABLE"] = "1"
        os.environ["DECISION_BACKEND"] = "sqlite"
        from backend import decision_ledger as dl

        dl._reset_singleton_for_tests()
        ps_engine._set_job(
            job_id=None, status="idle", progress=0, message="", processed=0,
            total=0, snapshot_id=None, cache_hit=False, error=None,
        )

    def tearDown(self):
        for key in ("PICKS_SHOVELS_DB_PATH", "PICKS_SHOVELS_RAG_ENABLE",
                    "PICKS_SHOVELS_INTER_CHUNK_DELAY_S", "PICKS_SHOVELS_CHUNK_SIZE",
                    "DECISIONS_DB_PATH", "DECISION_LEDGER_ENABLE", "DECISION_BACKEND"):
            os.environ.pop(key, None)
        from backend import decision_ledger as dl

        dl._reset_singleton_for_tests()
        self._tmp.cleanup()

    def _run(self, universe, fundamentals, history, **kwargs):
        with patch.object(ps_engine, "get_universe", return_value=universe), \
             patch.object(ps_data, "fetch_fundamentals_extended", side_effect=fundamentals), \
             patch.object(ps_data, "fetch_price_series", side_effect=history):
            return asyncio.run(ps_engine.run_scan("job-test", **kwargs))

    def test_full_scan_persists_ranks_and_emits(self):
        universe = ["NVDA", "MU", "COHR", "WEAK"]
        closes = {
            "NVDA": _uptrend_closes(slope=0.004),
            "MU": _uptrend_closes(slope=0.002),
            "COHR": _uptrend_closes(slope=0.001),
            "WEAK": [],  # no price history → insufficient → skipped
        }

        def fundamentals(t):
            if t == "WEAK":
                return {"ticker": "WEAK", "company_name": "WEAK"}
            return _fund(t, rev={"NVDA": 50.0, "MU": 30.0, "COHR": 10.0}[t],
                         gm={"NVDA": 70.0, "MU": 50.0, "COHR": 35.0}[t],
                         mcap={"NVDA": 2e12, "MU": 1e11, "COHR": 8e9}[t])

        meta = self._run(universe, fundamentals, lambda chunk: {t: closes[t] for t in chunk})

        self.assertEqual(meta["universe_size"], 4)
        self.assertEqual(meta["scored"], 3)
        self.assertEqual(meta["skipped"], 1)

        status = ps_engine.get_job_status()
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["progress"], 100)

        rows = ps_store.load_snapshot_rows(meta["snapshot_id"], limit=10)
        self.assertEqual([r["ticker"] for r in rows][0], "NVDA")  # strongest ranks first
        self.assertEqual({r["ticker"] for r in rows}, {"NVDA", "MU", "COHR"})
        for r in rows:
            self.assertIsNotNone(r["final_score"])
            self.assertIn("explanation", r)
            self.assertGreaterEqual(len(r["risks"]), 1)

        from backend import decision_ledger as dl

        decisions = dl.get_ledger().list_decisions_since(0.0, decision_type="picks_shovels_momentum")
        self.assertGreaterEqual(len(decisions), 1)
        self.assertEqual(decisions[0].horizon_hint, "21d")

    def test_second_run_is_cache_hit(self):
        universe = ["MU"]
        closes = {"MU": _uptrend_closes()}
        fundamentals = MagicMock(side_effect=lambda t: _fund(t))
        history = MagicMock(side_effect=lambda chunk: {t: closes[t] for t in chunk})

        first = self._run(universe, fundamentals, history)
        calls = fundamentals.call_count
        second = self._run(universe, fundamentals, history)
        self.assertEqual(second["snapshot_id"], first["snapshot_id"])
        self.assertEqual(fundamentals.call_count, calls)  # no re-fetch
        self.assertTrue(ps_engine.get_job_status()["cache_hit"])

    def test_force_bypasses_cache(self):
        universe = ["MU"]
        closes = {"MU": _uptrend_closes()}
        fundamentals = MagicMock(side_effect=lambda t: _fund(t))
        history = MagicMock(side_effect=lambda chunk: {t: closes[t] for t in chunk})

        first = self._run(universe, fundamentals, history)
        second = self._run(universe, fundamentals, history, force=True)
        self.assertNotEqual(second["snapshot_id"], first["snapshot_id"])


if __name__ == "__main__":
    unittest.main()
