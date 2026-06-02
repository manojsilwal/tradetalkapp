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

Use **Actions → Run workflow** to test manually.

## 4. `keep_alive.py` (HF Space / self-ping)

If `PIPELINE_CRON_SECRET` is set, the hourly S&P 500 re-ingest POST includes `Authorization: Bearer …` automatically.

Set `HF_SPACE_URL` to your deployed API base URL when using this module on GCP, Render, or HF.

## 5. Disable heavy data-lake work on small instances

```bash
DATA_LAKE_DAILY_INCREMENTAL=0
```

See `backend/daily_pipeline.py`.
