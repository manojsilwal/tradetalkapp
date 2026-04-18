"""
Pure numeric handlers for Phase C1 tier-0 TOOL resources.

Every function here is a DETERMINISTIC, SIDE-EFFECT-FREE implementation of a
tool's decision rule. They are called from two places:

    1. Live production path — ``agents.py`` and ``debate_agents.py`` pass the
       registry-resolved config to these helpers. This keeps the live rules
       byte-exactly reproducible outside of I/O.

    2. Shadow-mode evaluator — ``sepl_tool.py`` loads fixtures of the shape
       ``{"input": {...}, "expected": <int|str>, "weight": <float>}`` and runs
       both the active config and a candidate config through the same handler
       to compute a margin. No live traffic is touched.

Contract:
    * Handlers NEVER perform I/O, sleep, log, or raise for bad input — they
      treat missing keys as zero/None and return a well-defined default
      (``0`` for numeric signals, ``"NEUTRAL"`` for stance).
    * Handlers do NOT read env vars, the registry, or global state. All
      knobs come through the ``cfg`` dict argument.
    * Return values are primitives so fixtures can serialize them as JSON.

These invariants are tested in ``tests/test_tool_handlers.py``.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


# ── short_interest_classifier ────────────────────────────────────────────────


def classify_short_interest(
    data: Dict[str, Any],
    cfg: Dict[str, float],
    *,
    revision: bool = False,
) -> int:
    """Compute the swarm-analyst signal for the short-interest factor.

    Parameters
    ----------
    data:
        Observed market data. Required keys: ``short_interest_ratio``,
        ``days_to_cover``. Missing keys default to 0.
    cfg:
        Live config; must contain ``sir_bull_threshold``,
        ``sir_ambiguous_min``, ``sir_ambiguous_max``, ``dtc_confirm_threshold``.
    revision:
        ``False`` = first analyst iteration (can return -1 meaning "LLM-needed");
        ``True``  = revised iteration (always returns 0 or 1).

    Returns
    -------
    int
        ``1``  — bullish (short-squeeze setup)
        ``0``  — not bullish
        ``-1`` — ambiguous, only emitted when ``revision=False``; signals to
                 the caller that an LLM-assisted decision is warranted.
    """
    sir = float(data.get("short_interest_ratio", 0) or 0)
    dtc = float(data.get("days_to_cover", 0) or 0)
    sir_bull = float(cfg["sir_bull_threshold"])
    sir_amb_lo = float(cfg["sir_ambiguous_min"])
    sir_amb_hi = float(cfg["sir_ambiguous_max"])
    dtc_confirm = float(cfg["dtc_confirm_threshold"])

    if revision:
        return 1 if (sir > sir_bull and dtc > dtc_confirm) else 0

    if sir > sir_bull:
        return 1
    if sir_amb_lo <= sir <= sir_amb_hi:
        return -1
    return 0


def verify_short_interest(
    market_state: Dict[str, Any], cfg: Dict[str, float]
) -> bool:
    """Return True if the QA verifier accepts a bullish short-interest signal
    given the current macro state. Pure mirror of the CSI clause in
    ``ShortInterestAgentPair._qa_verifier_step``."""
    csi = float(market_state.get("credit_stress_index", 1.0) or 1.0)
    return csi <= float(cfg["bearish_csi_threshold"])


# ── debate_stance_heuristic_bull ─────────────────────────────────────────────


def decide_debate_bull_stance(
    data: Dict[str, Any], cfg: Dict[str, float]
) -> str:
    """Return ``"BULLISH" | "BEARISH" | "NEUTRAL"`` for the debate bull agent.

    Mirrors the ``role == "bull"`` branch of
    ``backend/debate_agents.py::_determine_stance``.
    """
    sir = float(data.get("short_interest_ratio", 0) or 0)
    rev = float(data.get("revenue_growth", 0) or 0)
    r3m = float(data.get("price_return_3m", 0) or 0)
    if (sir > cfg["sir_bull_floor"]
            or rev > cfg["rev_growth_bull_floor"]
            or r3m > cfg["r3m_bull_floor"]):
        return "BULLISH"
    if (sir < cfg["sir_bear_ceiling"]
            and rev < cfg["rev_growth_bear_ceiling"]
            and r3m < cfg["r3m_bear_ceiling"]):
        return "BEARISH"
    return "NEUTRAL"


# ── debate_stance_heuristic_bear ─────────────────────────────────────────────


def decide_debate_bear_stance(
    data: Dict[str, Any], cfg: Dict[str, float]
) -> str:
    """Return ``"BULLISH" | "BEARISH" | "NEUTRAL"`` for the debate bear agent.

    Mirrors the ``role == "bear"`` branch of
    ``backend/debate_agents.py::_determine_stance``.
    """
    pe = data.get("pe_ratio") or 0
    debt_eq = data.get("debt_to_equity") or 0
    r3m = float(data.get("price_return_3m", 0) or 0)
    pe_f = float(pe)
    debt_eq_f = float(debt_eq)
    if ((pe_f and pe_f > cfg["pe_bear_threshold"])
            or (debt_eq_f and debt_eq_f > cfg["debt_eq_bear_threshold"])
            or r3m < cfg["r3m_bear_ceiling"]):
        return "BEARISH"
    if pe_f and pe_f < cfg["pe_bull_ceiling"] and r3m > cfg["r3m_bull_floor"]:
        return "BULLISH"
    return "NEUTRAL"


# ── macro_vix_to_credit_stress ───────────────────────────────────────────────


def vix_to_credit_stress_status(
    data: Dict[str, Any], cfg: Dict[str, float]
) -> str:
    """Classify the macro regime from a raw VIX level.

    Returns
    -------
    ``"STRESS"`` when ``vix_level / divisor > status_threshold``, else
    ``"NORMAL"``. Returns ``"INVALID"`` for non-positive ``divisor`` —
    this is what the SEPL evaluator will see when a candidate is malformed,
    and it will simply score as a miss without crashing.

    This is the scoreable half of the tool. The numeric
    ``credit_stress_index`` value is computed directly in
    ``backend/connectors/macro.py`` using the same cfg — the status is what
    downstream agents gate on, so that's what we shadow-score on.
    """
    vix = float(data.get("vix_level", 0) or 0)
    try:
        divisor = float(cfg["divisor"])
        threshold = float(cfg["status_threshold"])
    except (KeyError, TypeError, ValueError):
        return "INVALID"
    if divisor <= 0:
        return "INVALID"
    csi = vix / divisor
    return "STRESS" if csi > threshold else "NORMAL"


def vix_to_credit_stress_value(
    data: Dict[str, Any], cfg: Dict[str, float]
) -> float:
    """Compute the numeric ``credit_stress_index`` for the production path.

    Kept separate from the status classifier so SEPL can evaluate the
    downstream decision on discrete labels (which admits a clean hit-rate
    margin). Production code wants the float too, so it calls this helper.
    """
    vix = float(data.get("vix_level", 0) or 0)
    divisor = float(cfg.get("divisor", 15.0)) or 15.0
    if divisor <= 0:
        divisor = 15.0
    return round(vix / divisor, 2)


# ── Registry lookup table for SEPL shadow evaluator ──────────────────────────


TOOL_HANDLERS: Dict[str, Dict[str, Any]] = {
    "short_interest_classifier": {
        # The shadow evaluator uses the non-revision path because that's the
        # canonical decision boundary (the revision path is a tightening).
        "fn": lambda data, cfg: classify_short_interest(data, cfg, revision=False),
        "output_kind": "int",  # fixture.expected should be one of {-1, 0, 1}
    },
    "debate_stance_heuristic_bull": {
        "fn": decide_debate_bull_stance,
        "output_kind": "str",
    },
    "debate_stance_heuristic_bear": {
        "fn": decide_debate_bear_stance,
        "output_kind": "str",
    },
    "macro_vix_to_credit_stress": {
        "fn": vix_to_credit_stress_status,
        "output_kind": "str",  # "STRESS" | "NORMAL" | "INVALID"
    },
}


__all__ = [
    "classify_short_interest",
    "verify_short_interest",
    "decide_debate_bull_stance",
    "decide_debate_bear_stance",
    "vix_to_credit_stress_status",
    "vix_to_credit_stress_value",
    "TOOL_HANDLERS",
]
