"""
Dual-read helper for RSPL TOOL resources (Phase C1).

Bridges the protocol-registered ``TOOL`` resources seeded from
``backend/resources/tools/*.yaml`` to their call-site consumers in
``backend/agents.py``, ``backend/debate_agents.py``, and future tier-1
handlers.

Design invariants (carried forward from Phase A prompt dual-read):
    1. If ``RESOURCES_USE_REGISTRY`` is disabled, every call returns the caller's
       hardcoded ``default`` dict unchanged. No import-time side effects.
    2. If the registry lookup fails for any reason (not found, malformed config,
       DB error), we fall back to ``default`` and emit a WARN — never raise.
    3. The returned dict is a *copy*: callers may not mutate it by accident
       into the cached registry record.
    4. ``update_tool_config`` is the only SEPL-facing write path; it wraps
       ``ResourceRegistry.update`` so all lineage and ``learnable`` safety
       checks apply uniformly with prompts.

The raw numeric config of a TOOL lives in ``metadata["config"]`` on its
``ResourceRecord``. The ``fallback`` field carries the canonical defaults so
that a malformed ``metadata["config"]`` can still be recovered. The ``body``
field is a human-readable docstring and does not carry parameters.
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Dict, Optional

from .resource_registry import (
    ResourceKind,
    ResourceNotFoundError,
    ResourcePinnedError,
    ResourceRecord,
    get_resource_registry,
    registry_enabled,
)

logger = logging.getLogger(__name__)


def _merge_numeric_defaults(
    base: Dict[str, Any], override: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Return a shallow merge: every key in ``base`` is preserved; ``override``
    wins when its value is not ``None``. Extra keys in ``override`` are ignored
    — TOOL configs never add fields at read time; only the registered schema
    can introduce fields (via a human YAML change)."""
    merged: Dict[str, Any] = dict(base)
    if override:
        for key in base.keys():
            if key in override and override[key] is not None:
                merged[key] = override[key]
    return merged


def _record_config(record: ResourceRecord) -> Optional[Dict[str, Any]]:
    """Extract the live numeric config from a TOOL ``ResourceRecord``.

    Priority:
        1. ``metadata["config"]`` — the SEPL-evolvable dict.
        2. ``fallback``          — the canonical YAML default.
        3. ``None``              — caller must use its own default.
    """
    if record.kind != ResourceKind.TOOL:
        return None
    meta_cfg = (record.metadata or {}).get("config")
    if isinstance(meta_cfg, dict):
        return dict(meta_cfg)
    if isinstance(record.fallback, dict):
        return dict(record.fallback)
    return None


def get_tool_config(name: str, default: Dict[str, Any]) -> Dict[str, Any]:
    """Return the live config dict for TOOL resource ``name``.

    Parameters
    ----------
    name:
        Registered TOOL name (e.g. ``"short_interest_classifier"``).
    default:
        Caller's hardcoded fallback. MUST contain every key the handler will
        read — this is the byte-exact Phase-A-style safety net.

    Contract
    --------
    * Return value is a fresh dict; mutating it never affects the registry.
    * Every key in ``default`` is guaranteed to appear in the result.
    * If the registry is disabled OR the resource is missing OR the config
      is malformed, we return a copy of ``default``.
    """
    if not isinstance(default, dict):
        raise TypeError("default must be a dict with all required keys")
    safe_default = copy.deepcopy(default)

    if not registry_enabled():
        return safe_default

    try:
        reg = get_resource_registry()
        record = reg.get(name)
    except Exception as e:
        logger.warning("[tool_configs] registry error for %s: %s — using default", name, e)
        return safe_default

    if record is None:
        logger.debug("[tool_configs] %s not registered — using default", name)
        return safe_default
    if record.kind != ResourceKind.TOOL:
        logger.warning(
            "[tool_configs] %s is kind=%s (not TOOL) — using default",
            name, record.kind.value,
        )
        return safe_default

    raw = _record_config(record)
    if raw is None:
        logger.warning(
            "[tool_configs] %s@%s has no metadata.config nor fallback — using caller default",
            name, record.version,
        )
        return safe_default

    merged = _merge_numeric_defaults(safe_default, raw)
    return merged


