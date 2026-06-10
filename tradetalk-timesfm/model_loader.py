"""
TimesFM 2.5 weight loading + inference for the forecast service.

Production image installs ``timesfm[torch]`` (see Dockerfile ``INSTALL_TIMESFM``
build arg) and serves real ``google/timesfm-2.5-200m-pytorch`` quantile
forecasts. When the package or weights are unavailable (CI, local dev), the
loader returns ``None`` and ``app.py`` falls back to the drift stub so the
route contract stays testable without a GPU.

Cost note: the 200M-parameter checkpoint runs fine on CPU for batch-sized
workloads (~500 tickers nightly), so a scale-to-zero Cloud Run CPU service or
Cloud Run Job is sufficient — no GPU spend required.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MODEL_ID = os.environ.get("TIMESFM_MODEL_ID", "google/timesfm-2.5-200m-pytorch")
MAX_CONTEXT = int(os.environ.get("TIMESFM_MAX_CONTEXT", "1024"))
MAX_HORIZON = int(os.environ.get("TIMESFM_MAX_HORIZON", "256"))

_lock = threading.Lock()
_model: Any = None
_load_failed = False


def load_model_stub() -> str:
    """Kept for backward compatibility with earlier smoke checks."""
    return MODEL_ID


def get_model() -> Optional[Any]:
    """Lazy singleton. Returns ``None`` when timesfm/torch are unavailable."""
    global _model, _load_failed
    if _model is not None:
        return _model
    if _load_failed:
        return None
    with _lock:
        if _model is not None:
            return _model
        if _load_failed:
            return None
        try:
            import timesfm  # type: ignore

            model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(MODEL_ID)
            model.compile(
                timesfm.ForecastConfig(
                    max_context=MAX_CONTEXT,
                    max_horizon=MAX_HORIZON,
                    normalize_inputs=True,
                    use_continuous_quantile_head=True,
                )
            )
            _model = model
            logger.info("[TimesFM] loaded %s (context=%d horizon=%d)", MODEL_ID, MAX_CONTEXT, MAX_HORIZON)
            return _model
        except Exception as e:
            _load_failed = True
            logger.warning("[TimesFM] real model unavailable, stub fallback: %s", e)
            return None


def real_forecast(inputs: List[float], horizon: int) -> Optional[Dict[str, Any]]:
    """Run the real model; returns ``{point, quantiles}`` or ``None``.

    ``quantiles`` rows follow the repo-wide 10-channel layout
    (index 0 = mean, 1..9 = deciles q10..q90) consumed by
    ``backend/predictor/timesfm_constants.py``.
    """
    model = get_model()
    if model is None:
        return None
    try:
        h = max(1, min(int(horizon), MAX_HORIZON))
        ctx = inputs[-MAX_CONTEXT:]
        point, quantiles = model.forecast(horizon=h, inputs=[ctx])
        point_row = [float(x) for x in point[0]]
        q = quantiles[0]
        rows: List[List[float]] = []
        for step in range(len(point_row)):
            step_q = [float(x) for x in q[step]]
            if len(step_q) < 10:
                step_q = step_q + [step_q[-1]] * (10 - len(step_q))
            rows.append(step_q[:10])
        return {"point": point_row, "quantiles": rows}
    except Exception as e:
        logger.warning("[TimesFM] forecast failed: %s", e)
        return None


def model_label() -> str:
    if get_model() is not None:
        return MODEL_ID
    return os.environ.get("TIMESFM_MODEL_LABEL", "timesfm-2.5-stub")
