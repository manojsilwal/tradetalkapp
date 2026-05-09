from __future__ import annotations

from typing import Any, Dict, List

from ..knowledge_store import COLLECTIONS


def list_data_sources() -> List[str]:
    """Return known logical/vector sources available for retrieval planning."""
    return list(COLLECTIONS)


def list_fields(ks, source_or_table: str, sample_limit: int = 10) -> List[str]:
    """
    Return observed metadata fields for a source based on sampled retrieval hits.
    Keeps discovery lightweight and read-only.
    """
    source = str(source_or_table or "").strip()
    if not source:
        return []

    hits = ks.query_with_metadata(source, query_text=source, n_results=max(1, sample_limit))
    fields: set[str] = set()
    for h in hits or []:
        meta = (h or {}).get("metadata") or {}
        if isinstance(meta, dict):
            fields.update(str(k) for k in meta.keys())
    return sorted(fields)


def sample_records(ks, source_or_table: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Return a small sample of rows for schema/value inspection."""
    source = str(source_or_table or "").strip()
    n = max(1, int(limit or 3))
    hits = ks.query_with_metadata(source, query_text=source, n_results=n)
    out: List[Dict[str, Any]] = []
    for h in hits[:n]:
        out.append(
            {
                "id": str(h.get("id") or ""),
                "distance": float(h.get("distance", 1.0)),
                "metadata": h.get("metadata") or {},
                "document": str(h.get("document") or "")[:300],
            }
        )
    return out

