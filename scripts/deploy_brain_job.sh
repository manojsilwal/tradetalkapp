#!/usr/bin/env bash
# deploy_brain_job.sh — Cloud Run Job + Cloud Scheduler for the nightly brain run
#
# Schedule: 02:00 UTC Mon–Sat (after the 22:30 UTC evening ingest), so snapshots
# are built on the freshest BigQuery prices.
#
# Usage:
#   bash scripts/deploy_brain_job.sh
#   bash scripts/deploy_brain_job.sh --execute
#   bash scripts/deploy_brain_job.sh --skip-build

set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-tradetalkapp-492904}"
REGION="${GCP_REGION:-us-central1}"
JOB_NAME="brain-nightly"
IMAGE="gcr.io/${PROJECT_ID}/sp500-ingest:latest"   # shares the data-lake image
SA_EMAIL="tradetalk-etl@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_NAME="brain-nightly-trigger"
CRON="0 2 * * 1-6"
EXECUTE=0
SKIP_BUILD=0

for arg in "$@"; do
  case "$arg" in
    --execute) EXECUTE=1 ;;
    --skip-build) SKIP_BUILD=1 ;;
  esac
done

cd "$(dirname "$0")/.."

echo "=== Deploy nightly brain job + scheduler ==="
echo "Project:  $PROJECT_ID"
echo "Job:      $JOB_NAME"
echo "Schedule: $CRON (UTC)"
echo ""

gcloud config set project "$PROJECT_ID"

if [[ "$SKIP_BUILD" != "1" ]]; then
  echo "[1/4] Building container image (shared with S&P ingest)..."
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
  --args scripts/run_brain_job.sh \
  --set-env-vars "MCP_DATA_BACKEND=bigquery,GCP_PROJECT_ID=${PROJECT_ID},BQ_DATASET_ID=tradetalk_swarm,GCS_BUCKET=tradetalk-data-lake,STORAGE_BACKEND=gcp,BRAIN_GCS_BUCKET=tradetalk-data-lake,BRAIN_GCS_PREFIX=brain,BRAIN_MODEL_VERSION=v2" \
  --quiet

echo "[3/4] Cloud Scheduler (OAuth as $SA_EMAIL)..."
JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"

if gcloud scheduler jobs describe "$SCHEDULER_NAME" --location="$REGION" &>/dev/null; then
  gcloud scheduler jobs update http "$SCHEDULER_NAME" \
    --location="$REGION" --schedule="$CRON" --time-zone="UTC" \
    --uri="$JOB_URI" --http-method=POST \
    --oauth-service-account-email="$SA_EMAIL" --quiet
else
  gcloud scheduler jobs create http "$SCHEDULER_NAME" \
    --location="$REGION" --schedule="$CRON" --time-zone="UTC" \
    --uri="$JOB_URI" --http-method=POST \
    --oauth-service-account-email="$SA_EMAIL" --quiet
fi

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
