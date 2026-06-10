"""LLM synthesis for predictor — routed through the model gateway (LLMClient).

Phase F (intelligence fabric): no raw provider HTTP here. The gateway owns the
NVIDIA → OpenRouter → Gemini cascade, ``llm_api_calls`` cost logging, and
registry prompt-version lineage for role ``predictor_synthesizer``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

_STATIC_FALLBACK = (
    "Probabilistic forecast narrative unavailable; rely on printed bands and disclaimers."
)


async def synthesize_narrative(*, tool_json: Dict[str, Any], cycle_id: str) -> str:
    """
    Summarize tool outputs only — never recompute prices here.

    Provider cascade and fallbacks are owned by the gateway; on total provider
    failure a deterministic static narrative is returned.
    """
    payload = json.dumps(tool_json, sort_keys=True, separators=(",", ":"))
    from .config_loader import load_yaml_cached

    routing = load_yaml_cached("llm_routing.yaml").get("predictor") or {}
    synth = routing.get("synthesis") or {}
    temp = float(synth.get("temperature") or 0.2)
    max_tokens = int(synth.get("max_tokens") or 800)

    from ..llm_client import get_llm_client

    try:
        text, _meta = await get_llm_client().generate_plain_with_meta(
            "predictor_synthesizer",
            f"TOOL_JSON:\n{payload}",
            temperature=temp,
            max_tokens=max_tokens,
            fallback_text="",
        )
    except Exception as e:
        logger.info("[PredictorSynth] gateway call failed: %s", e)
        text = ""

    if text:
        return text[:1500]

    return (
        "Forecast synthesized from ensemble statistics and probabilistic head outputs "
        f"(cycle {cycle_id}). Figures come only from tool JSON — no recomputation."
    )
