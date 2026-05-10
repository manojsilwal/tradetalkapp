"""
Stock-predictor agent (TimesFM 2.5).

Phase 0 + Phase 1 of the implementation plan in
``docs/STOCK_PREDICTOR_TIMESFM_PLAN.md``:

* Phase 0 ships the contracts (schemas, constants, hashing, cycle ID,
  kill-switch, replay corpus, LLM routing) — no model calls.
* Phase 1 ships baselines (numpy-only), a MockTimesFMClient, ensemble +
  scenarios + manifest, and the orchestration in :mod:`backend.predictor.agent`.

Phase 2+ adds the real TimesFM microservice and real LLMs behind the same
public surface — call sites do not change.
"""

from .schemas import (  # noqa: F401
    PredictorRequest,
    PredictorOutput,
    HorizonForecast,
    QuantileBand,
    PredictorDegradedPayload,
    PredictorDisabled,
    PredictorDegraded,
)
from .timesfm_constants import (  # noqa: F401
    IDX_MEAN,
    IDX_Q10,
    IDX_Q50,
    IDX_Q90,
    QUANTILE_LEVELS,
    DEFAULT_HORIZONS,
    HORIZON_TO_TRADING_DAYS,
)
from .kill_switch import (  # noqa: F401
    predictor_enabled,
    predictor_backend,
    PREDICTOR_BACKEND_BASELINES_ONLY,
    PREDICTOR_BACKEND_FULL,
    PREDICTOR_BACKEND_NONE,
)

__all__ = [
    "PredictorRequest",
    "PredictorOutput",
    "HorizonForecast",
    "QuantileBand",
    "PredictorDegradedPayload",
    "PredictorDisabled",
    "PredictorDegraded",
    "IDX_MEAN",
    "IDX_Q10",
    "IDX_Q50",
    "IDX_Q90",
    "QUANTILE_LEVELS",
    "DEFAULT_HORIZONS",
    "HORIZON_TO_TRADING_DAYS",
    "predictor_enabled",
    "predictor_backend",
    "PREDICTOR_BACKEND_BASELINES_ONLY",
    "PREDICTOR_BACKEND_FULL",
    "PREDICTOR_BACKEND_NONE",
]
