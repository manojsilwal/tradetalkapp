"""Unit tests for daily brief heuristics."""
from __future__ import annotations

import unittest

from backend.daily_brief import _value_spike_override, heuristic_verdict


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
            "daily_return_pct": 5.0,
            "catalyst_status": "symbol_specific",
            "primary_cause_headline": "Earnings beat",
            "primary_cause_category": "earnings",
            "return_zscore_60d": 1.5,
        }
        out = heuristic_verdict(row, "gainer")
        self.assertEqual(out["verdict"], "Strong Buy")

    def test_loser_oversold_buy(self):
        row = {
            "daily_return_pct": -4.0,
            "catalyst_status": "no_catalyst",
            "primary_cause_headline": "",
            "return_zscore_60d": -2.5,
        }
        out = heuristic_verdict(row, "loser")
        self.assertEqual(out["verdict"], "Buy")


if __name__ == "__main__":
    unittest.main()