def get_tool_config_with_version(
    name: str, default: Dict[str, Any]
) -> tuple[Dict[str, Any], Optional[str]]:
    """Same as :func:`get_tool_config` but also returns the active version
    string for lineage stamping (or ``None`` if registry is disabled/missing)."""
    if not isinstance(default, dict):
        raise TypeError("default must be a dict with all required keys")
    safe_default = copy.deepcopy(default)
    if not registry_enabled():
        return safe_default, None
    try:
        reg = get_resource_registry()
        record = reg.get(name)
    except Exception as e:
        logger.warning("[tool_configs] registry error for %s: %s — using default", name, e)
        return safe_default, None
    if record is None or record.kind != ResourceKind.TOOL:
        return safe_default, None
    raw = _record_config(record)
    if raw is None:
        return safe_default, record.version
    return _merge_numeric_defaults(safe_default, raw), record.version


def update_tool_config(
    name: str,
    new_config: Dict[str, Any],
    *,
    reason: str,
    actor: str,
    bump: str = "patch",
) -> ResourceRecord:
    """Write a new version of TOOL resource ``name`` with the given config.

    Safety:
        * Only ``learnable=True`` TOOL resources accept updates. ``ResourceRegistry``
          raises :class:`ResourcePinnedError` otherwise.
        * Callers (SEPL operators in Phase C1.2+) must provide non-empty
          ``reason`` and ``actor`` for lineage.
        * This function ONLY updates ``metadata["config"]``. It does NOT
          change schema, fallback, or the body docstring. Schema changes
          require a human YAML edit.

    Raises:
        :class:`ResourceNotFoundError`  if the tool is not registered.
        :class:`ResourcePinnedError`    if the tool is pinned (learnable=False).
        :class:`ValueError`             if ``new_config`` contains extra keys
                                         not present in the tool's fallback, or
                                         is missing required keys, or is not a
                                         flat numeric dict.
    """
    if not reason or not isinstance(reason, str):
        raise ValueError("reason is required for update_tool_config()")
    if not actor or not isinstance(actor, str):
        raise ValueError("actor is required for update_tool_config()")
    if not isinstance(new_config, dict) or not new_config:
        raise ValueError("new_config must be a non-empty dict")

    reg = get_resource_registry()
    current = reg.get(name)
    if current is None:
        raise ResourceNotFoundError(f"Unknown TOOL resource: {name}")
    if current.kind != ResourceKind.TOOL:
        raise ValueError(f"{name} is kind={current.kind.value}, not TOOL")
    if not current.learnable:
        raise ResourcePinnedError(
            f"TOOL {name!r} is pinned (learnable=False); update_tool_config rejected"
        )

    required_keys = set((current.fallback or {}).keys()) | set(
        ((current.metadata or {}).get("config") or {}).keys()
    )
    if not required_keys:
        raise ValueError(
            f"{name} has no declared parameter schema (empty fallback AND metadata.config)"
        )
    new_keys = set(new_config.keys())
    extra = new_keys - required_keys
    if extra:
        raise ValueError(f"{name}: new_config has unknown keys {sorted(extra)}")
    missing = required_keys - new_keys
    if missing:
        raise ValueError(f"{name}: new_config is missing keys {sorted(missing)}")
    for key, value in new_config.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(
                f"{name}: {key}={value!r} must be an int or float (got {type(value).__name__})"
            )

    merged_meta = dict(current.metadata or {})
    merged_meta["config"] = dict(new_config)

    return reg.update(
        name,
        new_body=current.body,
        bump=bump,  # type: ignore[arg-type]
        reason=reason,
        actor=actor,
        new_metadata=merged_meta,
    )


__all__ = [
    "get_tool_config",
    "get_tool_config_with_version",
    "update_tool_config",
]
