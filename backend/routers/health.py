"""Data Trust Layer observability — GET /health/data-freshness.

Reports the calendar context (current session + the real last completed session)
and a freshness envelope for each major data source so ops (and an optional global
UI status dot) can see at a glance whether the app is serving stale data.

The handler is best-effort and never raises: any source that cannot be assessed
is reported with an ``error`` note rather than failing the whole probe.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health/data-freshness")
def data_freshness_health() -> Dict[str, Any]:
    from ..freshness import assess
    from ..market_calendar import last_completed_session, now_et, session_status

    sources: List[Dict[str, Any]] = []

    # 1. Daily-prices store (the canonical EOD staleness signal).
    try:
        from ..daily_brief import get_latest_trade_date

        db_latest = get_latest_trade_date()
        f = assess(data_class="daily_brief", source="daily_prices_store", as_of=db_latest)
        sources.append({"name": "daily_prices_store", **f.model_dump()})
    except Exception as e:  # pragma: no cover - defensive
        sources.append({"name": "daily_prices_store", "error": str(e)})

    # 2. Market-intel live movers cache (intraday quote freshness).
    try:
        from .. import market_intel

        ts = float(getattr(market_intel, "_live_movers_cache_ts", 0.0) or 0.0)
        captured_at = datetime.fromtimestamp(ts, tz=timezone.utc) if ts > 0 else None
        # During an open session this should be live; otherwise treat as EOD-tier.
        klass = "live_quote" if session_status() == "regular" else "delayed_quote"
        f = assess(data_class=klass, source="market_intel_cache", captured_at=captured_at)
        sources.append({"name": "market_intel_live_movers", **f.model_dump()})
    except Exception as e:  # pragma: no cover - defensive
        sources.append({"name": "market_intel_live_movers", "error": str(e)})

    any_stale = any(bool(s.get("is_stale")) for s in sources if "is_stale" in s)

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "session_status": session_status(),
        "now_et": now_et().isoformat(),
        "last_completed_session": last_completed_session().isoformat(),
        "any_stale": any_stale,
        "sources": sources,
    }
