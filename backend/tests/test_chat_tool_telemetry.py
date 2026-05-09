"""
Phase A1 — TrajectoryStep v2 + accumulator + summary tests.

Locks the explicit fatal-step semantics from the plan:
- K = 3 consecutive ``execution-class`` errors trigger fatal markers.
- ``empty`` does NOT increment the streak (counts as ok execution).
- ``fatal_streak_start_step_index`` and ``fatal_trigger_step_index`` are
  sticky once set — the *first* fatal event wins.
- Reset only on ``execution_status == "ok"`` (success or empty).
- ``valid_prefix_steps`` equals the streak start when fatal, else step count.
- Quote-card prefetch contributes ``quote`` family but is *not* a step.
"""
from __future__ import annotations

import json
import unittest

from backend.chat_tool_family import SkillName, SkillTier, StepPhase
from backend.chat_tool_telemetry import (
    DB_OBSERVATION_CAP,
    EVENT_CHAT_TRACE,
    FATAL_K,
    HANDOFF_PAYLOAD_BUDGET,
    PUBLIC_OBSERVATION_CAP,
    TRAJECTORY_SCHEMA_VERSION,
    TrajectoryAccumulator,
    build_handoff_payload,
    build_trajectory_step,
    canonical_input_hash,
    derive_evidence_quality,
    summarize_trace,
    trajectory_steps_for_sse,
    TrajectoryStepInputs,
)


def _step(acc: TrajectoryAccumulator, name: str, outcome: str, **kwargs) -> dict:
    """Helper: record one step on the accumulator with sane defaults."""
    return acc.record(
        tool_name=name,
        arguments=kwargs.get("arguments", {}),
        result=kwargs.get("result", "ok"),
        outcome=outcome,
        latency_ms=kwargs.get("latency_ms", 5),
        error_type=kwargs.get("error_type"),
    )


# ── Pure builder semantics ───────────────────────────────────────────────────


class TestBuildTrajectoryStep(unittest.TestCase):
    def test_success_step_has_useful_quality(self) -> None:
        row = build_trajectory_step(
            TrajectoryStepInputs(
                step_index=0,
                tool_name="get_stock_quote",
                arguments={"ticker": "AAPL"},
                result="**AAPL** — Full Quote Snapshot\n- Price: $200.00",
                outcome="success",
                latency_ms=10,
            )
        )
        self.assertEqual(row["execution_status"], "ok")
        self.assertFalse(row["is_execution_error"])
        self.assertEqual(row["evidence_quality"], "useful")
        self.assertEqual(row["tool_family"], "quote")
        self.assertEqual(row["source_refs"], ["quote:AAPL"])
        self.assertEqual(row["consecutive_tool_errors_after_step"], 0)
        self.assertIsNone(row["fatal_trigger_step_index"])
        self.assertIsNone(row["fatal_streak_start_step_index"])
        self.assertFalse(row["fatal_detected_after_step"])
        # Backwards-compat fields preserved for chat.py / decision ledger.
        self.assertEqual(row["name"], "get_stock_quote")
        self.assertEqual(row["arguments"], {"ticker": "AAPL"})
        self.assertEqual(row["outcome"], "success")
        # Training-export hint flags
        self.assertTrue(row["observation_is_exogenous"])
        self.assertTrue(row["is_observation_masked"])

    def test_empty_outcome_resets_streak(self) -> None:
        # empty is execution_status=ok per plan: "soft empty" must not advance
        # the fatal counter.
        row = build_trajectory_step(
            TrajectoryStepInputs(
                step_index=2,
                tool_name="get_market_news",
                arguments={"query": "macro"},
                result="No live news headlines available at this time.",
                outcome="empty",
                latency_ms=3,
                prior_consec_errors=2,
                prior_active_streak_start=0,
            )
        )
        self.assertEqual(row["execution_status"], "ok")
        self.assertEqual(row["evidence_quality"], "irrelevant")
        self.assertEqual(row["consecutive_tool_errors_after_step"], 0)
        self.assertIsNone(row["fatal_streak_start_step_index"])

    def test_error_increments_streak(self) -> None:
        row = build_trajectory_step(
            TrajectoryStepInputs(
                step_index=1,
                tool_name="get_stock_quote",
                arguments={"ticker": "ZZZBAD"},
                result="Error executing get_stock_quote: timeout",
                outcome="error",
                latency_ms=12,
                error_type="TimeoutError",
                prior_consec_errors=0,
                prior_active_streak_start=None,
            )
        )
        self.assertEqual(row["execution_status"], "error")
        self.assertTrue(row["is_execution_error"])
        self.assertEqual(row["evidence_quality"], "unknown")
        self.assertEqual(row["consecutive_tool_errors_after_step"], 1)
        # Streak start logged on row but trigger not yet — only at K.
        self.assertIsNone(row["fatal_trigger_step_index"])
        self.assertEqual(row["error_type"], "TimeoutError")
        self.assertIn("error", row)

    def test_input_hash_is_deterministic(self) -> None:
        h1 = canonical_input_hash("get_stock_quote", {"ticker": "AAPL"})
        h2 = canonical_input_hash("get_stock_quote", {"ticker": "AAPL"})
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)

    def test_evidence_quality_mapping(self) -> None:
        self.assertEqual(derive_evidence_quality("success"), "useful")
        self.assertEqual(derive_evidence_quality("empty"), "irrelevant")
        self.assertEqual(derive_evidence_quality("error"), "unknown")
        self.assertEqual(derive_evidence_quality("garbled"), "unknown")


