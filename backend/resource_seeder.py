"""
Seed the RSPL resource registry from YAML files in ``backend/resources/``.

Idempotent: on repeat startup the (name, version) primary key guarantees
existing rows are preserved. If a YAML file declares a newer version than the
DB's active pointer, we log a WARN but do NOT auto-promote — human review is
required to promote a YAML-declared version. This matches AGP §3.1.2's
"safe update interface" principle for Phase A.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .resource_registry import (
    ResourceKind,
    ResourceRecord,
    ResourceRegistry,
    get_resource_registry,
    _parse_semver,
    _semver_key,
)

logger = logging.getLogger(__name__)

RESOURCES_DIR = Path(__file__).resolve().parent / "resources"
PROMPTS_DIR = RESOURCES_DIR / "prompts"
TOOLS_DIR = RESOURCES_DIR / "tools"


# ── YAML -> ResourceRecord ───────────────────────────────────────────────────

_REQUIRED_TOP_LEVEL_KEYS = {"name", "kind", "version", "body"}


class SeedError(RuntimeError):
    """Raised when a YAML resource file is malformed."""


def _validate_yaml(data: Dict[str, Any], source: Path) -> None:
    if not isinstance(data, dict):
        raise SeedError(f"{source}: top-level must be a mapping")
    missing = _REQUIRED_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise SeedError(f"{source}: missing required keys {sorted(missing)}")
    name = data["name"]
    if not isinstance(name, str) or not name.strip():
        raise SeedError(f"{source}: 'name' must be a non-empty string")
    kind = data["kind"]
    if kind not in {k.value for k in ResourceKind}:
        raise SeedError(f"{source}: 'kind' {kind!r} not in {[k.value for k in ResourceKind]}")
    version = data["version"]
    try:
        _parse_semver(str(version))
    except Exception as e:
        raise SeedError(f"{source}: invalid version {version!r}: {e}") from e
    if not isinstance(data["body"], str):
        raise SeedError(f"{source}: 'body' must be a string")
    # Optional keys have strict types when present
    for opt_key, opt_type in (
        ("description", str),
        ("learnable", bool),
        ("metadata", dict),
        ("schema", (dict, type(None))),
        ("fallback", (dict, list, str, int, float, bool, type(None))),
    ):
        if opt_key in data and not isinstance(data[opt_key], opt_type):
            raise SeedError(f"{source}: {opt_key!r} has wrong type {type(data[opt_key]).__name__}")


def _yaml_to_record(path: Path) -> ResourceRecord:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _validate_yaml(data, path)
    return ResourceRecord(
        name=data["name"],
        kind=ResourceKind(data["kind"]),
        version=str(data["version"]),
        description=str(data.get("description", "")),
        learnable=bool(data.get("learnable", False)),
        body=data["body"],
        schema=data.get("schema"),
        fallback=data.get("fallback"),
        metadata=dict(data.get("metadata") or {}),
        source_path=str(path),
    )


# ── Seed entrypoint ──────────────────────────────────────────────────────────


def discover_prompt_files() -> List[Path]:
    if not PROMPTS_DIR.is_dir():
        return []
    return sorted(p for p in PROMPTS_DIR.iterdir() if p.suffix in (".yaml", ".yml"))


def discover_tool_files() -> List[Path]:
    """Phase C1 — TOOL resources live under ``backend/resources/tools/``."""
    if not TOOLS_DIR.is_dir():
        return []
    return sorted(p for p in TOOLS_DIR.iterdir() if p.suffix in (".yaml", ".yml"))


def discover_all_resource_files() -> List[Path]:
    """All YAML-declared resources across every kind directory."""
    return [*discover_prompt_files(), *discover_tool_files()]


def seed_resources_if_empty(
    registry: Optional[ResourceRegistry] = None,
    *,
    reason: str = "initial seed from yaml",
    actor: str = "seed:yaml",
) -> Dict[str, Any]:
    """
    Insert any YAML-declared resource whose (name, version) pair is not in the
    registry. Existing rows are never overwritten. Mismatch between YAML
    version and DB active version emits a WARN but is not auto-resolved.

    Returns a small summary dict for logging / tests.
    """
    reg = registry or get_resource_registry()
    inserted: List[str] = []
    skipped: List[str] = []
    warned: List[str] = []
    errors: List[str] = []

    for path in discover_all_resource_files():
        try:
            record = _yaml_to_record(path)
        except SeedError as e:
            logger.error("[ResourceSeeder] skip malformed %s: %s", path.name, e)
            errors.append(path.name)
            continue

        existing_exact = reg.get(record.name, record.version)
        active = reg.active_version(record.name)

        if existing_exact is not None:
            # (name, version) already present — nothing to do
            skipped.append(f"{record.name}@{record.version}")
            if active and active != record.version:
                # YAML still declares this as canonical version but a newer one
                # is active in the DB. That is an operator decision; do not revert.
                if _semver_key(active) > _semver_key(record.version):
                    logger.info(
                        "[ResourceSeeder] %s: DB active=%s ahead of YAML=%s (ok, keeping DB)",
                        record.name, active, record.version,
                    )
                else:
                    logger.warning(
                        "[ResourceSeeder] %s: DB active=%s behind YAML=%s (manual promote required)",
                        record.name, active, record.version,
                    )
                    warned.append(record.name)
            continue

        # No exact version match. If active exists but differs, we register the
        # new YAML row WITHOUT promoting active — operator must promote.
        promote = active is None
        try:
            reg.register(
                record,
                actor=actor,
                reason=(reason if promote else f"yaml-registered (not promoted; active={active})"),
                make_active=promote,
            )
            inserted.append(f"{record.name}@{record.version}")
            if not promote:
                logger.warning(
                    "[ResourceSeeder] %s: added YAML version %s but active remains %s",
                    record.name, record.version, active,
                )
                warned.append(record.name)
        except Exception as e:
            logger.exception("[ResourceSeeder] failed to register %s: %s", record.name, e)
            errors.append(f"{record.name}@{record.version}")

    summary = {
        "inserted": inserted,
        "skipped": skipped,
        "warned": warned,
        "errors": errors,
        "total_yaml": len(discover_all_resource_files()),
    }
    if inserted:
        logger.info("[ResourceSeeder] inserted %d record(s): %s", len(inserted), inserted)
    if warned:
        logger.warning(
            "[ResourceSeeder] %d resource(s) need manual promotion: %s", len(warned), warned
        )
    if errors:
        logger.error("[ResourceSeeder] %d yaml file(s) failed: %s", len(errors), errors)
    return summary


def seed_on_startup() -> Dict[str, Any]:
    """Public startup hook called from ``main.py``."""
    import os

    if os.environ.get("RESOURCES_AUTOSEED", "1").strip() == "0":
        logger.info("[ResourceSeeder] RESOURCES_AUTOSEED=0 — skipping seed")
        return {"inserted": [], "skipped": [], "warned": [], "errors": [], "total_yaml": 0}
    return seed_resources_if_empty()
