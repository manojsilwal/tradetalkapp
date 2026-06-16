#!/usr/bin/env bash
# deploy_api_cloudrun.sh — Build and deploy TradeTalk FastAPI to Cloud Run (production backend).
#
# Backend is NOT deployed to Render. Set Vercel VITE_API_BASE_URL to the service URL printed here.
# Add API keys / secrets in Cloud Run → Variables & secrets (or --update-secrets on redeploy).
#
# Usage:
#   bash scripts/deploy_api_cloudrun.sh
#   bash scripts/deploy_api_cloudrun.sh --skip-build
#   CORS_ORIGINS=https://your-app.vercel.app bash scripts/deploy_api_cloudrun.sh

set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-tradetalkapp-492904}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="${CLOUD_RUN_API_SERVICE:-tradetalk-api}"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest"
SA_EMAIL="${CLOUD_RUN_API_SA:-tradetalk-etl@${PROJECT_ID}.iam.gserviceaccount.com}"
SKIP_BUILD=0

for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
  esac
done

CORS_ORIGINS="${CORS_ORIGINS:-https://frontend-manojsilwals-projects.vercel.app}"

cd "$(dirname "$0")/.."

echo "=== Deploy TradeTalk API → Cloud Run ==="
echo "Project:  $PROJECT_ID"
echo "Region:   $REGION"
echo "Service:  $SERVICE_NAME"
echo "Image:    $IMAGE"
echo "SA:       $SA_EMAIL"
echo ""

gcloud config set project "$PROJECT_ID"

if [[ "$SKIP_BUILD" != "1" ]]; then
  echo "[1/2] Building container image..."
  gcloud builds submit --config cloudbuild.api.yaml .
else
  echo "[1/2] Skipping build (--skip-build)"
fi

# Core environment variables
ENV_VARS="MCP_DATA_BACKEND=bigquery,\
GCP_PROJECT_ID=${PROJECT_ID},\
BQ_DATASET_ID=tradetalk_swarm,\
GCS_BUCKET=tradetalk-data-lake,\
GUARDRAILS_ENABLE=1,\
GUARDRAILS_STRICT_STARTUP=0,\
VECTOR_BACKEND=supabase,\
SUPABASE_URL=https://bvhdrwyxzjcoyqzmtean.supabase.co,\
DECISION_BACKEND=supabase,\
GEMINI_EMBEDDING_MODEL=gemini-embedding-001,\
CORS_ORIGINS=${CORS_ORIGINS},\
PORTFOLIO_STORAGE=postgres,\
SP500_INGEST_ON_STARTUP=0,\
FINCRAWLER_URL=http://34.71.218.179:10000,\
YF_DISABLED_CATEGORIES=info;news,\
FINCRAWLER_MAX_CONCURRENCY=6,\
YF_BREAKER_THRESHOLD=3,\
YF_BREAKER_COOLDOWN_S=600,\
SEPL_TOOL_ENABLE=1,\
SEPL_TOOL_DRY_RUN=0,\
SEPL_TOOL_AUTOCOMMIT=1,\
LLM_HTTP_PROVIDER=openrouter,\
GEMINI_PRIMARY=0,\
LLM_MAX_TOKENS=1500,\
OPENROUTER_MODEL=minimax/minimax-m3,\
OPENROUTER_MODEL_LIGHT=minimax/minimax-m3"

# Load secret keys dynamically from local environment configuration files if present
for env_file in .env.gcp backend/.env .env; do
  if [[ -f "$env_file" ]]; then
    echo "Found local config $env_file, appending keys..."
    while IFS= read -r line || [[ -n "$line" ]]; do
      # Skip comments and empty lines
      if [[ "$line" =~ ^[[:space:]]*# ]] || [[ -z "${line// }" ]]; then
        continue
      fi
      # Trim whitespace
      line=$(echo "$line" | xargs)
      if [[ "$line" == *"="* ]]; then
        key="${line%%=*}"
        val="${line#*=}"
        # Skip variables that are already in the core list
        if [[ "$key" == "SUPABASE_URL" || "$key" == "FINCRAWLER_URL" ]]; then
          continue
        fi
        # Only append keys that are not already present in ENV_VARS to prevent duplicates
        if ! [[ "$ENV_VARS" =~ (^|,)"$key"= ]]; then
          ENV_VARS="${ENV_VARS},${key}=${val}"
        fi
      fi
    done < "$env_file"
  fi
done

echo "[2/2] Deploying Cloud Run service..."
gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --platform managed \
  --service-account "$SA_EMAIL" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 300 \
  --min-instances 0 \
  --max-instances 10 \
  --port 8080 \
  --set-env-vars "$ENV_VARS" \
  --quiet

URL="$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --format='value(status.url)')"
echo ""
echo "Deployed: $URL"
echo "Set GitHub secret TRADETALK_API_BASE=$URL (no trailing slash)"
echo "Set Vercel env VITE_API_BASE_URL=$URL"
echo "Smoke:    curl -sS ${URL}/daily-brief | head"
