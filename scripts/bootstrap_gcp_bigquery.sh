#!/usr/bin/env bash
# bootstrap_gcp_bigquery.sh — One-shot GCP resource creation for the
# TradeTalk S&P 500 data substrate (BigQuery + GCS).
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (`gcloud auth login`)
#   - Project tradetalkapp-492904 exists
#
# Usage:
#   bash scripts/bootstrap_gcp_bigquery.sh

set -euo pipefail

PROJECT_ID="tradetalkapp-492904"
REGION="us-central1"
BQ_DATASET="tradetalk_swarm"
GCS_BUCKET="tradetalk-data-lake"
SERVICE_ACCOUNT="tradetalk-etl"
SA_EMAIL="${SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "=== TradeTalk BigQuery + GCS Bootstrap ==="
echo "Project:  $PROJECT_ID"
echo "Region:   $REGION"
echo "Dataset:  $BQ_DATASET"
echo "Bucket:   gs://$GCS_BUCKET"
echo ""

gcloud config set project "$PROJECT_ID"

# 1. Enable required APIs
echo "[1/6] Enabling BigQuery and Cloud Storage APIs..."
gcloud services enable bigquery.googleapis.com storage.googleapis.com --quiet

# 2. Create BigQuery dataset
echo "[2/6] Creating BigQuery dataset: $BQ_DATASET..."
if bq show --dataset "${PROJECT_ID}:${BQ_DATASET}" >/dev/null 2>&1; then
    echo "  Dataset already exists, skipping."
else
    bq mk --dataset \
        --location=US \
        --description="TradeTalk finance swarm — permanent data substrate" \
        "${PROJECT_ID}:${BQ_DATASET}"
fi

# 3. Create GCS bucket
echo "[3/6] Creating GCS bucket: gs://$GCS_BUCKET..."
if gsutil ls -b "gs://${GCS_BUCKET}" >/dev/null 2>&1; then
    echo "  Bucket already exists, skipping."
else
    gsutil mb -p "$PROJECT_ID" -l US "gs://${GCS_BUCKET}"
fi

# 4. Set lifecycle (move to Nearline after 90 days for cost savings)
echo "[4/6] Setting GCS lifecycle policy..."
cat > /tmp/lifecycle.json <<'EOF'
{
  "rule": [
    {
      "action": {"type": "SetStorageClass", "storageClass": "NEARLINE"},
      "condition": {"age": 90, "matchesPrefix": ["raw/"]}
    }
  ]
}
EOF
gsutil lifecycle set /tmp/lifecycle.json "gs://${GCS_BUCKET}"

# 5. Create service account for ETL writes
echo "[5/6] Creating service account: $SERVICE_ACCOUNT..."
if gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1; then
    echo "  Service account already exists, skipping."
else
    gcloud iam service-accounts create "$SERVICE_ACCOUNT" \
        --display-name="TradeTalk ETL Writer" \
        --description="Writes pipeline events and market data to BigQuery/GCS"
fi

# 6. Grant roles
echo "[6/6] Granting IAM roles..."
for ROLE in roles/bigquery.dataEditor roles/bigquery.jobUser roles/storage.objectAdmin; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="$ROLE" \
        --quiet >/dev/null
done

echo ""
echo "=== Bootstrap complete ==="
echo ""
echo "Next steps:"
echo "  1. Create a service account key (for local dev):"
echo "     gcloud iam service-accounts keys create ./gcp-sa-key.json --iam-account=$SA_EMAIL"
echo "  2. Set env var: export GOOGLE_APPLICATION_CREDENTIALS=./gcp-sa-key.json"
echo "  3. Run: python -m backend.mcp_server.bq_schema"
echo "  4. Run: python scripts/initial_load_to_gcs.py"
echo ""
echo "For production (Cloud Run / Render), set GOOGLE_APPLICATION_CREDENTIALS"
echo "or use Workload Identity Federation."
