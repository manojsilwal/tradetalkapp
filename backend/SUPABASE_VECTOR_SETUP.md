# Supabase Vector Storage Setup

Reflections and RAG memory survive Render restarts when using Supabase instead of ChromaDB.

## Prerequisites

- Supabase project (free tier: 500 MB database, sufficient for vector memory).
- Your `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` from [Supabase Dashboard → Settings → API](https://supabase.com/dashboard/project/_/settings/api).

## 1. Run the bootstrap SQL

In **Supabase Dashboard → SQL Editor**, run the contents of `backend/supabase_pgvector_bootstrap.sql`:

- Creates `vector_memory` table.
- Enables pgvector extension.
- Creates `match_vector_memory` function for semantic search.

## 2. Set environment variables

**Local (.env):**
```env
VECTOR_BACKEND=supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
```

**Render:**
- `VECTOR_BACKEND` and `SUPABASE_URL` are set in `render.yaml`.
- Add `SUPABASE_SERVICE_ROLE_KEY` in **Render Dashboard → Your Service → Environment** as a secret.

## 3. Optional: embeddings for semantic search

Without embeddings, the backend uses **lexical (keyword) search** — works fine on the free tier and does not consume OpenRouter quota.

For semantic search, add:
```env
OPENROUTER_EMBEDDING_MODEL=openai/text-embedding-3-small
```
This uses OpenRouter API calls; each embedding consumes your daily quota.

## 4. Verify

After deploy, check `GET /knowledge/stats` — it should report `vector_backend: supabase`.
