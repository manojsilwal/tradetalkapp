#!/usr/bin/env bash
# trigger_all_pipelines.sh — Fire all TradeTalk batch/cron pipelines on production.
#
# Usage:
#   bash scripts/trigger_all_pipelines.sh
#   TRADETALK_API_BASE=https://... PIPELINE_CRON_SECRET=... bash scripts/trigger_all_pipelines.sh
#   bash scripts/trigger_all_pipelines.sh --with-gcp-jobs   # also run Cloud Run Jobs (slow)

set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-tradetalkapp-492904}"
REGION="${GCP_REGION:-us-central1}"
WITH_GCP_JOBS=0

for arg in "$@"; do
  case "$arg" in
    --with-gcp-jobs) WITH_GCP_JOBS=1 ;;
  esac
done

_fetch_cloud_run_env() {
  local key="$1"
  gcloud run services describe tradetalk-api --region "$REGION" --project "$PROJECT_ID" \
    --format="json(spec.template.spec.containers[0].env)" 2>/dev/null \
    | python3 -c "
import json, sys
envs = json.load(sys.stdin).get('spec',{}).get('template',{}).get('spec',{}).get('containers',[{}])[0].get('env',[])
val = next((e.get('value','') for e in envs if e['name'] == '$key'), '')
print(val)
" 2>/dev/null || echo ""
}

BASE="${TRADETALK_API_BASE:-}"
SECRET="${PIPELINE_CRON_SECRET:-}"

if [[ -z "$BASE" ]]; then
  BASE="$(gcloud run services describe tradetalk-api --region "$REGION" --project "$PROJECT_ID" --format='value(status.url)' 2>/dev/null || true)"
fi
if [[ -z "$SECRET" ]]; then
  SECRET="$(_fetch_cloud_run_env PIPELINE_CRON_SECRET)"
fi

if [[ -z "$BASE" ]]; then
  echo "ERROR: Could not determine TRADETALK_API_BASE" >&2
  exit 1
fi

AUTH_HEADERS=(-H "Content-Type: application/json")
if [[ -n "$SECRET" ]]; then
  AUTH_HEADERS+=(-H "Authorization: Bearer ${SECRET}")
fi

post_bg() {
  local path="$1"
  local label="$2"
  echo "→ POST ${BASE}${path} (${label})"
  curl -fsS -X POST "${BASE}${path}" "${AUTH_HEADERS[@]}" || echo "  WARN: ${label} failed"
  echo ""
}

post_sync() {
  local path="$1"
  local label="$2"
  echo "→ POST ${BASE}${path} (${label}, sync — may take minutes)..."
  curl -fsS -m 900 -X POST "${BASE}${path}" "${AUTH_HEADERS[@]}" || echo "  WARN: ${label} failed"
  echo ""
}

echo "=== Trigger all TradeTalk pipelines ==="
echo "API: ${BASE}"
echo ""

if [[ "$WITH_GCP_JOBS" == "1" ]]; then
  echo "[GCP] sp500-daily-update (fresh BQ prices — run before brain)..."
  gcloud run jobs execute sp500-daily-update --region "$REGION" --project "$PROJECT_ID" --wait
  echo ""
  echo "[GCP] brain-nightly (v2 snapshots → GCS)..."
  gcloud run jobs execute brain-nightly --region "$REGION" --project "$PROJECT_ID" --wait
  echo ""
fi

echo "[HTTP] Knowledge & ingest (background)..."
post_bg "/knowledge/pipeline-run" "daily knowledge pipeline"
post_bg "/knowledge/sp500-ingest" "S&P 500 fundamentals ingest"
post_bg "/knowledge/sec-filing-job" "SEC filing job"
post_bg "/knowledge/filing-intelligence-run" "filing intelligence"
post_bg "/decision-terminal/prewarm?tickers=AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA" "verdict cache prewarm"

echo "[HTTP] Macro flow refresh..."
post_bg "/macro/flow/cron-refresh?interval=1w" "macro flow"

echo "[HTTP] Intelligence scans (sync)..."
post_sync "/knowledge/narrative-radar-run" "narrative radar"
post_sync "/knowledge/picks-shovels-run" "picks & shovels"
post_sync "/knowledge/fund-leaderboard-metrics-run" "fund leaderboard metrics"

echo "Done. Check: curl -sS ${BASE}/knowledge/pipeline-status"
