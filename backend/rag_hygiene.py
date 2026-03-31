"""
RAG Hygiene — periodic cleanup of stale vector store documents.

Per-collection TTL thresholds prevent unbounded growth of the knowledge store.
Collections not listed here are kept indefinitely (e.g. stock_profiles,
earnings_memory, strategy_reflections).

Schedule this as a daily APScheduler job in main.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ── Per-collection TTL (days) ─────────────────────────────────────────────────
# Collections not listed are retained indefinitely.
TTL_DAYS: Dict[str, int] = {
    "macro_alerts":         30,    # news alerts age quickly
    "price_movements":      90,    # daily movers — 3 month window
    "youtube_insights":     90,    # video summaries — 3 month window
    "chat_memories":        180,   # chat recall — 6 month window
    "macro_snapshots":      180,   # macro snapshots — 6 month window
    "swarm_history":        365,   # swarm analyses — 1 year
    "debate_history":       365,   # debate results — 1 year
}


async def cleanup_stale_documents(ks, dry_run: bool = False) -> Dict[str, int]:
    """
    Remove documents older than their collection's TTL threshold.

    Args:
        ks: KnowledgeStore instance.
        dry_run: If True, count but don't delete.

    Returns:
        Dict mapping collection name to number of documents removed (or would-be removed).
    """
    results: Dict[str, int] = {}

    for collection_name, max_age_days in TTL_DAYS.items():
        try:
            col = ks._safe_col(collection_name)
            if not col:
                continue

            count = col.count()
            if count == 0:
                continue

            cutoff_date = (
                datetime.now(timezone.utc) - timedelta(days=max_age_days)
            ).strftime("%Y-%m-%d")

            # Fetch all documents with their metadata and IDs
            rows = col.get(include=["metadatas"])
            all_ids = rows.get("ids", [])
            all_metas = rows.get("metadatas", [])

            stale_ids = []
            for doc_id, meta in zip(all_ids, all_metas):
                if not meta:
                    continue
                doc_date = meta.get("date", "")
                if doc_date and doc_date < cutoff_date:
                    stale_ids.append(doc_id)

            if stale_ids and not dry_run:
                # ChromaDB supports batch delete by IDs
                if hasattr(ks._vector_backend, "_client"):
                    try:
                        chroma_col = ks._vector_backend._client.get_collection(collection_name)
                        chroma_col.delete(ids=stale_ids)
                    except Exception as e:
                        logger.warning(
                            "[RAGHygiene] delete failed for %s: %s",
                            collection_name, e,
                        )

            results[collection_name] = len(stale_ids)
            action = "would remove" if dry_run else "removed"
            if stale_ids:
                logger.info(
                    "[RAGHygiene] %s %d/%d stale docs from %s (cutoff=%s)",
                    action, len(stale_ids), count, collection_name, cutoff_date,
                )

        except Exception as e:
            logger.warning("[RAGHygiene] error processing %s: %s", collection_name, e)
            results[collection_name] = 0

    total = sum(results.values())
    logger.info(
        "[RAGHygiene] cleanup complete — %d total stale docs across %d collections%s",
        total, len(results), " (DRY RUN)" if dry_run else "",
    )
    return results
