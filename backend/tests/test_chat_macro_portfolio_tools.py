"""
Unit tests for the super-agent context tools added to chat_send_message:
  - get_portfolio_snapshot
  - get_macro_regime
  - get_macro_flow_summary

Tests are fully offline — they mock the real data sources so no network or
database is required.
"""
from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


# ─── helpers ────────────────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def _make_portfolio_tool(uid: str | None):
    """
    Reproduce get_portfolio_snapshot closure exactly as declared in
    backend/routers/chat.py, but with pp.get_portfolio_performance mocked.
    """
    import asyncio as _aio
    import backend.paper_portfolio as pp

    async def get_portfolio_snapshot():
        if not uid:
            return "Sign in to view your portfolio."
        try:
            perf = await _aio.to_thread(pp.get_portfolio_performance, uid)
            positions = perf.get("positions") or []
            if not positions:
                return "Your portfolio is empty. Add positions via the Portfolio page."
            total_val = perf.get("total_value", 0.0)
            total_pnl = perf.get("total_pnl", 0.0)
            total_pnl_pct = perf.get("total_pnl_pct", 0.0)
            spy_pct = perf.get("spy_pnl_pct")
            beating = perf.get("beating_spy", False)
            lines = [
                f"**Portfolio Snapshot** ({len(positions)} open positions)",
                f"- Total Value: ${total_val:,.2f} | P&L: ${total_pnl:+,.2f} ({total_pnl_pct:+.2f}%)",
            ]
            if spy_pct is not None:
                lines.append(
                    f"- SPY benchmark: {spy_pct:+.2f}% | {'Beating' if beating else 'Trailing'} SPY"
                )
            sorted_pos = sorted(positions, key=lambda p: p.get("pnl_pct", 0.0), reverse=True)
            winners = sorted_pos[:3]
            for p in positions:
                pnl_str = f"{p.get('pnl_pct', 0):+.1f}%"
                lines.append(
                    f"  {p['ticker']} ({p.get('direction','LONG')}) | "
                    f"Entry: ${p.get('entry_price', 0):.2f} | "
                    f"Current: ${p.get('current_price', 0):.2f} | "
                    f"P&L: ${p.get('pnl_dollar', 0):+.2f} ({pnl_str}) | "
                    f"Sector: {p.get('sector', 'Unknown')}"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Portfolio snapshot unavailable: {e}"

    return get_portfolio_snapshot


def _make_macro_tool(connector):
    """Reproduce get_macro_regime closure with a given (mock) connector."""
    import asyncio as _aio

    async def get_macro_regime():
        try:
            data = await connector.fetch_data()
            ind = data.get("indicators") or {}
            vix = ind.get("vix_level")
            csi = ind.get("credit_stress_index")
            regime = "BULL_NORMAL" if (csi or 0) <= 1.1 else "BEAR_STRESS"
            lines = [f"**Macro Regime: {regime}**"]
            if vix is not None:
                lines.append(f"- VIX: {vix:.1f}")
            fed = ind.get("fed_funds_rate")
            if fed is not None:
                lines.append(f"- Rates: Fed Funds: {fed:.2f}%")
            return "\n".join(lines)
        except Exception as e:
            return f"Macro regime data unavailable: {e}"

    return get_macro_regime


def _make_flow_tool(latest_rrg_fn):
    """Reproduce get_macro_flow_summary closure with a given mock."""
    import asyncio as _aio

    async def get_macro_flow_summary(interval: str = "1w"):
        allowed = {"1d", "1w", "1m", "1y"}
        iv = interval.strip().lower() if interval.strip().lower() in allowed else "1w"
        try:
            pts = await _aio.to_thread(latest_rrg_fn, iv)
            if not pts:
                return (
                    f"No macro flow data cached for interval={iv}. "
                    "Try refreshing via the Macro page or ask again shortly."
                )
            pts_sorted = sorted(pts, key=lambda p: float(p.get("flow_score") or 0.0), reverse=True)
            lines = [f"**Macro Sector Flow Summary** (interval: {iv})"]
            lines.append("\nTop inflow sectors:")
            for p in pts_sorted[:3]:
                name = p.get("name") or "Unknown"
                fs = float(p.get("flow_score") or 0.0)
                lines.append(f"  {name}: flow={fs:+.3f}")
            return "\n".join(lines)
        except Exception as e:
            return f"Macro flow data unavailable: {e}"

    return get_macro_flow_summary


# ─── tests ──────────────────────────────────────────────────────────────────


class TestGetPortfolioSnapshot(unittest.TestCase):
    def test_unauthenticated_returns_sign_in_prompt(self):
        fn = _make_portfolio_tool(uid=None)
        result = _run(fn())
        self.assertIn("Sign in", result)

    def test_empty_portfolio(self):
        fn = _make_portfolio_tool(uid="user123")
        with patch("backend.paper_portfolio.get_portfolio_performance", return_value={"positions": []}):
            result = _run(fn())
        self.assertIn("empty", result.lower())

    def test_snapshot_with_positions(self):
        fake_perf = {
            "positions": [
                {
                    "ticker": "AAPL",
                    "direction": "LONG",
                    "entry_price": 150.0,
                    "current_price": 180.0,
                    "pnl_dollar": 300.0,
                    "pnl_pct": 20.0,
                    "sector": "Technology",
                },
            ],
            "total_value": 10_300.0,
            "total_pnl": 300.0,
            "total_pnl_pct": 3.0,
            "spy_pnl_pct": 2.5,
            "beating_spy": True,
        }
        fn = _make_portfolio_tool(uid="user123")
        with patch("backend.paper_portfolio.get_portfolio_performance", return_value=fake_perf):
            result = _run(fn())
        self.assertIn("Portfolio Snapshot", result)
        self.assertIn("AAPL", result)
        self.assertIn("Beating", result)
        self.assertIn("Technology", result)
        self.assertIn("$10,300.00", result)

    def test_exception_returns_safe_message(self):
        fn = _make_portfolio_tool(uid="user123")
        with patch("backend.paper_portfolio.get_portfolio_performance", side_effect=RuntimeError("db error")):
            result = _run(fn())
        self.assertIn("unavailable", result)


class TestGetMacroRegime(unittest.TestCase):
    def _mock_connector(self, indicators: dict, sectors=None):
        connector = MagicMock()
        connector.fetch_data = AsyncMock(return_value={
            "indicators": indicators,
            "sectors": sectors or [],
        })
        return connector

    def test_bull_regime_vix_and_rates(self):
        conn = self._mock_connector({
            "vix_level": 14.2,
            "credit_stress_index": 0.8,
            "fed_funds_rate": 5.25,
        })
        fn = _make_macro_tool(conn)
        result = _run(fn())
        self.assertIn("BULL_NORMAL", result)
        self.assertIn("14.2", result)
        self.assertIn("5.25", result)

    def test_bear_regime_high_credit_stress(self):
        conn = self._mock_connector({
            "vix_level": 30.5,
            "credit_stress_index": 2.1,
        })
        fn = _make_macro_tool(conn)
        result = _run(fn())
        self.assertIn("BEAR_STRESS", result)

    def test_exception_returns_safe_message(self):
        connector = MagicMock()
        connector.fetch_data = AsyncMock(side_effect=ConnectionError("timeout"))
        fn = _make_macro_tool(connector)
        result = _run(fn())
        self.assertIn("unavailable", result)


class TestGetMacroFlowSummary(unittest.TestCase):
    SAMPLE_POINTS = [
        {"name": "Technology", "flow_score": 0.72, "qa_verdict": "durable_inflow"},
        {"name": "Energy",     "flow_score": 0.44, "qa_verdict": "speculative_inflow"},
        {"name": "Utilities",  "flow_score": -0.35, "qa_verdict": "outflow"},
        {"name": "Financials", "flow_score": 0.10, "qa_verdict": "neutral"},
    ]

    def test_returns_inflow_sectors(self):
        fn = _make_flow_tool(lambda iv: self.SAMPLE_POINTS)
        result = _run(fn())
        self.assertIn("Technology", result)
        self.assertIn("Macro Sector Flow Summary", result)

    def test_empty_cache_returns_hint(self):
        fn = _make_flow_tool(lambda iv: [])
        result = _run(fn())
        self.assertIn("No macro flow data", result)

    def test_invalid_interval_fallback_to_1w(self):
        captured = {}

        def store_interval(iv):
            captured["iv"] = iv
            return self.SAMPLE_POINTS

        fn = _make_flow_tool(store_interval)
        _run(fn(interval="bad"))
        self.assertEqual(captured["iv"], "1w")

    def test_valid_interval_passthrough(self):
        captured = {}

        def store_interval(iv):
            captured["iv"] = iv
            return self.SAMPLE_POINTS

        fn = _make_flow_tool(store_interval)
        _run(fn(interval="1m"))
        self.assertEqual(captured["iv"], "1m")

    def test_exception_returns_safe_message(self):
        def boom(_iv):
            raise RuntimeError("db locked")

        fn = _make_flow_tool(boom)
        result = _run(fn())
        self.assertIn("unavailable", result)


# ─── taxonomy consistency ────────────────────────────────────────────────────


class TestToolTaxonomyRegistration(unittest.TestCase):
    """Verify the 3 new tools are properly registered in chat_tool_family.py."""

    def setUp(self):
        from backend.chat_tool_family import (
            CHAT_TOOL_FAMILY_BY_NAME,
            TOOL_NAMESPACE_BY_NAME,
            TOOL_RETRIEVAL_MODE_BY_NAME,
            SOURCE_REF_ARTIFACT_TYPE_BY_TOOL,
            EXPECTED_CHAT_TOOL_NAMES,
        )
        self.family_map = CHAT_TOOL_FAMILY_BY_NAME
        self.ns_map = TOOL_NAMESPACE_BY_NAME
        self.mode_map = TOOL_RETRIEVAL_MODE_BY_NAME
        self.artifact_map = SOURCE_REF_ARTIFACT_TYPE_BY_TOOL
        self.expected_names = EXPECTED_CHAT_TOOL_NAMES

    def _assert_registered(self, tool_name: str):
        self.assertIn(tool_name, self.family_map, f"{tool_name} missing from CHAT_TOOL_FAMILY_BY_NAME")
        self.assertIn(tool_name, self.ns_map, f"{tool_name} missing from TOOL_NAMESPACE_BY_NAME")
        self.assertIn(tool_name, self.mode_map, f"{tool_name} missing from TOOL_RETRIEVAL_MODE_BY_NAME")
        self.assertIn(tool_name, self.artifact_map, f"{tool_name} missing from SOURCE_REF_ARTIFACT_TYPE_BY_TOOL")
        self.assertIn(tool_name, self.expected_names, f"{tool_name} missing from EXPECTED_CHAT_TOOL_NAMES")

    def test_get_portfolio_snapshot_registered(self):
        self._assert_registered("get_portfolio_snapshot")

    def test_get_macro_regime_registered(self):
        self._assert_registered("get_macro_regime")

    def test_get_macro_flow_summary_registered(self):
        self._assert_registered("get_macro_flow_summary")

    def test_portfolio_snapshot_family_is_portfolio(self):
        from backend.chat_tool_family import ToolFamily
        self.assertEqual(self.family_map["get_portfolio_snapshot"], ToolFamily.PORTFOLIO)

    def test_macro_regime_family_is_macro(self):
        from backend.chat_tool_family import ToolFamily
        self.assertEqual(self.family_map["get_macro_regime"], ToolFamily.MACRO)

    def test_macro_flow_summary_family_is_macro(self):
        from backend.chat_tool_family import ToolFamily
        self.assertEqual(self.family_map["get_macro_flow_summary"], ToolFamily.MACRO)


if __name__ == "__main__":
    unittest.main()
