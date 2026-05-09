#!/usr/bin/env bash
# Run optional / previously skipped Playwright suites against a LOCAL stack:
#   - FH API smoke (E2E_API_BASE_URL)
#   - FinCrawler HTTP checks (FINCRAWLER_URL)
#   - Decision-terminal provider audit (RUN_DECISION_TERMINAL_DATA_AUDIT=1)
#   - LLM production UI QA (OPENAI_API_KEY or NVIDIA_API_KEY; see tests/e2e/llm-production-qa.spec.ts)
#
# Prereq: FastAPI on :8000 and Vite on :5173 (or set URLs below).
# Quick start (starts servers, then runs this script's tests):
#   ./scripts/run_local_e2e.sh ./scripts/run_local_optional_e2e.sh   # wrong — use two terminals or:
#
# Recommended:
#   Terminal A: PYTHONPATH=. uvicorn backend.main:app --host 127.0.0.1 --port 8000
#   Terminal B: cd frontend && npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
#   Terminal C: source .env.e2e.local  # optional
#               ./scripts/run_local_optional_e2e.sh
#
# Or one-shot (starts stack + full browser e2e + these extras — long):
#   START_LOCAL_STACK=1 ./scripts/run_local_optional_e2e.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env.e2e.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env.e2e.local"
  set +a
fi

export E2E_API_BASE_URL="${E2E_API_BASE_URL:-http://127.0.0.1:8000}"
export FRONTEND_URL="${FRONTEND_URL:-http://localhost:5173}"
export APP_URL="${APP_URL:-http://localhost:5173}"
export FH_PROFILE="${FH_PROFILE:-smoke}"

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

port_busy() {
  local port="$1"
  command -v lsof >/dev/null 2>&1 && lsof -i ":$port" -sTCP:LISTEN -t >/dev/null 2>&1
}

start_stack() {
  local PY
  PY="$(pick_python)" || {
    echo "Need Python 3.10+ on PATH for START_LOCAL_STACK." >&2
    exit 1
  }
  if port_busy 8000 || port_busy 5173; then
    echo "START_LOCAL_STACK=1 but port 8000 or 5173 is busy — use existing servers or free ports." >&2
    exit 1
  fi
  export PYTHONPATH="$ROOT"
  export SP500_INGEST_ON_STARTUP="${SP500_INGEST_ON_STARTUP:-0}"
  export GUARDRAILS_STRICT_STARTUP="${GUARDRAILS_STRICT_STARTUP:-0}"
  "$PY" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 &
  STACK_BACK_PID=$!
  (cd "$ROOT/frontend" && npm run dev -- --host 127.0.0.1 --port 5173 --strictPort) &
  STACK_FRONT_PID=$!
  for _ in $(seq 1 120); do
    curl -sf "http://127.0.0.1:8000/llm/status" >/dev/null 2>&1 && curl -sf "http://127.0.0.1:5173/" >/dev/null 2>&1 && break
    sleep 1
  done
}

cleanup_stack() {
  [[ -n "${STACK_BACK_PID:-}" ]] && kill "$STACK_BACK_PID" 2>/dev/null || true
  [[ -n "${STACK_FRONT_PID:-}" ]] && kill "$STACK_FRONT_PID" 2>/dev/null || true
}

if [[ "${START_LOCAL_STACK:-0}" == "1" ]]; then
  trap cleanup_stack EXIT INT TERM
  start_stack
fi

echo "[optional-e2e] E2E_API_BASE_URL=$E2E_API_BASE_URL FRONTEND_URL=$FRONTEND_URL APP_URL=$APP_URL"

echo "[optional-e2e] FaultHunter API smoke (FH_PROFILE=$FH_PROFILE)..."
FH_PROFILE="$FH_PROFILE" npx playwright test e2e/faulthunter-api.spec.js --config=playwright.config.js --reporter=line

if [[ -n "${FINCRAWLER_URL:-}" ]]; then
  echo "[optional-e2e] FinCrawler ($FINCRAWLER_URL)..."
  npx playwright test e2e/fincrawler-integration.spec.js --config=playwright.config.js --reporter=line
else
  echo "[optional-e2e] Skip FinCrawler (FINCRAWLER_URL unset)."
fi

if [[ "${RUN_DECISION_TERMINAL_DATA_AUDIT:-0}" == "1" ]]; then
  echo "[optional-e2e] Decision terminal data audit..."
  RUN_DECISION_TERMINAL_DATA_AUDIT=1 npx playwright test e2e/decision-terminal-data-audit.spec.js --config=playwright.config.js --reporter=line
else
  echo "[optional-e2e] Skip decision-terminal audit (set RUN_DECISION_TERMINAL_DATA_AUDIT=1)."
fi

if [[ -n "${OPENAI_API_KEY:-}" || -n "${NVIDIA_API_KEY:-}" ]]; then
  echo "[optional-e2e] LLM production QA (local APP_URL)..."
  npx playwright test tests/e2e/llm-production-qa.spec.ts --config=playwright.config.ts --project=chromium --reporter=line
else
  echo "[optional-e2e] Skip LLM production QA (set OPENAI_API_KEY or NVIDIA_API_KEY)."
fi

echo "[optional-e2e] Done."
