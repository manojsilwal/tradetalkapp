"""
Opt-in live probes for OpenRouter LLM and Google embedding APIs.

Security: routes return 404 unless ALLOW_PROVIDER_SMOKE=1. Optional PROVIDER_SMOKE_SECRET
requires matching X-Provider-Smoke-Secret header (recommended in shared environments).
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/health/smoke", tags=["health"])


def _smoke_enabled() -> bool:
    return os.environ.get("ALLOW_PROVIDER_SMOKE", "").strip().lower() in ("1", "true", "yes")


def _verify_smoke_request(request: Request) -> None:
    if not _smoke_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    secret = os.environ.get("PROVIDER_SMOKE_SECRET", "").strip()
    if secret and request.headers.get("x-provider-smoke-secret") != secret:
        raise HTTPException(status_code=404, detail="Not Found")


class SmokeOpenRouterBody(BaseModel):
    prompt: str = Field(
        default='Reply with exactly one word: OK',
        description="Short user message to minimize tokens/cost.",
    )


class SmokeEmbedBody(BaseModel):
    text: str = Field(default="tradetalk embedding smoke test")
    task_type: str = Field(default="RETRIEVAL_QUERY", description="RETRIEVAL_QUERY or RETRIEVAL_DOCUMENT")


@router.get("/status")
def smoke_status(request: Request) -> Dict[str, Any]:
    """Non-secret snapshot of whether NVIDIA / OpenRouter / Google embedding env is present."""
    _verify_smoke_request(request)
    from ..openrouter_pool import collect_openrouter_api_keys, collect_nvidia_llm_api_keys, resolve_llm_http_provider
    from ..vector_backends import (
        _DEFAULT_GEMINI_EMBEDDING_MODEL,
        _gemini_embedding_api_key,
        google_embeddings_enabled,
    )
    from ..llm_client import (
        OPENROUTER_BASE_URL,
        OPENROUTER_MODEL,
        OPENROUTER_MODEL_LIGHT,
        NVIDIA_BASE_URL,
        NVIDIA_MODEL,
        NVIDIA_MODEL_LIGHT,
    )

    return {
        "allow_provider_smoke": True,
        "llm_http_provider": resolve_llm_http_provider(),
        "nvidia_api_key_configured": bool(collect_nvidia_llm_api_keys()),
        "nvidia_base_url": NVIDIA_BASE_URL,
        "nvidia_models": {"heavy": NVIDIA_MODEL, "light": NVIDIA_MODEL_LIGHT},
        "openrouter_api_key_configured": bool(collect_openrouter_api_keys()),
        "openrouter_base_url": OPENROUTER_BASE_URL,
        "openrouter_models": {"heavy": OPENROUTER_MODEL, "light": OPENROUTER_MODEL_LIGHT},
        "gemini_api_key_configured": bool(_gemini_embedding_api_key()),
        "google_embeddings_enabled": google_embeddings_enabled(),
        "embedding_model_resolved": (
            os.environ.get("GEMINI_EMBEDDING_MODEL", "").strip() or _DEFAULT_GEMINI_EMBEDDING_MODEL
        ),
    }


@router.post("/openrouter/chat")
def smoke_openrouter_chat(request: Request, body: SmokeOpenRouterBody = SmokeOpenRouterBody()) -> Dict[str, Any]:
    """One short chat completion via the same OpenRouter pool as LLMClient."""
    _verify_smoke_request(request)
    from ..llm_client import (
        OPENROUTER_BASE_URL,
        OPENROUTER_HTTP_REFERER,
        OPENROUTER_MODEL_LIGHT,
        OPENROUTER_X_TITLE,
    )
    from ..openrouter_pool import (
        collect_openrouter_api_keys,
        get_or_create_openrouter_pool,
    )

    keys = collect_openrouter_api_keys()
    if not keys:
        return {"ok": False, "skipped": True, "reason": "no_openrouter_api_key"}

    headers: Dict[str, str] = {}
    if OPENROUTER_HTTP_REFERER:
        headers["HTTP-Referer"] = OPENROUTER_HTTP_REFERER
    if OPENROUTER_X_TITLE:
        headers["X-Title"] = OPENROUTER_X_TITLE

    model = OPENROUTER_MODEL_LIGHT
    try:
        pool = get_or_create_openrouter_pool(OPENROUTER_BASE_URL, headers)
        if pool is None:
            return {"ok": False, "skipped": True, "reason": "openrouter_pool_unavailable"}

        client = pool.next_sync()
        t0 = time.perf_counter()
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": body.prompt}],
            max_tokens=64,
            temperature=0.2,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        text = (completion.choices[0].message.content or "").strip()
        return {
            "ok": True,
            "provider": "openrouter",
            "model": model,
            "reply_preview": text[:500],
            "latency_ms": latency_ms,
        }
    except Exception as e:
        return {"ok": False, "provider": "openrouter", "error": str(e)[:500]}


@router.post("/nvidia/chat")
def smoke_nvidia_chat(request: Request, body: SmokeOpenRouterBody = SmokeOpenRouterBody()) -> Dict[str, Any]:
    """One short chat completion via the same NVIDIA pool as LLMClient."""
    _verify_smoke_request(request)
    from ..llm_client import (
        NVIDIA_BASE_URL,
        NVIDIA_MODEL_LIGHT,
    )
    from ..openrouter_pool import (
        collect_nvidia_llm_api_keys,
        get_or_create_llm_openai_compatible_pool,
    )

    keys = collect_nvidia_llm_api_keys()
    if not keys:
        return {"ok": False, "skipped": True, "reason": "no_nvidia_api_key"}

    model = NVIDIA_MODEL_LIGHT
    try:
        pool = get_or_create_llm_openai_compatible_pool(NVIDIA_BASE_URL, {}, keys)
        if pool is None:
            return {"ok": False, "skipped": True, "reason": "nvidia_pool_unavailable"}

        client = pool.next_sync()
        t0 = time.perf_counter()
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": body.prompt}],
            max_tokens=64,
            temperature=0.2,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        text = (completion.choices[0].message.content or "").strip()
        return {
            "ok": True,
            "provider": "nvidia",
            "model": model,
            "reply_preview": text[:500],
            "latency_ms": latency_ms,
        }
    except Exception as e:
        return {"ok": False, "provider": "nvidia", "error": str(e)[:500]}


@router.post("/google/embedding")
def smoke_google_embedding(request: Request, body: SmokeEmbedBody = SmokeEmbedBody()) -> Dict[str, Any]:
    """Single embed_content call using GEMINI_EMBEDDING_MODEL (default multilingual embedding)."""
    _verify_smoke_request(request)
    from ..vector_backends import (
        _DEFAULT_GEMINI_EMBEDDING_MODEL,
        _gemini_embedding_api_key,
    )

    key = _gemini_embedding_api_key()
    if not key:
        return {"ok": False, "skipped": True, "reason": "no_gemini_or_google_api_key"}

    model_name = os.environ.get("GEMINI_EMBEDDING_MODEL", "").strip() or _DEFAULT_GEMINI_EMBEDDING_MODEL
    task = (body.task_type or "RETRIEVAL_QUERY").strip().upper()
    if task not in ("RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT"):
        task = "RETRIEVAL_QUERY"

    try:
        from google import genai

        client = genai.Client(api_key=key)
        t0 = time.perf_counter()
        response = client.models.embed_content(
            model=model_name,
            contents=body.text,
            config={"task_type": task},
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if not response.embeddings:
            return {"ok": False, "provider": "google", "model": model_name, "error": "empty_embeddings"}
        vec = list(response.embeddings[0].values)
        return {
            "ok": True,
            "provider": "google",
            "model": model_name,
            "dimensions": len(vec),
            "task_type": task,
            "latency_ms": latency_ms,
        }
    except Exception as e:
        return {"ok": False, "provider": "google", "model": model_name, "error": str(e)[:500]}
