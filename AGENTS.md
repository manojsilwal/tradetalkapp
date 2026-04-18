# TradeTalk — agent instructions (FaultHunter remediation and release)

**Unattended remediation:** use **Cursor Background / Cloud Agent** tied to this repo. Do **not** assume a developer Mac is online—run tests in the **cloud agent sandbox** or in **GitHub Actions**, not only locally.

**Alarm clock:** scheduled **FaultHunter** reports are ingested by [`.github/workflows/faulthunter-report-reminder.yml`](.github/workflows/faulthunter-report-reminder.yml). By default it **commits** [`docs/FAULTHUNTER_TRIAGE.md`](docs/FAULTHUNTER_TRIAGE.md) (one file, no issue list). Optional **legacy** mode opens a GitHub Issue instead (`reminder_mode: issue`). Point the cloud agent at **`docs/FAULTHUNTER_TRIAGE.md`** plus this file.

---

## Daily Cursor triage (FaultHunter)

GitHub Actions **does not** start Cursor automatically.

**FaultHunter** (the evaluator repo) already writes **one** Markdown report per run (e.g. `reports/latest.md`). In **TradeTalk**, the default workflow behavior is to **refresh a single triage file** in this repo — not to maintain a list of issues.

### 1. Where to look (default: one file)

- Open **[`docs/FAULTHUNTER_TRIAGE.md`](docs/FAULTHUNTER_TRIAGE.md)** on `main` after the scheduled workflow (~03:45 UTC). It contains the parsed summary + checklist for the report at `FAULTHUNTER_REPORT_URL`.
- For the **full** evaluator output, use the **raw report URL** shown at the top of that file (same as FaultHunter’s `reports/latest.md`).

### 2. Legacy: GitHub Issues instead of the file

If you run the workflow with **`reminder_mode: issue`**, it opens an issue (title like `FaultHunter findings YYYY-MM-DD (UTC)`). Then: **Issues** → search `FaultHunter findings in:title`, or `gh issue list --search "FaultHunter findings in:title" --state open`.

### 3. Start Cursor

- **Background Agent:** Paste the path or URL to **`docs/FAULTHUNTER_TRIAGE.md`** (or the issue URL in legacy mode). Instruction: *“Follow `AGENTS.md` mandatory loop. Fix failures from this FaultHunter snapshot; run tests in the agent sandbox; push; then production E2E smoke as in step 6.”*

### 4. Follow the same loop as every code change

Use **Mandatory loop** below (steps 1–6): targeted tests → commit → deploy → **Verify** production `FRONTEND_URL`.

### 5. Mark “worked on” so you do not repeat

- **File mode:** Record results in the **PR** or commit message when triage is done (the triage file is overwritten on the next run).
- **Issue mode:** Comment on the issue with summary, PR link, E2E result; close when green.
- If only infra/config (e.g. `TRADETALK_BASE_URL` in FaultHunter secrets), note that in PR/issue — do not “fix” app code.

---

## Mandatory loop after every code change

1. **Targeted tests** — After each task, run tests for **affected** code in the cloud agent (or CI):  
   - Backend full smoke (`unittest`): from repo root, `./scripts/run_backend_tests.sh` — picks `python3.12` / `python3.11` / `python3.10` / `python3` and requires **Python 3.10+** (macOS system `python3` is often 3.9).  
   - Backend: `cd backend && pytest` with paths narrowed to changed modules when possible (e.g. `pytest tests/test_macro.py`).  
   - **Phase E (TEVV):** `PYTHONPATH=. python -m backend.eval.tevv_runner` and `PYTHONPATH=. python -m unittest backend.tests.test_tevv_harness -v` — see [`docs/PHASE_E_TEVV.md`](docs/PHASE_E_TEVV.md).  
   - **CORAL multi-agent hub:** [`docs/PHASE_CORAL_HUB.md`](docs/PHASE_CORAL_HUB.md) — `PYTHONPATH=. python -m unittest backend.tests.test_coral_hub.TestCoralAgentReflections -v`.  
   - **Phase B (evidence memo + dreaming):** [`docs/PHASE_B_DREAMING.md`](docs/PHASE_B_DREAMING.md) — `PYTHONPATH=. python -m unittest backend.tests.test_evidence_pack backend.tests.test_coral_hub -v`.  
   - **FaultHunter-aligned API E2E** (same cases as `faulthunter/case_bank.py`): start the API, then  
     `E2E_API_BASE_URL=http://127.0.0.1:8000 npx playwright test e2e/faulthunter-api.spec.js`  
     (`FH_PROFILE=smoke` for three smoke cases only).  
   - **Browser E2E**: for routine checks use **`npm run e2e:smoke`** (three tests: landing, debate, strategy lab) — not the full suite. Full Playwright: `npm run e2e`. Focused files: see [`playwright.config.js`](playwright.config.js).  
     Phase A (Layer 1 chat evidence contract): `npm run e2e -- e2e/chat-evidence-contract.spec.js` (requires Vite + API; see [`docs/PHASE_A_USECASES.md`](docs/PHASE_A_USECASES.md)).  
   Do **not** assume a local machine.

