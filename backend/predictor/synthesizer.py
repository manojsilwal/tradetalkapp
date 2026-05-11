"""LLM synthesis for predictor — OpenRouter only (not GEMINI_PRIMARY). Narrative only."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def synthesize_narrative(*, tool_json: Dict[str, Any], cycle_id: str) -> str:
    """
    Summarize tool outputs only — never recompute prices here.
    Uses dedicated OpenRouter call when ``OPENROUTER_API_KEY`` is set.
    """
    payload = json.dumps(tool_json, sort_keys=True, separators=(",", ":"))
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        return (
            "Forecast synthesized from ensemble statistics and probabilistic head outputs "
            f"(cycle {cycle_id}). Figures come only from tool JSON — no recomputation."
        )
    try:
        import httpx

        from .config_loader import load_yaml_cached

        routing = load_yaml_cached("llm_routing.yaml").get("predictor") or {}
        synth = routing.get("synthesis") or {}
        model = str(synth.get("primary") or os.environ.get("OPENROUTER_MODEL") or "openai/gpt-4o-mini")
        temp = float(synth.get("temperature") or 0.2)
        max_tokens = int(synth.get("max_tokens") or 800)
        base = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        headers = {
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://tradetalk.local",
            "X-Title": "TradeTalk Predictor",
        }
        sys_prompt = (
            "You explain probabilistic stock forecasts for informational purposes only. "
            "Use ONLY the JSON in the user message. Do not invent numbers or apply arithmetic to prices. "
            "Short prose (max 6 sentences)."
        )
        body = {
            "model": model,
            "temperature": temp,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": f"TOOL_JSON:\n{payload}"},
            ],
        }
        timeout = float(os.environ.get("PREDICTOR_SYNTH_TIMEOUT_S", "25") or "25")
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{base}/chat/completions", json=body, headers=headers)
            if r.status_code >= 400:
                logger.warning("[PredictorSynth] OpenRouter %s", r.status_code)
                raise RuntimeError("openrouter_error")
            data = r.json()
            text = (
                (data.get("choices") or [{}])[0]
                .get("message", {})
                .get("content", "")
                or ""
            ).strip()
            return text[:1500]
    except Exception as e:
        logger.debug("[PredictorSynth] fallback: %s", e)
        return (
            "Probabilistic forecast narrative unavailable; rely on printed bands and disclaimers."
        )
