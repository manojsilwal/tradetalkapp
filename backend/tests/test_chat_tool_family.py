"""
Phase A0 — canonical chat ToolFamily registry tests.

Locks ``chat_tool_family`` against the live ``tool_handlers`` declared in
``backend/routers/chat.py`` and asserts the registration rules from the plan
(no orphan handler keys, every name resolves to a non-UNKNOWN family, source
refs are non-empty bounded strings).
"""
import unittest

from backend.chat_tool_family import (
    CHAT_TOOL_FAMILY_BY_NAME,
    EXPECTED_CHAT_TOOL_NAMES,
    MemoryNamespace,
    SKILL_EXPECTED_FAMILIES,
    SKILL_TIER_BY_NAME,
    SOURCE_REF_ARTIFACT_TYPE_BY_TOOL,
    SkillName,
    SkillTier,
    StepPhase,
    TOOL_NAMESPACE_BY_NAME,
    TOOL_RETRIEVAL_MODE_BY_NAME,
    ToolFamily,
    expected_families_for_skill,
    find_orphan_tool_names,
    get_artifact_type,
    get_tool_family,
    get_tool_namespace,
    get_tool_retrieval_mode,
    make_source_refs,
    make_source_refs_v2,
    tier_for_skill,
)
from backend.routers.chat import CHAT_TOOL_NAMES


class TestToolFamilyRegistry(unittest.TestCase):
    def test_registry_matches_chat_handlers(self) -> None:
        # Renaming or adding a chat tool must update both surfaces.
        self.assertEqual(CHAT_TOOL_NAMES, EXPECTED_CHAT_TOOL_NAMES)

    def test_no_orphan_handlers(self) -> None:
        self.assertEqual(find_orphan_tool_names(CHAT_TOOL_NAMES), [])

    def test_every_handler_has_known_family(self) -> None:
        for name in CHAT_TOOL_NAMES:
            fam = get_tool_family(name)
            self.assertIsInstance(fam, ToolFamily)
            self.assertNotEqual(
                fam,
                ToolFamily.UNKNOWN,
                msg=f"chat tool {name!r} resolved to UNKNOWN family",
            )

    def test_unknown_name_returns_unknown(self) -> None:
        self.assertEqual(get_tool_family("nonexistent_tool"), ToolFamily.UNKNOWN)
        self.assertEqual(get_tool_family(""), ToolFamily.UNKNOWN)

    def test_family_enum_string_values_are_unique(self) -> None:
        values = [m.value for m in ToolFamily]
        self.assertEqual(len(values), len(set(values)))


class TestMakeSourceRefs(unittest.TestCase):
    def test_quote_with_ticker(self) -> None:
        refs = make_source_refs("get_stock_quote", {"ticker": "aapl"})
        self.assertEqual(refs, ["quote:AAPL"])

    def test_quote_without_ticker(self) -> None:
        refs = make_source_refs("get_stock_quote", {})
        self.assertEqual(refs, ["quote"])

    def test_price_history_includes_period(self) -> None:
        refs = make_source_refs(
            "get_price_history", {"ticker": "msft", "period": "1y"}
        )
        self.assertEqual(refs, ["chart:MSFT:1y"])

    def test_top_movers_uses_direction(self) -> None:
        refs = make_source_refs("get_top_movers", {"direction": "losers"})
        self.assertEqual(refs, ["screener:losers"])

    def test_news_default_query(self) -> None:
        refs = make_source_refs("get_market_news", {})
        self.assertEqual(refs, ["news:market"])

    def test_news_with_query(self) -> None:
        refs = make_source_refs("get_market_news", {"query": "iran oil"})
        self.assertEqual(refs, ["news:iran oil"])

    def test_filing_default_form(self) -> None:
        refs = make_source_refs("get_sec_filing", {"ticker": "tsla"})
        self.assertEqual(refs, ["filing:TSLA:10-K"])

    def test_scrape_url_uses_host(self) -> None:
        refs = make_source_refs(
            "scrape_url", {"url": "https://example.com/path?q=1"}
        )
        self.assertEqual(refs, ["web_search:example.com"])

    def test_unknown_tool_returns_family_default(self) -> None:
        refs = make_source_refs("not_a_tool", {})
        self.assertEqual(refs, ["unknown"])

    def test_risk_tool_ref(self) -> None:
        refs = make_source_refs("get_risk_assessment", {"ticker": "aapl"})
        self.assertEqual(refs, ["risk:AAPL"])

    def test_backtest_tool_ref(self) -> None:
        refs = make_source_refs("run_what_if_backtest", {"preset_id": "breakout_v1"})
        self.assertEqual(refs, ["backtest:breakout_v1"])

    def test_pattern_match_tool_ref(self) -> None:
        refs = make_source_refs(
            "find_similar_setups", {"ticker": "nvda", "lookback_bars": 5}
        )
        self.assertEqual(refs, ["market_data:NVDA:5"])


