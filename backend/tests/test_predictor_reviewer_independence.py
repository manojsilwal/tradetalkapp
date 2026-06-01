import os
import unittest

import yaml


class TestReviewerRouting(unittest.TestCase):
    def test_predictor_yaml_has_synthesis_and_reviewer_sections(self) -> None:
        """Both synthesis and reviewer config sections exist with temperature + max_tokens."""
        root = os.path.join(os.path.dirname(__file__), "..", "..", "configs", "llm_routing.yaml")
        path = os.path.abspath(root)
        self.assertTrue(os.path.isfile(path), f"missing {path}")
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        pred = raw.get("predictor") or {}
        syn = pred.get("synthesis") or {}
        rev = pred.get("reviewer") or {}
        self.assertIn("temperature", syn)
        self.assertIn("max_tokens", syn)
        self.assertIn("temperature", rev)
        self.assertIn("max_tokens", rev)

    def test_predictor_cascade_imports(self) -> None:
        """Synthesizer and reviewer modules load and expose the expected async functions."""
        from backend.predictor.synthesizer import synthesize_narrative
        from backend.predictor.reviewer import review_narrative
        import asyncio
        self.assertTrue(asyncio.iscoroutinefunction(synthesize_narrative))
        self.assertTrue(asyncio.iscoroutinefunction(review_narrative))


if __name__ == "__main__":
    unittest.main()
