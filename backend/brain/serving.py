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
                 emit: bool = True) -> Dict:
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

    emit_fn = build_emit_fn(user_id=user_id, source_route="/brain/ticker",
                            knowledge_store=knowledge_store) if emit else None
    reflex_engine = ReflexEngine(engine, emit_fn=emit_fn)
    result = reflex_engine.reflex(snapshot, LiveInputs(price=float(price)))
    result["price_source"] = source
    return result


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
