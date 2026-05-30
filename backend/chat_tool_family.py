"""
Canonical chat tool taxonomy (Phase A0 + E0).

Single source of truth for the ``tool_family`` of every chat-assistant tool the
LLM can call from :mod:`backend.routers.chat`. Phase B evals and production
trajectory rows must resolve families through this module so the anti-shortcut
case bank cannot drift from the live tool surface.

If a chat tool is added or renamed without updating
:data:`CHAT_TOOL_FAMILY_BY_NAME`, :func:`find_orphan_tool_names` flags it and
:mod:`backend.tests.test_chat_tool_family` fails the build.

Phase E0 additions:
- ``ToolFamily`` extended with ``RISK``, ``BACKTEST``, ``MARKET_DATA`` for the
  new chat tools (E3, E4, E6) and the OHLCV pattern collection (E5).
- :class:`SkillName` / :class:`SkillTier` enumerate the user-facing skills the
  agent can perform; the :data:`SKILL_EXPECTED_FAMILIES` map encodes the
  minimum tool-family chain that satisfies each skill.
- :class:`StepPhase` formalises the investigation/synthesis split for Phase E2.
- :class:`MemoryNamespace` and the :data:`TOOL_NAMESPACE_BY_NAME` /
  :data:`TOOL_RETRIEVAL_MODE_BY_NAME` maps tag every chat tool with the memory
  partition it touches and the retrieval semantics it uses, so the trajectory
  schema can carry that metadata on every step.
- :data:`SOURCE_REF_ARTIFACT_TYPE_BY_TOOL` returns the canonical
  ``artifact_type`` for the typed ``source_refs_v2`` shape introduced in E0.
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable, Optional


class ToolFamily(str, Enum):
    """Canonical tool family for trajectory and eval analytics."""

    QUOTE = "quote"
    NEWS = "news"
    MACRO = "macro"
    RAG = "rag"
    TECHNICAL_ANALYSIS = "technical_analysis"
    SENTIMENT = "sentiment"
    FILING = "filing"
    CHART = "chart"
    WEB_SEARCH = "web_search"
    CALCULATOR = "calculator"
    PORTFOLIO = "portfolio"
    SCREENER = "screener"
    # Phase E0 — new families for the risk/backtest/pattern tools.
    RISK = "risk"
    BACKTEST = "backtest"
    MARKET_DATA = "market_data"
    UNKNOWN = "unknown"


class SkillName(str, Enum):
    """User-facing capability the agent is performing this turn (Phase E0)."""

    QUICK_QUOTE = "quick_quote"
    NEWS_CONTEXT = "news_context"
    MACRO_BRIEFING = "macro_briefing"
    FULL_CHAIN_ANALYSIS = "full_chain_analysis"
    TRADE_SETUP = "trade_setup"
    RISK_CHECK = "risk_check"
    WHAT_IF_BACKTEST = "what_if_backtest"
    DEEP_RESEARCH = "deep_research"
    UNKNOWN = "unknown"


class SkillTier(str, Enum):
    """Coarse complexity tier for a skill (drives Phase B chain assertions)."""

    SIMPLE = "simple"          # 1 tool family
    MEDIUM = "medium"          # 2 tool families
    FULL_CHAIN = "full_chain"  # 3+ tool families
    DEEP_RESEARCH = "deep_research"  # 4+ families incl. risk + backtest/pattern
    UNKNOWN = "unknown"


class StepPhase(str, Enum):
    """Phase boundary marker on a TrajectoryStep (Phase E2)."""

    INVESTIGATION = "investigation"
    SYNTHESIS = "synthesis"


class MemoryNamespace(str, Enum):
    """Memory partition a tool reads/writes against (Phase E0)."""

    MARKET_DATA = "market_data"      # OHLCV / pattern vectors → similarity
    NEWS_RAG = "news_rag"            # news / macro / filings → recency
    OUTCOME_STORE = "outcome_store"  # backtest / risk verdicts → outcome-indexed


# Mirrors the ``tool_handlers`` dict declared in
# ``backend/routers/chat.py::chat_send_message``. The corresponding test
# (test_chat_tool_family.py) keeps these two in sync — adding a new chat tool
# must also extend this map.
CHAT_TOOL_FAMILY_BY_NAME: dict[str, ToolFamily] = {
    "get_stock_quote": ToolFamily.QUOTE,
    "get_price_history": ToolFamily.CHART,
    "get_top_movers": ToolFamily.SCREENER,
    "get_market_news": ToolFamily.NEWS,
    "get_deep_news": ToolFamily.NEWS,
    "get_sec_filing": ToolFamily.FILING,
    "scrape_url": ToolFamily.WEB_SEARCH,
    "recall_financial_profile": ToolFamily.PORTFOLIO,
    "save_financial_preference": ToolFamily.PORTFOLIO,
    "get_risk_assessment": ToolFamily.RISK,
    "run_what_if_backtest": ToolFamily.BACKTEST,
    "find_similar_setups": ToolFamily.MARKET_DATA,
    # Super-agent context tools
    "get_portfolio_snapshot": ToolFamily.PORTFOLIO,
    "get_macro_regime": ToolFamily.MACRO,
    "get_macro_flow_summary": ToolFamily.MACRO,
}

EXPECTED_CHAT_TOOL_NAMES: frozenset[str] = frozenset(CHAT_TOOL_FAMILY_BY_NAME.keys())


# Skill → minimum tool-family chain (Phase E0). Phase B evals assert that a
# trajectory tagged with ``skill_name`` actually touched these families.
# Order in the list is informative only; the eval check is a set-min.
SKILL_EXPECTED_FAMILIES: dict[SkillName, list[ToolFamily]] = {
    SkillName.QUICK_QUOTE: [ToolFamily.QUOTE],
    SkillName.NEWS_CONTEXT: [ToolFamily.QUOTE, ToolFamily.NEWS],
    SkillName.MACRO_BRIEFING: [ToolFamily.MACRO, ToolFamily.NEWS],
    SkillName.FULL_CHAIN_ANALYSIS: [
        ToolFamily.QUOTE,
        ToolFamily.NEWS,
        ToolFamily.MACRO,
    ],
    SkillName.TRADE_SETUP: [
        ToolFamily.QUOTE,
        ToolFamily.TECHNICAL_ANALYSIS,
        ToolFamily.NEWS,
        ToolFamily.MACRO,
        ToolFamily.RISK,
    ],
    SkillName.RISK_CHECK: [
        ToolFamily.QUOTE,
        ToolFamily.TECHNICAL_ANALYSIS,
        ToolFamily.RISK,
    ],
    SkillName.WHAT_IF_BACKTEST: [
        ToolFamily.QUOTE,
        ToolFamily.TECHNICAL_ANALYSIS,
        ToolFamily.BACKTEST,
        ToolFamily.RISK,
    ],
    SkillName.DEEP_RESEARCH: [
        ToolFamily.QUOTE,
        ToolFamily.NEWS,
        ToolFamily.MACRO,
        ToolFamily.RAG,
        ToolFamily.TECHNICAL_ANALYSIS,
        ToolFamily.RISK,
    ],
    SkillName.UNKNOWN: [],
}


SKILL_TIER_BY_NAME: dict[SkillName, SkillTier] = {
    SkillName.QUICK_QUOTE: SkillTier.SIMPLE,
    SkillName.NEWS_CONTEXT: SkillTier.MEDIUM,
    SkillName.MACRO_BRIEFING: SkillTier.MEDIUM,
    SkillName.FULL_CHAIN_ANALYSIS: SkillTier.FULL_CHAIN,
    SkillName.TRADE_SETUP: SkillTier.FULL_CHAIN,
    SkillName.RISK_CHECK: SkillTier.FULL_CHAIN,
    SkillName.WHAT_IF_BACKTEST: SkillTier.DEEP_RESEARCH,
    SkillName.DEEP_RESEARCH: SkillTier.DEEP_RESEARCH,
    SkillName.UNKNOWN: SkillTier.UNKNOWN,
}


# Memory partition each chat tool reads from. Falls back to NEWS_RAG for
# unmapped tools so chats that touch unknown surfaces still get a coherent
# default (recency-weighted retrieval). Phase E3/E4/E6 extend this map when
# their tools register their handlers.
TOOL_NAMESPACE_BY_NAME: dict[str, MemoryNamespace] = {
    "get_stock_quote": MemoryNamespace.MARKET_DATA,
    "get_price_history": MemoryNamespace.MARKET_DATA,
    "get_top_movers": MemoryNamespace.MARKET_DATA,
    "get_market_news": MemoryNamespace.NEWS_RAG,
    "get_deep_news": MemoryNamespace.NEWS_RAG,
    "get_sec_filing": MemoryNamespace.NEWS_RAG,
    "scrape_url": MemoryNamespace.NEWS_RAG,
    "recall_financial_profile": MemoryNamespace.OUTCOME_STORE,
    "save_financial_preference": MemoryNamespace.OUTCOME_STORE,
    "get_risk_assessment": MemoryNamespace.MARKET_DATA,
    "run_what_if_backtest": MemoryNamespace.OUTCOME_STORE,
    "find_similar_setups": MemoryNamespace.MARKET_DATA,
    "get_portfolio_snapshot": MemoryNamespace.OUTCOME_STORE,
    "get_macro_regime": MemoryNamespace.NEWS_RAG,
    "get_macro_flow_summary": MemoryNamespace.NEWS_RAG,
}


# Retrieval mode metadata per tool — used by the C judges to reason about
# whether the agent picked the appropriate retrieval strategy for the skill.
# Phase E3/E4/E6 extend this map alongside their handlers.
TOOL_RETRIEVAL_MODE_BY_NAME: dict[str, str] = {
    "get_stock_quote": "live_fetch",
    "get_price_history": "recency_weighted",
    "get_top_movers": "live_fetch",
    "get_market_news": "recency_weighted",
    "get_deep_news": "recency_weighted",
    "get_sec_filing": "recency_weighted",
    "scrape_url": "live_fetch",
    "recall_financial_profile": "outcome_indexed",
    "save_financial_preference": "outcome_indexed",
    "get_risk_assessment": "live_fetch",
    "run_what_if_backtest": "outcome_indexed",
    "find_similar_setups": "similarity",
    "get_portfolio_snapshot": "outcome_indexed",
    "get_macro_regime": "live_fetch",
    "get_macro_flow_summary": "recency_weighted",
}


# Canonical ``artifact_type`` for the typed source-ref shape introduced in E0.
# Phase E3/E4/E6 extend this map alongside their handlers.
SOURCE_REF_ARTIFACT_TYPE_BY_TOOL: dict[str, str] = {
    "get_stock_quote": "market_quote",
    "get_price_history": "market_quote",
    "get_top_movers": "market_quote",
    "get_market_news": "news_article",
    "get_deep_news": "news_article",
    "get_sec_filing": "filing_excerpt",
    "scrape_url": "web_excerpt",
    "recall_financial_profile": "user_profile",
    "save_financial_preference": "user_profile",
    "get_risk_assessment": "risk_assessment",
    "run_what_if_backtest": "backtest_summary",
    "find_similar_setups": "pattern_match",
    "get_portfolio_snapshot": "user_profile",
    "get_macro_regime": "macro_data",
    "get_macro_flow_summary": "macro_data",
}


def get_tool_family(tool_name: str) -> ToolFamily:
    """Return the canonical family for a tool name (UNKNOWN if unmapped)."""
    if not tool_name:
        return ToolFamily.UNKNOWN
    return CHAT_TOOL_FAMILY_BY_NAME.get(str(tool_name), ToolFamily.UNKNOWN)


def find_orphan_tool_names(tool_names: Iterable[str]) -> list[str]:
    """Return any names not registered in :data:`CHAT_TOOL_FAMILY_BY_NAME`."""
    return [n for n in tool_names if n not in CHAT_TOOL_FAMILY_BY_NAME]


def get_tool_namespace(tool_name: str) -> MemoryNamespace:
    """Return the memory namespace a chat tool reads from (Phase E0)."""
    if not tool_name:
        return MemoryNamespace.NEWS_RAG
    return TOOL_NAMESPACE_BY_NAME.get(str(tool_name), MemoryNamespace.NEWS_RAG)


def get_tool_retrieval_mode(tool_name: str) -> str:
    """Return the retrieval-mode label for a chat tool (Phase E0)."""
    if not tool_name:
        return "live_fetch"
    return TOOL_RETRIEVAL_MODE_BY_NAME.get(str(tool_name), "live_fetch")


def get_artifact_type(tool_name: str) -> str:
    """Return the canonical ``artifact_type`` for a tool's source refs (E0)."""
    if not tool_name:
        return "unknown"
    fam = get_tool_family(str(tool_name)).value
    return SOURCE_REF_ARTIFACT_TYPE_BY_TOOL.get(str(tool_name), fam)


