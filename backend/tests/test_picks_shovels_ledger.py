"""Offline test: Picks & Shovels emits to the Decision-Outcome Ledger (AGENTS.md rule)."""
from __future__ import annotations

import os
import tempfile
import unittest

from backend.picks_shovels import ledger as ps_ledger


def _row(ticker, score):
    return {
        "ticker": ticker,
        "company_name": f"{ticker} Inc",
        "final_score": score,
        "themes": ["memory_hbm"],
        "score_breakdown": {
            "price_momentum_score": 80.0,
            "revenue_acceleration_score": 75.0,
            "margin_expansion_score": 70.0,
            "valuation_risk_score": 70.0,
        },
        "coverage": 1.0,
        "hiddenness_level": "Big Player",
        "confidence_level": "High",
        "confidence_score": 90.0,
        "explanation": {"why_selected": "ranks highly in Memory / HBM"},
    }


class TestPicksShovelsLedger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["DECISIONS_DB_PATH"] = os.path.join(self._tmp.name, "decisions.db")
        os.environ["DECISION_LEDGER_ENABLE"] = "1"
        os.environ["DECISION_BACKEND"] = "sqlite"
        os.environ["PICKS_SHOVELS_RAG_ENABLE"] = "0"
        from backend import decision_ledger as dl

        dl._reset_singleton_for_tests()

    def tearDown(self):
        for key in ("DECISIONS_DB_PATH", "DECISION_LEDGER_ENABLE", "DECISION_BACKEND",
                    "PICKS_SHOVELS_RAG_ENABLE"):
            os.environ.pop(key, None)
        from backend import decision_ledger as dl

        dl._reset_singleton_for_tests()
        self._tmp.cleanup()

    def test_emit_writes_decisions(self):
        rows = [_row("MU", 88.0), _row("NVDA", 95.0), _row("COHR", 80.0)]
        emitted = ps_ledger.emit_decisions(rows, "snap-test")
        self.assertEqual(emitted, 3)

        from backend import decision_ledger as dl

        decisions = dl.get_ledger().list_decisions_since(0.0, decision_type="picks_shovels_momentum")
        self.assertEqual(len(decisions), 3)
        d = decisions[0]
        self.assertEqual(d.horizon_hint, "21d")
        self.assertEqual(d.source_route, "backend/picks_shovels/engine.py::run_scan")
        self.assertIn(d.symbol, {"MU", "NVDA", "COHR"})

    def test_top_n_limit(self):
        os.environ["PICKS_SHOVELS_LEDGER_TOP_N"] = "1"
        try:
            rows = [_row("MU", 88.0), _row("NVDA", 95.0)]
            emitted = ps_ledger.emit_decisions(rows, "snap-test")
            self.assertEqual(emitted, 1)
            from backend import decision_ledger as dl

            decisions = dl.get_ledger().list_decisions_since(0.0, decision_type="picks_shovels_momentum")
            self.assertEqual(decisions[0].symbol, "NVDA")  # highest score
        finally:
            os.environ.pop("PICKS_SHOVELS_LEDGER_TOP_N", None)


if __name__ == "__main__":
    unittest.main()
