from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List


def _parse_meta_date(meta: dict) -> datetime | None:
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


def _recency_boost(meta: dict, recency_lambda: float = 0.02) -> float:
    dt = _parse_meta_date(meta)
    if dt is None:
        return 1.0
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_h = max(0.0, (now - dt).total_seconds() / 3600.0)
    return math.exp(-recency_lambda * age_h)


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

            # Compute similarity boost (1.0 + similarity)
            dist_val = h.get("distance")
            if dist_val is not None:
                try:
                    dist_float = float(dist_val)
                    sim = max(0.0, min(1.0, 1.0 - dist_float))
                except (ValueError, TypeError):
                    sim = 0.0
            else:
                sim = 0.0

            # Compute recency boost based on age decay
            recency = _recency_boost(h.get("metadata") or {})

            row["_rrf"] = float(row.get("_rrf", 0.0)) + _rrf_score(rank, rrf_k) * boost * (1.0 + sim) * recency

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
        if "source" not in meta or not meta["source"]:
            meta["source"] = collection or "database"
        max_chars = _max_text_chars_for_collection(collection)
        if max_chars > 0:
            doc = doc[:max_chars]
        clipped = dict(h)
        clipped["metadata"] = meta
        clipped["document"] = doc
        out.append(clipped)
    return out


def clean_and_cap_raw_hits(
    hits: List[dict],
    *,
    max_records: int = 12,
    allowed_meta_fields: List[str] | None = None,
) -> List[dict]:
    """Clean and cap raw hits without RRF fusion, preserving citation/hygiene rules."""
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
    if max_records > 0:
        hits = hits[:max_records]
    out: List[dict] = []
    for h in hits:
        doc = str(h.get("document") or "").strip()
        if not doc:
            continue
        collection = str(h.get("collection") or "")
        meta = _allowed_meta(h.get("metadata") or {}, allowed_meta_fields)
        if "source" not in meta or not meta["source"]:
            meta["source"] = collection or "database"
        max_chars = _max_text_chars_for_collection(collection)
        if max_chars > 0:
            doc = doc[:max_chars]
        clipped = dict(h)
        clipped["metadata"] = meta
        clipped["document"] = doc
        out.append(clipped)
    return out


