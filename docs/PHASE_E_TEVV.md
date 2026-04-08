# Phase E — TEVV eval harness

Deterministic regression for **Layer 1** routing and evidence contracts (no live LLM in the default harness). Complements Phase A’s [`evidence_contract`](./PHASE_A_USECASES.md) SSE field.

## Three scoring axes

| Axis | What it checks | Cases (v1) |
|------|----------------|------------|
| **direction_accuracy** | Mover intent, live-quote intent, ticker extraction, `classify_tool_result` | 14 |
| **json_validity** | `build_evidence_contract` expectations, evidence JSON schema | 5 |
| **reasoning_quality** | LLM-as-judge (stub — skipped until `TEVV_LLM_JUDGE=1` and implementation) | 1 skipped |

Distribution: **8 golden**, **7 edge**, **4 failure**, plus **1** reasoning stub (skipped).

## Commands

```bash
# From repo root (loads backend deps)
PYTHONPATH=. python -m backend.eval.tevv_runner
PYTHONPATH=. python -m backend.eval.tevv_runner --json
PYTHONPATH=. python -m unittest backend.tests.test_tevv_harness -v
```

Case definitions: [`backend/eval/case_bank.json`](../backend/eval/case_bank.json) (20 cases: 8 golden, 8 edge, 4 failure + reasoning stub).

## CI

Workflow [.github/workflows/tevv-nightly.yml](../.github/workflows/tevv-nightly.yml) runs on PRs (when eval paths change), on `main` pushes, on a **daily** schedule (~07:15 UTC), and via `workflow_dispatch`.

## Extending

- Add rows to `case_bank.json` with `check` in `mover_intent` | `wants_quote` | `quote_ticker` | `classify_tool` | `evidence_contract` | `evidence_schema` | `llm_judge`.
- Implement optional LLM-as-judge in [`backend/eval/tevv_runner.py`](../backend/eval/tevv_runner.py) when `TEVV_LLM_JUDGE=1`.
