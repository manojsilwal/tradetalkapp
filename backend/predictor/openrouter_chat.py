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
    """Attempt OpenRouter/NVIDIA chat/completions. Returns text on success, None on failure."""
    from ..llm_client import NVIDIA_BASE_URL, NVIDIA_MODEL, OPENROUTER_BASE_URL, OPENROUTER_MODEL
    from ..openrouter_pool import collect_nvidia_llm_api_keys, collect_openrouter_api_keys

    nv_keys = collect_nvidia_llm_api_keys()
    or_keys = collect_openrouter_api_keys()

    if not nv_keys and not or_keys:
        return None

    import httpx

    # Candidates: list of (base_url, keys, model_name)
    candidates = []
    if nv_keys:
        nv_model = NVIDIA_MODEL if model == OPENROUTER_MODEL else model
        candidates.append((NVIDIA_BASE_URL, nv_keys, nv_model))
    if or_keys:
        candidates.append((OPENROUTER_BASE_URL, or_keys, model))

    for base_url, keys, model_name in candidates:
        base = base_url.rstrip("/")
        body = {
            "model": model_name,
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
                        logger.info("[PredictorOpenRouter] %s returned %s", model_name, r.status_code)
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
                logger.info("[PredictorOpenRouter] %s error: %s", model_name, e)
    return None
