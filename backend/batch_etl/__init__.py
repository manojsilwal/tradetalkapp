"""Batch ETL: yfinance profile text → HF Dataset archive + Supabase pgvector (yf_batch_chunks)."""

from .pipeline import chunk_text, run_batch_etl

__all__ = ["chunk_text", "run_batch_etl"]
