"""
Nightly / scheduled dreaming — ingest recent handoff events into CORAL notes + skills.

Reads ``coral_handoff_events`` (debate + swarm trace) from the last N hours and
writes compact hub notes so agents compound context without re-querying Chroma.

Env:
  CORAL_DREAMING_ENABLED (default 1)
  CORAL_DREAMING_HOURS (default 24)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from .coral_agents import hub_add_note, hub_add_skill

logger = logging.getLogger(__name__)

EVENT_DEBATE = "handoff_debate"
EVENT_SWARM = "handoff_swarm_trace"


async def run_dreaming_job(knowledge_store: Any = None, llm_client: Any = None) -> dict:
    """
    Summarize recent handoff events into CORAL hub (rule-based v1; no extra LLM calls).
    """
    out: dict[str, Any] = {"skipped": False, "reason": "", "notes_added": 0, "skills_added": 0}

    if os.environ.get("CORAL_DREAMING_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        out["skipped"] = True
        out["reason"] = "disabled"
        return out

    try:
        from . import coral_hub

        hours = max(1, min(168, int(os.environ.get("CORAL_DREAMING_HOURS", "24"))))
        since = time.time() - hours * 3600.0
        events = coral_hub.list_handoff_events_since(since)
    except Exception as e:
        logger.warning("[CoralDreaming] list events failed: %s", e)
        out["skipped"] = True
        out["reason"] = str(e)
        return out

    if not events:
        out["reason"] = "no_events"
        return out

    # One consolidated note per run (avoid spam); optional skill line from top tickers
    lines: list[str] = [f"[dream] Last {hours}h handoff digest — {len(events)} event(s)"]
    tickers: set[str] = set()
    for ev in events[:80]:
        et = ev.get("event_type") or ""
        p = ev.get("payload") or {}
        t = str(p.get("ticker") or "").upper().strip()
        if t:
            tickers.add(t[:12])
        if et == EVENT_DEBATE:
            lines.append(
                f"- debate {t}: verdict={p.get('verdict')} conf={p.get('consensus_confidence')}"
            )
        elif et == EVENT_SWARM:
            lines.append(
                f"- trace {t}: signal={p.get('global_signal')} verdict={p.get('global_verdict')}"
            )
        else:
            lines.append(f"- {et}: {str(p)[:120]}")

    blob = "\n".join(lines)[:7800]
    try:
        nid = hub_add_note(
            "dream_synthesizer",
            blob,
            market_regime="",
            ttl_seconds=float(os.environ.get("CORAL_DREAM_NOTE_TTL_SEC", str(14 * 24 * 3600))),
        )
        if nid and nid > 0:
            out["notes_added"] = 1
    except Exception as e:
        logger.warning("[CoralDreaming] add_note failed: %s", e)

    if tickers and out["notes_added"]:
        skill_body = (
            "Recent handoff tickers observed by dream job: " + ", ".join(sorted(tickers)[:40])
        )
        try:
            hub_add_skill(
                "dream_ticker_watchlist",
                skill_body[:4000],
                contributed_by="dream_synthesizer",
                name="Dream ticker watchlist",
            )
            out["skills_added"] = 1
        except Exception as e:
            logger.warning("[CoralDreaming] add_skill failed: %s", e)

    try:
        _ = knowledge_store
        _ = llm_client
    except Exception:
        pass

    logger.info("[CoralDreaming] notes=%s skills=%s", out["notes_added"], out["skills_added"])
    return out
