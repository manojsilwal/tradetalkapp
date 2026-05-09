"""
Phase E1 — heuristic chat-skill classifier.

The classifier looks at (a) the user's message, and (b) the tool-family set
the agent actually touched, and returns a (:class:`SkillName`,
:class:`SkillTier`) pair that downstream telemetry surfaces (Phase E0
trajectory rows, Phase A2 evidence contract) attach to the turn.

The implementation is intentionally deterministic so unit tests stay
hermetic and the LLM hot path is never blocked. A future LLM upgrade can be
wired behind a feature flag without changing callers.

Decision priority:

  1. Strong keyword cues in the user message (e.g. "backtest" → WHAT_IF_BACKTEST,
     "risk" / "stop" → RISK_CHECK, "compare" / "vs." → DEEP_RESEARCH,
     "similar setup" / "historical" → DEEP_RESEARCH).
  2. Tool-family chain that actually ran (e.g. {RISK, BACKTEST} → WHAT_IF_BACKTEST;
     {QUOTE, NEWS, MACRO} → FULL_CHAIN_ANALYSIS; {QUOTE, NEWS} → NEWS_CONTEXT).
  3. Fallback to QUICK_QUOTE / SIMPLE when only the quote family was used,
     and UNKNOWN / SIMPLE otherwise.

The tier is derived from :data:`backend.chat_tool_family.SKILL_TIER_BY_NAME`
once a skill is chosen, so callers always receive a coherent (skill, tier)
pair.
"""
from __future__ import annotations

import re
from typing import Iterable

from .chat_tool_family import (
    SKILL_TIER_BY_NAME,
    SkillName,
    SkillTier,
    ToolFamily,
)

# Keyword → SkillName (checked in priority order). Each entry is a compiled
# regex so we can match word-boundaried tokens without false positives.
_KEYWORD_PRIORITY: list[tuple[re.Pattern[str], SkillName]] = [
    (re.compile(r"\bback[\s-]?test(s|ed|ing)?\b", re.IGNORECASE), SkillName.WHAT_IF_BACKTEST),
    (re.compile(r"\bwhat[-\s]?if\b", re.IGNORECASE), SkillName.WHAT_IF_BACKTEST),
    (
        re.compile(
            r"\b(similar setup|historical setup|pattern match|like this before)\b",
            re.IGNORECASE,
        ),
        SkillName.DEEP_RESEARCH,
    ),
    (re.compile(r"\b(compare|vs\.?|versus)\b", re.IGNORECASE), SkillName.DEEP_RESEARCH),
    (re.compile(r"\bdeep research\b", re.IGNORECASE), SkillName.DEEP_RESEARCH),
    (
        re.compile(
            r"\b(risk|stop[-\s]?loss|position sizing|drawdown|volatility check)\b",
            re.IGNORECASE,
        ),
        SkillName.RISK_CHECK,
    ),
    (
        re.compile(
            r"\b(trade setup|build a trade|trade idea|entry plan|long\s+\w+\s+here|short\s+\w+\s+here)\b",
            re.IGNORECASE,
        ),
        SkillName.TRADE_SETUP,
    ),
    (
        re.compile(
            r"\b(macro|fed|fomc|cpi|nfp|jobs report|rates|yields|dxy|10y|bonds)\b",
            re.IGNORECASE,
        ),
        SkillName.MACRO_BRIEFING,
    ),
    (
        re.compile(
            r"\b(news|headline|reported|press release|earnings|guidance)\b",
            re.IGNORECASE,
        ),
        SkillName.NEWS_CONTEXT,
    ),
    (
        re.compile(
            r"\b(price|quote|trading at|last close|change|movers?)\b",
            re.IGNORECASE,
        ),
        SkillName.QUICK_QUOTE,
    ),
]


def _families_to_set(
    tool_families_used: Iterable[str | ToolFamily],
) -> set[str]:
    out: set[str] = set()
    for f in tool_families_used or []:
        if isinstance(f, ToolFamily):
            out.add(f.value)
        elif f:
            out.add(str(f))
    return out


def _classify_by_keywords(user_message: str) -> SkillName | None:
    msg = (user_message or "").strip()
    if not msg:
        return None
    for pattern, skill in _KEYWORD_PRIORITY:
        if pattern.search(msg):
            return skill
    return None


def _classify_by_family_set(families: set[str]) -> SkillName:
    """Map the family set the agent actually touched to a coherent skill."""
    if not families:
        return SkillName.UNKNOWN

    has_quote = ToolFamily.QUOTE.value in families
    has_news = ToolFamily.NEWS.value in families
    has_macro = ToolFamily.MACRO.value in families
    has_risk = ToolFamily.RISK.value in families
    has_backtest = ToolFamily.BACKTEST.value in families
    has_pattern = ToolFamily.MARKET_DATA.value in families
    has_technical = ToolFamily.TECHNICAL_ANALYSIS.value in families
    has_filing = ToolFamily.FILING.value in families
    has_rag = ToolFamily.RAG.value in families
    has_chart = ToolFamily.CHART.value in families

    # Deep research takes precedence: backtest + risk together, or 4+ family
    # mix that includes a knowledge-store retrieval.
    if has_backtest and has_risk:
        return SkillName.WHAT_IF_BACKTEST
    if has_pattern and (has_risk or has_technical or has_news):
        return SkillName.DEEP_RESEARCH
    if (
        len({f for f in families if f != ToolFamily.UNKNOWN.value}) >= 4
        and (has_rag or has_filing)
    ):
        return SkillName.DEEP_RESEARCH

    if has_risk and (has_quote or has_chart or has_technical):
        return SkillName.RISK_CHECK

    if has_quote and (has_news or has_filing) and has_macro:
        return SkillName.FULL_CHAIN_ANALYSIS

    if has_macro and has_news:
        return SkillName.MACRO_BRIEFING

    if has_quote and (has_news or has_filing):
        return SkillName.NEWS_CONTEXT

    if has_quote or has_chart:
        return SkillName.QUICK_QUOTE

    return SkillName.UNKNOWN


def classify_skill(
    *,
    user_message: str,
    tool_families_used: Iterable[str | ToolFamily],
) -> tuple[SkillName, SkillTier]:
    """
    Return a deterministic ``(skill_name, skill_tier)`` pair for one chat turn.

    Keyword cues from the user message win when present (e.g. an explicit
    "backtest" question). Otherwise the family set the agent touched decides.
    Empty inputs return ``(UNKNOWN, SIMPLE)`` so the trajectory still has
    coherent metadata.
    """
    families = _families_to_set(tool_families_used)
    skill = _classify_by_keywords(user_message)

    if skill is None:
        skill = _classify_by_family_set(families)
    else:
        # Keyword cue picked a skill — sanity-check against family evidence.
        # If the LLM never actually ran backtest tools but the user *asked*
        # about backtests, we still return WHAT_IF_BACKTEST so eval cases can
        # detect skill detection independent of execution success.
        pass

    tier = SKILL_TIER_BY_NAME.get(skill, SkillTier.UNKNOWN)
    if tier == SkillTier.UNKNOWN:
        tier = SkillTier.SIMPLE
    return skill, tier
