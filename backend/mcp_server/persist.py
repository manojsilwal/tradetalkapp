"""
Pipeline persistence — writes ALL daily pipeline events to BigQuery permanently.

Every pipeline output (movers, macro, news, CORAL notes) gets a permanent row.
Nothing is ever deleted. This is the training data accumulator.

Usage from daily_pipeline.py:
    from backend.mcp_server.persist import persist_pipeline_snapshot, persist_agent_learning
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ENABLED: Optional[bool] = None


def _is_enabled() -> bool:
    """Check if BQ persistence is available (graceful fallback)."""
    global _ENABLED
    if _ENABLED is None:
        import os
        _ENABLED = os.environ.get("MCP_DATA_BACKEND", "duckdb") == "bigquery"
        if not _ENABLED:
            logger.info("[Persist] BigQuery persistence disabled (MCP_DATA_BACKEND != bigquery)")
    return _ENABLED


def persist_pipeline_snapshot(
    snapshot_type: str,
    payload: Any,
    summary: str = "",
) -> Optional[str]:
    """
    Write a pipeline event to BigQuery pipeline_snapshots table.

    Args:
        snapshot_type: One of: top_movers, macro_snapshot, sector_rotation, news_scan,
                       coral_heartbeat, dream_synthesis, coral_reflection
        payload: Raw pipeline output (JSON-serializable)
        summary: Human-readable one-liner for RAG indexing

    Returns:
        snapshot_id if written, None if persistence is disabled/failed.
    """
    snapshot_id = str(uuid.uuid4())
    row = {
        "snapshot_id": snapshot_id,
        "snapshot_date": str(date.today()),
        "snapshot_type": snapshot_type,
        "payload_json": json.dumps(payload, default=str) if payload else "{}",
        "summary_text": (summary or "")[:4000],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if not _is_enabled():
        logger.debug("[Persist] Would write pipeline_snapshot: %s (%s)", snapshot_type, snapshot_id)
        return snapshot_id

    try:
        from .backend import backend
        written = backend().insert_rows("pipeline_snapshots", [row])
        if written:
            logger.debug("[Persist] Wrote pipeline_snapshot: %s", snapshot_id)
        return snapshot_id
    except Exception as e:
        logger.warning("[Persist] Failed to write pipeline_snapshot: %s", e)
        return None


def persist_agent_learning(
    agent_id: str,
    learning_type: str,
    observation: str,
    market_regime: str = "",
    feature_context: Optional[Dict] = None,
    source_pipeline_run: Optional[str] = None,
) -> Optional[str]:
    """
    Write an agent learning to BigQuery agent_learnings table (permanent, no TTL).

    Args:
        agent_id: CORAL agent ID (data_ingest, technical, sentiment, gold_analysis, etc.)
        learning_type: note, skill, reflection, handoff, dream_synthesis
        observation: The actual learning text
        market_regime: Market context at write time
        feature_context: Optional feature dict for context
        source_pipeline_run: FK to pipeline_snapshots.snapshot_id

    Returns:
        learning_id if written, None if persistence is disabled/failed.
    """
    learning_id = str(uuid.uuid4())
    row = {
        "learning_id": learning_id,
        "agent_id": (agent_id or "unknown")[:64],
        "learning_type": (learning_type or "note")[:32],
        "observation": (observation or "")[:8000],
        "market_regime": (market_regime or "")[:64],
        "feature_context_json": json.dumps(feature_context, default=str) if feature_context else "{}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_pipeline_run": source_pipeline_run or "",
    }

    if not _is_enabled():
        logger.debug("[Persist] Would write agent_learning: %s/%s (%s)", agent_id, learning_type, learning_id)
        return learning_id

    try:
        from .backend import backend
        written = backend().insert_rows("agent_learnings", [row])
        if written:
            logger.debug("[Persist] Wrote agent_learning: %s", learning_id)
        return learning_id
    except Exception as e:
        logger.warning("[Persist] Failed to write agent_learning: %s", e)
        return None


def persist_events_curated(events: List[Dict]) -> int:
    """Batch-write curated events to BigQuery."""
    if not events:
        return 0

    rows = []
    for evt in events:
        rows.append({
            "event_id": evt.get("event_id", str(uuid.uuid4())),
            "published_at": evt.get("published_at", datetime.now(timezone.utc).isoformat()),
            "category": evt.get("category", "unknown"),
            "source": evt.get("source", ""),
            "headline": (evt.get("headline", "") or "")[:1000],
            "body_text": (evt.get("body_text", "") or "")[:10000],
            "affected_symbols": evt.get("affected_symbols", []),
            "sentiment_score": evt.get("sentiment_score"),
            "dedupe_cluster_id": evt.get("dedupe_cluster_id", ""),
            "embedding_id": evt.get("embedding_id", ""),
        })

    if not _is_enabled():
        logger.debug("[Persist] Would write %d events_curated rows", len(rows))
        return len(rows)

    try:
        from .backend import backend
        return backend().insert_rows("events_curated", rows)
    except Exception as e:
        logger.warning("[Persist] Failed to write events_curated: %s", e)
        return 0


def persist_macro_policy_events(events: List[Dict]) -> int:
    """Batch-write macro/policy events to BigQuery macro_policy_events."""
    if not events:
        return 0

    rows = []
    for evt in events:
        rows.append({
            "event_id": evt.get("event_id", str(uuid.uuid4())),
            "published_at": evt.get("published_at", datetime.now(timezone.utc).isoformat()),
            "category": evt.get("category", "macro_data"),
            "headline": (evt.get("headline", "") or "")[:1000],
            "body_text": (evt.get("body_text", "") or "")[:10000],
            "affected_symbols": evt.get("affected_symbols", []),
            "source": evt.get("source", "fred"),
        })

    if not _is_enabled():
        logger.debug("[Persist] Would write %d macro_policy_events rows", len(rows))
        return len(rows)

    try:
        from .backend import backend
        return backend().insert_rows("macro_policy_events", rows)
    except Exception as e:
        logger.warning("[Persist] Failed to write macro_policy_events: %s", e)
        return 0
