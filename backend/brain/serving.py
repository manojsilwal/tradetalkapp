"""Request-time serving for the finance brain.

Loads the registered model + the latest persisted snapshot, fetches a live spot
price, runs the Reflex layer (O(1) re-inference vs the live price), emits the
live-adjusted decision to the ledger, and returns the reflex contract. The
model is cached per (name, version) so repeated requests are cheap.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from .inference import InferenceEngine
from .ledger import build_emit_fn
from .model_registry import ModelRegistry
from .ports.factory import get_storage
from .reflex import LiveInputs, ReflexEngine
from .snapshot_store import SnapshotStore

logger = logging.getLogger(__name__)

_engine_cache: Dict[str, InferenceEngine] = {}


def serving_enabled() -> bool:
    return os.environ.get("BRAIN_SERVE_ENABLE", "0") == "1"


def investment_surface_enabled() -> bool:
    """The long-horizon investment surface requires brain serving + its own flag."""
    return serving_enabled() and os.environ.get("INVESTMENT_SURFACE", "0") == "1"


def _engine(registry: ModelRegistry, model_name: str, version: str) -> InferenceEngine:
    key = f"{model_name}-{version}"
    eng = _engine_cache.get(key)
    if eng is None:
        eng = InferenceEngine(registry, model_name, version)
        _engine_cache[key] = eng
    return eng


def _latest_as_of(store: SnapshotStore, ticker: str) -> Optional[str]:
    # Prefer the as_of_date recorded by the last pipeline run.
    try:
        from .run_brain_pipeline import read_status
        status = read_status()
        if status and status.get("as_of_date") and store.exists(ticker, status["as_of_date"]):
            return status["as_of_date"]
    except Exception:  # noqa: BLE001
        pass
    # Fall back to scanning the snapshot keyspace for this ticker.
    try:
        suffix = f"/{ticker}.json"
        dates = []
        for key in store.storage.list(f"{store.root}/"):
            if key.endswith(suffix):
                parts = key.split("/")
                if len(parts) >= 3:
                    dates.append(parts[-2])
        return max(dates) if dates else None
    except Exception:  # noqa: BLE001
        return None


def serve_ticker(ticker: str, *, as_of_date: Optional[str] = None,
                 knowledge_store: Any = None, user_id: str = "",
                 emit: bool = True,
                 options_overlay: Optional[Dict[str, float]] = None) -> Dict:
    """Return the live brain verdict for a ticker, or a status dict if missing."""
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return {"status": "error", "reason": "empty ticker"}

    storage = get_storage()
    store = SnapshotStore(storage=storage)
    as_of = as_of_date or _latest_as_of(store, ticker)
    if not as_of or not store.exists(ticker, as_of):
        return {"status": "no_snapshot", "ticker": ticker,
                "reason": "no brain snapshot available; run the nightly brain pipeline"}

    snapshot = store.load(ticker, as_of)
    registry = ModelRegistry(storage=storage)
    engine = _engine(registry, snapshot.model_name, snapshot.model_version)

    price, source = _live_price(ticker)
    if price is None:
        price = snapshot.base_price  # degrade to the snapshot price
        source = "snapshot_base"

    overlay = options_overlay or {}
    live_inputs = LiveInputs(
        price=float(price),
        put_call_oi_ratio=overlay.get("put_call_oi_ratio"),
        put_call_volume_ratio=overlay.get("put_call_volume_ratio"),
        iv_skew=overlay.get("iv_skew"),
        unusual_activity_score=overlay.get("unusual_activity_score"),
        options_net_premium_bias_num=overlay.get("options_net_premium_bias_num"),
    )

    emit_fn = build_emit_fn(user_id=user_id, source_route="/brain/ticker",
                            knowledge_store=knowledge_store) if emit else None
    reflex_engine = ReflexEngine(engine, emit_fn=emit_fn)
    result = reflex_engine.reflex(snapshot, live_inputs)
    result["price_source"] = source
    try:
        from ..connectors.filing_intelligence import get_filing_intelligence
        from .filing_overlay import apply_filing_overlay

        fi_record = get_filing_intelligence(ticker)
        if fi_record:
            result = apply_filing_overlay(
                result,
                fi_record,
                fundamentals=snapshot.base_feature_row,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[brain.serving] filing overlay skipped for %s: %s", ticker, exc)
    return result


def serve_investment_analysis(ticker: str, *, as_of_date: Optional[str] = None,
                              knowledge_store: Any = None, user_id: str = "",
                              emit: bool = False) -> Dict:
    """Long-horizon investment surface: run the brain, then re-frame for 1-5y.

    Reuses :func:`serve_ticker` (snapshot + Reflex live re-inference) and wraps it
    with :mod:`backend.brain.investment_stance`. ``emit`` defaults to False so the
    investment surface does not double-emit the brain verdict that ``serve_ticker``
    already records at the 63-day learning horizon.
    """
    base = serve_ticker(ticker, as_of_date=as_of_date, knowledge_store=knowledge_store,
                        user_id=user_id, emit=emit)
    status = base.get("status")
    if status in ("no_snapshot", "error") or not status:
        return base
    from .investment_stance import build_investment_analysis
    return build_investment_analysis(base)


def _live_price(ticker: str):
    """Return (price, source_str) using the 60-second TTL spot cache."""
    try:
        from ..connectors.spot import resolve_spot
        q = resolve_spot(ticker)
        if q is not None and q.price:
            src = q.source or "spot"
            src = src if not q.degraded else f"{src}(degraded)"
            return float(q.price), src
        return None, "unavailable"
    except Exception as e:  # noqa: BLE001
        logger.debug("[brain.serving] spot fetch failed for %s: %s", ticker, e)
        return None, "unavailable"
