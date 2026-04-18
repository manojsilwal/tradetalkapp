"""
Chat session store, RAG with recency rerank, predictive pre-warming, and asyncio.gather helpers.

Includes:
  - Sticky structured state (active_ticker, analysis_mode, etc.) persisted per-session
  - Predictive RAG pre-warming at session creation
  - Recency-weighted reranking across multiple collections
  - User preference injection into system prompts
  - OpenTelemetry span instrumentation on hot paths
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import market_l1_cache
from . import chat_session_store
from .rag_retrieval import earnings_hits_as_rag_rows, plan_chat_rag
from .telemetry import get_tracer

logger = logging.getLogger(__name__)

# Legacy tuple — chat RAG now uses rag_retrieval.plan_chat_rag (filtered + extra collections).
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

# In-process cache; rows also stored in SQLite (chat_sessions) for restart survival.
# Multiple uvicorn workers still need a shared store — see chat_session_store module doc.
_SESSIONS: Dict[str, "ChatSession"] = {}
_SESSION_LOCK = asyncio.Lock()


def persist_chat_session(sess: "ChatSession") -> None:
    """Write session payload to SQLite (sync)."""
    chat_session_store.save_session_row(
        sess.session_id,
        sess.user_id,
        sess.assembled_at,
        sess.expires_at,
        sess,
    )


def _hydrate_session_from_db(session_id: str) -> Optional["ChatSession"]:
    """Load ChatSession from DB if row exists and not expired."""
    row = chat_session_store.load_session_row(session_id)
    if not row:
        return None
    now = time.time()
    if row["expires_at"] < now:
        chat_session_store.delete_session_row(session_id)
        return None
    sess = ChatSession(
        session_id=row["session_id"],
        system_prompt="",
        assembled_at=row["assembled_at"],
        expires_at=row["expires_at"],
        user_id=row["user_id"],
    )
    chat_session_store.apply_stored_payload(sess, row["payload"])
    return sess


@dataclass
class ChatSession:
    session_id: str
    system_prompt: str
    assembled_at: float
    expires_at: float
    user_id: Optional[str] = None
    rag_prewarm: Dict[str, str] = field(default_factory=dict)
    sticky_state: Dict[str, Any] = field(default_factory=dict)
    # Last completed turn — for Phase B evidence memo export (in-process only).
    last_user_message: str = ""
    last_assistant_text: str = ""
    last_evidence_contract: Optional[Dict[str, Any]] = None
    last_chat_meta: Dict[str, Any] = field(default_factory=dict)


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


def _query_rag_with_fallback(
    ks,
    collection: str,
    query_text: str,
    n: int,
    where: Optional[dict],
) -> List[dict]:
    """Metadata-filtered search; if empty, retry without filter (sparse ticker rows).

    Each returned hit carries an extra ``collection`` key so downstream reranking
    can be traced back to its source vector store and recorded as ledger
    evidence (see ``gather_message_context`` → ``meta["rag_chunk_refs"]``).
    """
    fn = ks.query_with_metadata
    hits = fn(collection, query_text, n, where=where)
    if not hits and where:
        hits = fn(collection, query_text, n, where=None)
    # Tag the source collection on each hit (additive, safe for existing callers).
    for h in hits or []:
        try:
            if isinstance(h, dict) and "collection" not in h:
                h["collection"] = collection
        except Exception:
            pass
    return hits


async def _query_rag_thread(
    ks,
    collection: str,
    query_text: str,
    n: int,
    where: Optional[dict],
) -> List[dict]:
    return await asyncio.to_thread(
        _query_rag_with_fallback, ks, collection, query_text, n, where
    )


async def chat_rag_context(
    ks,
    user_message: str,
    sticky_state: Optional[Dict[str, Any]] = None,
    *,
    out_refs: Optional[List[dict]] = None,
) -> str:
    """Multi-collection RAG (ticker filters + extras) + recency rerank → single context block.

    If ``out_refs`` is provided (a list), it is populated in-place with one
    ``{chunk_id, collection, rank, distance, ticker}`` dict per reranked hit
    used to build the context block. This lets the caller record per-chunk
    evidence into the Decision Ledger without changing the return shape.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("chat.rag_context") as span:
        plan = plan_chat_rag(
            user_message,
            sticky_state,
            oversample=RAG_OVERSAMPLE,
            extra_n=max(4, RAG_OVERSAMPLE // 2),
        )
        try:
            span.set_attribute("rag.query_preview", user_message[:100])
            span.set_attribute(
                "rag.collections",
                ",".join(sorted({q.collection for q in plan.queries})),
            )
            if plan.earnings_ticker:
                span.set_attribute("rag.earnings_ticker", plan.earnings_ticker)
        except Exception:
            pass

        tasks = [
            _query_rag_thread(ks, q.collection, user_message, q.n_results, q.where)
            for q in plan.queries
        ]
        parts = await asyncio.gather(*tasks) if tasks else []
        merged: List[dict] = []
        for p in parts:
            merged.extend(p)

        if plan.earnings_ticker:
            merged.extend(
                earnings_hits_as_rag_rows(ks, user_message, plan.earnings_ticker, n=4)
            )

        merged_cap = 72
        if len(merged) > merged_cap:
            merged = merged[:merged_cap]

        ranked = rerank_hits(merged)[:RAG_TOP_K]

        try:
            span.set_attribute("rag.merged_count", len(merged))
            span.set_attribute("rag.ranked_count", len(ranked))
        except Exception:
            pass

        lines = []
        for i, h in enumerate(ranked, 1):
            doc = (h.get("document") or "").strip()
            if not doc:
                continue
            meta = h.get("metadata") or {}
            src = (
                meta.get("source")
                or meta.get("ticker")
                or meta.get("strategy_name")
                or meta.get("channel")
                or "knowledge"
            )
            lines.append(f"[{i}] ({src}) {doc[:1200]}")
            if out_refs is not None:
                try:
                    out_refs.append(
                        {
                            "chunk_id": str(h.get("id") or ""),
                            "collection": str(h.get("collection") or ""),
                            "rank": i - 1,
                            "distance": float(h.get("distance", 1.0)),
                            "ticker": str((meta or {}).get("ticker") or (meta or {}).get("symbol") or ""),
                        }
                    )
                except Exception:
                    pass
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
    tracer = get_tracer()
    with tracer.start_as_current_span("chat.prewarm_predictive_rag") as span:
        async def run_one(key: str, q: str):
            ctx = await chat_rag_context(ks, q)
            return key, ctx

        pairs = await asyncio.gather(*[run_one(k, q) for k, q in PREDICTIVE_QUERIES])
        result = {k: v for k, v in pairs}
        try:
            span.set_attribute("prewarm.keys", ",".join(result.keys()))
            span.set_attribute("prewarm.count", len(result))
        except Exception:
            pass
        return result


def _build_system_prompt(
    market_snapshot: dict,
    user_ctx: dict,
    pipeline_status: Optional[dict],
    user_preferences_block: str = "",
    memory_instructions_block: str = "",
) -> str:
    snap = json.dumps(market_snapshot, indent=2, default=str)[:8000]
    uctx = json.dumps(user_ctx, indent=2, default=str)[:4000]
    pipe = json.dumps(pipeline_status or {}, indent=2, default=str)[:2000]
    prompt = (
        "You are TradeTalk, a concise finance and education assistant. "
        "Lead with a one-sentence takeaway, then details. "
        "Cite retrieved context when used; do not invent facts. "
        "This is educational context, not personalized investment advice.\n\n"
        "## HARD RULES — numeric market data\n"
        "- Do **not** state specific stock **prices**, **percent changes**, **ranks**, or "
        "\"top N\" gainers/losers unless they come from (1) a **tool result** in this chat turn, or "
        "(2) an **AUTHORITATIVE MOVER DATA** / **Live Market Intelligence** block in this system prompt.\n"
        "- Do **not** state **historical** returns, period high/low, or YTD / 5Y performance figures unless they "
        "come from **get_price_history** (or another tool result), not from memory.\n"
        "- If the user asks for rankings, movers, or who's up/down today, call **get_top_movers** "
        "(or use the AUTHORITATIVE block when present). Never fabricate plausible tickers or numbers.\n"
        "- If verified data is missing or still loading, say so plainly — do not guess.\n"
        "- Rankings prefer TradeTalk's **live Yahoo fast_info scan** (session % vs prior close), "
        "else a scheduled daily batch; data may be delayed ~15m. Other sites may rank differently.\n\n"
        "## Tool routing (use the right fetch; do not invent numbers)\n"
        "- **One ticker, current quote / valuation fields** → `get_stock_quote`.\n"
        "- **Historical performance**, YTD, 1y/5y return, period high/low, \"how did X do over...\" → "
        "`get_price_history` (Yahoo daily bars).\n"
        "- **Ranked gainers/losers**, market movers → `get_top_movers`.\n"
        "- **Macro / broad market why** (Fed, geopolitics, sector themes) → `get_market_news`.\n"
        "- **Company-specific why / recent events** → `get_deep_news(ticker)`.\n"
        "- **SEC filing text** (10-K, 10-Q, 8-K) → `get_sec_filing`.\n"
        "- **Pasted or named URL** → `scrape_url`.\n"
        "- **Retrieval / L1 blocks** below may contain KB or snapshot facts — cite them, but if the user "
        "asks for **exact** numbers not there, call the matching tool.\n\n"
        f"## Market snapshot (cached)\n{snap}\n\n"
        f"## User portfolio context (if any)\n{uctx}\n\n"
        f"## Knowledge pipeline status\n{pipe}\n"
    )
    if user_preferences_block:
        prompt += f"\n{user_preferences_block}\n"
    if memory_instructions_block:
        prompt += f"\n{memory_instructions_block}\n"
    return prompt


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


async def build_fresh_system_prompt(ks, user_id: Optional[str]) -> str:
    """
    Recompute the base system prompt (instructions + L1 snapshot + portfolio + pipeline + prefs).
    Call on **every** chat message so rule text and cached numbers stay current without a new session.
    """
    async def l1():
        return market_l1_cache.get_snapshot()

    async def pipe():
        try:
            return ks.stats().get("pipeline_status") or {}
        except Exception:
            return {}

    async def uctx():
        return await get_user_context_block(user_id)

    async def load_prefs():
        if not user_id:
            return ""
        try:
            from . import user_preferences as up
            return await asyncio.to_thread(up.format_for_system_prompt, user_id)
        except Exception as e:
            logger.debug("[Chat] preference load failed: %s", e)
            return ""

    async def load_mem_instr():
        if not user_id:
            return ""
        try:
            from . import user_preferences as up
            return await asyncio.to_thread(up.format_agent_memory_instructions)
        except Exception as e:
            logger.debug("[Chat] memory instructions failed: %s", e)
            return ""

    market_snapshot, pipeline_status, user_ctx, pref_block, mem_instr = await asyncio.gather(
        l1(), pipe(), uctx(), load_prefs(), load_mem_instr()
    )
    return _build_system_prompt(
        {"l1": market_snapshot, "updated_at": market_l1_cache.updated_at_epoch()},
        user_ctx,
        pipeline_status,
        user_preferences_block=pref_block,
        memory_instructions_block=mem_instr,
    )


async def create_session(
    ks,
    user_id: Optional[str],
    ttl_seconds: int = 86400,
) -> ChatSession:
    """asyncio.gather snapshot assembly + predictive RAG pre-warm + preference loading."""
    tracer = get_tracer()
    with tracer.start_as_current_span("chat.create_session") as span:
        system_prompt = await build_fresh_system_prompt(ks, user_id)

        rag_prewarm = await prewarm_predictive_rag(ks)

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
        persist_chat_session(sess)

        try:
            span.set_attribute("session.id", sid)
            span.set_attribute("session.user_id", user_id or "anonymous")
            span.set_attribute("session.has_preferences", bool(user_id))
        except Exception:
            pass

        return sess


async def resume_session(
    ks,
    user_id: Optional[str],
    resume_session_id: str,
    ttl_seconds: int = 86400,
) -> Optional[ChatSession]:
    """
    Restore a session from SQLite if id is valid, not expired, and user_id matches the row.
    Refreshes system prompt and extends TTL.
    """
    sid = (resume_session_id or "").strip()
    if len(sid) < 8:
        return None
    row = chat_session_store.load_session_row(sid)
    if not row:
        return None
    now = time.time()
    if row["expires_at"] < now:
        chat_session_store.delete_session_row(sid)
        return None
    if not chat_session_store.user_matches_row(row["user_id"], user_id):
        return None
    sess = _hydrate_session_from_db(sid)
    if not sess:
        return None
    sess.expires_at = now + ttl_seconds
    sess.system_prompt = await build_fresh_system_prompt(ks, user_id)
    async with _SESSION_LOCK:
        _SESSIONS[sid] = sess
    persist_chat_session(sess)
    return sess


def _prune_sessions() -> None:
    now = time.time()
    dead = [k for k, s in list(_SESSIONS.items()) if s.expires_at < now]
    for k in dead:
        _SESSIONS.pop(k, None)
    chat_session_store.prune_expired_rows(now)


def get_session(session_id: str) -> Optional[ChatSession]:
    """Return cached or hydrate from SQLite."""
    now = time.time()
    _prune_sessions()
    s = _SESSIONS.get(session_id)
    if s is not None:
        if s.expires_at < now:
            _SESSIONS.pop(session_id, None)
            chat_session_store.delete_session_row(session_id)
            return None
        return s
    loaded = _hydrate_session_from_db(session_id)
    if loaded:
        _SESSIONS[session_id] = loaded
    return loaded


async def gather_message_context(
    ks,
    session: ChatSession,
    user_message: str,
) -> Tuple[str, dict]:
    """
    Concurrent: RAG (or prewarm fast path), L1 read, user_ctx refresh, freshness meta.
    Returns (rag_block, meta_dict).
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("chat.gather_message_context") as span:
        # Collected in-place by chat_rag_context when taken through the non-prewarm
        # path. Prewarm responses are pre-computed blocks without per-hit refs, so
        # the list may be empty even when the RAG block is non-empty.
        rag_chunk_refs: List[dict] = []

        async def rag_task() -> str:
            key = _classify_prewarm_key(user_message)
            if key and key in session.rag_prewarm:
                try:
                    span.set_attribute("rag.prewarm_hit", key)
                except Exception:
                    pass
                return session.rag_prewarm[key]
            return await chat_rag_context(
                ks, user_message, session.sticky_state, out_refs=rag_chunk_refs
            )

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

        async def coral_hub_task() -> str:
            def _sync_hub() -> str:
                from .coral_hub import format_hub_context_block

                snap = market_l1_cache.get_snapshot() or {}
                cs = snap.get("credit_stress_index")
                reg = ""
                if cs is not None:
                    reg = "BULL_NORMAL" if float(cs) <= 1.1 else "BEAR_STRESS"
                return format_hub_context_block(market_regime=reg)

            return await asyncio.to_thread(_sync_hub)

        rag_block, l1_snap, uctx, fresh, coral_block = await asyncio.gather(
            rag_task(),
            l1_task(),
            user_task(),
            fresh_task(),
            coral_hub_task(),
        )

        # Include sticky state in meta for frontend awareness + evidence contract (Layer 1)
        meta = {
            **fresh,
            "l1_keys": list((l1_snap or {}).get("quotes", {}).keys()),
            "user_ctx_nonempty": bool(uctx),
            "sticky_state": session.sticky_state,
            "rag_nonempty": bool(rag_block and len(rag_block.strip()) > 40),
            "coral_hub_nonempty": bool((coral_block or "").strip()),
            "rag_chunk_refs": list(rag_chunk_refs),
        }

        # Build sticky state context if available
        sticky_block = ""
        if session.sticky_state:
            sticky_block = f"## Conversation state (sticky)\n{json.dumps(session.sticky_state, default=str)[:1000]}\n"

        hub_prefix = (coral_block or "").strip()
        if hub_prefix:
            hub_prefix = hub_prefix + "\n\n"

        attribution = (
            hub_prefix
            + "\n## Retrieval\n"
            f"{rag_block}\n\n"
            f"{sticky_block}"
            "## Instructions\n"
            "1. If the user is just saying hello, making small talk, or asking a broad non-financial question, reply naturally and conversationally. Do NOT recite the snapshot or retrieval data unless directly relevant.\n"
            "2. When you use facts from the retrieval block, mention they come from TradeTalk's knowledge base. "
            "Blend with the live L1 snapshot below when relevant.\n"
            "3. For numeric or time-series questions not fully answered by retrieval/L1, use the appropriate "
            "chat tools (quote, price history, movers, news, filings) — do not invent figures.\n"
            f"## L1 refresh snapshot\n{json.dumps(l1_snap, default=str)[:4000]}\n"
            f"## User context (refreshed)\n{json.dumps(uctx, default=str)[:4000]}\n"
        )
        return attribution, meta


# ── Ticker regex (case-sensitive, uppercase only) ─────────────────────────────
_STICKY_TICKER_RE = re.compile(r"(?<![A-Za-z])[A-Z]{2,5}(?![A-Za-z])")

# Common words that look like tickers but aren't
_TICKER_STOP = frozenset({
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HER",
    "WAS", "ONE", "OUR", "OUT", "HAS", "HIS", "HOW", "ITS", "MAY", "NEW",
    "NOW", "OLD", "SEE", "WAY", "WHO", "DID", "GET", "LET", "SAY", "SHE",
    "TOO", "USE", "DAD", "MOM", "BUY", "WHY", "TOP", "CEO", "IPO", "ETF",
    "GDP", "CPI", "VIX", "ATH", "EPS", "ROE", "ROA", "RSI", "USD", "EUR",
    "YTD", "FAQ", "USA", "PDF", "API", "SEC", "FED",
})


def update_sticky_state(
    session: ChatSession,
    user_msg: str,
    tool_results: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Post-turn extraction of structured state from the user message.

    Updates session.sticky_state with:
      - active_ticker: most recently mentioned ticker
      - analysis_mode: debate | backtest | chat | gold | macro
      - last_tool_used: name of last tool called (if any)
      - mentioned_tickers: rolling list of recently mentioned tickers
      - turn_count: number of turns in this session
    """
    state = session.sticky_state

    # ── Turn counter ──────────────────────────────────────────────────────
    state["turn_count"] = state.get("turn_count", 0) + 1

    # ── Ticker extraction ─────────────────────────────────────────────────
    raw_tickers = _STICKY_TICKER_RE.findall(user_msg)
    valid_tickers = [t for t in raw_tickers if t not in _TICKER_STOP]
    if valid_tickers:
        state["active_ticker"] = valid_tickers[-1]
        # Rolling deduped list, most recent first, max 10
        mentioned = state.get("mentioned_tickers", [])
        for t in reversed(valid_tickers):
            if t not in mentioned:
                mentioned.insert(0, t)
        state["mentioned_tickers"] = mentioned[:10]

    # ── Analysis mode detection ───────────────────────────────────────────
    msg_lower = user_msg.lower()
    if any(k in msg_lower for k in ("backtest", "strategy", "simulate")):
        state["analysis_mode"] = "backtest"
    elif any(k in msg_lower for k in ("debate", "bull case", "bear case", "bull vs")):
        state["analysis_mode"] = "debate"
    elif any(k in msg_lower for k in ("gold", "precious metal", "bullion")):
        state["analysis_mode"] = "gold"
    elif any(k in msg_lower for k in ("macro", "economy", "recession", "fed ", "interest rate")):
        state["analysis_mode"] = "macro"

    # ── Tool tracking ─────────────────────────────────────────────────────
    if tool_results:
        last_tool = list(tool_results.keys())[-1] if tool_results else None
        if last_tool:
            state["last_tool_used"] = last_tool

    state["last_updated"] = time.time()
    persist_chat_session(session)


def update_session_last_turn(
    session: ChatSession,
    user_message: str,
    assistant_text: str,
    evidence_contract: Dict[str, Any],
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist last turn for evidence memo export (in-memory session)."""
    session.last_user_message = (user_message or "")[:12000]
    session.last_assistant_text = (assistant_text or "")[:24000]
    session.last_evidence_contract = dict(evidence_contract) if evidence_contract else None
    session.last_chat_meta = dict(meta or {})
    persist_chat_session(session)


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
        persist_chat_session(sess)
    except Exception as e:
        logger.warning("[Chat] refresh_session failed: %s", e)
