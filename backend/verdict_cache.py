"""
Per-trading-day demand-driven cache for decision-terminal slices.

Populated when a user requests GET /decision-terminal/{snapshot|verdict|roadmap}
or the combined aggregator. Invalidated implicitly on the next trading session.

When ``VERDICT_CACHE_BACKEND=supabase``, entries are dual-written to Supabase so
warm verdicts survive Cloud Run scale-to-zero (in-memory dict is still used as L1).
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple, Union

from .market_calendar import last_completed_session
from .schemas import (
    DecisionRoadmapPayload,
    DecisionSnapshotPayload,
    DecisionSwarmPayload,
    DecisionTerminalPayload,
    DecisionVerdictPayload,
    SpotEnvelope,
)

logger = logging.getLogger(__name__)

SLICE_SNAPSHOT = "snapshot"
SLICE_SWARM = "swarm"
SLICE_VERDICT = "verdict"
SLICE_ROADMAP = "roadmap"

SlicePayload = Union[
    DecisionSnapshotPayload,
    DecisionSwarmPayload,
    DecisionVerdictPayload,
    DecisionRoadmapPayload,
    DecisionTerminalPayload,
]

_MAX_ENTRIES = int(os.environ.get("VERDICT_CACHE_MAX_ENTRIES", "600"))


def verdict_cache_enabled() -> bool:
    return os.environ.get("VERDICT_CACHE_ENABLE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def verdict_cache_backend() -> str:
    return os.environ.get("VERDICT_CACHE_BACKEND", "memory").strip().lower()


@dataclass
class _CacheEntry:
    payload: SlicePayload
    captured_at_utc: str
    session_date: date


_store: Dict[Tuple[str, str, date], _CacheEntry] = {}
_lock = threading.Lock()


def _session_date() -> date:
    return last_completed_session()


def _evict_if_needed() -> None:
    if len(_store) <= _MAX_ENTRIES:
        return
    while len(_store) > _MAX_ENTRIES:
        _store.pop(next(iter(_store)))


def _cache_key(slice_name: str, ticker: str, session: date) -> Tuple[str, str, date]:
    return (slice_name.strip().lower(), ticker.upper().strip(), session)


def _captured_at(payload: SlicePayload) -> str:
    if isinstance(payload, DecisionVerdictPayload):
        return payload.verdict_captured_at_utc or payload.generated_at_utc
    return payload.generated_at_utc


def _supabase_client():
    if verdict_cache_backend() != "supabase":
        return None
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        return None
    try:
        from supabase import create_client

        return create_client(url, key)
    except Exception as exc:
        logger.warning("[verdict_cache] Supabase client init failed: %s", exc)
        return None


def _payload_from_json(slice_name: str, data: dict) -> SlicePayload:
    if slice_name == SLICE_SNAPSHOT:
        return DecisionSnapshotPayload.model_validate(data)
    if slice_name == SLICE_SWARM:
        return DecisionSwarmPayload.model_validate(data)
    if slice_name == SLICE_VERDICT:
        return DecisionVerdictPayload.model_validate(data)
    if slice_name == SLICE_ROADMAP:
        return DecisionRoadmapPayload.model_validate(data)
    return DecisionTerminalPayload.model_validate(data)


def _read_supabase(slice_name: str, sym: str, session: date) -> Optional[_CacheEntry]:
    client = _supabase_client()
    if client is None:
        return None
    try:
        q = (
            client.table("verdict_cache")
            .select("payload_json, verdict_captured_at_utc, slice")
            .eq("ticker", sym)
            .eq("session_date", session.isoformat())
        )
        # Prefer slice column when present; fall back to legacy full-payload rows.
        try:
            res = q.eq("slice", slice_name).limit(1).execute()
        except Exception:
            res = q.limit(1).execute()
        rows = res.data or []
        if not rows:
            return None
        row = rows[0]
        row_slice = (row.get("slice") or slice_name).strip().lower()
        if row_slice != slice_name:
            return None
        payload = _payload_from_json(slice_name, row["payload_json"])
        captured = row.get("verdict_captured_at_utc") or _captured_at(payload)
        return _CacheEntry(payload=payload, captured_at_utc=captured, session_date=session)
    except Exception as exc:
        logger.warning("[verdict_cache] Supabase read failed for %s/%s: %s", slice_name, sym, exc)
        return None


def _write_supabase(slice_name: str, sym: str, session: date, payload: SlicePayload, captured: str) -> None:
    client = _supabase_client()
    if client is None:
        return
    row: Dict[str, Any] = {
        "ticker": sym,
        "session_date": session.isoformat(),
        "payload_json": payload.model_dump(mode="json"),
        "verdict_captured_at_utc": captured,
        "slice": slice_name,
    }
    try:
        client.table("verdict_cache").upsert(row).execute()
    except Exception as exc:
        logger.warning("[verdict_cache] Supabase write failed for %s/%s: %s", slice_name, sym, exc)


def get_cached_slice(slice_name: str, ticker: str) -> Optional[SlicePayload]:
    """Return a cached slice payload for ticker on the current trading session, or None."""
    if not verdict_cache_enabled():
        return None
    sym = (ticker or "").upper().strip()
    if not sym:
        return None
    sl = (slice_name or "").strip().lower()
    session = _session_date()
    key = _cache_key(sl, sym, session)
    entry: Optional[_CacheEntry] = None
    with _lock:
        entry = _store.get(key)
    if entry is None:
        entry = _read_supabase(sl, sym, session)
        if entry is not None:
            with _lock:
                _store[key] = entry
                _evict_if_needed()
    if entry is None:
        return None
    payload = entry.payload
    if sl == SLICE_SNAPSHOT and isinstance(payload, DecisionSnapshotPayload):
        return overlay_fresh_spot_on_snapshot(payload)
    if sl == SLICE_ROADMAP and isinstance(payload, DecisionRoadmapPayload):
        return overlay_fresh_spot_on_roadmap(payload)
    if isinstance(payload, DecisionVerdictPayload):
        return payload.model_copy(update={"slice_from_cache": True})
    if isinstance(payload, DecisionRoadmapPayload):
        return payload.model_copy(update={"slice_from_cache": True})
    if isinstance(payload, DecisionSnapshotPayload):
        return payload.model_copy(update={"slice_from_cache": True})
    if isinstance(payload, DecisionSwarmPayload):
        return payload.model_copy(update={"slice_from_cache": True})
    return payload


def store_slice_cache(slice_name: str, ticker: str, payload: SlicePayload) -> None:
    """Store a freshly computed slice for the current session."""
    if not verdict_cache_enabled():
        return
    sym = (ticker or "").upper().strip()
    if not sym:
        return
    sl = (slice_name or "").strip().lower()
    captured = _captured_at(payload)
    session = _session_date()
    key = _cache_key(sl, sym, session)
    with _lock:
        _store[key] = _CacheEntry(payload=payload, captured_at_utc=captured, session_date=session)
        _evict_if_needed()
    _write_supabase(sl, sym, session, payload, captured)


def overlay_fresh_spot_on_snapshot(
    payload: DecisionSnapshotPayload,
) -> DecisionSnapshotPayload:
    """Return a copy with spot price overlaid from the live spot resolver."""
    from .connectors.spot import resolve_spot
    from .freshness import assess_spot

    t = payload.ticker.upper()
    spot_quote = resolve_spot(t)
    updates: Dict[str, Any] = {"slice_from_cache": True}
    if spot_quote is not None and spot_quote.price:
        now = datetime.now(timezone.utc).isoformat()
        captured = spot_quote.captured_at_utc or now
        updates["spot"] = SpotEnvelope(
            price_usd=float(spot_quote.price),
            source=spot_quote.source,
            captured_at_utc=captured,
            degraded=bool(spot_quote.degraded),
            momentum_anchor_usd=spot_quote.momentum_anchor_usd,
        )
        updates["spot_price_source"] = spot_quote.source
        updates["market_data_degraded"] = bool(spot_quote.degraded)
        updates["data_freshness"] = assess_spot(
            source=str(spot_quote.source or "yfinance"),
            captured_at=captured,
            degraded=bool(spot_quote.degraded),
        )
        if payload.valuation is not None:
            updates["valuation"] = payload.valuation.model_copy(
                update={"current_price_usd": float(spot_quote.price)}
            )
    return payload.model_copy(update=updates)


def overlay_fresh_spot_on_roadmap(
    payload: DecisionRoadmapPayload,
) -> DecisionRoadmapPayload:
    """Return a copy with live spot overlaid on cached roadmap anchor price."""
    from .connectors.spot import resolve_spot

    t = payload.ticker.upper()
    spot_quote = resolve_spot(t)
    updates: Dict[str, Any] = {"slice_from_cache": True}
    if spot_quote is not None and spot_quote.price:
        updates["current_price_usd"] = float(spot_quote.price)
    return payload.model_copy(update=updates)


def overlay_fresh_spot(
    payload: DecisionTerminalPayload,
    *,
    verdict_captured_at_utc: Optional[str] = None,
) -> DecisionTerminalPayload:
    """Legacy full-payload spot overlay (aggregator / tests)."""
    from .connectors.spot import resolve_spot
    from .freshness import assess_spot

    t = payload.ticker.upper()
    spot_quote = resolve_spot(t)
    updates: Dict[str, Any] = {
        "verdict_from_cache": True,
        "verdict_captured_at_utc": verdict_captured_at_utc or payload.verdict_captured_at_utc,
    }
    if spot_quote is not None and spot_quote.price:
        now = datetime.now(timezone.utc).isoformat()
        updates["spot"] = SpotEnvelope(
            price_usd=float(spot_quote.price),
            source=spot_quote.source,
            captured_at_utc=spot_quote.captured_at_utc or now,
            degraded=bool(spot_quote.degraded),
            momentum_anchor_usd=spot_quote.momentum_anchor_usd,
        )
        updates["spot_price_source"] = spot_quote.source
        updates["market_data_degraded"] = bool(spot_quote.degraded)
        captured = spot_quote.captured_at_utc or now
        updates["data_freshness"] = assess_spot(
            source=str(spot_quote.source or "yfinance"),
            captured_at=captured,
            degraded=bool(spot_quote.degraded),
        )
        if payload.valuation is not None:
            val = payload.valuation.model_copy(
                update={"current_price_usd": float(spot_quote.price)}
            )
            updates["valuation"] = val
    return payload.model_copy(update=updates)


def get_cached_verdict(ticker: str) -> Optional[DecisionTerminalPayload]:
    """Legacy: assemble full payload from three slice caches when all are warm."""
    snap = get_cached_slice(SLICE_SNAPSHOT, ticker)
    verd = get_cached_slice(SLICE_VERDICT, ticker)
    road = get_cached_slice(SLICE_ROADMAP, ticker)
    if not isinstance(snap, DecisionSnapshotPayload):
        return None
    if not isinstance(verd, DecisionVerdictPayload):
        return None
    if not isinstance(road, DecisionRoadmapPayload):
        return None
    from .decision_terminal import assemble_terminal_from_slices

    return assemble_terminal_from_slices(
        snap,
        verd,
        road,
        verdict_from_cache=True,
    )


def store_verdict_cache(ticker: str, payload: DecisionTerminalPayload) -> None:
    """Legacy: store all three slices from a full aggregator payload."""
    sym = (ticker or "").upper().strip()
    if not sym:
        return
    now = payload.generated_at_utc
    store_slice_cache(
        SLICE_SNAPSHOT,
        sym,
        DecisionSnapshotPayload(
            ticker=sym,
            disclaimer=payload.disclaimer,
            generated_at_utc=now,
            cache_ttl_seconds=payload.cache_ttl_seconds,
            valuation=payload.valuation,
            quality=payload.quality,
            market_data_degraded=payload.market_data_degraded,
            spot_price_source=payload.spot_price_source,
            data_freshness=payload.data_freshness,
            spot=payload.spot,
            scorecard_summary=payload.scorecard_summary,
        ),
    )
    if payload.verdict and payload.swarm and payload.debate:
        store_slice_cache(
            SLICE_VERDICT,
            sym,
            DecisionVerdictPayload(
                ticker=sym,
                generated_at_utc=now,
                cache_ttl_seconds=payload.cache_ttl_seconds,
                verdict_captured_at_utc=payload.verdict_captured_at_utc or now,
                macro_fetched_at_utc=payload.macro_fetched_at_utc,
                verdict=payload.verdict,
                swarm=payload.swarm,
                debate=payload.debate,
                brain=payload.brain,
            ),
        )
    store_slice_cache(
        SLICE_ROADMAP,
        sym,
        DecisionRoadmapPayload(
            ticker=sym,
            generated_at_utc=now,
            cache_ttl_seconds=payload.cache_ttl_seconds,
            roadmap=payload.roadmap,
            current_price_usd=payload.valuation.current_price_usd if payload.valuation else None,
        ),
    )


def clear_verdict_cache(ticker: Optional[str] = None) -> None:
    """Test helper: clear all or one ticker's cached slices (in-memory L1 only)."""
    with _lock:
        if ticker:
            sym = ticker.upper().strip()
            keys = [k for k in _store if k[1] == sym]
            for k in keys:
                del _store[k]
        else:
            _store.clear()
