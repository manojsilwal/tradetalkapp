#!/usr/bin/env bash
# deploy_vercel.sh — Production frontend deploy (single Vercel project: "frontend").
#
# Canonical URL: https://frontend-manojsilwals-projects.vercel.app
# API: set VITE_API_BASE_URL in Vercel → frontend project → Production.
#
# Usage:
#   bash scripts/deploy_vercel.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}/frontend"

if ! command -v vercel >/dev/null 2>&1; then
  echo "vercel CLI not found. Install: npm i -g vercel" >&2
  exit 1
fi

echo "=== Deploy TradeTalk frontend → Vercel (project: frontend) ==="
vercel deploy --prod --yes

echo ""
echo "Production: https://frontend-manojsilwals-projects.vercel.app"
echo "Ensure Vercel env VITE_API_BASE_URL points at Cloud Run (see docs/GCP_API_DEPLOY.md)."
