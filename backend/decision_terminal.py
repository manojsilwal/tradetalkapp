"""
Assemble the K2 Investor Decision Terminal view-model from swarm + debate analysis,
market data, and optional LLM scenario prices. See DecisionTerminalPayload in schemas.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .metric_primitives import (
    format_usd_compact,
    normalize_gross_margin,
    roic_proxy,
)
from .connectors.polymarket_gating import (
    company_tokens_from_name,
    score_polymarket_relevance,
    select_gated_polymarket_event,
)
from .metric_reconciliation import build_reconciliation
from .schemas import (
    BrainVerdict,
    DebateResult,
    DecisionRoadmapPayload,
    DecisionSnapshotPayload,
    DecisionTerminalPayload,
    DecisionVerdictPayload,
    HorizonQuantileBand,
    SpotEnvelope,
    SwarmConsensus,
    TerminalFieldProvenance,
    TerminalQualityPanel,
    TerminalQualityRow,
    TerminalRoadmapPanel,
    TerminalScorecardSummary,
    TerminalValuationModel,
    TerminalValuationPanel,
    TerminalVerdictPanel,
    VerificationStatus,
)

logger = logging.getLogger(__name__)

# JSON (and Pydantic JSON mode) cannot encode float NaN/Inf — yfinance/heuristics sometimes yield them.
def _strip_non_json_floats(obj: Any) -> Any:
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _strip_non_json_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_non_json_floats(v) for v in obj]
    return obj


def _decision_terminal_payload_json_safe(payload: DecisionTerminalPayload) -> DecisionTerminalPayload:
    """Round-trip through dict so NaN/Inf become None; restore required roadmap confidence."""
    data = _strip_non_json_floats(payload.model_dump(mode="python"))
    rm = data.get("roadmap") or {}
    if rm.get("confidence_0_1") is None:
        rm["confidence_0_1"] = 0.0
        data["roadmap"] = rm
    return DecisionTerminalPayload.model_validate(data)


DISCLAIMER = (
    "Illustrative analysis only. Figures use third-party snapshots, heuristics, and AI "
    "synthesis — not audited models or investment advice."
)


def _terminal_data_freshness(spot_price_source, market_data_degraded, generated_at_utc):
    """Best-effort spot-price freshness envelope for the Decision Terminal.

    Folds the existing degraded flag + spot source into the shared DataFreshness
    contract (the spot is fetched live at request time).
    """
    try:
        from .freshness import assess_spot

        return assess_spot(
            source=str(spot_price_source or "yfinance"),
            captured_at=generated_at_utc,
            degraded=bool(market_data_degraded),
        )
    except Exception:
        return None

def _company_tokens_from_debate_data(dd: dict) -> List[str]:
    return company_tokens_from_name(str(dd.get("company_name") or ""))


def _sync_extended_snapshot(ticker: str) -> dict:
    """Extra yfinance fields for quality + valuation (info + statement fallbacks)."""
    from .valuation_inputs import fetch_yfinance_valuation_snapshot

    return fetch_yfinance_valuation_snapshot(ticker)


def _get_historical_cagr_3y(ticker: str) -> Optional[float]:
    """Calculate 3Y CAGR from data_lake_output/daily_prices."""
    try:
        import pandas as pd
        from .data_lake.config import PRICES_DIR, DATA_LAKE_SOURCE, HF_DATASET_ID
        import os

        path = None
        if DATA_LAKE_SOURCE == "hf" and HF_DATASET_ID:
            from huggingface_hub import hf_hub_download
            try:
                token = os.environ.get("HF_TOKEN")
                path = hf_hub_download(repo_id=HF_DATASET_ID, repo_type="dataset", filename=f"daily_prices/{ticker}.parquet", token=token)
            except Exception as e:
                logger.warning("[decision_terminal] HF DL failed for %s daily_prices: %s", ticker, e)

        if not path:
            path = os.path.join(PRICES_DIR, f"{ticker}.parquet")

        if not os.path.exists(path):
            return None
        df = pd.read_parquet(path, columns=["Close"])
        if len(df) < 756:  # roughly 3 years of trading days
            return None
        latest = df["Close"].iloc[-1]
        old = df["Close"].iloc[-756]
        if old <= 0: return None
        return round((pow(latest / old, 1.0 / 3.0) - 1.0) * 100.0, 2)
    except Exception as e:
        logger.warning("[decision_terminal] historical CAGR failed for %s: %s", ticker, e)
        return None


def _get_historical_quality_metrics(ticker: str) -> dict:
    """Fallback quality metrics from data_lake_output/quarterly_financials."""
    try:
        import pandas as pd
        from .data_lake.config import FUNDAMENTALS_DIR, DATA_LAKE_SOURCE, HF_DATASET_ID
        import os

        path = None
        if DATA_LAKE_SOURCE == "hf" and HF_DATASET_ID:
            from huggingface_hub import hf_hub_download
            try:
                token = os.environ.get("HF_TOKEN")
                path = hf_hub_download(repo_id=HF_DATASET_ID, repo_type="dataset", filename=f"quarterly_financials/{ticker}.parquet", token=token)
            except Exception as e:
                logger.warning("[decision_terminal] HF DL failed for %s financials: %s", ticker, e)

        if not path:
            path = os.path.join(FUNDAMENTALS_DIR, f"{ticker}.parquet")

        if not os.path.exists(path):
            return {}
        df = pd.read_parquet(path)
        if df.empty:
            return {}
        latest = df.iloc[-1]
        return {
            "roe": latest.get("roe"),
            "gross_margin": latest.get("gross_margin"),
            "freeCashflow": latest.get("Free Cash Flow"),
            "totalDebt": latest.get("Total Debt"),
            "ebitda": latest.get("EBITDA"),
        }
    except Exception as e:
        logger.warning("[decision_terminal] historical quality failed for %s: %s", ticker, e)
        return {}


def _format_usd_compact(n: Optional[float]) -> str:
    return format_usd_compact(n)


def _multiples_heuristic_fair_price(
    trailing_eps: Optional[float],
    roe_pct: float,
    current_price: float,
    trailing_pe: Optional[float],
    business_type: str = "other",
    revenue_growth: Optional[float] = None,
    forward_eps: Optional[float] = None,
    earnings_growth: Optional[float] = None,
) -> Optional[float]:
    """
    Growth- and quality-adjusted target P/E × EPS — heuristic, not peer medians.
    Uses forward EPS when it meaningfully exceeds trailing (e.g. WMT ramp).
    """
    eps = float(trailing_eps) if trailing_eps is not None else None
    if eps is None or eps <= 0:
        return None
    fwd = float(forward_eps) if forward_eps is not None else None
    if fwd is not None and fwd > eps * 1.03:
        eps = fwd

    base_pe = 12.0
    adj = min(14.0, max(0.0, roe_pct / 3.0))

    eg = float(earnings_growth) if earnings_growth is not None else None
    use_growth_pe = business_type in (
        "profitable_growth",
        "high_growth_unprofitable",
        "ai_accelerator_platform_leader",
    ) or (
        business_type == "wide_moat_compounder"
        and eg is not None
        and eg > 0.12
    )

    if use_growth_pe:
        growth_input = max(revenue_growth or 0.0, eg or 0.0)
        growth_bonus = max(0.0, growth_input * 100.0 * 0.4)
        max_pe = 45.0
        target_pe = min(max_pe, max(10.0, base_pe + adj + growth_bonus))
        if business_type == "wide_moat_compounder" and eg is not None and eg > 0.12 and roe_pct >= 18:
            quality_pe = min(max_pe, 18.0 + roe_pct / 2.0 + eg * 100.0 * 0.6)
            target_pe = max(target_pe, quality_pe)
    else:
        max_pe = 28.0
        target_pe = min(max_pe, max(10.0, base_pe + adj))

    if trailing_pe and trailing_pe > 0 and current_price > 0:
        pe_norm = min(1.15, max(0.85, 18.0 / trailing_pe))
        if use_growth_pe and business_type == "wide_moat_compounder" and eg is not None and eg > 0.12:
            pe_norm = max(pe_norm, 1.0)
        target_pe *= pe_norm
    return round(eps * target_pe, 2)


def _moat_heuristic(roe_pct: float, gross_margin_pct: float) -> Tuple[str, str]:
    if roe_pct >= 18 and gross_margin_pct >= 0.22:
        return "Wide (heuristic)", "Strong"
    if roe_pct >= 12 and gross_margin_pct >= 0.15:
        return "Narrow (heuristic)", "Moderate"
    return "Limited (heuristic)", "Weak"


def _debate_stance_bull_pct(debate: DebateResult) -> float:
    s = debate.bull_score + debate.bear_score + debate.neutral_score
    return round(100.0 * debate.bull_score / s, 1) if s else 50.0


def _debate_confidence_pct(debate: DebateResult) -> float:
    return round(float(debate.consensus_confidence) * 100.0, 1)


def _expert_bullish_pct(debate: DebateResult) -> float:
    return round(0.5 * _debate_stance_bull_pct(debate) + 0.5 * _debate_confidence_pct(debate), 1)


def _fuse_headline_verdict(swarm: SwarmConsensus, debate: DebateResult) -> Tuple[str, str]:
    d = (debate.verdict or "NEUTRAL").strip().upper()
    sv = swarm.global_verdict or ""
    note_parts: List[str] = []
    headline = debate.verdict
    if "REJECTED" in sv:
        note_parts.append("Swarm risk gate active (macro/stress or factor rejections).")
        if d in ("STRONG BUY", "BUY"):
            headline = "NEUTRAL"
            note_parts.append("Bullish debate verdict capped while swarm is in a REJECTED state.")
    elif sv == "NEUTRAL" and d in ("STRONG BUY", "BUY"):
        note_parts.append("Debate leans bullish; swarm consensus is neutral — signals are mixed.")
    elif sv in ("STRONG SELL", "SELL") and d in ("STRONG BUY", "BUY"):
        note_parts.append("Debate and swarm disagree on direction; rely on fusion note and provenance.")
    return headline, " ".join(note_parts)


def _swarm_rejection_present(swarm: SwarmConsensus) -> bool:
    if "REJECTED" in (swarm.global_verdict or ""):
        return True
    for fr in swarm.factors.values():
        if fr.status == VerificationStatus.REJECTED:
            return True
    return False


def _sanitize_roadmap_scenarios(
    spot: float,
    bull: Optional[float],
    base: Optional[float],
    bear: Optional[float],
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Keep 3Y scenario prices ordered (bull >= base >= bear) and anchored to spot:
    bull is an upside path (>= spot), bear is a downside path (<= spot).
    """
    if spot <= 0:
        return bull, base, bear
    try:
        b, m, e = float(bull), float(base), float(bear)
    except (TypeError, ValueError):
        return None, None, None
    if min(b, m, e) <= 0:
        return None, None, None

    misscaled = max(b, m, e) < spot * 0.55 or max(b, m, e) > spot * 25 or min(b, m, e) < spot * 0.02
    lo, hi = spot * 0.35, spot * 2.75

    if misscaled:
        # Truthful-data contract: a misscaled model output is dropped, never
        # replaced with fabricated multiples of spot.
        return None, None, None
    b, m, e = max(lo, min(hi, b)), max(lo, min(hi, m)), max(lo, min(hi, e))
    ordered = sorted([b, m, e], reverse=True)
    b, m, e = ordered[0], ordered[1], ordered[2]

    b = max(b, spot * 1.08)
    e = min(e, spot * 0.92)
    m = max(e, min(b, m))
    if m < spot * 0.98:
        m = max(e, min(b, spot * 1.04))

    b = max(b, m, e)
    e = min(b, m, e)
    m = max(e, min(b, m))
    return round(b, 2), round(m, 2), round(e, 2)


