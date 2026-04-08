"""
CORAL-style structured hub — SQLite rows for curated notes/skills before vector RAG.

Complements :class:`~backend.knowledge_store.KnowledgeStore` (Chroma): cheap
exact reads and regime-tagged observations; not a replacement for embeddings.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, List, Optional

from .user_preferences import DB_PATH
from .migrations.runner import run_migrations

logger = logging.getLogger(__name__)
_local = threading.local()

_DEFAULT_NOTE_TTL_SEC = 7 * 24 * 3600
_DEFAULT_SKILL_TTL_SEC = 30 * 24 * 3600


def _conn():
    if not hasattr(_local, "coral_conn"):
        _local.coral_conn = __import__("sqlite3").connect(DB_PATH, check_same_thread=False)
        _local.coral_conn.row_factory = __import__("sqlite3").Row
    return _local.coral_conn


def reset_thread_local_connection() -> None:
    """Close thread-local DB handle (e.g. when tests override ``DB_PATH``)."""
    if hasattr(_local, "coral_conn"):
        try:
            _local.coral_conn.close()
        except Exception:
            pass
        delattr(_local, "coral_conn")


def init_coral_hub_db() -> None:
    """Apply migrations for coral_* tables (idempotent)."""
    run_migrations(DB_PATH, "coral_hub")
    logger.info("[CoralHub] SQLite tables ready")


def _now() -> float:
    return time.time()


def _purge_expired_notes(conn) -> None:
    t = _now()
    conn.execute("DELETE FROM coral_notes WHERE expires_at IS NOT NULL AND expires_at < ?", (t,))
    conn.execute("DELETE FROM coral_skills WHERE expires_at IS NOT NULL AND expires_at < ?", (t,))


def add_note(
    agent_id: str,
    observation: str,
    *,
    market_regime: str = "",
    ttl_seconds: Optional[float] = None,
) -> int:
    """Append a hub note. Returns row id."""
    obs = (observation or "").strip()[:8000]
    if not obs:
        return -1
    ttl = float(ttl_seconds) if ttl_seconds is not None else float(_DEFAULT_NOTE_TTL_SEC)
    exp = _now() + ttl
    conn = _conn()
    _purge_expired_notes(conn)
    cur = conn.execute(
        """INSERT INTO coral_notes (agent_id, observation, market_regime, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        (agent_id[:64], obs, (market_regime or "")[:64], _now(), exp),
    )
    conn.commit()
    return int(cur.lastrowid)


def add_skill(
    name: str,
    content: str,
    *,
    contributed_by: str = "",
    skill_id: Optional[str] = None,
    ttl_seconds: Optional[float] = None,
) -> str:
    """Upsert a skill by skill_id (UUID if omitted). Returns skill_id."""
    sid = (skill_id or str(uuid.uuid4()))[:128]
    ttl = float(ttl_seconds) if ttl_seconds is not None else float(_DEFAULT_SKILL_TTL_SEC)
    exp = _now() + ttl
    conn = _conn()
    _purge_expired_notes(conn)
    conn.execute(
        """INSERT INTO coral_skills (skill_id, name, content, contributed_by, times_used, created_at, expires_at)
           VALUES (?, ?, ?, ?, 0, ?, ?)
           ON CONFLICT(skill_id) DO UPDATE SET
             name=excluded.name,
             content=excluded.content,
             contributed_by=excluded.contributed_by,
             expires_at=excluded.expires_at""",
        (
            sid,
            (name or "skill")[:256],
            (content or "").strip()[:12000],
            (contributed_by or "")[:128],
            _now(),
            exp,
        ),
    )
    conn.commit()
    return sid


def increment_skill_use(skill_id: str) -> None:
    conn = _conn()
    conn.execute(
        "UPDATE coral_skills SET times_used = times_used + 1 WHERE skill_id = ?",
        (skill_id[:128],),
    )
    conn.commit()


