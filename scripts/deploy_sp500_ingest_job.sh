#!/usr/bin/env bash
# deploy_sp500_ingest_job.sh — Build and deploy Cloud Run Job for S&P ingest
#
# Usage:
#   bash scripts/deploy_sp500_ingest_job.sh
#   bash scripts/deploy_sp500_ingest_job.sh --execute

set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-tradetalkapp-492904}"
REGION="${GCP_REGION:-us-central1}"
JOB_NAME="sp500-ingest"
IMAGE="gcr.io/${PROJECT_ID}/${JOB_NAME}:latest"
SA_EMAIL="tradetalk-etl@${PROJECT_ID}.iam.gserviceaccount.com"
EXECUTE=0
SKIP_BUILD=0

for arg in "$@"; do
  if [[ "$arg" == "--execute" ]]; then
    EXECUTE=1
  elif [[ "$arg" == "--skip-build" ]]; then
    SKIP_BUILD=1
  fi
done

cd "$(dirname "$0")/.."

echo "=== Deploy Cloud Run Job: $JOB_NAME ==="
echo "Project: $PROJECT_ID | Region: $REGION"
echo "Image:   $IMAGE"
echo ""

gcloud config set project "$PROJECT_ID"

if [[ "$SKIP_BUILD" != "1" ]]; then
  echo "[1/3] Building container image..."
  gcloud builds submit \
    --config cloudbuild.sp500-ingest.yaml \
    .
else
  echo "[1/3] Skipping build (--skip-build)"
fi

echo "[2/3] Deploying Cloud Run Job..."
gcloud run jobs deploy "$JOB_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --service-account "$SA_EMAIL" \
  --memory 4Gi \
  --cpu 2 \
  --task-timeout 86400 \
  --max-retries 0 \
  --set-env-vars "MCP_DATA_BACKEND=bigquery,GCP_PROJECT_ID=${PROJECT_ID},BQ_DATASET_ID=tradetalk_swarm,GCS_BUCKET=tradetalk-data-lake,DATA_LAKE_DIR=/tmp/data_lake_output,RECENT_NEWS_DAYS=90" \
  --quiet

echo "[3/3] Job deployed: $JOB_NAME"

if [[ "$EXECUTE" == "1" ]]; then
  echo "Executing job..."
  gcloud run jobs execute "$JOB_NAME" --region "$REGION" --wait
  echo "Execution complete."
else
  echo ""
  echo "To execute:"
  echo "  gcloud run jobs execute $JOB_NAME --region $REGION --wait"
fi
