"""
Chat RAG routing: metadata filters + extra collections so retrieval matches intent
(ticker-scoped debate/movers/swarm/fundamentals) without scanning the full store.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Tunable via env in chat_service (defaults imported at plan time)
_DEFAULT_OVERSAMPLE = 8
_DEFAULT_EXTRA_N = 4


@dataclass
class ChatRagQuery:
    """One vector query: collection, top-k, optional Chroma/Supabase metadata filter."""

    collection: str
    n_results: int
    where: Optional[Dict[str, Any]] = None


@dataclass
class ChatRagPlan:
    """Planned queries for one user message."""

    queries: List[ChatRagQuery] = field(default_factory=list)
    earnings_ticker: Optional[str] = None  # if set, merge earnings_memory via KnowledgeStore.query_earnings_memory


def _extract_tickers_from_message(user_message: str) -> List[str]:
    """Lazy import to avoid circular import with chat_service."""
    from .chat_service import _STICKY_TICKER_RE, _TICKER_STOP

    raw = _STICKY_TICKER_RE.findall(user_message or "")
    return [t for t in raw if t not in _TICKER_STOP]


def resolve_active_ticker(user_message: str, sticky_state: Optional[dict]) -> Optional[str]:
    """
    Prefer tickers mentioned in the current message (last wins), else sticky active_ticker.
    """
    sticky_state = sticky_state or {}
    from_msg = _extract_tickers_from_message(user_message)
    if from_msg:
        return from_msg[-1].upper()
    at = sticky_state.get("active_ticker")
    if isinstance(at, str) and at.strip():
        return at.strip().upper()
    mentioned = sticky_state.get("mentioned_tickers")
    if isinstance(mentioned, list) and mentioned:
        return str(mentioned[0]).upper()
    return None


def plan_chat_rag(
    user_message: str,
    sticky_state: Optional[dict] = None,
    *,
    oversample: int = _DEFAULT_OVERSAMPLE,
    extra_n: int = _DEFAULT_EXTRA_N,
) -> ChatRagPlan:
    """
    Build per-collection queries with optional ``where`` filters.

    - Always include macro + strategy backtests (unfiltered — no per-ticker metadata).
    - When a ticker is known, filter debate_history, price_movements to that ticker
      and add stock_profiles, sp500_fundamentals_narratives, swarm_history (filtered).
    - Keyword routes: sector analysis, YouTube, earnings memory.
    """
    msg = (user_message or "").strip()
    ml = msg.lower()
    ticker = resolve_active_ticker(msg, sticky_state)

    queries: List[ChatRagQuery] = []
    tw: Optional[Dict[str, Any]] = {"ticker": ticker} if ticker else None

    # Core — macro is global; debates/movers benefit from ticker filter when available
    queries.append(ChatRagQuery("macro_snapshots", oversample, None))
    queries.append(ChatRagQuery("debate_history", oversample, tw))
    queries.append(ChatRagQuery("price_movements", oversample, tw))
    queries.append(ChatRagQuery("strategy_backtests", oversample, None))

    if ticker:
        queries.append(ChatRagQuery("stock_profiles", extra_n, {"ticker": ticker}))
        queries.append(ChatRagQuery("sp500_fundamentals_narratives", extra_n, {"ticker": ticker}))
        queries.append(ChatRagQuery("swarm_history", extra_n, {"ticker": ticker}))
        queries.append(ChatRagQuery("yf_batch_chunks", extra_n, {"ticker": ticker}))

    if any(
        k in ml
        for k in (
            "sector",
            "sectors",
            "rotation",
            "industry",
            "xlk",
            "xlf",
            "xle",
            "xlv",
            "xli",
            "xly",
            "xlp",
            "xlb",
            "xlre",
            "xlu",
            "xlc",
        )
    ):
        queries.append(ChatRagQuery("sp500_sector_analysis", extra_n, None))

    if any(k in ml for k in ("youtube", "video", "channel", "podcast")):
        queries.append(ChatRagQuery("youtube_insights", max(3, extra_n // 2), None))

    earnings_ticker: Optional[str] = None
    if ticker and any(
        k in ml
        for k in (
            "earnings",
            "eps",
            "revenue",
            "guidance",
            "beat",
            "miss",
            "10-q",
            "10-k",
            "8-k",
            "filing",
            "sec filing",
        )
    ):
        earnings_ticker = ticker

    return ChatRagPlan(queries=queries, earnings_ticker=earnings_ticker)


def earnings_hits_as_rag_rows(
    ks: Any, user_message: str, ticker: str, n: int = 4
) -> List[dict]:
    """Wrap query_earnings_memory as {document, metadata, distance} for reranking."""
    try:
        docs = ks.query_earnings_memory(ticker, user_message, n_results=n)
    except Exception as e:
        logger.debug("[rag_retrieval] earnings_memory failed: %s", e)
        return []
    out: List[dict] = []
    for d in docs or []:
        if not (d or "").strip():
            continue
        out.append(
            {
                "document": d,
                "metadata": {"source": "earnings_memory", "ticker": ticker},
                "distance": 0.42,
            }
        )
    return out
