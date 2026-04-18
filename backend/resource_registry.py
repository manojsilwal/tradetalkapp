"""
RSPL Resource Registry (Phase A — PROMPT only).

Protocol-registered resource substrate per Autogenesis (arXiv:2604.15034v1, §3.1).
Each resource has explicit state, lifecycle, and versioned interfaces. All
state mutations go through this module; resources themselves remain passive.

Phase A constraints (deliberate):
  * Only ``PROMPT`` kind is registered. AGENT/TOOL/ENV/MEM are defined in the
    enum but not yet seeded. The DB schema is generic so later phases need no
    new migration.
  * ``update`` / ``restore`` are callable only by humans and tests. SEPL-driven
    updates land in Phase B behind ``GUARDRAILS_ENABLE``.
  * If the feature flag ``RESOURCES_USE_REGISTRY`` is disabled, consumers
    fall back to hardcoded dicts in ``llm_client.py``. This module can still
    be imported and queried safely either way.

Storage:
  * SQLite at ``RESOURCES_DB_PATH`` (default ``backend/resources.db``).
  * YAML source of truth under ``backend/resources/prompts/*.yaml``.
  * On startup, ``resource_seeder.seed_resources_if_empty()`` inserts each YAML
    at its declared version. Subsequent runs are idempotent (PK collision).

Schema: see ``backend/migrations/resources/001_initial_schema.sql``.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from .migrations.runner import run_migrations

logger = logging.getLogger(__name__)


# ── Config ───────────────────────────────────────────────────────────────────

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = str(BACKEND_DIR / "resources.db")


def _resolve_db_path() -> str:
    """Return the configured DB path, honoring ``RESOURCES_DB_PATH`` env."""
    raw = (os.environ.get("RESOURCES_DB_PATH", "") or "").strip()
    if not raw:
        return DEFAULT_DB_PATH
    # Allow relative paths to be interpreted from repo root (backend's parent).
    if os.path.isabs(raw):
        return raw
    repo_root = BACKEND_DIR.parent
    return str((repo_root / raw).resolve())


def registry_enabled() -> bool:
    """Feature flag: when disabled, callers must use hardcoded fallbacks."""
    return (os.environ.get("RESOURCES_USE_REGISTRY", "1").strip() or "1") != "0"


# ── Domain types ─────────────────────────────────────────────────────────────


class ResourceKind(str, Enum):
    PROMPT = "prompt"
    AGENT = "agent"   # reserved; not seeded in Phase A
    TOOL = "tool"     # reserved; tool_registry.py covers this surface today
    ENV = "env"       # reserved
    MEM = "mem"       # reserved


@dataclass(frozen=True)
class ResourceRecord:
    """
    A protocol-registered resource record c_{tau,i} from AGP §3.1.2.

    Frozen so that consumers can cache instances without risk of in-place
    mutation. ``update`` / ``restore`` return new instances.
    """

    name: str
    kind: ResourceKind
    version: str
    description: str
    learnable: bool              # g_{tau,i}
    body: str
    schema: Optional[Dict[str, Any]] = None       # F_{tau,i}
    fallback: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    source_path: str = ""
    created_at: float = 0.0

    def to_contract(self) -> Dict[str, Any]:
        """Stable, LLM-facing subset (name, version, schema, fallback)."""
        return {
            "name": self.name,
            "kind": self.kind.value,
            "version": self.version,
            "schema": self.schema,
            "fallback": self.fallback,
        }

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


class ResourceRegistryError(RuntimeError):
    """Base for registry-level errors."""


class ResourcePinnedError(ResourceRegistryError, PermissionError):
    """Raised when an update is attempted on a ``learnable=False`` resource."""


class ResourceNotFoundError(ResourceRegistryError, LookupError):
    """Raised when ``get`` / ``restore`` can't locate a record."""


# ── Semver helpers ───────────────────────────────────────────────────────────

_BumpKind = Literal["patch", "minor", "major"]


def _parse_semver(v: str) -> tuple[int, int, int]:
    parts = v.strip().split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid semver: {v!r}")
    try:
        major, minor, patch = (int(p) for p in parts)
    except ValueError as e:
        raise ValueError(f"Invalid semver parts in {v!r}: {e}") from e
    if major < 0 or minor < 0 or patch < 0:
        raise ValueError(f"Negative version component in {v!r}")
    return major, minor, patch


