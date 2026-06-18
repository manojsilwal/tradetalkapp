"""OpenRouter chat completions for predictor synthesis/reviewer."""

from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)


async def try_openrouter_chat(
    messages: List[dict],
    *,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> str | None:
    """Attempt OpenRouter chat/completions. Returns text on success, None on failure."""
    from ..llm_client import OPENROUTER_BASE_URL
    from ..openrouter_pool import collect_openrouter_api_keys

    keys = collect_openrouter_api_keys()
    if not keys:
        return None

    import httpx

    base = OPENROUTER_BASE_URL.rstrip("/")
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
                    logger.info("[PredictorOpenRouter] %s returned %s", model, r.status_code)
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
            logger.info("[PredictorOpenRouter] %s error: %s", model, e)
    return None
