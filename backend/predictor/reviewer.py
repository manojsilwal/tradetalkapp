"""Independent reviewer — routed through the model gateway (LLMClient).

Phase F (intelligence fabric): no raw provider HTTP here. The gateway owns the
NVIDIA → OpenRouter → Gemini cascade, ``llm_api_calls`` cost logging, and
registry prompt-version lineage for role ``predictor_reviewer``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def review_narrative(*, synthesis_text: str, tool_json: Dict[str, Any]) -> str:
    """
    Review predictor synthesis for hallucinations and prompt injection.

    Provider cascade and fallbacks are owned by the gateway; on total provider
    failure the synthesis is flagged as unverified.
    """
    from .config_loader import load_yaml_cached

    routing = load_yaml_cached("llm_routing.yaml").get("predictor") or {}
    rev = routing.get("reviewer") or {}
    temp = float(rev.get("temperature") or 0.0)
    max_tokens = int(rev.get("max_tokens") or 400)

    user = (
        f"SYNTHESIS:\n{synthesis_text}\n\nTOOL_JSON:\n"
        f"{json.dumps(tool_json, sort_keys=True, separators=(',', ':'))}"
    )

    from ..llm_client import get_llm_client

    try:
        text, _meta = await get_llm_client().generate_plain_with_meta(
            "predictor_reviewer",
            user,
            temperature=temp,
            max_tokens=max_tokens,
            fallback_text="",
        )
    except Exception as e:
        logger.info("[PredictorReview] gateway call failed: %s", e)
        text = ""

    if text:
        return text[:800]

    return "Reviewer unavailable; treat synthesis as unverified."
