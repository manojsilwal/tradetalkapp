# TradeTalk — agent instructions (FaultHunter remediation and release)

**Unattended remediation:** use **Cursor Background / Cloud Agent** tied to this repo. Do **not** assume a developer Mac is online—run tests in the **cloud agent sandbox** or in **GitHub Actions**, not only locally.

**Alarm clock:** scheduled **FaultHunter** reports are surfaced via GitHub Issues (see [`.github/workflows/faulthunter-report-reminder.yml`](.github/workflows/faulthunter-report-reminder.yml)). Point the cloud agent at the latest **FaultHunter findings** issue plus this file.

---

## Mandatory loop after every code change

1. **Targeted tests** — After each task, run tests for **affected** code in the cloud agent (or CI):  
   - Backend: `cd backend && pytest` with paths narrowed to changed modules when possible (e.g. `pytest tests/test_macro.py`).  
   - E2E: `npm run e2e` with a focused file or grep when possible (see [`playwright.config.js`](playwright.config.js)).  
   Do **not** assume a local machine.

2. **Missing tests** — If behavior changed without coverage, add **unit/integration** tests under `backend/tests/` and **E2E** under [`e2e/`](e2e). Prefer stable selectors and timeouts aligned with [`playwright.config.js`](playwright.config.js) (long timeouts for backtest-heavy flows).

3. **Red-green** — If tests fail, fix code or tests and return to step 1. Do not commit a broken tree.

4. **Commit and push** — Logical commits; message may reference FaultHunter **test ids** (e.g. `decision-aapl-today`) when fixing evaluator findings.

5. **Deploy** — Pushing to the branch connected to **Render** (backend) and **Vercel** (frontend) triggers builds. Optional **explicit** hooks (if git sync lags): store **Render Deploy Hook** and **Vercel Deploy Hook** URLs as GitHub secrets `RENDER_DEPLOY_HOOK_URL` and `VERCEL_DEPLOY_HOOK_URL`, then `curl -fsS -X POST "$URL"` after push. Avoid double-deploy if pushes already trigger both.

6. **Verify in production** — Run E2E smoke against the live frontend (default base URL in Playwright):  
   `FRONTEND_URL=https://frontend-manojsilwals-projects.vercel.app npm run e2e`  
   Ensure API calls hit production backend (env vars your tests use for API base, if any). Record pass/fail in the PR or issue.

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

- Scheduled issue with report summary: [`.github/workflows/faulthunter-report-reminder.yml`](.github/workflows/faulthunter-report-reminder.yml)  
- Optional Cursor rule: [`.cursor/rules/tradetalk-release.mdc`](.cursor/rules/tradetalk-release.mdc)
