# QA test matrix — dual-track (App + Yahoo reference)

This document is the **source of truth** for what TradeTalk verifies automatically vs manually, and how **Track A** (application / E2E) and **Track B** (Yahoo-grounded numbers) relate.

## Tracks


| Track                | Purpose                                                                | When to run                                                 |
| -------------------- | ---------------------------------------------------------------------- | ----------------------------------------------------------- |
| **A — App QA**       | End-to-end flows, UI states, no raw network failures                   | CI optional; scheduled/manual (see workflow)                |
| **B — Reference QA** | Falsifiable numbers vs Yahoo (`yfinance`) for deterministic API fields | Scheduled/manual; **not** on every PR (rate limits / flake) |


**Hard limit:** Chat **prose** is not fully automatable for “truth”; verify **structured** paths (quote card, metrics JSON, decision-terminal payload) first.

---

## Track A — Playwright (`e2e/`, `playwright.config.js`)

**Default base URL:** `FRONTEND_URL` (see `playwright.config.js`). Override for staging/production.


| Priority | Scenario                                                                        | Spec file                   | Notes                                           |
| -------- | ------------------------------------------------------------------------------- | --------------------------- | ----------------------------------------------- |
| P0       | Landing loads                                                                   | `smoke.spec.js`             | Heading “TradeTalk”                             |
| P0       | AI Debate tab + Start Debate                                                    | `smoke.spec.js`             | Waits for analyst UI (~3 min cap in config)     |
| P0       | Strategy Lab tab loads                                                          | `smoke.spec.js`             | Textarea/input visible                          |
| P1       | Valuation dashboard (AAPL)                                                      | `analysis-surfaces.spec.js` | Verdict / metrics surface                       |
| P1       | Decision terminal (NVDA)                                                        | `analysis-surfaces.spec.js` | Verdict + roadmap                               |
| P1       | Valuation / decision / macro / gold / debate / backtest / assistant / portfolio | `investor-usecases.spec.js` | Post-onboarding paths                           |
| P1       | Chat quote-style path (`quote-card`)                                            | `chat-numeric.spec.js`      | MSFT; structured card, not free-text regex only |


**Commands**

```bash
# Local (backend :8000, Vite :5173)
FRONTEND_URL=http://127.0.0.1:5173 npm run e2e -- --reporter=line

# Production / staging
FRONTEND_URL=https://your-app.vercel.app npm run e2e -- --reporter=line
```

**Timeouts:** Global test timeout is **360s** in `playwright.config.js` (backtest / debate). Individual tests may wait up to **320s** for backtest terminal state.

---

## Track B — Yahoo reference

### Automated parity (`backend/tests/test_market_data_parity.py`)

Compares **internal API** fields to a **Yahoo snapshot** at run time (same session).


| Check          | API                                                               | Yahoo reference                      | Tolerance                |
| -------------- | ----------------------------------------------------------------- | ------------------------------------ | ------------------------ |
| Price          | `GET /decision-terminal? ticker=` → `valuation.current_price_usd` | `yfinance.Ticker.fast_info` / `info` | `max(2 USD, 2% × price)` |
| Gross margin % | `GET /metrics/{ticker}` → `gross_margins.current` (parsed)        | `info["grossMargins"] * 100`         | **±2** percentage points |
| ROE %          | `GET /metrics/{ticker}` → `roic_roe.current` (parsed)             | `info["returnOnEquity"] * 100`       | **±2** percentage points |


**Default tickers:** `SPY`, `AAPL`, `MSFT` (`MARKET_PARITY_TICKERS`).

**Opt-in:** parity is **skipped** in default `unittest discover` unless `RUN_MARKET_PARITY=1`.

```bash
RUN_MARKET_PARITY=1 MARKET_PARITY_TICKERS=SPY,AAPL,MSFT \
  PYTHONPATH=. python -m unittest backend.tests.test_market_data_parity -v
```

### Manual snapshot script

For spreadsheets / sign-off, print a JSON snapshot (no TradeTalk server required):

```bash
python3 scripts/qa_yahoo_reference.py SPY AAPL MSFT
```

---

## CI policy

- **Every PR / push:** frontend build (`.github/workflows/frontend-build.yml`) — **no** Playwright or parity by default (keeps PRs fast).
- **Nightly / manual:** `.github/workflows/qa-dual-track.yml` — Track B parity + optional Track A E2E against `FRONTEND_URL` / `E2E_FRONTEND_URL`.

---

## FaultHunter (external evaluator)

**FaultHunter** lives in a separate repo; it runs API/browser probes against TradeTalk and **writes Markdown reports to files** in that repo when its GitHub Action finishes. You do **not** need to wait on the run—treat it as **async**: trigger, then review the report when convenient.

- **Trigger:** [FaultHunter → Actions](https://github.com/manojsilwal/FaultHunter/actions) → **Daily FaultHunter Report** → **Run workflow**. Optional input **`target_base_url`** overrides the **`TRADETALK_BASE_URL`** secret for that run (e.g. staging API); leave blank for production secret. CLI:  
  `gh workflow run "Daily FaultHunter Report" -R manojsilwal/FaultHunter -f target_base_url="https://…"`
- **Local TradeTalk from your machine (not GitHub):** run TradeTalk **backend** `:8000` + **Vite** `npm run dev` `:5173` (see `frontend/vite.config.js` API proxy). Clone FaultHunter and run  
  `python -m faulthunter.cli --profile smoke --report-kind manual --target-base-url http://127.0.0.1:5173`  
  (default in FaultHunter is already `http://127.0.0.1:5173`; use `:8000` only if you point FaultHunter straight at FastAPI with no Vite.)
- **Output:** Committed reports (e.g. `reports/latest.md` on the FaultHunter default branch). Use the raw URL for automation or open the file on GitHub.
- **Read later in TradeTalk:** `python3 scripts/summarize_faulthunter_report.py /path/to/report.md --markdown`  
Optional: set `FAULTHUNTER_REPORT_URL` in this repo and use the **FaultHunter report reminder** workflow to refresh [`docs/FAULTHUNTER_TRIAGE.md`](../docs/FAULTHUNTER_TRIAGE.md) (or legacy: open a tracking issue). See [README.md](../README.md#faulthunter-integration).

Use past reports to prioritize fixes; they are **not** a blocking gate on every deploy.

---

## Manual QA sheet (weekly production)


| Scenario          | Screen       | Expected (high level)                        | Yahoo ref                     | Pass/Fail | Notes |
| ----------------- | ------------ | -------------------------------------------- | ----------------------------- | --------- | ----- |
| Quote prompt      | Assistant    | `quote-card` for ticker                      | `qa_yahoo_reference.py` price |           |       |
| Decision terminal | Decision     | Verdict + roadmap panels                     | Parity test or script         |           |       |
| Backtest          | Strategy Lab | Results **or** structured error + Request ID | N/A                           |           |       |


---

## Risks (from plan)

- **Datacenter IPs** (e.g. Render) may hit Yahoo rate limits — run heavy parity **locally or on schedule**, not every commit.
- **LLM hallucination:** prefer Track B on **API/tool** paths; treat assistant **narrative** as spot-check + human tier.