def _bump_semver(v: str, bump: _BumpKind) -> str:
    major, minor, patch = _parse_semver(v)
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "major":
        return f"{major + 1}.0.0"
    raise ValueError(f"Unknown bump kind: {bump!r}")


def _semver_key(v: str) -> tuple[int, int, int]:
    try:
        return _parse_semver(v)
    except Exception:
        return (-1, -1, -1)


# ── Registry ─────────────────────────────────────────────────────────────────


class ResourceRegistry:
    """
    SQLite-backed context manager M_tau from AGP §3.1.1 for PROMPT resources.

    Thread-safe for single-process use (APP is single-worker per AGENTS.md).
    All writes go through ``_lock``; reads are cheap point queries that do
    not take the lock.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or _resolve_db_path()
        self._lock = threading.Lock()
        self._ensure_schema()

    # ── schema / connection ──────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        run_migrations(self._db_path, "resources")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @property
    def db_path(self) -> str:
        return self._db_path

    # ── internals ────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ResourceRecord:
        return ResourceRecord(
            name=row["name"],
            kind=ResourceKind(row["kind"]),
            version=row["version"],
            description=row["description"] or "",
            learnable=bool(row["learnable"]),
            body=row["body"],
            schema=json.loads(row["schema_json"]) if row["schema_json"] else None,
            fallback=json.loads(row["fallback_json"]) if row["fallback_json"] else None,
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            source_path=row["source_path"] or "",
            created_at=float(row["created_at"]),
        )

    def _record_by_version(
        self, conn: sqlite3.Connection, name: str, version: str
    ) -> Optional[ResourceRecord]:
        row = conn.execute(
            "SELECT * FROM resource_records WHERE name = ? AND version = ?",
            (name, version),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def _active_version(self, conn: sqlite3.Connection, name: str) -> Optional[str]:
        row = conn.execute(
            "SELECT version FROM resource_active WHERE name = ?",
            (name,),
        ).fetchone()
        return row["version"] if row else None

    def _write_lineage(
        self,
        conn: sqlite3.Connection,
        *,
        name: str,
        kind: ResourceKind,
        from_version: Optional[str],
        to_version: str,
        operation: str,
        reason: str,
        actor: str,
    ) -> None:
        conn.execute(
            """INSERT INTO resource_lineage
               (name, kind, from_version, to_version, operation, reason, actor, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                kind.value,
                from_version,
                to_version,
                operation,
                reason,
                actor,
                time.time(),
            ),
        )

    # ── write API ────────────────────────────────────────────────────────

    def register(
        self,
        record: ResourceRecord,
        *,
        actor: str = "seed:yaml",
        reason: str = "initial registration",
        make_active: bool = True,
    ) -> ResourceRecord:
        """
        Insert a new record. Idempotent on (name, version) — a second call
        with the same key is a no-op (does not bump active pointer).

        Raises ``ValueError`` if the semver is malformed.
        """
        _parse_semver(record.version)  # validate early

        with self._lock:
            conn = self._conn()
            try:
                existing = self._record_by_version(conn, record.name, record.version)
                if existing is not None:
                    logger.debug(
                        "[ResourceRegistry] %s@%s already exists, skipping register",
                        record.name, record.version,
                    )
                    return existing

                now = time.time()
                conn.execute(
                    """INSERT INTO resource_records
                       (name, kind, version, description, learnable, body,
                        schema_json, fallback_json, metadata_json, source_path, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record.name,
                        record.kind.value,
                        record.version,
                        record.description or "",
                        1 if record.learnable else 0,
                        record.body,
                        json.dumps(record.schema) if record.schema is not None else None,
                        json.dumps(record.fallback) if record.fallback is not None else None,
                        json.dumps(record.metadata or {}),
                        record.source_path or "",
                        now,
                    ),
                )

                from_version = None
                if make_active:
                    from_version = self._active_version(conn, record.name)
                    conn.execute(
                        """INSERT INTO resource_active (name, kind, version, updated_at)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(name) DO UPDATE SET
                             kind=excluded.kind,
                             version=excluded.version,
                             updated_at=excluded.updated_at""",
                        (record.name, record.kind.value, record.version, now),
                    )

                self._write_lineage(
                    conn,
                    name=record.name,
                    kind=record.kind,
                    from_version=from_version,
                    to_version=record.version,
                    operation="register",
                    reason=reason,
                    actor=actor,
                )
                conn.commit()

                return ResourceRecord(
                    name=record.name,
                    kind=record.kind,
                    version=record.version,
                    description=record.description or "",
                    learnable=record.learnable,
                    body=record.body,
                    schema=record.schema,
                    fallback=record.fallback,
                    metadata=record.metadata or {},
                    source_path=record.source_path or "",
                    created_at=now,
                )
            finally:
                conn.close()

    def update(
        self,
        name: str,
        new_body: str,
        *,
        bump: _BumpKind = "patch",
        reason: str,
        actor: str,
        new_description: Optional[str] = None,
        new_metadata: Optional[Dict[str, Any]] = None,
    ) -> ResourceRecord:
        """
        Produce a new version from the currently active record.

        Safety invariants (Phase A):
          * resource must exist
          * resource must have ``learnable=True`` (else ``ResourcePinnedError``)
          * ``reason`` and ``actor`` are required for lineage
        """
        if not reason:
            raise ValueError("reason is required for update()")
        if not actor:
            raise ValueError("actor is required for update()")

        with self._lock:
            conn = self._conn()
            try:
                active_ver = self._active_version(conn, name)
                if active_ver is None:
                    raise ResourceNotFoundError(f"Unknown resource: {name}")
                current = self._record_by_version(conn, name, active_ver)
                if current is None:
                    raise ResourceNotFoundError(
                        f"Active pointer for {name!r} points to missing version {active_ver!r}"
                    )
                if not current.learnable:
                    raise ResourcePinnedError(
                        f"Resource {name!r} is pinned (learnable=False); update rejected"
                    )

                next_ver = _bump_semver(active_ver, bump)
                now = time.time()
                merged_meta = dict(current.metadata or {})
                if new_metadata:
                    merged_meta.update(new_metadata)
                merged_meta.setdefault("lineage", []).append(
                    {"from": active_ver, "to": next_ver, "actor": actor, "at": now}
                )

                new_record = ResourceRecord(
                    name=name,
                    kind=current.kind,
                    version=next_ver,
                    description=new_description if new_description is not None else current.description,
                    learnable=current.learnable,
                    body=new_body,
                    schema=current.schema,
                    fallback=current.fallback,
                    metadata=merged_meta,
                    source_path=current.source_path,
                    created_at=now,
                )

                conn.execute(
                    """INSERT INTO resource_records
                       (name, kind, version, description, learnable, body,
                        schema_json, fallback_json, metadata_json, source_path, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        new_record.name,
                        new_record.kind.value,
                        new_record.version,
                        new_record.description,
                        1 if new_record.learnable else 0,
                        new_record.body,
                        json.dumps(new_record.schema) if new_record.schema is not None else None,
                        json.dumps(new_record.fallback) if new_record.fallback is not None else None,
                        json.dumps(new_record.metadata),
                        new_record.source_path,
                        now,
                    ),
                )
                conn.execute(
                    """UPDATE resource_active
                       SET version = ?, updated_at = ?
                       WHERE name = ?""",
                    (next_ver, now, name),
                )
                self._write_lineage(
                    conn,
                    name=name,
                    kind=current.kind,
                    from_version=active_ver,
                    to_version=next_ver,
                    operation="update",
                    reason=reason,
                    actor=actor,
                )
                conn.commit()
                return new_record
            finally:
                conn.close()

    def restore(
        self,
        name: str,
        version: str,
        *,
        reason: str,
        actor: str,
    ) -> ResourceRecord:
        """Flip the active pointer back to an existing version. No new row created."""
        if not reason:
            raise ValueError("reason is required for restore()")
        if not actor:
            raise ValueError("actor is required for restore()")

        with self._lock:
            conn = self._conn()
            try:
                target = self._record_by_version(conn, name, version)
                if target is None:
                    raise ResourceNotFoundError(f"{name}@{version} not found")
                current_active = self._active_version(conn, name)
                now = time.time()
                conn.execute(
                    """UPDATE resource_active
                       SET kind = ?, version = ?, updated_at = ?
                       WHERE name = ?""",
                    (target.kind.value, target.version, now, name),
                )
                self._write_lineage(
                    conn,
                    name=name,
                    kind=target.kind,
                    from_version=current_active,
                    to_version=target.version,
                    operation="restore",
                    reason=reason,
                    actor=actor,
                )
                conn.commit()
                return target
            finally:
                conn.close()

    # ── read API ─────────────────────────────────────────────────────────

    def get(self, name: str, version: str = "latest") -> Optional[ResourceRecord]:
        """Return the requested record, or the active one if ``version='latest'``."""
        conn = self._conn()
        try:
            if version == "latest":
                av = self._active_version(conn, name)
                if av is None:
                    return None
                return self._record_by_version(conn, name, av)
            return self._record_by_version(conn, name, version)
        finally:
            conn.close()

    def active_version(self, name: str) -> Optional[str]:
        conn = self._conn()
        try:
            return self._active_version(conn, name)
        finally:
            conn.close()

    def list(self, kind: Optional[ResourceKind] = None) -> List[ResourceRecord]:
        """List active records (one row per name); optionally filter by kind."""
        conn = self._conn()
        try:
            if kind is None:
                rows = conn.execute(
                    """SELECT r.* FROM resource_records r
                       JOIN resource_active a ON a.name = r.name AND a.version = r.version
                       ORDER BY r.name ASC"""
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT r.* FROM resource_records r
                       JOIN resource_active a ON a.name = r.name AND a.version = r.version
                       WHERE r.kind = ?
                       ORDER BY r.name ASC""",
                    (kind.value,),
                ).fetchall()
            return [self._row_to_record(r) for r in rows]
        finally:
            conn.close()

    def versions(self, name: str) -> List[str]:
        """Return all versions of ``name``, newest semver first."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT version FROM resource_records WHERE name = ? ORDER BY created_at DESC",
                (name,),
            ).fetchall()
            if not rows:
                return []
            versions = [r["version"] for r in rows]
            return sorted(versions, key=_semver_key, reverse=True)
        finally:
            conn.close()

    def lineage(self, name: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Audit trail for a resource, newest first."""
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT id, name, kind, from_version, to_version, operation,
                          reason, actor, created_at
                   FROM resource_lineage WHERE name = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (name, int(limit)),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def load_contract(self, name: str) -> Optional[Dict[str, Any]]:
        """Return stable LLM-facing descriptor (see ``ResourceRecord.to_contract``)."""
        rec = self.get(name)
        return rec.to_contract() if rec else None

    def snapshot(self) -> Dict[str, Any]:
        """Full registry dump for debug endpoints. Only active records."""
        records = [r.as_dict() for r in self.list()]
        return {
            "db_path": self._db_path,
            "count": len(records),
            "generated_at": time.time(),
            "records": records,
        }

    def snapshot_id(self) -> str:
        """
        Deterministic identifier for the set of active versions. Used by callers
        to stamp reflections / traces so that an optimizer can later tie outcomes
        to the exact registry state that produced them.
        """
        import hashlib

        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT name, kind, version FROM resource_active ORDER BY name ASC"
            ).fetchall()
        finally:
            conn.close()
        payload = "|".join(f"{r['kind']}:{r['name']}@{r['version']}" for r in rows)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ── Module-level singleton ───────────────────────────────────────────────────

_registry_singleton: Optional[ResourceRegistry] = None
_singleton_lock = threading.Lock()


def get_resource_registry() -> ResourceRegistry:
    """Return the process-wide registry, creating it on first access."""
    global _registry_singleton
    if _registry_singleton is not None:
        return _registry_singleton
    with _singleton_lock:
        if _registry_singleton is None:
            _registry_singleton = ResourceRegistry()
            logger.info(
                "[ResourceRegistry] initialized db=%s enabled=%s",
                _registry_singleton.db_path,
                registry_enabled(),
            )
    return _registry_singleton


def _reset_singleton_for_tests() -> None:
    """Test-only helper — drops the module-level cache."""
    global _registry_singleton
    with _singleton_lock:
        _registry_singleton = None
