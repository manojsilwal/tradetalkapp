# SEPL — Self-Evolution Protocol Layer (Phase B)

> Operational & architectural reference for the five-operator prompt-evolution
> loop built on top of the Phase A `ResourceRegistry`. See the source paper
> _Autogenesis: A Self-Evolving Agent Protocol_ (arXiv:2604.15034v1) §3.2
> for the theoretical framing.

## 1. Why SEPL exists

Phase A made every LLM prompt a versioned, auditable registry entry. That
alone gets us nothing adaptive — a human still has to edit YAML to make the
system any smarter. SEPL closes the loop: given the registry's audit trail
and the `swarm_reflections` outcome log (both stamped with `prompt_versions`
since Phase A), it proposes, evaluates, and — only when the evidence is
unambiguous — commits improvements automatically. A companion kill switch
watches the aftermath and reverts updates that regress against pre-commit
baselines.

Everything is **off by default** and wrapped in two independent safety
flags. None of it runs until an operator turns it on.

## 2. The five operators

Autogenesis specifies the closed-loop operator algebra below. Each one is a
public method on [`backend.sepl.SEPL`](../backend/sepl.py). They compose in
`run_cycle()` but are individually testable (see
`backend/tests/test_sepl.py`).

| Operator | Paper symbol | Code | What it does |
| --- | --- | --- | --- |
| Reflect  | ρ | `SEPL.reflect()`  | Aggregate failure lessons from the `swarm_reflections` store for a single prompt. |
| Select   | σ | `SEPL.select()`   | Pick the learnable prompt with the lowest recent effectiveness. Respects the pinned flag. |
| Improve  | ι | `SEPL.improve()`  | Call the pinned `sepl_improver` meta-prompt to draft a new body. |
| Evaluate | ε | `SEPL.evaluate()` | Score candidate vs active on a curated per-prompt fixture set. |
| Commit   | κ | — internal — | Call `registry.update()` iff `margin ≥ SEPL_MIN_MARGIN`, else log rejection. |

`run_cycle()` is the Reflect→Select→Improve→Evaluate→Commit finite state
machine. It returns a `CycleReport` that captures every branch — useful for
operator dashboards.

## 3. Inputs, outputs, and invariants

### Inputs

1. **Registry state** — the full set of `PROMPT` records + their
   `learnable` flag + their YAML-declared JSON schemas.
2. **Reflections** — rows from `knowledge_store.swarm_reflections`. Each
   row carries `effectiveness_score` (0.3 failure / 0.7 success from
   `backend/daily_pipeline.py::_track_swarm_outcomes`) and (since Phase A)
   a `prompt_versions` stamp.
3. **Fixtures** — curated held-out cases in
   `backend/resources/sepl_eval_fixtures/<name>.json`. See that folder's
   README for the schema. Missing fixture file → **Evaluate always
   returns zero margin → Commit always rejects**. That is intentional:
   the system refuses to commit when it has no held-out data to judge.

### Outputs

* **Registry mutation** via `registry.update(..., actor="sepl:<run_id>")`
  on commit. Old versions are retained; callers can `registry.restore()`
  manually or let the kill switch do it.
* **Lineage row** attached automatically by `_write_lineage`; the
  `reason` column includes the SEPL run ID, margin, sample size, and the
  improver's truncated rationale.
* **Metadata stamp** on the new version: the `sepl` key contains
  `run_id`, `active_score`, `candidate_score`, `margin`, `fixtures_used`.

### Invariants (enforced AND tested)

1. SEPL **never** writes to a `learnable=False` resource — the registry
   rejects it in `update()` (see `ResourcePinnedError`). The cycle
   surfaces `ABORTED_PINNED` and moves on.
2. Every terminal branch returns a typed `SEPLOutcome`. None raise.
   (See `TestNeverRaises::test_run_cycle_swallows_registry_update_errors`.)
3. Empty, fenced, jailbreak-looking, or length-exploded candidate bodies
   are rejected before eval (`_looks_safe`, `_length_reasonable`).
