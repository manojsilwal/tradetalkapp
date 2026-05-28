"""Tool-call-gated numeric market output enforcement."""

from __future__ import annotations

import re
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from ..trajectory import TrajectoryBuffer, TrajectoryEvent, TrajectoryEventType

F = TypeVar("F", bound=Callable[..., Any])

# Prices like $2,345.67, 2345.67 USD, +1.2%, -0.5%
_PRICE_RE = re.compile(
    r"(?:\$|USD\s*)?\d{1,3}(?:,\d{3})*(?:\.\d+)?|\b[+-]?\d+(?:\.\d+)?\s*%",
    re.IGNORECASE,
)


class PriceOutputGuard:
    def __init__(self, buffer: TrajectoryBuffer, *, agent_id: str, session_id: str) -> None:
        self._buffer = buffer
        self._agent_id = agent_id
        self._session_id = session_id
        self._last_tool_call_id: Optional[str] = None

    def register_tool_call(self, tool_call_id: str) -> None:
        self._last_tool_call_id = tool_call_id
        step = self._buffer.next_step()
        self._buffer.push(
            TrajectoryEvent(
                session_id=self._session_id,
                step=step,
                agent_id=self._agent_id,
                event_type=TrajectoryEventType.TOOL_CALL,
                payload={"tool_call_id": tool_call_id},
                tool_call_ref=tool_call_id,
                trace_id=tool_call_id,
            )
        )

    def sanitize_text(
        self,
        text: str,
        *,
        tool_call_ref: Optional[str] = None,
    ) -> str:
        ref = tool_call_ref or self._last_tool_call_id
        allowed = self._buffer.recent_tool_call_refs()
        if ref:
            allowed.add(ref)

        if not text or not _PRICE_RE.search(text):
            return text

        if ref and ref in allowed:
            step = self._buffer.next_step()
            self._buffer.push(
                TrajectoryEvent(
                    session_id=self._session_id,
                    step=step,
                    agent_id=self._agent_id,
                    event_type=TrajectoryEventType.PRICE_OUTPUT,
                    payload={"snippet": text[:200]},
                    tool_call_ref=ref,
                    trace_id=ref,
                )
            )
            return text

        redacted = _PRICE_RE.sub("[PRICE_REDACTED]", text)
        step = self._buffer.next_step()
        self._buffer.push(
            TrajectoryEvent(
                session_id=self._session_id,
                step=step,
                agent_id=self._agent_id,
                event_type=TrajectoryEventType.HALLUCINATION_FLAG,
                payload={"original": text[:400], "reason": "missing_tool_call_ref"},
                trace_id=ref or "",
            )
        )
        return redacted


def price_output_guard(
    buffer: TrajectoryBuffer,
    *,
    agent_id: str,
    session_id: str,
) -> Callable[[F], F]:
    guard = PriceOutputGuard(buffer, agent_id=agent_id, session_id=session_id)

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = fn(*args, **kwargs)
            if isinstance(result, str):
                return guard.sanitize_text(result)
            if isinstance(result, dict):
                out = dict(result)
                for key in ("summary", "text", "briefing", "levels_to_watch"):
                    if key in out and isinstance(out[key], str):
                        out[key] = guard.sanitize_text(out[key])
                return out
            return result

        return wrapper  # type: ignore[return-value]

    return decorator