def _heuristic_roadmap(
    current_price: float, hist_cagr_3y: Optional[float] = None
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], List[str]]:
    """
    Transparent roadmap derived from REAL historical 3Y CAGR only.

    Truthful-data contract: without a historical CAGR there is no basis for a
    scenario roadmap, so all prices come back ``None`` with an honest note —
    never arbitrary multiples of spot.
    """
    if hist_cagr_3y is None:
        return None, None, None, None, [
            "Roadmap unavailable: no predictor forecast, no LLM scenario JSON, "
            "and no historical 3Y CAGR to anchor a transparent heuristic.",
        ]

    cagr_factor = hist_cagr_3y / 100.0
    b = current_price * pow(1.0 + cagr_factor, 3.0)
    u = b * 1.2
    e = current_price * pow(1.0 + min(0.0, cagr_factor - 0.05), 3.0)

    u, b, e = _sanitize_roadmap_scenarios(current_price, u, b, e)
    if u is None or b is None or e is None:
        return None, None, None, None, [
            "Roadmap unavailable: historical-CAGR heuristic produced out-of-range "
            "scenarios and was discarded rather than substituted.",
        ]
    cagr_b = (pow(b / current_price, 1.0 / 3.0) - 1.0) * 100.0

    assumptions = [
        f"Base case tied to historical 3Y CAGR ({hist_cagr_3y:.1f}%).",
        "Bull / bear are symmetric stress bands around base (not a formal model).",
    ]
    return round(u, 2), round(b, 2), round(e, 2), round(cagr_b, 2), assumptions


def _build_provider_audit(
    *,
    ticker: str,
    debate_data: dict,
    poly_raw: dict,
    debate_spot_price_source: Optional[str],
    terminal_spot_price_source: Optional[str],
    market_data_degraded: bool,
    filled_spot_from_ext: bool,
    hist_cagr_present: bool,
    hist_quality_nonempty: bool,
    roadmap: TerminalRoadmapPanel,
) -> Dict[str, Any]:
    """
    Documentation-oriented map of which upstream families feed each terminal block.
    Not a telemetry pipeline — omit from responses unless explicitly requested.
    """
    spot = terminal_spot_price_source or "none"
    spot_family = (
        "stooq"
        if spot == "stooq"
        else "fincrawler"
        if spot == "fincrawler"
        else "yfinance"
        if spot in ("yfinance_history", "yfinance_info", "merged_from_info") or filled_spot_from_ext
        else "none"
    )
    return {
        "schema_version": 1,
        "ticker": ticker.upper(),
        "debate_market_pipeline": {
            "connector": "debate_data (yfinance → Stooq → FinCrawler for spot)",
            "debate_spot_price_source": debate_spot_price_source,
            "terminal_spot_price_source": terminal_spot_price_source,
            "spot_provider_family": spot_family,
            "market_data_degraded": market_data_degraded,
            "filled_terminal_spot_from_yfinance_ext": filled_spot_from_ext,
        },
        "valuation": {
            "panel": "valuation",
            "spot_and_momentum_inputs": spot_family,
            "extended_snapshot_for_multiples_and_quality": "yfinance_valuation_snapshot",
            "fair_value_models": {
                "DCF": "owner_earnings_dcf_scenarios",
                "Momentum": "composite_momentum_model",
                "Multiples": "heuristic",
            },
            "historical_cagr_from_data_lake": hist_cagr_present,
        },
        "quality": {
            "panel": "quality",
            "fundamentals_and_ratios": "yfinance_via_debate_data_and_ext",
            "historical_quarterly_fallback": "data_lake_parquet" if hist_quality_nonempty else "none",
        },
        "verdict": {
            "panel": "verdict",
            "expert_stance_and_headline_fusion": "internal_swarm_and_debate_llm",
            "prediction_market": {
                "provider": "polymarket",
                "api_surface": "gamma-api.polymarket.com",
                "connector_label": poly_raw.get("source"),
                "keyword_resolution": poly_raw.get("keyword_resolution") or "unknown",
                "event_count": len(poly_raw.get("events") or []),
                "has_relevant_data": bool(poly_raw.get("has_relevant_data")),
            },
        },
        "roadmap": {
            "panel": "roadmap",
            "scenario_prices_source": roadmap.provenance.source or "unknown",
            "used_heuristic_fallback": roadmap.used_heuristic_fallback,
        },
    }