4. Hard rate limit: `SEPL_MAX_PER_DAY` committed updates per prompt per
   24h. Exceeded → `REJECTED_RATE_LIMIT`.
5. Missing fixtures + non-zero candidate → `REJECTED_LOW_MARGIN`
   (zero-margin eval).
6. Dry-run mode runs every operator except `commit` — audit everything,
   mutate nothing.

## 4. The `sepl_improver` meta-prompt

The Improve operator hands its context to the pinned
[`sepl_improver`](../backend/resources/prompts/sepl_improver.yaml)
resource. `learnable=false` is critical: without it, SEPL would be able
to rewrite the prompt that drives its own edits — a classic
self-referential attack surface.

Its contract (schema) is exactly:

```json
{
  "new_body": "string (replacement body for the target prompt)",
  "rationale": "string (1–3 sentences of why this should help)",
  "confidence_0_1": 0.0
}
```

The body of `sepl_improver` locks down forbidden edits (no markdown
fences, no schema field changes, no "ignore previous" style jailbreak
text, length within ±25% of the current body) and explicitly tells the
improver to return `new_body == current_body` at `confidence ≤ 0.3` when
it cannot do better — cheap defense against coerced changes.

## 5. The kill switch (auto-rollback)

[`SEPLKillSwitch`](../backend/sepl.py) is the other half of the
control-theoretic contract. After SEPL commits, actual swarm outcomes
(reflected via the daily pipeline) are stamped with the new version.
Once `SEPL_ROLLBACK_MIN_SAMPLES` post-commit reflections exist, the
kill switch compares mean effectiveness:

* **post-commit cohort** = reflections stamped with the new version.
* **pre-commit cohort**  = reflections stamped with the prior version
  (the `from_version` from the lineage row). When no pre-commit
  reflections exist, a neutral 0.5 baseline is used.

If `post < pre - SEPL_ROLLBACK_MARGIN`, the kill switch calls
`registry.restore(target, prior_version, actor="sepl:rollback:<run_id>")`.
The lineage row shows `operation=restore` under the rollback actor — so
the audit trail tells you exactly which change was reverted and why.

Like `SEPL.run_cycle`, the kill switch defaults to dry-run and needs
`SEPL_AUTOCOMMIT=1` (for the scheduler) or `commit=true` (for the HTTP
endpoint) to perform an actual restore.

## 6. HTTP surface

Mounted at `/sepl/*` via `backend/routers/sepl.py`. Every live endpoint
returns **503** unless `SEPL_ENABLE=1`.

### `GET /sepl/status`

Always safe. Returns tunables + flag state:

```json
{
  "enabled": false,
  "dry_run_default": true,
  "tunables": {
    "min_samples": 10.0,
    "min_margin": 0.05,
    "max_commits_per_day": 1.0,
    "effectiveness_ceiling": 0.6
  },
  "rollback_tunables": {
    "margin": 0.10,
    "min_samples": 5.0,
    "window_hours": 168.0
  },
  "fixtures_dir": "/.../backend/resources/sepl_eval_fixtures"
}
```

### `GET /sepl/select/preview`

What `Select` would pick right now — no side effects.

### `POST /sepl/run`

Runs one cycle. Body:

```json
{
  "dry_run": false,
  "target": "bull",     // optional: skip Select
  "commit": true        // REQUIRED to actually mutate
}
```

Belt-and-suspenders: even `dry_run=false` stays dry unless `commit=true`.

### `POST /sepl/kill-switch/run`, `GET /sepl/kill-switch/preview`

Evaluates post-commit regression. Same `commit`-flag pattern as
`/sepl/run`.

## 7. Scheduler

If (and only if) `SEPL_ENABLE=1`, `backend/main.py` starts an
`AsyncIOScheduler` tick that runs every `SEPL_INTERVAL_HOURS`
(default 24h). Each tick:

1. Runs one `SEPL.run_cycle(dry_run=not SEPL_AUTOCOMMIT)`.
2. Runs `SEPLKillSwitch.check_all(dry_run=not SEPL_AUTOCOMMIT)` **after**
   the evolution step, so a just-committed change can never be rolled
   back in its own tick.

