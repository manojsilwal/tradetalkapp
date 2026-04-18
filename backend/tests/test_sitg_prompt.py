"""Schema-conformance golden test for the SITG (Skin-In-The-Game) scorer prompt.

This test guards three invariants for the Risk-Return-Ratio Step 2e persona:

1. The YAML resource (`backend/resources/prompts/sitg_scorer.yaml`) parses and
   exposes the required top-level fields (``name``, ``schema``, ``fallback``,
   ``body``) that the resource registry + LLMClient fallbacks rely on.
2. The declared ``fallback`` object validates against the declared JSON
   ``schema`` — so if the LLM is unavailable we still emit a schema-conformant
   payload downstream.
3. The eval-fixture file (`backend/resources/sepl_eval_fixtures/sitg_scorer.json`)
   exists, is a non-empty JSON array, and each entry has an ``input`` string
   mentioning a ticker. This is the corpus that SEPL will use for held-out
   evaluation if/when the persona is promoted to learnable evolution.

No network, no LLM calls — purely static validation.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft7Validator


BACKEND_DIR = Path(__file__).resolve().parent.parent
PROMPT_PATH = BACKEND_DIR / "resources" / "prompts" / "sitg_scorer.yaml"
FIXTURE_PATH = BACKEND_DIR / "resources" / "sepl_eval_fixtures" / "sitg_scorer.json"


class TestSitgPromptResource(unittest.TestCase):
    """Lightweight contract tests for the SITG persona resource."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = PROMPT_PATH.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.raw)

    def test_prompt_file_exists(self) -> None:
        self.assertTrue(PROMPT_PATH.exists(), f"missing: {PROMPT_PATH}")

    def test_top_level_keys_present(self) -> None:
        for key in ("name", "kind", "version", "schema", "fallback", "body"):
            self.assertIn(key, self.doc, f"sitg_scorer.yaml missing '{key}'")
        self.assertEqual(self.doc["name"], "sitg_scorer")
        self.assertEqual(self.doc["kind"], "prompt")

    def test_metadata_marks_risk_return_ratio_step_2e(self) -> None:
        meta = self.doc.get("metadata") or {}
        self.assertEqual(meta.get("methodology"), "risk_return_ratio")
        self.assertEqual(str(meta.get("step")), "2e")

    def test_schema_is_valid_json_schema(self) -> None:
        schema = self.doc["schema"]
        self.assertIsInstance(schema, dict)
        Draft7Validator.check_schema(schema)

    def test_schema_requires_canonical_fields(self) -> None:
        schema = self.doc["schema"]
        required = set(schema.get("required") or [])
        for must in ("sitg_score", "ceo_name", "archetype", "reasoning"):
            self.assertIn(must, required, f"schema.required missing '{must}'")
        props = schema.get("properties") or {}
        score_prop = props.get("sitg_score") or {}
        self.assertEqual(score_prop.get("type"), "number")
        self.assertEqual(score_prop.get("minimum"), 0)
        self.assertEqual(score_prop.get("maximum"), 10)

    def test_fallback_conforms_to_schema(self) -> None:
        schema = self.doc["schema"]
        fallback = self.doc["fallback"]
        errors = sorted(
            Draft7Validator(schema).iter_errors(fallback), key=lambda e: e.path
        )
        self.assertEqual(errors, [], f"fallback violates schema: {errors}")

    def test_body_mentions_sitg_and_rubric_anchors(self) -> None:
        body = self.doc["body"]
        self.assertIsInstance(body, str)
        for anchor in ("Skin-In-The-Game", "0-10", "Form 4"):
            self.assertIn(anchor, body, f"body missing rubric anchor: {anchor!r}")

    def test_body_ends_with_json_output_instruction(self) -> None:
        body = self.doc["body"].lower()
        self.assertIn("respond only with valid json", body)


class TestSitgFixture(unittest.TestCase):
    """Eval-fixture contract: used by SEPL for held-out evaluation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.entries = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_exists(self) -> None:
        self.assertTrue(FIXTURE_PATH.exists(), f"missing: {FIXTURE_PATH}")

    def test_fixture_is_non_empty_list(self) -> None:
        self.assertIsInstance(self.entries, list)
        self.assertGreaterEqual(
            len(self.entries), 2, "need at least 2 eval rows for SEPL"
        )

    def test_each_entry_has_input_with_ticker(self) -> None:
        for i, entry in enumerate(self.entries):
            with self.subTest(row=i):
                self.assertIsInstance(entry, dict)
                self.assertIn("input", entry)
                text = entry["input"]
                self.assertIsInstance(text, str)
                self.assertGreater(len(text), 50, "input too short to be realistic")
                self.assertRegex(
                    text,
                    r"Ticker:?\s*[A-Z]{1,6}",
                    "input should name a ticker (e.g. 'Ticker: NVDA')",
                )

    def test_fixture_exercises_diverse_archetypes(self) -> None:
        joined = " ".join(entry["input"] for entry in self.entries).lower()
        self.assertIn("founder", joined, "need at least one founder case")
        self.assertIn("hired", joined, "need at least one hired-CEO case")


if __name__ == "__main__":
    unittest.main()
