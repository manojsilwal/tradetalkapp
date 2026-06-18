"""LLM synthesis for predictor — OpenRouter primary, Gemini 3.5 Flash fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict

from .openrouter_chat import try_openrouter_chat

logger = logging.getLogger(__name__)

_SYS_PROMPT = (
    "You explain probabilistic stock forecasts for informational purposes only. "
    "Use ONLY the JSON in the user message. Do not invent numbers or apply arithmetic to prices. "
    "Short prose (max 6 sentences)."
)


async def _try_gemini(messages: list, *, temperature: float, max_tokens: int) -> str | None:
    """Gemini 3.5 Flash fallback via google.genai."""
    try:
        from ..gemini_llm import gemini_simple_completion_sync, GEMINI_MODEL

        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        user = messages[-1]["content"] if messages else ""
        model = GEMINI_MODEL
        text = await asyncio.to_thread(
            gemini_simple_completion_sync,
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=False,
            model=model,
        )
        return (text or "").strip() or None
    except Exception as e:
        logger.info("[PredictorSynth] Gemini fallback error: %s", e)
        return None


async def synthesize_narrative(*, tool_json: Dict[str, Any], cycle_id: str) -> str:
    """
    Summarize tool outputs only — never recompute prices here.

    Cascade: OpenRouter (OPENROUTER_MODEL) → Gemini 3.5 Flash → static fallback.
    """
    payload = json.dumps(tool_json, sort_keys=True, separators=(",", ":"))
    from .config_loader import load_yaml_cached

    routing = load_yaml_cached("llm_routing.yaml").get("predictor") or {}
    synth = routing.get("synthesis") or {}
    temp = float(synth.get("temperature") or 0.2)
    max_tokens = int(synth.get("max_tokens") or 800)
    timeout = float(os.environ.get("PREDICTOR_SYNTH_TIMEOUT_S", "25") or "25")

    from ..llm_client import OPENROUTER_MODEL

    messages = [
        {"role": "system", "content": _SYS_PROMPT},
        {"role": "user", "content": f"TOOL_JSON:\n{payload}"},
    ]

    for _attempt in range(2):
        result = await try_openrouter_chat(
            messages,
            model=OPENROUTER_MODEL,
            temperature=temp,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        if result:
            return result[:1500]

    result = await _try_gemini(messages, temperature=temp, max_tokens=max_tokens)
    if result:
        return result[:1500]

    return (
        "Forecast synthesized from ensemble statistics and probabilistic head outputs "
        f"(cycle {cycle_id}). Figures come only from tool JSON — no recomputation."
    )
