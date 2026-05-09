#!/usr/bin/env bash
# Start FastAPI (:8000) + Vite (:5173) from this repo, wait for health, run Playwright (e2e/).
# Requires: free ports 8000/5173, backend deps installed, frontend `npm install` done.
# Usage:
#   ./scripts/run_local_e2e.sh              # full e2e/ (same as npm run e2e)
#   E2E_LOCAL_PROFILE=smoke ./scripts/run_local_e2e.sh
#   ./scripts/run_local_e2e.sh e2e/smoke.spec.js
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

pick_python() {
  for candidate in python3.12 python3.11 python3.10 "$ROOT/.venv-py312/bin/python" python3; do
    if ! command -v "$candidate" >/dev/null 2>&1 && [[ ! -x "$candidate" ]]; then
      continue
    fi
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

PY="$(pick_python)" || {
  echo "run_local_e2e.sh: need Python 3.10+ (e.g. python3.12 or .venv-py312)." >&2
  exit 1
}

port_busy() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -i ":$port" -sTCP:LISTEN -t >/dev/null 2>&1
  else
    return 1
  fi
}

if port_busy 8000; then
  echo "run_local_e2e.sh: port 8000 is already in use. Stop the other process or run E2E against it:" >&2
  echo "  FRONTEND_URL=http://localhost:5173 E2E_API_BASE_URL=http://127.0.0.1:8000 npm run e2e" >&2
  exit 1
fi
if port_busy 5173; then
  echo "run_local_e2e.sh: port 5173 is already in use. Stop Vite or pick another port." >&2
  exit 1
fi

cleanup() {
  [[ -n "${BACK_PID:-}" ]] && kill "$BACK_PID" 2>/dev/null || true
  [[ -n "${FRONT_PID:-}" ]] && kill "$FRONT_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

export PYTHONPATH="$ROOT"
export SP500_INGEST_ON_STARTUP="${SP500_INGEST_ON_STARTUP:-0}"
export GUARDRAILS_STRICT_STARTUP="${GUARDRAILS_STRICT_STARTUP:-0}"

log() { printf '[local-e2e] %s\n' "$*"; }

log "Starting FastAPI (uvicorn) on 127.0.0.1:8000..."
"$PY" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 &
BACK_PID=$!

log "Starting Vite dev server on 127.0.0.1:5173..."
(cd "$ROOT/frontend" && npm run dev -- --host 127.0.0.1 --port 5173 --strictPort) &
FRONT_PID=$!

wait_http() {
  local url=$1 msg=$2
  local i
  for i in $(seq 1 120); do
    if curl -sf "$url" >/dev/null 2>&1; then
      log "ready: $msg"
      return 0
    fi
    sleep 1
  done
  log "timeout waiting for $msg ($url)"
  return 1
}

wait_http "http://127.0.0.1:8000/llm/status" "backend /llm/status"
wait_http "http://127.0.0.1:5173/" "Vite"

export FRONTEND_URL="${FRONTEND_URL:-http://localhost:5173}"
export E2E_API_BASE_URL="${E2E_API_BASE_URL:-http://127.0.0.1:8000}"

cd "$ROOT"
if [[ $# -gt 0 ]]; then
  log "Playwright (custom args): $*"
  npx playwright test --config=playwright.config.js --reporter=line "$@"
elif [[ "${E2E_LOCAL_PROFILE:-full}" == "smoke" ]]; then
  log "Playwright: e2e/smoke.spec.js"
  npm run e2e:smoke -- --reporter=line
else
  log "Playwright: full e2e/ (set E2E_LOCAL_PROFILE=smoke for a short run)"
  npm run e2e -- --reporter=line
fi
