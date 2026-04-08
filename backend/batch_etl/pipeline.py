"""
Batch pipeline: pull yfinance company profile text, chunk with hierarchy metadata,
write Parquet to a Hugging Face Dataset, upsert vectors into Supabase ``yf_batch_chunks``.

SEC filing ingestion can extend this module later; v1 is yfinance-only per plan scope.
"""
from __future__ import annotations

import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Chunking ───────────────────────────────────────────────────────────────────


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> List[str]:
    """Sliding-window chunks for RAG; preserves more context than single splits."""
    t = (text or "").strip()
    if not t:
        return []
    if size <= overlap:
        overlap = max(0, size // 4)
    out: List[str] = []
    i = 0
    n = len(t)
    while i < n:
        piece = t[i : i + size].strip()
        if piece:
            out.append(piece)
        i += max(1, size - overlap)
    return out if out else [t[:size]]


def _yfinance_profile_blob(ticker: str) -> str:
    """Sync: build a single text blob from yfinance Ticker.info (best-effort)."""
    import yfinance as yf

    t = yf.Ticker(ticker.upper())
    info = t.info or {}
    parts: List[str] = []
    name = info.get("longName") or info.get("shortName") or ticker.upper()
    parts.append(f"Company: {name} ({ticker.upper()})")
    if info.get("sector"):
        parts.append(f"Sector: {info.get('sector')}")
    if info.get("industry"):
        parts.append(f"Industry: {info.get('industry')}")
    summ = info.get("longBusinessSummary") or info.get("description") or ""
    if summ:
        parts.append(f"Business summary:\n{summ}")
    for k in ("website", "fullTimeEmployees", "marketCap", "trailingPE", "forwardPE"):
        if info.get(k) is not None:
            parts.append(f"{k}: {info.get(k)}")
    return "\n".join(parts).strip() or f"(no profile text for {ticker.upper()})"


def run_batch_etl(
    tickers: List[str],
    *,
    upload_hf: bool = True,
    upsert_supabase: bool = True,
) -> Dict[str, Any]:
    """
    For each ticker: chunk profile text → optional Parquet upload to HF Dataset →
    upsert into Supabase collection ``yf_batch_chunks`` with OpenRouter embeddings.
    """
    tickers = [x.strip().upper() for x in tickers if x and str(x).strip()]
    if not tickers:
        return {"ok": False, "error": "no_tickers", "rows": 0}

    if upsert_supabase:
        emb = os.environ.get("OPENROUTER_EMBEDDING_MODEL", "").strip()
        if not emb:
            return {
                "ok": False,
                "error": "OPENROUTER_EMBEDDING_MODEL required for Supabase upsert",
                "rows": 0,
            }
        supa = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not supa or not key:
            return {
                "ok": False,
                "error": "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required",
                "rows": 0,
            }

    hf_id = os.environ.get("HF_DATASET_ID", "").strip()
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or ""
    if upload_hf and (not hf_id or not hf_token):
        logger.warning(
            "[batch_etl] HF upload skipped: set HF_DATASET_ID and HF_TOKEN for Hub upload"
        )
        upload_hf = False

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_uuid = str(uuid.uuid4())[:8]
    all_rows: List[Dict[str, Any]] = []

    for sym in tickers:
        try:
            blob = _yfinance_profile_blob(sym)
        except Exception as e:
            logger.warning("[batch_etl] yfinance failed for %s: %s", sym, e)
            continue
        chunks = chunk_text(blob, size=800, overlap=100)
        for idx, chunk in enumerate(chunks):
            cid = f"yf_batch_{sym}_{run_uuid}_{idx}"
            meta = {
                "ticker": sym,
                "source": "yfinance_profile_batch",
                "chunk_level": "paragraph",
                "chunk_index": idx,
                "parent_id": f"{sym}_profile",
                "batch_run_id": run_id,
            }
            all_rows.append(
                {
                    "id": cid,
                    "ticker": sym,
                    "chunk_index": idx,
                    "text": chunk,
                    "metadata": meta,
                }
            )

    if not all_rows:
        return {"ok": False, "error": "no_chunks_built", "rows": 0}

    # ── Hugging Face Dataset (archive only — not a vector index) ─────────────
    if upload_hf and hf_id:
        try:
            import pandas as pd
            from huggingface_hub import HfApi

            df = pd.DataFrame(all_rows)
            with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
                path = tmp.name
            try:
                df.to_parquet(path, index=False)
                api = HfApi(token=hf_token)
                dest = f"batch_etl/yfinance_profiles_{run_id}.parquet"
                api.upload_file(
                    path_or_fileobj=path,
                    path_in_repo=dest,
                    repo_id=hf_id,
                    repo_type="dataset",
                    commit_message=f"batch_etl yfinance profiles {run_id}",
                )
                logger.info("[batch_etl] Uploaded %s to HF dataset %s", dest, hf_id)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        except Exception as e:
            logger.warning("[batch_etl] HF upload failed: %s", e)

    # ── Supabase pgvector ─────────────────────────────────────────────────────
    upserted = 0
    if upsert_supabase:
        from backend.vector_backends import SupabaseVectorBackend

        backend = SupabaseVectorBackend(supa, key)
        collection = "yf_batch_chunks"
        docs = [r["text"] for r in all_rows]
        metas = [r["metadata"] for r in all_rows]
        ids = [r["id"] for r in all_rows]
        backend.add(collection, documents=docs, metadatas=metas, ids=ids, embeddings=None)
        upserted = len(ids)

    return {
        "ok": True,
        "rows": len(all_rows),
        "upserted_supabase": upserted,
        "uploaded_hf": upload_hf and bool(hf_id),
        "run_id": run_id,
        "tickers": tickers,
    }
