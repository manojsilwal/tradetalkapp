"""
Agent memory — short-term (SQLite history) + long-term (vector semantic recall).

Maps the converged pattern to TradeTalk's stack: relational rows in progress.db
and embeddings in the existing ``chat_memories`` Chroma collection via
:class:`~backend.knowledge_store.KnowledgeStore`.

- ``save_memory`` — persist message rows; optional ``semantic_summary`` stores one
  embedding per turn for cross-session cosine search.
- ``load_memory`` — last N messages for a session (short-term context).
- ``search_memory`` — semantic retrieval over past turns (per-user).
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from typing import Any, List, Optional

from .user_preferences import DB_PATH

logger = logging.getLogger(__name__)

_local = threading.local()

# Max messages loaded as short-term context (user+assistant pairs, up to ~8 turns)
_DEFAULT_MESSAGE_LIMIT = 16


def extract_tickers_from_text(text: str) -> List[str]:
    """Extract stock ticker symbols like $AAPL or known finance terms from text."""
    # Find words with $ prefix e.g. $AAPL, $MSFT
    tickers = re.findall(r'\$([A-Za-z]{1,5})\b', text)
    # Match standard stock symbols that are uppercase of length 2-5
    common = {"AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "NFLX", "AMD", "SPY", "QQQ", "GLD"}
    for word in re.findall(r'\b([A-Z]{2,5})\b', text):
        if word in common:
            tickers.append(word)
    seen = set()
    out = []
    for t in tickers:
        tu = t.upper()
        if tu not in seen:
            seen.add(tu)
            out.append(tu)
    return out


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "agent_mem_conn"):
        _local.agent_mem_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.agent_mem_conn.row_factory = sqlite3.Row
    return _local.agent_mem_conn


def init_agent_memory_db() -> None:
    """Create chat_message_history if missing (idempotent)."""
    conn = _get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chat_message_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_chat_hist_user_session
            ON chat_message_history(user_id, session_id, created_at);
        """
    )
    conn.commit()
    logger.info("[AgentMemory] chat_message_history ready")


def save_memory(
    ks: Any,
    user_id: str,
    session_id: str,
    role: str,
    content: str,
    *,
    semantic_summary: Optional[str] = None,
    tickers: Optional[List[str]] = None,
    topic: str = "chat",
) -> None:
    """
    Insert a chat message row. Optionally store a vector embedding for semantic
    search (typically one summary per turn after the assistant reply).
    """
    if not user_id or not session_id or role not in ("user", "assistant"):
        return
    try:
        from .agent_policy_guardrails import redact_secrets_in_text

        safe_content = redact_secrets_in_text(content.strip())[:12000]
        if not safe_content:
            return
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO chat_message_history (user_id, session_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, session_id, role, safe_content, time.time()),
        )
        conn.commit()
    except Exception as e:
        logger.warning("[AgentMemory] save_memory SQL failed: %s", e)
        return

    # Auto-extract tickers if not provided
    tlist = tickers
    if not tlist:
        tlist = extract_tickers_from_text(content)

    # Auto-generate semantic summary if missing for assistant replies to promote continual learning
    if not semantic_summary and role == "assistant" and ks is not None:
        try:
            recent = load_memory(user_id, session_id, limit=3)
            user_msg = ""
            for msg in reversed(recent):
                if msg["role"] == "user":
                    user_msg = msg["content"]
                    break
            if user_msg:
                semantic_summary = f"User query: {user_msg[:200]}\nAssistant answer: {content[:200]}"
            else:
                semantic_summary = f"Assistant response: {content[:400]}"
        except Exception as e:
            logger.warning("[AgentMemory] auto-summary failed: %s", e)

    if semantic_summary and ks is not None:
        try:
            from .agent_policy_guardrails import redact_secrets_in_text

            summary = redact_secrets_in_text(semantic_summary.strip())[:4000]
            if not summary:
                return
            ks.add_chat_memory(
                user_id,
                session_id,
                summary,
                tlist or [],
                topic,
            )
        except Exception as e:
            logger.warning("[AgentMemory] save_memory embedding failed: %s", e)


def load_memory(
    user_id: str,
    session_id: str,
    limit: int = _DEFAULT_MESSAGE_LIMIT,
) -> List[dict]:
    """
    Return the last ``limit`` messages for this session, oldest first, for
    injection as short-term context.
    """
    if not user_id or not session_id:
        return []
    try:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT role, content FROM chat_message_history
            WHERE user_id = ? AND session_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, session_id, limit),
        ).fetchall()
        rows = list(reversed(rows))
        out: List[dict] = []
        for r in rows:
            role = r["role"]
            if role not in ("user", "assistant"):
                continue
            out.append({"role": role, "content": str(r["content"])})
        return out
    except Exception as e:
        logger.warning("[AgentMemory] load_memory failed: %s", e)
        return []


def search_memory(
    ks: Any,
    user_id: str,
    query_text: str,
    n_results: int = 5,
) -> List[str]:
    """Semantic similarity search over stored per-user chat memories."""
    if not user_id or not query_text.strip() or ks is None:
        return []
    try:
        return ks.query_chat_memories(user_id, query_text.strip(), n_results=n_results)
    except Exception as e:
        logger.warning("[AgentMemory] search_memory failed: %s", e)
        return []


def format_memory_context_block(memories: List[str], max_chars: int = 2400) -> str:
    """Turn search hits into a system-prompt sized block."""
    if not memories:
        return ""
    lines = []
    total = 0
    for i, m in enumerate(memories, 1):
        chunk = (m or "").strip()[:800]
        if not chunk:
            continue
        line = f"[{i}] {chunk}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    if not lines:
        return ""
    return (
        "\n## Prior conversation memory (semantic recall)\n"
        + "\n".join(lines)
        + "\n"
    )