@dataclass
class _ResolvedSpot:
    price_f: Optional[float]
    spot_price_source: Optional[str]
    market_data_degraded: bool
    filled_spot_from_ext: bool
    spot_envelope: Optional[SpotEnvelope]
    debate_spot_price_source: Optional[str]


def _resolve_terminal_spot(
    *,
    spot_quote: Any,
    debate_data: dict,
    ext: dict,
) -> _ResolvedSpot:
    debate_spot_price_source = debate_data.get("spot_price_source")
    filled_spot_from_ext = False
    price_f: Optional[float] = None
    market_data_degraded = bool(debate_data.get("market_data_degraded"))
    spot_price_source = debate_spot_price_source
    spot_envelope: Optional[SpotEnvelope] = None

    if spot_quote is not None and getattr(spot_quote, "price", None):
        price_f = float(spot_quote.price)
        spot_price_source = spot_quote.source
        market_data_degraded = bool(spot_quote.degraded)
        spot_envelope = SpotEnvelope(
            price_usd=price_f,
            source=spot_quote.source,
            captured_at_utc=spot_quote.captured_at_utc,
            degraded=spot_quote.degraded,
            momentum_anchor_usd=getattr(spot_quote, "momentum_anchor_usd", None),
        )
    else:
        price = debate_data.get("current_price")
        try:
            price_f = float(price) if price is not None else None
        except (TypeError, ValueError):
            price_f = None
        if price_f is not None and price_f <= 0:
            price_f = None
        if price_f is None:
            for key in ("regularMarketPrice", "currentPrice", "previousClose"):
                raw = ext.get(key)
                if raw is None:
                    continue
                try:
                    pf = float(raw)
                    if pf > 0:
                        price_f = pf
                        filled_spot_from_ext = True
                        market_data_degraded = True
                        if not spot_price_source:
                            spot_price_source = "yfinance_info"
                        break
                except (TypeError, ValueError):
                    continue

    return _ResolvedSpot(
        price_f=price_f,
        spot_price_source=spot_price_source if isinstance(spot_price_source, str) else None,
        market_data_degraded=market_data_degraded,
        filled_spot_from_ext=filled_spot_from_ext,
        spot_envelope=spot_envelope,
        debate_spot_price_source=debate_spot_price_source
        if isinstance(debate_spot_price_source, str)
        else None,
    )


def _dcf_sensitivity_weight_factor(
    bear: Optional[float], base: Optional[float], bull: Optional[float]
) -> float:
    """Blend weight multiplier for DCF based on how wide its sensitivity range is.

    ``width_pct = (bull − bear)/base``. The factor decays linearly from 1.0 at a
    60%-wide range down to a 0.3 floor, so a very wide DCF (e.g. NVDA's ~213%)
    contributes far less to the consensus fair value and weight shifts to
    multiples. Returns 1.0 when the range is unavailable.
    """
    if bear is None or bull is None or not base or base <= 0:
        return 1.0
    width_pct = (bull - bear) / base
    return max(0.3, min(1.0, 1.0 - max(0.0, width_pct - 0.6) / 2.0))


