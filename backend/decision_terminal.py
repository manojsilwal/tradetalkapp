"""
Assemble the K2 Investor Decision Terminal view-model from swarm + debate analysis,
market data, and optional LLM scenario prices. See DecisionTerminalPayload in schemas.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .schemas import (
    DebateResult,
    DecisionTerminalPayload,
    SwarmConsensus,
    TerminalFieldProvenance,
    TerminalQualityPanel,
    TerminalQualityRow,
    TerminalRoadmapPanel,
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

_EQUITY_CONTEXT_TERMS = (
    "stock",
    "stocks",
    "share",
    "shares",
    "equity",
    "earnings",
    "eps",
    "revenue",
    "guidance",
    "market cap",
    "ipo",
    "split",
    "dividend",
    "nasdaq",
    "nyse",
    "s&p",
    "buyout",
    "merger",
    "acquisition",
    "takeover",
    "valuation",
    "price target",
)

_NOISE_TERMS = (
    "election",
    "president",
    "senate",
    "house of representatives",
    "governor",
    "poll",
    "presidential",
    "oscar",
    "super bowl",
    "world cup",
)


def score_polymarket_relevance(
    title: str,
    description: str,
    ticker: str,
    company_tokens: List[str],
) -> float:
    """
    0–1 score: require company/ticker anchor plus equity-ish language; penalize political/sports noise.
    """
    blob = f"{title} {description}".lower()
    t = ticker.upper()
    score = 0.0
    toks = [x for x in company_tokens if x and len(x) >= 2]
    for tok in toks:
        if tok.lower() in blob:
            score += 0.34
            break
    if t.lower() in blob:
        score += 0.22
    for term in _EQUITY_CONTEXT_TERMS:
        if term in blob:
            score += 0.06
    for term in _NOISE_TERMS:
        if term in blob:
            score -= 0.2
    return max(0.0, min(1.0, score))


def _company_tokens_from_debate_data(dd: dict) -> List[str]:
    name = str(dd.get("company_name") or "")
    parts = re.split(r"[^\w]+", name)
    return [p for p in parts if len(p) >= 2][:4]


def _sync_extended_snapshot(ticker: str) -> dict:
    """Extra yfinance info fields for quality + valuation heuristics."""
    try:
        import yfinance as yf

        t = yf.Ticker(ticker.upper())
        info = t.info or {}
        return {
            "currentRatio": info.get("currentRatio"),
            "totalDebt": info.get("totalDebt"),
            "ebitda": info.get("ebitda"),
            "trailingEps": info.get("trailingEps"),
            "bookValue": info.get("bookValue"),
            "returnOnEquity": info.get("returnOnEquity"),
            "grossMargins": info.get("grossMargins"),
            "freeCashflow": info.get("freeCashflow"),
            "regularMarketPrice": info.get("regularMarketPrice"),
            "currentPrice": info.get("currentPrice"),
            "previousClose": info.get("previousClose")
            or info.get("regularMarketPreviousClose"),
            "longName": info.get("longName") or info.get("shortName") or ticker.upper(),
        }
    except Exception as e:
        logger.warning("[decision_terminal] extended snapshot failed %s: %s", ticker, e)
        return {}


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


def _graham_fair_value(eps: float, book_per_share: float) -> Optional[float]:
    if eps and eps > 0 and book_per_share and book_per_share > 0:
        return float(math.sqrt(22.5 * eps * book_per_share))
    return None


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


def _format_usd_compact(n: Optional[float]) -> str:
    if n is None:
        return "N/A"
    x = float(n)
    ax = abs(x)
    if ax >= 1e9:
        return f"${x/1e9:.2f}B"
    if ax >= 1e6:
        return f"${x/1e6:.2f}M"
    if ax >= 1e3:
        return f"${x/1e3:.2f}K"
    return f"${x:.2f}"


def _moat_heuristic(roe_pct: float, gross_margin_pct: float) -> Tuple[str, str]:
    if roe_pct >= 18 and gross_margin_pct >= 0.22:
        return "Wide (heuristic)", "Strong"
    if roe_pct >= 12 and gross_margin_pct >= 0.15:
        return "Narrow (heuristic)", "Moderate"
    return "Limited (heuristic)", "Weak"


def _expert_bullish_pct(debate: DebateResult) -> float:
    s = debate.bull_score + debate.bear_score + debate.neutral_score
    stance_pct = 100.0 * debate.bull_score / s if s else 50.0
    conf_pct = float(debate.consensus_confidence) * 100.0
    return round(0.5 * stance_pct + 0.5 * conf_pct, 1)


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


def _heuristic_roadmap(current_price: float, hist_cagr_3y: Optional[float] = None) -> Tuple[float, float, float, float, List[str]]:
    if hist_cagr_3y is not None:
        cagr_factor = hist_cagr_3y / 100.0
        b = current_price * pow(1.0 + cagr_factor, 3.0)
        u = b * 1.2
        e = current_price * pow(1.0 + min(0.0, cagr_factor - 0.05), 3.0)
        asm_txt = f"Base case tied to historical 3Y CAGR ({hist_cagr_3y:.1f}%)."
        cagr_b = hist_cagr_3y
    else:
        b = current_price * 1.12
        u = current_price * 1.36
        e = current_price * 0.82
        cagr_b = (pow(b / current_price, 1.0 / 3.0) - 1.0) * 100.0
        asm_txt = "Base case ≈ +12% cumulative over 3Y (heuristic placeholder)."
        
    assumptions = [
        asm_txt,
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
            "extended_snapshot_for_multiples_and_quality": "yfinance_ticker_info",
            "fair_value_models": {
                "DCF": "not_implemented",
                "Graham": "yfinance",
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
    include_provider_audit: bool = False,
) -> DecisionTerminalPayload:
    t = ticker.upper()
    now = datetime.now(timezone.utc).isoformat()
    debate_spot_price_source = debate_data.get("spot_price_source")
    filled_spot_from_ext = False
    price = debate_data.get("current_price")
    try:
        price_f = float(price) if price is not None else None
    except (TypeError, ValueError):
        price_f = None
    if price_f is not None and price_f <= 0:
        price_f = None

    market_data_degraded = bool(debate_data.get("market_data_degraded"))
    spot_price_source = debate_spot_price_source

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

    hist_quality = _get_historical_quality_metrics(t)
    hist_cagr = _get_historical_cagr_3y(t)

    roe_val = debate_data.get("roe")
    if roe_val is None and hist_quality.get("roe") is not None:
        roe_val = hist_quality.get("roe") * 100.0
    roe_pct = float(roe_val or 0.0)

    gross_m_val = debate_data.get("gross_margins")
    if gross_m_val is None and hist_quality.get("gross_margin") is not None:
        gross_m_val = hist_quality.get("gross_margin") * 100.0
    gross_m = float(gross_m_val or 0.0)

    trailing_eps = ext.get("trailingEps") or None
    if trailing_eps is not None:
        trailing_eps = float(trailing_eps)
    book_ps = ext.get("bookValue")
    if book_ps is not None:
        book_ps = float(book_ps)

    pe = debate_data.get("pe_ratio")
    pe_f = float(pe) if pe is not None else None

    gfv = _graham_fair_value(trailing_eps or 0.0, book_ps or 0.0)
    if gfv is None and trailing_eps and book_ps:
        gfv = _graham_fair_value(trailing_eps, book_ps)

    mfv = None
    if price_f:
        mfv = _multiples_heuristic_fair_price(trailing_eps, roe_pct, price_f, pe_f)

    models: List[TerminalValuationModel] = [
        TerminalValuationModel(
            name="DCF",
            fair_value_usd=None,
            available=False,
            provenance=TerminalFieldProvenance(
                source="not_implemented",
                missing_reason="No deterministic DCF engine; add assumptions-backed module before showing a DCF fair value.",
                formula_or_note="Deferred per product policy — avoid LLM-only DCF.",
            ),
        ),
        TerminalValuationModel(
            name="Graham",
            fair_value_usd=gfv,
            available=gfv is not None,
            provenance=TerminalFieldProvenance(
                source="yfinance",
                formula_or_note="sqrt(22.5 × trailing EPS × book value per share) — Benjamin Graham number.",
                missing_reason=None if gfv is not None else "Need positive trailing EPS and book value on yfinance.",
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
        panel_note="Average uses Graham + Multiples only when both are available; DCF omitted intentionally.",
    )

    fcf = ext.get("freeCashflow") or debate_data.get("free_cashflow") or hist_quality.get("freeCashflow")
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

    gm_ratio = (gross_m / 100.0) if gross_m and gross_m > 1.0 else float(gross_m or 0.0)
    moat_lab, moat_st = _moat_heuristic(roe_pct, gm_ratio)

    roic_proxy = round(roe_pct * 0.8, 1)
    quality = TerminalQualityPanel(
        rows=[
            TerminalQualityRow(
                id="roic",
                label="ROIC (proxy)",
                value_label=f"{roic_proxy}%",
                status_label="See note" if roe_pct else "N/A",
                provenance=TerminalFieldProvenance(
                    source="heuristic",
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
    best_ev = None
    best_score = 0.0
    for ev in events:
        title = ev.get("title") or ""
        desc = ev.get("description") or ""
        sc = score_polymarket_relevance(title, desc, t, tokens + [t])
        if sc > best_score:
            best_score = sc
            best_ev = ev

    pm_pct = None
    pm_title = None
    gated_out = True
    if best_ev and best_score >= 0.45:
        prob = best_ev.get("probability")
        if prob is not None:
            pm_pct = round(float(prob) * 100.0, 1)
            pm_title = best_ev.get("title")
            gated_out = False

    headline, fusion_note = _fuse_headline_verdict(swarm, debate)
    if _swarm_rejection_present(swarm) and "capped" not in fusion_note.lower():
        fusion_note = (fusion_note + " One or more swarm factors were REJECTED.").strip()

    verdict = TerminalVerdictPanel(
        headline_verdict=headline,
        debate_verdict=debate.verdict,
        swarm_verdict=swarm.global_verdict,
        fusion_note=fusion_note,
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

    if price_f and price_f > 0:
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
            roadmap_prov.source = "heuristic"
            roadmap_prov.confidence = 0.25
            roadmap_prov.formula_or_note = "Symmetric bands or historical CAGR when LLM JSON unavailable."

    roadmap = TerminalRoadmapPanel(
        bull_price_usd=bull_p,
        base_price_usd=base_p,
        bear_price_usd=bear_p,
        predicted_cagr_base_pct=cagr_b,
        assumptions=assumptions,
        confidence_0_1=conf_r,
        used_heuristic_fallback=heuristic_fb,
        provenance=roadmap_prov,
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

    return _decision_terminal_payload_json_safe(
        DecisionTerminalPayload(
            ticker=t,
            disclaimer=DISCLAIMER,
            generated_at_utc=now,
            cache_ttl_seconds=300,
            valuation=valuation,
            quality=quality,
            verdict=verdict,
            roadmap=roadmap,
            market_data_degraded=market_data_degraded,
            spot_price_source=spot_price_source,
            provider_audit=provider_audit,
        )
    )


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
) -> DecisionTerminalPayload:
    """
    Run full analyze (swarm + debate) in parallel with extra market fetches, then assemble payload.

    ``execute_analyze`` must be ``_execute_analyze`` from main (injected to avoid cycles).
    """
    t = ticker.upper()

    async def _safe_poly():
        try:
            return await poly_connector.fetch_data(ticker=t)
        except Exception as e:
            logger.warning("[decision_terminal] polymarket fetch failed: %s", e)
            return {"events": [], "ticker": t, "has_relevant_data": False}

    analysis, debate_data, poly_raw, ext = await asyncio.gather(
        execute_analyze(t, credit_stress, auth_user, award_deep_analysis_xp=False),
        tool_registry.invoke("fetch_debate_data", {"ticker": t}, timeout_s=90.0),
        _safe_poly(),
        asyncio.to_thread(_sync_extended_snapshot, t),
    )

    return await build_decision_terminal_payload(
        t,
        analysis.swarm,
        analysis.debate,
        debate_data,
        poly_raw,
        ext,
        llm_client,
        include_provider_audit=provider_audit,
    )