2. **Missing tests** — If behavior changed without coverage, add **unit/integration** tests under `backend/tests/` and **E2E** under [`e2e/`](e2e). Prefer stable selectors and timeouts aligned with [`playwright.config.js`](playwright.config.js) (long timeouts for backtest-heavy flows).

3. **Red-green** — If tests fail, fix code or tests and return to step 1. Do not commit a broken tree.

4. **Commit and push** — Logical commits; message may reference FaultHunter **test ids** (e.g. `decision-aapl-today`) when fixing evaluator findings.

5. **Deploy** — Pushing to the branch connected to **Render** (backend) and **Vercel** (frontend) triggers builds. Optional **explicit** hooks (if git sync lags): store **Render Deploy Hook** and **Vercel Deploy Hook** URLs as GitHub secrets `RENDER_DEPLOY_HOOK_URL` and `VERCEL_DEPLOY_HOOK_URL`, then `curl -fsS -X POST "$URL"` after push. Avoid double-deploy if pushes already trigger both.

6. **Verify** — Local: run Vite on **:5173** (Playwright default `baseURL` is `http://localhost:5173`). **Production user smoke** (minimal — do not run the entire E2E folder for every release):  
   `FRONTEND_URL=https://frontend-manojsilwals-projects.vercel.app npm run e2e:smoke`  
   Optional deeper coverage: `npm run e2e` (all specs) or targeted files. Ensure API calls go to the intended backend (`frontend` `.env` / `VITE_API_BASE_URL`). Record pass/fail in the PR (or the FaultHunter **issue** if you use legacy mode).

**CORS:** production backend should list this origin (see [`render.yaml`](render.yaml) `CORS_ORIGINS`).

---

## FaultHunter report → code map

FaultHunter labels cases by **feature** and HTTP path. Use this to find TradeTalk code and tests.

| FaultHunter `feature` | Endpoint (typical) | Backend module | Suggested E2E / tests |
|-------------------------|-------------------|----------------|------------------------|
| `decision_terminal` | `/decision-terminal` | [`backend/routers/analysis.py`](backend/routers/analysis.py) | [`e2e/analysis-surfaces.spec.js`](e2e/analysis-surfaces.spec.js), [`backend/tests/test_market_data_parity.py`](backend/tests/test_market_data_parity.py) |
| `macro` | `/macro` | [`backend/routers/macro.py`](backend/routers/macro.py) | [`e2e/analysis-surfaces.spec.js`](e2e/analysis-surfaces.spec.js), [`backend/tests/test_macro.py`](backend/tests/test_macro.py) |
| `gold` | `/advisor/gold` | [`backend/routers/macro.py`](backend/routers/macro.py) | [`e2e/analysis-surfaces.spec.js`](e2e/investor-usecases.spec.js) |
| `trace` | `/trace` | [`backend/routers/analysis.py`](backend/routers/analysis.py) | [`e2e/investor-usecases.spec.js`](e2e/investor-usecases.spec.js) |
| `debate` | `/debate` | [`backend/routers/analysis.py`](backend/routers/analysis.py) | [`e2e/investor-usecases.spec.js`](e2e/investor-usecases.spec.js) |
| `backtest` | `/backtest` | [`backend/routers/backtest.py`](backend/routers/backtest.py) | [`e2e/analysis-surfaces.spec.js`](e2e/analysis-surfaces.spec.js) |

