"""
Integration tests for Phase C1 PR 1 dual-read wiring.

These tests call the real handlers in ``backend/agents.py`` and
``backend/debate_agents.py`` and assert:

1. With ``RESOURCES_USE_REGISTRY=0`` and the registry singleton reset, the
   handlers use their byte-exact hardcoded defaults (regression guard — the
   pre-Phase-C behavior must be 100% preserved when the flag is off).

2. With the registry enabled and a SEPL-written config override, the handlers
   actually honor the new thresholds (proves dual-read is plumbed end-to-end,
   not just importable).
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")


from backend.schemas import MarketState, MarketRegime, AgentStance  # noqa: E402
from backend.resource_registry import (  # noqa: E402
    ResourceKind,
    ResourceRecord,
    ResourceRegistry,
    _reset_singleton_for_tests,
)
from backend.resource_seeder import seed_resources_if_empty  # noqa: E402


class _FakeShortsConnector:
    """In-memory stand-in for ShortsConnector used by ShortInterestAgentPair."""

    def __init__(self, sir: float, dtc: float) -> None:
        self._sir = sir
        self._dtc = dtc

    async def fetch_data(self, ticker: str):
        return {"short_interest_ratio": self._sir, "days_to_cover": self._dtc}


class _EnvIsolated(unittest.TestCase):
    """Isolate RESOURCES_* env + singleton across tests."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "r.db")

        self._env_keys = ("RESOURCES_DB_PATH", "RESOURCES_USE_REGISTRY")
        self._orig_env = {k: os.environ.get(k) for k in self._env_keys}
        _reset_singleton_for_tests()

    def tearDown(self) -> None:
        for k in self._env_keys:
            v = self._orig_env.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_singleton_for_tests()


# ── Regression: flag-off preserves pre-Phase-C behavior byte-exact ───────────


class TestSIRClassifierFlagOff(_EnvIsolated):
    def test_sir_below_bull_threshold_emits_zero(self):
        os.environ["RESOURCES_USE_REGISTRY"] = "0"
        from backend.agents import ShortInterestAgentPair
        pair = ShortInterestAgentPair(connector=_FakeShortsConnector(sir=8.0, dtc=2.0))
        mk = MarketState(
            vix_level=12.0,
            market_regime=MarketRegime.BULL_NORMAL,
            credit_stress_index=0.8,
        )
        report = asyncio.run(pair._analyst_step(mk, "ANY", []))
        self.assertEqual(report["trading_signal"], 0)

    def test_sir_above_bull_threshold_emits_one(self):
        os.environ["RESOURCES_USE_REGISTRY"] = "0"
        from backend.agents import ShortInterestAgentPair
        pair = ShortInterestAgentPair(connector=_FakeShortsConnector(sir=18.0, dtc=2.0))
        mk = MarketState(
            vix_level=12.0,
            market_regime=MarketRegime.BULL_NORMAL,
            credit_stress_index=0.8,
        )
        report = asyncio.run(pair._analyst_step(mk, "ANY", []))
        self.assertEqual(report["trading_signal"], 1)

    def test_qa_verifier_rejects_when_csi_exceeds_default_1_1(self):
        os.environ["RESOURCES_USE_REGISTRY"] = "0"
        from backend.agents import ShortInterestAgentPair
        from backend.schemas import VerificationStatus
        pair = ShortInterestAgentPair(connector=_FakeShortsConnector(sir=18.0, dtc=2.0))
        mk = MarketState(
            vix_level=50.0,
            market_regime=MarketRegime.BEAR_NORMAL,
            credit_stress_index=1.2,  # > 1.1 -> reject
        )
        verdict = asyncio.run(
            pair._qa_verifier_step(
                {
                    "trading_signal": 1,
                    "rationale": "SIR high; days to cover is rising.",
                },
                mk,
                [],
            )
        )
        self.assertEqual(verdict["status"], VerificationStatus.REJECTED)


class TestDebateStanceFlagOff(_EnvIsolated):
    def test_bull_bullish_when_sir_above_default_5(self):
        os.environ["RESOURCES_USE_REGISTRY"] = "0"
        from backend.debate_agents import _determine_stance
        out = _determine_stance(
            "bull",
            {"short_interest_ratio": 6.0, "revenue_growth": 0, "price_return_3m": 0},
            {},
        )
        self.assertEqual(out, AgentStance.BULLISH)

    def test_bull_bearish_when_all_below_default_ceilings(self):
        os.environ["RESOURCES_USE_REGISTRY"] = "0"
        from backend.debate_agents import _determine_stance
        out = _determine_stance(
            "bull",
            {"short_interest_ratio": 1.0, "revenue_growth": -5, "price_return_3m": -15},
            {},
        )
        self.assertEqual(out, AgentStance.BEARISH)

    def test_bear_bearish_when_pe_above_default_50(self):
        os.environ["RESOURCES_USE_REGISTRY"] = "0"
        from backend.debate_agents import _determine_stance
        out = _determine_stance(
            "bear",
            {"pe_ratio": 60.0, "debt_to_equity": 10, "price_return_3m": 0},
            {},
        )
        self.assertEqual(out, AgentStance.BEARISH)

    def test_bear_bullish_when_low_pe_and_positive_return(self):
        os.environ["RESOURCES_USE_REGISTRY"] = "0"
        from backend.debate_agents import _determine_stance
        out = _determine_stance(
            "bear",
            {"pe_ratio": 15.0, "debt_to_equity": 10, "price_return_3m": 5.0},
            {},
        )
        self.assertEqual(out, AgentStance.BULLISH)


