# TradeTalk API — GCP Cloud Run (production backend)

The **FastAPI backend is deployed only on GCP Cloud Run**, not Render.

| Layer | Host |
|--------|------|
| Frontend | Vercel (`VITE_API_BASE_URL` → Cloud Run URL) |
| API | Cloud Run service `tradetalk-api` (default) |
| Data lake / daily brief cron | Cloud Run **Jobs** (`sp500-daily-update`, etc.) |

## One-time deploy

```bash
bash scripts/deploy_api_cloudrun.sh
```

Requires `gcloud` auth and IAM to build to `gcr.io/tradetalkapp-492904/tradetalk-api`.

## After deploy

1. Copy the service URL from the script output (e.g. `https://tradetalk-api-xxxxx-uc.a.run.app`).
2. **Vercel** → Project → Environment → `VITE_API_BASE_URL` = that URL (Production).
3. **GitHub** → Secrets → `TRADETALK_API_BASE` = same URL (for cron / wake workflows).
4. **Cloud Run** → `tradetalk-api` → Variables & secrets — add secrets that were on Render:
   - `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY` / `GOOGLE_API_KEY`, `NVIDIA_API_KEY`, `OPENROUTER_API_KEY`, `FINCRAWLER_KEY`, `PIPELINE_CRON_SECRET`, `POSTGRES_PASSWORD`, etc.
   - **Auth (required for sign-in):** `JWT_SECRET` (strong random), `GOOGLE_CLIENT_ID` (OAuth Web client). Set `PORTFOLIO_STORAGE=postgres` (deploy script default) so users, watchlists, and chat history persist on Cloud SQL.
   - **Frontend (Vercel):** `VITE_GOOGLE_CLIENT_ID` = same as `GOOGLE_CLIENT_ID`; keep `VITE_AUTH_REQUIRED=false` for public browsing with optional sign-in.

The runtime service account defaults to `tradetalk-etl@tradetalkapp-492904.iam.gserviceaccount.com` (BigQuery + GCS). Override with `CLOUD_RUN_API_SA=...`.

## Redeploy without rebuild

```bash
bash scripts/deploy_api_cloudrun.sh --skip-build
```

## CORS

Pass your Vercel origin when deploying:

```bash
CORS_ORIGINS=https://your-app.vercel.app bash scripts/deploy_api_cloudrun.sh
```

## CI

`.github/workflows/gcp-api-deploy.yml` builds and deploys on pushes to `main` that touch `backend/**` (requires `GCP_WORKLOAD_IDENTITY` or repo secret with a deploy key — or run the script locally).

## Render

`render.yaml` no longer defines `tradetalkapp-backend`. FinCrawler runs on GCP VM `dreamrise-gcp` (`FINCRAWLER_URL=http://34.71.218.179:10000`); Cloud Run picks this up via `scripts/deploy_api_cloudrun.sh`.
