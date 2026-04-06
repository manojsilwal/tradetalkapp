"""Tests for chat mover-query detection (anti-hallucination pre-injection)."""
import unittest

from backend.routers.chat import _mover_query_intent


class TestMoverQueryIntent(unittest.TestCase):
    def test_losers_phrases(self):
        self.assertEqual(_mover_query_intent("What are the top losers today?"), "losers")
        self.assertEqual(_mover_query_intent("biggest decliners in the S&P 500"), "losers")
        self.assertEqual(_mover_query_intent("which stocks are down the most"), "losers")

    def test_gainers_phrases(self):
        self.assertEqual(_mover_query_intent("top gainers right now"), "gainers")
        self.assertEqual(_mover_query_intent("best performers today"), "gainers")

    def test_macro_not_movers(self):
        self.assertIsNone(_mover_query_intent("Why is the market down today?"))
        self.assertIsNone(_mover_query_intent("Fed outlook and rates"))


if __name__ == "__main__":
    unittest.main()
