#!/usr/bin/env bash
# deploy_api_cloudrun.sh — Build and deploy TradeTalk FastAPI to Cloud Run (production backend).
#
# Backend is NOT deployed to Render. Set Vercel VITE_API_BASE_URL to the service URL printed here.
# Add API keys / secrets in Cloud Run → Variables & secrets (or --update-secrets on redeploy).
# GEMINI_API_KEY and YOUTUBE_API_KEY: bash scripts/upsert_gcp_secret.sh NAME backend/.env
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
VERDICT_CACHE_BACKEND=supabase,\
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
GEMINI_LLM_FALLBACK=1,\
GEMINI_MODEL=gemini-3.5-flash,\
GEMINI_MODEL_LIGHT=gemini-3.5-flash,\
LLM_MAX_TOKENS=1500,\
OPENROUTER_MODEL=minimax/minimax-m3,\
OPENROUTER_MODEL_LIGHT=minimax/minimax-m3,\
STORAGE_BACKEND=gcp,\
BRAIN_GCS_BUCKET=tradetalk-data-lake,\
BRAIN_GCS_PREFIX=brain,\
BRAIN_SERVE_ENABLE=1,\
BRAIN_CUTOVER_ALL=0"

# Strip one KEY=VALUE pair from the comma-separated ENV_VARS string.
_strip_env_key() {
  local key="$1"
  ENV_VARS="$(printf '%s' "$ENV_VARS" | sed -E "s/(^|,)$key=[^,]*//" | sed -E 's/^,//; s/,,*/,/g; s/,$//')"
}

# Keys set in the core ENV_VARS block above must not be overridden by local .env files
# (backend/.env often pins dev models; production defaults stay on Cloud Run).
_CORE_ENV_KEYS="MCP_DATA_BACKEND GCP_PROJECT_ID BQ_DATASET_ID GCS_BUCKET GUARDRAILS_ENABLE GUARDRAILS_STRICT_STARTUP VECTOR_BACKEND SUPABASE_URL DECISION_BACKEND VERDICT_CACHE_BACKEND GEMINI_EMBEDDING_MODEL CORS_ORIGINS PORTFOLIO_STORAGE SP500_INGEST_ON_STARTUP FINCRAWLER_URL YF_DISABLED_CATEGORIES FINCRAWLER_MAX_CONCURRENCY YF_BREAKER_THRESHOLD YF_BREAKER_COOLDOWN_S SEPL_TOOL_ENABLE SEPL_TOOL_DRY_RUN SEPL_TOOL_AUTOCOMMIT LLM_HTTP_PROVIDER GEMINI_PRIMARY GEMINI_LLM_FALLBACK GEMINI_MODEL GEMINI_MODEL_LIGHT LLM_MAX_TOKENS OPENROUTER_MODEL OPENROUTER_MODEL_LIGHT STORAGE_BACKEND BRAIN_GCS_BUCKET BRAIN_GCS_PREFIX BRAIN_SERVE_ENABLE BRAIN_CUTOVER_ALL"

_is_core_env_key() {
  case " $_CORE_ENV_KEYS " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

# Load keys from local env files (.env.gcp → backend/.env → .env).
# Later files override earlier ones. Empty values are never deployed.
# GEMINI_API_KEY and YOUTUBE_API_KEY are mounted from Secret Manager (--set-secrets).
for env_file in .env.gcp backend/.env .env; do
  if [[ -f "$env_file" ]]; then
    echo "Found local config $env_file, merging keys..."
    while IFS= read -r line || [[ -n "$line" ]]; do
      if [[ "$line" =~ ^[[:space:]]*# ]] || [[ -z "${line// }" ]]; then
        continue
      fi
      line=$(echo "$line" | xargs)
      if [[ "$line" == *"="* ]]; then
        key="${line%%=*}"
        val="${line#*=}"
        if [[ "$key" == "SUPABASE_URL" || "$key" == "FINCRAWLER_URL" || "$key" == "YOUTUBE_API_KEY" || "$key" == "GEMINI_API_KEY" ]]; then
          continue
        fi
        if _is_core_env_key "$key"; then
          continue
        fi
        if [[ -z "${val// }" ]]; then
          continue
        fi
        _strip_env_key "$key"
        ENV_VARS="${ENV_VARS},${key}=${val}"
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
  --set-secrets "YOUTUBE_API_KEY=YOUTUBE_API_KEY:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest" \
  --quiet

URL="$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --format='value(status.url)')"
echo ""
echo "Deployed: $URL"
echo "Set GitHub secret TRADETALK_API_BASE=$URL (no trailing slash)"
echo "Set Vercel env VITE_API_BASE_URL=$URL"
echo "Smoke:    curl -sS ${URL}/daily-brief | head"
