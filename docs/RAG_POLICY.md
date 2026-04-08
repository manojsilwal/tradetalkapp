# RAG / knowledge store policy

This document describes how **TradeTalk** uses Chroma (or Supabase vector) collections for retrieval-augmented generation (RAG). It aligns with the **memory** and **privacy** principles from the production multi-agent reference architecture (scoped long-term memory, no secrets in the vector store).

## Collections (purpose)

| Collection | Purpose | Typical writers |
|------------|---------|-----------------|
| `swarm_history` | Swarm `/trace` analyses | API |
| `swarm_reflections` | Outcome tracking / learning | Daily pipeline |
| `debate_history` | Debate transcripts | API |
| `macro_alerts` | News-derived alerts | Notifier |
| `strategy_backtests` | Backtest summaries | API |
| `price_movements` | Top movers | Daily pipeline |
| `macro_snapshots` | FRED macro narratives | Daily pipeline |
| `youtube_insights` | Video summaries | Daily pipeline |
| `strategy_reflections` | Post-backtest lessons | Engine |
| `stock_profiles` | Per-ticker narratives | Data lake |
| `earnings_memory` | Earnings events | Data lake |
| `sp500_fundamentals_narratives` | S&P narrative snapshots | Ingestion |
| `sp500_sector_analysis` | Sector rotation | Ingestion |
| `yf_batch_chunks` | Batch ETL yfinance profile chunks (ticker-filtered) | `batch_etl_hf_supabase` / CI |

## TTL and retention

- **Default**: there is **no automatic TTL** in application code today. Embeddings persist until explicitly deleted or the underlying DB is reset.
- **Operational**: treat `CHROMA_PATH` (or Supabase project) as **data at rest** — back up or wipe per environment policy.
- **Recommended**: for production, define **max document age** per collection (e.g. macro alerts) in a scheduled job; this is a future enhancement.

## What must never be embedded

Do **not** store in any collection:

- API keys, JWTs, OAuth tokens, or raw session cookies
- Passwords or credentials
- Full payment or government ID numbers
- **Unredacted** personal email/phone unless product requires it and legal review is complete

Use `redact_secrets_in_text` (and similar) before persisting LLM-generated text when it may echo user input.

## Quotas

- **Per-collection size** is bounded by disk and embedding provider limits; there is no hard per-tenant quota in code yet.
- **Retrieval**: `KnowledgeStore.query` / `query_reflections` cap `n_results` via internal max limits where implemented.

## PII and compliance

- This app is **not** a certified HIPAA/GDPR tool by default.
- For EU users or regulated use cases, complete a **DPA**, **data map**, and **retention schedule** before enabling broad PII ingestion.

## Chat RAG (Layer 1)

- **Planner:** `backend/rag_retrieval.py` builds per-message queries with optional **metadata filters** (e.g. `ticker` on `debate_history`, `price_movements`, `stock_profiles`, `sp500_fundamentals_narratives`, `swarm_history`, `yf_batch_chunks`) so the vector index searches a **narrower candidate set** when the user (or sticky session state) names a symbol.
- **Extras:** Same planner can add `sp500_sector_analysis`, `youtube_insights`, and **earnings** rows via `query_earnings_memory` when keywords match.
- **Fallback:** If a filtered query returns no hits, chat retries **without** the filter for that collection so sparse metadata does not blank retrieval.
- **Debate agents:** `debate_history` / multi-collection reads use the same ticker filter where metadata supports it, with moderator fallback to unfiltered debate history if needed.

## Observability

- RAG reads are traced under span name `rag.query` (when OpenTelemetry is enabled). See `backend/telemetry.py` and env vars `OTEL_*`.