def _build_valuation_panel(
    *,
    ticker: str,
    debate_data: dict,
    ext: dict,
    resolved: _ResolvedSpot,
    hist_cagr: Optional[float],
    hist_quality: dict,
    momentum_readout: Optional[Dict[str, Any]],
) -> TerminalValuationPanel:
    price_f = resolved.price_f
    roe_val = debate_data.get("roe")
    if roe_val is None and hist_quality.get("roe") is not None:
        roe_val = hist_quality.get("roe") * 100.0
    roe_pct = float(roe_val or 0.0)

    gross_m_val = debate_data.get("gross_margins")
    if gross_m_val is None and hist_quality.get("gross_margin") is not None:
        gross_m_val = hist_quality.get("gross_margin") * 100.0
    gm = normalize_gross_margin(gross_m_val)
    gross_m = gm.percent if gm else 0.0

    trailing_eps = ext.get("trailingEps") or None
    if trailing_eps is not None:
        trailing_eps = float(trailing_eps)

    pe = debate_data.get("pe_ratio")
    pe_f = float(pe) if pe is not None else None
    momentum_available = momentum_readout is not None

    from .valuation_inputs import compute_dcf_scenarios, owner_earnings_fcf

    dcf_result = compute_dcf_scenarios(
        ext,
        hist_cagr_pct=hist_cagr,
        price_usd=price_f,
    )
    dcf_price = dcf_result.get("base_fair_value_usd")
    dcf_scenarios = dcf_result.get("scenarios") or {}
    business_type = dcf_result.get("business_type", "other")
    
    revenue_growth = dcf_result.get("revenue_growth")
    if revenue_growth is None and ext.get("revenueGrowth") is not None:
        try:
            revenue_growth = float(ext.get("revenueGrowth"))
        except (TypeError, ValueError):
            pass

    mfv = None
    if price_f:
        forward_eps = ext.get("forwardEps")
        if forward_eps is not None:
            try:
                forward_eps = float(forward_eps)
            except (TypeError, ValueError):
                forward_eps = None
        eg = ext.get("earningsGrowth")
        if eg is not None:
            try:
                eg = float(eg)
            except (TypeError, ValueError):
                eg = None
        mfv = _multiples_heuristic_fair_price(
            trailing_eps, roe_pct, price_f, pe_f,
            business_type=business_type,
            revenue_growth=revenue_growth,
            forward_eps=forward_eps,
            earnings_growth=eg,
        )

    def _dcf_provenance_note() -> str:
        if not dcf_result.get("available"):
            return dcf_result.get("missing_reason") or "Insufficient DCF inputs."

        bear = dcf_scenarios.get("bear")
        bull = dcf_scenarios.get("bull")
        fcf_years = dcf_result.get("fcf_years_used") or 0
        growth_src = dcf_result.get("growth_anchor_source")
        model_name = dcf_result.get("model_name", "DCF")

        parts = []
        if model_name == "High-Growth Revenue-to-FCF DCF":
            rev_g = dcf_result.get("revenue_growth", 0)
            target_margin = dcf_result.get("target_fcf_margin_base", 0)
            parts.append(f"High-Growth DCF: 10-year fade from {rev_g:.1%} revenue growth.")
            parts.append(f"FCF margin expands to mature target of {target_margin:.1%}.")
            parts.append(
                "Warning: This valuation is highly sensitive to revenue growth, "
                "mature margin, dilution, and WACC assumptions."
            )
        else:
            if fcf_years >= 3 and dcf_result.get("fcf_source") == "median_5y_owner_earnings":
                fcf_desc = f"5Y median owner earnings (OCF−capex across {fcf_years} fiscal years)"
            else:
                fcf_desc = "Owner-earnings DCF (OCF−capex with statement fallbacks)"

            if growth_src == "median_5y_ocf_yoy":
                yoy = dcf_result.get("median_yoy_growth_pct")
                growth_desc = (
                    f"base growth anchored to median YoY OCF ({yoy:.1f}%)"
                    if yoy is not None
                    else "base growth anchored to median YoY OCF"
                )
            else:
                growth_desc = "declining 5Y FCF growth path"

            parts.append(
                f"{fcf_desc}; {growth_desc}, CAPM WACC {dcf_result.get('wacc_base', 0):.1%}, "
                f"terminal 2.5%, net cash added."
            )
            parts.append(
                f"FCF source: {dcf_result.get('fcf_source')}; "
                f"net cash: ${dcf_result.get('net_cash_usd', 0) / 1e9:.1f}B "
                f"({dcf_result.get('net_cash_source')})."
            )

        if bear is not None and bull is not None and dcf_price is not None:
            parts.append(f"Scenario range: bear ${bear:.0f} · base ${dcf_price:.0f} · bull ${bull:.0f}.")

        return " ".join(parts)

    models: List[TerminalValuationModel] = [
        TerminalValuationModel(
            name="DCF",
            fair_value_usd=dcf_price,
            available=bool(dcf_result.get("available")),
            scenarios={k: float(v) for k, v in dcf_scenarios.items() if v is not None} or None,
            classification=dcf_result.get("classification"),
            implied_growth=dcf_result.get("implied_growth"),
            implied_growth_3y=dcf_result.get("implied_growth_3y"),
            implied_growth_5y=dcf_result.get("implied_growth_5y"),
            implied_margin=dcf_result.get("implied_margin"),
            implied_roic=dcf_result.get("implied_roic"),
            dcf_tiers=dcf_result.get("dcf_tiers"),
            valuation_range=dcf_result.get("valuation_range"),
            margin_of_safety_pct=dcf_result.get("margin_of_safety_pct"),
            market_expectation=dcf_result.get("market_expectation"),
            risk_flags=dcf_result.get("risk_flags") or [],
            provenance=TerminalFieldProvenance(
                source="owner_earnings_dcf",
                confidence=round((dcf_result.get("dcf_confidence_score", 55) or 55) / 100.0, 2),
                missing_reason=(
                    None
                    if dcf_result.get("available")
                    else dcf_result.get("missing_reason")
                    or "Insufficient owner-earnings FCF or shares outstanding data"
                ),
                formula_or_note=_dcf_provenance_note(),
            ),
        ),
        TerminalValuationModel(
            name="Momentum",
            fair_value_usd=None,
            available=momentum_available,
            momentum_score=(
                momentum_readout.get("momentum_pricing_score") if momentum_readout else None
            ),
            momentum_summary=momentum_readout,
            provenance=TerminalFieldProvenance(
                source="composite_momentum_model",
                formula_or_note=(
                    "Composite momentum score (absolute + relative vs SPY/sector + "
                    "capital flow + risk-adjusted + regime) with downside exposure."
                ),
                missing_reason=None if momentum_available else "Momentum model fetch failed or timed out.",
            ),
        ),
        TerminalValuationModel(
            name="Multiples",
            fair_value_usd=mfv,
            available=mfv is not None,
            provenance=TerminalFieldProvenance(
                source="heuristic",
                confidence=0.45,
                formula_or_note="Target P/E based on ROE and business type (max 28–45), scaled by trailing P/E vs ~18 — illustrative only.",
                missing_reason=None if mfv is not None else "Insufficient EPS for multiples fair value.",
            ),
        ),
    ]

    from .valuation_signal import (
        case_assessments,
        composite_signal_label,
        implied_downside_pct,
        margin_of_safety_pct,
        valuation_confidence_label,
        valuation_gap_pct,
        valuation_signal_label,
    )

    valuation_models = [m for m in models if m.name != "Momentum"]
    usable = [
        m for m in valuation_models
        if m.available and m.fair_value_usd is not None
    ]

    # Down-weight DCF when its sensitivity range is very wide: a $67–$722 span
    # (≈213% of base for NVDA) is too imprecise to anchor the blend, so weight
    # shifts to multiples.
    dcf_width_factor = _dcf_sensitivity_weight_factor(
        dcf_scenarios.get("bear"), dcf_scenarios.get("base"), dcf_scenarios.get("bull")
    )

    def _effective_weight(m: TerminalValuationModel) -> float:
        w = m.provenance.confidence or 0.5
        if "DCF" in (m.name or ""):
            w *= dcf_width_factor
        return w

    if usable:
        weighted_sum = sum(m.fair_value_usd * _effective_weight(m) for m in usable)
        total_weight = sum(_effective_weight(m) for m in usable)
        avg_fair = round(weighted_sum / total_weight, 2) if total_weight > 0 else None
    else:
        avg_fair = None

    pct_vs = gap_pct = downside_pct = None
    signal_label = confidence_label = bull_assessment = bear_assessment = ""
    dcf_low = dcf_scenarios.get("bear")
    dcf_high = dcf_scenarios.get("bull")
    if avg_fair and price_f and avg_fair > 0:
        pct_vs = margin_of_safety_pct(price_f, avg_fair)
        gap_pct = valuation_gap_pct(price_f, avg_fair)
        downside_pct = implied_downside_pct(price_f, avg_fair)
        signal_label = valuation_signal_label(gap_pct, price_f, dcf_high)
        bull_assessment, bear_assessment = case_assessments(price_f, dcf_low, dcf_high)
        confidence_label = valuation_confidence_label(
            len(usable),
            bool(dcf_result.get("available")),
            dcf_low,
            dcf_high,
            dcf_price,
            [m.fair_value_usd for m in usable],
        )

    gauge_label = signal_label or ("N/A" if price_f is None else "INSUFFICIENT MODEL INPUTS")

    momentum_model = next((m for m in models if m.name == "Momentum"), None)
    momentum_score_val = getattr(momentum_model, "momentum_score", None) if momentum_model else None
    composite = composite_signal_label(signal_label, momentum_score_val) if signal_label else ""

    return TerminalValuationPanel(
        current_price_usd=price_f,
        average_fair_value_usd=avg_fair,
        pct_vs_average=pct_vs,
        valuation_gap_pct=gap_pct,
        implied_downside_pct=downside_pct,
        valuation_signal=signal_label,
        valuation_confidence=confidence_label,
        composite_signal=composite,
        dcf_range_low_usd=float(dcf_low) if dcf_low is not None else None,
        dcf_range_high_usd=float(dcf_high) if dcf_high is not None else None,
        dcf_tiers=dcf_result.get("dcf_tiers"),
        bull_case_assessment=bull_assessment,
        bear_case_assessment=bear_assessment,
        gauge_label=gauge_label,
        business_classification=business_type,
        market_expectation=dcf_result.get("market_expectation"),
        implied_growth_3y=dcf_result.get("implied_growth_3y"),
        implied_growth_5y=dcf_result.get("implied_growth_5y"),
        risk_flags=dcf_result.get("risk_flags") or [],
        models=models,
        panel_note=(
            "Base fair value is a confidence-weighted average of DCF (base case) and Multiples; "
            "DCF is down-weighted when its sensitivity range is very wide. "
            "Momentum is shown separately (0–100 score, not blended into fair value). "
            "Valuation gap and implied move use distinct denominators."
        ),
    )


