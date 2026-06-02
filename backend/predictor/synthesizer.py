"""LLM synthesis for predictor — NVIDIA Pro → Flash → Gemini 3.5 Flash cascade."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

_STATIC_FALLBACK = (
    "Probabilistic forecast narrative unavailable; rely on printed bands and disclaimers."
)

_SYS_PROMPT = (
    "You explain probabilistic stock forecasts for informational purposes only. "
    "Use ONLY the JSON in the user message. Do not invent numbers or apply arithmetic to prices. "
    "Short prose (max 6 sentences)."
)


def _nvidia_base_url() -> str:
    u = os.environ.get("NVIDIA_LLM_BASE_URL", "").strip().rstrip("/")
    if u:
        return u
    return "https://integrate.api.nvidia.com/v1"


def _nvidia_keys() -> list[str]:
    from ..openrouter_pool import collect_nvidia_llm_api_keys
    return collect_nvidia_llm_api_keys()


async def _try_nvidia(messages: list, *, model: str, temperature: float,
                      max_tokens: int, timeout: float) -> str | None:
    """Attempt a single NVIDIA Build call. Returns text on success, None on failure."""
    keys = _nvidia_keys()
    if not keys:
        return None
    import httpx

    base = _nvidia_base_url()
    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    for key in keys:
        headers = {"Authorization": f"Bearer {key}"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(f"{base}/chat/completions", json=body, headers=headers)
                if r.status_code >= 400:
                    logger.info("[PredictorSynth] NVIDIA %s returned %s", model, r.status_code)
                    continue
                data = r.json()
                text = (
                    (data.get("choices") or [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    or ""
                ).strip()
                if text:
                    return text
        except Exception as e:
            logger.info("[PredictorSynth] NVIDIA %s error: %s", model, e)
    return None


async def _try_gemini(messages: list, *, temperature: float, max_tokens: int) -> str | None:
    """Gemini 3.5 Flash fallback via google.genai."""
    try:
        from ..gemini_llm import gemini_simple_completion_sync, GEMINI_MODEL

        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        user = messages[-1]["content"] if messages else ""
        model = GEMINI_MODEL  # gemini-3.5-flash
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

    Cascade: NVIDIA Kimi K2.6 → NVIDIA DeepSeek v4 Pro → Gemini 3.5 Flash → static fallback.
    """
    payload = json.dumps(tool_json, sort_keys=True, separators=(",", ":"))
    from .config_loader import load_yaml_cached

    routing = load_yaml_cached("llm_routing.yaml").get("predictor") or {}
    synth = routing.get("synthesis") or {}
    temp = float(synth.get("temperature") or 0.2)
    max_tokens = int(synth.get("max_tokens") or 800)
    timeout = float(os.environ.get("PREDICTOR_SYNTH_TIMEOUT_S", "25") or "25")

    from ..llm_client import nvidia_llm_model_cascade

    messages = [
        {"role": "system", "content": _SYS_PROMPT},
        {"role": "user", "content": f"TOOL_JSON:\n{payload}"},
    ]

    for nvidia_model in nvidia_llm_model_cascade():
        for _attempt in range(2):
            result = await _try_nvidia(
                messages,
                model=nvidia_model,
                temperature=temp,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            if result:
                return result[:1500]

    # Gemini 3.5 Flash (paid fallback)
    result = await _try_gemini(messages, temperature=temp, max_tokens=max_tokens)
    if result:
        return result[:1500]

    # 4) Static fallback
    return (
        "Forecast synthesized from ensemble statistics and probabilistic head outputs "
        f"(cycle {cycle_id}). Figures come only from tool JSON — no recomputation."
    )
