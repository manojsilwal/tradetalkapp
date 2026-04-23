"""Unit tests for decision terminal helpers (no network)."""
import json
import unittest

from backend.decision_terminal import (
    score_polymarket_relevance,
    _fuse_headline_verdict,
    _graham_fair_value,
    _strip_non_json_floats,
    _decision_terminal_payload_json_safe,
    _build_provider_audit,
)
from backend.schemas import (
    DebateResult,
    DecisionTerminalPayload,
    FactorResult,
    SwarmConsensus,
    MarketState,
    MarketRegime,
    TerminalFieldProvenance,
    TerminalQualityPanel,
    TerminalRoadmapPanel,
    TerminalValuationPanel,
    TerminalVerdictPanel,
    VerificationStatus,
)


class TestJsonSafeFloats(unittest.TestCase):
    def test_strip_nan_inf_for_json(self):
        self.assertIsNone(_strip_non_json_floats(float("nan")))
        self.assertIsNone(_strip_non_json_floats(float("inf")))
        self.assertEqual(_strip_non_json_floats(1.5), 1.5)
        self.assertEqual(
            _strip_non_json_floats({"x": float("nan"), "y": [float("-inf"), 2.0]}),
            {"x": None, "y": [None, 2.0]},
        )

    def test_payload_roundtrip_drops_nan(self):
        # Optional floats can hold NaN from yfinance/heuristics; Pydantic allows it here.
        p = DecisionTerminalPayload(
            ticker="X",
            disclaimer="d",
            generated_at_utc="t",
            valuation=TerminalValuationPanel(
                current_price_usd=float("nan"),
                average_fair_value_usd=float("nan"),
                pct_vs_average=float("inf"),
                gauge_label="",
                models=[],
            ),
            quality=TerminalQualityPanel(rows=[]),
            verdict=TerminalVerdictPanel(
                headline_verdict="h",
                debate_verdict="d",
                swarm_verdict="s",
                expert_bullish_pct=float("nan"),
            ),
            roadmap=TerminalRoadmapPanel(
                confidence_0_1=0.0,
                provenance=TerminalFieldProvenance(),
            ),
        )
        safe = _decision_terminal_payload_json_safe(p)
        self.assertIsNone(safe.valuation.current_price_usd)
        self.assertIsNone(safe.valuation.average_fair_value_usd)
        self.assertIsNone(safe.valuation.pct_vs_average)
        self.assertIsNone(safe.verdict.expert_bullish_pct)
        json.dumps(safe.model_dump(mode="json"))


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


class TestProviderAudit(unittest.TestCase):
    def test_maps_blocks_and_spot_family(self):
        audit = _build_provider_audit(
            ticker="AAPL",
            debate_data={"spot_price_source": "stooq"},
            poly_raw={
                "source": "Polymarket Gamma API (Live)",
                "keyword_resolution": "static_map",
                "events": [{"title": "x"}],
                "has_relevant_data": True,
            },
            debate_spot_price_source="stooq",
            terminal_spot_price_source="stooq",
            market_data_degraded=True,
            filled_spot_from_ext=False,
            hist_cagr_present=False,
            hist_quality_nonempty=True,
            roadmap=TerminalRoadmapPanel(
                confidence_0_1=0.2,
                used_heuristic_fallback=True,
                provenance=TerminalFieldProvenance(source="heuristic"),
            ),
        )
        self.assertEqual(audit["debate_market_pipeline"]["spot_provider_family"], "stooq")
        self.assertEqual(audit["valuation"]["spot_and_momentum_inputs"], "stooq")
        self.assertEqual(audit["verdict"]["prediction_market"]["provider"], "polymarket")
        self.assertEqual(audit["roadmap"]["scenario_prices_source"], "heuristic")


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