def _build_quality_panel(
    *,
    ticker: str,
    debate_data: dict,
    ext: dict,
    hist_quality: dict,
    market_regime: str = "BULL_NORMAL",
) -> TerminalQualityPanel:
    roe_val = debate_data.get("roe")
    if roe_val is None and hist_quality.get("roe") is not None:
        roe_val = hist_quality.get("roe") * 100.0
    roe_pct = float(roe_val or 0.0)

    gross_m_val = debate_data.get("gross_margins")
    if gross_m_val is None and hist_quality.get("gross_margin") is not None:
        gross_m_val = hist_quality.get("gross_margin") * 100.0
    gm = normalize_gross_margin(gross_m_val)
    gross_m = gm.percent if gm else 0.0

    from .valuation_inputs import owner_earnings_fcf

    fcf_for_quality, _fcf_src = owner_earnings_fcf(ext)
    fcf = (
        fcf_for_quality
        or ext.get("freeCashflow")
        or debate_data.get("free_cash_flow")
        or hist_quality.get("freeCashflow")
    )
    debt = ext.get("totalDebt") or hist_quality.get("totalDebt")
    ebitda = ext.get("ebitda") or hist_quality.get("ebitda")
    cr = ext.get("currentRatio")
    debt_ebitda_lab = "N/A"
    de_prov = TerminalFieldProvenance(source="yfinance_or_datalake")
    debt_ratio_val: Optional[float] = None
    if debt is not None and ebitda and float(ebitda) != 0:
        debt_ratio_val = float(debt) / float(ebitda)
        debt_ebitda_lab = f"{debt_ratio_val:.2f}x EBITDA"
        de_prov.formula_or_note = "Total debt ÷ EBITDA (yfinance snapshot)."
    else:
        de_prov.missing_reason = "Debt and/or EBITDA not available from provider."

    gm_ratio = gm.ratio if gm else 0.0
    moat_lab, moat_st = _moat_heuristic(roe_pct, gm_ratio)
    roic_proxy_val = roic_proxy(roe_pct)

    if not isinstance(ext, dict):
        ext = {}
    if "totalRevenue" not in ext or "revenueGrowth" not in ext:
        try:
            from backend.fincrawler_client import FinCrawlerClient

            fc_client = FinCrawlerClient()
            fund = fc_client.get_fundamentals_sync(ticker)
            if fund:
                if "totalRevenue" not in ext:
                    ext["totalRevenue"] = fund.get("totalRevenue")
                if "revenueGrowth" not in ext:
                    ext["revenueGrowth"] = fund.get("revenueGrowth")
                if "freeCashflow" not in ext:
                    ext["freeCashflow"] = fund.get("freeCashflow")
                if "totalCash" not in ext:
                    ext["totalCash"] = fund.get("totalCash")
                if "stockBasedCompensation" not in ext:
                    ext["stockBasedCompensation"] = fund.get("stockBasedCompensation")
                if "grossMargins" not in ext:
                    ext["grossMargins"] = fund.get("grossMargins")
        except Exception:
            pass

    rev_g = ext.get("revenueGrowth")
    fcf = ext.get("freeCashflow") or ext.get("operatingCashflow") or 0.0
    rev_0 = ext.get("totalRevenue")
    fcf_margin_val = (fcf / rev_0) * 100.0 if rev_0 and rev_0 > 0 else None
    rev_g_pct = rev_g * 100.0 if rev_g is not None else None
    rule_of_40_val = None
    if rev_g_pct is not None and fcf_margin_val is not None:
        rule_of_40_val = rev_g_pct + fcf_margin_val
    total_cash = ext.get("totalCash")
    sbc = ext.get("stockBasedCompensation") or 0.0
    cash_burn_months = None
    if total_cash and fcf < 0:
        cash_burn_months = (total_cash / abs(fcf)) * 12.0

    quality = TerminalQualityPanel(
        rows=[
            TerminalQualityRow(
                id="roic",
                label="ROIC (proxy)",
                value_label=f"{roic_proxy_val}%",
                status_label="See note" if roe_pct else "N/A",
                provenance=TerminalFieldProvenance(
                    source="metric_primitives",
                    formula_or_note="Approximated as 0.8 × ROE from yfinance — not reported ROIC.",
                ),
            ),
            TerminalQualityRow(
                id="moat",
                label="Moat",
                value_label=moat_lab,
                status_label=moat_st,
                provenance=TerminalFieldProvenance(
                    source="heuristic",
                    confidence=0.4,
                    formula_or_note="Rule from ROE + gross margin — not a Morningstar-style moat rating.",
                ),
            ),
            TerminalQualityRow(
                id="fcf",
                label="Free cash flow",
                value_label=_format_usd_compact(float(fcf)) if fcf is not None else "N/A",
                status_label="TTM snapshot",
                provenance=TerminalFieldProvenance(source="yfinance"),
            ),
            TerminalQualityRow(
                id="debt",
                label="Leverage",
                value_label=debt_ebitda_lab,
                status_label=(
                    "Low"
                    if debt_ratio_val is not None and debt_ratio_val < 2.5
                    else ("Unknown" if debt_ratio_val is None else "Review")
                ),
                provenance=de_prov,
            ),
            TerminalQualityRow(
                id="margin",
                label="Gross margin",
                value_label=f"{gross_m:.1f}%" if gross_m else "N/A",
                status_label="Good" if gross_m and gross_m >= 18 else "Thin",
                provenance=TerminalFieldProvenance(source="yfinance"),
            ),
            TerminalQualityRow(
                id="current_ratio",
                label="Current ratio",
                value_label=f"{float(cr):.2f}" if cr is not None else "N/A",
                status_label="High" if cr is not None and float(cr) >= 1.5 else "Watch",
                provenance=TerminalFieldProvenance(
                    source="yfinance",
                    missing_reason=None if cr is not None else "Not reported in info bundle.",
                ),
            ),
            TerminalQualityRow(
                id="revenue_growth",
                label="Revenue Growth (TTM)",
                value_label=f"{rev_g_pct:.1f}%" if rev_g_pct is not None else "N/A",
                status_label=(
                    "High Growth"
                    if rev_g_pct and rev_g_pct > 20
                    else ("Moderate" if rev_g_pct and rev_g_pct > 5 else "Slow")
                ),
                provenance=TerminalFieldProvenance(
                    source="yfinance/statements",
                    formula_or_note="YoY Revenue Growth %",
                ),
            ),
            TerminalQualityRow(
                id="rule_of_40",
                label="Rule of 40",
                value_label=f"{rule_of_40_val:.1f}" if rule_of_40_val is not None else "N/A",
                status_label="Pass" if rule_of_40_val and rule_of_40_val >= 40 else "Fail",
                provenance=TerminalFieldProvenance(
                    source="yfinance/statements",
                    formula_or_note="Revenue Growth % + FCF Margin %",
                ),
            ),
            TerminalQualityRow(
                id="cash_runway",
                label="Cash Runway",
                value_label=(
                    f"{cash_burn_months:.1f} months"
                    if cash_burn_months is not None
                    else ("Profitable" if fcf > 0 else "N/A")
                ),
                status_label=(
                    "Healthy"
                    if cash_burn_months and cash_burn_months > 24
                    else ("Warning" if cash_burn_months and cash_burn_months < 12 else "Stable")
                ),
                provenance=TerminalFieldProvenance(
                    source="yfinance/statements",
                    formula_or_note="Total Cash / Absolute Negative FCF (annualized)",
                ),
            ),
        ]
    )

    market_cap_raw = ext.get("marketCap")
    market_cap_f: Optional[float] = None
    if market_cap_raw is not None:
        try:
            market_cap_f = float(market_cap_raw)
        except (TypeError, ValueError):
            market_cap_f = None

    from .business_health import enrich_quality_panel

    fcf_float: Optional[float] = None
    if fcf is not None:
        try:
            fcf_float = float(fcf)
        except (TypeError, ValueError):
            fcf_float = None

    cr_float: Optional[float] = None
    if cr is not None:
        try:
            cr_float = float(cr)
        except (TypeError, ValueError):
            cr_float = None

    return enrich_quality_panel(
        quality,
        market_regime=market_regime,
        roic_pct=roic_proxy_val if roe_pct else None,
        moat_status=moat_st,
        fcf_usd=fcf_float,
        market_cap=market_cap_f,
        debt_to_ebitda=debt_ratio_val,
        gross_margin_pct=gross_m if gross_m else None,
        current_ratio=cr_float,
    )


async def _build_verdict_panel_and_brain(
    ticker: str,
    swarm: SwarmConsensus,
    debate: DebateResult,
    poly_raw: dict,
    debate_data: dict,
) -> Tuple[TerminalVerdictPanel, Optional[BrainVerdict]]:
    tokens = _company_tokens_from_debate_data(debate_data)
    events = poly_raw.get("events") or []
    gated = select_gated_polymarket_event(events, ticker, tokens + [ticker])
    best_score = gated.relevance_score if gated else 0.0

    pm_pct = None
    pm_title = None
    gated_out = True
    if gated is not None:
        pm_pct = gated.probability_pct
        pm_title = gated.title
        gated_out = False

    headline, fusion_note = _fuse_headline_verdict(swarm, debate)
    if _swarm_rejection_present(swarm) and "capped" not in fusion_note.lower():
        fusion_note = (fusion_note + " One or more swarm factors were REJECTED.").strip()

    brain_block: Optional[BrainVerdict] = None
    try:
        from .brain.cutover import aserve_for_surface
        from .brain import adapters as _ba

        _br = await aserve_for_surface(ticker.upper(), "decision_terminal")
        if _br:
            _head = _ba.to_decision_terminal_headline(_br)
            headline = _head["headline_verdict"]
            fusion_note = _head["fusion_note"]
            try:
                _live = _br.get("live") or _br.get("base") or {}
                brain_block = BrainVerdict(
                    outperform_probability=_live.get("outperform_probability"),
                    composite_score=_live.get("composite_score"),
                    recommendation=_live.get("recommendation"),
                    confidence_score=_br.get("confidence_score"),
                    live_price=_live.get("live_price"),
                    price_source=_br.get("price_source"),
                    signal_scores=_live.get("signal_scores"),
                    status=_br.get("status"),
                    waterfall=_br.get("waterfall"),
                )
            except Exception as _be:  # noqa: BLE001
                logger.debug("[decision_terminal] brain block assembly failed: %s", _be)
    except Exception as _e:  # noqa: BLE001
        logger.debug("[decision_terminal] brain cutover skipped: %s", _e)

    verdict = TerminalVerdictPanel(
        headline_verdict=headline,
        debate_verdict=debate.verdict,
        swarm_verdict=swarm.global_verdict,
        fusion_note=fusion_note,
        debate_stance_bull_pct=_debate_stance_bull_pct(debate),
        debate_confidence_pct=_debate_confidence_pct(debate),
        expert_bullish_pct=_expert_bullish_pct(debate),
        prediction_market_bullish_pct=pm_pct,
        prediction_market_event_title=pm_title,
        polymarket_relevance_score=round(best_score, 3) if events else None,
        polymarket_gated_out=gated_out,
    )
    return verdict, brain_block


