# Phase: Super Investor harness (model-agnostic + TimesFM self-learning loop)

Turns the existing primitives (Decision-Outcome Ledger, outcome grader,
model-swap replay, RSPL registry, data lake, predictor) into one closed
self-learning circuit that any model — LLM or time-series FM — can plug into.

```
PIT data lake → TimesFM service / baselines → calibrated ensemble forecast
      ↓ emit                                   ↑ conformal scales + learned weights
Decision Ledger → outcome grader (verdicts + pinball/coverage)
      ↓
feature correlations + calibration stats
      ↓
self-learning 02:40 UTC: conformal update · weight refresh · SEPL market fixtures
      ↓ champion/challenger replay gate (audit-trailed)
promote → registry version (rollback guard) → next night
```

## Phase 1 — model-agnostic harness

| Piece | Where |
|---|---|
| `VerdictBackend` / `ForecastBackend` protocols + adapters (LLM, TimesFM service, baseline ensemble, stub) | `backend/harness/backend_protocol.py` |
| True per-call model attribution in ledger emits (provider-cascade aware) | `backend/decision_ledger_registry.py` → `resolved_model_label()` |
| Operational replay service: named candidates, persisted reports (`harness_replay_reports` table) | `backend/harness/replay_service.py` |
| HTTP surface | `POST /harness/replay`, `GET /harness/replay/reports` (`backend/routers/harness.py`) |

## Phase 2 — real TimesFM + forecast truth

| Piece | Where |
|---|---|
| Real `google/timesfm-2.5-200m-pytorch` loading (CPU OK; stub fallback) | `tradetalk-timesfm/model_loader.py`, `app.py`, Dockerfile `--build-arg INSTALL_TIMESFM=1` |
| Remote-primary forecasts (service quantiles drive bands; mock = fallback + shadow) | `backend/predictor/agent.py`, flag `TIMESFM_REMOTE_PRIMARY` (default on) |
| Quantile bands threaded into `output_json` for grading | `backend/predictor/ledger_emit.py` |
| Forecast truth metrics: `forecast_band_hit`, `forecast_pinball`, `forecast_point_err` | `backend/outcome_grader.py` |
| Nightly batch forecaster (Cloud Run Job entry point) | `python -m backend.predictor.batch_forecast` |

## Phase 3 — self-learning loops

| Loop | Where | Artifact |
|---|---|---|
| Conformal q10–q90 recalibration from graded coverage | `backend/predictor/conformal.py` | registry TOOL `predictor_conformal` (versioned, lineage) |
| Learned ensemble weights from walk-forward data-lake replay | `backend/predictor/learned_weights.py` | registry TOOL `predictor_ensemble_weights` |
| Market-truth SEPL fixtures from graded decisions | `backend/sepl_market_fixtures.py` | `backend/resources/sepl_eval_fixtures/*.json` |
| Feature-correlation context in SEPL improver | `backend/sepl.py` (`improve()`) | — |

Scheduler: `predictor_self_learning_daily` at **02:40 UTC** (after the 02:10
grader) in `backend/daily_pipeline.py`. Manual trigger:
`POST /harness/self-learning/run?dry_run=true`.

## Phase 4 — Super Investor surfaces

| Surface | Where |
|---|---|
| House View (forecast × consensus fusion, position hint, **ledger emit**) | `GET /house-view?ticker=` (`backend/house_view.py`) |
| Model-as-strategy walk-forward backtest vs equal-weight hold | `POST /harness/model-backtest` (`backend/harness/model_backtest.py`) |
| Hit-rate + feature analytics | `GET /harness/hit-rates` |
| Forecast calibration dashboard data | `GET /harness/calibration` |

## Phase 5 — guardrails

* **Champion/challenger gate** — `champion_challenger_gate()` (min labelled
  sample + min hit-rate delta); every replay report stores `gate_passed`.
* **Conformal rollback** — `maybe_rollback()` restores the prior registry
  version when measured coverage regresses > 0.10 from the value at commit.
* **Kill switches** (all independent):
  `HARNESS_API_ENABLE`, `PREDICTOR_CONFORMAL_ENABLE`,
  `PREDICTOR_LEARNED_WEIGHTS_ENABLE`, `PREDICTOR_SELF_LEARNING_ENABLE`,
  `SEPL_MARKET_FIXTURES_ENABLE`, `HOUSE_VIEW_ENABLE`,
  `TIMESFM_REMOTE_PRIMARY`, plus the existing
  `DECISION_LEDGER_ENABLE` / `PREDICTOR_ENABLE` / `SEPL_*` switches.

## Ops / cost notes (GCP free-tier-first)

* **TimesFM**: build `tradetalk-timesfm` with `INSTALL_TIMESFM=1` and run the
  **batch forecaster as a Cloud Run Job on CPU** (nightly, ~500 tickers) —
  no GPU, no always-on service. Online serving stays scale-to-zero.
* **Self-learning jobs** run inside the existing API process scheduler — no
  new infrastructure. They are no-ops until graded `forecast_*` rows exist.
* **SQLite artifacts** (`harness_replay_reports`, registry versions) live in
  the decisions/resources DBs — use the existing Supabase/Postgres paths in
  production since container disks are ephemeral.

## Tests

Offline coverage for the grader's forecast metrics lives in
`backend/tests/test_outcome_grader.py` (`TestForecastGrading`); the predictor
emit contract is asserted in `backend/tests/test_predictor_decision_ledger.py`.
Run: `./scripts/run_backend_tests.sh`.
