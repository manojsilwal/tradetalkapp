"""
Agent Policy Guardrails — In-Process Defense-in-Depth

Lightweight policy layer enforcing per-workload capability checks, outbound
host allowlists, and startup secret validation.

IMPORTANT: This is an IN-PROCESS defense layer.  It does NOT provide
OS/container-level isolation.  A compromised process can bypass these
checks.  When NVIDIA OpenShell exits alpha, migrate to real out-of-process
enforcement for production-grade sandboxing.
"""
from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Set
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class PolicyBlockedError(RuntimeError):
    """Raised when a workload attempts an action outside its policy."""


@dataclass(frozen=True)
class SandboxProfile:
    workload: str
    capabilities: frozenset[str]
    allowed_hosts: frozenset[str]
    filesystem_mode: str  # readonly | readwrite


def _csv_set(value: str) -> Set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}


DEFAULT_ALLOWED_HOSTS = frozenset(
    {
        "openrouter.ai",
        "api.openai.com",
        "api.nvidia.com",
        "integrate.api.nvidia.com",
        "query1.finance.yahoo.com",
        "query2.finance.yahoo.com",
        "api.polygon.io",
        "api.alpaca.markets",
        "newsapi.org",
        "www.alphavantage.co",
        "fred.stlouisfed.org",
        "www.googleapis.com",
        "youtube.googleapis.com",
    }
)


def _hosts_from_env() -> frozenset[str]:
    extra = _csv_set(os.environ.get("GUARDRAILS_ALLOWED_HOSTS", ""))
    return frozenset(set(DEFAULT_ALLOWED_HOSTS).union(extra))


def _profile_map() -> Dict[str, SandboxProfile]:
    hosts = _hosts_from_env()
    llm_hosts = frozenset(
        h
        for h in hosts
        if h in {"openrouter.ai", "api.openai.com", "api.nvidia.com", "integrate.api.nvidia.com"}
    )
    market_hosts = frozenset(
        h
        for h in hosts
        if h
        in {
            "query1.finance.yahoo.com",
            "query2.finance.yahoo.com",
            "api.polygon.io",
            "api.alpaca.markets",
            "fred.stlouisfed.org",
            "newsapi.org",
            "www.alphavantage.co",
            "www.googleapis.com",
            "youtube.googleapis.com",
        }
    )
    readwrite = "readwrite"
    readonly = "readonly"
    return {
        "debate": SandboxProfile(
            workload="debate",
            capabilities=frozenset({"knowledge_read", "knowledge_write", "llm_inference"}),
            allowed_hosts=frozenset(set(llm_hosts).union(market_hosts)),
            filesystem_mode=readonly,
        ),
        "backtest": SandboxProfile(
            workload="backtest",
            capabilities=frozenset({"knowledge_read", "knowledge_write", "llm_inference", "market_data_read"}),
            allowed_hosts=frozenset(set(llm_hosts).union(market_hosts)),
            filesystem_mode=readwrite,
        ),
        "notifications": SandboxProfile(
            workload="notifications",
            capabilities=frozenset({"news_ingest", "knowledge_write", "notifications_emit"}),
            allowed_hosts=market_hosts,
            filesystem_mode=readwrite,
        ),
        "scheduler": SandboxProfile(
            workload="scheduler",
            capabilities=frozenset({"news_ingest", "market_data_read", "knowledge_write"}),
            allowed_hosts=market_hosts,
            filesystem_mode=readwrite,
        ),
        "video": SandboxProfile(
            workload="video",
            capabilities=frozenset({"llm_inference", "video_generation"}),
            allowed_hosts=frozenset(set(llm_hosts).union({"www.googleapis.com"})),
            filesystem_mode=readwrite,
        ),
        "llm": SandboxProfile(
            workload="llm",
            capabilities=frozenset({"llm_inference"}),
            allowed_hosts=llm_hosts,
            filesystem_mode=readonly,
        ),
    }


def resolve_sandbox_profile(workload: str) -> SandboxProfile:
    """Resolve a workload class into a strict least-privilege profile."""
    profiles = _profile_map()
    if workload in profiles:
        return profiles[workload]
    return SandboxProfile(
        workload=workload,
        capabilities=frozenset({"knowledge_read"}),
        allowed_hosts=_hosts_from_env(),
        filesystem_mode="readonly",
    )


def ensure_capability(workload: str, capability: str) -> None:
    """Block execution when a workload requests a non-whitelisted capability."""
    profile = resolve_sandbox_profile(workload)
    if capability not in profile.capabilities:
        raise PolicyBlockedError(
            f"[PolicyGuardrail] capability blocked workload={workload} capability={capability} "
            f"allowed={sorted(profile.capabilities)}"
        )


def guard_host(workload: str, url: str) -> None:
    """Validate outbound host access against workload policy."""
    profile = resolve_sandbox_profile(workload)
    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise PolicyBlockedError(f"[PolicyGuardrail] invalid host for workload={workload}: {url}")
    allowed = any(host == h or host.endswith(f".{h}") for h in profile.allowed_hosts)
    if not allowed:
        raise PolicyBlockedError(
            f"[PolicyGuardrail] host blocked workload={workload} host={host} "
            f"allowed={sorted(profile.allowed_hosts)}"
        )


def redact_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return ("*" * (len(value) - keep)) + value[-keep:]


def redact_secrets_in_text(text: str, secret_values: Optional[Iterable[str]] = None) -> str:
    """Replace known secret values in logs/errors with redacted placeholders."""
    if not text:
        return text
    redacted = text
    values = list(secret_values or [])
    values.extend(
        [
            os.environ.get("OPENROUTER_API_KEY", ""),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            os.environ.get("GOOGLE_API_KEY", ""),
            os.environ.get("GEMINI_API_KEY", ""),
        ]
    )
    for value in values:
        if value:
            redacted = redacted.replace(value, redact_secret(value))
    return redacted


def validate_startup_secrets() -> list[str]:
    """Return startup validation issues. Caller decides strictness."""
    issues: list[str] = []

    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not openrouter_key:
        issues.append("OPENROUTER_API_KEY is not set — LLM inference will use rule-based fallback.")

    vector_backend = os.environ.get("VECTOR_BACKEND", "chroma").strip().lower()
    if vector_backend == "supabase" and not os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip():
        issues.append("VECTOR_BACKEND=supabase requires SUPABASE_SERVICE_ROLE_KEY.")

    return issues


@contextlib.contextmanager
def workload_scope(workload: str, capability: Optional[str] = None):
    """
    Scope helper for explicit policy checks around sensitive operations.
    """
    if capability:
        ensure_capability(workload, capability)
    try:
        yield
    except PolicyBlockedError:
        raise
    except Exception as exc:
        safe_message = redact_secrets_in_text(str(exc))
        logger.warning("[PolicyGuardrail] workload=%s error=%s", workload, safe_message)
        raise


def is_enabled() -> bool:
    return os.environ.get("GUARDRAILS_ENABLE", "1").strip() != "0"
