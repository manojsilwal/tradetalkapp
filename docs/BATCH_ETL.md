# Batch ETL (yfinance → Hugging Face Dataset + Supabase)

This pipeline implements the architecture described in the project plan: **HF Dataset** holds versioned Parquet archives; **Supabase `pgvector`** holds vectors with metadata (`ticker`, `source`, `chunk_level`, etc.) for filtered ANN search via `match_vector_memory`.

## What it does

- For each ticker, pulls **yfinance** profile text (company summary, sector, industry, key fields).
- **Chunks** text with overlap for RAG.
- Optionally uploads a **Parquet** snapshot to a Hugging Face **Dataset** repo (`batch_etl/yfinance_profiles_<timestamp>.parquet`).
- **Upserts** chunks into collection **`yf_batch_chunks`** in Supabase with OpenRouter embeddings.

SEC filing ingestion can extend the same module later; v1 is yfinance-only.

## One-off run (local)

From the repository root:

```bash
export OPENROUTER_EMBEDDING_MODEL=text-embedding-3-small
export SUPABASE_URL=...
export SUPABASE_SERVICE_ROLE_KEY=...
export HF_DATASET_ID=your-org/your-dataset   # optional
export HF_TOKEN=...                            # optional; use a secret, never commit

PYTHONPATH=. python backend/scripts/batch_etl_hf_supabase.py --tickers AAPL,MSFT,GOOGL
```

- `--no-hf` — skip Dataset upload (vectors only).
- `--no-supabase` — Parquet to HF only (no embedding upsert).

## Chat RAG

When a ticker is active, `plan_chat_rag` includes **`yf_batch_chunks`** with `where: { ticker }` so retrieval stays scoped like other ticker collections.

## Supabase index tuning

After bootstrap, apply **`backend/migrations/supabase/002_hnsw_vector_memory_embedding.sql`** for an **HNSW** index on `embedding` (cosine). See comments in that file for **IVFFlat** and operational notes.

## GitHub Actions

Workflow **`.github/workflows/batch-etl-hub.yml`** runs weekly (and on manual dispatch). Configure repository secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `OPENROUTER_API_KEY`, `OPENROUTER_EMBEDDING_MODEL`, and optionally `HF_DATASET_ID`, `HF_TOKEN`. Optional repository variable **`BATCH_ETL_TICKERS`** (comma-separated symbols; if unset, the workflow defaults to `SPY,AAPL,MSFT`).