async def _build_roadmap_panel(
    ticker: str,
    price_f: Optional[float],
    hist_cagr: Optional[float],
    tool_registry: Any,
) -> TerminalRoadmapPanel:
    roadmap_prov = TerminalFieldProvenance(source="predictor_or_heuristic", confidence=0.35)
    bull_p = base_p = bear_p = None
    cagr_b = None
    assumptions: List[str] = []
    conf_r = 0.0
    heuristic_fb = True
    horizon_bands: List[HorizonQuantileBand] = []
    pred_syn_ex: Optional[str] = None
    pred_rev_ex: Optional[str] = None

    if price_f and price_f > 0:
        predictor_filled = False
        if tool_registry is not None:
            try:
                from .brain.flags import brain_surface_enabled

                hs = ["1d", "5d", "21d", "63d"]
                pred = None
                if brain_surface_enabled("predictor"):
                    from .brain.predictor_serve import arun_brain_predictor_forecast

                    pred = await arun_brain_predictor_forecast(ticker, hs)
                    if pred.status != "ok":
                        pred = None
                if pred is None:
                    from .predictor.agent import run_predictor_forecast

                    pred = await run_predictor_forecast(
                        ticker,
                        horizons=hs,
                        tool_registry=tool_registry,
                        emit_ledger=True,
                    )
                if pred.status == "ok" and pred.base_price_usd_3y_scenario is not None:
                    bull_p = pred.bull_price_usd_3y_scenario
                    base_p = pred.base_price_usd_3y_scenario
                    bear_p = pred.bear_price_usd_3y_scenario
                    if base_p and base_p > 0 and price_f > 0:
                        cagr_b = round((pow(base_p / price_f, 1.0 / 3.0) - 1.0) * 100.0, 2)
                    assumptions = list(pred.assumptions or [])[:6]
                    conf_r = {"high": 0.72, "medium": 0.55, "low": 0.38}.get(
                        pred.model_confidence, 0.55
                    )
                    heuristic_fb = False
                    predictor_filled = True
                    roadmap_prov.source = pred.model_version or "predictor"
                    roadmap_prov.confidence = conf_r
                    roadmap_prov.formula_or_note = (
                        "Probabilistic predictor (baselines + TimesFM path); "
                        "3Y scenarios extrapolated from horizon bands."
                    )
                    horizon_bands = [
                        HorizonQuantileBand(
                            horizon=b.horizon,
                            q10_usd=b.q10_usd,
                            q50_usd=b.q50_usd,
                            q90_usd=b.q90_usd,
                            point_usd=b.point_usd,
                        )
                        for b in pred.horizon_bands_usd
                    ]
                    pred_syn_ex = (pred.synthesis_summary or "")[:900] or None
                    pred_rev_ex = (pred.reviewer_summary or "")[:600] or None
            except Exception as e:
                logger.warning("[decision_terminal] predictor roadmap failed: %s", e)

        if not predictor_filled:
            u, b, e, cg, asm = _heuristic_roadmap(price_f, hist_cagr)
            bull_p, base_p, bear_p, cagr_b = u, b, e, cg
            assumptions = asm
            heuristic_fb = True
            if bull_p is not None:
                roadmap_prov.source = "heuristic"
                roadmap_prov.confidence = 0.25
                roadmap_prov.formula_or_note = "Historical 3Y CAGR heuristic when predictor unavailable."
            else:
                roadmap_prov.source = "unavailable"
                roadmap_prov.confidence = 0.0
                roadmap_prov.formula_or_note = "Insufficient data for any roadmap scenario."

    if price_f and price_f > 0 and bull_p and base_p and bear_p:
        bull_p, base_p, bear_p = _sanitize_roadmap_scenarios(price_f, bull_p, base_p, bear_p)
        if bull_p and base_p and bear_p and base_p > 0:
            cagr_b = round((pow(base_p / price_f, 1.0 / 3.0) - 1.0) * 100.0, 2)
        else:
            cagr_b = None
            assumptions = [
                "Roadmap scenarios were dropped: model output was out of range "
                "versus spot and is not substituted with fabricated values.",
            ]

    return TerminalRoadmapPanel(
        bull_price_usd=bull_p,
        base_price_usd=base_p,
        bear_price_usd=bear_p,
        predicted_cagr_base_pct=cagr_b,
        assumptions=assumptions,
        confidence_0_1=conf_r,
        used_heuristic_fallback=heuristic_fb,
        provenance=roadmap_prov,
        horizon_quantile_bands=horizon_bands,
        predictor_synthesis_excerpt=pred_syn_ex,
        predictor_reviewer_excerpt=pred_rev_ex,
    )


def build_snapshot_slice(
    ticker: str,
    debate_data: dict,
    ext: dict,
    *,
    momentum_readout: Optional[Dict[str, Any]] = None,
    spot_quote: Any = None,
    scorecard_summary: Optional[TerminalScorecardSummary] = None,
    market_regime: str = "BULL_NORMAL",
    generated_at_utc: Optional[str] = None,
    slice_from_cache: bool = False,
) -> DecisionSnapshotPayload:
    t = ticker.upper()
    now = generated_at_utc or datetime.now(timezone.utc).isoformat()
    hist_cagr = _get_historical_cagr_3y(t)
    hist_quality = _get_historical_quality_metrics(t)
    resolved = _resolve_terminal_spot(spot_quote=spot_quote, debate_data=debate_data, ext=ext)
    valuation = _build_valuation_panel(
        ticker=t,
        debate_data=debate_data,
        ext=ext,
        resolved=resolved,
        hist_cagr=hist_cagr,
        hist_quality=hist_quality,
        momentum_readout=momentum_readout,
    )
    quality = _build_quality_panel(
        ticker=t,
        debate_data=debate_data,
        ext=ext,
        hist_quality=hist_quality,
        market_regime=market_regime,
    )
    return DecisionSnapshotPayload(
        ticker=t,
        disclaimer=DISCLAIMER,
        generated_at_utc=now,
        slice_from_cache=slice_from_cache,
        valuation=valuation,
        quality=quality,
        market_data_degraded=resolved.market_data_degraded,
        spot_price_source=resolved.spot_price_source,
        data_freshness=_terminal_data_freshness(
            resolved.spot_price_source,
            resolved.market_data_degraded,
            now,
        ),
        spot=resolved.spot_envelope,
        scorecard_summary=scorecard_summary,
    )


async def build_verdict_slice(
    ticker: str,
    swarm: SwarmConsensus,
    debate: DebateResult,
    debate_data: dict,
    poly_raw: dict,
    *,
    macro_fetched_at_utc: Optional[str] = None,
    generated_at_utc: Optional[str] = None,
    slice_from_cache: bool = False,
) -> DecisionVerdictPayload:
    t = ticker.upper()
    now = generated_at_utc or datetime.now(timezone.utc).isoformat()
    verdict, brain = await _build_verdict_panel_and_brain(t, swarm, debate, poly_raw, debate_data)
    return DecisionVerdictPayload(
        ticker=t,
        generated_at_utc=now,
        verdict_captured_at_utc=now,
        macro_fetched_at_utc=macro_fetched_at_utc,
        slice_from_cache=slice_from_cache,
        verdict=verdict,
        swarm=swarm,
        debate=debate,
        brain=brain,
    )


