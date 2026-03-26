# AGENTS.md

## Cursor Cloud specific instructions

### Architecture overview

TradeTalk is an AI-powered investment analysis platform with two tightly-coupled services:

| Service | Tech | Port | Start command |
|---------|------|------|---------------|
| **Backend** | Python 3.12 + FastAPI + Uvicorn | 8000 | `cd /workspace && PYTHONPATH=. python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000` |
| **Frontend** | React 19 + Vite 7 (JSX) | 5173 | `cd /workspace/frontend && npm run dev` |

Both ChromaDB (vector store) and SQLite (auth/progress) are embedded — no external database services needed for local dev.

### Environment files (not committed)

- `backend/.env` — copy from `backend/.env.example`; set `VECTOR_BACKEND=chroma` for local dev (avoids Supabase dependency). Pre-seeded ChromaDB data lives in `/workspace/chroma_db/`.
- `frontend/.env.local` — set `VITE_API_BASE_URL=http://localhost:8000`.

### Key dev-mode behaviors

- Without `OPENROUTER_API_KEY`, the LLM client uses a **rule-based fallback** — AI features still work but with templated responses.
- Without `GOOGLE_CLIENT_ID`, the app enters **DEV_MODE** — authentication is bypassed with a hardcoded dev user.
- The `SP500_INGEST_ON_STARTUP` defaults to `true` in non-Render environments; set to `0` to skip the slow Yahoo Finance ingest on startup.

### Running tests

Backend smoke tests (per `CLAUDE.md`):
```bash
cd /workspace && PYTHONPATH=. python3 -m unittest discover -s backend/tests -p 'test_*.py' -v
```

### Build

Frontend production build: `cd /workspace/frontend && npm run build`

### Known gotchas

- `python3-dev` build headers are required for `chroma-hnswlib` (C++ extension). The update script ensures they are present.
- `httpx>=0.28` breaks `starlette` `TestClient` (which `fastapi==0.110.0` ships). Pin `httpx<0.28` after installing requirements to keep tests working. The update script handles this.
- ChromaDB telemetry emits `capture() takes 1 positional argument but 3 were given` warnings — these are harmless and can be ignored.
- The frontend has no ESLint or TypeScript config despite `CLAUDE.md` mentioning TypeScript; the codebase is pure JSX. Use `npm run build` (Vite/esbuild) as the lint/compile check.

### Scalability constraints (single-process)

The backend is designed for single-process deployment:
- **SSE client list** (`sse_clients` in `deps.py`) is in-memory — no cross-worker broadcasting.
- **L1 cache** (`cache.py`) is an in-process `OrderedDict` — no shared cache across workers.
- **APScheduler** runs cron jobs in-process — running multiple workers would duplicate jobs.
- **SQLite** uses thread-local connections — suitable for moderate write loads; for higher concurrency, migrate to PostgreSQL.

For multi-worker scaling, the recommended path is:
1. Replace SSE with Redis Pub/Sub for real-time notifications.
2. Replace L1 cache with Redis.
3. Move scheduler to a dedicated worker process or use an external scheduler (e.g. Celery Beat).
4. Migrate SQLite to PostgreSQL for concurrent writes.
