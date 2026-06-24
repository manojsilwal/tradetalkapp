"""Grounded bull/bear memo built on the brain's structured output.

The brain produces the numbers; this layer turns them into prose for the
swarm/debate surfaces. The deterministic memo is grounded by construction
(every number comes from the contract). ``enhance_memo`` optionally asks an LLM
for richer prose but only accepts it if ``agent_explainer.verify_grounding``
passes — otherwise it keeps the deterministic text. So the LLM can never invent
numbers (docs Rule 12 / AGENTS.md).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from . import adapters
from . import agent_explainer as ax

logger = logging.getLogger(__name__)


def _stance(verdict: str) -> str:
    if verdict in ("Strong Buy", "Buy"):
        return "bull"
    if verdict in ("Strong Sell", "Sell"):
        return "bear"
    return "neutral"


def build_memo(brain_result: Dict) -> Dict[str, Any]:
    """Deterministic, fully-grounded bull/bear memo from the brain result."""
    block = adapters._block(brain_result)
    drivers = block.get("drivers") or {}
    supporting = drivers.get("supporting") or []
    detracting = drivers.get("detracting") or []
    verdict = adapters.verdict_5(brain_result)
    p = adapters.outperform_probability(brain_result)
    p_pct = round(p * 100) if p is not None else None
    conf_pct = round((brain_result.get("confidence_score") or 0.0) * 100)

    bull_points = supporting or ["model probability is the primary signal"]
    bear_points = detracting or ["limited detracting signals identified"]
    summary = ax.generate_reflex_explanation(brain_result) if brain_result.get("base") \
        else ax.generate_explanation(block)

    arguments: List[Dict[str, Any]] = [
        {
            "agent_role": "bull",
            "stance": "bullish",
            "headline": f"Constructive case ({p_pct}% outperformance odds)" if p_pct is not None
                        else "Constructive case",
            "key_points": bull_points[:3],
            "confidence": conf_pct,
        },
        {
            "agent_role": "bear",
            "stance": "bearish",
            "headline": "Risks and detractors",
            "key_points": bear_points[:3],
            "confidence": conf_pct,
        },
    ]
    return {
        "verdict": verdict,
        "stance": _stance(verdict),
        "summary": summary,
        "bull_case": "; ".join(bull_points[:3]),
        "bear_case": "; ".join(bear_points[:3]),
        "arguments": arguments,
        "confidence_pct": conf_pct,
        "grounded": True,
    }


async def enhance_memo(brain_result: Dict, memo: Dict, llm_client: Any) -> Dict:
    """Optionally enrich the memo with LLM prose, kept only if grounded."""
    if llm_client is None:
        return memo
    try:
        block = adapters._block(brain_result)
        prompt = (
            "Write a 2-sentence bull case and a 2-sentence bear case for "
            f"{brain_result.get('ticker')} using ONLY these numbers: "
            f"probability={block.get('outperform_probability')}, "
            f"composite={block.get('composite_score')}, "
            f"risk={block.get('risk_score')}. Do not invent any other numbers."
        )
        resp = await llm_client.generate("brain_memo", prompt)
        text = resp.get("text") if isinstance(resp, dict) else str(resp)
        if not text:
            return memo
        check = ax.verify_grounding(text, brain_result)
        if check.get("grounded"):
            out = dict(memo)
            out["summary"] = text
            out["llm_grounded"] = True
            return out
        logger.info("[brain.memo] LLM memo rejected (ungrounded numbers: %s)",
                    check.get("ungrounded_numbers"))
    except Exception as e:  # noqa: BLE001
        logger.debug("[brain.memo] LLM enhancement skipped: %s", e)
    return memo
