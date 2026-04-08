-- Optional production tuning: approximate nearest neighbor index on embeddings.
-- Run in Supabase SQL after bootstrap (001 / supabase_pgvector_bootstrap.sql) and
-- after you have representative data volume (empty/small tables: index build is cheap).
--
-- Prerequisites: pgvector extension (already in bootstrap), embedding dimension 1536
-- matches match_vector_memory and OpenRouter embedding model output.
--
-- HNSW (recommended for most read-heavy workloads; good recall vs latency tradeoff)
-- Drop first if re-creating: DROP INDEX IF EXISTS vector_memory_embedding_hnsw_idx;
create index if not exists vector_memory_embedding_hnsw_idx
  on public.vector_memory
  using hnsw (embedding vector_cosine_ops);

-- IVFFlat alternative (often better for very large tables after ANALYZE; needs lists tuning):
-- Requires enough rows to train lists — typically run after bulk load.
-- create index if not exists vector_memory_embedding_ivfflat_idx
--   on public.vector_memory
--   using ivfflat (embedding vector_cosine_ops)
--   with (lists = 100);
--
-- Query path: public.match_vector_memory filters by collection + metadata @> filter,
-- then orders by embedding <=> query. Postgres uses the HNSW/IVFFlat index when the
-- planner estimates it wins over sequential scan; keep collection + metadata selective.
