"""
Phase D — pre-fatal trajectory export skeleton (gated, deferred).

These tests stay hermetic: they pass synthetic ``coral_handoff_events``
rows to the export helper so no writable DB is required. The CLI's gate
behavior is documented here so accidental scheduling cannot bypass the
calibration gate.
"""
import os
import unittest

from backend.eval.export_trajectories import (
    EXPORT_FLAG_ENV,
    _flag_enabled,
    consume_into_coral,
    export_pre_fatal_trajectories,
)


def _trace_event(
    *,
    event_id: int,
    trace_id: str,
    fatal: bool = False,
    fatal_streak_start: int | None = None,
    steps: list[dict] | None = None,
) -> dict:
    return {
        "id": event_id,
        "event_type": "handoff_chat_trace",
        "payload": {
            "trace_id": trace_id,
            "session_id": "s",
            "message_id": "m",
            "skill_name": "full_chain_analysis",
            "skill_tier": "full_chain",
            "fatal_detected": fatal,
            "fatal_streak_start_step_index": fatal_streak_start,
            "fatal_trigger_step_index": (
                None if not fatal else (fatal_streak_start or 0) + 2
            ),
            "valid_prefix_steps": (
                fatal_streak_start if (fatal and fatal_streak_start is not None)
                else len(steps or [])
            ),
            "synthesis_step_index": len(steps or []),
            "answer_grounded_to_investigation": True,
            "tool_families_used": ["quote", "news"],
            "tools_called": [s["tool_name"] for s in (steps or [])],
            "evidence_contract": {
                "schema_version": 3,
                "confidence_band": "medium",
                "abstain_reason": None,
            },
            "trajectory_steps": list(steps or []),
        },
    }


def _step(
    idx: int,
    name: str,
    family: str,
    *,
    status: str = "ok",
    quality: str = "useful",
    obs: str = "ok",
) -> dict:
    return {
        "step_index": idx,
        "tool_name": name,
        "tool_family": family,
        "phase": "investigation",
        "skill_name": "full_chain_analysis",
        "skill_tier": "full_chain",
        "execution_status": status,
        "evidence_quality": quality,
        "source_refs": [f"{family}:X"],
        "model_action_summary": f"Called {name}",
        "tool_observation_summary": obs,
        "is_observation_masked": True,
    }


class TestExportPreFatalTrajectories(unittest.TestCase):
    def test_non_fatal_includes_all_steps(self) -> None:
        ev = _trace_event(
            event_id=1,
            trace_id="t-1",
            fatal=False,
            steps=[
                _step(0, "get_stock_quote", "quote"),
                _step(1, "get_market_news", "news"),
            ],
        )
        records = export_pre_fatal_trajectories([ev])
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]["trajectory_prefix"]), 2)
        self.assertFalse(records[0]["fatal_detected"])
        self.assertEqual(records[0]["skill_name"], "full_chain_analysis")
        self.assertEqual(records[0]["synthesis_step_index"], 2)

    def test_fatal_drops_post_fatal_steps(self) -> None:
        ev = _trace_event(
            event_id=2,
            trace_id="t-2",
            fatal=True,
            fatal_streak_start=2,
            steps=[
                _step(0, "get_stock_quote", "quote"),
                _step(1, "get_market_news", "news"),
                _step(2, "get_top_movers", "screener", status="error", quality="unknown"),
                _step(3, "get_top_movers", "screener", status="error", quality="unknown"),
                _step(4, "get_top_movers", "screener", status="error", quality="unknown"),
            ],
        )
        records = export_pre_fatal_trajectories([ev])
        self.assertEqual(len(records), 1)
        prefix = records[0]["trajectory_prefix"]
        # Steps 0 and 1 are valid prefix; 2..4 are post-fatal and dropped.
        self.assertEqual([s["step_index"] for s in prefix], [0, 1])
        self.assertTrue(records[0]["fatal_detected"])

    def test_filters_other_event_types(self) -> None:
        good = _trace_event(
            event_id=3,
            trace_id="t-3",
            fatal=False,
            steps=[_step(0, "get_stock_quote", "quote")],
        )
        unrelated = {
            "id": 4,
            "event_type": "handoff_swarm_trace",
            "payload": {"trajectory_steps": [{"step_index": 0}]},
        }
        records = export_pre_fatal_trajectories([good, unrelated])
        self.assertEqual([r["event_id"] for r in records], [3])

    def test_empty_prefix_filtered_by_default(self) -> None:
        ev = _trace_event(
            event_id=5,
            trace_id="t-5",
            fatal=True,
            fatal_streak_start=0,  # everything is post-fatal
            steps=[
                _step(0, "x", "unknown", status="error", quality="unknown"),
                _step(1, "x", "unknown", status="error", quality="unknown"),
                _step(2, "x", "unknown", status="error", quality="unknown"),
            ],
        )
        # Default: drop empty-prefix records.
        records = export_pre_fatal_trajectories([ev])
        self.assertEqual(records, [])
        # Opt-in: keep them for inspection.
        records2 = export_pre_fatal_trajectories([ev], only_with_steps=False)
        self.assertEqual(len(records2), 1)
        self.assertEqual(records2[0]["trajectory_prefix"], [])

    def test_record_carries_separation_flags(self) -> None:
        ev = _trace_event(
            event_id=6,
            trace_id="t-6",
            fatal=False,
            steps=[_step(0, "get_stock_quote", "quote", obs="**AAPL** quote")],
        )
        records = export_pre_fatal_trajectories([ev])
        prefix = records[0]["trajectory_prefix"]
        # Plan: separation between model-authored content and exogenous
        # tool returns must be preserved through the export.
        self.assertIn("model_action_summary", prefix[0])
        self.assertIn("tool_observation_summary", prefix[0])
        self.assertTrue(prefix[0]["is_observation_masked"])
        self.assertEqual(prefix[0]["phase"], "investigation")


class TestExportGating(unittest.TestCase):
    def test_export_flag_default_off(self) -> None:
        os.environ.pop(EXPORT_FLAG_ENV, None)
        self.assertFalse(_flag_enabled())

    def test_export_flag_on_when_env_set(self) -> None:
        os.environ[EXPORT_FLAG_ENV] = "1"
        try:
            self.assertTrue(_flag_enabled())
        finally:
            os.environ.pop(EXPORT_FLAG_ENV, None)

    def test_consume_into_coral_noop_until_calibration_gate(self) -> None:
        # Calibration gate is OFF by default; consume_into_coral must be
        # an explicit no-op so accidental scheduling can't ingest.
        os.environ.pop("CORAL_INGEST_JUDGE_SCORES", None)
        n = consume_into_coral([{"event_id": 1}])
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
