# FaultHunter triage (single source)

**Snapshot (UTC):** 2026-05-03T06:16:47Z

**Workflow run:** [https://github.com/manojsilwal/tradetalkapp/actions/runs/25271771000](https://github.com/manojsilwal/tradetalkapp/actions/runs/25271771000)

**Raw report URL:** `https://raw.githubusercontent.com/manojsilwal/FaultHunter/main/reports/latest.md` (secret `FAULTHUNTER_REPORT_URL`).

This file is **overwritten** on each successful run. For the evaluator's full Markdown, open the raw report URL above.

---

## FaultHunter report summary

- Run ID: `20260404T050445Z-30dc1211`
- Profile: `daily`
- Target: ``

### Failing or non-pass rows

- **`decision-aapl-today`** (`decision_terminal`) — verdict `fail`, severity `high`
- **`macro-allocation-week`** (`macro`) — verdict `fail`, severity `high`
- **`gold-hedge-week`** (`gold`) — verdict `fail`, severity `high`
- **`trace-nvda-today`** (`trace`) — verdict `fail`, severity `high`
- **`debate-tsla-thesis`** (`debate`) — verdict `fail`, severity `high`

### Findings detail

#### `decision-aapl-today`

- **Issue:** Target returned HTTP 599 for /decision-terminal.
- **Recommended fix:** Stabilize the endpoint before judging recommendation quality.

#### `macro-allocation-week`

- **Issue:** Target returned HTTP 599 for /macro.
- **Recommended fix:** Stabilize the endpoint before judging recommendation quality.

#### `gold-hedge-week`

- **Issue:** Target returned HTTP 599 for /advisor/gold.
- **Recommended fix:** Stabilize the endpoint before judging recommendation quality.

#### `trace-nvda-today`

- **Issue:** Target returned HTTP 599 for /trace.
- **Recommended fix:** Stabilize the endpoint before judging recommendation quality.

#### `debate-tsla-thesis`

- **Issue:** Target returned HTTP 599 for /debate.
- **Recommended fix:** Stabilize the endpoint before judging recommendation quality.

---

*Automation: [.github/workflows/faulthunter-report-reminder.yml](.github/workflows/faulthunter-report-reminder.yml). Remediation: follow [AGENTS.md](https://github.com/manojsilwal/tradetalkapp/blob/main/AGENTS.md).*

### Cursor agent — triage

Follow the loop in [AGENTS.md](https://github.com/manojsilwal/tradetalkapp/blob/main/AGENTS.md#daily-cursor-triage-faulthunter): fix → test → push → verify env.

- [ ] Map failing **test ids** to code (table in AGENTS.md)
- [ ] **Fix** + targeted tests (backend / `e2e/faulthunter-api.spec.js` / `npm run e2e` as needed)
- [ ] **Push** to `main` (or PR); wait for Render + Vercel
- [ ] **Production smoke:** `FRONTEND_URL=https://frontend-manojsilwals-projects.vercel.app npm run e2e:smoke` (minimal; full suite: `npm run e2e`)

- [ ] **Record** pass/fail + PR link in a commit message or PR when done