def expected_families_for_skill(skill_name: SkillName) -> list[str]:
    """Return the expected tool-family chain for a skill as plain strings."""
    fams = SKILL_EXPECTED_FAMILIES.get(skill_name, [])
    return [f.value for f in fams]


def tier_for_skill(skill_name: SkillName) -> SkillTier:
    """Return the tier associated with a skill (UNKNOWN for unmapped names)."""
    return SKILL_TIER_BY_NAME.get(skill_name, SkillTier.UNKNOWN)


def make_source_refs(tool_name: str, arguments: Optional[dict] = None) -> list[str]:
    """
    Build a small, deterministic list of ``source_refs`` for one tool call.

    These refs are used by Phase B/C anti-shortcut checks and by the C2
    grounding judge to verify that the final answer cites at least one piece of
    evidence the agent actually retrieved. The format is intentionally compact
    (``family:key[:qualifier]``) and never embeds free-form user content.
    """
    fam = get_tool_family(tool_name).value
    args = arguments or {}

    if tool_name == "get_stock_quote":
        t = str(args.get("ticker", "") or "").upper().strip()
        return [f"{fam}:{t}"] if t else [fam]
    if tool_name == "get_price_history":
        t = str(args.get("ticker", "") or "").upper().strip()
        p = str(args.get("period", "1y") or "1y").strip()
        return [f"{fam}:{t}:{p}"] if t else [f"{fam}:{p}"]
    if tool_name == "get_top_movers":
        d = str(args.get("direction", "movers") or "movers").strip()
        return [f"{fam}:{d}"]
    if tool_name == "get_market_news":
        q = str(args.get("query") or "market").strip()
        return [f"{fam}:{q[:40]}"]
    if tool_name == "get_deep_news":
        t = str(args.get("ticker", "") or "").upper().strip()
        return [f"{fam}:{t}"] if t else [fam]
    if tool_name == "get_sec_filing":
        t = str(args.get("ticker", "") or "").upper().strip()
        f = str(args.get("form", "10-K") or "10-K").strip()
        return [f"{fam}:{t}:{f}"] if t else [f"{fam}:{f}"]
    if tool_name == "scrape_url":
        url = str(args.get("url", "") or "").strip()
        if not url:
            return [fam]
        try:
            from urllib.parse import urlparse

            host = urlparse(url).hostname or url
        except Exception:
            host = url
        return [f"{fam}:{host[:60]}"]
    if tool_name == "recall_financial_profile":
        return [f"{fam}:profile"]
    if tool_name == "save_financial_preference":
        k = str(args.get("key", "") or "").strip()
        return [f"{fam}:write:{k}"] if k else [f"{fam}:write"]
    if tool_name == "get_risk_assessment":
        t = str(args.get("ticker", "") or "").upper().strip()
        return [f"{fam}:{t}"] if t else [fam]
    if tool_name == "run_what_if_backtest":
        pid = str(args.get("preset_id", "") or "").strip()
        return [f"{fam}:{pid}"] if pid else [fam]
    if tool_name == "find_similar_setups":
        t = str(args.get("ticker", "") or "").upper().strip()
        lb = str(args.get("lookback_bars", "") or "").strip()
        if t and lb:
            return [f"{fam}:{t}:{lb}"]
        if t:
            return [f"{fam}:{t}"]
        return [fam]
    # Phase E3/E4/E6 registers source-ref builders for ``get_risk_assessment``,
    # ``run_what_if_backtest`` and ``find_similar_setups`` alongside their
    # handlers; until then those tool_names fall through to the family
    # default below.

    return [fam]


def make_source_refs_v2(
    tool_name: str, arguments: Optional[dict] = None
) -> list[dict]:
    """
    Phase E0 — typed source-ref objects.

    Each entry pairs the compact ``ref_id`` produced by :func:`make_source_refs`
    with the source family and the canonical ``artifact_type`` for the tool
    (e.g. ``backtest_summary``, ``pattern_match``). Phase B/C eval checks and
    the offline answer-quality judge can reason over these without re-parsing
    the flat ``family:key`` strings.
    """
    refs = make_source_refs(tool_name, arguments)
    family = get_tool_family(tool_name).value
    artifact = get_artifact_type(tool_name)
    out: list[dict] = []
    for ref in refs:
        out.append(
            {
                "ref_id": ref,
                "source_family": family,
                "artifact_type": artifact,
            }
        )
    return out
