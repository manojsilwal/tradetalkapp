#!/usr/bin/env bash
# run_fund_leaderboard_job.sh — Fund Leaderboard batch job (Cloud Run Job entry)
#
# Default: full weekly 13F ingest + clone returns.
# Set FUND_LB_JOB_MODE=metrics for the daily price-only refresh (no SEC ingest).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="${PYTHONPATH:-$ROOT}"

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

MODE="${FUND_LB_JOB_MODE:-full}"
log "=== Fund Leaderboard Job (mode=$MODE) ==="

if [[ "$MODE" == "metrics" ]]; then
  "$PYTHON" -m backend.fund_leaderboard_job --metrics-only
else
  "$PYTHON" -m backend.fund_leaderboard_job
fi

log "=== Fund Leaderboard job complete ==="
