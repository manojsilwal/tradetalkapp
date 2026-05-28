"""Runtime hooks for existing TradeTalk surfaces."""

from __future__ import annotations

from typing import Any, Dict

from .guards.price_output_guard import PriceOutputGuard
from .loop import get_session_loop, harness_enabled
from .trajectory import TrajectoryEvent, TrajectoryEventType


def record_gold_advisor_step(
    *,
    context: Dict[str, Any],
    briefing: Dict[str, Any],
    session_id: str = "gold-advisor",
) -> Dict[str, Any]:
    """Emit trajectory events and sanitize briefing text for numeric outputs."""
    if not harness_enabled():
        return briefing

    loop = get_session_loop(session_id)

    tool_ref = f"gold_fetch:{context.get('as_of_utc', '')}"
    guard = PriceOutputGuard(loop.buffer, agent_id="gold_advisor", session_id=session_id)
    guard.register_tool_call(tool_ref)

    macro = context.get("macro") or {}
    step = loop.buffer.next_step()
    loop.buffer.push(
        TrajectoryEvent(
            session_id=session_id,
            step=step,
            agent_id="gold_advisor",
            event_type=TrajectoryEventType.TOOL_RESULT,
            payload={
                "gold_last": macro.get("gold_futures_last_usd"),
                "dxy": macro.get("dxy_spot"),
                "data_timestamp": context.get("as_of_utc"),
            },
            tool_call_ref=tool_ref,
            trace_id=tool_ref,
        )
    )

    sanitized = dict(briefing or {})
    for key in ("summary", "levels_to_watch"):
        if isinstance(sanitized.get(key), str):
            sanitized[key] = guard.sanitize_text(sanitized[key], tool_call_ref=tool_ref)

    return sanitized


async def harness_on_events(session_id: str, events: list[TrajectoryEvent]) -> None:
    if not harness_enabled() or not events:
        return
    loop = get_session_loop(session_id)
    await loop.on_step(events)


def merge_harness_memory_channel(
    channel_hits: Dict[str, list],
    *,
    session_id: str = "default",
    weight: float = 1.5,
) -> Dict[str, list]:
    """Inject harness memory overrides as an extra RRF channel."""
    if not harness_enabled():
        return channel_hits
    from .editors.memory_editor import MemoryEditor

    state = get_session_loop(session_id).manager.get_current_state()
    hits = MemoryEditor().as_retrieval_channel(state, query="", top_k=10)
    for h in hits:
        h["_harness_weight"] = weight
    out = dict(channel_hits or {})
    out["harness"] = hits
    return out
