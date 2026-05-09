"""
Phase C1 + C2 — offline trajectory and answer judges.

These tests exercise the judge plumbing with stub LLMs so the harness stays
hermetic. Real LLM calls require ``TEVV_LLM_JUDGE=1`` and a configured
``OPENROUTER_API_KEY`` (covered by the calibration runs, not CI).

Locks:
  * Pinned prompt files load and contain the rubric tokens
    (``shortcut_collapse_detected``, ``grounding_ratio``).
  * Strict-JSON parser tolerates accidental markdown fences from LLMs.
  * Trajectory and answer normalizers clamp dimensions to [0, 1] and
    populate ``prompt_version`` / ``judge_model`` for every output.
  * The calibration gate defaults to OFF so CORAL can't ingest judge
    scores until the env flag is flipped.
"""
import os
import unittest

from backend.eval import judge as J


class _StubLLM:
    """Sync callable stub the judge accepts via the ``llm=`` parameter."""

    judge_model_name = "stub-deterministic-v1"

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.last_prompt: str = ""

    def __call__(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self.payload


class TestJudgePrompts(unittest.TestCase):
    def test_trajectory_prompt_loads_with_required_tokens(self) -> None:
        text = J.load_prompt("trajectory")
        self.assertIn("trajectory_quality_score", text)
        self.assertIn("shortcut_collapse_detected", text)
        self.assertIn("loop_or_repetition_detected", text)
        # Strict JSON instruction must be present so the parser can rely on it.
        self.assertIn("STRICT JSON", text)

    def test_answer_prompt_loads_with_required_tokens(self) -> None:
        text = J.load_prompt("answer")
        self.assertIn("answer_quality_score", text)
        self.assertIn("grounding_ratio", text)
        self.assertIn("unsupported_claim_count", text)
        self.assertIn("final_answer_evidence_refs", text)
        self.assertIn("STRICT JSON", text)

    def test_unknown_kind_raises(self) -> None:
        with self.assertRaises(ValueError):
            J.load_prompt("nonsense")

    def test_prompt_version_matches_constants(self) -> None:
        self.assertEqual(J.get_prompt_version("trajectory"), "trajectory_judge_v1")
        self.assertEqual(J.get_prompt_version("answer"), "answer_judge_v1")


class TestTrajectoryJudge(unittest.TestCase):
    def test_score_trajectory_normalizes_and_pins_metadata(self) -> None:
        payload = (
            '{"prompt_version": "trajectory_judge_v1",'
            '"trajectory_quality_score": 0.8,'
            '"dimensions": {"relevance": 0.9, "progression": 0.8,'
            ' "signal_to_noise": 0.7, "coverage": 0.8},'
            '"shortcut_collapse_detected": false,'
            '"loop_or_repetition_detected": false,'
            '"reasoning": "Three families covered with clean progression."}'
        )
        out = J.score_trajectory(
            user_message="Why is NVDA moving today?",
            tool_trace=[{"name": "get_stock_quote", "outcome": "success"}],
            evidence_contract={"schema_version": 3, "tool_families_used": ["quote"]},
            llm=_StubLLM(payload),
        )
        self.assertEqual(out["prompt_version"], "trajectory_judge_v1")
        self.assertEqual(out["judge_model"], "stub-deterministic-v1")
        self.assertAlmostEqual(out["trajectory_quality_score"], 0.8, places=3)
        self.assertEqual(set(out["dimensions"].keys()),
                         {"relevance", "progression", "signal_to_noise", "coverage"})
        self.assertFalse(out["shortcut_collapse_detected"])

    def test_score_trajectory_clamps_out_of_range(self) -> None:
        payload = (
            '{"trajectory_quality_score": 1.7,'
            '"dimensions": {"relevance": 1.4, "progression": -0.2,'
            ' "signal_to_noise": 0.5, "coverage": 0.5},'
            '"shortcut_collapse_detected": true,'
            '"loop_or_repetition_detected": true,'
            '"reasoning": "Single family"}'
        )
        out = J.score_trajectory(
            user_message="x",
            tool_trace=[],
            evidence_contract={},
            llm=_StubLLM(payload),
        )
        # Top-level score clamped to [0, 1].
        self.assertLessEqual(out["trajectory_quality_score"], 1.0)
        self.assertGreaterEqual(out["trajectory_quality_score"], 0.0)
        # Per-dimension clamp.
        self.assertEqual(out["dimensions"]["relevance"], 1.0)
        self.assertEqual(out["dimensions"]["progression"], 0.0)
        self.assertTrue(out["shortcut_collapse_detected"])

    def test_strict_json_tolerates_markdown_fence(self) -> None:
        payload = (
            "```json\n"
            '{"trajectory_quality_score": 0.5,'
            '"dimensions": {"relevance": 0.5, "progression": 0.5,'
            ' "signal_to_noise": 0.5, "coverage": 0.5},'
            '"shortcut_collapse_detected": false,'
            '"loop_or_repetition_detected": false}\n'
            "```"
        )
        out = J.score_trajectory(
            user_message="x",
            tool_trace=[],
            evidence_contract={},
            llm=_StubLLM(payload),
        )
        self.assertEqual(out["trajectory_quality_score"], 0.5)


class TestAnswerJudge(unittest.TestCase):
    def test_score_answer_propagates_grounding_signals(self) -> None:
        payload = (
            '{"answer_quality_score": 0.7,'
            '"dimensions": {"risk_awareness": 0.8, "grounding": 0.6},'
            '"grounding_ratio": 0.75,'
            '"unsupported_claim_count": 1,'
            '"final_answer_evidence_refs": ["quote:AAPL", "news:fed"],'
            '"reasoning": "One unsupported price quote in paragraph 2."}'
        )
        out = J.score_answer(
            user_message="What is AAPL doing?",
            final_answer="AAPL is up 1.2% on Fed-driven sentiment.",
            source_refs=["quote:AAPL", "news:fed"],
            evidence_contract={"schema_version": 3},
            llm=_StubLLM(payload),
        )
        self.assertEqual(out["prompt_version"], "answer_judge_v1")
        self.assertEqual(out["unsupported_claim_count"], 1)
        self.assertAlmostEqual(out["grounding_ratio"], 0.75, places=3)
        self.assertEqual(out["final_answer_evidence_refs"], ["quote:AAPL", "news:fed"])

    def test_score_answer_falls_back_to_dimension_mean(self) -> None:
        # When the model omits ``answer_quality_score`` the normalizer
        # synthesizes it from the dimension mean × grounding ratio.
        payload = (
            '{"dimensions": {"risk_awareness": 0.8, "grounding": 0.6},'
            '"grounding_ratio": 1.0,'
            '"unsupported_claim_count": 0,'
            '"final_answer_evidence_refs": []}'
        )
        out = J.score_answer(
            user_message="x",
            final_answer="y",
            source_refs=[],
            evidence_contract={},
            llm=_StubLLM(payload),
        )
        self.assertAlmostEqual(out["answer_quality_score"], 0.7, places=3)


class TestCalibrationGate(unittest.TestCase):
    def test_default_off_so_coral_does_not_ingest(self) -> None:
        os.environ.pop("CORAL_INGEST_JUDGE_SCORES", None)
        self.assertFalse(J.is_calibration_gate_satisfied())

    def test_explicit_opt_in(self) -> None:
        os.environ["CORAL_INGEST_JUDGE_SCORES"] = "1"
        try:
            self.assertTrue(J.is_calibration_gate_satisfied())
        finally:
            os.environ.pop("CORAL_INGEST_JUDGE_SCORES", None)


if __name__ == "__main__":
    unittest.main()
