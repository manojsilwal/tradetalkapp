#!/usr/bin/env bash
# run_daily_market_job.sh — Incremental daily prices + news + movement links
#
# Runs weekday mornings before US market open (~15–25 min).
# Fetches only missing trade dates since last BigQuery row.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="${PYTHONPATH:-$ROOT}"
export MCP_DATA_BACKEND="${MCP_DATA_BACKEND:-bigquery}"
export GCP_PROJECT_ID="${GCP_PROJECT_ID:-tradetalkapp-492904}"
export BQ_DATASET_ID="${BQ_DATASET_ID:-tradetalk_swarm}"
export GCS_BUCKET="${GCS_BUCKET:-tradetalk-data-lake}"

THROUGH_DATE="${THROUGH_DATE:-}"
DRY_RUN="${DRY_RUN:-0}"

PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  done
fi
if [[ -z "$PYTHON" ]]; then
  echo "No python3 found on PATH" >&2
  exit 1
fi

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

log "=== Daily Market Incremental Job ==="
log "MCP_DATA_BACKEND=$MCP_DATA_BACKEND"

log "[0/5] Ensure BigQuery tables exist..."
"$PYTHON" -m backend.mcp_server.bq_schema

EXTRA_ARGS=()
if [[ "$DRY_RUN" == "1" ]]; then
  EXTRA_ARGS+=(--dry-run)
fi
if [[ -n "$THROUGH_DATE" ]]; then
  EXTRA_ARGS+=(--through "$THROUGH_DATE")
fi

log "[1/5] Prices + events incremental upsert..."
if ((${#EXTRA_ARGS[@]})); then
  "$PYTHON" -m backend.data_lake.daily_market_update "${EXTRA_ARGS[@]}" --skip-links --skip-features
else
  "$PYTHON" -m backend.data_lake.daily_market_update --skip-links --skip-features
fi

log "[2/5] Feature mart + gold context..."
"$PYTHON" -m backend.mcp_server.feature_mart
"$PYTHON" -m backend.mcp_server.gold_context

log "[3/5] Incremental movement links + context..."
"$PYTHON" - <<'PY'
import os
from datetime import date, timedelta
from backend.data_lake.daily_market_update import (
    LAG_LINK_DAYS,
    get_bq_last_trade_date,
    resolve_ingest_window,
    target_through_date,
)
from backend.mcp_server.build_movement_links import run_incremental_movement_links

through = os.environ.get("THROUGH_DATE")
through_d = date.fromisoformat(through) if through else target_through_date()
window = resolve_ingest_window(through=through_d)
if window is None:
    last = get_bq_last_trade_date()
    if last and last >= through_d:
        start = (through_d - timedelta(days=LAG_LINK_DAYS)).isoformat()
        end = through_d.isoformat()
        print(f"No new prices; refreshing links for lag window {start} → {end}")
        run_incremental_movement_links(start_date=start, end_date=end)
    else:
        print("Skip links — no ingest window and no BQ baseline")
else:
    ingest_start, ingest_end = window
    link_start = (ingest_start - timedelta(days=LAG_LINK_DAYS)).isoformat()
    link_end = ingest_end.isoformat()
    print(f"Incremental links {link_start} → {link_end}")
    run_incremental_movement_links(start_date=link_start, end_date=link_end)
PY

log "[4/5] Materialize daily brief snapshot (heuristic)..."
"$PYTHON" -m backend.daily_brief --materialize-heuristic || log "Daily brief materialize skipped"

log "[5/5] RAG bridge (last week)..."
"$PYTHON" -m backend.mcp_server.rag_bridge --week || log "RAG bridge skipped"

log "=== Daily job complete ==="

"$PYTHON" - <<'PY'
import os
os.environ.setdefault("MCP_DATA_BACKEND", "bigquery")
from backend.mcp_server.backend import backend
from backend.data_lake.daily_market_update import get_bq_last_trade_date

last = get_bq_last_trade_date()
print(f"  daily_prices max trade_date: {last}")
for table in ("events_curated", "movement_event_links", "movement_context_daily"):
    try:
        rows = backend().query(f"SELECT COUNT(*) AS n FROM {table}")
        print(f"  {table}: {rows[0]['n'] if rows else '?'} rows")
    except Exception as e:
        print(f"  {table}: error — {e}")
PY
