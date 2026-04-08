# TradeTalk

AI-assisted market analysis application: FastAPI backend (Render), React frontend (Vercel).

## Documentation

- **[AGENTS.md](AGENTS.md)** — Release and **FaultHunter** remediation loop (mandatory test/deploy steps, **Cursor Cloud Agent** instructions, feature → code map).
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — System architecture.

## FaultHunter integration

External evaluator repo runs daily probes against the public API; reports are Markdown.

1. **Summarize a report locally**

   ```bash
   python3 scripts/summarize_faulthunter_report.py /path/to/report.md --markdown
   # or --json
   ```

2. **Scheduled triage file (default)** — [.github/workflows/faulthunter-report-reminder.yml](.github/workflows/faulthunter-report-reminder.yml) fetches `FAULTHUNTER_REPORT_URL` and **commits** [docs/FAULTHUNTER_TRIAGE.md](docs/FAULTHUNTER_TRIAGE.md) with a parsed summary + checklist (one file, no growing issue list). **Legacy:** run with `reminder_mode: issue` to open a GitHub Issue instead. **Repository secrets:**

   | Secret | Purpose |
   |--------|---------|
   | `FAULTHUNTER_REPORT_URL` | Raw URL to `reports/latest.md` in the FaultHunter repo (required for `schedule`). |
   | `RENDER_DEPLOY_HOOK_URL` | Optional — `curl` after deploy to force Render build. |
   | `VERCEL_DEPLOY_HOOK_URL` | Optional — same for Vercel. |

   **Public GitHub repos:** no token is needed to fetch reports. Set the secret to the raw URL for `reports/latest.md`, for example:

   `https://raw.githubusercontent.com/manojsilwal/FaultHunter/main/reports/latest.md`

   Storing it as `FAULTHUNTER_REPORT_URL` lets you change branch or path later without editing the workflow.

   **Run from GitHub UI:** **Actions** → **FaultHunter report reminder** → **Run workflow**, optionally set `report_url` or `reminder_mode` (`file` | `issue`).

   **Run from CLI** ([GitHub CLI](https://cli.github.com/) `gh`, authenticated with `gh auth login`):

   ```bash
   # Uses repository secret FAULTHUNTER_REPORT_URL (no inputs required)
   gh workflow run "FaultHunter report reminder" --repo manojsilwal/tradetalkapp
   ```

   Optional: override the report URL or use legacy issue mode:

   ```bash
   gh workflow run "FaultHunter report reminder" --repo manojsilwal/tradetalkapp \
     -f report_url="https://raw.githubusercontent.com/manojsilwal/FaultHunter/main/reports/latest.md" \
     -f reminder_mode=file
   ```

   List recent runs: `gh run list --repo manojsilwal/tradetalkapp --workflow "FaultHunter report reminder" -L 5`

3. **FaultHunter evaluator CLI** ([FaultHunter](https://github.com/manojsilwal/FaultHunter)) — default local base URL is **`http://127.0.0.1:5173`** (Vite dev server; [frontend/vite.config.js](frontend/vite.config.js) proxies API routes to FastAPI on `:8000`). Start **backend** and **`npm run dev`** in `frontend/`, then:

   ```bash
   python -m faulthunter.cli --profile smoke --report-kind manual --target-base-url http://127.0.0.1:5173
   ```

   Omit `--target-base-url` to use the same default. To hit FastAPI directly (no Vite), use `--target-base-url http://127.0.0.1:8000`. GitHub Actions: **Daily FaultHunter Report** → optional `target_base_url` input, or `gh workflow run "Daily FaultHunter Report" -R manojsilwal/FaultHunter -f target_base_url=…`.

4. Point **Cursor Background/Cloud Agent** at [docs/FAULTHUNTER_TRIAGE.md](docs/FAULTHUNTER_TRIAGE.md) (or the new issue if you used `reminder_mode=issue`) and [AGENTS.md](AGENTS.md).

## Development

- **Backend:** `cd backend && pip install -r requirements.txt && uvicorn backend.main:app --reload`  
  Copy `backend/.env.example` to `backend/.env` and set **`OPENROUTER_API_KEY`** for live chat (the API loads `.env` and optional `.env.local` on startup). Without it, chat shows a “configure OPENROUTER_API_KEY” message instead of model replies. If OpenRouter returns **invalid model ID**, check **`OPENROUTER_MODEL`** — the slug must match OpenRouter exactly (e.g. `qwen/qwen3.6-plus:free`, not `qwen-3.6`).
- **Frontend:** `cd frontend && npm install && npm run dev`
- **E2E:** With Vite running (`cd frontend && npm run dev` → [http://localhost:5173](http://localhost:5173)), **`npm run e2e:smoke`** runs a **minimal production-style check** (landing, AI Debate flow, Strategy Lab) — use this to confirm the app works for real users without running every spec. Full suite: `npm run e2e`. Playwright `baseURL` defaults to `http://localhost:5173`; set **`FRONTEND_URL`** to hit production (e.g. Vercel) instead.
- **FaultHunter-aligned API checks** (mirrors [FaultHunter case bank](https://github.com/manojsilwal/FaultHunter/blob/main/faulthunter/case_bank.py); no browser): with the backend running on port 8000,  
  `E2E_API_BASE_URL=http://127.0.0.1:8000 npx playwright test e2e/faulthunter-api.spec.js`  
  Use `FH_PROFILE=smoke` to run only smoke cases. Case definitions live in [e2e/faulthunter-cases.js](e2e/faulthunter-cases.js).

## Production URLs

- Frontend: `https://frontend-manojsilwals-projects.vercel.app`
- Backend: `https://tradetalkapp-backend.onrender.com` (see `render.yaml`)