class TestMakeSourceRefsV2(unittest.TestCase):
    def test_typed_quote_ref(self) -> None:
        refs = make_source_refs_v2("get_stock_quote", {"ticker": "aapl"})
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["ref_id"], "quote:AAPL")
        self.assertEqual(refs[0]["source_family"], "quote")
        self.assertEqual(refs[0]["artifact_type"], "market_quote")

    def test_typed_news_ref(self) -> None:
        refs = make_source_refs_v2("get_market_news", {"query": "fed"})
        self.assertEqual(refs[0]["source_family"], "news")
        self.assertEqual(refs[0]["artifact_type"], "news_article")

    def test_typed_filing_ref(self) -> None:
        refs = make_source_refs_v2("get_sec_filing", {"ticker": "AAPL"})
        self.assertEqual(refs[0]["source_family"], "filing")
        self.assertEqual(refs[0]["artifact_type"], "filing_excerpt")

    def test_typed_risk_ref(self) -> None:
        refs = make_source_refs_v2("get_risk_assessment", {"ticker": "AAPL"})
        self.assertEqual(refs[0]["source_family"], "risk")
        self.assertEqual(refs[0]["artifact_type"], "risk_assessment")

    def test_typed_backtest_ref(self) -> None:
        refs = make_source_refs_v2("run_what_if_backtest", {"preset_id": "breakout_v1"})
        self.assertEqual(refs[0]["source_family"], "backtest")
        self.assertEqual(refs[0]["artifact_type"], "backtest_summary")

    def test_typed_pattern_ref(self) -> None:
        refs = make_source_refs_v2(
            "find_similar_setups", {"ticker": "NVDA", "lookback_bars": 5}
        )
        self.assertEqual(refs[0]["source_family"], "market_data")
        self.assertEqual(refs[0]["artifact_type"], "pattern_match")


class TestSkillRegistry(unittest.TestCase):
    def test_skill_name_enum_values_are_unique(self) -> None:
        values = [m.value for m in SkillName]
        self.assertEqual(len(values), len(set(values)))

    def test_skill_tier_enum_values_are_unique(self) -> None:
        values = [m.value for m in SkillTier]
        self.assertEqual(len(values), len(set(values)))

    def test_every_skill_has_tier(self) -> None:
        for name in SkillName:
            self.assertIn(name, SKILL_TIER_BY_NAME, msg=f"missing tier for {name}")

    def test_every_skill_has_expected_chain(self) -> None:
        for name in SkillName:
            self.assertIn(name, SKILL_EXPECTED_FAMILIES)
            chain = SKILL_EXPECTED_FAMILIES[name]
            for fam in chain:
                self.assertIsInstance(fam, ToolFamily)

    def test_expected_families_for_skill_returns_strings(self) -> None:
        chain = expected_families_for_skill(SkillName.FULL_CHAIN_ANALYSIS)
        self.assertIn("quote", chain)
        self.assertIn("news", chain)
        self.assertIn("macro", chain)

    def test_tier_for_skill_resolves(self) -> None:
        self.assertEqual(tier_for_skill(SkillName.QUICK_QUOTE), SkillTier.SIMPLE)
        self.assertEqual(
            tier_for_skill(SkillName.WHAT_IF_BACKTEST), SkillTier.DEEP_RESEARCH
        )
        self.assertEqual(tier_for_skill(SkillName.UNKNOWN), SkillTier.UNKNOWN)


class TestNamespaceMaps(unittest.TestCase):
    def test_every_chat_tool_has_namespace(self) -> None:
        for name in CHAT_TOOL_NAMES:
            self.assertIn(name, TOOL_NAMESPACE_BY_NAME, msg=f"namespace missing for {name}")

    def test_every_chat_tool_has_retrieval_mode(self) -> None:
        for name in CHAT_TOOL_NAMES:
            self.assertIn(name, TOOL_RETRIEVAL_MODE_BY_NAME, msg=f"retrieval mode missing for {name}")

    def test_every_chat_tool_has_artifact_type(self) -> None:
        for name in CHAT_TOOL_NAMES:
            self.assertIn(name, SOURCE_REF_ARTIFACT_TYPE_BY_TOOL)

    def test_get_tool_namespace_returns_enum(self) -> None:
        self.assertIsInstance(get_tool_namespace("get_stock_quote"), MemoryNamespace)
        self.assertEqual(
            get_tool_namespace("get_stock_quote"), MemoryNamespace.MARKET_DATA
        )
        self.assertEqual(
            get_tool_namespace("get_market_news"), MemoryNamespace.NEWS_RAG
        )

    def test_get_tool_retrieval_mode_default(self) -> None:
        self.assertEqual(get_tool_retrieval_mode("nonexistent"), "live_fetch")

    def test_step_phase_enum(self) -> None:
        self.assertEqual(StepPhase.INVESTIGATION.value, "investigation")
        self.assertEqual(StepPhase.SYNTHESIS.value, "synthesis")


if __name__ == "__main__":
    unittest.main()