Both dry-run by default. Setting `SEPL_AUTOCOMMIT=1` opts into live
commits AND live rollbacks.

## 8. Feature flags

| Variable | Default | Purpose |
| --- | --- | --- |
| `SEPL_ENABLE` | `0` | Master switch. No scheduler, no live HTTP. |
| `SEPL_AUTOCOMMIT` | `0` | Scheduled ticks allowed to commit + rollback. Manual endpoints still need `commit: true`. |
| `SEPL_DRY_RUN` | `1` | Default for `run_cycle` when caller doesn't specify. |
| `SEPL_INTERVAL_HOURS` | `24` | Scheduled tick frequency. |
| `SEPL_MIN_SAMPLES` | `10` | Reflections required before Select considers a prompt. |
| `SEPL_MIN_MARGIN` | `0.05` | Candidate must beat active by ≥ this on eval fixtures. |
| `SEPL_MAX_PER_DAY` | `1` | Hard cap on committed updates per prompt per 24h. |
| `SEPL_EFFECTIVENESS_CEILING` | `0.6` | Prompts above this are "healthy" and skipped by Select. |
| `SEPL_CONTEXT_REFLECTIONS` | `6` | Cost cap on failure lessons sent to Improve. |
| `SEPL_ROLLBACK_MARGIN` | `0.10` | Post-commit effectiveness must be this far below pre-commit to trigger rollback. |
| `SEPL_ROLLBACK_MIN_SAMPLES` | `5` | Post-commit reflections required before kill switch will act. |
| `SEPL_ROLLBACK_WINDOW_HOURS` | `168` | Look-back window for recent SEPL commits the kill switch considers. |

## 9. Extension points

The operators depend on three injected collaborators defined as `Protocol`s
in `backend/sepl.py`:

* `LLMLike`       — `LLMClient` satisfies it (plus the optional
  `generate_with_body_override` used by Evaluate).
* `RegistryLike`  — `ResourceRegistry` satisfies it.
* `ReflectionSourceLike` — `KnowledgeStoreReflectionSource` adapts the
  Chroma `swarm_reflections` collection.

Swap any of these and SEPL keeps working. Tests use in-memory fakes for
all three so the 64 SEPL tests run in < 3 seconds with zero network I/O.

## 10. What SEPL deliberately does NOT do

1. **Edit agent definitions.** Only `PROMPT` resources are touched in
   Phase B. `AGENT`, `TOOL`, `ENV`, `MEM` resources are out of scope
   until a future phase.
2. **Generate fixtures.** The eval fixture set is human-curated by
   design. Auto-generating them from reflections would create an
   optimization target SEPL could game.
3. **Cross-prompt optimization.** Each cycle picks one prompt and
   improves it in isolation. Coupled effects (e.g. moderator + all bulls
   co-evolving) require the multi-agent extensions in Phase C.
4. **Mutate schemas.** `sepl_improver` is instructed to preserve the
   output schema, AND the length/token guards reject radical rewrites,
   AND the `_looks_safe` guard rejects fence-inserting candidates. All
   three must fail simultaneously for a schema break to reach commit.

## 11. Testing

Phase B adds 64 tests, each under 100 ms:

* `backend/tests/test_sepl.py` — 39 unit tests for the five operators,
  helpers, the orchestrator's finite state machine, and error paths.
* `backend/tests/test_sepl_kill_switch.py` — 10 tests covering every
  kill-switch branch including `ERROR`, `OK_WITHIN_TOLERANCE`, live
  restore, and pinned skips.
* `backend/tests/test_sepl_router.py` — 15 integration tests for the
  HTTP surface, gating flags, and response schemas.

Run with:

```bash
python -m pytest backend/tests/test_sepl.py backend/tests/test_sepl_kill_switch.py backend/tests/test_sepl_router.py -v
```

Full backend suite (215 passing, 3 skipped, 1 pre-existing unrelated
failure as of Phase B close):

```bash
python -m pytest backend/tests/ -q --ignore=backend/tests/test_chat_mover_intent.py --ignore=backend/tests/test_backtest_data_hub.py
```
