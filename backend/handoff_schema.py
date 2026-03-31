"""
Structured handoff contracts between agent stages.

These Pydantic models define the explicit data shape passed between pipeline
stages (router → agents, swarm → debate, agents → moderator).  Using
structured handoffs instead of re-querying RAG or re-parsing user messages
eliminates redundant work and reduces token consumption.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class QueryExtraction(BaseModel):
    """
    Output of stage 1 (router): structured fields extracted once from
    the user query, consumed by all downstream agents without re-parsing.
    """
    route: str = "general"                   # sql | rag | python | general
    tickers: List[str] = Field(default_factory=list)
    timeframe: Optional[str] = None          # "1m", "3m", "1y", "ytd"
    constraints: Dict[str, Any] = Field(default_factory=dict)
    original_query: str = ""
    detected_intent: str = ""                # "compare", "screen", "explain", etc.


class DebateSharedContext(BaseModel):
    """
    Pre-fetched context shared across all 5 debate agents.

    Instead of each agent independently querying 4-6 ChromaDB collections,
    the orchestrator fetches all RAG context ONCE into this model and hands
    it to every agent, eliminating ~5x redundant vector queries.
    """
    ticker: str
    live_data: Dict[str, Any] = Field(default_factory=dict)
    macro_state: Dict[str, Any] = Field(default_factory=dict)
    rag_context: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Pre-fetched RAG docs per collection: {collection_name: [docs]}"
    )
    reflection_docs: List[str] = Field(default_factory=list)
    reflection_telemetry: Dict[str, Any] = Field(default_factory=dict)
    swarm_context: str = ""


class AgentHandoff(BaseModel):
    """
    Generic structured handoff from one agent to the next.

    Used when Agent A produces findings that Agent B should consume directly
    (via structured JSON fields) rather than Agent B re-querying RAG.
    """
    source_agent: str = ""
    target_agent: str = ""
    extracted_facts: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.5
    note: str = ""
