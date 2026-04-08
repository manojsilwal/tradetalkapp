"""Phase B — chat evidence memo Markdown builder."""
import unittest

from backend.evidence_pack import build_chat_evidence_memo_markdown


class TestEvidencePack(unittest.TestCase):
    def test_memo_contains_disclaimer_and_contract(self):
        md = build_chat_evidence_memo_markdown(
            session_id="sess-12345678-abcd",
            user_message="What is MSFT price?",
            assistant_text="Here is the snapshot.",
            evidence_contract={
                "schema_version": 1,
                "sources_used": ["quote_card:MSFT"],
                "tools_called": [],
                "tool_outcomes": [],
                "confidence_band": "high",
                "abstain_reason": None,
            },
            meta={"rag_nonempty": False, "coral_hub_nonempty": False},
        )
        self.assertIn("TradeTalk", md)
        self.assertIn("Disclaimer", md)
        self.assertIn("MSFT", md)
        self.assertIn('"confidence_band": "high"', md)


if __name__ == "__main__":
    unittest.main()
