# Cron, keep-alive, and scheduled pipelines

## Render free tier behavior

Web services **sleep** after ~15 minutes without **incoming** HTTP traffic. While asleep, nothing runs (no APScheduler, no background loops).

## 1. Keep the API warm — external GET

Point a free monitor at a cheap endpoint every **10–14 minutes**, for example:

- `GET https://<your-backend>.onrender.com/docs`

Options: **UptimeRobot**, **cron-job.org**, **GitHub Actions** (see `.github/workflows/render-wake.yml`).

## 2. Secure cron triggers — `PIPELINE_CRON_SECRET`

Set in **Render → Environment** (and locally in `.env`):

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

- `RENDER_API_BASE` — e.g. `https://tradetalkapp-backend.onrender.com` (no trailing slash)
- `PIPELINE_CRON_SECRET` — same value as Render

## 3. GitHub Actions workflows

| Workflow | Schedule | Action |
|----------|----------|--------|
| `render-wake.yml` | Every 10 minutes | `GET /docs` |
| `render-daily-pipeline.yml` | 00:05 UTC daily | `POST /knowledge/pipeline-run` with Bearer secret |

Use **Actions → Run workflow** to test manually.

## 4. `keep_alive.py` (HF Space / self-ping)

If `PIPELINE_CRON_SECRET` is set, the hourly S&P 500 re-ingest POST includes `Authorization: Bearer …` automatically.

Set `HF_SPACE_URL` to your deployed API base URL when using this module on Render or HF.

## 5. Disable heavy data-lake work on small instances

```bash
DATA_LAKE_DAILY_INCREMENTAL=0
```

See `backend/daily_pipeline.py`.
