from __future__ import annotations

import os
from typing import Any, Dict, List


def _rrf_score(rank: int, k: int) -> float:
    return 1.0 / float(k + rank + 1)


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
) -> List[dict]:
    """
    Fuse per-collection ranked hits using RRF, then apply post-fusion L/W/D caps.

    Length cap: max_records on final fused list.
    Width cap: keep only allowed metadata fields.
    Depth cap: truncate per-document text.
    """
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
            key = str(h.get("id") or f"{collection}:{rank}:{h.get('document','')[:80]}")
            row = by_key.get(key)
            if row is None:
                row = dict(h)
                row["collection"] = str(h.get("collection") or collection or "")
                row["_rrf"] = 0.0
                by_key[key] = row
            row["_rrf"] = float(row.get("_rrf", 0.0)) + _rrf_score(rank, rrf_k)

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