# ── Flag-on: registry override reaches the handler ──────────────────────────


class TestSIRClassifierRegistryOverride(_EnvIsolated):
    def setUp(self) -> None:
        super().setUp()
        os.environ["RESOURCES_DB_PATH"] = self.db_path
        os.environ["RESOURCES_USE_REGISTRY"] = "1"
        seed_resources_if_empty()

    def test_registry_override_lowers_bull_threshold(self):
        """Evolve the SIR bull threshold 15 -> 6; confirm SIR=8 now triggers
        signal=1 (it would have been 0 with the default)."""
        from backend.tool_configs import update_tool_config
        update_tool_config(
            "short_interest_classifier",
            {
                "sir_bull_threshold": 6.0,
                "sir_ambiguous_min": 3.0,
                "sir_ambiguous_max": 5.0,
                "dtc_confirm_threshold": 5.0,
                "bearish_csi_threshold": 1.1,
            },
            reason="unit test canary",
            actor="tester",
        )
        from backend.agents import ShortInterestAgentPair
        pair = ShortInterestAgentPair(connector=_FakeShortsConnector(sir=8.0, dtc=1.0))
        mk = MarketState(
            vix_level=12.0,
            market_regime=MarketRegime.BULL_NORMAL,
            credit_stress_index=0.8,
        )
        report = asyncio.run(pair._analyst_step(mk, "ANY", []))
        self.assertEqual(report["trading_signal"], 1)

    def test_registry_override_raises_csi_rejection_threshold(self):
        from backend.tool_configs import update_tool_config
        from backend.schemas import VerificationStatus
        update_tool_config(
            "short_interest_classifier",
            {
                "sir_bull_threshold": 15.0,
                "sir_ambiguous_min": 10.0,
                "sir_ambiguous_max": 20.0,
                "dtc_confirm_threshold": 5.0,
                "bearish_csi_threshold": 1.5,  # was 1.1
            },
            reason="unit test canary",
            actor="tester",
        )
        from backend.agents import ShortInterestAgentPair
        pair = ShortInterestAgentPair(connector=_FakeShortsConnector(sir=18.0, dtc=2.0))
        mk = MarketState(
            vix_level=40.0,
            market_regime=MarketRegime.BEAR_NORMAL,
            credit_stress_index=1.2,  # between old (1.1) and new (1.5)
        )
        verdict = asyncio.run(
            pair._qa_verifier_step(
                {
                    "trading_signal": 1,
                    # include "days to cover" so the secondary rejection clause
                    # does not fire and we isolate the CSI threshold behavior.
                    "rationale": "High SIR confirmed; days to cover is healthy.",
                },
                mk,
                [],
            )
        )
        # With new threshold 1.5 > 1.2, QA no longer rejects on macro grounds.
        self.assertEqual(verdict["status"], VerificationStatus.VERIFIED)


class TestDebateStanceRegistryOverride(_EnvIsolated):
    def setUp(self) -> None:
        super().setUp()
        os.environ["RESOURCES_DB_PATH"] = self.db_path
        os.environ["RESOURCES_USE_REGISTRY"] = "1"
        seed_resources_if_empty()

    def test_registry_override_flips_bull_stance_decision(self):
        """Raise the bull floors so a data point that was BULLISH becomes NEUTRAL."""
        from backend.tool_configs import update_tool_config
        from backend.debate_agents import _determine_stance
        update_tool_config(
            "debate_stance_heuristic_bull",
            {
                "sir_bull_floor": 10.0,  # was 5
                "rev_growth_bull_floor": 30.0,  # was 15
                "r3m_bull_floor": 15.0,  # was 5
                "sir_bear_ceiling": 2.0,
                "rev_growth_bear_ceiling": 0.0,
                "r3m_bear_ceiling": -10.0,
            },
            reason="unit test canary",
            actor="tester",
        )
        out = _determine_stance(
            "bull",
            {"short_interest_ratio": 6.0, "revenue_growth": 0, "price_return_3m": 0},
            {},
        )
        self.assertEqual(out, AgentStance.NEUTRAL)


if __name__ == "__main__":
    unittest.main()
