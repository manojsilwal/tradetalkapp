"""
Per-trading-day demand-driven cache for decision-terminal payloads.

Populated only when a user requests GET /decision-terminal for a ticker.
Invalidated implicitly on the next trading session (cache key includes session_date).

When ``VERDICT_CACHE_BACKEND=supabase``, entries are dual-written to Supabase so
warm verdicts survive Cloud Run scale-to-zero (in-memory dict is still used as L1).
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .market_calendar import last_completed_session
from .schemas import DecisionTerminalPayload, SpotEnvelope

logger = logging.getLogger(__name__)

_MAX_ENTRIES = int(os.environ.get("VERDICT_CACHE_MAX_ENTRIES", "200"))


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
    payload: DecisionTerminalPayload
    verdict_captured_at_utc: str
    session_date: date


_store: Dict[Tuple[str, date], _CacheEntry] = {}
_lock = threading.Lock()


def _session_date() -> date:
    return last_completed_session()


def _evict_if_needed() -> None:
    if len(_store) <= _MAX_ENTRIES:
        return
    while len(_store) > _MAX_ENTRIES:
        _store.pop(next(iter(_store)))


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


def _read_supabase(sym: str, session: date) -> Optional[_CacheEntry]:
    client = _supabase_client()
    if client is None:
        return None
    try:
        res = (
            client.table("verdict_cache")
            .select("payload_json, verdict_captured_at_utc")
            .eq("ticker", sym)
            .eq("session_date", session.isoformat())
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None
        row = rows[0]
        payload = DecisionTerminalPayload.model_validate(row["payload_json"])
        captured = row.get("verdict_captured_at_utc") or payload.verdict_captured_at_utc
        return _CacheEntry(payload=payload, verdict_captured_at_utc=captured, session_date=session)
    except Exception as exc:
        logger.warning("[verdict_cache] Supabase read failed for %s: %s", sym, exc)
        return None


def _write_supabase(sym: str, session: date, payload: DecisionTerminalPayload, captured: str) -> None:
    client = _supabase_client()
    if client is None:
        return
    try:
        client.table("verdict_cache").upsert(
            {
                "ticker": sym,
                "session_date": session.isoformat(),
                "payload_json": payload.model_dump(mode="json"),
                "verdict_captured_at_utc": captured,
            }
        ).execute()
    except Exception as exc:
        logger.warning("[verdict_cache] Supabase write failed for %s: %s", sym, exc)


def get_cached_verdict(ticker: str) -> Optional[DecisionTerminalPayload]:
    """Return a cached payload for ticker on the current trading session, or None."""
    if not verdict_cache_enabled():
        return None
    sym = (ticker or "").upper().strip()
    if not sym:
        return None
    session = _session_date()
    key = (sym, session)
    entry: Optional[_CacheEntry] = None
    with _lock:
        entry = _store.get(key)
    if entry is None:
        entry = _read_supabase(sym, session)
        if entry is not None:
            with _lock:
                _store[key] = entry
                _evict_if_needed()
    if entry is None:
        return None
    return overlay_fresh_spot(entry.payload, verdict_captured_at_utc=entry.verdict_captured_at_utc)


def store_verdict_cache(ticker: str, payload: DecisionTerminalPayload) -> None:
    """Store a freshly computed decision-terminal payload for the current session."""
    if not verdict_cache_enabled():
        return
    sym = (ticker or "").upper().strip()
    if not sym:
        return
    captured = payload.verdict_captured_at_utc or payload.generated_at_utc
    session = _session_date()
    key = (sym, session)
    with _lock:
        _store[key] = _CacheEntry(
            payload=payload,
            verdict_captured_at_utc=captured,
            session_date=session,
        )
        _evict_if_needed()
    _write_supabase(sym, session, payload, captured)


def overlay_fresh_spot(
    payload: DecisionTerminalPayload,
    *,
    verdict_captured_at_utc: Optional[str] = None,
) -> DecisionTerminalPayload:
    """Return a copy of payload with spot price overlaid from the live spot resolver."""
    from .connectors.spot import resolve_spot
    from .decision_terminal import _terminal_data_freshness

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
        updates["data_freshness"] = _terminal_data_freshness(
            spot_quote.source,
            bool(spot_quote.degraded),
            spot_quote.captured_at_utc or now,
        )
        if payload.valuation is not None:
            val = payload.valuation.model_copy(
                update={"current_price_usd": float(spot_quote.price)}
            )
            updates["valuation"] = val
    return payload.model_copy(update=updates)


def clear_verdict_cache(ticker: Optional[str] = None) -> None:
    """Test helper: clear all or one ticker's cached verdicts (in-memory L1 only)."""
    with _lock:
        if ticker:
            sym = ticker.upper().strip()
            keys = [k for k in _store if k[0] == sym]
            for k in keys:
                del _store[k]
        else:
            _store.clear()