async def build_roadmap_slice(
    ticker: str,
    price_f: Optional[float],
    *,
    tool_registry: Any = None,
    generated_at_utc: Optional[str] = None,
    slice_from_cache: bool = False,
) -> DecisionRoadmapPayload:
    t = ticker.upper()
    now = generated_at_utc or datetime.now(timezone.utc).isoformat()
    hist_cagr = _get_historical_cagr_3y(t)
    roadmap = await _build_roadmap_panel(t, price_f, hist_cagr, tool_registry)
    return DecisionRoadmapPayload(
        ticker=t,
        generated_at_utc=now,
        slice_from_cache=slice_from_cache,
        roadmap=roadmap,
        current_price_usd=price_f,
    )


def assemble_terminal_from_slices(
    snapshot: DecisionSnapshotPayload,
    verdict: DecisionVerdictPayload,
    roadmap: DecisionRoadmapPayload,
    *,
    verdict_from_cache: bool = False,
    include_provider_audit: bool = False,
    debate_data: Optional[dict] = None,
    poly_raw: Optional[dict] = None,
    resolved: Optional[_ResolvedSpot] = None,
) -> DecisionTerminalPayload:
    """Merge three progressive slices into the legacy combined payload."""
    t = snapshot.ticker.upper()
    now = datetime.now(timezone.utc).isoformat()
    pct_vs = snapshot.valuation.pct_vs_average if snapshot.valuation else None
    gap_pct = snapshot.valuation.valuation_gap_pct if snapshot.valuation else None
    gauge_label = snapshot.valuation.gauge_label if snapshot.valuation else ""
    cagr_b = roadmap.roadmap.predicted_cagr_base_pct if roadmap.roadmap else None

    reconciliation = None
    if os.environ.get("RECONCILIATION_ENABLE", "1").strip().lower() not in ("0", "false", "no"):
        reconciliation = build_reconciliation(
            headline_verdict=verdict.verdict.headline_verdict,
            fusion_note=verdict.verdict.fusion_note,
            pct_vs_average=pct_vs,
            gauge_label=gauge_label,
            valuation_gap_pct=gap_pct,
            predicted_cagr_base_pct=cagr_b,
            swarm_rejected=_swarm_rejection_present(verdict.swarm),
            scorecard_summary=snapshot.scorecard_summary,
        )

    provider_audit: Optional[Dict[str, Any]] = None
    if include_provider_audit and debate_data is not None and poly_raw is not None:
        hist_cagr = _get_historical_cagr_3y(t)
        hist_quality = _get_historical_quality_metrics(t)
        if resolved is None:
            resolved = _resolve_terminal_spot(
                spot_quote=None,
                debate_data=debate_data,
                ext={},
            )
        provider_audit = _build_provider_audit(
            ticker=t,
            debate_data=debate_data,
            poly_raw=poly_raw,
            debate_spot_price_source=resolved.debate_spot_price_source,
            terminal_spot_price_source=resolved.spot_price_source,
            market_data_degraded=snapshot.market_data_degraded,
            filled_spot_from_ext=resolved.filled_spot_from_ext,
            hist_cagr_present=hist_cagr is not None,
            hist_quality_nonempty=bool(hist_quality),
            roadmap=roadmap.roadmap,
        )

    return _decision_terminal_payload_json_safe(
        DecisionTerminalPayload(
            ticker=t,
            disclaimer=snapshot.disclaimer,
            generated_at_utc=now,
            verdict_captured_at_utc=verdict.verdict_captured_at_utc or now,
            verdict_from_cache=verdict_from_cache or verdict.slice_from_cache,
            macro_fetched_at_utc=verdict.macro_fetched_at_utc,
            valuation=snapshot.valuation,
            quality=snapshot.quality,
            verdict=verdict.verdict,
            roadmap=roadmap.roadmap,
            market_data_degraded=snapshot.market_data_degraded,
            spot_price_source=snapshot.spot_price_source,
            provider_audit=provider_audit,
            swarm=verdict.swarm,
            debate=verdict.debate,
            data_freshness=snapshot.data_freshness,
            spot=snapshot.spot,
            scorecard_summary=snapshot.scorecard_summary,
            reconciliation=reconciliation,
            brain=verdict.brain,
        )
    )


async def build_decision_terminal_payload(
    ticker: str,
    swarm: SwarmConsensus,
    debate: DebateResult,
    debate_data: dict,
    poly_raw: dict,
    ext: dict,
    llm_client: Any,
    *,
    momentum_readout: Optional[Dict[str, Any]] = None,
    include_provider_audit: bool = False,
    tool_registry: Any = None,
    spot_quote: Any = None,
    scorecard_summary: Optional[TerminalScorecardSummary] = None,
    macro_fetched_at_utc: Optional[str] = None,
    verdict_captured_at_utc: Optional[str] = None,
    verdict_from_cache: bool = False,
) -> DecisionTerminalPayload:
    """Compose full payload from slice builders (unit tests + legacy callers)."""
    _ = llm_client  # roadmap no longer uses LLM fallback in slice path
    t = ticker.upper()
    now = verdict_captured_at_utc or datetime.now(timezone.utc).isoformat()
    market_regime = (
        swarm.macro_state.market_regime.value
        if swarm.macro_state and swarm.macro_state.market_regime
        else "BULL_NORMAL"
    )
    resolved = _resolve_terminal_spot(spot_quote=spot_quote, debate_data=debate_data, ext=ext)
    snapshot = build_snapshot_slice(
        t,
        debate_data,
        ext,
        momentum_readout=momentum_readout,
        spot_quote=spot_quote,
        scorecard_summary=scorecard_summary,
        market_regime=market_regime,
        generated_at_utc=now,
    )
    verdict = await build_verdict_slice(
        t,
        swarm,
        debate,
        debate_data,
        poly_raw,
        macro_fetched_at_utc=macro_fetched_at_utc,
        generated_at_utc=now,
    )
    roadmap = await build_roadmap_slice(
        t,
        resolved.price_f,
        tool_registry=tool_registry,
        generated_at_utc=now,
    )
    return assemble_terminal_from_slices(
        snapshot,
        verdict,
        roadmap,
        verdict_from_cache=verdict_from_cache,
        include_provider_audit=include_provider_audit,
        debate_data=debate_data,
        poly_raw=poly_raw,
        resolved=resolved,
    )


def _resolve_spot_for_terminal(ticker: str):
    from .connectors.spot import resolve_spot

    return resolve_spot(ticker)


async def _build_scorecard_for_terminal(ticker: str) -> Optional[TerminalScorecardSummary]:
    try:
        from .scorecard_service import build_terminal_scorecard_summary

        return await build_terminal_scorecard_summary(
            ticker, preset="balanced", skip_llm_scores=False
        )
    except Exception as e:
        logger.warning("[decision_terminal] scorecard embed failed %s: %s", ticker, e)
        return None


async def _safe_poly_fetch(poly_connector: Any, ticker: str) -> dict:
    from .data_errors import InsufficientDataError

    t = ticker.upper()
    try:
        return await poly_connector.fetch_data(ticker=t)
    except InsufficientDataError:
        raise
    except Exception as e:
        logger.warning("[decision_terminal] polymarket fetch failed: %s", e)
        raise InsufficientDataError(
            "polymarket",
            f"Polymarket fetch failed for {t}: {e}",
            ticker=t,
            missing=["polymarket_events"],
        ) from e


async def _safe_momentum_fetch(ticker: str) -> Optional[Dict[str, Any]]:
    try:
        from .connectors.momentum_data import fetch_momentum_inputs
        from .momentum_model import analyze_momentum

        stock_df, spy_df, sector_df, mom_meta = await fetch_momentum_inputs(ticker, None)
        return analyze_momentum(stock_df, spy_df, sector_df, mom_meta)
    except Exception as e:
        logger.warning("[decision_terminal] momentum model unavailable for %s: %s", ticker, e)
        return None


