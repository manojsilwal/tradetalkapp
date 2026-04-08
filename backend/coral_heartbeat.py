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


def _regime_from_credit_stress(snap: dict[str, Any]) -> str:
    try:
        cs = snap.get("credit_stress_index")
        if cs is None:
            return ""
        return "BULL_NORMAL" if float(cs) <= 1.1 else "BEAR_STRESS"
    except Exception:
        return ""


def _observation_data_ingest(intel: dict[str, Any]) -> str:
    try:
        from .market_intel import updated_at_epoch

        age = max(0, int(__import__("time").time() - updated_at_epoch()))
    except Exception:
        age = -1
    hl = intel.get("headlines") or []
    nhl = len(hl) if isinstance(hl, list) else 0
    sp = intel.get("sector_perf") or {}
    nsec = len(sp) if isinstance(sp, dict) else 0
    return f"[data_ingest] MIL cache age≈{age}s; headlines={nhl}; sector_perf keys={nsec}"[:800]


def _observation_technical(snap: dict[str, Any]) -> str:
    q = snap.get("quotes") or {}
    spy, qqq, gld = q.get("SPY"), q.get("QQQ"), q.get("GLD")
    vix = snap.get("vix_level")
    cs = snap.get("credit_stress_index")
    se = snap.get("sector_etfs") or {}
    nsec = len(se) if isinstance(se, dict) else 0
    return (
        f"[technical] SPY={spy} QQQ={qqq} GLD={gld} | VIX={vix} | "
        f"credit_stress={cs} | sector_etf_quotes={nsec}"
    )[:800]


def _observation_sentiment(intel: dict[str, Any]) -> str:
    hl = intel.get("headlines") or []
    if not isinstance(hl, list) or not hl:
        return "[sentiment] No headline list in MIL cache (RSS/yfinance may still be warming)."
    titles = []
    for h in hl[:3]:
        titles.append((h if isinstance(h, str) else str(h))[:100])
    return ("[sentiment] " + " | ".join(titles))[:800]


def _observation_gold(snap: dict[str, Any]) -> str:
    q = snap.get("quotes") or {}
    gld, uup = q.get("GLD"), q.get("UUP")
    return (
        f"[gold_analysis] GLD proxy≈{gld} USD; UUP (USD)≈{uup} "
        f"— use /advisor/gold for full briefing."
    )[:800]


async def run_coral_agent_reflections(knowledge_store: Any, llm_client: Any = None) -> dict:
    """
    One structured note per finance agent (data_ingest, technical, sentiment, gold_analysis).

    Same market-hours gate as :func:`run_coral_heartbeat` unless
    ``CORAL_HEARTBEAT_IGNORE_MARKET_HOURS=1``.

    Env:
      CORAL_AGENT_REFLECTIONS (default 1) — set 0 to disable multi-agent notes.
    """
    out: dict[str, Any] = {"skipped": False, "reason": "", "note_ids": []}

    if os.environ.get("CORAL_AGENT_REFLECTIONS", "1").strip().lower() in ("0", "false", "no"):
        out["skipped"] = True
        out["reason"] = "agent_reflections_disabled"
        return out

    ignore_hours = os.environ.get("CORAL_HEARTBEAT_IGNORE_MARKET_HOURS", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not ignore_hours and not us_equity_market_hours_open():
        out["skipped"] = True
        out["reason"] = "outside_market_hours"
        return out

    from .coral_agents import (
        AGENT_DATA_INGEST,
        AGENT_GOLD_ANALYSIS,
        AGENT_SENTIMENT,
        AGENT_TECHNICAL,
        hub_add_note,
    )

    try:
        from .market_intel import get_intel

        intel = get_intel() or {}
    except Exception as e:
        logger.warning("[CoralAgentReflections] get_intel failed: %s", e)
        intel = {}

    try:
        from . import market_l1_cache

        snap = market_l1_cache.get_snapshot() or {}
    except Exception as e:
        logger.warning("[CoralAgentReflections] L1 snapshot failed: %s", e)
        snap = {}

    regime = _regime_from_credit_stress(snap)
    ttl = float(os.environ.get("CORAL_NOTE_TTL_SEC", str(7 * 24 * 3600)))

    pairs = [
        (AGENT_DATA_INGEST, _observation_data_ingest(intel)),
        (AGENT_TECHNICAL, _observation_technical(snap)),
        (AGENT_SENTIMENT, _observation_sentiment(intel)),
        (AGENT_GOLD_ANALYSIS, _observation_gold(snap)),
    ]

    for agent_id, obs in pairs:
        text = (obs or "").strip()
        if not text:
            continue
        try:
            nid = hub_add_note(agent_id, text, market_regime=regime, ttl_seconds=ttl)
            out["note_ids"].append({"agent_id": agent_id, "note_id": nid})
        except Exception as e:
            logger.warning("[CoralAgentReflections] %s failed: %s", agent_id, e)

    logger.info("[CoralAgentReflections] wrote %s agent notes", len(out["note_ids"]))
    try:
        _ = knowledge_store
        _ = llm_client
    except Exception:
        pass
    return out
