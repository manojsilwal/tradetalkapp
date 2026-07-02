"""Unit tests for decision terminal helpers (no network)."""
import json
import unittest
from unittest.mock import patch

from backend.connectors.polymarket_gating import score_polymarket_relevance
from backend.decision_terminal import (
    _build_analyst_consensus,
    _dcf_sensitivity_weight_factor,
    _fuse_headline_verdict,
    _strip_non_json_floats,
    _decision_terminal_payload_json_safe,
    _build_provider_audit,
    _build_valuation_panel,
    _ResolvedSpot,
    STREET_DIVERGENCE_FLAG_PCT,
    assemble_terminal_from_slices,
    build_decision_terminal_payload,
    build_snapshot_slice,
)
from backend.valuation_signal import composite_signal_label
from backend.schemas import (
    DebateResult,
    DecisionRoadmapPayload,
    DecisionSnapshotPayload,
    DecisionTerminalPayload,
    DecisionVerdictPayload,
    FactorResult,
    OptionsFlow,
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

    def test_payload_carries_swarm_and_debate(self):
        swarm = SwarmConsensus(
            ticker="AAPL",
            macro_state=MarketState(market_regime=MarketRegime.BULL_NORMAL),
            global_signal=1,
            global_verdict="BUY",
            confidence=0.7,
            factors={
                "short_interest": FactorResult(
                    factor_name="Short Interest",
                    status=VerificationStatus.VERIFIED,
                    confidence=0.8,
                    rationale="ok",
                    trading_signal=1,
                ),
            },
        )
        debate = DebateResult(
            ticker="AAPL",
            arguments=[],
            verdict="BUY",
            consensus_confidence=0.8,
            moderator_summary="summary",
            bull_score=3,
            bear_score=1,
            neutral_score=1,
        )
        payload = DecisionTerminalPayload(
            ticker="AAPL",
            disclaimer="d",
            generated_at_utc="t",
            valuation=TerminalValuationPanel(
                current_price_usd=100.0,
                average_fair_value_usd=110.0,
                pct_vs_average=10.0,
                gauge_label="",
                models=[],
            ),
            quality=TerminalQualityPanel(rows=[]),
            verdict=TerminalVerdictPanel(
                headline_verdict="BUY",
                debate_verdict="BUY",
                swarm_verdict="BUY",
                expert_bullish_pct=75.0,
            ),
            roadmap=TerminalRoadmapPanel(
                confidence_0_1=0.5,
                provenance=TerminalFieldProvenance(),
            ),
            swarm=swarm,
            debate=debate,
        )
        self.assertIsNotNone(payload.swarm)
        self.assertIsNotNone(payload.debate)
        self.assertEqual(payload.swarm.ticker, "AAPL")
        self.assertEqual(payload.debate.ticker, "AAPL")


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


class TestMomentumValuationPanel(unittest.IsolatedAsyncioTestCase):
    async def test_momentum_model_entry_when_data_available(self):
        from unittest.mock import AsyncMock, patch
        import pandas as pd

        fake_df = pd.DataFrame(
            {
                "Open": [100.0] * 200,
                "High": [101.0] * 200,
                "Low": [99.0] * 200,
                "Close": [100.0 + i * 0.1 for i in range(200)],
                "Volume": [1e6] * 200,
            }
        )
        mom_summary = {
            "momentum_pricing_score": 82.5,
            "downside_exposure_score": 45.0,
            "decision_quality_score": 69.0,
            "classification": "Strong Momentum Candidate",
            "crash_risk": "Medium",
            "subscores": {},
            "downside": {},
            "risk_flags": [],
            "agent_summary": "test",
        }

        with patch(
            "backend.connectors.momentum_data.fetch_momentum_inputs",
            new=AsyncMock(return_value=(fake_df, fake_df, fake_df, {"ticker": "AAPL"})),
        ), patch(
            "backend.momentum_model.analyze_momentum",
            return_value=mom_summary,
        ):
            payload = await build_decision_terminal_payload(
                "AAPL",
                SwarmConsensus(
                    ticker="AAPL",
                    macro_state=MarketState(market_regime=MarketRegime.BULL_NORMAL),
                    global_signal=1,
                    global_verdict="BUY",
                    confidence=0.7,
                    factors={},
                ),
                DebateResult(
                    ticker="AAPL",
                    arguments=[],
                    verdict="BUY",
                    consensus_confidence=0.8,
                    moderator_summary="",
                    bull_score=3,
                    bear_score=1,
                    neutral_score=1,
                ),
                {
                    "current_price": 150.0,
                    "roe": 20.0,
                    "pe_ratio": 25.0,
                    "sector": "Technology",
                    "beta": 1.1,
                },
                {},
                {"trailingEps": 5.0},
                None,
                momentum_readout=mom_summary,
            )

        momentum_models = [m for m in payload.valuation.models if m.name == "Momentum"]
        self.assertEqual(len(momentum_models), 1)
        self.assertTrue(momentum_models[0].available)
        self.assertEqual(momentum_models[0].momentum_score, 82.5)
        self.assertEqual(momentum_models[0].momentum_summary["classification"], "Strong Momentum Candidate")

    async def test_dcf_model_includes_scenarios(self):
        ext = {
            "operatingCashflow": 140_000_000_000,
            "capitalExpenditures": -11_000_000_000,
            "sharesOutstanding": 14_690_000_000,
            "totalCash": 45_570_000_000,
            "shortTermInvestments": 22_940_000_000,
            "longTermInvestments": 78_090_000_000,
            "totalDebt": 84_710_000_000,
            "beta": 1.09,
            "revenueGrowth": 0.06,
            "trailingEps": 8.26,
        }
        payload = await build_decision_terminal_payload(
            "AAPL",
            SwarmConsensus(
                ticker="AAPL",
                macro_state=MarketState(market_regime=MarketRegime.BULL_NORMAL),
                global_signal=1,
                global_verdict="BUY",
                confidence=0.7,
                factors={},
            ),
            DebateResult(
                ticker="AAPL",
                arguments=[],
                verdict="BUY",
                consensus_confidence=0.8,
                moderator_summary="",
                bull_score=3,
                bear_score=1,
                neutral_score=1,
            ),
            {"current_price": 298.0, "roe": 150.0, "pe_ratio": 36.0},
            {},
            ext,
            None,
        )
        dcf = [m for m in payload.valuation.models if m.name == "DCF"][0]
        self.assertTrue(dcf.available)
        self.assertIsNotNone(dcf.fair_value_usd)
        self.assertGreater(dcf.fair_value_usd, 120.0)
        self.assertIsNotNone(dcf.scenarios)
        assert dcf.scenarios is not None
        self.assertIn("bear", dcf.scenarios)
        self.assertIn("bull", dcf.scenarios)
        self.assertLess(dcf.scenarios["bear"], dcf.fair_value_usd)
        self.assertGreater(dcf.scenarios["bull"], dcf.fair_value_usd)


class TestAnalystConsensus(unittest.TestCase):
    def test_build_analyst_consensus_divergence_when_far_above_street(self):
        ac = _build_analyst_consensus(
            analyst_targets={
                "mean_target_usd": 190.0,
                "high_target_usd": 250.0,
                "low_target_usd": 150.0,
                "num_analysts": 45,
                "recommendation_key": "buy",
                "source": "fincrawler",
            },
            price_f=192.53,
            avg_fair=551.0,
        )
        self.assertIsNotNone(ac)
        assert ac is not None
        self.assertTrue(ac.divergence_flag)
        self.assertGreater(ac.our_vs_street_pct or 0, STREET_DIVERGENCE_FLAG_PCT)
        self.assertAlmostEqual(ac.street_vs_price_pct or 0, -1.31, places=1)

    @patch("backend.decision_terminal._multiples_heuristic_fair_price")
    @patch("backend.valuation_inputs.compute_dcf_scenarios")
    def test_valuation_panel_attaches_analyst_consensus_and_flag(
        self, mock_dcf, mock_multiples
    ):
        mock_dcf.return_value = {
            "available": True,
            "base_fair_value_usd": 688.0,
            "scenarios": {"bear": 97.0, "base": 688.0, "bull": 1598.0},
            "business_type": "ai_accelerator_platform_leader",
            "dcf_confidence_score": 55,
            "risk_flags": ["high_growth_sensitivity"],
            "revenue_growth": 0.55,
        }
        mock_multiples.return_value = 487.0

        panel = _build_valuation_panel(
            ticker="NVDA",
            debate_data={"roe": 100.0, "pe_ratio": 50.0, "gross_margins": 75.0},
            ext={
                "trailingEps": 5.0,
                "analyst_targets": {
                    "mean_target_usd": 190.0,
                    "high_target_usd": 250.0,
                    "low_target_usd": 150.0,
                    "num_analysts": 45,
                    "recommendation_key": "buy",
                    "source": "fincrawler",
                },
            },
            resolved=_ResolvedSpot(
                price_f=192.53,
                spot_price_source="test",
                market_data_degraded=False,
                filled_spot_from_ext=False,
                spot_envelope=None,
                debate_spot_price_source=None,
            ),
            hist_cagr=None,
            hist_quality={},
            momentum_readout=None,
        )
        self.assertIsNotNone(panel.analyst_consensus)
        self.assertTrue(panel.analyst_consensus.divergence_flag)
        self.assertIn("street_far_below_consensus", panel.risk_flags)

    @patch("backend.decision_terminal._multiples_heuristic_fair_price")
    @patch("backend.valuation_inputs.compute_dcf_scenarios")
    def test_valuation_panel_caps_headline_above_street(
        self, mock_dcf, mock_multiples
    ):
        mock_dcf.return_value = {
            "available": True,
            "base_fair_value_usd": 688.0,
            "scenarios": {"bear": 97.0, "base": 688.0, "bull": 1598.0},
            "business_type": "ai_accelerator_platform_leader",
            "dcf_confidence_score": 55,
            "risk_flags": [],
        }
        mock_multiples.return_value = 487.0

        panel = _build_valuation_panel(
            ticker="NVDA",
            debate_data={"roe": 100.0, "pe_ratio": 50.0},
            ext={
                "trailingEps": 5.0,
                "analyst_targets": {
                    "mean_target_usd": 190.0,
                    "source": "fincrawler",
                },
            },
            resolved=_ResolvedSpot(
                price_f=192.53,
                spot_price_source="test",
                market_data_degraded=False,
                filled_spot_from_ext=False,
                spot_envelope=None,
                debate_spot_price_source=None,
            ),
            hist_cagr=None,
            hist_quality={},
            momentum_readout=None,
        )
        street_cap = 190.0 * (1.0 + STREET_DIVERGENCE_FLAG_PCT / 100.0)
        self.assertAlmostEqual(panel.average_fair_value_usd, round(street_cap, 2))
        self.assertIn("capped at", panel.panel_note.lower())


class TestDcfSensitivityWeighting(unittest.TestCase):
    def test_tight_range_keeps_full_weight(self):
        # (110-90)/100 = 20% wide -> below the 60% threshold -> factor 1.0
        self.assertAlmostEqual(_dcf_sensitivity_weight_factor(90.0, 100.0, 110.0), 1.0)

    def test_wide_range_shrinks_to_floor(self):
        # NVDA-like: (722-67)/308 ≈ 213% wide -> floored at 0.3
        self.assertAlmostEqual(_dcf_sensitivity_weight_factor(67.0, 308.0, 722.0), 0.3)

    def test_monotonic_decreasing_with_width(self):
        narrow = _dcf_sensitivity_weight_factor(80.0, 100.0, 160.0)   # 80% wide
        wider = _dcf_sensitivity_weight_factor(60.0, 100.0, 200.0)    # 140% wide
        self.assertGreater(narrow, wider)

    def test_missing_range_is_neutral(self):
        self.assertEqual(_dcf_sensitivity_weight_factor(None, 100.0, 200.0), 1.0)
        self.assertEqual(_dcf_sensitivity_weight_factor(50.0, 0.0, 200.0), 1.0)


class TestCompositeSignal(unittest.TestCase):
    def test_undervalued_weak_momentum_is_watchlist(self):
        out = composite_signal_label("Moderately Undervalued", 46)
        self.assertIn("watchlist", out.lower())
        self.assertIn("momentum weak", out.lower())

    def test_undervalued_strong_momentum_confirms(self):
        out = composite_signal_label("Significantly Undervalued", 75)
        self.assertIn("confirming", out.lower())

    def test_overvalued_strong_momentum_is_trend_only(self):
        out = composite_signal_label("Moderately Overvalued", 80)
        self.assertIn("trend", out.lower())

    def test_blank_when_no_signal(self):
        self.assertEqual(composite_signal_label("", 50), "")


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
        self.assertEqual(audit["valuation"]["fair_value_models"]["Momentum"], "composite_momentum_model")
        self.assertEqual(audit["verdict"]["prediction_market"]["provider"], "polymarket")
        self.assertEqual(audit["roadmap"]["scenario_prices_source"], "heuristic")


class TestSliceAssembly(unittest.TestCase):
    def test_assemble_terminal_from_slices(self):
        swarm = SwarmConsensus(
            ticker="AAPL",
            macro_state=MarketState(market_regime=MarketRegime.BULL_NORMAL),
            global_signal=1,
            global_verdict="BUY",
            confidence=0.7,
            factors={},
        )
        debate = DebateResult(
            ticker="AAPL",
            arguments=[],
            verdict="BUY",
            consensus_confidence=0.8,
            moderator_summary="summary",
            bull_score=3,
            bear_score=1,
            neutral_score=1,
        )
        snapshot = build_snapshot_slice(
            "AAPL",
            {"current_price": 100.0, "roe": 20.0, "pe_ratio": 25.0},
            {"trailingEps": 5.0},
        )
        verdict = DecisionVerdictPayload(
            ticker="AAPL",
            generated_at_utc="t",
            verdict=TerminalVerdictPanel(
                headline_verdict="BUY",
                debate_verdict="BUY",
                swarm_verdict="BUY",
            ),
            swarm=swarm,
            debate=debate,
        )
        roadmap = DecisionRoadmapPayload(
            ticker="AAPL",
            generated_at_utc="t",
            roadmap=TerminalRoadmapPanel(
                confidence_0_1=0.5,
                provenance=TerminalFieldProvenance(),
            ),
        )
        merged = assemble_terminal_from_slices(snapshot, verdict, roadmap)
        self.assertEqual(merged.ticker, "AAPL")
        self.assertEqual(merged.valuation.current_price_usd, 100.0)
        self.assertEqual(merged.verdict.headline_verdict, "BUY")
        self.assertIsNotNone(merged.swarm)
        self.assertIsNotNone(merged.debate)

    def test_assemble_terminal_carries_options_from_swarm(self):
        opts = OptionsFlow(
            put_call_volume_ratio=1.24,
            source="cboe",
            as_of="2026-07-01T12:00:00+00:00",
            net_premium_bias="bearish",
        )
        swarm = SwarmConsensus(
            ticker="AAPL",
            macro_state=MarketState(market_regime=MarketRegime.BULL_NORMAL),
            global_signal=0,
            global_verdict="NEUTRAL",
            confidence=0.5,
            factors={},
            options=opts,
        )
        debate = DebateResult(
            ticker="AAPL",
            arguments=[],
            verdict="NEUTRAL",
            consensus_confidence=0.5,
            moderator_summary="",
            bull_score=1,
            bear_score=1,
            neutral_score=3,
        )
        snapshot = build_snapshot_slice(
            "AAPL",
            {"current_price": 100.0, "roe": 20.0, "pe_ratio": 25.0},
            {"trailingEps": 5.0},
        )
        verdict = DecisionVerdictPayload(
            ticker="AAPL",
            generated_at_utc="t",
            verdict=TerminalVerdictPanel(
                headline_verdict="NEUTRAL",
                debate_verdict="NEUTRAL",
                swarm_verdict="NEUTRAL",
            ),
            swarm=swarm,
            debate=debate,
            options=opts,
        )
        roadmap = DecisionRoadmapPayload(
            ticker="AAPL",
            generated_at_utc="t",
            roadmap=TerminalRoadmapPanel(
                confidence_0_1=0.5,
                provenance=TerminalFieldProvenance(),
            ),
        )
        merged = assemble_terminal_from_slices(snapshot, verdict, roadmap)
        self.assertIsNotNone(merged.options)
        self.assertEqual(merged.options.source, "cboe")
        self.assertAlmostEqual(merged.options.put_call_volume_ratio, 1.24)


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
