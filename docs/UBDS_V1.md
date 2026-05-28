# UBDS v1.0 — UI Behavior & Design Benchmark

TradeTalk implements the **App UI Behavior & Design Benchmark Standard (UBDS v1.0)** end-to-end: scoring engine, 13 agentic UI tasks, Playwright probes, accessibility/visual audits, release gates, CI, API, and dashboard UI.

## Quick start

```bash
# Offline fixture (CI default)
npm run eval:ubds

# Full Playwright + score (local: Vite :5173 + API :8000)
npm run eval:ubds:playwright

# Fast Playwright without live chat LLM
UBDS_SKIP_CHAT=1 npm run eval:ubds:playwright
```

## In-app

- Route: **`/ubds`** (Developer → UBDS Benchmark)
- `POST /admin/ubds/run` with `{ "mode": "fixture" | "playwright" }`
- Export report button copies/downloads Markdown

## Task suite (agentic app)

| ID | Task |
|----|------|
| `agent_start_dashboard` | Start analyze from unified dashboard |
| `understand_agent_path` | See agent roles on Observer |
| `nav_observer_trace` | Run multi-agent trace |
| `inspect_rag_evidence` | Chat evidence / sources panel |
| `understand_ai_confidence` | Confidence band on evidence contract |
| `recover_tool_failure` | Retry after error (Observer) |
| `compare_two_reports` | SwarmScore variant comparison |
| `export_eval_report` | Export UBDS markdown |
| `find_previous_eval` | Find SwarmScore/UBDS summary cards |
| `dashboard_alert` | Continual learning health banner |
| `nav_swarm_score` | Open SwarmScore eval |
| `nav_macro_surface` | Macro dashboard charts |
| `nav_decision_terminal` | Decision terminal analyze |

Definitions: `evals/datasets/ubds_tasks.json`  
Time benchmarks: `evals/configs/ubds_benchmark.yaml`

## Outputs (per run)

Under `evals/reports/YYYY-MM-DD/`:

- `uiux_behavior_design_report.md`
- `uiux_behavior_design_results.json`
- `uiux_task_results.csv`
- `uiux_accessibility_results.json`
- `uiux_dashboard_summary.json`

Latest symlinks: `evals/reports/latest_uiux.md`, `latest_uiux_results.json`  
Dashboard: `frontend/public/dashboard/uiux-summary.json`, `uiux-history.json`

Playwright sidecars:

- `evals/reports/ubds_playwright_latest.json`
- `evals/reports/ubds_accessibility_latest.json`
- `evals/reports/ubds_visual_latest.json`

## Composite score

```text
UBDS =
  Task Success × 0.25 +
  Efficiency × 0.15 +
  Error & Recovery × 0.15 +
  Navigation × 0.15 +
  Visual Design × 0.10 +
  Accessibility × 0.10 +
  Satisfaction × 0.10
```

Release gate (`pass`): overall ≥ 80, task success ≥ 85, accessibility ≥ 80, zero critical a11y issues, critical abandonment ≤ 5%.

## CI

Workflow: [`.github/workflows/ubds-benchmark.yml`](../.github/workflows/ubds-benchmark.yml)

- Every PR: fixture run + unit tests
- Manual `workflow_dispatch` with `run_playwright: true` for full E2E scoring

## Historical comparison

Each run compares against the previous entry in `uiux-history.json` and writes `overall_score_delta` into the report and dashboard summary.
