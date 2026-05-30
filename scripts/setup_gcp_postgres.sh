#!/usr/bin/env bash
# Idempotent Cloud SQL setup for TradeTalk paper portfolio (GCP project tradetalkapp-492904).
# Password is NOT stored in the repo — pass POSTGRES_APP_PASSWORD or set it interactively.
set -euo pipefail

PROJECT="${GCP_PROJECT_ID:-tradetalkapp-492904}"
INSTANCE="${CLOUD_SQL_INSTANCE_ID:-tradetalk-postgres}"
REGION="${CLOUD_SQL_REGION:-us-central1}"
ZONE="${CLOUD_SQL_ZONE:-us-central1-a}"
DB_NAME="${POSTGRES_DB:-tradetalk}"
DB_USER="${POSTGRES_USER:-tradetalk}"
VM_IP="${CLOUD_SQL_AUTHORIZED_NETWORK:-34.57.42.63/32}"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud CLI required" >&2
  exit 1
fi

gcloud config set project "$PROJECT" >/dev/null

if ! gcloud sql instances describe "$INSTANCE" --project="$PROJECT" >/dev/null 2>&1; then
  echo "Creating Cloud SQL instance $INSTANCE ..."
  gcloud sql instances create "$INSTANCE" \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region="$REGION" \
    --zone="$ZONE" \
    --project="$PROJECT"
else
  echo "Instance $INSTANCE already exists."
fi

gcloud sql databases describe "$DB_NAME" --instance="$INSTANCE" --project="$PROJECT" >/dev/null 2>&1 \
  || gcloud sql databases create "$DB_NAME" --instance="$INSTANCE" --project="$PROJECT"

if ! gcloud sql users list --instance="$INSTANCE" --project="$PROJECT" --format='value(name)' | grep -qx "$DB_USER"; then
  if [[ -z "${POSTGRES_APP_PASSWORD:-}" ]]; then
    echo "Set POSTGRES_APP_PASSWORD before creating user $DB_USER" >&2
    exit 1
  fi
  gcloud sql users create "$DB_USER" --instance="$INSTANCE" --password="$POSTGRES_APP_PASSWORD" --project="$PROJECT"
else
  echo "User $DB_USER already exists."
fi

gcloud sql instances patch "$INSTANCE" \
  --authorized-networks="$VM_IP" \
  --project="$PROJECT" \
  --quiet

IP="$(gcloud sql instances describe "$INSTANCE" --project="$PROJECT" --format='value(ipAddresses[0].ipAddress)')"
echo ""
echo "Cloud SQL ready."
echo "  POSTGRES_HOST=$IP"
echo "  POSTGRES_DB=$DB_NAME"
echo "  POSTGRES_USER=$DB_USER"
echo "Add POSTGRES_PASSWORD to .env.gcp on the VM, then redeploy:"
echo "  docker compose -f docker-compose.gcp.yml up -d --build"
