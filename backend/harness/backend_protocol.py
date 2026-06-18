"""
InferenceBackend protocols — the model-agnostic seam (Phase 1).

Every model that produces investment output for TradeTalk fits one of two
contracts:

* :class:`VerdictBackend`  — discrete directional calls (BUY/SELL/UP/DOWN…),
  the shape LLM agents already emit to the Decision-Outcome Ledger.
* :class:`ForecastBackend` — quantile price forecasts (q10/q50/q90 + point),
  the shape the predictor / TimesFM path emits.

Swapping a model = constructing a different backend instance. The replay
service (``backend/harness/replay_service.py``) converts any backend into a
``CandidateRunner`` for :mod:`backend.model_swap_replay`, so champion vs
challenger comparisons work identically for an LLM, a TimesFM build, or a
plain statistical baseline.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from backend import decision_ledger as _dl
from backend.model_swap_replay import CandidateRunner, CandidateVerdict

logger = logging.getLogger(__name__)

_VALID_VERDICTS = (
    "STRONG BUY", "STRONG SELL", "BUY", "SELL", "HOLD", "NEUTRAL",
    "UP", "DOWN", "FLAT", "MIXED",
)


# ── Result contracts ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VerdictResult:
    """Output of a directional-call backend."""

    verdict: str
    confidence: Optional[float] = None
    model: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ForecastResult:
    """Output of a quantile-forecast backend, in USD price levels."""

    horizon: str
    q10: Optional[float] = None
    q50: Optional[float] = None
    q90: Optional[float] = None
    point: Optional[float] = None
    model: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def directional_verdict(self, spot: float, eps: float = 1e-4) -> str:
        ref = self.q50 if self.q50 is not None else self.point
        if not ref or spot <= 0:
            return "FLAT"
        r = (ref - spot) / spot
        if r > eps:
            return "UP"
        if r < -eps:
            return "DOWN"
        return "FLAT"


# ── Protocols ────────────────────────────────────────────────────────────────


@runtime_checkable
class VerdictBackend(Protocol):
    name: str

    async def verdict(
        self,
        *,
        symbol: str,
        context: Dict[str, Any],
    ) -> VerdictResult: ...


@runtime_checkable
class ForecastBackend(Protocol):
    name: str

    async def forecast(
        self,
        *,
        symbol: str,
        series: List[float],
        horizon_td: int,
        horizon_label: str,
    ) -> ForecastResult: ...


# ── Verdict adapters ─────────────────────────────────────────────────────────


class StubVerdictBackend:
    """Deterministic verdict backend — keeps replay plumbing testable offline."""

    def __init__(self, fixed_verdict: str = "HOLD") -> None:
        self.name = f"stub:{fixed_verdict.lower().replace(' ', '_')}"
        self._verdict = fixed_verdict.upper()

    async def verdict(self, *, symbol: str, context: Dict[str, Any]) -> VerdictResult:
        return VerdictResult(verdict=self._verdict, confidence=0.5, model=self.name)


class LLMVerdictBackend:
    """Adapter: route a replayed decision through the production LLM client.

    ``role`` selects the prompt/tier mapping in :mod:`backend.llm_client`.
    Candidate models are configured the same way production is (env vars /
    registry), so a replay run answers "would the CURRENTLY configured model
    have done better on this history?".
    """

    def __init__(self, role: str = "swarm_synthesizer", label: str = "") -> None:
        self.role = role
        self.name = label or f"llm:{role}"

    def _build_prompt(self, *, symbol: str, context: Dict[str, Any]) -> str:
        evidence = context.get("evidence") or []
        features = context.get("features") or []
        ev_lines = [
            f"- [{e.get('collection', '')}] chunk={e.get('chunk_id', '')} relevance={e.get('relevance')}"
            for e in evidence[:10]
        ]
        ft_lines = [
            f"- {f.get('feature_name', '')}={f.get('value_str') or f.get('value_num')}"
            f" regime={f.get('regime', '')}"
            for f in features[:20]
        ]
        return (
            f"Historical replay. Symbol: {symbol or 'N/A'}.\n"
            f"Original decision type: {context.get('decision_type', '')}, "
            f"horizon: {context.get('horizon_hint', '')}.\n"
            "Input features at decision time:\n" + ("\n".join(ft_lines) or "(none)") + "\n"
            "RAG evidence refs at decision time:\n" + ("\n".join(ev_lines) or "(none)") + "\n\n"
            "Based only on the above, output a JSON object with keys "
            '"verdict" (one of STRONG BUY|BUY|HOLD|SELL|STRONG SELL) and '
            '"confidence" (0..1).'
        )

    @staticmethod
    def _parse(text_or_obj: Any) -> VerdictResult:
        obj: Dict[str, Any] = {}
        if isinstance(text_or_obj, dict):
            obj = text_or_obj
        else:
            text = str(text_or_obj or "")
            m = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if m:
                try:
                    obj = json.loads(m.group(0))
                except Exception:
                    obj = {}
            if not obj:
                upper = text.upper()
                for v in _VALID_VERDICTS:
                    if v in upper:
                        obj = {"verdict": v}
                        break
        verdict = str(obj.get("verdict") or "").upper().strip()
        if verdict not in _VALID_VERDICTS:
            verdict = ""
        conf: Optional[float] = None
        try:
            if obj.get("confidence") is not None:
                conf = max(0.0, min(1.0, float(obj["confidence"])))
        except (TypeError, ValueError):
            conf = None
        return VerdictResult(verdict=verdict, confidence=conf, raw=obj if isinstance(obj, dict) else {})

    async def verdict(self, *, symbol: str, context: Dict[str, Any]) -> VerdictResult:
        from backend.llm_client import get_llm_client

        llm = get_llm_client()
        prompt = self._build_prompt(symbol=symbol, context=context)
        out: Any
        meta: Dict[str, Any] = {}
        gen_meta = getattr(llm, "generate_with_meta", None)
        if callable(gen_meta):
            out, meta = await gen_meta(self.role, prompt)
        else:
            out = await llm.generate(self.role, prompt)
        parsed = self._parse(out)
        model = str((meta or {}).get("model") or "") or resolved_model_label()
        return VerdictResult(
            verdict=parsed.verdict,
            confidence=parsed.confidence,
            model=model or self.name,
            raw=parsed.raw,
        )


# ── Forecast adapters ────────────────────────────────────────────────────────


class BaselineEnsembleForecastBackend:
    """Statistical ensemble (naive/seasonal/EWMA/drift) — no network, no GPU.

    Acts both as the cheapest challenger in forecast replays and as the
    fallback champion when the TimesFM service is unreachable.
    """

    name = "baseline_ensemble"

    async def forecast(
        self,
        *,
        symbol: str,
        series: List[float],
        horizon_td: int,
        horizon_label: str,
    ) -> ForecastResult:
        import numpy as np

        from backend.predictor.baselines import (
            drift_forecast, ewma_forecast, naive_forecast, seasonal_naive_forecast,
        )
        from backend.predictor.ensemble import weighted_inverse_mase

        arr = np.asarray(series, dtype=np.float64)
        if arr.size < 8:
            return ForecastResult(horizon=horizon_label, model=self.name)
        members = {
            "naive": naive_forecast(arr, horizon_td),
            "seasonal_naive": seasonal_naive_forecast(arr, horizon_td),
            "ewma": ewma_forecast(arr, horizon_td),
            "drift": drift_forecast(arr, horizon_td),
        }
        blended, wts = weighted_inverse_mase(arr, horizon_td, members)
        # Residual-vol band: spread grows with sqrt(horizon), z(0.1/0.9)≈1.28.
        rets = np.diff(np.log(np.maximum(arr[-90:], 1e-8)))
        vol = float(np.std(rets)) if rets.size > 2 else 0.015
        spread = blended * vol * (horizon_td ** 0.5) * 1.28
        return ForecastResult(
            horizon=horizon_label,
            q10=max(1e-8, blended - spread),
            q50=blended,
            q90=blended + spread,
            point=blended,
            model=self.name,
            raw={"weights": wts},
        )


class TimesFMServiceForecastBackend:
    """Adapter for the deployed ``tradetalk-timesfm`` HTTP service."""

    def __init__(self, label: str = "") -> None:
        self.name = label or "timesfm_service"

    async def forecast(
        self,
        *,
        symbol: str,
        series: List[float],
        horizon_td: int,
        horizon_label: str,
    ) -> ForecastResult:
        import math

        import numpy as np

        from backend.predictor.timesfm_client import (
            fetch_timesfm_forecast_http, http_quantiles_to_numpy_path,
        )
        from backend.predictor.timesfm_constants import (
            DEFAULT_MODEL_LABEL, IDX_MEAN, IDX_Q10, IDX_Q50, IDX_Q90,
        )

        log_p = np.log(np.maximum(np.asarray(series, dtype=np.float64), 1e-8))
        payload = await fetch_timesfm_forecast_http(
            inputs=log_p[-1024:].tolist(),
            horizon=max(1, horizon_td),
            config_hash="harness_replay",
            model_version=DEFAULT_MODEL_LABEL,
        )
        if not payload:
            return ForecastResult(horizon=horizon_label, model=self.name)
        path = http_quantiles_to_numpy_path(payload)
        if path is None or path.shape[0] < horizon_td:
            return ForecastResult(horizon=horizon_label, model=self.name)
        row = path[horizon_td - 1]
        return ForecastResult(
            horizon=horizon_label,
            q10=math.exp(float(row[IDX_Q10])),
            q50=math.exp(float(row[IDX_Q50])),
            q90=math.exp(float(row[IDX_Q90])),
            point=math.exp(float(row[IDX_MEAN])),
            model=str(payload.get("model_version") or self.name),
            raw={"served_at": payload.get("served_at")},
        )


# ── Bridges into model_swap_replay ──────────────────────────────────────────


def verdict_candidate_runner(backend: VerdictBackend) -> CandidateRunner:
    """Wrap any :class:`VerdictBackend` as a ``CandidateRunner``."""

    async def _run(
        ev: _dl.DecisionEvent,
        evidence: List[Dict[str, Any]],
        features: List[Dict[str, Any]],
    ) -> CandidateVerdict:
        result = await backend.verdict(
            symbol=ev.symbol,
            context={
                "decision_type": ev.decision_type,
                "horizon_hint": ev.horizon_hint,
                "evidence": evidence,
                "features": features,
            },
        )
        return CandidateVerdict(
            decision_id=ev.decision_id,
            verdict=result.verdict,
            confidence=result.confidence,
            model=result.model or backend.name,
            output=result.raw,
        )

    return _run


def forecast_candidate_runner(backend: ForecastBackend) -> CandidateRunner:
    """Replay ``price_forecast`` decisions through a forecast backend.

    Point-in-time discipline: the price series is loaded from the data lake
    and truncated to the decision's ``created_at`` date, so the candidate sees
    only what was knowable at decision time.
    """

    td_map = {"1d": 1, "5d": 5, "21d": 21, "63d": 63}

    async def _run(
        ev: _dl.DecisionEvent,
        evidence: List[Dict[str, Any]],
        features: List[Dict[str, Any]],
    ) -> CandidateVerdict:
        series = _pit_close_series(ev.symbol, ev.created_at)
        if not series:
            return CandidateVerdict(
                decision_id=ev.decision_id,
                verdict="",
                model=backend.name,
                error="no_pit_series",
            )
        h = (ev.horizon_hint or "21d").lower()
        result = await backend.forecast(
            symbol=ev.symbol,
            series=series,
            horizon_td=td_map.get(h, 21),
            horizon_label=h,
        )
        spot = float(series[-1])
        return CandidateVerdict(
            decision_id=ev.decision_id,
            verdict=result.directional_verdict(spot),
            confidence=None,
            model=result.model or backend.name,
            output={
                "q10_usd": result.q10,
                "q50_usd": result.q50,
                "q90_usd": result.q90,
                "point_forecast_usd": result.point,
                "spot_usd": spot,
            },
        )

    return _run


def _pit_close_series(symbol: str, as_of_ts: float, max_rows: int = 512) -> List[float]:
    """Daily closes up to (and including) the decision date, from the data lake."""
    if not symbol:
        return []
    try:
        import pandas as pd

        from backend.data_lake.config import PRICES_DIR

        path = os.path.join(PRICES_DIR, f"{symbol.upper()}.parquet")
        if not os.path.isfile(path):
            return []
        df = pd.read_parquet(path, columns=["Close"])
        if df.empty:
            return []
        # Data-lake parquet carries a yfinance DatetimeIndex; coerce + filter
        # so the candidate never sees prices after the original decision.
        idx = pd.to_datetime(df.index, errors="coerce", utc=True)
        cutoff = pd.Timestamp(
            datetime.fromtimestamp(as_of_ts, tz=timezone.utc).date(), tz="UTC"
        ) + pd.Timedelta(days=1)
        mask = idx.notna() & (idx < cutoff)
        if not mask.any():
            return []
        return df.loc[mask, "Close"].tail(max_rows).astype(float).tolist()
    except Exception as e:
        logger.debug("[Harness] PIT series unavailable %s: %s", symbol, e)
        return []


# ── True per-call model identity (Phase 1.2) ────────────────────────────────


def resolved_model_label() -> str:
    """Best-effort label of the model that would actually serve the next call.

    Mirrors the routing cascade in ``llm_client`` / ``openrouter_pool`` instead
    of blindly reading ``OPENROUTER_MODEL`` — so ledger rows attribute
    decisions to the provider that really answered.
    """
    truthy = ("1", "true", "yes", "on")
    if (os.getenv("GEMINI_PRIMARY", "0").strip().lower() in truthy):
        return f"gemini:{os.getenv('GEMINI_MODEL', '').strip() or 'default'}"
    provider = ""
    try:
        from backend.openrouter_pool import resolve_llm_http_provider

        provider = resolve_llm_http_provider()
    except Exception:
        provider = ""
    if provider == "openrouter":
        return f"openrouter:{os.getenv('OPENROUTER_MODEL', '').strip() or 'default'}"
    fallback = (os.getenv("OPENROUTER_MODEL") or os.getenv("GEMINI_MODEL") or "").strip()
    return fallback or "rule_based_fallback"