# ── Accumulator scenarios ────────────────────────────────────────────────────


class TestAccumulatorFatalSemantics(unittest.TestCase):
    def test_three_errors_in_a_row_trigger_fatal(self) -> None:
        acc = TrajectoryAccumulator(
            trace_id="t-1", session_id="s-1", message_id="m-1"
        )
        rows = [
            _step(acc, "get_stock_quote", "error",
                  result="Error executing get_stock_quote: timeout"),
            _step(acc, "get_market_news", "error",
                  result="Error executing get_market_news: 500"),
            _step(acc, "get_top_movers", "error",
                  result="Error executing get_top_movers: 503"),
        ]
        # Last step is the trigger; counter == K.
        self.assertEqual(rows[-1]["consecutive_tool_errors_after_step"], FATAL_K)
        self.assertEqual(rows[-1]["fatal_trigger_step_index"], 2)
        self.assertEqual(rows[-1]["fatal_streak_start_step_index"], 0)
        self.assertTrue(rows[-1]["fatal_detected_after_step"])
        # Earlier rows do not have fatal markers (sticky-after-trigger).
        self.assertFalse(rows[0]["fatal_detected_after_step"])
        self.assertFalse(rows[1]["fatal_detected_after_step"])

    def test_success_resets_streak_then_subsequent_errors_dont_retrigger(self) -> None:
        acc = TrajectoryAccumulator()
        _step(acc, "get_stock_quote", "error",
              result="Error executing get_stock_quote: x")
        _step(acc, "get_market_news", "error",
              result="Error executing get_market_news: y")
        ok = _step(acc, "get_stock_quote", "success",
                   arguments={"ticker": "AAPL"},
                   result="**AAPL** — Full Quote Snapshot")
        self.assertEqual(ok["consecutive_tool_errors_after_step"], 0)
        # Next two errors begin a new streak but only K=3 triggers fatal — and
        # since this trace has only 2 more errors the trigger should NOT fire.
        e1 = _step(acc, "get_top_movers", "error",
                   result="Error executing get_top_movers: z")
        e2 = _step(acc, "get_market_news", "error",
                   result="Error executing get_market_news: q")
        self.assertEqual(e2["consecutive_tool_errors_after_step"], 2)
        self.assertIsNone(e2["fatal_trigger_step_index"])
        self.assertFalse(e2["fatal_detected_after_step"])

    def test_empty_does_not_advance_streak(self) -> None:
        acc = TrajectoryAccumulator()
        _step(acc, "get_market_news", "error",
              result="Error executing get_market_news: 500")
        _step(acc, "get_market_news", "error",
              result="Error executing get_market_news: 500")
        empty = _step(acc, "get_market_news", "empty",
                      arguments={"query": "X"},
                      result="No live news headlines available at this time.")
        # Empty is treated as ok execution → resets streak.
        self.assertEqual(empty["consecutive_tool_errors_after_step"], 0)
        self.assertFalse(empty["fatal_detected_after_step"])

    def test_first_fatal_is_sticky_across_recovery_then_new_streak(self) -> None:
        acc = TrajectoryAccumulator()
        # First streak: errors at 0,1,2 → fatal trigger at step 2 (start 0).
        _step(acc, "get_stock_quote", "error", result="Error executing get_stock_quote: x")
        _step(acc, "get_stock_quote", "error", result="Error executing get_stock_quote: x")
        first_trigger = _step(
            acc, "get_stock_quote", "error", result="Error executing get_stock_quote: x"
        )
        self.assertEqual(first_trigger["fatal_trigger_step_index"], 2)
        self.assertEqual(first_trigger["fatal_streak_start_step_index"], 0)

        # Recovery
        _step(acc, "get_stock_quote", "success", arguments={"ticker": "AAPL"},
              result="**AAPL** — Full Quote Snapshot")
        # New streak: errors at 4,5,6 — would trigger again, but fatal markers stay frozen.
        _step(acc, "get_stock_quote", "error", result="Error executing get_stock_quote: x")
        _step(acc, "get_stock_quote", "error", result="Error executing get_stock_quote: x")
        second_trigger = _step(
            acc, "get_stock_quote", "error", result="Error executing get_stock_quote: x"
        )
        # Sticky: still pointing at the FIRST fatal event.
        self.assertEqual(second_trigger["fatal_trigger_step_index"], 2)
        self.assertEqual(second_trigger["fatal_streak_start_step_index"], 0)
        self.assertEqual(second_trigger["consecutive_tool_errors_after_step"], 3)


