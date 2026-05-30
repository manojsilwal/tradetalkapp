# Paper portfolio — screenshot import (ops & optional data model)

The Robinhood-style flow (upload screenshot or manual rows → Gemini JSON → reconcile preview → apply) is implemented in FastAPI + SQLite `paper_positions`, not in the browser against Supabase. This document is the checklist from the portfolio vision plan: **runtime verification**, **manual QA**, and **optional** hardening.

## Backend routes (auth required)

- `POST /portfolio/parse-holdings-image` — multipart image(s); field `files` (repeatable, up to 10) or legacy single `file`. Uses Gemini vision on the **API** host; merges holdings across images.
- `POST /portfolio/preview-holdings-import` — JSON `items` + `full_snapshot`.
- `POST /portfolio/apply-holdings-import` — persists after user confirms.

Code: `backend/routers/portfolio.py`, reconciliation `backend/portfolio_holdings_reconcile.py`, apply `backend/paper_portfolio.py`, vision `backend/gemini_llm.py`. UI: `frontend/src/PaperPortfolioUI.jsx`.

## Persistence (why holdings disappear)

Holdings are keyed by your signed-in **user id** — not browser memory.

| Environment | Where data lives | Survives browser refresh? | Survives API redeploy? |
|-------------|------------------|---------------------------|-------------------------|
| Local `uvicorn` on :8000 | SQLite `backend/progress.db` | Yes (same API + same login) | Yes |
| GCP Docker (production) | **Cloud SQL Postgres** when `PORTFOLIO_STORAGE=postgres` | Yes | Yes |
| GCP Docker (fallback) | `/app/data/progress.db` on volume `tradetalk-data` | Yes | Yes |

See [`docs/GCP_POSTGRES.md`](GCP_POSTGRES.md) for host/user defaults in code and `POSTGRES_PASSWORD` in `.env.gcp`.

**Also check:** `frontend/.env.local` `VITE_API_BASE_URL` must be the same host you used when adding positions (e.g. always `http://127.0.0.1:8000` or always production). Switching hosts looks like an empty portfolio.

**Login:** Dev login uses `dev_user_001`; Google sign-in uses your Google account id — different portfolios. Stay on one sign-in method.

## Deploy verification (Cloud Run or any API host)

1. **Dependencies** — `python-multipart` must be installed in the API image (declared in `backend/requirements.txt`); without it, multipart upload routes fail at startup or on first parse request.

2. **Gemini / Google key on the API** — Vision runs **server-side**. Set at least one of:
   - `GEMINI_API_KEY`, or
   - `GOOGLE_API_KEY`  
   on the **backend** service (not only the Vercel frontend). Resolution logic: `backend/gemini_llm.resolve_gemini_api_key`.

3. **Confirm env in GCP (example)** — after deploy, from a machine with `gcloud` and access:

   ```bash
   gcloud run services describe SERVICE_NAME --region REGION \
     --format='value(spec.template.spec.containers[0].env)'
   ```

   Ensure the key variables are present (values are redacted in some views; use the console “Variables & secrets” if needed).

4. **Automated checks in repo** — reconciliation logic:  
   `PYTHONPATH=. python3.12 -m unittest backend.tests.test_portfolio_holdings_reconcile -v`  
   Full backend smoke: `./scripts/run_backend_tests.sh` (see `AGENTS.md`).

## Manual QA (import UI smoke)

1. Sign in (Paper Portfolio is behind `GamificationTab` in `frontend/src/App.jsx`).
2. Open **Paper Portfolio** (`/portfolio`).
3. Expand **Import holdings (screenshot or manual)**.
4. Either upload one or more broker screenshots (multi-select, max 10) or add manual rows with ticker + shares (+ optional avg cost).
5. **Preview changes** — confirm reconciliation groups (new / updated / unchanged / removed when “full snapshot” is checked).
6. **Apply to paper portfolio** — confirm positions update and performance reloads.

Playwright: `e2e/smoke.spec.js` includes a lightweight check that `/portfolio` shows either the auth gate or the import entry; full preview/apply still requires a signed-in session (documented above).

## Optional: one row per `(user_id, ticker)` in SQLite

**Today:** `paper_positions` uses `PRIMARY KEY (id, user_id)` and allows multiple open rows per ticker; import **aggregates** open LONGs per ticker in app code and `apply_holdings_import` rewrites toward the target. No DB-level `UNIQUE(user_id, ticker)` is required for the product to work.

**If you want DB-enforced uniqueness later:**

1. **Normalize** — introduce a table such as `holdings_open(user_id, ticker, shares, avg_cost, ...)` with `UNIQUE(user_id, ticker)`, or migrate legacy duplicates via a one-off merge.
2. **Application changes** — `add_position`, `apply_holdings_import`, and any close/adjust paths must assume a single logical row per ticker (or explicitly model lots in a child table).
3. **Risk** — existing users with duplicate open rows need a migration script before enabling `UNIQUE`.

Treat this as a **separate migration project**; do not add a silent `UNIQUE` to the current table without a merge plan.

## Optional: Supabase mirror

If analytics or a second product needs Postgres: add an ETL or webhook from FastAPI after `apply-holdings-import` (or on a schedule) to upsert into a `user_portfolios`-style table. That is **additional** infrastructure; it does not replace SQLite paper portfolio unless you migrate auth and all CRUD.
