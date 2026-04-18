# Decision-Outcome Ledger

The Decision-Outcome Ledger is Phase 2 of the Harness Engineering moat. It is
the SQL-queryable substrate under every user-facing agent decision, so we can:

1. **Audit** — reconstruct exactly what the agent saw, which chunks it cited,
   what prompt + model version produced the answer.
2. **Grade** — attach multi-horizon market-truth outcomes (1d / 5d / 21d /
   63d, SPY-relative, risk-adjusted) to the original decision, not to a
   parallel log.
3. **Correlate** — answer "which features / regimes / prompt versions cause
   wrong answers" with a `GROUP BY`.
4. **Swap models safely** — replay historical decisions through a candidate
   model and compare hit-rates against the incumbent before flipping
   `GEMINI_PRIMARY` / `OPENROUTER_MODEL`.

The ledger is **observability-grade**: writes never raise, every producer
wraps `emit_decision` in a `try/except`, and the whole layer no-ops when
`DECISION_LEDGER_ENABLE=0`.

> **Related docs**
> - [ARCHITECTURE.md](./ARCHITECTURE.md) — where the ledger sits in the stack.
> - [SEPL.md](./SEPL.md) — the Self-Evolution Protocol Layer that consumes
>   graded outcomes through `DecisionLedgerReflectionSource`.
> - [RESOURCE_REGISTRY.md](./RESOURCE_REGISTRY.md) — prompts whose versions
>   are stamped into every decision row.

---

## 1. Where it lives

| Piece | Location |
|-------|----------|
| Module | [`backend/decision_ledger.py`](../backend/decision_ledger.py) |
| SQLite DDL | [`backend/migrations/decisions/001_initial.sql`](../backend/migrations/decisions/001_initial.sql) |
| Supabase DDL | [`backend/supabase_decisions_bootstrap.sql`](../backend/supabase_decisions_bootstrap.sql) |
| Startup wiring | [`backend/main.py`](../backend/main.py) (`get_ledger()` + `install_contract_validator_sink()`) |
| Feature correlations | [`backend/feature_correlations.py`](../backend/feature_correlations.py) + Supabase MV in [`backend/supabase_feature_correlations.sql`](../backend/supabase_feature_correlations.sql) |
| Outcome grader | [`backend/outcome_grader.py`](../backend/outcome_grader.py), scheduled in [`backend/daily_pipeline.py`](../backend/daily_pipeline.py) at **02:10 UTC** |
| SEPL reflection source | `DecisionLedgerReflectionSource` in [`backend/sepl.py`](../backend/sepl.py) |
| Model-swap replay | [`backend/model_swap_replay.py`](../backend/model_swap_replay.py) |

---

## 2. Feature flags

| Variable | Default | Meaning |
|----------|---------|---------|
| `DECISION_LEDGER_ENABLE` | `1` | Master switch. Off → every producer is a logged no-op; grader and scheduler hook are skipped. |
| `DECISION_BACKEND` | `sqlite` | `sqlite` \| `supabase` \| `none`. Picks the backing store. `none` = in-memory no-op even with the master switch on. |
| `DECISIONS_DB_PATH` | `backend/decisions.db` | SQLite file path (ephemeral on Render unless a disk is mounted). |
| `CONTRACT_VALIDATOR_ENABLE` | `1` | Whether LLM outputs are validated against RSPL schemas. Violations land in `contract_violations` when the sink is installed. |
| `OUTCOME_GRADER_BATCH` | `50` | Max decisions graded per scheduler tick. |

Supabase backend requires `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` and the
DDL in `backend/supabase_decisions_bootstrap.sql` applied once.

---

## 3. Schema reference

Five tables. See
[`001_initial.sql`](../backend/migrations/decisions/001_initial.sql) for the
authoritative DDL (indexes, comments, uniqueness constraints). The Supabase
mirror swaps `REAL` → `DOUBLE PRECISION`, `INTEGER PK AUTOINCREMENT` →
`BIGSERIAL`, and JSON columns → `JSONB`.

### 3.1 `decision_events`

One row per user-facing agent decision. Append-only — graders write to
`outcome_observations` instead of updating the event.

