#!/usr/bin/env python3
"""
CLI: batch ETL — yfinance profile text → HF Dataset Parquet + Supabase ``yf_batch_chunks``.

Usage (from repo root)::

    PYTHONPATH=. python backend/scripts/batch_etl_hf_supabase.py --tickers AAPL,MSFT,GOOGL

Environment:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, OPENROUTER_API_KEY (required for vectors).
  OPENROUTER_EMBEDDING_MODEL optional (defaults to openai/text-embedding-3-small).
  HF_DATASET_ID, HF_TOKEN (optional — archive Parquet to Hub)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    p = argparse.ArgumentParser(description="Batch ETL: yfinance → HF + Supabase")
    p.add_argument(
        "--tickers",
        type=str,
        required=True,
        help="Comma-separated symbols (e.g. AAPL,MSFT)",
    )
    p.add_argument(
        "--no-hf",
        action="store_true",
        help="Skip Hugging Face Dataset upload",
    )
    p.add_argument(
        "--no-supabase",
        action="store_true",
        help="Skip Supabase vector upsert (archive-only HF path)",
    )
    args = p.parse_args()
    tickers = [x.strip() for x in args.tickers.split(",") if x.strip()]

    from backend.batch_etl.pipeline import run_batch_etl

    out = run_batch_etl(
        tickers,
        upload_hf=not args.no_hf,
        upsert_supabase=not args.no_supabase,
    )
    print(json.dumps(out, indent=2))
    if not out.get("ok") and out.get("error") == "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required":
        logger.error(
            "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (e.g. GitHub Actions: "
            "Settings → Secrets and variables → Actions). Use --no-supabase to skip vectors only."
        )
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
