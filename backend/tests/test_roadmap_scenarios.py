"""Roadmap scenario sanitization and extrapolation caps."""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.decision_terminal import (
    _build_roadmap_panel,
    _heuristic_roadmap,
    _roadmap_predictor_budget_s,
    _sanitize_roadmap_scenarios,
)
from backend.predictor.scenarios import extrapolate_geometric_3y


class TestRoadmapPredictorBudget(unittest.IsolatedAsyncioTestCase):
    async def test_predictor_timeout_falls_back_to_heuristic(self):
        async def slow_forecast(*_args, **_kwargs):
            await asyncio.sleep(5.0)
            return MagicMock(status="ok", base_price_usd_3y_scenario=999.0)

        with patch.dict(os.environ, {"ROADMAP_PREDICTOR_BUDGET_S": "0.05"}, clear=False):
            with patch(
                "backend.brain.flags.brain_surface_enabled",
                return_value=False,
            ):
                with patch(
                    "backend.predictor.agent.run_predictor_forecast",
                    new=AsyncMock(side_effect=slow_forecast),
                ):
                    panel = await _build_roadmap_panel(
                        "NVDA",
                        200.0,
                        hist_cagr=50.0,
                        tool_registry=MagicMock(),
                    )

        self.assertTrue(panel.used_heuristic_fallback)
        self.assertIsNotNone(panel.base_price_usd)

    def test_budget_env_parses(self):
        with patch.dict(os.environ, {"ROADMAP_PREDICTOR_BUDGET_S": "12"}, clear=False):
            self.assertEqual(_roadmap_predictor_budget_s(), 12.0)


class TestRoadmapSanitize(unittest.TestCase):
    def test_misscaled_llm_prices_discarded_not_reanchored(self):
        spot = 315.0
        bull, base, bear = _sanitize_roadmap_scenarios(spot, 42.0, 20.0, 9.0)
        self.assertIsNone(bull)
        self.assertIsNone(base)
        self.assertIsNone(bear)

    def test_negative_cagr_heuristic_discarded_when_misscaled(self):
        u, b, e, cagr, asm = _heuristic_roadmap(100.0, hist_cagr_3y=-25.0)
        self.assertIsNone(u)
        self.assertIsNone(b)
        self.assertIsNone(e)
        self.assertIsNone(cagr)
        self.assertTrue(asm)

    def test_predictor_extrapolation_capped(self):
        spot = 200.0
        wild = extrapolate_geometric_3y(spot, 63, spot * 4.0)
        self.assertIsNotNone(wild)
        self.assertLessEqual(wild, spot * 2.75)
        self.assertGreaterEqual(wild, spot * 0.35)


if __name__ == "__main__":
    unittest.main()