def list_recent_notes(
    n: int = 6,
    *,
    market_regime: Optional[str] = None,
    exclude_agent_id: Optional[str] = None,
) -> List[dict]:
    """Recent notes, optionally filtered by regime and/or excluding one agent."""
    conn = _conn()
    _purge_expired_notes(conn)
    conn.commit()
    n = max(1, min(50, int(n)))
    where = "WHERE 1=1"
    params: List[Any] = []
    if market_regime:
        where += " AND (market_regime = ? OR market_regime = '')"
        params.append(market_regime[:64])
    if exclude_agent_id:
        where += " AND agent_id != ?"
        params.append(exclude_agent_id[:64])
    params.append(n)
    rows = conn.execute(
        f"""SELECT id, agent_id, observation, market_regime, created_at FROM coral_notes
            {where} ORDER BY created_at DESC LIMIT ?""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def list_top_skills(n: int = 5) -> List[dict]:
    conn = _conn()
    _purge_expired_notes(conn)
    conn.commit()
    n = max(1, min(30, int(n)))
    rows = conn.execute(
        """SELECT skill_id, name, content, contributed_by, times_used, created_at
           FROM coral_skills ORDER BY times_used DESC, created_at DESC LIMIT ?""",
        (n,),
    ).fetchall()
    return [dict(r) for r in rows]


def log_handoff_event(event_type: str, payload: dict[str, Any]) -> int:
    """
    Append a structured handoff/trace event (debate complete, swarm trace, etc.) for dreaming.

    event_type examples: handoff_debate, handoff_swarm_trace
    """
    et = (event_type or "").strip()[:64]
    if not et:
        return -1
    try:
        blob = json.dumps(payload, default=str)[:24000]
    except Exception:
        blob = "{}"
    conn = _conn()
    cur = conn.execute(
        """INSERT INTO coral_handoff_events (event_type, payload_json, created_at)
           VALUES (?, ?, ?)""",
        (et, blob, _now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_handoff_events_since(since_epoch: float) -> List[dict[str, Any]]:
    """Events with created_at >= since_epoch, newest first."""
    conn = _conn()
    rows = conn.execute(
        """SELECT id, event_type, payload_json, created_at FROM coral_handoff_events
           WHERE created_at >= ? ORDER BY created_at DESC LIMIT 500""",
        (float(since_epoch),),
    ).fetchall()
    out: List[dict[str, Any]] = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except Exception:
            payload = {}
        out.append(
            {
                "id": r["id"],
                "event_type": r["event_type"],
                "payload": payload,
                "created_at": r["created_at"],
            }
        )
    return out


def list_attempts_since(since_epoch: float) -> List[dict[str, Any]]:
    """Swarm / trace attempts with created_at >= since_epoch (newest first)."""
    conn = _conn()
    rows = conn.execute(
        """SELECT id, task_id, agent_id, signal, score, created_at FROM coral_attempts
           WHERE created_at >= ? ORDER BY created_at DESC LIMIT 500""",
        (float(since_epoch),),
    ).fetchall()
    return [dict(r) for r in rows]


def record_attempt(task_id: str, agent_id: str, signal: Optional[float], score: Optional[float]) -> None:
    conn = _conn()
    conn.execute(
        """INSERT INTO coral_attempts (task_id, agent_id, signal, score, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        ((task_id or "")[:128], (agent_id or "")[:64], signal, score, _now()),
    )
    conn.commit()


def format_hub_context_block(
    *,
    market_regime: str = "",
    max_notes: int = 5,
    max_skills: int = 4,
) -> str:
    """
    Compact text block for system / retrieval prompts: curated notes + skills first.
    """
    lines: List[str] = []
    regime = (market_regime or "").strip()[:64]
    skills = list_top_skills(max_skills)
    for s in skills:
        sid = s.get("skill_id", "")
        lines.append(
            f"- Skill [{s.get('name', '')}] (id={sid}, uses={s.get('times_used', 0)}): "
            f"{str(s.get('content', ''))[:500]}"
        )
    notes = list_recent_notes(max_notes, market_regime=regime or None)
    if not notes and regime:
        notes = list_recent_notes(max_notes, market_regime=None)
    for n in notes:
        lines.append(
            f"- Note ({n.get('agent_id', '')} | regime={n.get('market_regime', '')}): "
            f"{str(n.get('observation', ''))[:400]}"
        )
    if not lines:
        return ""
    return (
        "## CORAL hub (curated skills & notes — read before broad retrieval)\n"
        + "\n".join(lines)
        + "\n"
    )


def format_swarm_prior_block(factor_name: str, ticker: str, market_regime: str) -> str:
    """Smaller injection for swarm factor pairs (alongside vector reflections)."""
    hub = format_hub_context_block(market_regime=market_regime, max_notes=3, max_skills=2)
    if not hub.strip():
        return ""
    return f"\n[CORAL hub priors for {factor_name} / {ticker}]\n{hub}"
