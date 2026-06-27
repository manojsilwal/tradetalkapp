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

## 3. GitHub Actions workflows

| Workflow | Schedule | Action |
|----------|----------|--------|
| `render-wake.yml` (API wake) | Every 10 minutes | `GET /docs` |
| `render-daily-pipeline.yml` (daily knowledge pipeline) | 00:05 UTC daily | `POST /knowledge/pipeline-run` with Bearer secret |
| `macro-flow-daily.yml` | 01:25 UTC daily | `POST /macro/flow/cron-refresh` with Bearer secret |
| `precompute-pages.yml` (global page snapshots) | 01:10 UTC daily + Mon 06:00 UTC | `POST /knowledge/picks-shovels-run` + `POST /knowledge/narrative-radar-run` (Bearer secret, **synchronous**) daily; `POST /api/funds/ingest/run` (`X-Admin-Token`) weekly |

**Precompute pages (why external cron):** Cloud Run scales to zero and in-process
schedulers don't run at zero, and its filesystem is ephemeral. The Picks & Shovels,
Narrative Radar, and Fund Leaderboard pages therefore rely on (a) **durable snapshots**
(`backend/durable_snapshot.py`, Postgres in prod) and (b) this external cron, which
calls the warm endpoints **synchronously** so the instance stays alive until the
durable snapshot is written. See [PRECOMPUTED_PAGES_PLAN.md](./PRECOMPUTED_PAGES_PLAN.md).
Required repo secrets: `TRADETALK_API_BASE`, `PIPELINE_CRON_SECRET`, `FUND_LB_ADMIN_TOKEN` (weekly leaderboard only).

Use **Actions → Run workflow** to test manually.

## 4. `keep_alive.py` (HF Space / self-ping)

If `PIPELINE_CRON_SECRET` is set, the hourly S&P 500 re-ingest POST includes `Authorization: Bearer …` automatically.

Set `HF_SPACE_URL` to your deployed API base URL when using this module on GCP, Render, or HF.

## 5. Disable heavy data-lake work on small instances

```bash
DATA_LAKE_DAILY_INCREMENTAL=0
```

See `backend/daily_pipeline.py`.
