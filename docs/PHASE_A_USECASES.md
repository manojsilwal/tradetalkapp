# Phase A — local verification (code → test → next phase)

Follow the loop in [AGENTS.md](../AGENTS.md): change code → run targeted tests → fix until green → run E2E with backend + frontend → then proceed to Phase E (eval harness).

## Servers (local)

1. Backend: `cd` repo root, `PYTHONPATH=. python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`
2. Frontend: `cd frontend && npm run dev` (Vite on `http://localhost:5173`)
3. `frontend/.env.local`: `VITE_API_BASE_URL=http://127.0.0.1:8000`

**SPA route `/chat`:** `/chat` must **not** be listed as a Vite dev proxy prefix (see [`frontend/vite.config.js`](../frontend/vite.config.js)). Proxying `/chat` to the API breaks direct navigation to the Assistant page (the browser would receive JSON `404` instead of `index.html`).

## Automated checks

| Step | Command |
|------|---------|
| Backend unit (includes evidence contract) | `PYTHONPATH=. python -m unittest discover -s backend/tests -p 'test_*.py' -v` |
| Chat evidence E2E (Playwright) | `npm run e2e -- e2e/chat-evidence-contract.spec.js` |
| Existing chat smoke | `npm run e2e -- e2e/chat-numeric.spec.js` |

## Granular use cases (manual + E2E coverage)

### Layer 1 contract (SSE `evidence_contract`)

1. **Quote path** — Ask for current price for a liquid ticker (e.g. MSFT). Expect: `quote_card` SSE, assistant reply, **`evidence_contract`** with `confidence_band` high, `sources_used` includes `quote_card:TICKER` when applicable.
2. **Greeting** — “Hello”. Expect: `evidence_contract` with `confidence_band` medium, no `abstain_reason` unless tools failed in a data-only turn.
3. **Tool success** — Prompt that forces `get_price_history` or `get_top_movers` (e.g. “YTD return for AAPL”). Expect: `tools_called` non-empty, `tool_outcomes` includes success where Yahoo returned data.
4. **Tool empty / error** — Ticker unlikely to resolve or FinCrawler-off path; expect: `abstain_reason` = `all_tools_empty_or_error` when every tool returned empty/error and no quote card.
5. **RAG / CORAL** — With retrieval populated, expect `sources_used` may include `internal_kb` and/or `coral_hub` (see `meta.rag_nonempty` / `coral_hub_nonempty` in backend).

### Regression (existing product)

6. Session open + send without `session_not_found` (see chat recovery behavior).
7. No “Failed to fetch” in network/console during chat (Playwright `expectNoGenericFetchFailure`).

## Definition of done (Phase A)

- [ ] Unit tests green for `chat_evidence_contract` classifiers and contract builder.
- [ ] Every completed chat stream ends with an `evidence_contract` SSE event before `[DONE]`.
- [ ] UI shows collapsible “Sources & confidence” (`data-testid="evidence-contract"`).
- [ ] `e2e/chat-evidence-contract.spec.js` passes against local stack.
