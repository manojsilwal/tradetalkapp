#!/usr/bin/env bash
# run_sp500_ingest_job.sh — Full S&P ingest + movement↔event linking pipeline
#
# Runs inside Cloud Run Job (or locally with MCP_DATA_BACKEND=bigquery).
#
# Phases:
#   1. Full price ingest (530 tickers, checkpoint reset)
#   2. Gap fill retry
#   3. Upload parquets → GCS → BigQuery
#   4. Feature mart + gold context
#   5. Macro policy + daily events → events_curated
#   6. Movement links + movement_context_daily
#   7. RAG bridge index

set -euo pipefail

cd /app

export PYTHONPATH="${PYTHONPATH:-/app}"
export MCP_DATA_BACKEND="${MCP_DATA_BACKEND:-bigquery}"
export DATA_LAKE_DIR="${DATA_LAKE_DIR:-/tmp/data_lake_output}"
export GCP_PROJECT_ID="${GCP_PROJECT_ID:-tradetalkapp-492904}"
export BQ_DATASET_ID="${BQ_DATASET_ID:-tradetalk_swarm}"
export GCS_BUCKET="${GCS_BUCKET:-tradetalk-data-lake}"

SKIP_PRICES="${SKIP_PRICES:-0}"
SKIP_EVENTS="${SKIP_EVENTS:-0}"
RECENT_NEWS_DAYS="${RECENT_NEWS_DAYS:-90}"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

log "=== S&P Full Ingest Job ==="
log "MCP_DATA_BACKEND=$MCP_DATA_BACKEND DATA_LAKE_DIR=$DATA_LAKE_DIR"

# Ensure BQ tables exist
log "[0/7] Creating/updating BigQuery tables..."
python -m backend.mcp_server.bq_schema

if [[ "$SKIP_PRICES" != "1" ]]; then
  log "[1/7] Full price ingest (~530 tickers, 2-4 hrs)..."
  python -m backend.data_lake.run_full_ingestion \
    --phase prices \
    --reset-checkpoint

  log "[1b/7] Events ingest (earnings, insider, etc.)..."
  python -m backend.data_lake.run_full_ingestion --phase events || log "Events phase had failures"

  log "[2/7] Gap fill retry..."
  python -m backend.data_lake.fill_gaps || log "Gap fill had failures (continuing)"

  log "[3/7] Upload prices → GCS → BigQuery..."
  python scripts/sync_prices_to_bq.py

  log "[3b/7] Upload events parquets → GCS..."
  python scripts/initial_load_to_gcs.py || log "GCS events upload skipped"
else
  log "[1-3/7] SKIP_PRICES=1 — skipping price ingest"
fi

log "[4/7] Feature mart + gold context..."
python -m backend.mcp_server.feature_mart
python -m backend.mcp_server.gold_context

if [[ "$SKIP_EVENTS" != "1" ]]; then
  log "[5/7] Macro policy + daily events..."
  python - <<'PY'
import os
os.environ.setdefault("MCP_DATA_BACKEND", "bigquery")
from backend.mcp_server.backend import backend
from backend.mcp_server.bq_schema import FULL_DATASET
for table in ("macro_policy_events", "events_curated"):
    try:
        backend().execute(f"TRUNCATE TABLE `{FULL_DATASET}.{table}`")
        print(f"Truncated {table}")
    except Exception as e:
        print(f"Truncate {table} skipped: {e}")
PY
  python -m backend.data_lake.ingest_macro_policy
  python -m backend.data_lake.ingest_daily_events --recent-news-days "$RECENT_NEWS_DAYS"
else
  log "[5/7] SKIP_EVENTS=1 — skipping event ingest"
fi

log "[6/7] Build movement links + context..."
python -m backend.mcp_server.build_movement_links

log "[7/7] RAG bridge (last week)..."
python -m backend.mcp_server.rag_bridge --week || log "RAG bridge skipped"

log "=== Job complete ==="

# Summary counts
python - <<'PY'
import os
os.environ.setdefault("MCP_DATA_BACKEND", "bigquery")
from backend.mcp_server.backend import backend
for table in ("daily_prices", "events_curated", "macro_policy_events",
              "movement_event_links", "movement_context_daily"):
    try:
        rows = backend().query(f"SELECT COUNT(*) AS n FROM {table}")
        print(f"  {table}: {rows[0]['n'] if rows else '?'} rows")
    except Exception as e:
        print(f"  {table}: error — {e}")
PY
