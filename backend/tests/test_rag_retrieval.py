"""RAG retrieval planning for chat (metadata filters + extra collections)."""
import unittest

from backend.rag_retrieval import plan_chat_rag, resolve_active_ticker


class TestPlanChatRag(unittest.TestCase):
    def test_ticker_from_message_overrides_sticky(self):
        t = resolve_active_ticker("What about MSFT vs AAPL?", {"active_ticker": "NVDA"})
        self.assertEqual(t, "AAPL")

    def test_sticky_when_no_ticker_in_message(self):
        t = resolve_active_ticker("How is the outlook?", {"active_ticker": "TSLA"})
        self.assertEqual(t, "TSLA")

    def test_plan_includes_filtered_collections_when_ticker(self):
        plan = plan_chat_rag(
            "AAPL fundamentals",
            {"active_ticker": "AAPL"},
            oversample=6,
            extra_n=4,
        )
        names = {q.collection for q in plan.queries}
        self.assertIn("debate_history", names)
        self.assertIn("stock_profiles", names)
        self.assertIn("swarm_history", names)
        dh = next(q for q in plan.queries if q.collection == "debate_history")
        self.assertEqual(dh.where, {"ticker": "AAPL"})

    def test_plan_macro_unfiltered(self):
        plan = plan_chat_rag("fed rates and inflation", None)
        macro = next(q for q in plan.queries if q.collection == "macro_snapshots")
        self.assertIsNone(macro.where)

    def test_earnings_route_sets_ticker(self):
        plan = plan_chat_rag("AAPL earnings beat last quarter", {"active_ticker": "AAPL"})
        self.assertEqual(plan.earnings_ticker, "AAPL")


if __name__ == "__main__":
    unittest.main()
