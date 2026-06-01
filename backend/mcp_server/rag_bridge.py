"""
RAG Bridge — wires BigQuery rows into Knowledge Store embeddings.

Supabase pgvector stores embeddings with BQ row IDs as metadata.
When agents do semantic search, results include BQ references for full hydration.

This module:
  1. Indexes new pipeline_snapshots and events_curated into the vector store
  2. Stores (embedding, metadata={bq_table, row_id, summary}) in Supabase
  3. Provides a hydration helper: given vector search results, fetch full rows from BQ

Usage:
    python -m backend.mcp_server.rag_bridge --index-recent
    python -m backend.mcp_server.rag_bridge --dry-run
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _index_document(collection: str, text: str, metadata: dict, doc_id: str) -> bool:
    """Add one document to a Knowledge Store collection."""
    try:
        from ..knowledge_store import get_knowledge_store

        ks = get_knowledge_store()
        col = ks._safe_col(collection)
        if not col:
            return False
        meta = {k: (json.dumps(v) if isinstance(v, (list, dict)) else ("" if v is None else v)) for k, v in metadata.items()}
        col.add(documents=[text], metadatas=[meta], ids=[doc_id])
        return True
    except Exception as e:
        logger.debug("[RAG Bridge] index failed for %s: %s", collection, e)
        return False


def index_pipeline_snapshots(days_back: int = 1, dry_run: bool = False) -> int:
    """
    Index recent pipeline_snapshots into Knowledge Store for RAG retrieval.
    Each snapshot's summary_text is embedded and stored with its snapshot_id.
    """
    from .backend import backend

    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    sql = f"""
        SELECT snapshot_id, snapshot_date, snapshot_type, summary_text
        FROM pipeline_snapshots
        WHERE snapshot_date >= '{cutoff}'
          AND summary_text IS NOT NULL
          AND LENGTH(summary_text) > 20
    """
    rows = backend().query(sql)

    if not rows:
        logger.info("[RAG Bridge] No new pipeline snapshots to index")
        return 0

    if dry_run:
        logger.info("[RAG Bridge] Would index %d pipeline snapshots", len(rows))
        return len(rows)

    indexed = 0
    for row in rows:
        text = f"[{row.get('snapshot_type', '')}] {row.get('summary_text', '')}"
        metadata = {
            "bq_table": "pipeline_snapshots",
            "row_id": row.get("snapshot_id", ""),
            "snapshot_type": row.get("snapshot_type", ""),
            "date": str(row.get("snapshot_date", "")),
            "source": "bq_rag_bridge",
        }
        doc_id = f"ps_{row.get('snapshot_id', uuid.uuid4())}"
        if _index_document("pipeline_rag_index", text, metadata, doc_id):
            indexed += 1

    logger.info("[RAG Bridge] Indexed %d/%d pipeline snapshots", indexed, len(rows))
    return indexed


def index_events_curated(days_back: int = 1, dry_run: bool = False) -> int:
    """
    Index recent events_curated into Knowledge Store 'events_semantic' collection.
    Stores event_id in metadata so find_similar_events can hydrate from BQ.
    """
    from .backend import backend

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    sql = f"""
        SELECT event_id, published_at, category, headline, sentiment_score
        FROM events_curated
        WHERE published_at >= '{cutoff}'
          AND headline IS NOT NULL
          AND LENGTH(headline) > 10
    """
    rows = backend().query(sql)

    if not rows:
        logger.info("[RAG Bridge] No new events to index")
        return 0

    if dry_run:
        logger.info("[RAG Bridge] Would index %d events", len(rows))
        return len(rows)

    indexed = 0
    for row in rows:
        text = f"[{row.get('category', '')}] {row.get('headline', '')}"
        metadata = {
            "bq_table": "events_curated",
            "event_id": row.get("event_id", ""),
            "row_id": row.get("event_id", ""),
            "category": row.get("category", ""),
            "sentiment_score": row.get("sentiment_score"),
            "published_at": str(row.get("published_at", "")),
            "source": "bq_rag_bridge",
        }
        doc_id = f"ev_{row.get('event_id', uuid.uuid4())}"
        if _index_document("events_semantic", text, metadata, doc_id):
            indexed += 1

    logger.info("[RAG Bridge] Indexed %d/%d events", indexed, len(rows))
    return indexed


def index_agent_learnings(days_back: int = 1, dry_run: bool = False) -> int:
    """
    Index recent agent_learnings into Knowledge Store for cross-agent RAG.
    Allows any agent to find relevant learnings from other agents.
    """
    from .backend import backend

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    sql = f"""
        SELECT learning_id, agent_id, learning_type, observation, market_regime
        FROM agent_learnings
        WHERE created_at >= '{cutoff}'
          AND observation IS NOT NULL
          AND LENGTH(observation) > 30
    """
    rows = backend().query(sql)

    if not rows:
        logger.info("[RAG Bridge] No new agent learnings to index")
        return 0

    if dry_run:
        logger.info("[RAG Bridge] Would index %d agent learnings", len(rows))
        return len(rows)

    indexed = 0
    for row in rows:
        text = f"[{row.get('agent_id', '')}:{row.get('learning_type', '')}] {row.get('observation', '')}"
        metadata = {
            "bq_table": "agent_learnings",
            "row_id": row.get("learning_id", ""),
            "agent_id": row.get("agent_id", ""),
            "learning_type": row.get("learning_type", ""),
            "market_regime": row.get("market_regime", ""),
            "source": "bq_rag_bridge",
        }
        doc_id = f"al_{row.get('learning_id', uuid.uuid4())}"
        if _index_document("agent_learnings_rag", text, metadata, doc_id):
            indexed += 1

    logger.info("[RAG Bridge] Indexed %d/%d agent learnings", indexed, len(rows))
    return indexed


def hydrate_from_bq(search_results: List[Dict], table: str = None) -> List[Dict]:
    """
    Given vector search results with BQ metadata, fetch full rows from BigQuery.

    Args:
        search_results: List of dicts from Knowledge Store query
            (must have metadata.bq_table and metadata.row_id)
        table: Override table name (optional)

    Returns:
        Enriched results with full BQ row data merged in.
    """
    from .backend import backend

    if not search_results:
        return []

    table_groups: Dict[str, List[str]] = {}
    for r in search_results:
        meta = r.get("metadata") or r
        bq_table = table or meta.get("bq_table", "")
        row_id = meta.get("row_id") or meta.get("event_id") or meta.get("learning_id", "")
        if bq_table and row_id:
            table_groups.setdefault(bq_table, []).append(row_id)

    hydrated_map: Dict[str, Dict] = {}
    for tbl, ids in table_groups.items():
        if not ids:
            continue
        ids_str = ",".join(f"'{i}'" for i in ids)
        id_col = "event_id" if tbl == "events_curated" else "learning_id" if tbl == "agent_learnings" else "snapshot_id"
        sql = f"SELECT * FROM {tbl} WHERE {id_col} IN ({ids_str})"
        try:
            rows = backend().query(sql)
            for row in rows:
                key = row.get(id_col, "")
                if key:
                    hydrated_map[key] = row
        except Exception as e:
            logger.debug("[RAG Bridge] Hydration query failed for %s: %s", tbl, e)

    for r in search_results:
        meta = r.get("metadata") or r
        row_id = meta.get("row_id") or meta.get("event_id") or meta.get("learning_id", "")
        if row_id in hydrated_map:
            r["bq_hydrated"] = hydrated_map[row_id]

    return search_results


def run_full_index(days_back: int = 1, dry_run: bool = False) -> Dict[str, int]:
    """Run all RAG indexing jobs."""
    results = {
        "pipeline_snapshots": index_pipeline_snapshots(days_back, dry_run),
        "events_curated": index_events_curated(days_back, dry_run),
        "agent_learnings": index_agent_learnings(days_back, dry_run),
    }
    logger.info("[RAG Bridge] Full index complete: %s", results)
    return results


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dry = "--dry-run" in sys.argv
    days = 7 if "--week" in sys.argv else 1
    results = run_full_index(days_back=days, dry_run=dry)
    for k, v in results.items():
        print(f"  {k}: {v} indexed")
