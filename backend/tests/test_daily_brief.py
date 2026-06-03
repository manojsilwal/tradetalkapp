"""Unit tests for daily brief heuristics and snapshot helpers."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.daily_brief import (
    VERDICT_ORDER,
    _payload_from_rows,
    _value_spike_override,
    apply_deep_verdicts,
    heuristic_verdict,
    persist_snapshot,
)


class TestDailyBriefHeuristics(unittest.TestCase):
    def test_value_spike_override_hold(self):
        self.assertTrue(
            _value_spike_override(
                "gainer",
                "news",
                "Company announces strategic acquisition deal",
                5.0,
            )
        )
        row = {
            "daily_return_pct": 5.0,
            "catalyst_status": "symbol_specific",
            "primary_cause_headline": "Strategic acquisition announced",
            "primary_cause_category": "news",
            "return_zscore_60d": 2.0,
        }
        out = heuristic_verdict(row, "gainer")
        self.assertEqual(out["verdict"], "Hold")
        self.assertEqual(out.get("adjustment_note"), "value_spike_override")

    def test_gainer_strong_buy(self):
        row = {
            "daily_return_pct": 9.68,
            "catalyst_status": "symbol_specific",
            "primary_cause_headline": "CRM SEC 8-K — 2026-06-01",
            "primary_cause_category": "sec_filing",
            "return_zscore_60d": 3.09,
            "relative_volume": 1.85,
        }
        out = heuristic_verdict(row, "gainer")
        self.assertEqual(out["verdict"], "Strong Buy")
        self.assertNotIn("SEC 8-K", out["one_line_reason"])
        self.assertIn("catalyst", out["one_line_reason"].lower())

    def test_gainer_uses_substantive_earnings_headline(self):
        row = {
            "daily_return_pct": 5.0,
            "catalyst_status": "symbol_specific",
            "primary_cause_headline": "Earnings beat on revenue and EPS",
            "primary_cause_category": "earnings",
            "return_zscore_60d": 1.5,
        }
        out = heuristic_verdict(row, "gainer")
        self.assertIn("Earnings beat", out["one_line_reason"])

    def test_loser_oversold_buy(self):
        row = {
            "daily_return_pct": -4.0,
            "catalyst_status": "no_catalyst",
            "primary_cause_headline": "",
            "return_zscore_60d": -2.5,
        }
        out = heuristic_verdict(row, "loser")
        self.assertEqual(out["verdict"], "Buy")

    def test_payload_from_rows(self):
        rows = [
            {"bucket": "loser", "symbol": "AAA", "rank": 1, "is_compelling": True},
            {"bucket": "gainer", "symbol": "BBB", "rank": 1},
        ]
        payload = _payload_from_rows(rows, __import__("datetime").date(2026, 5, 29), "test")
        self.assertEqual(len(payload["losers"]), 1)
        self.assertEqual(len(payload["gainers"]), 1)
        self.assertEqual(payload["verdict_tier"], "heuristic")

    @patch("backend.daily_brief._backend_type", return_value="none")
    def test_persist_snapshot_skips_without_bq(self, _mock):
        n = persist_snapshot({"trade_date": "2026-05-29", "rows": [{"symbol": "X"}]})
        self.assertEqual(n, 0)

    def test_apply_deep_verdicts(self):
        rows = [{"symbol": "AAPL", "verdict": "Hold", "bucket": "loser"}]
        llm = MagicMock()
        llm.generate_daily_brief_batch = AsyncMock(
            return_value=[{"symbol": "AAPL", "verdict": "Buy", "one_line_reason": "Oversold"}]
        )

        import asyncio

        out = asyncio.run(apply_deep_verdicts(rows, llm))
        self.assertEqual(out[0]["verdict"], "Buy")
        self.assertEqual(out[0]["verdict_tier"], "deep")
        self.assertIn("Buy", VERDICT_ORDER)


if __name__ == "__main__":
    unittest.main()
