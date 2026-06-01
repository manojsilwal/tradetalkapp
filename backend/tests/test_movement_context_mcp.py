"""Offline tests for movement context MCP tool shape."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch


class TestMovementContextMCP(unittest.TestCase):
    def test_get_movement_context_reads_movement_context_daily(self):
        mock_row = {
            "symbol": "AAPL",
            "trade_date": "2024-02-15",
            "close": 185.0,
            "volume": 50000000,
            "daily_return_pct": 0.5,
            "return_zscore_60d": 0.2,
            "relative_volume": 1.1,
            "market_regime": "neutral",
            "same_day_events_json": [],
            "lagged_events_json": [],
            "macro_events_json": [
                {
                    "event_id": "abc",
                    "category": "macro_data",
                    "headline": "CPI release",
                    "attribution_weight": 1.0,
                    "lag_days": 2,
                    "published_at": "2024-02-13 13:30:00+00",
                }
            ],
            "linked_events_json": [],
            "catalyst_status": "macro_only",
            "primary_cause_category": "macro_data",
            "primary_cause_headline": "CPI release",
            "primary_cause_weight": 1.0,
            "spx_return": -0.5,
            "risk_regime": "neutral",
        }

        with patch("backend.mcp_server.tools.backend") as mock_backend:
            mock_backend.return_value.query.side_effect = [
                [mock_row],
            ]
            with patch("backend.mcp_server.tools.get_gold_spx_context") as mock_gold:
                mock_gold.return_value = {"available": True, "risk_regime": "neutral"}
                from backend.mcp_server.tools import get_movement_context

                ctx = get_movement_context("AAPL", "2024-02-15")

        self.assertEqual(ctx["symbol"], "AAPL")
        self.assertEqual(ctx["catalyst_status"], "macro_only")
        self.assertEqual(ctx["primary_cause"]["category"], "macro_data")
        self.assertEqual(len(ctx["macro_events"]), 1)
        self.assertIn("gold_context", ctx)

    def test_build_movement_links_sql_has_all_days_scope(self):
        from backend.mcp_server.build_movement_links import MOVEMENT_LINKS_SQL

        self.assertIn("all_days", MOVEMENT_LINKS_SQL)
        self.assertIn("daily_prices", MOVEMENT_LINKS_SQL)

    def test_macro_policy_builds_events(self):
        from backend.data_lake.ingest_macro_policy import build_macro_events

        events = build_macro_events(start="2024-01-01", end="2024-12-31")
        categories = {e["category"] for e in events}
        self.assertIn("fed_decision", categories)
        self.assertIn("macro_data", categories)
        self.assertTrue(all("published_at" in e for e in events))


if __name__ == "__main__":
    unittest.main()
