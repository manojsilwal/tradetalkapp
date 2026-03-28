"""
Chat session store, RAG with recency rerank, predictive pre-warming, and asyncio.gather helpers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import market_l1_cache

logger = logging.getLogger(__name__)

CHAT_RAG_COLLECTIONS = (
    "macro_snapshots",
    "debate_history",
    "strategy_backtests",
    "price_movements",
)
RECENCY_LAMBDA_HOURS = float(__import__("os").environ.get("CHAT_RAG_RECENCY_LAMBDA", "0.02"))
RAG_TOP_K = 12
RAG_OVERSAMPLE = 8

PREDICTIVE_QUERIES: List[Tuple[str, str]] = [
    ("macro", "stock market macro outlook interest rates VIX"),
    ("gold", "gold price outlook precious metals dollar"),
    ("sector", "sector rotation equities leadership"),
    ("rates", "treasury yields fed policy equities"),
    ("debate", "AI investment debate verdict consensus"),
]

_SESSIONS: Dict[str, "ChatSession"] = {}
_SESSION_LOCK = asyncio.Lock()


@dataclass
class ChatSession:
    session_id: str
    system_prompt: str
    assembled_at: float
    expires_at: float
    user_id: Optional[str] = None
    rag_prewarm: Dict[str, str] = field(default_factory=dict)


def _parse_meta_date(meta: dict) -> Optional[datetime]:
    for key in ("date", "ingested_at", "timestamp", "run_date"):
        raw = meta.get(key)
        if not raw:
            continue
        s = str(raw)[:32]
        try:
            if "T" in s:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _recency_score(similarity: float, meta: dict) -> float:
    """Score = similarity * exp(-lambda * age_hours)."""
    dt = _parse_meta_date(meta)
    if dt is None:
        age_h = 0.0
    else:
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_h = max(0.0, (now - dt).total_seconds() / 3600.0)
    return float(similarity) * math.exp(-RECENCY_LAMBDA_HOURS * age_h)


def rerank_hits(hits: List[dict]) -> List[dict]:
    """hits: {document, metadata, distance} — cosine distance from Chroma."""
    scored = []
    for h in hits:
        dist = float(h.get("distance", 1.0))
        sim = max(0.0, min(1.0, 1.0 - dist))
        meta = h.get("metadata") or {}
        s = _recency_score(sim, meta)
        scored.append((s, h))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in scored]


async def _query_coll(
    ks, collection: str, query_text: str, n: int, fn: Callable[..., Any]
) -> List[dict]:
    return await asyncio.to_thread(fn, collection, query_text, n)


async def chat_rag_context(ks, user_message: str) -> str:
    """Multi-collection RAG + recency rerank → single context block."""
    fn = ks.query_with_metadata

    async def one(coll: str):
        return await _query_coll(ks, coll, user_message, RAG_OVERSAMPLE, fn)

    parts = await asyncio.gather(*[one(c) for c in CHAT_RAG_COLLECTIONS])
    merged: List[dict] = []
    for p in parts:
        merged.extend(p)
    ranked = rerank_hits(merged)[:RAG_TOP_K]
    lines = []
    for i, h in enumerate(ranked, 1):
        doc = (h.get("document") or "").strip()
        if not doc:
            continue
        meta = h.get("metadata") or {}
        src = meta.get("source") or meta.get("ticker") or meta.get("strategy_name") or "knowledge"
        lines.append(f"[{i}] ({src}) {doc[:1200]}")
    return "\n".join(lines) if lines else "(no relevant knowledge base hits)"


def _classify_prewarm_key(msg: str) -> Optional[str]:
    m = msg.lower()
    for key, _ in PREDICTIVE_QUERIES:
        if key == "macro" and any(k in m for k in ("macro", "market", "economy", "recession")):
            return "macro"
        if key == "gold" and "gold" in m:
            return "gold"
        if key == "sector" and "sector" in m:
            return "sector"
        if key == "rates" and any(k in m for k in ("rate", "yield", "fed", "treasury")):
            return "rates"
        if key == "debate" and "debate" in m:
            return "debate"
    return None


async def prewarm_predictive_rag(ks) -> Dict[str, str]:
    """Run canned queries in parallel; used at session open."""

    async def run_one(key: str, q: str):
        ctx = await chat_rag_context(ks, q)
        return key, ctx

    pairs = await asyncio.gather(*[run_one(k, q) for k, q in PREDICTIVE_QUERIES])
    return {k: v for k, v in pairs}


def _build_system_prompt(
    market_snapshot: dict,
    user_ctx: dict,
    pipeline_status: Optional[dict],
) -> str:
    snap = json.dumps(market_snapshot, indent=2, default=str)[:8000]
    uctx = json.dumps(user_ctx, indent=2, default=str)[:4000]
    pipe = json.dumps(pipeline_status or {}, indent=2, default=str)[:2000]
    return (
        "You are TradeTalk, a concise finance and education assistant. "
        "Lead with a one-sentence takeaway, then details. "
        "Cite retrieved context when used; do not invent facts. "
        "This is educational context, not personalized investment advice.\n\n"
        f"## Market snapshot (cached)\n{snap}\n\n"
        f"## User portfolio context (if any)\n{uctx}\n\n"
        f"## Knowledge pipeline status\n{pipe}\n"
    )


async def get_user_context_block(user_id: Optional[str]) -> dict:
    if not user_id:
        return {}
    try:
        from . import paper_portfolio as pp

        perf = await asyncio.to_thread(pp.get_portfolio_performance, user_id)
        pos = await asyncio.to_thread(pp.get_positions, user_id)
        return {"portfolio_performance": perf, "open_positions": pos[:20]}
    except Exception as e:
        logger.debug("[Chat] user context failed: %s", e)
        return {}


async def create_session(
    ks,
    user_id: Optional[str],
    ttl_seconds: int = 86400,
) -> ChatSession:
    """asyncio.gather snapshot assembly + predictive RAG pre-warm."""

    async def l1():
        return market_l1_cache.get_snapshot()

    async def pipe():
        try:
            return ks.stats().get("pipeline_status") or {}
        except Exception:
            return {}

    async def uctx():
        return await get_user_context_block(user_id)

    market_snapshot, pipeline_status, user_ctx = await asyncio.gather(l1(), pipe(), uctx())

    rag_prewarm = await prewarm_predictive_rag(ks)

    system_prompt = _build_system_prompt(
        {"l1": market_snapshot, "updated_at": market_l1_cache.updated_at_epoch()},
        user_ctx,
        pipeline_status,
    )

    now = time.time()
    sid = str(uuid.uuid4())
    sess = ChatSession(
        session_id=sid,
        system_prompt=system_prompt,
        assembled_at=now,
        expires_at=now + ttl_seconds,
        user_id=user_id,
        rag_prewarm=rag_prewarm,
    )
    async with _SESSION_LOCK:
        _SESSIONS[sid] = sess
    _prune_sessions()
    return sess


def _prune_sessions() -> None:
    now = time.time()
    dead = [k for k, s in _SESSIONS.items() if s.expires_at < now]
    for k in dead:
        _SESSIONS.pop(k, None)


def get_session(session_id: str) -> Optional[ChatSession]:
    _prune_sessions()
    return _SESSIONS.get(session_id)


async def gather_message_context(
    ks,
    session: ChatSession,
    user_message: str,
) -> Tuple[str, dict]:
    """
    Concurrent: RAG (or prewarm fast path), L1 read, user_ctx refresh, freshness meta.
    Returns (rag_block, meta_dict).
    """

    async def rag_task() -> str:
        key = _classify_prewarm_key(user_message)
        if key and key in session.rag_prewarm:
            return session.rag_prewarm[key]
        return await chat_rag_context(ks, user_message)

    async def l1_task():
        return market_l1_cache.get_snapshot()

    async def user_task():
        return await get_user_context_block(session.user_id)

    async def fresh_task():
        return {
            "session_assembled_at": session.assembled_at,
            "l1_updated_at": market_l1_cache.updated_at_epoch(),
            "stale_session": time.time() > session.expires_at,
        }

    rag_block, l1_snap, uctx, fresh = await asyncio.gather(
        rag_task(),
        l1_task(),
        user_task(),
        fresh_task(),
    )
    meta = {
        **fresh,
        "l1_keys": list((l1_snap or {}).get("quotes", {}).keys()),
        "user_ctx_nonempty": bool(uctx),
    }
    attribution = (
        "\n## Retrieval\n"
        f"{rag_block}\n\n"
        "## Instructions\n"
        "1. If the user is just saying hello, making small talk, or asking a broad non-financial question, reply naturally and conversationally. Do NOT recite the snapshot or retrieval data unless directly relevant.\n"
        "2. When you use facts from the retrieval block, mention they come from TradeTalk's knowledge base. "
        "Blend with the live L1 snapshot below when relevant.\n"
        f"## L1 refresh snapshot\n{json.dumps(l1_snap, default=str)[:4000]}\n"
        f"## User context (refreshed)\n{json.dumps(uctx, default=str)[:4000]}\n"
    )
    return attribution, meta


def chat_bootstrap_payload(ks) -> dict:
    """Global prefetch without session — cheap snapshot for React."""
    l1 = market_l1_cache.get_snapshot()
    try:
        pipe = ks.stats().get("pipeline_status") or {}
    except Exception:
        pipe = {}
    return {
        "l1_updated_at": market_l1_cache.updated_at_epoch(),
        "pipeline_status": pipe,
        "l1": l1,
    }


async def refresh_session_task(session_id: str, ks) -> None:
    """Background refresh: re-predictive RAG + extend TTL."""
    sess = get_session(session_id)
    if not sess:
        return
    try:
        sess.rag_prewarm = await prewarm_predictive_rag(ks)
        sess.expires_at = time.time() + 86400
    except Exception as e:
        logger.warning("[Chat] refresh_session failed: %s", e)