class TestAccumulatorLoopCounters(unittest.TestCase):
    def test_repeats_track_name_and_input_hash(self) -> None:
        acc = TrajectoryAccumulator()
        a = _step(acc, "get_stock_quote", "success", arguments={"ticker": "AAPL"})
        b = _step(acc, "get_stock_quote", "success", arguments={"ticker": "AAPL"})
        c = _step(acc, "get_stock_quote", "success", arguments={"ticker": "MSFT"})
        # First call sees zero prior repeats; subsequent see prior count.
        self.assertEqual(a["repeated_tool_call_count"], 0)
        self.assertEqual(a["same_input_hash_repeats"], 0)
        self.assertEqual(b["repeated_tool_call_count"], 1)
        self.assertEqual(b["same_input_hash_repeats"], 1)
        # Third call shares tool_name but different ticker → only name repeats.
        self.assertEqual(c["repeated_tool_call_count"], 2)
        self.assertEqual(c["same_input_hash_repeats"], 0)


# ── Summary helper ──────────────────────────────────────────────────────────


class TestSummarizeTrace(unittest.TestCase):
    def test_no_fatal_yields_full_prefix(self) -> None:
        acc = TrajectoryAccumulator(trace_id="t-1", session_id="s-1", message_id="m-1")
        _step(acc, "get_stock_quote", "success", arguments={"ticker": "AAPL"})
        _step(acc, "get_market_news", "success", arguments={"query": "iran"})
        steps = [r for r in acc._steps] if hasattr(acc, "_steps") else []
        # We don't expose _steps; build manually from accumulator's emitted rows.
        # Use summarize_trace directly on the recorded rows from this test.
        rows: list[dict] = []
        acc2 = TrajectoryAccumulator()
        rows.append(_step(acc2, "get_stock_quote", "success", arguments={"ticker": "AAPL"}))
        rows.append(_step(acc2, "get_market_news", "success", arguments={"query": "iran"}))
        s = summarize_trace(rows, quote_card_tickers=[])
        self.assertEqual(s["trajectory_step_count"], 2)
        self.assertFalse(s["fatal_detected"])
        self.assertEqual(s["valid_prefix_steps"], 2)
        self.assertIn("quote", s["tool_families_used"])
        self.assertIn("news", s["tool_families_used"])

    def test_quote_card_prefetch_credits_quote_family(self) -> None:
        rows: list[dict] = []
        acc = TrajectoryAccumulator()
        rows.append(_step(acc, "get_market_news", "success", arguments={"query": "fed"}))
        s = summarize_trace(rows, quote_card_tickers=["AAPL"])
        # quote (from prefetch) appears even though no quote tool was called.
        self.assertIn("quote", s["tool_families_used"])
        self.assertIn("news", s["tool_families_used"])

    def test_fatal_summary_uses_streak_start_for_valid_prefix(self) -> None:
        rows: list[dict] = []
        acc = TrajectoryAccumulator()
        rows.append(_step(acc, "get_stock_quote", "success", arguments={"ticker": "AAPL"}))
        rows.append(_step(acc, "get_market_news", "success", arguments={"query": "fed"}))
        rows.append(_step(acc, "get_top_movers", "error", result="Error executing get_top_movers: x"))
        rows.append(_step(acc, "get_top_movers", "error", result="Error executing get_top_movers: x"))
        rows.append(_step(acc, "get_top_movers", "error", result="Error executing get_top_movers: x"))
        s = summarize_trace(rows)
        self.assertTrue(s["fatal_detected"])
        # Streak started at step 2; valid prefix is 2 (steps 0-1 are clean).
        self.assertEqual(s["fatal_streak_start_step_index"], 2)
        self.assertEqual(s["fatal_trigger_step_index"], 4)
        self.assertEqual(s["valid_prefix_steps"], 2)


