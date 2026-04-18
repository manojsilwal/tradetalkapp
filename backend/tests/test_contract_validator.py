"""
Unit tests for :mod:`backend.contract_validator`.

Covers the JSON-Schema subset actually used by ``backend/resources/prompts/*.yaml``:
``type``, ``required``, ``properties``, ``enum``, ``minimum``, ``maximum``,
``items``, ``additionalProperties``. Also covers the pluggable violation sink
and the coerce-to-fallback path used by ``LLMClient._provider_generate``.
"""
from __future__ import annotations

import os
import unittest

from backend import contract_validator as cv


class _ValidatorBase(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["CONTRACT_VALIDATOR_ENABLE"] = "1"
        cv._reset_singleton_for_tests()

    def tearDown(self) -> None:
        cv._reset_singleton_for_tests()


# ── Pure validate() ──────────────────────────────────────────────────────────


class TestValidateTopLevelShape(_ValidatorBase):
    SCHEMA = {
        "type": "object",
        "required": ["headline", "key_points", "confidence"],
        "properties": {
            "headline": {"type": "string"},
            "key_points": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }

    def test_valid_payload_has_no_violations(self) -> None:
        ok = {"headline": "X", "key_points": ["a", "b"], "confidence": 0.7}
        self.assertEqual(cv.validate(ok, self.SCHEMA), [])

    def test_missing_required_is_flagged(self) -> None:
        v = cv.validate({"headline": "X"}, self.SCHEMA)
        codes = {x.code for x in v}
        paths = {x.path for x in v}
        self.assertIn("missing_required", codes)
        self.assertIn("key_points", paths)
        self.assertIn("confidence", paths)

    def test_wrong_type_is_flagged(self) -> None:
        v = cv.validate({"headline": 42, "key_points": [], "confidence": 0.5}, self.SCHEMA)
        self.assertTrue(any(x.code == "type_mismatch" and x.path == "headline" for x in v))

    def test_out_of_range_number(self) -> None:
        v = cv.validate({"headline": "x", "key_points": [], "confidence": 1.5}, self.SCHEMA)
        self.assertTrue(any(x.code == "above_maximum" for x in v))

    def test_array_item_shape_recurses(self) -> None:
        v = cv.validate(
            {"headline": "x", "key_points": ["ok", 99], "confidence": 0.1}, self.SCHEMA
        )
        self.assertTrue(any(x.code == "type_mismatch" and x.path == "key_points[1]" for x in v))


class TestValidateEnum(_ValidatorBase):
    SCHEMA = {
        "type": "object",
        "required": ["verdict"],
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["STRONG BUY", "BUY", "NEUTRAL", "SELL", "STRONG SELL"],
            }
        },
    }

    def test_valid_enum_value_passes(self) -> None:
        self.assertEqual(cv.validate({"verdict": "BUY"}, self.SCHEMA), [])

    def test_out_of_enum_value_is_flagged(self) -> None:
        v = cv.validate({"verdict": "maybe"}, self.SCHEMA)
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].code, "enum_mismatch")
        self.assertEqual(v[0].path, "verdict")


class TestValidateAdditionalProperties(_ValidatorBase):
    SCHEMA = {
        "type": "object",
        "properties": {"a": {"type": "integer"}},
        "additionalProperties": False,
    }

    def test_extra_keys_flagged_when_forbidden(self) -> None:
        v = cv.validate({"a": 1, "b": 2}, self.SCHEMA)
        codes = {x.code for x in v}
        self.assertIn("additional_property", codes)

    def test_extra_keys_allowed_by_default(self) -> None:
        schema = dict(self.SCHEMA)
        schema.pop("additionalProperties")
        self.assertEqual(cv.validate({"a": 1, "b": 2}, schema), [])


class TestValidateBooleanNotNumber(_ValidatorBase):
    def test_bool_is_not_a_number(self) -> None:
        schema = {"type": "object", "properties": {"n": {"type": "number"}}}
        v = cv.validate({"n": True}, schema)
        self.assertTrue(any(x.code == "type_mismatch" for x in v))


class TestValidateEmptyOrNoneSchema(_ValidatorBase):
    def test_none_schema_is_permissive(self) -> None:
        self.assertEqual(cv.validate({"anything": 1}, None), [])

    def test_empty_dict_schema_is_permissive(self) -> None:
        self.assertEqual(cv.validate({"anything": 1}, {}), [])


class TestValidateDoesNotRaiseOnGarbage(_ValidatorBase):
    def test_non_dict_schema_returns_empty(self) -> None:
        self.assertEqual(cv.validate({"x": 1}, "not-a-schema"), [])  # type: ignore[arg-type]

    def test_non_dict_value_against_object_schema(self) -> None:
        v = cv.validate("oops", {"type": "object", "required": ["a"]})
        self.assertTrue(any(x.code == "type_mismatch" for x in v))


# ── ContractValidator high-level API ─────────────────────────────────────────


