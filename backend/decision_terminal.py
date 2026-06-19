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
    DebateResult,
    DecisionTerminalPayload,
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
) -> Optional[float]:
    """
    Growth- and quality-adjusted target P/E × EPS — heuristic, not peer medians.
    """
    if not trailing_eps or trailing_eps <= 0:
        return None
    base_pe = 12.0
    adj = min(14.0, max(0.0, roe_pct / 3.0))
    target_pe = min(28.0, max(10.0, base_pe + adj))
    if trailing_pe and trailing_pe > 0 and current_price > 0:
        pe_norm = min(1.15, max(0.85, 18.0 / trailing_pe))
        target_pe *= pe_norm
    return round(trailing_eps * target_pe, 2)


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
    t = ticker.upper()
    now = datetime.now(timezone.utc).isoformat()
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
    hist_cagr = _get_historical_cagr_3y(t)
    hist_quality = _get_historical_quality_metrics(t)

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

    mfv = None
    if price_f:
        mfv = _multiples_heuristic_fair_price(trailing_eps, roe_pct, price_f, pe_f)

    from .valuation_inputs import compute_dcf_scenarios, owner_earnings_fcf

    dcf_result = compute_dcf_scenarios(
        ext,
        hist_cagr_pct=hist_cagr,
        price_usd=price_f,
    )
    dcf_price = dcf_result.get("base_fair_value_usd")
    dcf_scenarios = dcf_result.get("scenarios") or {}
    fcf_for_quality, _fcf_src = owner_earnings_fcf(ext)

    def _dcf_provenance_note() -> str:
        if not dcf_result.get("available"):
            return dcf_result.get("missing_reason") or "Insufficient DCF inputs."
        bear = dcf_scenarios.get("bear")
        bull = dcf_scenarios.get("bull")
        parts = [
            "Owner-earnings DCF (OCF−capex with statement fallbacks), declining 5Y FCF growth, "
            f"CAPM WACC {dcf_result.get('wacc_base', 0):.1%}, terminal 2.5%, net cash added.",
            f"FCF source: {dcf_result.get('fcf_source')}; "
            f"net cash: ${dcf_result.get('net_cash_usd', 0) / 1e9:.1f}B ({dcf_result.get('net_cash_source')}).",
        ]
        if bear is not None and bull is not None and dcf_price is not None:
            parts.append(
                f"Scenario range: bear ${bear:.0f} · base ${dcf_price:.0f} · bull ${bull:.0f}."
            )
        return " ".join(parts)

    models: List[TerminalValuationModel] = [
        TerminalValuationModel(
            name="DCF",
            fair_value_usd=dcf_price,
            available=bool(dcf_result.get("available")),
            scenarios={
                k: float(v)
                for k, v in dcf_scenarios.items()
                if v is not None
            }
            or None,
            provenance=TerminalFieldProvenance(
                source="owner_earnings_dcf",
                confidence=0.55,
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
                formula_or_note="Target P/E anchored at 12–28 from ROE, scaled by trailing P/E vs ~18 — illustrative only.",
                missing_reason=None if mfv is not None else "Insufficient EPS for multiples fair value.",
            ),
        ),
    ]

    usable = [m.fair_value_usd for m in models if m.available and m.fair_value_usd is not None]
    avg_fair = round(sum(usable) / len(usable), 2) if usable else None
    pct_vs = None
    gauge_label = ""
    if avg_fair and price_f and avg_fair > 0:
        pct_vs = round((avg_fair - price_f) / avg_fair * 100.0, 2)
        if pct_vs > 1.0:
            gauge_label = f"+{pct_vs:.2f}% UNDERVALUED"
        elif pct_vs < -1.0:
            gauge_label = f"{pct_vs:.2f}% OVERVALUED"
        else:
            gauge_label = "NEAR FAIR VALUE"

    valuation = TerminalValuationPanel(
        current_price_usd=price_f,
        average_fair_value_usd=avg_fair,
        pct_vs_average=pct_vs,
        gauge_label=gauge_label or ("N/A" if price_f is None else "INSUFFICIENT MODEL INPUTS"),
        models=models,
        panel_note=(
            "Average uses DCF (base case) and Multiples when available. "
            "Momentum is a 0–100 score (not USD fair value). "
            "DCF shows bear/base/bull in model provenance."
        ),
    )

    fcf = fcf_for_quality or ext.get("freeCashflow") or debate_data.get("free_cashflow") or hist_quality.get("freeCashflow")
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
        ]
    )

    tokens = _company_tokens_from_debate_data(debate_data)
    events = poly_raw.get("events") or []
    gated = select_gated_polymarket_event(events, t, tokens + [t])
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

    roadmap_prov = TerminalFieldProvenance(source="llm_or_heuristic", confidence=0.35)
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
                from .predictor.agent import run_predictor_forecast

                pred = await run_predictor_forecast(
                    t,
                    horizons=["1d", "5d", "21d", "63d"],
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
                    conf_r = {"high": 0.72, "medium": 0.55, "low": 0.38}.get(pred.model_confidence, 0.55)
                    heuristic_fb = False
                    predictor_filled = True
                    roadmap_prov.source = pred.model_version or "predictor"
                    roadmap_prov.confidence = conf_r
                    roadmap_prov.formula_or_note = (
                        "Probabilistic predictor (baselines + TimesFM path); 3Y scenarios extrapolated from horizon bands."
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
            from .deps import knowledge_store
            stock_profile = knowledge_store.query_stock_profile(t)
            earnings_memory = knowledge_store.query_earnings_memory(t)
            fundamentals_n = knowledge_store.query_sp500_fundamentals(t)

            ctx = {
                "ticker": t,
                "current_price": price_f,
                "historical_cagr_3y": hist_cagr,
                "debate_verdict": debate.verdict,
                "swarm_verdict": swarm.global_verdict,
                "valuation_avg_fair": avg_fair,
                "pct_vs_average": pct_vs,
                "bull_score": debate.bull_score,
                "bear_score": debate.bear_score,
                "moderator_summary": debate.moderator_summary,
                "stock_profile": stock_profile,
                "earnings_memory": "\n".join(earnings_memory) if isinstance(earnings_memory, list) else earnings_memory,
                "fundamentals_narrative": fundamentals_n,
            }
            try:
                rm = await llm_client.generate_decision_terminal_roadmap(t, ctx)
                bull_p = rm.get("bull_price_usd") or rm.get("bull_price")
                base_p = rm.get("base_price_usd") or rm.get("base_price")
                bear_p = rm.get("bear_price_usd") or rm.get("bear_price")
                assumptions = [str(x) for x in (rm.get("assumptions") or [])][:6]
                conf_r = float(rm.get("confidence_0_1") or 0.0)
                if bull_p and base_p and bear_p:
                    bull_p, base_p, bear_p = float(bull_p), float(base_p), float(bear_p)
                    if base_p > 0 and price_f > 0:
                        cagr_b = round((pow(base_p / price_f, 1.0 / 3.0) - 1.0) * 100.0, 2)
                    heuristic_fb = bool(rm.get("used_heuristic_fallback", False))
                    roadmap_prov.source = "llm_json"
                    roadmap_prov.confidence = conf_r
            except Exception as e:
                logger.warning("[decision_terminal] roadmap LLM failed: %s", e)

            if not (bull_p and base_p and bear_p):
                u, b, e, cg, asm = _heuristic_roadmap(price_f, hist_cagr)
                bull_p, base_p, bear_p, cagr_b = u, b, e, cg
                assumptions = asm
                heuristic_fb = True
                if bull_p is not None:
                    roadmap_prov.source = "heuristic"
                    roadmap_prov.confidence = 0.25
                    roadmap_prov.formula_or_note = "Historical 3Y CAGR heuristic when LLM JSON unavailable."
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

    roadmap = TerminalRoadmapPanel(
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

    provider_audit: Optional[Dict[str, Any]] = None
    if include_provider_audit:
        provider_audit = _build_provider_audit(
            ticker=t,
            debate_data=debate_data,
            poly_raw=poly_raw,
            debate_spot_price_source=debate_spot_price_source
            if isinstance(debate_spot_price_source, str)
            else None,
            terminal_spot_price_source=spot_price_source
            if isinstance(spot_price_source, str)
            else None,
            market_data_degraded=market_data_degraded,
            filled_spot_from_ext=filled_spot_from_ext,
            hist_cagr_present=hist_cagr is not None,
            hist_quality_nonempty=bool(hist_quality),
            roadmap=roadmap,
        )

    reconciliation = None
    if os.environ.get("RECONCILIATION_ENABLE", "1").strip().lower() not in ("0", "false", "no"):
        reconciliation = build_reconciliation(
            headline_verdict=headline,
            fusion_note=fusion_note,
            pct_vs_average=pct_vs,
            gauge_label=gauge_label,
            predicted_cagr_base_pct=cagr_b,
            swarm_rejected=_swarm_rejection_present(swarm),
            scorecard_summary=scorecard_summary,
        )

    return _decision_terminal_payload_json_safe(
        DecisionTerminalPayload(
            ticker=t,
            disclaimer=DISCLAIMER,
            generated_at_utc=now,
            cache_ttl_seconds=300,
            verdict_captured_at_utc=verdict_captured_at_utc or now,
            verdict_from_cache=verdict_from_cache,
            macro_fetched_at_utc=macro_fetched_at_utc,
            valuation=valuation,
            quality=quality,
            verdict=verdict,
            roadmap=roadmap,
            market_data_degraded=market_data_degraded,
            spot_price_source=spot_price_source,
            provider_audit=provider_audit,
            swarm=swarm,
            debate=debate,
            data_freshness=_terminal_data_freshness(spot_price_source, market_data_degraded, now),
            spot=spot_envelope,
            scorecard_summary=scorecard_summary,
            reconciliation=reconciliation,
        )
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
    Run full analyze (swarm + debate) in parallel with extra market fetches, then assemble payload.

    ``execute_analyze`` must be ``_execute_analyze`` from main (injected to avoid cycles).
    """
    from .verdict_cache import get_cached_verdict, store_verdict_cache, verdict_cache_enabled

    t = ticker.upper()

    if not force and verdict_cache_enabled():
        cached = get_cached_verdict(t)
        if cached is not None:
            return cached

    async def _safe_poly():
        # Truthful-data contract: a failed Polymarket fetch must not be
        # presented as "no relevant markets" — propagate insufficient data.
        from .data_errors import InsufficientDataError

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

    # Fetch debate market data once, then reuse it for both the debate pipeline
    # (inside execute_analyze) and the payload assembly below — previously this
    # data was fetched twice per decision-terminal request. Start it as a task
    # so the network fetch overlaps with the swarm phase, the Polymarket fetch,
    # and the extended snapshot instead of blocking ahead of them.
    debate_data_task = asyncio.ensure_future(
        tool_registry.invoke("fetch_debate_data", {"ticker": t}, timeout_s=90.0)
    )
    # Start momentum fetch concurrently so it has the full ~90s window
    async def _safe_momentum() -> Optional[Dict[str, Any]]:
        try:
            from .connectors.momentum_data import fetch_momentum_inputs
            from .momentum_model import analyze_momentum
            
            # Use info=None so it fetches what it needs directly from yfinance
            stock_df, spy_df, sector_df, mom_meta = await fetch_momentum_inputs(t, None)
            return analyze_momentum(stock_df, spy_df, sector_df, mom_meta)
        except Exception as e:
            logger.warning("[decision_terminal] momentum model unavailable for %s: %s", t, e)
            return None

    momentum_task = asyncio.ensure_future(_safe_momentum())

    try:
        analysis, poly_raw, ext, spot_quote, scorecard_summary, momentum_result = await asyncio.gather(
            execute_analyze(
                t, credit_stress, auth_user,
                award_deep_analysis_xp=False, debate_data_task=debate_data_task,
            ),
            _safe_poly(),
            asyncio.to_thread(_sync_extended_snapshot, t),
            asyncio.to_thread(_resolve_spot_for_terminal, t),
            _build_scorecard_for_terminal(t),
            momentum_task,
        )
    except BaseException:
        # Avoid a "Task was destroyed but it is pending" warning if a sibling
        # (e.g. swarm) fails before the debate phase awaits the fetch; if the
        # fetch already finished with an error, retrieve it so it is not later
        # reported as "exception never retrieved".
        if not debate_data_task.done():
            debate_data_task.cancel()
        else:
            try:
                debate_data_task.exception()
            except BaseException:
                pass
        raise

    # execute_analyze's debate phase already awaited this task, so it is done;
    # awaiting again just returns the cached fetched dict for payload assembly.
    debate_data = await debate_data_task

    payload = await build_decision_terminal_payload(
        t,
        analysis.swarm,
        analysis.debate,
        debate_data,
        poly_raw,
        ext,
        llm_client,
        momentum_readout=momentum_task.result(),
        include_provider_audit=provider_audit,
        tool_registry=tool_registry,
        spot_quote=spot_quote,
        scorecard_summary=scorecard_summary,
        macro_fetched_at_utc=analysis.macro_fetched_at_utc,
    )

    if verdict_cache_enabled():
        store_verdict_cache(t, payload)

    try:
        from . import decision_ledger as _dl
        from .decision_ledger_registry import registry_attribution

        _pv, _snap, _model = registry_attribution()
        verdict_panel = payload.verdict
        headline = (
            verdict_panel.headline_verdict if verdict_panel is not None else ""
        )
        momentum_out: Dict[str, Any] = {}
        for m in payload.valuation.models:
            if m.name == "Momentum" and m.momentum_summary:
                momentum_out = {
                    "momentum_pricing_score": m.momentum_summary.get("momentum_pricing_score"),
                    "momentum_classification": m.momentum_summary.get("classification"),
                    "downside_exposure_score": m.momentum_summary.get("downside_exposure_score"),
                    "crash_risk": m.momentum_summary.get("crash_risk"),
                    "decision_quality_score": m.momentum_summary.get("decision_quality_score"),
                }
                break
        _dl.emit_decision(
            decision_type="decision_terminal",
            symbol=t,
            horizon_hint="21d",
            verdict=str(headline or ""),
            confidence=None,
            output={
                "headline_verdict": headline,
                "debate_verdict": getattr(verdict_panel, "debate_verdict", ""),
                "swarm_verdict": getattr(verdict_panel, "swarm_verdict", ""),
                "market_data_degraded": payload.market_data_degraded,
                "reconciliation_note": (
                    payload.reconciliation.reconciliation_note
                    if payload.reconciliation
                    else ""
                ),
                "generated_at_utc": payload.generated_at_utc,
                **momentum_out,
            },
            source_route="backend/decision_terminal.py::run_decision_terminal_request",
            prompt_versions=_pv,
            registry_snapshot_id=_snap,
            model=_model,
        )
    except Exception as e:
        logger.debug("[decision_terminal] ledger emit skipped: %s", e)

    return payload
