#!/usr/bin/env bash
# upsert_gcp_secret.sh — Create or update a GCP Secret Manager secret and grant Cloud Run SA access.
#
# Usage:
#   bash scripts/upsert_gcp_secret.sh GEMINI_API_KEY backend/.env
#   bash scripts/upsert_gcp_secret.sh YOUTUBE_API_KEY
#   echo -n "secret-value" | bash scripts/upsert_gcp_secret.sh MY_SECRET
#
# Reads KEY=VALUE from the env file (or stdin when no file). Never prints the secret value.

set -euo pipefail

SECRET_NAME="${1:?Usage: upsert_gcp_secret.sh SECRET_NAME [env-file]}"
ENV_FILE="${2:-}"

PROJECT_ID="${GCP_PROJECT_ID:-tradetalkapp-492904}"
SA_EMAIL="${CLOUD_RUN_API_SA:-tradetalk-etl@${PROJECT_ID}.iam.gserviceaccount.com}"

_read_secret_value() {
  if [[ -n "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_FILE" ]]; then
      echo "Env file not found: $ENV_FILE" >&2
      exit 1
    fi
    local line val=""
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$line" =~ ^[[:space:]]*# ]] && continue
      [[ -z "${line// }" ]] && continue
      line="$(echo "$line" | xargs)"
      [[ "$line" != "${SECRET_NAME}="* ]] && continue
      val="${line#*=}"
      break
    done < "$ENV_FILE"
    if [[ -z "${val// }" ]]; then
      echo "${SECRET_NAME} is missing or empty in $ENV_FILE" >&2
      exit 1
    fi
    printf '%s' "$val"
    return
  fi

  if [[ -t 0 ]]; then
    echo "Provide an env file or pipe the secret on stdin." >&2
    exit 1
  fi
  cat
}

VALUE="$(_read_secret_value)"
if [[ -z "${VALUE// }" ]]; then
  echo "Secret value is empty." >&2
  exit 1
fi

gcloud config set project "$PROJECT_ID" >/dev/null

if gcloud secrets describe "$SECRET_NAME" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "Updating secret $SECRET_NAME (new version)..."
else
  echo "Creating secret $SECRET_NAME..."
  gcloud secrets create "$SECRET_NAME" \
    --project="$PROJECT_ID" \
    --replication-policy="automatic"
fi

printf '%s' "$VALUE" | gcloud secrets versions add "$SECRET_NAME" \
  --project="$PROJECT_ID" \
  --data-file=-

echo "Granting roles/secretmanager.secretAccessor to $SA_EMAIL..."
gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
  --project="$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --quiet >/dev/null

echo "Done: projects/${PROJECT_ID}/secrets/${SECRET_NAME} (latest version added)"