---

## Ingesting a report

- **URL:** set `FAULTHUNTER_REPORT_URL` to the raw Markdown URL of `reports/latest.md` (or a dated file) in the FaultHunter repo. For **public** repos, no PAT is needed—e.g. `https://raw.githubusercontent.com/manojsilwal/FaultHunter/main/reports/latest.md`.  
- **Summarize:**  
  `python scripts/summarize_faulthunter_report.py path/to/report.md --markdown`  
  or `--json` for machine-readable output.

---

## What not to “fix” blindly

- **Empty `Target` or bad base URL** in the report: often **Render / GitHub secrets** for FaultHunter (`TRADETALK_BASE_URL`), not application logic.  
- **HTTP 599 / outages:** infrastructure or deploy, not parity tuning.  
- **Yahoo parity mismatch:** do not weaken FaultHunter checks without human review; fix app fields or data freshness instead.

---

## Related automation

- Scheduled triage (default: commit [`docs/FAULTHUNTER_TRIAGE.md`](docs/FAULTHUNTER_TRIAGE.md); optional: open issue): [`.github/workflows/faulthunter-report-reminder.yml`](.github/workflows/faulthunter-report-reminder.yml)  
- Optional Cursor rule: [`.cursor/rules/tradetalk-release.mdc`](.cursor/rules/tradetalk-release.mdc)

---

## Decision-Outcome Ledger rule (Harness Engineering Phase 2)

**Every new user-facing agent surface that produces a verdict MUST emit to the Decision-Outcome Ledger before returning to the caller.** This is how the app builds a model-agnostic moat: the ledger stores the decision, the RAG chunks that informed it, the features it saw, the prompt + model versions that produced it, and (later, via the grader) the multi-horizon market-truth outcome. Without an emit, a new surface is invisible to SEPL, to feature-correlation analytics, and to the model-swap replay harness.

**Required at emit time** (see [`docs/DECISION_LEDGER.md`](docs/DECISION_LEDGER.md) §4.1 for the full checklist):

1. Build a `DecisionEvent` with `decision_id = decision_ledger.new_decision_id()` and a sensible `decision_type` / `horizon_hint` (`1d` / `5d` / `21d` / `63d` / `none`).
2. Retrieve RAG context through `knowledge_store.query_with_refs(...)` (not the legacy `query_with_metadata`) so `chunk_id`, `collection`, and `relevance = 1 - distance` thread into `EvidenceRef` objects.
3. Record the key input features (e.g. `market_regime`, `confidence_band`) as `FeatureValue`s.
4. Stamp `prompt_versions_json` from `resource_registry.list_active()` and `registry_snapshot_id` from `resource_registry.snapshot_id()`.
5. Call `decision_ledger.emit_decision(...)` inside a `try/except` — **ledger failure must never break user-facing behavior**. The wrapper also dual-writes a `decision_emitted` event into the CORAL hub, so existing dreaming / meta-harness surfaces keep working without any changes.

**Do not** call ledger APIs for non-user-facing scheduled jobs (ETL, cache warmers, etc.) — the ledger is the *decision* substrate, not a generic event log. Use `coral_hub.log_handoff_event` for those.

**Tests must be offline.** New producers need a unit or integration test that seeds a temporary `DECISIONS_DB_PATH`, calls the producer, and asserts that a row appeared in `decision_events` (+ evidence + features). See [`backend/tests/test_decision_ledger_producers.py`](backend/tests/test_decision_ledger_producers.py) and [`backend/tests/test_model_swap_replay.py`](backend/tests/test_model_swap_replay.py) for the reference shape.

**Off switch.** If the ledger misbehaves in production, flip `DECISION_LEDGER_ENABLE=0` (or `DECISION_BACKEND=none`) and redeploy. All producers and the `outcome_grader` scheduler hook become no-ops; nothing else in the platform depends on ledger return values for user-facing behavior.
