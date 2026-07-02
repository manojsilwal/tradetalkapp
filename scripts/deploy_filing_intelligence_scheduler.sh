#!/usr/bin/env bash
# Schedule filing intelligence batch job at 01:00 UTC (before brain-nightly 02:00).
# Thin wrapper — full scheduler set: bash scripts/deploy_precompute_scheduler.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec bash "$ROOT/scripts/deploy_precompute_scheduler.sh" "$@"
