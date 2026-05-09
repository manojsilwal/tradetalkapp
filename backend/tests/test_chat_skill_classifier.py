"""Phase E1 — heuristic skill classifier tests."""
from __future__ import annotations

import unittest

from backend.chat_skill_classifier import classify_skill
from backend.chat_tool_family import SkillName, SkillTier, ToolFamily


class TestClassifySkillByKeywords(unittest.TestCase):
    def test_backtest_keyword_wins_over_family_set(self) -> None:
        skill, tier = classify_skill(
            user_message="Run a backtest of breakout_v1 on NVDA",
            tool_families_used=[ToolFamily.QUOTE],
        )
        self.assertEqual(skill, SkillName.WHAT_IF_BACKTEST)
        self.assertEqual(tier, SkillTier.DEEP_RESEARCH)

    def test_compare_routes_to_deep_research(self) -> None:
        skill, tier = classify_skill(
            user_message="Compare MSFT vs GOOGL for a 3-month trade",
            tool_families_used=[ToolFamily.QUOTE, ToolFamily.NEWS],
        )
        self.assertEqual(skill, SkillName.DEEP_RESEARCH)
        self.assertEqual(tier, SkillTier.DEEP_RESEARCH)

    def test_risk_keyword_routes_to_risk_check(self) -> None:
        skill, tier = classify_skill(
            user_message="What's the risk of a long XAUUSD position here?",
            tool_families_used=[ToolFamily.QUOTE],
        )
        self.assertEqual(skill, SkillName.RISK_CHECK)
        self.assertEqual(tier, SkillTier.FULL_CHAIN)


class TestClassifySkillByFamilySet(unittest.TestCase):
    def test_only_quote_routes_to_quick_quote(self) -> None:
        skill, tier = classify_skill(
            user_message="AAPL",
            tool_families_used=[ToolFamily.QUOTE],
        )
        self.assertEqual(skill, SkillName.QUICK_QUOTE)
        self.assertEqual(tier, SkillTier.SIMPLE)

    def test_quote_news_macro_routes_to_full_chain(self) -> None:
        skill, tier = classify_skill(
            user_message="should I worry about my position",
            tool_families_used=[ToolFamily.QUOTE, ToolFamily.NEWS, ToolFamily.MACRO],
        )
        self.assertEqual(skill, SkillName.FULL_CHAIN_ANALYSIS)
        self.assertEqual(tier, SkillTier.FULL_CHAIN)

    def test_empty_inputs_yield_unknown_skill(self) -> None:
        skill, tier = classify_skill(user_message="", tool_families_used=[])
        self.assertEqual(skill, SkillName.UNKNOWN)
        self.assertEqual(tier, SkillTier.SIMPLE)


if __name__ == "__main__":
    unittest.main()