# ── Persistence cap ─────────────────────────────────────────────────────────


class TestHandoffPayload(unittest.TestCase):
    def test_payload_uses_trajectory_schema_version(self) -> None:
        rows: list[dict] = []
        acc = TrajectoryAccumulator(trace_id="t-1", session_id="s-1", message_id="m-1")
        rows.append(_step(acc, "get_stock_quote", "success", arguments={"ticker": "AAPL"}))
        s = summarize_trace(rows, trace_id="t-1", session_id="s-1", message_id="m-1")
        ev = {"schema_version": 3, "confidence_band": "high", "abstain_reason": None}
        p = build_handoff_payload(summary=s, trajectory_steps=rows, evidence_contract=ev)
        self.assertEqual(p["schema_version"], TRAJECTORY_SCHEMA_VERSION)
        self.assertEqual(p["trace_id"], "t-1")
        self.assertEqual(p["tools_called"], ["get_stock_quote"])
        self.assertEqual(p["tool_families_used"], ["quote"])
        # Persistence redacts arguments + caps observation length.
        for step in p["trajectory_steps"]:
            self.assertNotIn("arguments", step)
            self.assertLessEqual(len(step.get("tool_observation_summary") or ""), DB_OBSERVATION_CAP)

    def test_payload_truncates_under_cap(self) -> None:
        # Construct a wide trace whose unconstrained payload would exceed the cap.
        rows: list[dict] = []
        acc = TrajectoryAccumulator()
        big_obs = "x" * 5000
        for i in range(20):
            rows.append(
                _step(
                    acc,
                    "get_market_news",
                    "success",
                    arguments={"query": f"q{i}"},
                    result=big_obs,
                )
            )
        s = summarize_trace(rows)
        ev = {"schema_version": 3, "confidence_band": "medium", "abstain_reason": None}
        p = build_handoff_payload(summary=s, trajectory_steps=rows, evidence_contract=ev)
        blob = json.dumps(p, default=str)
        self.assertLessEqual(len(blob), HANDOFF_PAYLOAD_BUDGET)


