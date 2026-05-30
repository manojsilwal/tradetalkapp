# GCP Cloud SQL — paper portfolio

Paper positions on production GCP use **Postgres** (Cloud SQL) instead of ephemeral SQLite inside Docker.

## In code (no secrets)

[`backend/gcp_settings.py`](../backend/gcp_settings.py) holds project, instance id, region, public host, database name, and app user. [`backend/postgres_config.py`](../backend/postgres_config.py) reads `POSTGRES_PASSWORD` or `DATABASE_URL` from the environment only.

## Secrets

Set on the VM in **`.env.gcp`** (gitignored):

```bash
POSTGRES_PASSWORD=<from Cloud SQL user setup>
```

Or `DATABASE_URL=postgresql://tradetalk:...@34.31.98.184:5432/tradetalk`.

Copy [`.env.gcp.example`](../.env.gcp.example) as a template.

## Docker (GCP VM)

[`docker-compose.gcp.yml`](../docker-compose.gcp.yml) sets `PORTFOLIO_STORAGE=postgres` and non-secret `POSTGRES_*` fields. Password comes from `env_file: .env.gcp`.

After pull:

```bash
docker compose -f docker-compose.gcp.yml up -d --build
```

On startup the API runs schema migration (`backend/migrations/postgres/001_paper_portfolio.sql`) and, if Postgres is empty, copies rows once from `/app/data/progress.db`.

## Create or repair Cloud SQL

```bash
POSTGRES_APP_PASSWORD='…' ./scripts/setup_gcp_postgres.sh
```

Authorized client network defaults to the app VM (`34.57.42.63/32`). Update `CLOUD_SQL_AUTHORIZED_NETWORK` if the VM IP changes.

## Local dev

Default remains SQLite (`progress.db`). To test Postgres locally, set `PORTFOLIO_STORAGE=postgres` and `POSTGRES_PASSWORD` (and ensure your IP is authorized on the instance).