class TestContractValidatorCoerceAndSink(_ValidatorBase):
    SCHEMA = {
        "type": "object",
        "required": ["headline", "confidence"],
        "properties": {
            "headline": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }
    FALLBACK = {"headline": "unavailable", "confidence": 0.0}

    def _capture_sink(self):
        captured: list = []

        def sink(v: cv.ContractViolation, ctx: dict) -> None:
            captured.append((v, dict(ctx)))

        return sink, captured

    def test_valid_result_passes_through(self) -> None:
        sink, captured = self._capture_sink()
        val = cv.ContractValidator(sink=sink)
        payload, viols, coerced = val.validate_result(
            {"headline": "x", "confidence": 0.5},
            role="bull",
            schema=self.SCHEMA,
            fallback=self.FALLBACK,
            version="1.0.0",
            model="test-model",
        )
        self.assertEqual(payload, {"headline": "x", "confidence": 0.5})
        self.assertEqual(viols, [])
        self.assertFalse(coerced)
        self.assertEqual(captured, [])
        stats = val.stats_snapshot()
        self.assertEqual(stats["checked"], 1)
        self.assertEqual(stats["passed"], 1)

    def test_missing_required_coerces_to_fallback(self) -> None:
        sink, captured = self._capture_sink()
        val = cv.ContractValidator(sink=sink)
        payload, viols, coerced = val.validate_result(
            {"headline": "x"},  # confidence missing
            role="bull",
            schema=self.SCHEMA,
            fallback=self.FALLBACK,
            version="1.0.0",
            model="test-model",
        )
        self.assertEqual(payload, self.FALLBACK)
        self.assertTrue(coerced)
        self.assertTrue(any(v.code == "missing_required" for v in viols))
        self.assertGreaterEqual(len(captured), 1)
        # Context carries role + model + version
        _, ctx = captured[0]
        self.assertEqual(ctx["role"], "bull")
        self.assertEqual(ctx["model"], "test-model")
        self.assertEqual(ctx["version"], "1.0.0")
        stats = val.stats_snapshot()
        self.assertEqual(stats["violated"], 1)
        self.assertEqual(stats["coerced"], 1)

    def test_non_fatal_violation_keeps_result(self) -> None:
        sink, captured = self._capture_sink()
        val = cv.ContractValidator(sink=sink)
        # All required present; only a range violation (not fatal per policy).
        payload, viols, coerced = val.validate_result(
            {"headline": "x", "confidence": 1.7},
            role="bull",
            schema=self.SCHEMA,
            fallback=self.FALLBACK,
            version="1.0.0",
            model="test-model",
        )
        self.assertFalse(coerced)
        self.assertEqual(payload, {"headline": "x", "confidence": 1.7})
        self.assertTrue(any(v.code == "above_maximum" for v in viols))
        # But the violation still reached the sink so BI can see it.
        self.assertGreaterEqual(len(captured), 1)

    def test_no_fallback_means_no_coercion(self) -> None:
        sink, _captured = self._capture_sink()
        val = cv.ContractValidator(sink=sink)
        payload, viols, coerced = val.validate_result(
            {"headline": "x"},
            role="bull",
            schema=self.SCHEMA,
            fallback=None,
            version="1.0.0",
            model="test-model",
        )
        self.assertFalse(coerced)
        self.assertEqual(payload, {"headline": "x"})
        self.assertTrue(viols)

    def test_sink_exception_does_not_break_validation(self) -> None:
        def angry_sink(*_args, **_kw):
            raise RuntimeError("boom")

        val = cv.ContractValidator(sink=angry_sink)
        payload, viols, coerced = val.validate_result(
            {"headline": "x"},  # missing confidence
            role="bull",
            schema=self.SCHEMA,
            fallback=self.FALLBACK,
            version="1.0.0",
            model="test-model",
        )
        self.assertTrue(coerced)
        self.assertTrue(viols)
        self.assertEqual(payload, self.FALLBACK)

    def test_feature_flag_off_is_passthrough(self) -> None:
        os.environ["CONTRACT_VALIDATOR_ENABLE"] = "0"
        try:
            sink, captured = self._capture_sink()
            val = cv.ContractValidator(sink=sink)
            payload, viols, coerced = val.validate_result(
                {"headline": "x"},  # missing confidence
                role="bull",
                schema=self.SCHEMA,
                fallback=self.FALLBACK,
                version="1.0.0",
                model="test-model",
            )
            self.assertEqual(payload, {"headline": "x"})
            self.assertEqual(viols, [])
            self.assertFalse(coerced)
            self.assertEqual(captured, [])
        finally:
            os.environ["CONTRACT_VALIDATOR_ENABLE"] = "1"

    def test_set_sink_replaces_previous_sink(self) -> None:
        original_captured: list = []
        new_captured: list = []

        def first(v, ctx):
            original_captured.append(v)

        def second(v, ctx):
            new_captured.append(v)

        val = cv.ContractValidator(sink=first)
        val.set_sink(second)
        val.validate_result(
            {"headline": "x"},
            role="bull",
            schema=self.SCHEMA,
            fallback=self.FALLBACK,
            version="1.0.0",
            model="test-model",
        )
        self.assertEqual(original_captured, [])
        self.assertGreaterEqual(len(new_captured), 1)


class TestContractValidatorSingleton(_ValidatorBase):
    def test_returns_same_instance(self) -> None:
        a = cv.get_contract_validator()
        b = cv.get_contract_validator()
        self.assertIs(a, b)

    def test_reset_gives_new_instance(self) -> None:
        a = cv.get_contract_validator()
        cv._reset_singleton_for_tests()
        b = cv.get_contract_validator()
        self.assertIsNot(a, b)


if __name__ == "__main__":
    unittest.main()