class TestTrajectoryStepsForSse(unittest.TestCase):
    def test_arguments_stripped_for_sse(self) -> None:
        acc = TrajectoryAccumulator()
        rows = [
            _step(acc, "get_stock_quote", "success", arguments={"ticker": "AAPL"}),
            _step(acc, "get_market_news", "empty", arguments={"query": "X"},
                  result="No live news headlines available at this time."),
        ]
        sse_rows = trajectory_steps_for_sse(rows)
        for r in sse_rows:
            self.assertNotIn("arguments", r)
            obs = r.get("tool_observation_summary") or ""
            self.assertLessEqual(len(obs), PUBLIC_OBSERVATION_CAP)


class TestEventConstants(unittest.TestCase):
    def test_event_chat_trace_constant_matches_handoff_naming(self) -> None:
        self.assertEqual(EVENT_CHAT_TRACE, "handoff_chat_trace")


# ── Phase E0/E2 — skill, phase, namespace fields on rows + summary ─────────


class TestPhaseE0FieldsOnRow(unittest.TestCase):
    def test_default_phase_is_investigation(self) -> None:
        row = build_trajectory_step(
            TrajectoryStepInputs(
                step_index=0,
                tool_name="get_stock_quote",
                arguments={"ticker": "AAPL"},
                result="**AAPL** — Full Quote Snapshot",
                outcome="success",
                latency_ms=1,
            )
        )
        self.assertEqual(row["phase"], "investigation")
        self.assertEqual(row["memory_namespace"], "market_data")
        self.assertEqual(row["retrieval_mode"], "live_fetch")
        self.assertEqual(row["artifact_type"], "market_quote")
        self.assertIsNone(row["skill_name"])
        self.assertIsNone(row["skill_tier"])
        # Typed source_refs_v2 carries family + artifact_type.
        v2 = row.get("source_refs_v2")
        self.assertIsInstance(v2, list)
        self.assertEqual(len(v2), 1)
        self.assertEqual(v2[0]["source_family"], "quote")
        self.assertEqual(v2[0]["artifact_type"], "market_quote")

    def test_skill_propagates_via_input(self) -> None:
        row = build_trajectory_step(
            TrajectoryStepInputs(
                step_index=0,
                tool_name="get_stock_quote",
                arguments={"ticker": "AAPL"},
                result="**AAPL** — Full Quote Snapshot",
                outcome="success",
                latency_ms=1,
                skill_name=SkillName.QUICK_QUOTE,
                skill_tier=SkillTier.SIMPLE,
                phase=StepPhase.INVESTIGATION,
            )
        )
        self.assertEqual(row["skill_name"], "quick_quote")
        self.assertEqual(row["skill_tier"], "simple")


class TestAccumulatorSkillTagging(unittest.TestCase):
    def test_set_skill_threads_into_subsequent_rows(self) -> None:
        acc = TrajectoryAccumulator()
        acc.set_skill(skill_name=SkillName.NEWS_CONTEXT, skill_tier=SkillTier.MEDIUM)
        row = _step(acc, "get_market_news", "success", arguments={"query": "fed"})
        self.assertEqual(row["skill_name"], "news_context")
        self.assertEqual(row["skill_tier"], "medium")


