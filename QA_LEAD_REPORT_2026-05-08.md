# TradeTalk — Lead QA consolidated test report

**Run date:** 2026-05-08  
**Host:** macOS, repo root  
**Python:** 3.12 (`.venv-py312`); `pip install -r backend/requirements.txt` plus **`jsonschema`** so `test_sitg_prompt` could load (not in `backend/requirements.txt` today).  
**Node:** `npm ci` at repo root; Playwright Chromium.

---

## Executive summary

| Area | Result | Notes |
|------|--------|--------|
| **Backend `unittest` discover** | **RED** | 652 tests: **631 passed**, **4 failed**, **17 errors**, **11 skipped**. |
| **Track B — `test_market_data_parity`** | **RED** | 2 errors: asyncio **Semaphore bound to a different event loop** on decision-terminal path. |
| **TEVV runner** (`backend.eval.tevv_runner --json`) | **GREEN** | 26 passed, 0 failed; 1 reasoning case skipped (stub). |
| **TEVV harness** | **GREEN** | 3 tests OK. |
| **Phase B** (evidence + CORAL) | **GREEN** | 11 tests OK. |
| **Playwright smoke** (`e2e/smoke.spec.js`) | **GREEN** | 4/4 vs production. |
| **Playwright Yahoo parity** (`parity.spec.ts`) | **GREEN** | 4/4. |
| **Playwright full `e2e/`** | **PARTIAL** | 47 passed; **2 failed** (specs call **127.0.0.1:8000** without API). |
| **Playwright `full_suite.spec.ts`** | **GREEN** | 52/52. |
| **Playwright LLM prod QA** | **SKIPPED** | No `OPENAI_API_KEY` locally. |
| **Frontend `npm test`** | **N/A** | No script in `frontend/package.json`. |

---

## 1. Backend unittest discover

**Command:** `PYTHONPATH=. python -m unittest discover -s backend/tests -p 'test_*.py' -v`  
**68** files under `backend/tests/test_*.py`.

### Failures (4)

- `test_auth.TestJWT` — `test_issue_and_decode_jwt`, `test_decode_invalid_token_raises`, `test_decode_empty_raises` (`jwt.encode` seen as **MagicMock** in full suite → mock leakage / order).
- `test_gemini_flags.TestGeminiFlags.test_primary_requires_key` — local **GEMINI** key makes `gemini_primary_enabled()` true.

### Errors (17) — highlights

- **`test_openapi`**, **`test_openapi_still_boots_with_gemini_primary`**, **`test_debate_invalid_ticker_422`** — **`KeyError: '$ref'`** in FastAPI OpenAPI generation.
- **`test_openrouter_failover.*`** — **`TypeError: isinstance() arg 2 must be a type`** for `RateLimitError` (OpenAI SDK vs test expectations).
- **`test_cron_auth.*`**, **`test_debate_data_fallback`**, **`test_gold_technicals`**, **`test_macro`**, **`test_backtest_data_hub`**, **`test_auth.TestDevModeLogin`** — env/fixture/patch issues (see full console log if captured).

### Skipped (11) — examples

- `RUN_MARKET_PARITY=1` parity class skipped in discover by default.
- Live Gemini / Veo / FinCrawler / decision-terminal smoke — opt-in env flags.

---

## 2. Market parity (`RUN_MARKET_PARITY=1`)

**Result:** FAILED — **event loop / Semaphore** error during debate/LLM on decision-terminal path.

---

## 3. TEVV

- **`python -m backend.eval.tevv_runner --json`:** exit 0; **26** passed, **0** failed; reasoning axis **1** skipped.  
- **`unittest backend.tests.test_tevv_harness`:** OK (3 tests).

---

## 4. Phase B

`test_evidence_pack` + `test_coral_hub`: **11** tests OK.

---

## 5. Playwright (`playwright.config.js`)

**Base URL:** `FRONTEND_URL=https://frontend-manojsilwals-projects.vercel.app`

| Command | Outcome |
|---------|---------|
| `npm run e2e:smoke` | 4 passed |
| `npm run e2e` (full) | 47 passed, 2 failed, 3 skipped |

**Failed:** `decision-terminal-data-audit.spec.js`, `faulthunter-api.spec.js` (first case) — **ECONNREFUSED 127.0.0.1:8000**.

**Not run this session:** `e2e:smoke:api` with `E2E_API_BASE_URL` pointing at deployed API; `e2e:fincrawler` (optional).

---

## 6. Playwright TS (`playwright.config.ts`)

**Base URL:** `APP_URL=https://frontend-manojsilwals-projects.vercel.app`

| Command | Outcome |
|---------|---------|
| `npm run e2e:prod-parity` | 4 passed |
| `npx playwright test tests/e2e/full_suite.spec.ts --project=chromium` | 52 passed |
| `npm run e2e:prod-qa` | 1 skipped (no OpenAI key) |

---

## 7. Full entry-point checklist

| Entry | This run |
|-------|----------|
| `./scripts/run_backend_tests.sh` | Same as unittest discover — executed |
| `RUN_MARKET_PARITY=1` + `test_market_data_parity` | Executed — RED |
| `backend.eval.tevv_runner` | Executed — GREEN |
| `test_tevv_harness` | Executed — GREEN |
| Phase B unittest bundle | Executed — GREEN |
| `npm run e2e:smoke` | GREEN |
| `npm run e2e` | PARTIAL (API specs) |
| `e2e:prod-parity`, `full_suite.spec.ts` | GREEN |
| `e2e:prod-qa` | Skipped locally |
| `e2e:smoke:api` / deployed API | Not run |
| `pytest` | N/A (not in requirements) |
| Frontend unit tests | N/A |

---

## 8. Suggested engineering follow-ups

1. Fix **OpenAPI** generation (`$ref` KeyError) under current FastAPI/Pydantic pins.  
2. Isolate **JWT** tests from global mocks.  
3. Isolate **Gemini flag** tests from developer env keys.  
4. Align **OpenRouter** `RateLimitError` handling with installed OpenAI SDK.  
5. Fix **asyncio** semaphore / client lifecycle for parity tests hitting full debate path.  
6. Add **`jsonschema`** to `backend/requirements.txt` if sitg tests stay in default discover.  
7. For local full green **Playwright**, set **`E2E_API_BASE_URL`** (or start API on :8000) for API specs.

---

*Generated by lead QA pass; re-run after fixes or in CI (Python 3.11) for merge parity.*
