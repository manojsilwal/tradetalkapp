"""
Data Lake — S&P 500 historical + events pipeline (Phases 4–7).

Phase 4 — `ingest_events`: earnings (EPS + revenue enrich), splits, dividends,
         insider (normalized cols), institutional/major holders, recommendations.
         Preferred storage: ``events/{TICKER}_{kind}.parquet`` (legacy ``events/{TICKER}/`` still read).

Phase 5 — `summarize_for_rag`: stock_profiles, earnings_memory, price_movements patterns,
         macro_snapshots (quarterly regime). Optional OpenRouter polish via ``--no-llm`` to disable.

Phase 6 — Agents read ``stock_profiles`` / ``earnings_memory`` via KnowledgeStore + debate RAG.

Phase 7 — `incremental`: daily OHLCV append + rotating event slices; Mondays: insider + recs refresh.
         Wired into ``backend.daily_pipeline`` (disable with ``DATA_LAKE_DAILY_INCREMENTAL=0``).

Usage:
    python -m backend.data_lake.run_full_ingestion --dry-run
    python -m backend.data_lake.run_full_ingestion --tickers AAPL,MSFT
    python -m backend.data_lake.summarize_for_rag --tickers AAPL
    python -m backend.data_lake.upload_to_supabase --dry-run
"""
