"""
Decision-Outcome Ledger — Phase 2 of the Harness Engineering moat.

One row per user-facing agent decision, with full provenance:

* ``decision_events``     — what the agent decided (verdict, confidence, output)
* ``decision_evidence``   — which RAG chunks / data-lake slices it cited
* ``feature_snapshots``   — the input datapoints it saw (PE, SIR, VIX, regime…)
* ``outcome_observations``— multi-horizon market-truth grading (1d/5d/21d/63d)
* ``contract_violations`` — runtime schema failures from contract_validator

This module is the SQL-queryable layer under every agent output, so a future
finance-specialized model can be evaluated against our own history before
replacing the incumbent (see ``backend/eval/model_swap_replay.py``).

Design constraints:

* **Never raises.** Every producer API catches exceptions and logs. The ledger
  is observability-grade — a DB outage must not take user-facing flows with it.
* **Backend-agnostic.** ``DECISION_BACKEND`` env selects ``sqlite`` (default),
  ``supabase`` (uses the ``supabase-py`` client already in requirements), or
  ``none`` (full no-op when the flag is off or the infra isn't available).
* **Feature flag.** ``DECISION_LEDGER_ENABLE`` (default on). When off, every
  write turns into a logged no-op.
* **CORAL dual-write.** ``emit_decision`` also emits a CORAL handoff event so
  the existing dreaming / meta-harness surface keeps working unchanged.
* **Thread safe.** SQLite uses a per-thread connection (same pattern as
  :mod:`backend.claim_store`). Supabase client is thread-safe by construction.

See ``docs/DECISION_LEDGER.md`` for schema reference and example queries.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .migrations.runner import run_migrations

logger = logging.getLogger(__name__)


# ── Config ───────────────────────────────────────────────────────────────────


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = str(BACKEND_DIR / "decisions.db")

_SUPPORTED_BACKENDS = frozenset({"sqlite", "supabase", "none"})

_DEFAULT_HORIZONS: tuple[str, ...] = ("1d", "5d", "21d", "63d")
HORIZONS = _DEFAULT_HORIZONS


def ledger_enabled() -> bool:
    """Master switch — off turns every write into a no-op."""
    return (os.environ.get("DECISION_LEDGER_ENABLE", "1").strip() or "1") != "0"


def _configured_backend() -> str:
    raw = (os.environ.get("DECISION_BACKEND", "") or "sqlite").strip().lower()
    if raw not in _SUPPORTED_BACKENDS:
        logger.warning(
            "[DecisionLedger] Unknown DECISION_BACKEND=%r — falling back to sqlite",
            raw,
        )
        return "sqlite"
    return raw


def _resolve_db_path() -> str:
    raw = (os.environ.get("DECISIONS_DB_PATH", "") or "").strip()
    if not raw:
        return DEFAULT_DB_PATH
    if os.path.isabs(raw):
        return raw
    return str((BACKEND_DIR.parent / raw).resolve())


# ── Domain types ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionEvent:
    """Row mirror of ``decision_events`` — returned by ``get_decision`` etc."""

    decision_id: str
    created_at: float
    decision_type: str
    user_id: str = ""
    symbol: str = ""
    horizon_hint: str = "none"
    model: str = ""
    prompt_versions: Dict[str, str] = field(default_factory=dict)
    registry_snapshot_id: str = ""
    inputs_hash: str = ""
    output: Dict[str, Any] = field(default_factory=dict)
    verdict: str = ""
    confidence: Optional[float] = None
    source_route: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceRef:
    """One retrieval artifact that informed a decision."""

    chunk_id: str
    collection: str = ""
    relevance: Optional[float] = None
    rank: int = 0


@dataclass(frozen=True)
class FeatureValue:
    """One input datapoint the decision observed."""

    name: str
    value_num: Optional[float] = None
    value_str: str = ""
    regime: str = ""


@dataclass(frozen=True)
class OutcomeObservation:
    """One graded market-truth observation attached to a decision."""

    decision_id: str
    horizon: str
    metric: str
    value: Optional[float]
    as_of_ts: float
    benchmark: str = ""
    excess_return: Optional[float] = None
    correct: Optional[bool] = None
    label_source: str = ""


# ── Ledger backend interface ────────────────────────────────────────────────


class LedgerBackend(ABC):
    """Contract each backend (sqlite / supabase / null) must honor."""

    @abstractmethod
    def emit_decision(self, event: DecisionEvent) -> None: ...

    @abstractmethod
    def attach_evidence(self, decision_id: str, refs: Sequence[EvidenceRef]) -> int: ...

    @abstractmethod
    def record_features(
        self, decision_id: str, features: Sequence[FeatureValue]
    ) -> int: ...

    @abstractmethod
    def record_outcome(self, obs: OutcomeObservation) -> bool: ...

    @abstractmethod
    def record_violation(
        self,
        *,
        resource_name: str,
        resource_version: str,
        model: str,
        path: str,
        code: str,
        message: str,
        observed_type: str = "",
        expected: str = "",
        decision_id: str = "",
    ) -> None: ...

    # ── read API (used by grader + replay) ───────────────────────────────

    @abstractmethod
    def get_decision(self, decision_id: str) -> Optional[DecisionEvent]: ...

    @abstractmethod
    def list_decisions_since(
        self,
        since_ts: float,
        *,
        decision_type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[DecisionEvent]: ...

    @abstractmethod
    def ungraded_decisions_for_horizon(
        self, horizon: str, *, older_than_ts: float, limit: int = 500
    ) -> List[DecisionEvent]: ...

    # ── observability ────────────────────────────────────────────────────

    @abstractmethod
    def stats(self) -> Dict[str, int]: ...

    @property
    @abstractmethod
    def name(self) -> str: ...


# ── SQLite backend ───────────────────────────────────────────────────────────


class SQLiteLedgerBackend(LedgerBackend):
    """
    Default local backend. One DB file (``decisions.db`` next to ``progress.db``)
    with a per-thread connection cache and idempotent migrations.

    Write methods catch every exception because producers live on the hot path
    and must never block on a ledger hiccup. Read methods are only called from
    scheduled jobs and propagate exceptions (the caller is expected to retry).
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or _resolve_db_path()
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        run_migrations(self._db_path, "decisions")
        self._local = threading.local()
        self._write_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "sqlite"

    @property
    def db_path(self) -> str:
        return self._db_path

    # ── connection pool ────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            self._local.conn = conn
        return conn

    def reset_thread_local_connection(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    # ── write API (never raise) ────────────────────────────────────────

    def emit_decision(self, event: DecisionEvent) -> None:
        try:
            with self._write_lock:
                conn = self._conn()
                conn.execute(
                    """INSERT OR REPLACE INTO decision_events
                       (decision_id, created_at, user_id, decision_type, symbol,
                        horizon_hint, model, prompt_versions_json,
                        registry_snapshot_id, inputs_hash, output_json,
                        verdict, confidence, source_route)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        event.decision_id,
                        float(event.created_at),
                        event.user_id or "",
                        event.decision_type,
                        (event.symbol or "").upper(),
                        event.horizon_hint or "none",
                        event.model or "",
                        json.dumps(event.prompt_versions or {}, default=str),
                        event.registry_snapshot_id or "",
                        event.inputs_hash or "",
                        json.dumps(event.output or {}, default=str),
                        event.verdict or "",
                        float(event.confidence) if event.confidence is not None else None,
                        event.source_route or "",
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.warning(
                "[DecisionLedger] emit_decision failed id=%s: %s",
                event.decision_id, e,
            )

    def attach_evidence(
        self, decision_id: str, refs: Sequence[EvidenceRef]
    ) -> int:
        if not refs:
            return 0
        try:
            now = time.time()
            rows = [
                (
                    decision_id,
                    r.chunk_id,
                    r.collection or "",
                    float(r.relevance) if r.relevance is not None else None,
                    int(r.rank),
                    now,
                )
                for r in refs
                if r.chunk_id
            ]
            if not rows:
                return 0
            with self._write_lock:
                conn = self._conn()
                conn.executemany(
                    """INSERT INTO decision_evidence
                       (decision_id, chunk_id, collection, relevance, rank, created_at)
                       VALUES (?,?,?,?,?,?)""",
                    rows,
                )
                conn.commit()
            return len(rows)
        except Exception as e:
            logger.warning(
                "[DecisionLedger] attach_evidence failed id=%s n=%d: %s",
                decision_id, len(refs), e,
            )
            return 0

    def record_features(
        self, decision_id: str, features: Sequence[FeatureValue]
    ) -> int:
        if not features:
            return 0
        try:
            now = time.time()
            rows = [
                (
                    decision_id,
                    f.name,
                    float(f.value_num) if f.value_num is not None else None,
                    f.value_str or "",
                    f.regime or "",
                    now,
                )
                for f in features
                if f.name
            ]
            if not rows:
                return 0
            with self._write_lock:
                conn = self._conn()
                conn.executemany(
                    """INSERT INTO feature_snapshots
                       (decision_id, feature_name, value_num, value_str, regime, created_at)
                       VALUES (?,?,?,?,?,?)""",
                    rows,
                )
                conn.commit()
            return len(rows)
        except Exception as e:
            logger.warning(
                "[DecisionLedger] record_features failed id=%s n=%d: %s",
                decision_id, len(features), e,
            )
            return 0

    def record_outcome(self, obs: OutcomeObservation) -> bool:
        try:
            correct = (
                int(bool(obs.correct)) if obs.correct is not None else None
            )
            with self._write_lock:
                conn = self._conn()
                conn.execute(
                    """INSERT OR REPLACE INTO outcome_observations
                       (decision_id, horizon, as_of_ts, metric, value,
                        benchmark, excess_return, correct_bool, label_source, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        obs.decision_id,
                        obs.horizon,
                        float(obs.as_of_ts),
                        obs.metric,
                        float(obs.value) if obs.value is not None else None,
                        obs.benchmark or "",
                        float(obs.excess_return)
                        if obs.excess_return is not None
                        else None,
                        correct,
                        obs.label_source or "",
                        time.time(),
                    ),
                )
                conn.commit()
            return True
        except Exception as e:
            logger.warning(
                "[DecisionLedger] record_outcome failed id=%s horizon=%s metric=%s: %s",
                obs.decision_id, obs.horizon, obs.metric, e,
            )
            return False

    def record_violation(
        self,
        *,
        resource_name: str,
        resource_version: str,
        model: str,
        path: str,
        code: str,
        message: str,
        observed_type: str = "",
        expected: str = "",
        decision_id: str = "",
    ) -> None:
        try:
            with self._write_lock:
                conn = self._conn()
                conn.execute(
                    """INSERT INTO contract_violations
                       (decision_id, resource_name, resource_version, model,
                        path, code, message, observed_type, expected, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        decision_id or "",
                        resource_name or "",
                        resource_version or "",
                        model or "",
                        path or "$",
                        code or "",
                        message or "",
                        observed_type or "",
                        expected or "",
                        time.time(),
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.warning(
                "[DecisionLedger] record_violation failed resource=%s code=%s: %s",
                resource_name, code, e,
            )

    # ── read API ────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> DecisionEvent:
        try:
            pv = json.loads(row["prompt_versions_json"] or "{}")
        except Exception:
            pv = {}
        try:
            out = json.loads(row["output_json"] or "{}")
        except Exception:
            out = {}
        return DecisionEvent(
            decision_id=row["decision_id"],
            created_at=float(row["created_at"]),
            user_id=row["user_id"] or "",
            decision_type=row["decision_type"],
            symbol=row["symbol"] or "",
            horizon_hint=row["horizon_hint"] or "none",
            model=row["model"] or "",
            prompt_versions=pv if isinstance(pv, dict) else {},
            registry_snapshot_id=row["registry_snapshot_id"] or "",
            inputs_hash=row["inputs_hash"] or "",
            output=out if isinstance(out, dict) else {},
            verdict=row["verdict"] or "",
            confidence=row["confidence"],
            source_route=row["source_route"] or "",
        )

    def get_decision(self, decision_id: str) -> Optional[DecisionEvent]:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM decision_events WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        return self._row_to_event(row) if row else None

    def list_decisions_since(
        self,
        since_ts: float,
        *,
        decision_type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[DecisionEvent]:
        conn = self._conn()
        if decision_type:
            rows = conn.execute(
                """SELECT * FROM decision_events
                   WHERE created_at >= ? AND decision_type = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (float(since_ts), decision_type, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM decision_events
                   WHERE created_at >= ?
                   ORDER BY created_at DESC LIMIT ?""",
                (float(since_ts), int(limit)),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def ungraded_decisions_for_horizon(
        self,
        horizon: str,
        *,
        older_than_ts: float,
        limit: int = 500,
    ) -> List[DecisionEvent]:
        """
        Decisions whose horizon has elapsed and that do NOT yet have a row in
        ``outcome_observations`` for this horizon. The grader uses this to pick
        up work across every decision_type (not just swarm).
        """
        conn = self._conn()
        rows = conn.execute(
            """SELECT d.* FROM decision_events d
               LEFT JOIN outcome_observations o
                 ON o.decision_id = d.decision_id AND o.horizon = ?
               WHERE d.created_at <= ?
                 AND o.decision_id IS NULL
               ORDER BY d.created_at ASC LIMIT ?""",
            (horizon, float(older_than_ts), int(limit)),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def stats(self) -> Dict[str, int]:
        conn = self._conn()
        out: Dict[str, int] = {}
        for tbl in (
            "decision_events",
            "decision_evidence",
            "outcome_observations",
            "feature_snapshots",
            "contract_violations",
        ):
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                out[tbl] = int(n)
            except Exception:
                out[tbl] = -1
        return out


# ── Null backend (disabled / unsupported config) ────────────────────────────


class NullLedgerBackend(LedgerBackend):
    """No-op backend. Used when ``DECISION_LEDGER_ENABLE=0`` or infra missing."""

    @property
    def name(self) -> str:
        return "null"

    def emit_decision(self, event: DecisionEvent) -> None:
        return None

    def attach_evidence(self, decision_id: str, refs: Sequence[EvidenceRef]) -> int:
        return 0

    def record_features(self, decision_id: str, features: Sequence[FeatureValue]) -> int:
        return 0

    def record_outcome(self, obs: OutcomeObservation) -> bool:
        return False

    def record_violation(self, **_kw: Any) -> None:
        return None

    def get_decision(self, decision_id: str) -> Optional[DecisionEvent]:
        return None

    def list_decisions_since(self, since_ts: float, **_kw: Any) -> List[DecisionEvent]:
        return []

    def ungraded_decisions_for_horizon(
        self, horizon: str, **_kw: Any
    ) -> List[DecisionEvent]:
        return []

    def stats(self) -> Dict[str, int]:
        return {}


# ── Supabase backend (optional; used when DECISION_BACKEND=supabase) ────────


class SupabaseLedgerBackend(LedgerBackend):
    """
    Supabase / Postgres backend using the ``supabase-py`` client.

    Assumes the tables from ``backend/supabase_decisions_bootstrap.sql`` have
    been applied against the project. The schema mirrors SQLite exactly so
    queries are portable.

    We wrap every call in a broad try/except — the Supabase service is remote
    and producer paths must not go down with it.
    """

    def __init__(self, client: Any) -> None:
        # ``client`` is a ``supabase.Client`` instance. Typed as Any so this
        # module imports cleanly even when supabase-py is not installed.
        self._client = client

    @property
    def name(self) -> str:
        return "supabase"

    # ── writes ────────────────────────────────────────────────────────

    def emit_decision(self, event: DecisionEvent) -> None:
        try:
            payload = {
                "decision_id": event.decision_id,
                "created_at": float(event.created_at),
                "user_id": event.user_id or "",
                "decision_type": event.decision_type,
                "symbol": (event.symbol or "").upper(),
                "horizon_hint": event.horizon_hint or "none",
                "model": event.model or "",
                "prompt_versions_json": event.prompt_versions or {},
                "registry_snapshot_id": event.registry_snapshot_id or "",
                "inputs_hash": event.inputs_hash or "",
                "output_json": event.output or {},
                "verdict": event.verdict or "",
                "confidence": event.confidence,
                "source_route": event.source_route or "",
            }
            self._client.table("decision_events").upsert(payload).execute()
        except Exception as e:
            logger.warning("[DecisionLedger][supabase] emit_decision failed: %s", e)

    def attach_evidence(self, decision_id: str, refs: Sequence[EvidenceRef]) -> int:
        if not refs:
            return 0
        try:
            now = time.time()
            rows = [
                {
                    "decision_id": decision_id,
                    "chunk_id": r.chunk_id,
                    "collection": r.collection or "",
                    "relevance": r.relevance,
                    "rank": int(r.rank),
                    "created_at": now,
                }
                for r in refs
                if r.chunk_id
            ]
            if rows:
                self._client.table("decision_evidence").insert(rows).execute()
            return len(rows)
        except Exception as e:
            logger.warning("[DecisionLedger][supabase] attach_evidence failed: %s", e)
            return 0

    def record_features(
        self, decision_id: str, features: Sequence[FeatureValue]
    ) -> int:
        if not features:
            return 0
        try:
            now = time.time()
            rows = [
                {
                    "decision_id": decision_id,
                    "feature_name": f.name,
                    "value_num": f.value_num,
                    "value_str": f.value_str or "",
                    "regime": f.regime or "",
                    "created_at": now,
                }
                for f in features
                if f.name
            ]
            if rows:
                self._client.table("feature_snapshots").insert(rows).execute()
            return len(rows)
        except Exception as e:
            logger.warning("[DecisionLedger][supabase] record_features failed: %s", e)
            return 0

    def record_outcome(self, obs: OutcomeObservation) -> bool:
        try:
            payload = {
                "decision_id": obs.decision_id,
                "horizon": obs.horizon,
                "as_of_ts": float(obs.as_of_ts),
                "metric": obs.metric,
                "value": obs.value,
                "benchmark": obs.benchmark or "",
                "excess_return": obs.excess_return,
                "correct_bool": int(bool(obs.correct))
                if obs.correct is not None
                else None,
                "label_source": obs.label_source or "",
                "created_at": time.time(),
            }
            self._client.table("outcome_observations").upsert(
                payload, on_conflict="decision_id,horizon,metric"
            ).execute()
            return True
        except Exception as e:
            logger.warning("[DecisionLedger][supabase] record_outcome failed: %s", e)
            return False

    def record_violation(self, **kw: Any) -> None:
        try:
            payload = {
                "decision_id": kw.get("decision_id", "") or "",
                "resource_name": kw.get("resource_name", "") or "",
                "resource_version": kw.get("resource_version", "") or "",
                "model": kw.get("model", "") or "",
                "path": kw.get("path", "$") or "$",
                "code": kw.get("code", "") or "",
                "message": kw.get("message", "") or "",
                "observed_type": kw.get("observed_type", "") or "",
                "expected": kw.get("expected", "") or "",
                "created_at": time.time(),
            }
            self._client.table("contract_violations").insert(payload).execute()
        except Exception as e:
            logger.warning(
                "[DecisionLedger][supabase] record_violation failed: %s", e
            )

    # ── reads ─────────────────────────────────────────────────────────

    def get_decision(self, decision_id: str) -> Optional[DecisionEvent]:
        try:
            res = (
                self._client.table("decision_events")
                .select("*")
                .eq("decision_id", decision_id)
                .limit(1)
                .execute()
            )
            data = getattr(res, "data", None) or []
            if not data:
                return None
            return _supabase_row_to_event(data[0])
        except Exception as e:
            logger.warning("[DecisionLedger][supabase] get_decision failed: %s", e)
            return None

    def list_decisions_since(
        self,
        since_ts: float,
        *,
        decision_type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[DecisionEvent]:
        try:
            q = (
                self._client.table("decision_events")
                .select("*")
                .gte("created_at", float(since_ts))
                .order("created_at", desc=True)
                .limit(int(limit))
            )
            if decision_type:
                q = q.eq("decision_type", decision_type)
            res = q.execute()
            return [_supabase_row_to_event(r) for r in (getattr(res, "data", None) or [])]
        except Exception as e:
            logger.warning(
                "[DecisionLedger][supabase] list_decisions_since failed: %s", e
            )
            return []

    def ungraded_decisions_for_horizon(
        self,
        horizon: str,
        *,
        older_than_ts: float,
        limit: int = 500,
    ) -> List[DecisionEvent]:
        """
        Supabase doesn't expose a LEFT JOIN through the simple PostgREST API
        used here. Install a helper view/RPC in the Supabase project and call
        it via ``rpc()``; until then we fetch elderly decisions and filter
        client-side. The scheduler only runs this once a day so the bandwidth
        is acceptable; for heavy deployments switch to an RPC.
        """
        try:
            res = (
                self._client.table("decision_events")
                .select("*")
                .lte("created_at", float(older_than_ts))
                .order("created_at", desc=False)
                .limit(int(limit) * 2)
                .execute()
            )
            candidates = [
                _supabase_row_to_event(r) for r in (getattr(res, "data", None) or [])
            ]
            if not candidates:
                return []
            ids = [c.decision_id for c in candidates]
            graded_ids: set[str] = set()
            # PostgREST `in` clause has a practical size cap; chunk to 100.
            for i in range(0, len(ids), 100):
                batch = ids[i : i + 100]
                res2 = (
                    self._client.table("outcome_observations")
                    .select("decision_id")
                    .eq("horizon", horizon)
                    .in_("decision_id", batch)
                    .execute()
                )
                for row in getattr(res2, "data", None) or []:
                    graded_ids.add(row.get("decision_id", ""))
            return [c for c in candidates if c.decision_id not in graded_ids][:limit]
        except Exception as e:
            logger.warning(
                "[DecisionLedger][supabase] ungraded_decisions_for_horizon failed: %s",
                e,
            )
            return []

    def stats(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for tbl in (
            "decision_events",
            "decision_evidence",
            "outcome_observations",
            "feature_snapshots",
            "contract_violations",
        ):
            try:
                res = (
                    self._client.table(tbl)
                    .select("decision_id" if tbl == "decision_events" else "id",
                            count="exact")
                    .limit(1)
                    .execute()
                )
                out[tbl] = int(getattr(res, "count", 0) or 0)
            except Exception:
                out[tbl] = -1
        return out


def _supabase_row_to_event(row: Dict[str, Any]) -> DecisionEvent:
    pv = row.get("prompt_versions_json") or {}
    if isinstance(pv, str):
        try:
            pv = json.loads(pv)
        except Exception:
            pv = {}
    out = row.get("output_json") or {}
    if isinstance(out, str):
        try:
            out = json.loads(out)
        except Exception:
            out = {}
    return DecisionEvent(
        decision_id=row.get("decision_id", ""),
        created_at=float(row.get("created_at", 0.0) or 0.0),
        user_id=row.get("user_id", "") or "",
        decision_type=row.get("decision_type", ""),
        symbol=row.get("symbol", "") or "",
        horizon_hint=row.get("horizon_hint", "none") or "none",
        model=row.get("model", "") or "",
        prompt_versions=pv if isinstance(pv, dict) else {},
        registry_snapshot_id=row.get("registry_snapshot_id", "") or "",
        inputs_hash=row.get("inputs_hash", "") or "",
        output=out if isinstance(out, dict) else {},
        verdict=row.get("verdict", "") or "",
        confidence=row.get("confidence"),
        source_route=row.get("source_route", "") or "",
    )


# ── Module-level singleton ──────────────────────────────────────────────────


_ledger: Optional[LedgerBackend] = None
_ledger_lock = threading.Lock()


def _build_backend() -> LedgerBackend:
    if not ledger_enabled():
        logger.info("[DecisionLedger] disabled via DECISION_LEDGER_ENABLE=0")
        return NullLedgerBackend()

    backend = _configured_backend()
    if backend == "none":
        return NullLedgerBackend()

    if backend == "supabase":
        try:
            from .vector_backends import _get_supabase_client  # type: ignore
            client = _get_supabase_client()
        except Exception as e:
            logger.warning(
                "[DecisionLedger] Supabase backend requested but client "
                "unavailable (%s) — falling back to sqlite", e,
            )
            return SQLiteLedgerBackend()
        if client is None:
            logger.warning(
                "[DecisionLedger] Supabase client returned None — falling back to sqlite"
            )
            return SQLiteLedgerBackend()
        return SupabaseLedgerBackend(client)

    return SQLiteLedgerBackend()


def get_ledger() -> LedgerBackend:
    """Return the process-wide ledger backend, creating it on first access."""
    global _ledger
    if _ledger is not None:
        return _ledger
    with _ledger_lock:
        if _ledger is None:
            _ledger = _build_backend()
            logger.info(
                "[DecisionLedger] initialized backend=%s", _ledger.name
            )
    return _ledger


def set_ledger_for_tests(backend: Optional[LedgerBackend]) -> None:
    """Swap in an explicit backend (used by tests and scheduler wiring)."""
    global _ledger
    with _ledger_lock:
        _ledger = backend


def _reset_singleton_for_tests() -> None:
    set_ledger_for_tests(None)


# ── Public convenience API ──────────────────────────────────────────────────
#
# Producers (agents, moderator, chat, scorecard, decision_terminal) call these
# thin functions so every producer looks the same and there's exactly one place
# to add cross-cutting behavior (CORAL dual-write, metrics, tracing).


def new_decision_id() -> str:
    """Caller-friendly id generator (uuid4 hex; 32 chars)."""
    return uuid.uuid4().hex


def emit_decision(
    *,
    decision_type: str,
    output: Dict[str, Any],
    user_id: str = "",
    symbol: str = "",
    horizon_hint: str = "none",
    model: str = "",
    prompt_versions: Optional[Dict[str, str]] = None,
    registry_snapshot_id: str = "",
    inputs_hash: str = "",
    verdict: str = "",
    confidence: Optional[float] = None,
    source_route: str = "",
    evidence: Optional[Sequence[EvidenceRef]] = None,
    features: Optional[Sequence[FeatureValue]] = None,
    decision_id: Optional[str] = None,
    created_at: Optional[float] = None,
) -> str:
    """
    Write a single decision to the ledger. Returns the ``decision_id`` (caller-
    supplied or auto-generated) so later calls can attach outcomes.

    Convenience wrapper around the backend API. Accepts evidence + features
    inline so simple producers are a one-liner; specialized producers can call
    ``attach_evidence`` / ``record_features`` incrementally.

    Always best-effort — never raises. If the ledger is disabled or the
    backend write fails, returns the intended id anyway so calling code can
    proceed without special-casing.
    """
    did = decision_id or new_decision_id()
    ts = float(created_at) if created_at is not None else time.time()
    event = DecisionEvent(
        decision_id=did,
        created_at=ts,
        decision_type=decision_type,
        user_id=user_id or "",
        symbol=(symbol or "").upper(),
        horizon_hint=horizon_hint or "none",
        model=model or "",
        prompt_versions=dict(prompt_versions or {}),
        registry_snapshot_id=registry_snapshot_id or "",
        inputs_hash=inputs_hash or "",
        output=dict(output or {}),
        verdict=verdict or "",
        confidence=confidence,
        source_route=source_route or "",
    )
    try:
        ledger = get_ledger()
        ledger.emit_decision(event)
        if evidence:
            ledger.attach_evidence(did, list(evidence))
        if features:
            ledger.record_features(did, list(features))
    except Exception as e:
        logger.warning("[DecisionLedger] emit_decision wrapper failed: %s", e)

    # CORAL dual-write: keep the existing dreaming / meta-harness surface
    # working with zero changes. Failure here must not affect the ledger.
    try:
        from . import coral_hub as _ch
        _ch.log_handoff_event(
            "decision_emitted",
            {
                "decision_id": did,
                "decision_type": decision_type,
                "symbol": event.symbol,
                "verdict": event.verdict,
                "confidence": event.confidence,
                "model": event.model,
                "source_route": event.source_route,
                "horizon_hint": event.horizon_hint,
            },
        )
    except Exception as e:
        logger.debug("[DecisionLedger] CORAL dual-write skipped: %s", e)

    return did


def attach_evidence(
    decision_id: str, refs: Iterable[EvidenceRef]
) -> int:
    try:
        return get_ledger().attach_evidence(decision_id, list(refs))
    except Exception as e:
        logger.warning("[DecisionLedger] attach_evidence wrapper failed: %s", e)
        return 0


def record_features(
    decision_id: str, features: Iterable[FeatureValue]
) -> int:
    try:
        return get_ledger().record_features(decision_id, list(features))
    except Exception as e:
        logger.warning("[DecisionLedger] record_features wrapper failed: %s", e)
        return 0


def record_outcome(obs: OutcomeObservation) -> bool:
    try:
        return get_ledger().record_outcome(obs)
    except Exception as e:
        logger.warning("[DecisionLedger] record_outcome wrapper failed: %s", e)
        return False


def record_violation(
    *,
    resource_name: str,
    resource_version: str,
    model: str,
    path: str,
    code: str,
    message: str,
    observed_type: str = "",
    expected: str = "",
    decision_id: str = "",
) -> None:
    try:
        get_ledger().record_violation(
            resource_name=resource_name,
            resource_version=resource_version,
            model=model,
            path=path,
            code=code,
            message=message,
            observed_type=observed_type,
            expected=expected,
            decision_id=decision_id,
        )
    except Exception as e:
        logger.warning("[DecisionLedger] record_violation wrapper failed: %s", e)


# ── Contract-validator sink wiring ──────────────────────────────────────────
#
# The validator ships with a logging-only sink by default. This helper wires
# the ledger backend in so violations land in the ``contract_violations``
# table. Invoke once at process startup (see backend/main.py).


def install_contract_validator_sink() -> None:
    try:
        from . import contract_validator as _cv
    except Exception as e:
        logger.debug("[DecisionLedger] contract_validator import failed: %s", e)
        return

    def _sink(v: Any, context: Dict[str, Any]) -> None:
        record_violation(
            resource_name=getattr(v, "resource_name", "") or "",
            resource_version=getattr(v, "resource_version", "") or "",
            model=str(context.get("model", "") or ""),
            path=getattr(v, "path", "$") or "$",
            code=getattr(v, "code", "") or "",
            message=getattr(v, "message", "") or "",
            observed_type=getattr(v, "observed_type", "") or "",
            expected=getattr(v, "expected", "") or "",
            decision_id=str(context.get("decision_id", "") or ""),
        )

    try:
        _cv.get_contract_validator().set_sink(_sink)
        logger.info("[DecisionLedger] contract_validator sink installed")
    except Exception as e:
        logger.warning(
            "[DecisionLedger] install_contract_validator_sink failed: %s", e
        )
