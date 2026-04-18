"""Debug and observability endpoints — cache, query router, LLM status, policy check."""
from fastapi import APIRouter, Query

from ..agent_policy_guardrails import (
    PolicyBlockedError, ensure_capability, is_enabled as guardrails_enabled, validate_startup_secrets,
)
from ..deps import knowledge_store, llm_client

router = APIRouter(tags=["debug"])


@router.get("/llm/status")
async def llm_status():
    """Show which LLM backend, model tiers, and routing all agents use."""
    from ..gemini_llm import GEMINI_FALLBACK_MODEL, gemini_llm_fallback_enabled
    from ..llm_client import RAG_TOP_K_DEFAULT, MODEL_TIER, OPENROUTER_MODEL_LIGHT, _model_for_role
    backend = llm_client.backend
    ks_stats = knowledge_store.stats()
    role_models = {role: _model_for_role(role) for role in MODEL_TIER}
    return {
        "backend": backend,
        "provider": getattr(llm_client, "provider", backend),
        "model_heavy": llm_client.model if backend == "openrouter" else "rule-based",
        "model_light": OPENROUTER_MODEL_LIGHT if backend == "openrouter" else "rule-based",
        "endpoint": llm_client.endpoint if backend == "openrouter" else None,
        "guardrails_enabled": guardrails_enabled(),
        "vector_backend": ks_stats.get("vector_backend", "chroma"),
        "rag_top_k_default": RAG_TOP_K_DEFAULT,
        "role_model_mapping": role_models,
        "note": "Roles use heavy or light model tier based on reasoning complexity.",
        "gemini_fallback_enabled": gemini_llm_fallback_enabled(),
        "gemini_fallback_model": GEMINI_FALLBACK_MODEL if gemini_llm_fallback_enabled() else None,
    }


@router.get("/runtime/policy-check")
async def runtime_policy_check():
    """Agent policy guardrails self-test."""
    issues = validate_startup_secrets()
    blocked = False
    blocked_reason = ""
    try:
        ensure_capability("debate", "notifications_emit")
    except PolicyBlockedError as e:
        blocked = True
        blocked_reason = str(e)
    return {
        "guardrails_enabled": guardrails_enabled(),
        "policy_block_check": "pass" if blocked else "fail",
        "policy_block_reason": blocked_reason,
        "startup_secret_issues": issues,
    }


@router.get("/cache/stats")
async def cache_stats_endpoint():
    """Returns L1 in-memory tool-call cache hit/miss stats."""
    from ..cache import cache_stats
    return cache_stats()


@router.delete("/cache/flush")
async def cache_flush_endpoint(tool_name: str = None):
    """Flush the L1 tool cache (optionally filter by tool name)."""
    from ..cache import invalidate
    removed = invalidate(tool_name or None)
    return {"flushed": removed, "tool_name": tool_name}


@router.get("/query/route")
async def query_route_endpoint(q: str = Query(..., description="User query to classify")):
    """Debug endpoint: classify a query."""
    from ..query_router import route_query_detail
    return route_query_detail(q)
