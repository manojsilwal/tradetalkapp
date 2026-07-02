"""Append-only harness refinement changelog (SQLite)."""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from ..state import HarnessCRUDEdit, HarnessState, RefinementCycle

logger = logging.getLogger(__name__)


class HarnessChangelog:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._queue: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._drain, daemon=True)
        self._init_db()
        self._worker.start()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS harness_cycles (
                    cycle_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    triggered_at TEXT,
                    failure_signatures_json TEXT,
                    proposed_edits_json TEXT,
                    applied_edits_json TEXT,
                    deferred_edits_json TEXT,
                    pre_cycle_eval_score REAL,
                    post_cycle_eval_score REAL,
                    rolled_back INTEGER,
                    observe_only INTEGER,
                    pre_cycle_version INTEGER
                );
                CREATE TABLE IF NOT EXISTS harness_snapshots (
                    version INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT,
                    PRIMARY KEY (session_id, version)
                );
                """
            )
            conn.commit()

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                break
            try:
                fn, args = item
                fn(*args)
            except Exception as e:
                logger.warning("[HarnessChangelog] async write failed: %s", e)
            finally:
                self._queue.task_done()

    def flush(self) -> None:
        self._queue.join()

    def save_snapshot(self, snapshot: HarnessState) -> None:
        self._queue.put((self._save_snapshot_sync, (snapshot,)))

    def _save_snapshot_sync(self, snapshot: HarnessState) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO harness_snapshots
                (version, session_id, state_json, created_at)
                VALUES (?, ?, ?, datetime('now'))
                """,
                (snapshot.version, snapshot.session_id, snapshot.to_json()),
            )
            conn.commit()

    def commit_cycle(self, cycle: RefinementCycle, snapshot: HarnessState) -> None:
        self._queue.put((self._commit_cycle_sync, (cycle, snapshot)))

    def _commit_cycle_sync(self, cycle: RefinementCycle, snapshot: HarnessState) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO harness_cycles
                (cycle_id, session_id, triggered_at, failure_signatures_json,
                 proposed_edits_json, applied_edits_json, deferred_edits_json,
                 pre_cycle_eval_score, post_cycle_eval_score, rolled_back,
                 observe_only, pre_cycle_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle.cycle_id,
                    cycle.session_id,
                    cycle.triggered_at.isoformat(),
                    json.dumps(cycle.failure_signatures),
                    json.dumps([e.model_dump(mode="json") for e in cycle.proposed_edits]),
                    json.dumps([e.model_dump(mode="json") for e in cycle.applied_edits]),
                    json.dumps([e.model_dump(mode="json") for e in cycle.deferred_edits]),
                    cycle.pre_cycle_eval_score,
                    cycle.post_cycle_eval_score,
                    1 if cycle.rolled_back else 0,
                    1 if cycle.observe_only else 0,
                    cycle.pre_cycle_version,
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO harness_snapshots
                (version, session_id, state_json, created_at)
                VALUES (?, ?, ?, datetime('now'))
                """,
                (snapshot.version, snapshot.session_id, snapshot.to_json()),
            )
            conn.commit()

    def get_cycle_history(self, session_id: str, *, last_n: int = 10) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM harness_cycles
                WHERE session_id = ?
                ORDER BY triggered_at DESC
                LIMIT ?
                """,
                (session_id, int(last_n)),
            ).fetchall()
        return [dict(r) for r in rows]

    def load_snapshot(self, session_id: str, version: int) -> Optional[HarnessState]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT state_json FROM harness_snapshots WHERE session_id=? AND version=?",
                (session_id, int(version)),
            ).fetchone()
        if not row:
            return None
        return HarnessState.from_json(str(row["state_json"]))

    def export_for_mutation_engine(self, session_id: str) -> Dict[str, Any]:
        history = self.get_cycle_history(session_id, last_n=50)
        successful: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        rollbacks: List[str] = []
        recurring: Dict[str, int] = {}
        for row in history:
            sigs = json.loads(row.get("failure_signatures_json") or "[]")
            for s in sigs:
                recurring[s] = recurring.get(s, 0) + 1
            if row.get("rolled_back"):
                rollbacks.append(str(row.get("cycle_id")))
            applied = json.loads(row.get("applied_edits_json") or "[]")
            if applied:
                successful.extend(applied)
            else:
                proposed = json.loads(row.get("proposed_edits_json") or "[]")
                failed.extend(proposed)
        return {
            "successful_edits": successful,
            "failed_edits": failed,
            "rollbacks": rollbacks,
            "recurring_failure_signatures": [
                k for k, v in recurring.items() if v >= 2
            ],
        }

    def shutdown(self) -> None:
        self._queue.put(None)
