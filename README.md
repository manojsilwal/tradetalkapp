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

2. **Scheduled GitHub Issue** — [.github/workflows/faulthunter-report-reminder.yml](.github/workflows/faulthunter-report-reminder.yml) fetches `FAULTHUNTER_REPORT_URL` and opens an issue with a checklist. **Repository secrets:**

   | Secret | Purpose |
   |--------|---------|
   | `FAULTHUNTER_REPORT_URL` | Raw URL to `reports/latest.md` in the FaultHunter repo (required for `schedule`). |
   | `RENDER_DEPLOY_HOOK_URL` | Optional — `curl` after deploy to force Render build. |
   | `VERCEL_DEPLOY_HOOK_URL` | Optional — same for Vercel. |

   **Public GitHub repos:** no token is needed to fetch reports. Set the secret to the raw URL for `reports/latest.md`, for example:

   `https://raw.githubusercontent.com/manojsilwal/FaultHunter/main/reports/latest.md`

   Storing it as `FAULTHUNTER_REPORT_URL` lets you change branch or path later without editing the workflow.

   **Run from GitHub UI:** **Actions** → **FaultHunter report reminder** → **Run workflow**, optionally set `report_url`.

   **Run from CLI** ([GitHub CLI](https://cli.github.com/) `gh`, authenticated with `gh auth login`):

   ```bash
   # Uses repository secret FAULTHUNTER_REPORT_URL (no inputs required)
   gh workflow run "FaultHunter report reminder" --repo manojsilwal/tradetalkapp
   ```

   Optional: override the report URL for this run only:

   ```bash
   gh workflow run "FaultHunter report reminder" --repo manojsilwal/tradetalkapp \
     -f report_url="https://raw.githubusercontent.com/manojsilwal/FaultHunter/main/reports/latest.md"
   ```

   List recent runs: `gh run list --repo manojsilwal/tradetalkapp --workflow "FaultHunter report reminder" -L 5`

3. Point **Cursor Background/Cloud Agent** at the new issue and [AGENTS.md](AGENTS.md).

## Development

- **Backend:** `cd backend && pip install -r requirements.txt && uvicorn backend.main:app --reload`
- **Frontend:** `cd frontend && npm install && npm run dev`
- **E2E:** `npm run e2e` (see [playwright.config.js](playwright.config.js); `FRONTEND_URL` defaults to production Vercel URL)

## Production URLs

- Frontend: `https://frontend-manojsilwals-projects.vercel.app`
- Backend: `https://tradetalkapp-backend.onrender.com` (see `render.yaml`)
