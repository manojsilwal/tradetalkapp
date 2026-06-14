"""
Tests for debate swarm resilience — verifying that the debate survives
partial LLM failures without crashing the entire analysis.

Three scenarios:
1. Single role fails permanently → debate completes with 4 real + 1 degraded
2. All 5 roles fail → InsufficientDataError raised (truthful-data contract)
3. Single role fails once then succeeds on retry → no degradation
"""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
os.environ.setdefault("GEMINI_PRIMARY", "0")
os.environ.setdefault("GEMINI_LLM_FALLBACK", "0")
# Speed up retries for tests
os.environ["DEBATE_AGENT_RETRY_BACKOFF_S"] = "0.01"

from backend.data_errors import InsufficientDataError  # noqa: E402
from backend.schemas import DebateArgument, AgentStance  # noqa: E402
from backend import debate_agents as da  # noqa: E402


def _make_arg(role: str, stance: AgentStance = AgentStance.NEUTRAL) -> DebateArgument:
    """Build a fake DebateArgument for testing."""
    return DebateArgument(
        agent_role=role,
        agent_icon="•",
        stance=stance,
        headline=f"{role} headline",
        key_points=["p1", "p2"],
        confidence=0.6,
    )


class _DebateResilienceBase(unittest.TestCase):
    """Shared setup: stub moderator + snapshot storage to avoid side-effects."""

    def setUp(self) -> None:
        self._originals = {
            "run_moderator": da.run_moderator,
            "_store_agent_snapshot": da._store_agent_snapshot,
        }
        da.run_moderator = AsyncMock(return_value=("NEUTRAL", 0.5, "summary", None))
        da._store_agent_snapshot = lambda *a, **kw: None  # type: ignore[assignment]

    def tearDown(self) -> None:
        da.run_moderator = self._originals["run_moderator"]
        da._store_agent_snapshot = self._originals["_store_agent_snapshot"]


class TestSingleRoleFailure(_DebateResilienceBase):
    """When one role's LLM is unavailable, the debate should still complete
    with 4 real arguments and 1 degraded argument."""

    def test_bear_fails_debate_completes_with_degraded(self) -> None:
        call_count = {"bear": 0}

        async def _patched_run_agent(role, *args, **kwargs):
            if role == "bear":
                call_count["bear"] += 1
                raise InsufficientDataError(
                    "llm",
                    f"LLM analysis unavailable for role '{role}'",
                    missing=[f"llm_output:{role}"],
                )
            return _make_arg(role)

        with patch.object(da, "_run_agent", side_effect=_patched_run_agent):
            ks = type("FakeKS", (), {
                "query": lambda *a, **kw: [],
                "format_context": lambda self, docs: "",
            })()
            llm = type("FakeLLM", (), {})()
            macro_state = {"market_regime": "BULL_NORMAL", "credit_stress_index": 1.2}

            result = asyncio.run(
                da._run_full_debate_impl(
                    "GOOGL", {"ticker": "GOOGL"}, macro_state, ks, llm,
                )
            )

        # Debate should complete successfully
        self.assertEqual(len(result.arguments), 5)
        self.assertEqual(result.degraded_roles, ["bear"])

        # Bear argument should be degraded
        bear_arg = next(a for a in result.arguments if a.agent_role == "bear")
        self.assertTrue(bear_arg.degraded)
        self.assertEqual(bear_arg.confidence, 0.0)
        self.assertIn("[LLM unavailable]", bear_arg.headline)

        # Other 4 arguments should be normal
        normal_args = [a for a in result.arguments if a.agent_role != "bear"]
        for arg in normal_args:
            self.assertFalse(arg.degraded)
            self.assertEqual(arg.confidence, 0.6)

        # quality_warning should mention the degraded role
        self.assertIn("bear", result.quality_warning)
        self.assertIn("1 of 5", result.quality_warning)

        # Bear should have been called twice (initial + retry)
        self.assertEqual(call_count["bear"], 2)


class TestAllRolesFailure(_DebateResilienceBase):
    """When ALL 5 roles fail, the debate should raise InsufficientDataError
    (preserving the truthful-data contract)."""

    def test_all_roles_fail_raises_insufficient_data(self) -> None:
        async def _always_fail(role, *args, **kwargs):
            raise InsufficientDataError(
                "llm",
                f"LLM unavailable for role '{role}'",
                missing=[f"llm_output:{role}"],
            )

        with patch.object(da, "_run_agent", side_effect=_always_fail):
            ks = type("FakeKS", (), {
                "query": lambda *a, **kw: [],
                "format_context": lambda self, docs: "",
            })()
            llm = type("FakeLLM", (), {})()
            macro_state = {"market_regime": "BEAR", "credit_stress_index": 2.0}

            with self.assertRaises(InsufficientDataError) as ctx:
                asyncio.run(
                    da._run_full_debate_impl(
                        "AAPL", {"ticker": "AAPL"}, macro_state, ks, llm,
                    )
                )

            self.assertIn("all debate agents", ctx.exception.message)
            self.assertEqual(len(ctx.exception.missing), 5)