def _valuation_ledger_features(verdict_payload: DecisionVerdictPayload) -> list:
    """Extract DCF V2 features (classification, implied metrics) for the ledger."""
    from .decision_ledger import FeatureValue

    feats: list = []
    panel = getattr(verdict_payload, "valuation", None)
    if panel is None:
        return feats
    classification = getattr(panel, "business_classification", None)
    if classification:
        feats.append(FeatureValue(name="business_classification", value_str=str(classification)))
    expectation = getattr(panel, "market_expectation", None)
    if expectation:
        feats.append(FeatureValue(name="market_expectation", value_str=str(expectation)))
    dcf_model = next((m for m in getattr(panel, "models", []) or [] if m.name == "DCF"), None)
    if dcf_model is not None:
        for attr in ("implied_growth", "implied_margin", "implied_roic", "margin_of_safety_pct"):
            val = getattr(dcf_model, attr, None)
            if val is not None:
                feats.append(FeatureValue(name=attr, value_num=float(val)))
        if dcf_model.fair_value_usd is not None:
            feats.append(FeatureValue(name="dcf_base_fair_value", value_num=float(dcf_model.fair_value_usd)))
    return feats


def _emit_verdict_ledger(ticker: str, verdict_payload: DecisionVerdictPayload) -> None:
    try:
        from . import decision_ledger as _dl
        from .decision_ledger_registry import registry_attribution

        _pv, _snap, _model = registry_attribution()
        verdict_panel = verdict_payload.verdict
        headline = verdict_panel.headline_verdict if verdict_panel is not None else ""
        _dl.emit_decision(
            features=_valuation_ledger_features(verdict_payload),
            decision_type="decision_terminal",
            symbol=ticker.upper(),
            horizon_hint="21d",
            verdict=str(headline or ""),
            confidence=None,
            output={
                "headline_verdict": headline,
                "debate_verdict": getattr(verdict_panel, "debate_verdict", ""),
                "swarm_verdict": getattr(verdict_panel, "swarm_verdict", ""),
                "generated_at_utc": verdict_payload.generated_at_utc,
            },
            source_route="backend/decision_terminal.py::run_decision_verdict_request",
            prompt_versions=_pv,
            registry_snapshot_id=_snap,
            model=_model,
        )
    except Exception as e:
        logger.debug("[decision_terminal] ledger emit skipped: %s", e)


async def run_decision_snapshot_request(
    ticker: str,
    *,
    tool_registry: Any,
    force: bool = False,
) -> DecisionSnapshotPayload:
    from .verdict_cache import (
        SLICE_SNAPSHOT,
        get_cached_slice,
        store_slice_cache,
        verdict_cache_enabled,
    )

    t = ticker.upper()
    if not force and verdict_cache_enabled():
        cached = get_cached_slice(SLICE_SNAPSHOT, t)
        if isinstance(cached, DecisionSnapshotPayload):
            return cached

    debate_data, ext, spot_quote, scorecard_summary, momentum_result = await asyncio.gather(
        tool_registry.invoke("fetch_debate_data", {"ticker": t}, timeout_s=90.0),
        asyncio.to_thread(_sync_extended_snapshot, t),
        asyncio.to_thread(_resolve_spot_for_terminal, t),
        _build_scorecard_for_terminal(t),
        _safe_momentum_fetch(t),
    )

    payload = build_snapshot_slice(
        t,
        debate_data,
        ext,
        momentum_readout=momentum_result,
        spot_quote=spot_quote,
        scorecard_summary=scorecard_summary,
    )
    if verdict_cache_enabled():
        store_slice_cache(SLICE_SNAPSHOT, t, payload)
    return payload


async def run_decision_verdict_request(
    ticker: str,
    credit_stress: Optional[float],
    auth_user: Any,
    *,
    execute_analyze,
    tool_registry: Any,
    poly_connector: Any,
    force: bool = False,
) -> DecisionVerdictPayload:
    from .verdict_cache import (
        SLICE_VERDICT,
        get_cached_slice,
        store_slice_cache,
        verdict_cache_enabled,
    )

    t = ticker.upper()
    if not force and verdict_cache_enabled():
        cached = get_cached_slice(SLICE_VERDICT, t)
        if isinstance(cached, DecisionVerdictPayload):
            return cached

    debate_data_task = asyncio.ensure_future(
        tool_registry.invoke("fetch_debate_data", {"ticker": t}, timeout_s=90.0)
    )
    try:
        analysis, poly_raw, debate_data = await asyncio.gather(
            execute_analyze(
                t,
                credit_stress,
                auth_user,
                award_deep_analysis_xp=False,
                debate_data_task=debate_data_task,
            ),
            _safe_poly_fetch(poly_connector, t),
            debate_data_task,
        )
    except BaseException:
        if not debate_data_task.done():
            debate_data_task.cancel()
        else:
            try:
                debate_data_task.exception()
            except BaseException:
                pass
        raise

    payload = await build_verdict_slice(
        t,
        analysis.swarm,
        analysis.debate,
        debate_data,
        poly_raw,
        macro_fetched_at_utc=analysis.macro_fetched_at_utc,
    )
    if verdict_cache_enabled():
        store_slice_cache(SLICE_VERDICT, t, payload)
    _emit_verdict_ledger(t, payload)
    return payload


async def run_decision_roadmap_request(
    ticker: str,
    *,
    tool_registry: Any,
    force: bool = False,
) -> DecisionRoadmapPayload:
    from .verdict_cache import (
        SLICE_ROADMAP,
        get_cached_slice,
        store_slice_cache,
        verdict_cache_enabled,
    )

    t = ticker.upper()
    if not force and verdict_cache_enabled():
        cached = get_cached_slice(SLICE_ROADMAP, t)
        if isinstance(cached, DecisionRoadmapPayload):
            return cached

    debate_data, spot_quote = await asyncio.gather(
        tool_registry.invoke("fetch_debate_data", {"ticker": t}, timeout_s=90.0),
        asyncio.to_thread(_resolve_spot_for_terminal, t),
    )
    resolved = _resolve_terminal_spot(spot_quote=spot_quote, debate_data=debate_data, ext={})
    payload = await build_roadmap_slice(
        t,
        resolved.price_f,
        tool_registry=tool_registry,
    )
    if verdict_cache_enabled():
        store_slice_cache(SLICE_ROADMAP, t, payload)
    return payload


async def run_decision_terminal_request(
    ticker: str,
    credit_stress: Optional[float],
    auth_user: Any,
    *,
    execute_analyze,
    tool_registry: Any,
    poly_connector: Any,
    llm_client: Any,
    provider_audit: bool = False,
    force: bool = False,
) -> DecisionTerminalPayload:
    """
    Aggregator: run snapshot, verdict, and roadmap slices in parallel, then merge.

    ``execute_analyze`` must be ``_execute_analyze`` from analysis router (injected).
    """
    _ = llm_client
    from .verdict_cache import get_cached_verdict, verdict_cache_enabled

    t = ticker.upper()
    if not force and verdict_cache_enabled():
        cached = get_cached_verdict(t)
        if cached is not None:
            return cached

    snapshot, verdict, roadmap = await asyncio.gather(
        run_decision_snapshot_request(t, tool_registry=tool_registry, force=force),
        run_decision_verdict_request(
            t,
            credit_stress,
            auth_user,
            execute_analyze=execute_analyze,
            tool_registry=tool_registry,
            poly_connector=poly_connector,
            force=force,
        ),
        run_decision_roadmap_request(t, tool_registry=tool_registry, force=force),
    )

    debate_data = None
    poly_raw = None
    resolved = None
    if provider_audit:
        debate_data, ext, spot_quote = await asyncio.gather(
            tool_registry.invoke("fetch_debate_data", {"ticker": t}, timeout_s=90.0),
            asyncio.to_thread(_sync_extended_snapshot, t),
            asyncio.to_thread(_resolve_spot_for_terminal, t),
        )
        poly_raw = await _safe_poly_fetch(poly_connector, t)
        resolved = _resolve_terminal_spot(spot_quote=spot_quote, debate_data=debate_data, ext=ext)

    return assemble_terminal_from_slices(
        snapshot,
        verdict,
        roadmap,
        verdict_from_cache=bool(
            snapshot.slice_from_cache or verdict.slice_from_cache or roadmap.slice_from_cache
        ),
        include_provider_audit=provider_audit,
        debate_data=debate_data,
        poly_raw=poly_raw,
        resolved=resolved,
    )
