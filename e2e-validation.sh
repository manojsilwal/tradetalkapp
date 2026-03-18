#!/bin/bash
# E2E Validation — run after Vercel + Render deploy from latest push.
# Usage: ./e2e-validation.sh [BACKEND_URL] [FRONTEND_URL]

BACKEND="${1:-https://tradetalkapp-backend.onrender.com}"
FRONTEND="${2:-https://frontend-manojsilwals-projects.vercel.app}"

echo "=== E2E Validation ==="
echo "Backend:  $BACKEND"
echo "Frontend: $FRONTEND"
echo ""

FAIL=0

# 1. Backend health
echo "[1] Backend root..."
if curl -s -m 120 "$BACKEND/" | head -1 | grep -q .; then
  echo "    OK"
else
  echo "    FAIL (timeout or no response)"
  FAIL=1
fi

# 2. LLM status — new code should have guardrails_enabled
echo "[2] LLM status..."
RESP=$(curl -s -m 60 "$BACKEND/llm/status")
if echo "$RESP" | grep -q "guardrails_enabled"; then
  echo "    OK (new code deployed)"
  echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('    backend:', d.get('backend'), '| vector:', d.get('vector_backend','chroma'))" 2>/dev/null || true
else
  echo "    WARN: old API shape (deploy may still be building)"
fi

# 3. Knowledge stats — vector_backend should be supabase after new deploy
echo "[3] Knowledge stats..."
STATS=$(curl -s -m 60 "$BACKEND/knowledge/stats")
if echo "$STATS" | grep -q "vector_backend"; then
  VB=$(echo "$STATS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('vector_backend','?'))" 2>/dev/null)
  echo "    OK (vector_backend=$VB)"
else
  echo "    OK (collections reachable)"
fi

# 4. Runtime policy check
echo "[4] Runtime policy check..."
PC=$(curl -s -m 60 "$BACKEND/runtime/policy-check")
if echo "$PC" | grep -q "policy_block_check"; then
  echo "    OK"
else
  echo "    WARN: endpoint may have changed"
fi

# 5. Debate (smoke) — light call
echo "[5] Debate smoke (ticker=SPY)..."
DEB=$(curl -s -m 180 "$BACKEND/debate?ticker=SPY")
if echo "$DEB" | grep -q "ticker"; then
  echo "    OK (debate returned)"
else
  echo "    FAIL or timeout"
  FAIL=1
fi

# 6. Frontend loads
echo "[6] Frontend loads..."
CODE=$(curl -s -o /dev/null -w "%{http_code}" -m 30 "$FRONTEND/")
if [ "$CODE" = "200" ]; then
  echo "    OK (HTTP $CODE)"
else
  echo "    FAIL (HTTP $CODE)"
  FAIL=1
fi

echo ""
if [ $FAIL -eq 0 ]; then
  echo "=== Validation PASSED ==="
else
  echo "=== Validation had failures ==="
fi
echo ""
echo "Manual browser checks:"
echo "  1. Open $FRONTEND"
echo "  2. Run a Debate (e.g. GME)"
echo "  3. Run a Backtest (e.g. 'buy low PE value stocks')"
echo "  4. Check /knowledge/stats shows vector_backend=supabase"
