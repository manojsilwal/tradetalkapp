"""Unit tests for Layer 1 chat evidence contract (no live LLM)."""
import unittest

from backend.chat_evidence_contract import (
    SCHEMA_VERSION,
    build_evidence_contract,
    classify_tool_result,
)


class TestClassifyToolResult(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(classify_tool_result(""), "empty")

    def test_error_prefix(self):
        self.assertEqual(classify_tool_result("Error fetching quote: boom"), "error")

    def test_success_rich_quote(self):
        s = "**AAPL** — Full Quote Snapshot\n- Price: $200.00"
        self.assertEqual(classify_tool_result(s), "success")

    def test_empty_no_data(self):
        self.assertEqual(
            classify_tool_result("Ticker XYZ: No price data found."),
            "empty",
        )


class TestBuildEvidenceContract(unittest.TestCase):
    def test_schema_version_is_v4(self):
        c = build_evidence_contract(
            tool_trace=[],
            quote_card_tickers=[],
            meta={"rag_nonempty": False, "coral_hub_nonempty": False},
        )
        self.assertEqual(c["schema_version"], SCHEMA_VERSION)
        self.assertEqual(c["schema_version"], 4)

    def test_quote_card_high_confidence(self):
        c = build_evidence_contract(
            tool_trace=[],
            quote_card_tickers=["MSFT"],
            meta={"rag_nonempty": False, "coral_hub_nonempty": False},
        )
        self.assertEqual(c["confidence_band"], "high")
        self.assertIn("quote_card:MSFT", c["sources_used"])
        self.assertIsNone(c["abstain_reason"])

    def test_all_tools_bad_abstain(self):
        c = build_evidence_contract(
            tool_trace=[
                {"name": "get_stock_quote", "outcome": "empty"},
            ],
            quote_card_tickers=[],
            meta={"rag_nonempty": False, "coral_hub_nonempty": False},
        )
        self.assertEqual(c["confidence_band"], "low")
        self.assertEqual(c["abstain_reason"], "all_tools_empty_or_error")

    def test_rag_and_coral_sources(self):
        c = build_evidence_contract(
            tool_trace=[],
            quote_card_tickers=[],
            meta={"rag_nonempty": True, "coral_hub_nonempty": True},
        )
        self.assertIn("internal_kb", c["sources_used"])
        self.assertIn("coral_hub", c["sources_used"])


# ── Phase A2: B hard-gate fields and grounding placeholders ────────────────


class TestBuildEvidenceContractA2Fields(unittest.TestCase):
    def test_b_gate_fields_present_with_safe_defaults(self):
        c = build_evidence_contract(
            tool_trace=[],
            quote_card_tickers=[],
            meta={"rag_nonempty": False, "coral_hub_nonempty": False},
        )
        for key in (
            "trace_id",
            "tool_families_used",
            "trajectory_step_count",
            "valid_prefix_steps",
            "fatal_detected",
            "fatal_trigger_step_index",
            "fatal_streak_start_step_index",
        ):
            self.assertIn(key, c, msg=f"contract missing required A2 key: {key!r}")
        self.assertFalse(c["fatal_detected"])
        self.assertEqual(c["tool_families_used"], [])
        self.assertEqual(c["trajectory_step_count"], 0)
        self.assertEqual(c["valid_prefix_steps"], 0)
        self.assertIsNone(c["fatal_trigger_step_index"])

    def test_grounding_placeholders_present(self):
        c = build_evidence_contract(
            tool_trace=[],
            quote_card_tickers=[],
            meta={"rag_nonempty": False, "coral_hub_nonempty": False},
        )
        self.assertEqual(c["final_answer_evidence_refs"], [])
        self.assertIsNone(c["grounding_ratio"])
        self.assertIsNone(c["unsupported_claim_count"])

    def test_quote_card_credits_quote_family_without_summary(self):
        c = build_evidence_contract(
            tool_trace=[],
            quote_card_tickers=["NVDA"],
            meta={"rag_nonempty": False, "coral_hub_nonempty": False},
        )
        # Fallback path uses _families_from_tool_trace; quote-card prefetch
        # alone surfaces 'quote' even without a real tool call.
        self.assertIn("quote", c["tool_families_used"])

    def test_families_resolved_from_trace_when_summary_missing(self):
        # Legacy callers may pass a plain trace without ``tool_family`` keys.
        c = build_evidence_contract(
            tool_trace=[
                {"name": "get_stock_quote", "outcome": "success"},
                {"name": "get_market_news", "outcome": "success"},
            ],
            quote_card_tickers=[],
            meta={"rag_nonempty": False, "coral_hub_nonempty": False},
        )
        fams = c["tool_families_used"]
        self.assertIn("quote", fams)
        self.assertIn("news", fams)

    def test_summary_fields_propagate_when_supplied(self):
        summary = {
            "trace_id": "chat:s1:m1",
            "tool_families_used": ["quote", "news", "macro"],
            "fatal_detected": True,
            "fatal_trigger_step_index": 4,
            "fatal_streak_start_step_index": 2,
            "valid_prefix_steps": 2,
            "trajectory_step_count": 5,
        }
        c = build_evidence_contract(
            tool_trace=[],
            quote_card_tickers=[],
            meta={"rag_nonempty": False, "coral_hub_nonempty": False},
            trajectory_summary=summary,
        )
        self.assertEqual(c["trace_id"], "chat:s1:m1")
        self.assertEqual(c["tool_families_used"], ["quote", "news", "macro"])
        self.assertTrue(c["fatal_detected"])
        self.assertEqual(c["fatal_trigger_step_index"], 4)
        self.assertEqual(c["fatal_streak_start_step_index"], 2)
        self.assertEqual(c["valid_prefix_steps"], 2)
        self.assertEqual(c["trajectory_step_count"], 5)

    def test_trajectory_steps_optional_in_contract(self):
        c = build_evidence_contract(
            tool_trace=[],
            quote_card_tickers=[],
            meta={"rag_nonempty": False, "coral_hub_nonempty": False},
            trajectory_steps=[{"step_index": 0, "tool_name": "get_stock_quote"}],
        )
        self.assertIn("trajectory_steps", c)
        self.assertEqual(len(c["trajectory_steps"]), 1)


# ── Phase E0: skill + phase + namespace metadata ───────────────────────────


class TestBuildEvidenceContractE0Fields(unittest.TestCase):
    def test_e0_keys_present_with_safe_defaults(self):
        c = build_evidence_contract(
            tool_trace=[],
            quote_card_tickers=[],
            meta={"rag_nonempty": False, "coral_hub_nonempty": False},
        )
        for key in (
            "skill_name",
            "skill_tier",
            "expected_tool_families",
            "investigation_step_count",
            "synthesis_step_index",
            "answer_grounded_to_investigation",
            "memory_namespaces_touched",
            "artifact_types_used",
            "source_refs_v2",
        ):
            self.assertIn(key, c, msg=f"contract missing required E0 key: {key!r}")
        self.assertIsNone(c["skill_name"])
        self.assertIsNone(c["skill_tier"])
        self.assertEqual(c["expected_tool_families"], [])
        self.assertEqual(c["investigation_step_count"], 0)
        self.assertEqual(c["synthesis_step_index"], 0)
        self.assertFalse(c["answer_grounded_to_investigation"])
        self.assertEqual(c["memory_namespaces_touched"], [])
        self.assertEqual(c["artifact_types_used"], [])
        self.assertEqual(c["source_refs_v2"], [])

    def test_skill_metadata_propagates_with_expected_chain(self):
        summary = {
            "trace_id": "chat:s2:m2",
            "tool_families_used": ["quote", "news", "macro"],
            "fatal_detected": False,
            "fatal_trigger_step_index": None,
            "fatal_streak_start_step_index": None,
            "valid_prefix_steps": 3,
            "trajectory_step_count": 3,
            "investigation_step_count": 3,
            "synthesis_step_index": 3,
            "answer_grounded_to_investigation": True,
            "memory_namespaces_touched": ["market_data", "news_rag"],
            "artifact_types_used": ["market_quote", "news_article", "macro_data"],
            "source_refs_v2_all": [
                {"ref_id": "quote:NVDA", "source_family": "quote", "artifact_type": "market_quote"},
            ],
            "skill_name": "full_chain_analysis",
            "skill_tier": "full_chain",
        }
        c = build_evidence_contract(
            tool_trace=[],
            quote_card_tickers=[],
            meta={"rag_nonempty": False, "coral_hub_nonempty": False},
            trajectory_summary=summary,
        )
        self.assertEqual(c["skill_name"], "full_chain_analysis")
        self.assertEqual(c["skill_tier"], "full_chain")
        # Expected chain looked up from SKILL_EXPECTED_FAMILIES.
        self.assertIn("quote", c["expected_tool_families"])
        self.assertIn("news", c["expected_tool_families"])
        self.assertIn("macro", c["expected_tool_families"])
        self.assertEqual(c["investigation_step_count"], 3)
        self.assertEqual(c["synthesis_step_index"], 3)
        self.assertTrue(c["answer_grounded_to_investigation"])
        self.assertIn("market_data", c["memory_namespaces_touched"])
        self.assertIn("market_quote", c["artifact_types_used"])
        self.assertEqual(len(c["source_refs_v2"]), 1)


if __name__ == "__main__":
    unittest.main()
