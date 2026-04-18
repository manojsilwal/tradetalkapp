"""
Universal Execution-Contract Validator (Harness Engineering §1).

Every RSPL ``ResourceRecord`` may carry a ``schema`` (LLM-facing output shape)
and a ``fallback`` (structured default when the schema fails). Today those are
only advisory — the LLM is politely asked via the prompt body to respect the
shape, and nothing enforces it at runtime. Different models interpret the
prompt differently, so swapping the underlying LLM silently degrades output
quality in ways that surface days later in reflections.

This module closes that gap. It validates any LLM-derived dict against the
resource's declared ``schema`` (a JSON-Schema subset — see ``validate`` below
for the exact keyword set), records every violation so later analyses can
correlate "which model/prompt drifts" with "which outcomes degraded", and
coerces to the resource's ``fallback`` when the output is unsalvageable so
callers never observe a broken contract.

Design constraints:

* **Never raises.** Validation failures return structured violations and a
  best-effort coerced payload. Inference must not be interrupted by validator
  bugs — this is defense-in-depth, not a gate.
* **Pluggable sink.** Violations are emitted to a ``violation_sink`` callable
  that defaults to logging. Phase 2 of the moat plan wires this sink to
  ``decision_ledger.record_violation`` so violations land in the
  ``contract_violations`` table for SQL-queryable model-drift analytics.
* **Subset JSON Schema.** We support the keywords actually used by
  ``backend/resources/prompts/*.yaml``: ``type``, ``required``, ``properties``,
  ``enum``, ``minimum``, ``maximum``, ``items``, ``additionalProperties``.
  Adding more is a one-line addition in :func:`_validate_node`.
* **Feature flag.** ``CONTRACT_VALIDATOR_ENABLE`` (default on). When off the
  validator becomes a passthrough that returns the input unchanged with an
  empty violation list.

Phase A (this change) integrates the validator after ``_parse_json_response``
in :mod:`backend.llm_client`. Phase B (after :mod:`backend.decision_ledger`
lands) replaces the logging sink with a ledger writer.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Feature flag ─────────────────────────────────────────────────────────────


def validator_enabled() -> bool:
    """Master switch — off disables validation globally (passthrough mode)."""
    return (os.environ.get("CONTRACT_VALIDATOR_ENABLE", "1").strip() or "1") != "0"


# ── Domain types ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContractViolation:
    """
    One validation failure against a resource's declared schema.

    ``path`` uses dot-notation for objects and ``[i]`` for arrays, so a nested
    failure reads like ``scenes[0].duration``. ``code`` is a stable machine-
    readable identifier so BI queries can group by drift type without regex
    on the message.
    """

    resource_name: str
    resource_version: str
    path: str
    code: str
    message: str
    observed_type: str = ""
    expected: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_name": self.resource_name,
            "resource_version": self.resource_version,
            "path": self.path,
            "code": self.code,
            "message": self.message,
            "observed_type": self.observed_type,
            "expected": self.expected,
        }


# ── Core JSON-Schema-subset validator ────────────────────────────────────────


_JSON_TYPE_ALIASES: Dict[str, Tuple[type, ...]] = {
    "object": (dict,),
    "array": (list, tuple),
    "string": (str,),
    # bool is a subclass of int — exclude it from 'number' / 'integer' below.
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "null": (type(None),),
}


def _observed_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _matches_type(value: Any, expected: str) -> bool:
    expected = (expected or "").strip().lower()
    allowed = _JSON_TYPE_ALIASES.get(expected)
    if allowed is None:
        # Unknown type in schema — treat as permissive (don't falsely fail).
        return True
    if expected in ("number", "integer") and isinstance(value, bool):
        return False
    return isinstance(value, allowed)


def _append(
    out: List[ContractViolation],
    *,
    resource_name: str,
    resource_version: str,
    path: str,
    code: str,
    message: str,
    observed: Any = None,
    expected: str = "",
) -> None:
    out.append(
        ContractViolation(
            resource_name=resource_name,
            resource_version=resource_version,
            path=path or "$",
            code=code,
            message=message,
            observed_type=_observed_type(observed) if observed is not None or code != "missing_required" else "",
            expected=expected,
        )
    )


def _validate_node(
    value: Any,
    schema: Optional[Dict[str, Any]],
    *,
    path: str,
    resource_name: str,
    resource_version: str,
    out: List[ContractViolation],
) -> None:
    """Walk ``value`` against ``schema`` recursively, appending violations."""
    if not isinstance(schema, dict) or not schema:
        return

    expected_type = schema.get("type")
    if isinstance(expected_type, str) and expected_type:
        if not _matches_type(value, expected_type):
            _append(
                out,
                resource_name=resource_name,
                resource_version=resource_version,
                path=path,
                code="type_mismatch",
                message=f"expected type '{expected_type}', got '{_observed_type(value)}'",
                observed=value,
                expected=expected_type,
            )
            # Short-circuit deeper checks — further keywords assume right type.
            return
    elif isinstance(expected_type, list) and expected_type:
        if not any(_matches_type(value, t) for t in expected_type):
            _append(
                out,
                resource_name=resource_name,
                resource_version=resource_version,
                path=path,
                code="type_mismatch",
                message=f"expected one of {expected_type}, got '{_observed_type(value)}'",
                observed=value,
                expected=",".join(expected_type),
            )
            return

    # enum
    if "enum" in schema:
        allowed = schema["enum"] or []
        if value not in allowed:
            _append(
                out,
                resource_name=resource_name,
                resource_version=resource_version,
                path=path,
                code="enum_mismatch",
                message=f"value not in allowed set of {len(allowed)} options",
                observed=value,
                expected=",".join(str(x) for x in allowed),
            )

    # number bounds
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            _append(
                out,
                resource_name=resource_name,
                resource_version=resource_version,
                path=path,
                code="below_minimum",
                message=f"value {value} < minimum {schema['minimum']}",
                observed=value,
                expected=f">={schema['minimum']}",
            )
        if "maximum" in schema and value > schema["maximum"]:
            _append(
                out,
                resource_name=resource_name,
                resource_version=resource_version,
                path=path,
                code="above_maximum",
                message=f"value {value} > maximum {schema['maximum']}",
                observed=value,
                expected=f"<={schema['maximum']}",
            )

    # object properties + required
    if isinstance(value, dict):
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                _append(
                    out,
                    resource_name=resource_name,
                    resource_version=resource_version,
                    path=f"{path}.{key}" if path else key,
                    code="missing_required",
                    message=f"required key '{key}' is missing",
                    expected="present",
                )
        properties = schema.get("properties") or {}
        if isinstance(properties, dict):
            for key, subschema in properties.items():
                if key in value:
                    _validate_node(
                        value[key],
                        subschema,
                        path=f"{path}.{key}" if path else key,
                        resource_name=resource_name,
                        resource_version=resource_version,
                        out=out,
                    )
        # additionalProperties: False -> extras are violations; default permissive.
        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            extras = [k for k in value.keys() if k not in properties]
            for extra in extras:
                _append(
                    out,
                    resource_name=resource_name,
                    resource_version=resource_version,
                    path=f"{path}.{extra}" if path else extra,
                    code="additional_property",
                    message=f"unexpected key '{extra}' (additionalProperties=false)",
                    observed=value[extra],
                    expected="absent",
                )

    # array items
    if isinstance(value, (list, tuple)) and isinstance(schema.get("items"), dict):
        sub = schema["items"]
        for idx, item in enumerate(value):
            _validate_node(
                item,
                sub,
                path=f"{path}[{idx}]",
                resource_name=resource_name,
                resource_version=resource_version,
                out=out,
            )


def validate(
    data: Any,
    schema: Optional[Dict[str, Any]],
    *,
    resource_name: str = "",
    resource_version: str = "",
) -> List[ContractViolation]:
    """
    Validate ``data`` against ``schema``; return the list of violations.

    Empty list == valid. A ``None`` or empty schema is treated as "no contract
    declared" and always returns ``[]`` (permissive by design — the vast
    majority of the existing surface has no schema yet).
    """
    if not isinstance(schema, dict) or not schema:
        return []
    out: List[ContractViolation] = []
    try:
        _validate_node(
            data,
            schema,
            path="",
            resource_name=resource_name or "",
            resource_version=resource_version or "",
            out=out,
        )
    except Exception as e:  # never let validator bugs block inference
        logger.warning(
            "[ContractValidator] validate() crashed for %s@%s: %s",
            resource_name, resource_version, e,
        )
        return []
    return out


# ── High-level validator with pluggable sink ─────────────────────────────────


ViolationSink = Callable[[ContractViolation, Dict[str, Any]], None]


def _default_sink(v: ContractViolation, context: Dict[str, Any]) -> None:
    """Log-only sink. Replaced in Phase 2 with a decision_ledger writer."""
    logger.warning(
        "[ContractValidator] violation resource=%s@%s path=%s code=%s msg=%s ctx=%s",
        v.resource_name, v.resource_version, v.path, v.code, v.message,
        {k: context[k] for k in ("model", "role") if k in context},
    )


class ContractValidator:
    """
    Process-wide validator used by :mod:`backend.llm_client`.

    A ``ResourceRecord`` is looked up by role name via the RSPL registry to
    source ``schema`` + ``fallback``. Callers that already have the record in
    hand (e.g. SEPL override paths) can pass it directly via
    :meth:`validate_result` to skip the registry hit.
    """

    def __init__(self, sink: Optional[ViolationSink] = None) -> None:
        self._sink: ViolationSink = sink or _default_sink
        self._lock = threading.Lock()
        self._stats: Dict[str, int] = {
            "checked": 0,
            "passed": 0,
            "violated": 0,
            "coerced": 0,
        }

    # ── sink control (Phase 2 uses this to swap in ledger writer) ────────

    def set_sink(self, sink: ViolationSink) -> None:
        with self._lock:
            self._sink = sink or _default_sink

    def stats_snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._stats)

    # ── core API ─────────────────────────────────────────────────────────

    def validate_result(
        self,
        result: Any,
        *,
        role: str,
        schema: Optional[Dict[str, Any]],
        fallback: Optional[Dict[str, Any]] = None,
        version: str = "",
        model: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, List[ContractViolation], bool]:
        """
        Validate ``result``; optionally coerce to ``fallback`` if fatal.

        Returns ``(payload, violations, coerced)`` where:

        * ``payload`` is ``result`` when valid (or when validation is disabled
          / no schema declared), otherwise ``fallback`` when a non-empty
          fallback was provided and the violations include a fatal class
          (missing required / wrong top-level type).
        * ``violations`` is the full list — never truncated.
        * ``coerced`` is ``True`` iff the payload returned is the fallback.

        Side-effects: each violation is forwarded to the configured sink with
        a context dict ``{role, model, version, ...context}``.
        """
        self._bump("checked")
        if not validator_enabled() or not isinstance(schema, dict) or not schema:
            self._bump("passed")
            return result, [], False

        violations = validate(
            result, schema, resource_name=role, resource_version=version
        )
        if not violations:
            self._bump("passed")
            return result, [], False

        ctx = {
            "role": role,
            "model": model,
            "version": version,
            "ts": time.time(),
        }
        if context:
            ctx.update(context)
        for v in violations:
            try:
                self._sink(v, ctx)
            except Exception as e:  # sink bug must not break inference
                logger.warning("[ContractValidator] sink raised: %s", e)

        self._bump("violated")

        fatal = any(
            v.code in ("missing_required", "type_mismatch") and v.path in ("$", "")
            for v in violations
        ) or any(v.code == "missing_required" for v in violations)

        if fatal and isinstance(fallback, dict) and fallback:
            self._bump("coerced")
            return dict(fallback), violations, True
        return result, violations, False

    def _bump(self, key: str) -> None:
        with self._lock:
            self._stats[key] = self._stats.get(key, 0) + 1


# ── Module-level singleton ───────────────────────────────────────────────────


_singleton: Optional[ContractValidator] = None
_singleton_lock = threading.Lock()


def get_contract_validator() -> ContractValidator:
    """Return the process-wide validator, creating it on first access."""
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = ContractValidator()
    return _singleton


def _reset_singleton_for_tests() -> None:
    """Test-only helper — drops the module-level cache."""
    global _singleton
    with _singleton_lock:
        _singleton = None
