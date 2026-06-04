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


@router.get("/harness/status")
async def harness_status_endpoint(session_id: str = "default"):
    """Continual harness operator snapshot."""
    from datetime import datetime, timezone

    from ..harness.config import harness_config_from_env
    from ..harness.loop import get_session_loop, harness_enabled

    if not harness_enabled():
        return {"enabled": False}
    cfg = harness_config_from_env()
    loop = get_session_loop(session_id)
    state = loop.manager.get_current_state()
    failures = loop.detector.run()
    return {
        "enabled": True,
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "config": {
            "mutation_enable": cfg.mutation_enable,
            "observe_only": cfg.observe_only,
            "model_tier": cfg.model_tier,
        },
        "state": {
            "version": state.version,
            "refinement_cycle_count": state.refinement_cycle_count,
            "rollback_count": state.rollback_count,
            "refinement_frozen": state.refinement_frozen,
            "prompts": len(state.system_prompts),
            "skills": len(state.skills),
            "sub_agents_active": sum(1 for s in state.sub_agents if s.is_active),
        },
        "trajectory_events_in_window": len(loop.buffer.get_window()),
        "failure_signatures": [
            {"id": f.signature_id, "severity": f.severity} for f in failures
        ],
        "recent_cycles": loop.changelog.get_cycle_history(session_id, last_n=5),
    }


@router.get("/learning-health")
async def learning_health_endpoint():
    """
    Operator snapshot: ledger volume, graded coverage, SEPL flags, reflection source.
    """
    import os
    from datetime import datetime, timezone

    from .. import decision_ledger as dl
    from ..sepl import sepl_dry_run, sepl_enabled, sepl_reflection_source_mode

    ledger_enabled = os.environ.get("DECISION_LEDGER_ENABLE", "1").strip() not in (
        "0",
        "false",
        "no",
    )
    stats: dict = {}
    graded_decisions = 0
    decisions_with_prompt_versions = 0
    try:
        backend = dl.get_ledger()
        stats = backend.stats() if hasattr(backend, "stats") else {}
        conn = getattr(backend, "_conn", lambda: None)()
        if conn is not None:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT d.decision_id) AS n
                FROM decision_events d
                INNER JOIN outcome_observations o ON o.decision_id = d.decision_id
                """
            ).fetchone()
            graded_decisions = int(row["n"] if row else 0)
            row2 = conn.execute(
                """
                SELECT COUNT(*) AS n FROM decision_events
                WHERE prompt_versions_json IS NOT NULL
                  AND TRIM(prompt_versions_json) NOT IN ('', '{}')
                """
            ).fetchone()
            decisions_with_prompt_versions = int(row2["n"] if row2 else 0)
    except Exception:
        pass

    pipeline_status: dict = {}
    try:
        pipeline_status = (knowledge_store.stats() or {}).get("pipeline_status") or {}
    except Exception:
        pipeline_status = {}

    total_decisions = int(stats.get("decision_events", 0) or 0)
    graded_pct = (
        round(100.0 * graded_decisions / total_decisions, 2)
        if total_decisions > 0
        else 0.0
    )

    return {
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "ledger": {
            "enabled": ledger_enabled,
            "backend": getattr(dl.get_ledger(), "name", "unknown"),
            "table_counts": stats,
            "graded_decisions": graded_decisions,
            "graded_pct": graded_pct,
            "decisions_with_prompt_versions": decisions_with_prompt_versions,
        },
        "sepl": {
            "enabled": sepl_enabled(),
            "dry_run_default": sepl_dry_run(),
            "reflection_source": sepl_reflection_source_mode(),
            "autocommit": os.environ.get("SEPL_AUTOCOMMIT", "0").strip() == "1",
        },
        "pipeline": {
            "last_run": pipeline_status.get("last_run"),
            "swarm_outcomes_tracked": pipeline_status.get("swarm_outcomes_tracked"),
            "errors": pipeline_status.get("errors"),
        },
    }


@router.get("/llm/calls")
async def get_llm_calls(limit: int = Query(100, description="Max calls to return")):
    """Get the history of recent LLM API calls."""
    from ..decision_ledger import get_ledger
    return get_ledger().list_llm_calls(limit=limit)