class TestSummarizeTraceE0Fields(unittest.TestCase):
    def test_summary_exposes_phase_and_namespace_metadata(self) -> None:
        rows: list[dict] = []
        acc = TrajectoryAccumulator()
        acc.set_skill(
            skill_name=SkillName.FULL_CHAIN_ANALYSIS, skill_tier=SkillTier.FULL_CHAIN
        )
        rows.append(_step(acc, "get_stock_quote", "success", arguments={"ticker": "NVDA"}))
        rows.append(_step(acc, "get_market_news", "success", arguments={"query": "ai chips"}))
        s = summarize_trace(
            rows,
            final_answer_text="NVDA is exposed to AI demand and recent news",
        )
        self.assertEqual(s["skill_name"], "full_chain_analysis")
        self.assertEqual(s["skill_tier"], "full_chain")
        self.assertIn("market_data", s["memory_namespaces_touched"])
        self.assertIn("news_rag", s["memory_namespaces_touched"])
        self.assertEqual(s["investigation_step_count"], 2)
        self.assertEqual(s["synthesis_step_index"], 2)
        # Final answer mentions NVDA → grounded.
        self.assertTrue(s["answer_grounded_to_investigation"])
        self.assertGreaterEqual(len(s["source_refs_v2_all"]), 2)
        self.assertIn("market_quote", s["artifact_types_used"])

    def test_no_steps_is_vacuously_grounded(self) -> None:
        s = summarize_trace([], final_answer_text="hello!")
        self.assertTrue(s["answer_grounded_to_investigation"])

    def test_unrelated_answer_is_not_grounded(self) -> None:
        rows: list[dict] = []
        acc = TrajectoryAccumulator()
        rows.append(
            _step(acc, "get_stock_quote", "success", arguments={"ticker": "NVDA"})
        )
        s = summarize_trace(
            rows, final_answer_text="lorem ipsum dolor sit amet, no symbols cited"
        )
        self.assertFalse(s["answer_grounded_to_investigation"])

    def test_phase_boundary_synthesis_index_equals_step_count(self) -> None:
        # Phase E2: synthesis_step_index is the index of the *first* synthesis
        # step — when investigation produced N steps and the assistant text
        # follows, the synthesis index lands at N.
        rows: list[dict] = []
        acc = TrajectoryAccumulator()
        rows.append(_step(acc, "get_stock_quote", "success", arguments={"ticker": "NVDA"}))
        rows.append(_step(acc, "get_market_news", "success", arguments={"query": "ai chips"}))
        rows.append(_step(acc, "get_stock_quote", "success", arguments={"ticker": "AAPL"}))
        s = summarize_trace(rows, final_answer_text="NVDA holds support; AAPL flat.")
        self.assertEqual(s["investigation_step_count"], 3)
        self.assertEqual(s["synthesis_step_index"], 3)

    def test_zero_step_turn_has_grounded_default_true(self) -> None:
        # A turn with no tool calls (e.g. small talk, "Thanks!") is vacuously
        # grounded so it doesn't drag down the answer-judge calibration.
        s = summarize_trace([], final_answer_text="thanks!")
        self.assertEqual(s["investigation_step_count"], 0)
        self.assertEqual(s["synthesis_step_index"], 0)
        self.assertTrue(s["answer_grounded_to_investigation"])

    def test_grounded_when_answer_cites_family_token(self) -> None:
        # The conservative grounding heuristic also accepts the family
        # token itself (e.g. mentioning "macro" in the answer credits a
        # macro-family investigation).
        rows: list[dict] = []
        acc = TrajectoryAccumulator()
        rows.append(
            _step(
                acc,
                "get_market_news",
                "success",
                arguments={"query": "macro outlook"},
            )
        )
        s = summarize_trace(
            rows,
            final_answer_text="Recent macro outlook suggests caution.",
        )
        self.assertTrue(s["answer_grounded_to_investigation"])

    def test_handoff_payload_carries_skill_and_phase(self) -> None:
        rows: list[dict] = []
        acc = TrajectoryAccumulator(trace_id="t-2", session_id="s-2", message_id="m-2")
        acc.set_skill(
            skill_name=SkillName.QUICK_QUOTE, skill_tier=SkillTier.SIMPLE
        )
        rows.append(_step(acc, "get_stock_quote", "success", arguments={"ticker": "AAPL"}))
        s = summarize_trace(
            rows,
            trace_id="t-2",
            session_id="s-2",
            message_id="m-2",
            final_answer_text="AAPL is at $200",
        )
        ev = {"schema_version": 4, "confidence_band": "high", "abstain_reason": None}
        p = build_handoff_payload(summary=s, trajectory_steps=rows, evidence_contract=ev)
        self.assertEqual(p["skill_name"], "quick_quote")
        self.assertEqual(p["skill_tier"], "simple")
        self.assertEqual(p["investigation_step_count"], 1)
        self.assertEqual(p["synthesis_step_index"], 1)
        self.assertTrue(p["answer_grounded_to_investigation"])


if __name__ == "__main__":
    unittest.main()
