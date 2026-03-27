"""Unit tests for decision terminal helpers (no network)."""
import unittest

from backend.decision_terminal import (
    score_polymarket_relevance,
    _fuse_headline_verdict,
    _graham_fair_value,
)
from backend.schemas import (
    DebateResult,
    FactorResult,
    SwarmConsensus,
    MarketState,
    MarketRegime,
    VerificationStatus,
)


class TestPolymarketRelevance(unittest.TestCase):
    def test_equity_title_scores_high(self):
        s = score_polymarket_relevance(
            "Will AAPL beat earnings Q4?",
            "",
            "AAPL",
            ["Apple", "AAPL"],
        )
        self.assertGreaterEqual(s, 0.45)

    def test_political_noise_scores_low(self):
        s = score_polymarket_relevance(
            "2028 US presidential election winner",
            "political markets",
            "AAPL",
            ["Apple"],
        )
        self.assertLess(s, 0.45)


class TestGraham(unittest.TestCase):
    def test_graham(self):
        g = _graham_fair_value(5.0, 20.0)
        self.assertIsNotNone(g)
        self.assertGreater(g, 0)


class TestVerdictFusion(unittest.TestCase):
    def _swarm(self, verdict):
        return SwarmConsensus(
            ticker="TEST",
            macro_state=MarketState(market_regime=MarketRegime.BULL_NORMAL),
            global_signal=0,
            global_verdict=verdict,
            confidence=0.5,
            factors={
                "short_interest": FactorResult(
                    factor_name="Short Interest",
                    status=VerificationStatus.VERIFIED,
                    confidence=0.8,
                    rationale="ok",
                    trading_signal=0,
                ),
            },
        )

    def _debate(self, verdict):
        return DebateResult(
            ticker="TEST",
            arguments=[],
            verdict=verdict,
            consensus_confidence=0.8,
            moderator_summary="",
            bull_score=3,
            bear_score=1,
            neutral_score=1,
        )

    def test_rejected_caps_bullish_debate(self):
        h, note = _fuse_headline_verdict(
            self._swarm("REJECTED (MACRO/RISK STRESS)"),
            self._debate("STRONG BUY"),
        )
        self.assertEqual(h, "NEUTRAL")
        self.assertIn("capped", note.lower())


if __name__ == "__main__":
    unittest.main()
