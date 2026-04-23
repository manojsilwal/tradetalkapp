#!/usr/bin/env bash
# Curl-based FinCrawler smoke (no Playwright). Use after starting FinCrawler, e.g.:
#   cd /path/to/fincrawler && export API_KEY=yoursecret && uvicorn main:app --host 127.0.0.1 --port 10000
#
# Usage:
#   FINCRAWLER_URL=http://127.0.0.1:10000 FINCRAWLER_KEY=yoursecret ./scripts/verify-fincrawler.sh
#
# If FINCRAWLER_URL is unset, exits 0 with a skip message (safe for CI when optional).

set -euo pipefail

BASE="${FINCRAWLER_URL:-}"
KEY="${FINCRAWLER_KEY:-}"

if [[ -z "$BASE" ]]; then
  echo "skip: FINCRAWLER_URL not set"
  exit 0
fi

BASE="${BASE%/}"

echo "== GET $BASE/health"
curl -sS -f "$BASE/health" | python3 -m json.tool || {
  echo "health check failed"
  exit 1
}

echo ""
echo "== GET $BASE/quote?ticker=AAPL"
HDR=()
if [[ -n "$KEY" ]]; then
  HDR=( -H "Authorization: Bearer $KEY" -H "X-Api-Key: $KEY" )
fi

CODE=$(curl -sS -o /tmp/fc_quote.json -w "%{http_code}" "${HDR[@]}" "$BASE/quote?ticker=AAPL" || true)
echo "HTTP $CODE"
python3 -m json.tool /tmp/fc_quote.json || cat /tmp/fc_quote.json

if [[ "$CODE" == "401" ]]; then
  echo "error: 401 — set FINCRAWLER_KEY to match FinCrawler API_KEY"
  exit 1
fi

if [[ "$CODE" != "200" && "$CODE" != "422" ]]; then
  echo "error: expected 200 or 422"
  exit 1
fi

echo "verify-fincrawler.sh: ok"
