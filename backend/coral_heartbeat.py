"""
Heartbeat-driven CORAL reflection — periodic notes to the structured hub.

Runs on APScheduler during US equity market hours (configurable) and writes
a compact observation from cached market intel so chat/swarm can read cheap
structured context before vector RAG.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from . import coral_hub

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def us_equity_market_hours_open(now: Optional[datetime] = None) -> bool:
    """True during regular US equity session Mon–Fri 09:30–16:00 ET."""
    dt = now or datetime.now(_ET)
    if dt.weekday() >= 5:
        return False
    minutes = dt.hour * 60 + dt.minute
    open_m = 9 * 60 + 30
    close_m = 16 * 60
    return open_m <= minutes < close_m


def _intel_one_liner(intel: dict[str, Any]) -> str:
    parts: list[str] = []
    hl = intel.get("headlines") or []
    if isinstance(hl, list) and hl:
        h0 = hl[0]
        title = (h0 if isinstance(h0, str) else str(h0))[:120]
        if title:
            parts.append(f"Headline: {title}")
    fomc = intel.get("fomc") or {}
    if isinstance(fomc, dict) and fomc.get("next_meeting"):
        parts.append(f"Next FOMC: {fomc.get('next_meeting')}")
    sp = intel.get("sector_perf") or {}
    if isinstance(sp, dict) and sp:
        vals = list(sp.values())
        if vals:
            top = max(vals, key=lambda x: float(x.get("pct", 0) or 0))
            nm = top.get("name") or top.get("symbol") or ""
            pct = top.get("pct")
            if nm and pct is not None:
                parts.append(f"Leading sector: {nm} {float(pct):+.2f}%")
    if not parts:
        return "Market intel cache refreshed (no headline snapshot)."
    return " | ".join(parts)[:500]


async def run_coral_heartbeat(
    knowledge_store: Any,
    llm_client: Any = None,
) -> dict:
    """
    Append a hub note from current market intel; optionally read peer notes.

    Controlled by env:
      CORAL_HEARTBEAT_ENABLED (default 1)
      CORAL_HEARTBEAT_IGNORE_MARKET_HOURS (default 0) — set 1 for dev/tests
    """
    out: dict = {"skipped": False, "reason": "", "note_id": -1}

    if os.environ.get("CORAL_HEARTBEAT_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        out["skipped"] = True
        out["reason"] = "disabled"
        return out

    ignore_hours = os.environ.get("CORAL_HEARTBEAT_IGNORE_MARKET_HOURS", "0").strip().lower() in (
        "1", "true", "yes",
    )
    if not ignore_hours and not us_equity_market_hours_open():
        out["skipped"] = True
        out["reason"] = "outside_market_hours"
        return out

    try:
        from .market_intel import get_intel

        intel = get_intel() or {}
    except Exception as e:
        logger.warning("[CoralHeartbeat] get_intel failed: %s", e)
        intel = {}

    regime = ""
    try:
        from . import market_l1_cache

        snap = market_l1_cache.get_snapshot() or {}
        cs = snap.get("credit_stress_index")
        if cs is not None:
            regime = "BULL_NORMAL" if float(cs) <= 1.1 else "BEAR_STRESS"
    except Exception:
        regime = ""

    observation = _intel_one_liner(intel)
    peers = coral_hub.list_recent_notes(n=4, exclude_agent_id="heartbeat")
    if peers:
        peer_line = " | ".join(str(p.get("observation", ""))[:120] for p in peers[:2])
        observation = f"{observation} [recent peer notes: {peer_line}]"[:800]

    note_id = coral_hub.add_note(
        "heartbeat",
        observation,
        market_regime=regime,
        ttl_seconds=float(os.environ.get("CORAL_NOTE_TTL_SEC", str(7 * 24 * 3600))),
    )
    out["note_id"] = note_id
    out["regime"] = regime

    # Optional: promote a tiny skill from stable intel (no extra LLM by default)
    if os.environ.get("CORAL_HEARTBEAT_WRITE_SKILL", "0").strip().lower() in ("1", "true", "yes"):
        coral_hub.add_skill(
            "market_intel_snapshot",
            observation[:2000],
            contributed_by="heartbeat",
            name="Last intel snapshot",
        )

    try:
        _ = knowledge_store  # reserved for future Chroma cross-write
    except Exception:
        pass

    logger.info("[CoralHeartbeat] note_id=%s regime=%s", note_id, regime)
    return out
