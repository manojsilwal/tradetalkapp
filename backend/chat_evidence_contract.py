"""
Layer 1 — structured evidence contract for chat (Phase A).

Builds a small JSON-serializable payload per assistant turn: sources_used,
tools_called, confidence_band, abstain_reason. Tool outcomes are classified from
handler return strings so the contract stays deterministic for eval harnesses.
"""
from __future__ import annotations

from typing import Any, Optional


def classify_tool_result(result: str) -> str:
    """Return success | empty | error for a tool handler string result."""
    s = (result or "").strip()
    if not s:
        return "empty"
    head = s[:120].lower()
    if s.startswith("Error ") or s.startswith("Error fetching") or "error executing" in head:
        return "error"
    if "invalid ticker" in head or "invalid url" in head or "please provide" in head:
        return "empty"
    if "no price data" in head or "no historical" in head or "no recent news" in head:
        return "empty"
    if "unavailable" in head and ("fincrawler" in head or "scraping" in head or "sec filing" in head):
        return "empty"
    if len(s) < 40 and ("no " in head or "not found" in head or "delisted" in head):
        return "empty"
    return "success"


def build_evidence_contract(
    *,
    tool_trace: list[dict[str, Any]],
    quote_card_tickers: list[str],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """
    Assemble the side-channel contract for one chat turn.

    tool_trace items: {name, outcome, error?} (arguments logged server-side only).
    """
    rag_ok = bool(meta.get("rag_nonempty"))
    coral_ok = bool(meta.get("coral_hub_nonempty"))

    sources_used: list[str] = []
    if rag_ok:
        sources_used.append("internal_kb")
    if coral_ok:
        sources_used.append("coral_hub")

    tool_outcomes: list[dict[str, str]] = []
    for t in tool_trace:
        name = t.get("name") or "unknown"
        oc = t.get("outcome") or "empty"
        tool_outcomes.append({"name": str(name), "outcome": str(oc)})
        if oc == "success":
            sources_used.append(f"tool:{name}")

    for tk in quote_card_tickers:
        if tk:
            sources_used.append(f"quote_card:{tk}")

    tools_called = [str(t.get("name", "")) for t in tool_trace if t.get("name")]

    has_success_tool = any(t.get("outcome") == "success" for t in tool_trace)
    has_quote = bool(quote_card_tickers)
    any_tools = bool(tool_trace)
    all_bad = any_tools and all(t.get("outcome") in ("empty", "error") for t in tool_trace)

    abstain_reason: Optional[str] = None
    if any_tools and all_bad and not has_quote:
        abstain_reason = "all_tools_empty_or_error"

    if has_quote or has_success_tool:
        confidence_band = "high"
    elif all_bad:
        confidence_band = "low"
    else:
        confidence_band = "medium"

    return {
        "schema_version": 1,
        "sources_used": sources_used,
        "tools_called": tools_called,
        "tool_outcomes": tool_outcomes,
        "confidence_band": confidence_band,
        "abstain_reason": abstain_reason,
    }