class TestRetrySucceeds(_DebateResilienceBase):
    """When a role fails on the first attempt but succeeds on retry,
    no degradation should be reported."""

    def test_bear_recovers_on_retry(self) -> None:
        call_count = {"bear": 0}

        async def _flaky_run_agent(role, *args, **kwargs):
            if role == "bear":
                call_count["bear"] += 1
                if call_count["bear"] == 1:
                    raise InsufficientDataError(
                        "llm",
                        f"LLM unavailable for role '{role}'",
                        missing=[f"llm_output:{role}"],
                    )
                # Second attempt succeeds
                return _make_arg(role, AgentStance.BEARISH)
            return _make_arg(role)

        with patch.object(da, "_run_agent", side_effect=_flaky_run_agent):
            ks = type("FakeKS", (), {
                "query": lambda *a, **kw: [],
                "format_context": lambda self, docs: "",
            })()
            llm = type("FakeLLM", (), {})()
            macro_state = {"market_regime": "BULL_NORMAL", "credit_stress_index": 1.0}

            result = asyncio.run(
                da._run_full_debate_impl(
                    "TSLA", {"ticker": "TSLA"}, macro_state, ks, llm,
                )
            )

        # All 5 arguments should be present and NONE degraded
        self.assertEqual(len(result.arguments), 5)
        self.assertEqual(result.degraded_roles, [])

        # Bear should not be degraded
        bear_arg = next(a for a in result.arguments if a.agent_role == "bear")
        self.assertFalse(bear_arg.degraded)
        self.assertEqual(bear_arg.confidence, 0.6)
        self.assertEqual(bear_arg.stance, AgentStance.BEARISH)

        # quality_warning should be None (from moderator mock) or empty
        # since no degradation occurred
        self.assertIsNone(result.quality_warning)

        # Bear was called exactly twice (fail + succeed)
        self.assertEqual(call_count["bear"], 2)


class TestMultipleRolesFailure(_DebateResilienceBase):
    """When 2-4 roles fail, the debate should still complete with a warning."""

    def test_two_roles_fail_debate_completes(self) -> None:
        async def _two_fail(role, *args, **kwargs):
            if role in ("bear", "macro"):
                raise InsufficientDataError(
                    "llm",
                    f"LLM unavailable for role '{role}'",
                    missing=[f"llm_output:{role}"],
                )
            return _make_arg(role)

        with patch.object(da, "_run_agent", side_effect=_two_fail):
            ks = type("FakeKS", (), {
                "query": lambda *a, **kw: [],
                "format_context": lambda self, docs: "",
            })()
            llm = type("FakeLLM", (), {})()
            macro_state = {"market_regime": "BULL_NORMAL"}

            result = asyncio.run(
                da._run_full_debate_impl(
                    "MSFT", {"ticker": "MSFT"}, macro_state, ks, llm,
                )
            )

        self.assertEqual(len(result.arguments), 5)
        self.assertEqual(sorted(result.degraded_roles), ["bear", "macro"])
        self.assertIn("2 of 5", result.quality_warning)

        # Degraded arguments should have 0 confidence
        for arg in result.arguments:
            if arg.agent_role in ("bear", "macro"):
                self.assertTrue(arg.degraded)
                self.assertEqual(arg.confidence, 0.0)
            else:
                self.assertFalse(arg.degraded)


class TestBuildDegradedArgument(unittest.TestCase):
    """Unit test for the _build_degraded_argument helper."""

    def test_degraded_argument_uses_fallback_template(self) -> None:
        arg = da._build_degraded_argument("bear", "AAPL", {"pe_ratio": 30})
        self.assertTrue(arg.degraded)
        self.assertEqual(arg.confidence, 0.0)
        self.assertIn("[LLM unavailable]", arg.headline)
        self.assertEqual(arg.agent_role, "bear")
        self.assertIsInstance(arg.key_points, list)
        self.assertGreater(len(arg.key_points), 0)

    def test_degraded_argument_unknown_role(self) -> None:
        """Even an unknown role should produce a degraded argument without crashing."""
        arg = da._build_degraded_argument("unknown_role", "XYZ", {})
        self.assertTrue(arg.degraded)
        self.assertEqual(arg.confidence, 0.0)
        self.assertIn("[LLM unavailable]", arg.headline)


class TestModeratorResilience(unittest.TestCase):
    """Verify that when the moderator LLM call fails, run_moderator catches the
    exception and falls back to a heuristic consensus verdict instead of crashing."""

    def test_moderator_fails_returns_heuristic_neutral(self) -> None:
        from unittest.mock import MagicMock

        llm = MagicMock()
        # Mock generate_moderator_verdict to raise InsufficientDataError
        llm.generate_moderator_verdict = AsyncMock(side_effect=InsufficientDataError("llm", "Moderator down"))

        ks = type("FakeKS", (), {
            "query": lambda *a, **kw: [],
            "query_with_refs": lambda *a, **kw: ([], []),
            "format_context": lambda self, docs: "",
        })()

        arguments = [
            _make_arg("bull", AgentStance.NEUTRAL),
            _make_arg("bear", AgentStance.NEUTRAL),
        ]

        # run_moderator should succeed and return NEUTRAL
        verdict, confidence, summary, warning = asyncio.run(
            da.run_moderator("AAPL", arguments, ks, llm)
        )

        self.assertEqual(verdict, "NEUTRAL")
        self.assertEqual(confidence, 0.6)
        self.assertIsNotNone(warning)
        self.assertIn("unavailable", warning)
        self.assertIn("consensus", summary)


if __name__ == "__main__":
    unittest.main()
