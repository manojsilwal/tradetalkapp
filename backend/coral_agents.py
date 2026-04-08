"""
CORAL hub — named finance agents (Phase: CORAL multi-agent).

Stable Python surface for notes / skills / attempts. Intended to map cleanly to
MCP tool descriptors if you expose the hub to external agent runtimes later.

Agents:
  data_ingest   — MIL / pipeline freshness, headline cache
  technical     — L1 quotes, VIX, credit stress, sector structure
  sentiment     — headline / news snapshot (not FinBERT scores in v1)
  gold_analysis — GLD / gold proxy + macro link (full Gold Advisor is separate route)
"""
from __future__ import annotations

import logging
from typing import Any, FrozenSet, Optional

from . import coral_hub

logger = logging.getLogger(__name__)

AGENT_DATA_INGEST = "data_ingest"
AGENT_TECHNICAL = "technical"
AGENT_SENTIMENT = "sentiment"
AGENT_GOLD_ANALYSIS = "gold_analysis"

FINANCE_AGENT_IDS: FrozenSet[str] = frozenset(
    {
        AGENT_DATA_INGEST,
        AGENT_TECHNICAL,
        AGENT_SENTIMENT,
        AGENT_GOLD_ANALYSIS,
    }
)

# Known non-finance IDs that may write to the hub (legacy / infra)
_LEGACY_AGENT_IDS: FrozenSet[str] = frozenset({"heartbeat", "swarm_trace", "dream_synthesizer"})

# Optional future MCP-style manifest (documentation / codegen)
CORAL_TOOL_DESCRIPTORS: list[dict[str, Any]] = [
    {
        "name": "coral_hub_add_note",
        "description": "Append a short observation to the CORAL SQLite hub (TTL notes).",
        "parameters": {
            "agent_id": f"One of: {', '.join(sorted(FINANCE_AGENT_IDS))}",
            "observation": "Plain text, <= 8000 chars",
            "market_regime": "Optional e.g. BULL_NORMAL, BEAR_STRESS",
        },
    },
    {
        "name": "coral_hub_add_skill",
        "description": "Upsert a reusable skill line for RAG-adjacent retrieval.",
        "parameters": {"name": "str", "content": "str", "contributed_by": "agent_id"},
    },
    {
        "name": "coral_hub_record_attempt",
        "description": "Log a task attempt with optional signal/score for meta-learning.",
        "parameters": {"task_id": "str", "agent_id": "str", "signal": "float?", "score": "float?"},
    },
]


def _warn_if_unknown_agent(agent_id: str) -> None:
    aid = (agent_id or "").strip()
    if aid in FINANCE_AGENT_IDS or aid in _LEGACY_AGENT_IDS:
        return
    if aid.startswith("test_") or aid == "test_agent":
        return
    logger.warning("[CoralAgents] unregistered agent_id=%s (still writing to hub)", agent_id[:64])


def hub_add_note(
    agent_id: str,
    observation: str,
    *,
    market_regime: str = "",
    ttl_seconds: Optional[float] = None,
) -> int:
    _warn_if_unknown_agent(agent_id)
    return coral_hub.add_note(
        agent_id,
        observation,
        market_regime=market_regime,
        ttl_seconds=ttl_seconds,
    )


def hub_add_skill(
    name: str,
    content: str,
    *,
    contributed_by: str = "",
    skill_id: Optional[str] = None,
    ttl_seconds: Optional[float] = None,
) -> str:
    return coral_hub.add_skill(
        name,
        content,
        contributed_by=contributed_by,
        skill_id=skill_id,
        ttl_seconds=ttl_seconds,
    )


def hub_record_attempt(
    task_id: str,
    agent_id: str,
    signal: Optional[float],
    score: Optional[float],
) -> None:
    _warn_if_unknown_agent(agent_id)
    coral_hub.record_attempt(task_id, agent_id, signal, score)