| Column | Notes |
|--------|-------|
| `decision_id` | UUID hex (external id). Caller-supplied via `new_decision_id()`. |
| `decision_type` | `swarm_factor` \| `debate` \| `chat_turn` \| `decision_terminal` \| `scorecard` \| `gold_advisor` \| … |
| `symbol` | Ticker; empty for macro/summary decisions. |
| `horizon_hint` | `1d` \| `5d` \| `21d` \| `63d` \| `none`. Guides the grader. |
| `model` | OpenRouter / Gemini model id at call time. |
| `prompt_versions_json` | `{"role": "1.2.3", …}` stamped from the RSPL registry. |
| `registry_snapshot_id` | `resource_registry.snapshot_id()` at emit time. |
| `inputs_hash` | `sha256(prompt + inputs)` — dedupe key. |
| `output_json` | The structured agent output (post-contract-validation). |
| `verdict` | Extracted enum (e.g. `BUY` / `SELL` / `HOLD`) for fast grading. |
| `confidence` | `0..1`, may be NULL. |
| `source_route` | Call site, e.g. `backend/agents.py::AgentPair.run`. |

### 3.2 `decision_evidence`

Which RAG chunks informed the decision. Populated from `query_with_refs` so
each row carries `chunk_id`, `collection`, `relevance` (`1 - distance`), and
`rank`. See [§5.2](./ARCHITECTURE.md#52-knowledge-store-rag) for collection
names.

### 3.3 `feature_snapshots`

The input datapoints the decision saw. One row per `(decision_id,
feature_name)`. Numeric features populate `value_num`; string features
(regime labels, confidence bands) populate `value_str`. `regime` is
denormalized so correlation queries don't need a self-join.

### 3.4 `outcome_observations`

Multi-horizon market-truth grades written by
[`outcome_grader.py`](../backend/outcome_grader.py). One row per
`(decision_id, horizon, metric)` — `UNIQUE` index enforces idempotent
re-grading. Metrics emitted today: `price_return_pct`,
`excess_return_vs_spy_pct`, `risk_adjusted_return`, `paper_pnl`.
`correct_bool` is derived from the verdict × `excess_return` rule.

### 3.5 `contract_violations`

Runtime schema violations from
[`backend/contract_validator.py`](../backend/contract_validator.py). Populated
when `install_contract_validator_sink()` runs at startup. Lets the operator
answer "which model + prompt drifts most" with a single `GROUP BY resource_name,
model`.

---

## 4. Producers — who writes to the ledger

Every producer calls `decision_ledger.emit_decision(...)` in a
`try/except`; failure never breaks the user flow. The ledger **also
dual-writes** a `decision_emitted` event to the CORAL hub so the existing
dreaming / meta-harness surfaces keep working unchanged.

| Producer | Source | `decision_type` | Evidence | Features |
|----------|--------|-----------------|----------|----------|
| Swarm factor agents | [`backend/agents.py`](../backend/agents.py) (`AgentPair.run` → `_emit_factor_decision`) | `swarm_factor` | — | `market_regime`, macro state |
| IC debate | [`backend/debate_agents.py`](../backend/debate_agents.py) (`_run_full_debate_impl`) | `debate` | All agent + moderator RAG chunk refs, tagged with `agent_role` | Macro regime features |
| Chat turns | [`backend/routers/chat.py`](../backend/routers/chat.py) (`chat_send_message`) | `chat_turn` | Tool-level + RAG chunk refs from the evidence contract | Confidence band, tools invoked |

### 4.1 Adding a new producer

1. Build the `DecisionEvent` with `decision_id=new_decision_id()`.
2. Attach `EvidenceRef` list from whatever retrieval you ran (use
   `knowledge_store.query_with_refs` so the chunk ids flow through).
3. Build `FeatureValue`s for the key inputs (regime, confidence band, etc.).
4. Call `decision_ledger.emit_decision(...)` inside `try/except` and **do not
   raise** from the wrapper.
5. Pick a sensible `horizon_hint` so the grader knows what to mark against.
6. Stamp `prompt_versions_json` from `resource_registry.list_active()` so SEPL
   can trace the decision back to the exact prompt version.

> **Rule:** every new user-facing agent surface that produces a verdict MUST
> emit to the ledger before returning to the caller. See the rule appended
> to [AGENTS.md](../AGENTS.md).

---

## 5. Consumers — who reads from it

### 5.1 `OutcomeGrader`

`backend/outcome_grader.py` runs at **02:10 UTC** via APScheduler (only when
`DECISION_LEDGER_ENABLE=1`). For every ungraded decision whose horizon has
passed, it fetches prices (yfinance by default) and writes absolute, excess
(vs SPY), and risk-adjusted returns into `outcome_observations`. The
`UNIQUE(decision_id, horizon, metric)` index makes the pass idempotent.

### 5.2 SEPL `DecisionLedgerReflectionSource`

`backend/sepl.py::DecisionLedgerReflectionSource` pulls graded
`excess_return` rows, converts each decision into a reflection document with
`prompt_versions_json` + `effectiveness_score` (1.0 correct, 0.0 incorrect,
0.5 unlabelled) + `market_regime`, and feeds SEPL's Reflect stage. This means
SEPL evolves prompts based on **real outcome deltas** — not on LLM self-grades
of reflections.

### 5.3 Feature correlations

`backend/feature_correlations.py` computes `(feature_name, feature_bucket,
regime) × horizon` hit-rate and excess-return stats. In SQLite it exposes the
`v_feature_hit_rate` view (install via `install_sqlite_view()`); in Supabase it
exposes a `MATERIALIZED VIEW` of the same name. Python helpers
(`compute_feature_stats`, `top_features`) handle numeric bucketing into
quantiles and `t`-stat ranking for ad-hoc analysis.

### 5.4 Model-swap replay

`backend/model_swap_replay.py` re-runs stored decisions through a candidate
model and compares its verdicts against the incumbent's graded outcomes. It
is the gate we use before promoting a new model family. The report is
structured (`ReplayReport.as_dict()`) — not archived as a ledger row —
because candidate runs should be reproducible, not treated as production
decisions.

---

## 6. Example queries (SQLite)

These work against `decisions.db` after a few decisions + a grader pass.

### 6.1 Hit rate by decision type and horizon

```sql
SELECT d.decision_type,
       o.horizon,
       COUNT(*)                                         AS n,
       SUM(CASE WHEN o.correct_bool = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS hit_rate,
       AVG(o.excess_return)                              AS mean_excess
FROM decision_events d
JOIN outcome_observations o
  ON o.decision_id = d.decision_id AND o.metric = 'excess_return'
GROUP BY d.decision_type, o.horizon
ORDER BY n DESC;
```

### 6.2 Which prompt versions drift most (schema violations)

```sql
SELECT resource_name, resource_version, model, code, COUNT(*) AS n
FROM contract_violations
GROUP BY resource_name, resource_version, model, code
ORDER BY n DESC
LIMIT 20;
```

### 6.3 Feature × regime hit-rate (via the view)

```sql
SELECT * FROM v_feature_hit_rate
WHERE n >= 30
ORDER BY mean_excess DESC
LIMIT 20;
```

---

## 7. Operational notes

- **Ephemeral filesystem on Render.** The default `DECISION_BACKEND=sqlite`
  stores `decisions.db` on the service's ephemeral disk. For durable analytics
  set `DECISION_BACKEND=supabase` and apply `supabase_decisions_bootstrap.sql`
  + `supabase_feature_correlations.sql`.
- **Thread safety.** SQLite uses a per-thread connection, matching
  `backend/claim_store.py`. Supabase client is thread-safe by construction.
- **Off switch.** If the ledger misbehaves, set `DECISION_LEDGER_ENABLE=0` and
  redeploy. All producers and the grader immediately become no-ops; nothing
  else in the platform depends on ledger return values for user-facing
  behaviour.
- **Migration runner.** `run_migrations('decisions', …)` applies the schema
  the first time the module initializes a connection. Tests use
  `_reset_singleton_for_tests()` + a `DECISIONS_DB_PATH` override in a temp
  dir.
