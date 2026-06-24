"""Agent explanation guardrail: LLM numbers must trace to the model payload."""
import tempfile
import unittest

from backend.brain import agent_explainer as ax
from backend.brain import dataset, pipeline
from backend.brain.inference import InferenceEngine
from backend.brain.model_registry import ModelRegistry
from backend.brain.ports.local_adapters import LocalStorage


class TestGroundingPrimitives(unittest.TestCase):
    def test_grounded_simple(self):
        payload = {"outperform_probability": 0.62, "composite_score": 73.0}
        # 62% (from 0.62) and 73 both appear in payload -> grounded
        res = ax.verify_grounding("Probability is 62% with a composite of 73.", payload)
        self.assertTrue(res["grounded"])
        self.assertEqual(res["ungrounded_numbers"], [])

    def test_flags_invented_number(self):
        payload = {"outperform_probability": 0.55}
        res = ax.verify_grounding("This stock will surely return 250%.", payload)
        self.assertFalse(res["grounded"])
        self.assertIn(250.0, res["ungrounded_numbers"])

    def test_ignores_numbers_in_identifiers(self):
        payload = {"outperform_probability": 0.5, "model_version": "v1"}
        # "T075" and "v1" should not be parsed as numeric claims
        res = ax.verify_grounding("Ticker T075 scored by model v1 at 50%.", payload)
        self.assertTrue(res["grounded"])

    def test_ignores_hyphenated_word_numbers(self):
        payload = {"outperform_probability": 0.5}
        res = ax.verify_grounding("It has strong 6-month momentum at 50%.", payload)
        self.assertTrue(res["grounded"])


class TestExplanationIsGrounded(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.registry = ModelRegistry(root="artifacts", storage=LocalStorage(tmp))
        panel = dataset.synthetic_panel(n_tickers=60, n_periods=18, seed=5)
        pipeline.train_and_register(panel, "v1", self.registry,
                                    model_name="finrank-net", model_config={"epochs": 150})
        eng = InferenceEngine(self.registry, "finrank-net", "v1")
        self.contracts = eng.rank_universe(panel["rows"][:60], panel["tickers"][:60],
                                           as_of_date="2026-06-22")

    def test_generated_explanation_always_grounded(self):
        # The deterministic explainer must never emit an ungrounded number.
        for c in self.contracts[:10]:
            text = ax.generate_explanation(c)
            res = ax.verify_grounding(text, c)
            self.assertTrue(res["grounded"],
                            msg=f"ungrounded {res['ungrounded_numbers']} in: {text}")

    def test_explanation_mentions_disclaimer(self):
        text = ax.generate_explanation(self.contracts[0])
        self.assertIn("Not financial advice", text)


if __name__ == "__main__":
    unittest.main()
