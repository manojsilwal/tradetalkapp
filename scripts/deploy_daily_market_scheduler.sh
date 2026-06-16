#!/usr/bin/env bash
# deploy_daily_market_scheduler.sh — Cloud Run Job + Cloud Scheduler for daily ingest
#
# Schedule: 12:00 UTC Mon–Fri (~8:00 AM ET) before US market open.
#
# Usage:
#   bash scripts/deploy_daily_market_scheduler.sh
#   bash scripts/deploy_daily_market_scheduler.sh --execute
#   bash scripts/deploy_daily_market_scheduler.sh --skip-build

set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-tradetalkapp-492904}"
REGION="${GCP_REGION:-us-central1}"
JOB_NAME="sp500-daily-update"
IMAGE="gcr.io/${PROJECT_ID}/sp500-ingest:latest"
SA_EMAIL="tradetalk-etl@${PROJECT_ID}.iam.gserviceaccount.com"
# Two triggers per weekday so the just-closed session is ingested the same
# evening (shrinking the overnight "stale" window) and re-checked the next
# morning. The job is idempotent (resolve_ingest_window returns None if current).
#   evening: 22:30 UTC Mon–Fri (~18:30 ET) — after the 16:00 ET cash close + settle
#   morning: 12:00 UTC Mon–Fri (~08:00 ET) — backstop before US market open
SCHEDULER_NAME="sp500-daily-update-trigger"
CRON="0 12 * * 1-5"
SCHEDULER_NAME_PM="sp500-daily-update-trigger-pm"
CRON_PM="30 22 * * 1-5"
EXECUTE=0
SKIP_BUILD=0

for arg in "$@"; do
  case "$arg" in
    --execute) EXECUTE=1 ;;
    --skip-build) SKIP_BUILD=1 ;;
  esac
done

cd "$(dirname "$0")/.."

echo "=== Deploy daily market job + scheduler ==="
echo "Project:  $PROJECT_ID"
echo "Job:      $JOB_NAME"
echo "Schedule: $CRON (UTC, weekdays)"
echo ""

gcloud config set project "$PROJECT_ID"

if [[ "$SKIP_BUILD" != "1" ]]; then
  echo "[1/4] Building container image (shared with full ingest)..."
  gcloud builds submit --config cloudbuild.sp500-ingest.yaml .
else
  echo "[1/4] Skipping build (--skip-build)"
fi

echo "[2/4] Deploying Cloud Run Job..."
gcloud run jobs deploy "$JOB_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --service-account "$SA_EMAIL" \
  --memory 2Gi \
  --cpu 2 \
  --task-timeout 3600 \
  --max-retries 1 \
  --command bash \
  --args scripts/run_daily_market_job.sh \
  --set-env-vars "MCP_DATA_BACKEND=bigquery,GCP_PROJECT_ID=${PROJECT_ID},BQ_DATASET_ID=tradetalk_swarm,GCS_BUCKET=tradetalk-data-lake" \
  --quiet

echo "[3/4] Cloud Scheduler (OAuth as $SA_EMAIL)..."
JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"

upsert_scheduler() {
  local name="$1" cron="$2"
  if gcloud scheduler jobs describe "$name" --location="$REGION" &>/dev/null; then
    gcloud scheduler jobs update http "$name" \
      --location="$REGION" \
      --schedule="$cron" \
      --time-zone="UTC" \
      --uri="$JOB_URI" \
      --http-method=POST \
      --oauth-service-account-email="$SA_EMAIL" \
      --quiet
  else
    gcloud scheduler jobs create http "$name" \
      --location="$REGION" \
      --schedule="$cron" \
      --time-zone="UTC" \
      --uri="$JOB_URI" \
      --http-method=POST \
      --oauth-service-account-email="$SA_EMAIL" \
      --quiet
  fi
}

upsert_scheduler "$SCHEDULER_NAME" "$CRON"
upsert_scheduler "$SCHEDULER_NAME_PM" "$CRON_PM"

echo "[4/4] Grant scheduler SA permission to invoke job..."
gcloud run jobs add-iam-policy-binding "$JOB_NAME" \
  --region="$REGION" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.invoker" \
  --quiet 2>/dev/null || true

echo ""
echo "Deployed:"
echo "  Job:       gcloud run jobs execute $JOB_NAME --region $REGION --wait"
echo "  Scheduler: gcloud scheduler jobs run $SCHEDULER_NAME --location $REGION"

if [[ "$EXECUTE" == "1" ]]; then
  echo ""
  echo "Executing job now..."
  gcloud run jobs execute "$JOB_NAME" --region "$REGION" --wait
fi
