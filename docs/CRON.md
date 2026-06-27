# Cron, keep-alive, and scheduled pipelines

Production stack is typically **Vercel** (frontend) + **GCP Cloud Run** (FastAPI backend). Legacy docs below still apply if the backend URL came from another host.

## Cold starts / scale-to-zero

Serverless backends **may sleep** after idle time without **incoming** HTTP traffic. While asleep, nothing runs (no APScheduler, no background loops) unless you set **min instances** > 0.

## 1. Keep the API warm — external GET

Point a free monitor at a cheap endpoint every **10–14 minutes**, for example:

- `GET https://<your-cloud-run-url>/docs`

Options: **UptimeRobot**, **cron-job.org**, **GitHub Actions** (see `.github/workflows/render-wake.yml`, workflow name **API wake**).

## 2. Secure cron triggers — `PIPELINE_CRON_SECRET`

Set in **Cloud Run → Variables & secrets** (or legacy Render env, and locally in `.env`):

```bash
PIPELINE_CRON_SECRET=<long random string>
```

When this is **non-empty**, these routes require authentication:

| Route | Method |
|-------|--------|
| `/knowledge/pipeline-run` | POST |
| `/knowledge/sp500-ingest` | POST |

**Accepted headers** (either):

- `Authorization: Bearer <PIPELINE_CRON_SECRET>`
- `X-Cron-Secret: <PIPELINE_CRON_SECRET>`

If `PIPELINE_CRON_SECRET` is **unset**, behavior matches local dev (open access).

**GitHub Actions:** add repository secrets:

- `TRADETALK_API_BASE` — Cloud Run API origin, e.g. `https://tradetalk-api-xxxxx.run.app` (no trailing slash)
- `PIPELINE_CRON_SECRET` — same value as the backend service env var

## 3. Cloud Scheduler (primary cron)

GCP Cloud Scheduler is the **primary trigger** for all recurring precompute, pipeline,
and prewarm jobs. It is more reliable than GitHub Actions cron (no runner queuing, no
spending-limit issues) and lives in the same GCP project as the Cloud Run API.

**Deploy / update all scheduler jobs:**

```bash
TRADETALK_API_BASE=https://tradetalk-api-xxxxx.run.app \
PIPELINE_CRON_SECRET=<same as API env> \
FUND_LB_ADMIN_TOKEN=<optional> \
bash scripts/deploy_precompute_scheduler.sh
```

Add `--dry-run` to preview without creating/updating jobs.

| Scheduler job name | Cron (UTC) | Target | Auth |
|----|----|----|-----|
| `precompute-knowledge-pipeline` | `5 0 * * *` daily | `POST /knowledge/pipeline-run` | Bearer secret |
| `precompute-picks-shovels` | `10 1 * * *` daily | `POST /knowledge/picks-shovels-run` | Bearer secret |
| `precompute-narrative-radar` | `12 1 * * *` daily | `POST /knowledge/narrative-radar-run` | Bearer secret |
| `macro-flow-daily` | `25 1 * * *` daily | `POST /macro/flow/cron-refresh?interval=1w` | Bearer secret |
| `verdict-prewarm-preopen` | `0 13 * * 1-5` weekdays | `POST /decision-terminal/prewarm` | Bearer secret |
| `verdict-prewarm-midsession` | `30 18 * * 1-5` weekdays | `POST /decision-terminal/prewarm` | Bearer secret |
| `precompute-fund-leaderboard` | `0 6 * * 1` Mondays | `POST /api/funds/ingest/run` | X-Admin-Token |

**Force-run a job:**

```bash
gcloud scheduler jobs run precompute-picks-shovels --location us-central1
```

**View all jobs:**

```bash
gcloud scheduler jobs list --location=us-central1 --project=tradetalkapp-492904
```

**Why Cloud Scheduler instead of GitHub Actions:** Cloud Run scales to zero and
in-process schedulers don't run at zero, and its filesystem is ephemeral. The
Picks & Shovels, Narrative Radar, and Fund Leaderboard pages therefore rely on
(a) **durable snapshots** (`backend/durable_snapshot.py`, Postgres in prod) and
(b) an external cron, which calls the warm endpoints **synchronously** so the
instance stays alive until the durable snapshot is written. See
[PRECOMPUTED_PAGES_PLAN.md](./PRECOMPUTED_PAGES_PLAN.md). Cloud Scheduler is
free for 3 jobs per billing account and very cheap beyond that, with built-in
retry and deadline support.

## 4. GitHub Actions workflows (manual fallback)

Schedule triggers have been **removed** from the following workflows. They retain
`workflow_dispatch` for manual re-runs via **Actions → Run workflow**.

| Workflow | Action |
|----------|--------|
| `render-wake.yml` (API wake) | `GET /docs` (still scheduled every 10 min — keep-alive is fine on Actions) |
| `render-daily-pipeline.yml` (daily knowledge pipeline) | `POST /knowledge/pipeline-run` with Bearer secret |
| `macro-flow-daily.yml` | `POST /macro/flow/cron-refresh` with Bearer secret |
| `precompute-pages.yml` (global page snapshots) | Picks & Shovels + Narrative Radar + Fund Leaderboard |
| `verdict-prewarm.yml` | `POST /decision-terminal/prewarm` with Bearer secret |

Required repo secrets (for manual runs): `TRADETALK_API_BASE`, `PIPELINE_CRON_SECRET`, `FUND_LB_ADMIN_TOKEN` (weekly leaderboard only).

## 5. `keep_alive.py` (HF Space / self-ping)

If `PIPELINE_CRON_SECRET` is set, the hourly S&P 500 re-ingest POST includes `Authorization: Bearer …` automatically.

Set `HF_SPACE_URL` to your deployed API base URL when using this module on GCP, Render, or HF.

## 6. Disable heavy data-lake work on small instances

```bash
DATA_LAKE_DAILY_INCREMENTAL=0
```

See `backend/daily_pipeline.py`.

