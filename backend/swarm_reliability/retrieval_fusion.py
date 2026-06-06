from __future__ import annotations

import os
from typing import Any, Dict, List


def _rrf_score(rank: int, k: int) -> float:
    # Protect against division by zero or negative k
    k_val = max(1, k)
    return 1.0 / float(k_val + rank + 1)


def _allowed_meta(meta: dict[str, Any], allowed_fields: List[str]) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    out: dict[str, Any] = {}
    for key in allowed_fields:
        if key in meta:
            out[key] = meta[key]
    return out


def _max_text_chars_for_collection(collection: str) -> int:
    c = (collection or "").lower()
    if "news" in c:
        return int(os.environ.get("CHAT_RAG_NEWS_MAX_TEXT_CHARS", "700"))
    return int(os.environ.get("CHAT_RAG_MAX_TEXT_CHARS", "1200"))


def fuse_and_cap_hits(
    channel_hits: Dict[str, List[dict]],
    *,
    rrf_k: int = 60,
    max_records: int = 12,
    allowed_meta_fields: List[str] | None = None,
    harness_session_id: str | None = None,
) -> List[dict]:
    """
    Fuse per-collection ranked hits using RRF, then apply post-fusion L/W/D caps.

    Length cap: max_records on final fused list.
    Width cap: keep only allowed metadata fields.
    Depth cap: truncate per-document text.
    """
    if harness_session_id:
        try:
            from backend.harness.integration import merge_harness_memory_channel

            channel_hits = merge_harness_memory_channel(
                channel_hits, session_id=harness_session_id
            )
        except Exception:
            pass

    allowed_meta_fields = allowed_meta_fields or [
        "source",
        "ticker",
        "symbol",
        "strategy_name",
        "channel",
        "date",
        "timestamp",
        "ingested_at",
        "run_date",
    ]

    by_key: dict[str, dict] = {}
    for collection, hits in (channel_hits or {}).items():
        for rank, h in enumerate(hits or []):
            if not isinstance(h, dict):
                continue
            
            # Use document ID if present, else fall back to a normalized text content key
            # so that duplicate documents across different sources/channels are correctly fused.
            doc_text = str(h.get("document") or "").strip()
            doc_norm = " ".join(doc_text.lower().split())
            key = str(h.get("id") or f"doc:{doc_norm[:120]}")
            
            row = by_key.get(key)
            if row is None:
                row = dict(h)
                row["collection"] = str(h.get("collection") or collection or "")
                row["_rrf"] = 0.0
                by_key[key] = row
            else:
                # Keep the minimum distance (representing highest similarity) across all occurrences of the same document
                dist_val = h.get("distance")
                if dist_val is not None:
                    try:
                        dist_float = float(dist_val)
                        if "distance" not in row or row["distance"] is None:
                            row["distance"] = dist_float
                        else:
                            row["distance"] = min(float(row["distance"]), dist_float)
                    except (ValueError, TypeError):
                        pass

                # Merge metadata keys to ensure all retrieved contexts retain source/symbol metadata for downstream citation
                if "metadata" not in row or not isinstance(row["metadata"], dict):
                    row["metadata"] = {}
                h_meta = h.get("metadata")
                if isinstance(h_meta, dict):
                    for k_meta, v_meta in h_meta.items():
                        if v_meta is not None and row["metadata"].get(k_meta) is None:
                            row["metadata"][k_meta] = v_meta

            try:
                boost = float(h.get("_harness_weight", 1.0))
            except (ValueError, TypeError):
                boost = 1.0
            row["_rrf"] = float(row.get("_rrf", 0.0)) + _rrf_score(rank, rrf_k) * boost

    ranked = sorted(by_key.values(), key=lambda x: float(x.get("_rrf", 0.0)), reverse=True)
    if max_records > 0:
        ranked = ranked[:max_records]

    out: List[dict] = []
    for h in ranked:
        doc = str(h.get("document") or "").strip()
        if not doc:
            continue
        collection = str(h.get("collection") or "")
        meta = _allowed_meta(h.get("metadata") or {}, allowed_meta_fields)
        max_chars = _max_text_chars_for_collection(collection)
        if max_chars > 0:
            doc = doc[:max_chars]
        clipped = dict(h)
        clipped["metadata"] = meta
        clipped["document"] = doc
        out.append(clipped)
    return out

