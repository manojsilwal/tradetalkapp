"""
Phase B — frozen evidence memo for chat (Decision Terminal–style provenance cues).

Builds a static Markdown artifact: disclaimer, user query, assistant reply excerpt,
evidence contract table, and per-source provenance rows (source / confidence / notes).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from .decision_terminal import DISCLAIMER


def build_chat_evidence_memo_markdown(
    *,
    session_id: str,
    user_message: str,
    assistant_text: str,
    evidence_contract: dict[str, Any],
    meta: Optional[dict[str, Any]] = None,
) -> str:
    """Single-turn memo suitable for audit / export (not legal filing)."""
    meta = meta or {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ev = evidence_contract or {}
    lines: list[str] = [
        "# TradeTalk — evidence memo (chat)",
        "",
        f"**Session:** `{session_id[:16]}…`  ",
        f"**Generated (UTC):** {now}  ",
        "",
        "## Disclaimer",
        "",
        DISCLAIMER,
        "",
        "## User message",
        "",
        (user_message or "").strip() or "_(empty)_",
        "",
        "## Assistant reply (excerpt)",
        "",
        _excerpt(assistant_text, 6000),
        "",
        "## Evidence contract (Layer 1 JSON)",
        "",
        "```json",
        json.dumps(ev, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Provenance summary",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| confidence_band | {ev.get('confidence_band', '')} |",
        f"| abstain_reason | {ev.get('abstain_reason') or '—'} |",
        f"| rag_nonempty (meta) | {meta.get('rag_nonempty', '')} |",
        f"| coral_hub_nonempty (meta) | {meta.get('coral_hub_nonempty', '')} |",
        "",
        "### Declared sources",
        "",
    ]
    for s in ev.get("sources_used") or []:
        lines.append(f"- **{s}** — see tool outputs / retrieval blocks from this turn.")
    if not (ev.get("sources_used") or []):
        lines.append("_No explicit sources_used — conversational or pre-tool turn._")
    lines.extend(
        [
            "",
            "### Tool outcomes",
            "",
        ]
    )
    for row in ev.get("tool_outcomes") or []:
        if isinstance(row, dict):
            lines.append(f"- `{row.get('name')}` → **{row.get('outcome')}**")
    lines.append("")
    lines.append(
        "_Every factual claim in the assistant reply should align with the evidence contract, "
        "retrieval blocks, or explicit abstention — see product Layer 1 policy._"
    )
    lines.append("")
    return "\n".join(lines)


def _excerpt(text: str, max_chars: int) -> str:
    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 20] + "\n\n… _(truncated)_"
