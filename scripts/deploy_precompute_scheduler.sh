#!/usr/bin/env bash
# deploy_precompute_scheduler.sh — Cloud Scheduler HTTP jobs for precompute crons
#
# Replaces the GitHub Actions schedule triggers that previously drove:
#   precompute-pages.yml, macro-flow-daily.yml, verdict-prewarm.yml,
#   render-daily-pipeline.yml
#
# Cloud Scheduler is the PRIMARY trigger. GitHub Actions workflows keep
# workflow_dispatch for manual re-runs only.
#
# Env vars (all auto-fetched from Cloud Run tradetalk-api if not set):
#   TRADETALK_API_BASE      — Cloud Run service URL (no trailing slash)
#   PIPELINE_CRON_SECRET    — must match PIPELINE_CRON_SECRET on the API service
#
# Weekly full 13F ingest: bash scripts/deploy_fund_leaderboard_job.sh
#
# Usage:
#   bash scripts/deploy_precompute_scheduler.sh            # auto-fetch secrets from Cloud Run
#   bash scripts/deploy_precompute_scheduler.sh --dry-run  # preview only
#   PIPELINE_CRON_SECRET=xxx bash scripts/deploy_precompute_scheduler.sh  # explicit override

set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-tradetalkapp-492904}"
REGION="${GCP_REGION:-us-central1}"
SA_EMAIL="tradetalk-etl@${PROJECT_ID}.iam.gserviceaccount.com"
SERVICE_NAME="${CLOUD_RUN_API_SERVICE:-tradetalk-api}"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
  esac
done

# ── Auto-fetch from Cloud Run service if not provided ────────────────
_fetch_cloud_run_env() {
  local key="$1"
  gcloud run services describe "$SERVICE_NAME" --region "$REGION" \
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
  echo "TRADETALK_API_BASE not set — fetching from Cloud Run service URL..."
  BASE="$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --format='value(status.url)' 2>/dev/null || true)"
fi
if [[ -z "$SECRET" ]]; then
  echo "PIPELINE_CRON_SECRET not set — fetching from Cloud Run $SERVICE_NAME env..."
  SECRET="$(_fetch_cloud_run_env PIPELINE_CRON_SECRET)"
fi

if [[ -z "$BASE" ]]; then
  echo "ERROR: Could not determine TRADETALK_API_BASE. Set it explicitly or ensure gcloud is authed."
  exit 1
fi
if [[ -z "$SECRET" ]]; then
  echo "ERROR: Could not determine PIPELINE_CRON_SECRET. Set it explicitly or ensure gcloud is authed."
  exit 1
fi

cd "$(dirname "$0")/.."

echo "=== Deploy precompute Cloud Scheduler jobs ==="
echo "Project:  $PROJECT_ID"
echo "Region:   $REGION"
echo "API base: $BASE"
echo ""

gcloud config set project "$PROJECT_ID"

# ── Upsert helper ────────────────────────────────────────────────────
upsert_scheduler() {
  local name="$1"
  local cron="$2"
  local uri="$3"
  local deadline="$4"
  local retries="$5"
  local header_str="$6"
  local tz="${7:-UTC}"

  echo "  → $name  ($cron $tz)  $uri"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    [dry-run] would create/update scheduler job"
    return
  fi

  local common_args=(
    --location="$REGION"
    --schedule="$cron"
    --time-zone="$tz"
    --uri="$uri"
    --http-method=POST
    --attempt-deadline="${deadline}"
    --max-retry-attempts="${retries}"
    --quiet
  )

  if gcloud scheduler jobs describe "$name" --location="$REGION" &>/dev/null; then
    gcloud scheduler jobs update http "$name" "${common_args[@]}" --update-headers="${header_str}"
  else
    gcloud scheduler jobs create http "$name" "${common_args[@]}" --headers="${header_str}"
  fi
}

# ── Schedule definitions ─────────────────────────────────────────────
# Market-open precompute: 9:30 AM ET weekdays (America/New_York handles DST).
ET_TZ="America/New_York"

echo "[1/8] Filing intelligence batch (01:00 UTC — before brain-nightly)"
upsert_scheduler "filing-intelligence-run" \
  "0 1 * * *" \
  "${BASE}/knowledge/filing-intelligence-run" \
  "1800s" "2" \
  "Content-Type=application/json,Authorization=Bearer ${SECRET}" \
  "UTC"

echo "[2/8] Daily knowledge pipeline (00:05 UTC)"
upsert_scheduler "precompute-knowledge-pipeline" \
  "5 0 * * *" \
  "${BASE}/knowledge/pipeline-run" \
  "900s" "2" \
  "Content-Type=application/json,Authorization=Bearer ${SECRET}" \
  "UTC"

echo "[3/8] Picks & Shovels (9:30 AM ET weekdays)"
upsert_scheduler "precompute-picks-shovels" \
  "30 9 * * 1-5" \
  "${BASE}/knowledge/picks-shovels-run" \
  "900s" "2" \
  "Content-Type=application/json,Authorization=Bearer ${SECRET}" \
  "$ET_TZ"

echo "[4/8] Narrative Rotation Radar (9:32 AM ET weekdays — 2 min stagger)"
upsert_scheduler "precompute-narrative-radar" \
  "32 9 * * 1-5" \
  "${BASE}/knowledge/narrative-radar-run" \
  "900s" "2" \
  "Content-Type=application/json,Authorization=Bearer ${SECRET}" \
  "$ET_TZ"

echo "[5/8] Fund Leaderboard metrics refresh (9:35 AM ET weekdays)"
upsert_scheduler "precompute-fund-leaderboard-metrics" \
  "35 9 * * 1-5" \
  "${BASE}/knowledge/fund-leaderboard-metrics-run" \
  "900s" "2" \
  "Content-Type=application/json,Authorization=Bearer ${SECRET}" \
  "$ET_TZ"

echo "[6/8] Macro flow daily refresh (01:25 UTC daily)"
upsert_scheduler "macro-flow-daily" \
  "25 1 * * *" \
  "${BASE}/macro/flow/cron-refresh?interval=1w" \
  "900s" "2" \
  "Content-Type=application/json,Authorization=Bearer ${SECRET}" \
  "UTC"

echo "[7/8] Verdict cache prewarm — pre-open (13:00 UTC weekdays)"
upsert_scheduler "verdict-prewarm-preopen" \
  "0 13 * * 1-5" \
  "${BASE}/decision-terminal/prewarm" \
  "1800s" "1" \
  "Content-Type=application/json,Authorization=Bearer ${SECRET}" \
  "UTC"

echo "[8/8] Verdict cache prewarm — mid-session (18:30 UTC weekdays)"
upsert_scheduler "verdict-prewarm-midsession" \
  "30 18 * * 1-5" \
  "${BASE}/decision-terminal/prewarm" \
  "1800s" "1" \
  "Content-Type=application/json,Authorization=Bearer ${SECRET}" \
  "UTC"

echo ""
echo "Weekly full 13F ingest is deployed separately (Cloud Run Job, not API fire-and-forget):"
echo "  bash scripts/deploy_fund_leaderboard_job.sh"

echo ""
echo "Done. Verify with:"
echo "  gcloud scheduler jobs list --location=$REGION --project=$PROJECT_ID"
echo ""
echo "Force-run one:"
echo "  gcloud scheduler jobs run precompute-picks-shovels --location=$REGION"
