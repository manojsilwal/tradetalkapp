#!/usr/bin/env bash
# run_brain_job.sh — Nightly finance-brain snapshot pipeline (Cloud Run Job entry)
#
# Reads BigQuery prices, ensures a registered model, builds per-ticker brain
# snapshots, and persists them to GCS (STORAGE_BACKEND=gcp). Idempotent: snapshots
# are keyed by as_of_date so re-runs overwrite the same day.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="${PYTHONPATH:-$ROOT}"
export MCP_DATA_BACKEND="${MCP_DATA_BACKEND:-bigquery}"
export GCP_PROJECT_ID="${GCP_PROJECT_ID:-tradetalkapp-492904}"
export BQ_DATASET_ID="${BQ_DATASET_ID:-tradetalk_swarm}"
export GCS_BUCKET="${GCS_BUCKET:-tradetalk-data-lake}"
export STORAGE_BACKEND="${STORAGE_BACKEND:-gcp}"
export BRAIN_GCS_BUCKET="${BRAIN_GCS_BUCKET:-tradetalk-data-lake}"
export BRAIN_GCS_PREFIX="${BRAIN_GCS_PREFIX:-brain}"

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

EXTRA_ARGS=()
if [[ "${BRAIN_TIMESFM_ENABLE:-0}" == "1" ]]; then
  EXTRA_ARGS+=(--timesfm)
fi
if [[ -n "${BRAIN_LIMIT:-}" ]]; then
  EXTRA_ARGS+=(--limit "$BRAIN_LIMIT")
fi

log "=== Nightly Finance Brain Job ==="
log "STORAGE_BACKEND=$STORAGE_BACKEND BRAIN_GCS_BUCKET=$BRAIN_GCS_BUCKET/$BRAIN_GCS_PREFIX"

if ((${#EXTRA_ARGS[@]})); then
  "$PYTHON" -m backend.brain.run_brain_pipeline "${EXTRA_ARGS[@]}"
else
  "$PYTHON" -m backend.brain.run_brain_pipeline
fi

log "=== Brain job complete ==="
