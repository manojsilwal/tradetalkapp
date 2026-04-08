"""Unit tests for Layer 1 chat evidence contract (no live LLM)."""
import unittest

from backend.chat_evidence_contract import (
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


if __name__ == "__main__":
    unittest.main()
