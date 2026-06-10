# Continual Learning Capability Assessment

TradeTalk captures decision signals, grades market outcomes, and can evolve prompts via SEPL — but the closed loop from **graded outcomes → agent behavior** is only partial in production unless SEPL and ledger reflection are enabled.

## Executive verdict

| Dimension | Rating | Summary |
|-----------|--------|---------|
| Signal capture | Strong | Phase F: every verdict surface emits — swarm (consolidated + per-factor), debate, chat (+ tools), decision terminal, scorecard, gold, small cap, backtest, daily brief, predictor, house view, morning brief, macro flow. Coverage observable at `GET /learning-health` → `capture_coverage_24h` |
| Memory / context | Strong | Vector RAG (Supabase pgvector default, Chroma local) + CORAL hub on hot path |
| Outcome-grounded evolution | Improving | `DecisionLedgerReflectionSource` wired into SEPL via **composite** default |
| Closed-loop improvement | Gated | `SEPL_ENABLE=0` by default; `SEPL_AUTOCOMMIT=0` |
| Attribution / replay | Operational | Per-decision prompt-role attribution (`registry_attribution(roles=…)`) + resolved model labels; model-swap replay, hit rates, and calibration exposed via `/harness/*` (`backend/routers/harness.py`) |

## Architecture (after wiring)

```mermaid
flowchart TB
  User[User request] --> Agent[Swarm / Debate / Chat / Macro]
  Agent --> RAG[knowledge_store RAG]
  Agent --> CORAL[coral_hub context]
  Agent --> Ledger[decision_ledger]
  Cron[daily_pipeline] --> Grader[outcome_grader]
  Grader --> Ledger
  SEPL[SEPL scheduler] -->|composite| LedgerReflect[DecisionLedgerReflectionSource]
  SEPL -->|composite| ChromaReflect[swarm_reflections]
  SEPL --> Registry[resource_registry]
  Registry --> Agent
```

**Reflection source:** `SEPL_REFLECTION_SOURCE=composite` (default) merges graded ledger rows first, then legacy Chroma `swarm_reflections`. Use `ledger` or `chroma` to force a single source.

## Verification runbook

### A. Signal volume

```bash
PYTHONPATH=. python3.12 -c "
from backend import decision_ledger as dl
L = dl.get_ledger()
print('stats:', L.stats())
"
```

Or `GET /learning-health` (also surfaced on **Observer** UI).

### B. Grader health

```bash
PYTHONPATH=. python3.12 -m unittest backend.tests.test_outcome_grader -v
```

### C. SEPL dry-run

Set `SEPL_ENABLE=1`, keep `SEPL_AUTOCOMMIT=0`, call `POST /sepl/run` with `{"dry_run": true}`.

Check `GET /sepl/status` for `reflection_source`.

### D. Reflection wiring tests

```bash
PYTHONPATH=. python3.12 -m unittest backend.tests.test_sepl_decision_source backend.tests.test_sepl_reflection_wiring -v
```

### E. Regression safety

```bash
PYTHONPATH=. python -m backend.eval.tevv_runner
PYTHONPATH=. python3.12 -m unittest backend.tests.test_decision_ledger_producers -v
```

## Producer checklist

All user-facing verdict surfaces should call `decision_ledger.emit_decision` with:

1. `prompt_versions` + `registry_snapshot_id` from `decision_ledger_registry.registry_attribution()`
2. RAG evidence via `EvidenceRef` where applicable
3. `try/except` — ledger failure must not break UX

## Environment flags

| Variable | Default | Meaning |
|----------|---------|---------|
| `DECISION_LEDGER_ENABLE` | `1` | Master ledger switch |
| `SEPL_ENABLE` | `0` | Prompt evolution scheduler |
| `SEPL_DRY_RUN` | `1` | Log-only cycles |
| `SEPL_AUTOCOMMIT` | `0` | Actually commit prompt versions |
| `SEPL_REFLECTION_SOURCE` | `composite` | `ledger` \| `chroma` \| `composite` |

## Gap priority (remaining)

| Priority | Item |
|----------|------|
| ~~P2~~ done | ~~Ops endpoints for feature correlations + model-swap replay~~ — shipped as `/harness/hit-rates` + `/harness/replay` (`backend/routers/harness.py`) |
| P1 | Durable ledger/registry in production (`DECISION_BACKEND=supabase`; Supabase path for `DecisionLedgerReflectionSource`) — Phase F workstream F3 |
| P2 | Retire or revive the dead `swarm_reflections` write path (readers still query it) — Phase F workstream F4 |
| P3 | LLM-enhanced CORAL dreaming |

See [PHASE_F_INTELLIGENCE_FABRIC.md](./PHASE_F_INTELLIGENCE_FABRIC.md) for the
full gap analysis and sequencing.
