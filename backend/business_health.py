"""Deterministic fundamental health assessments for quality + financial health UI."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .schemas import (
    FundamentalHealthPanel,
    MetricHealthAssessment,
    TerminalQualityPanel,
    TerminalQualityRow,
)


@dataclass
class _Assessment:
    tone: str
    label: str
    detail: str = ""

    def to_model(self) -> MetricHealthAssessment:
        return MetricHealthAssessment(tone=self.tone, label=self.label, detail=self.detail)


def _is_bearish_regime(market_regime: Optional[str]) -> bool:
    r = (market_regime or "").upper()
    return r in ("BEAR_STRESS", "BEAR_NORMAL", "K_SHAPE_DIVERGENCE")


def _macro_note(market_regime: Optional[str]) -> str:
    regime = (market_regime or "BULL_NORMAL").upper()
    if regime == "BEAR_STRESS":
        return (
            "Macro regime is stressed — balance sheet and cash-flow thresholds are tighter "
            "than in calm markets."
        )
    if regime in ("BEAR_NORMAL", "K_SHAPE_DIVERGENCE"):
        return (
            "Macro backdrop is cautious — leverage and cash generation carry more weight "
            "in this assessment."
        )
    if regime == "BULL_EXCESS":
        return (
            "Macro regime is risk-on — growth and valuation stretch are weighed alongside "
            "balance-sheet durability."
        )
    return "Macro regime is neutral — standard fundamental thresholds apply."


def assess_roic_proxy(roic_pct: Optional[float]) -> _Assessment:
    if roic_pct is None:
        return _Assessment("neutral", "N/A", "ROIC proxy unavailable.")
    if roic_pct >= 15:
        return _Assessment("positive", "Strong", "ROIC proxy indicates durable returns on capital.")
    if roic_pct >= 8:
        return _Assessment("neutral", "Adequate", "Returns on capital are acceptable but not elite.")
    if roic_pct >= 0:
        return _Assessment("caution", "Watch", "Low return on capital — competitive advantage may be limited.")
    return _Assessment("negative", "Weak", "Negative ROIC proxy — capital may be misallocated.")


def assess_moat_status(moat_status: str) -> _Assessment:
    st = (moat_status or "").lower()
    if "strong" in st:
        return _Assessment("positive", "Strong", "Wide moat heuristic from ROE and gross margin.")
    if "moderate" in st:
        return _Assessment("neutral", "Adequate", "Narrow moat — some pricing power signals.")
    if st in ("weak", "limited"):
        return _Assessment("caution", "Watch", "Limited moat — margins and ROE do not support a wide advantage.")
    return _Assessment("neutral", "N/A", "Moat assessment unavailable.")


def assess_fcf_level(
    fcf_usd: Optional[float],
    market_cap: Optional[float],
    *,
    bearish: bool,
) -> _Assessment:
    if fcf_usd is None:
        return _Assessment("neutral", "N/A", "Free cash flow not reported.")
    if fcf_usd < 0:
        return _Assessment("negative", "Stressed", "Negative free cash flow — business is burning cash.")
    yield_pct: Optional[float] = None
    if market_cap and market_cap > 0:
        yield_pct = fcf_usd / market_cap * 100.0
    if yield_pct is not None:
        healthy = 4.5 if bearish else 4.0
        adequate = 2.5 if bearish else 2.0
        watch = 0.5 if bearish else 0.0
        if yield_pct >= healthy:
            return _Assessment(
                "positive",
                "Healthy",
                f"FCF yield ~{yield_pct:.1f}% — strong cash generation vs market cap.",
            )
        if yield_pct >= adequate:
            return _Assessment(
                "neutral",
                "Adequate",
                f"FCF yield ~{yield_pct:.1f}% — positive but not standout.",
            )
        if yield_pct >= watch:
            return _Assessment(
                "caution",
                "Watch",
                f"FCF yield ~{yield_pct:.1f}% — thin cash return on equity value.",
            )
        return _Assessment(
            "negative",
            "Stressed",
            f"FCF yield ~{yield_pct:.1f}% — weak cash generation relative to size.",
        )
    return _Assessment("neutral", "Adequate", "Positive TTM free cash flow.")


def assess_leverage(
    debt_to_ebitda: Optional[float],
    *,
    bearish: bool,
) -> _Assessment:
    if debt_to_ebitda is None:
        return _Assessment("neutral", "N/A", "Debt/EBITDA unavailable.")
    healthy = 2.0 if bearish else 2.5
    watch = 3.5 if bearish else 4.0
    if debt_to_ebitda < healthy:
        return _Assessment("positive", "Healthy", f"Debt/EBITDA {debt_to_ebitda:.1f}× — manageable leverage.")
    if debt_to_ebitda < watch:
        return _Assessment("caution", "Watch", f"Debt/EBITDA {debt_to_ebitda:.1f}× — leverage warrants monitoring.")
    return _Assessment("negative", "Stressed", f"Debt/EBITDA {debt_to_ebitda:.1f}× — elevated leverage risk.")


def assess_gross_margin(gross_pct: Optional[float]) -> _Assessment:
    if gross_pct is None:
        return _Assessment("neutral", "N/A", "Gross margin unavailable.")
    if gross_pct >= 40:
        return _Assessment("positive", "Healthy", "High gross margin supports pricing power.")
    if gross_pct >= 25:
        return _Assessment("neutral", "Adequate", "Gross margin is solid for most industries.")
    if gross_pct >= 15:
        return _Assessment("caution", "Watch", "Thin gross margin — limited room before cost pressure.")
    return _Assessment("negative", "Stressed", "Very thin gross margin — weak pricing power.")


def assess_current_ratio(cr: Optional[float]) -> _Assessment:
    if cr is None:
        return _Assessment("neutral", "N/A", "Current ratio unavailable.")
    if cr >= 1.5:
        return _Assessment("positive", "Healthy", "Strong short-term liquidity.")
    if cr >= 1.0:
        return _Assessment("neutral", "Adequate", "Current assets cover near-term liabilities.")
    return _Assessment("caution", "Watch", "Current ratio below 1 — liquidity pressure possible.")


def assess_pe(trailing_pe: Optional[float], forward_pe: Optional[float]) -> _Assessment:
    if trailing_pe is None and forward_pe is None:
        return _Assessment("neutral", "N/A", "P/E unavailable.")
    pe = forward_pe if forward_pe is not None else trailing_pe
    if pe is None or pe <= 0:
        return _Assessment("caution", "Watch", "P/E not meaningful (losses or missing data).")
    stretch = None
    if trailing_pe and forward_pe and trailing_pe > 0:
        stretch = (forward_pe - trailing_pe) / trailing_pe * 100.0
    if pe <= 18:
        label = "Attractive"
        tone = "positive"
        detail = f"P/E ~{pe:.1f} — valuation multiple is moderate."
    elif pe <= 35:
        label = "Fair"
        tone = "neutral"
        detail = f"P/E ~{pe:.1f} — growth may justify the multiple."
    else:
        label = "Stretched"
        tone = "caution"
        detail = f"P/E ~{pe:.1f} — elevated multiple; expectations are high."
    if stretch is not None and stretch < -10:
        detail += " Forward P/E below trailing — earnings expected to improve."
    return _Assessment(tone, label, detail)


def assess_ev_ebitda(ev_ebitda: Optional[float]) -> _Assessment:
    if ev_ebitda is None:
        return _Assessment("neutral", "N/A", "EV/EBITDA unavailable.")
    if ev_ebitda <= 12:
        return _Assessment("positive", "Attractive", f"EV/EBITDA {ev_ebitda:.1f}× — reasonable enterprise value.")
    if ev_ebitda <= 20:
        return _Assessment("neutral", "Fair", f"EV/EBITDA {ev_ebitda:.1f}× — typical for quality growers.")
    return _Assessment("caution", "Stretched", f"EV/EBITDA {ev_ebitda:.1f}× — rich vs cash earnings.")


def assess_growth_rate(
    rate_decimal: Optional[float],
    *,
    bearish: bool,
    label: str,
) -> _Assessment:
    if rate_decimal is None:
        return _Assessment("neutral", "N/A", f"{label} unavailable.")
    pct = rate_decimal * 100.0
    if pct >= 15:
        return _Assessment("positive", "Strong", f"{label} +{pct:.1f}% — robust expansion.")
    if pct >= 5:
        return _Assessment("neutral", "Adequate", f"{label} +{pct:.1f}% — steady growth.")
    if pct >= 0:
        return _Assessment("caution", "Watch", f"{label} +{pct:.1f}% — slow growth.")
    if bearish:
        return _Assessment("negative", "Stressed", f"{label} {pct:.1f}% — contraction in a cautious macro backdrop.")
    return _Assessment("caution", "Watch", f"{label} {pct:.1f}% — declining.")


def assess_dividend_payout(payout_decimal: Optional[float]) -> _Assessment:
    if payout_decimal is None:
        return _Assessment("neutral", "N/A", "Payout ratio unavailable.")
    pct = payout_decimal * 100.0
    if pct <= 60:
        return _Assessment("neutral", "Sustainable", f"Payout ~{pct:.0f}% — room to reinvest or buffer shocks.")
    if pct <= 85:
        return _Assessment("caution", "Watch", f"Payout ~{pct:.0f}% — limited reinvestment headroom.")
    return _Assessment("negative", "Stressed", f"Payout ~{pct:.0f}% — dividend may strain cash flow.")


def _tone_score(tone: str) -> int:
    return {
        "positive": 2,
        "neutral": 0,
        "caution": -1,
        "negative": -2,
    }.get(tone, 0)


def synthesize_fundamental_health(
    assessments: List[_Assessment],
    *,
    market_regime: Optional[str],
) -> FundamentalHealthPanel:
    scored = [a for a in assessments if a.label != "N/A"]
    coverage = len(scored) / max(len(assessments), 1)
    if not scored or coverage < 0.35:
        return FundamentalHealthPanel(
            headline="Insufficient data",
            tone="neutral",
            summary="Too few fundamental inputs to grade business quality reliably.",
            macro_regime=(market_regime or "BULL_NORMAL").upper(),
            macro_note=_macro_note(market_regime),
            coverage_pct=round(coverage * 100, 1),
        )

    avg = sum(_tone_score(a.tone) for a in scored) / len(scored)
    neg = sum(1 for a in scored if a.tone == "negative")
    pos = sum(1 for a in scored if a.tone == "positive")

    if avg >= 1.0 and neg == 0:
        headline = "High-quality business"
        tone = "positive"
        summary = (
            "Profitability, balance sheet, and cash-flow signals skew strong — "
            "fundamentals support a quality compounder profile."
        )
    elif avg < -0.6 or neg >= 2:
        headline = "Weak fundamentals"
        tone = "negative"
        summary = (
            "Several core metrics flag stress — leverage, margins, or cash flow "
            "may limit resilience in tougher markets."
        )
    else:
        headline = "Mixed fundamentals"
        tone = "neutral"
        summary = (
            "Strengths and weaknesses offset — review leverage, cash generation, "
            "and margins before sizing conviction."
        )

    if pos > 0 and neg > 0:
        summary += f" ({pos} strong vs {neg} stressed signals in this snapshot.)"

    return FundamentalHealthPanel(
        headline=headline,
        tone=tone,
        summary=summary,
        macro_regime=(market_regime or "BULL_NORMAL").upper(),
        macro_note=_macro_note(market_regime),
        coverage_pct=round(coverage * 100, 1),
    )


def enrich_quality_panel(
    quality: TerminalQualityPanel,
    *,
    market_regime: Optional[str],
    roic_pct: Optional[float],
    moat_status: str,
    fcf_usd: Optional[float],
    market_cap: Optional[float],
    debt_to_ebitda: Optional[float],
    gross_margin_pct: Optional[float],
    current_ratio: Optional[float],
) -> TerminalQualityPanel:
    bearish = _is_bearish_regime(market_regime)
    row_assessments: Dict[str, _Assessment] = {
        "roic": assess_roic_proxy(roic_pct),
        "moat": assess_moat_status(moat_status),
        "fcf": assess_fcf_level(fcf_usd, market_cap, bearish=bearish),
        "debt": assess_leverage(debt_to_ebitda, bearish=bearish),
        "margin": assess_gross_margin(gross_margin_pct),
        "current_ratio": assess_current_ratio(current_ratio),
    }

    enriched_rows: List[TerminalQualityRow] = []
    all_assessments: List[_Assessment] = []
    for row in quality.rows:
        a = row_assessments.get(row.id, _Assessment("neutral", "N/A", ""))
        all_assessments.append(a)
        enriched_rows.append(
            TerminalQualityRow(
                id=row.id,
                label=row.label,
                value_label=row.value_label,
                status_label=row.status_label,
                provenance=row.provenance,
                assessment_tone=a.tone,
                assessment_label=a.label,
                assessment_detail=a.detail,
            )
        )

    panel_health = synthesize_fundamental_health(all_assessments, market_regime=market_regime)
    return TerminalQualityPanel(rows=enriched_rows, fundamental_health=panel_health)


def assess_financial_metrics(
    metrics: Dict[str, Any],
    *,
    market_regime: Optional[str] = None,
) -> Tuple[FundamentalHealthPanel, Dict[str, MetricHealthAssessment]]:
    bearish = _is_bearish_regime(market_regime)
    val = metrics.get("valuation") or {}
    cf = metrics.get("cash_flow") or {}
    mg = metrics.get("margins_and_growth") or {}
    div = metrics.get("dividend") or {}

    market_cap = val.get("market_cap")
    fcf_yield = cf.get("fcf_yield")

    assessments: Dict[str, _Assessment] = {
        "trailing_pe": assess_pe(val.get("trailing_pe"), val.get("forward_pe")),
        "forward_pe": assess_pe(val.get("trailing_pe"), val.get("forward_pe")),
        "price_to_sales": _assess_price_to_sales(val.get("price_to_sales")),
        "ev_to_ebitda": assess_ev_ebitda(val.get("ev_to_ebitda")),
        "fcf_yield": _assess_fcf_yield_decimal(fcf_yield, bearish=bearish),
        "profit_margin": _assess_margin_decimal(mg.get("profit_margins"), "Profit margin"),
        "operating_margin": _assess_margin_decimal(mg.get("operating_margins"), "Operating margin"),
        "earnings_growth_yoy": assess_growth_rate(
            mg.get("earnings_growth_yoy"), bearish=bearish, label="Earnings growth"
        ),
        "revenue_growth_yoy": assess_growth_rate(
            mg.get("revenue_growth_yoy"), bearish=bearish, label="Revenue growth"
        ),
        "dividend_yield": _assess_dividend_yield_decimal(div.get("dividend_yield")),
        "payout_ratio": assess_dividend_payout(div.get("payout_ratio")),
    }

    if fcf_yield is None and cf.get("free_cash_flow") is not None:
        assessments["fcf_yield"] = assess_fcf_level(
            cf.get("free_cash_flow"),
            market_cap,
            bearish=bearish,
        )

    all_list = list(assessments.values())
    panel = synthesize_fundamental_health(all_list, market_regime=market_regime)
    models = {k: v.to_model() for k, v in assessments.items()}
    return panel, models


def _assess_fcf_yield_decimal(yield_decimal: Optional[float], *, bearish: bool) -> _Assessment:
    if yield_decimal is None:
        return _Assessment("neutral", "N/A", "FCF yield unavailable.")
    pct = yield_decimal * 100.0
    healthy = 4.5 if bearish else 4.0
    adequate = 2.5 if bearish else 2.0
    if pct >= healthy:
        return _Assessment("positive", "Healthy", f"FCF yield ~{pct:.1f}%.")
    if pct >= adequate:
        return _Assessment("neutral", "Adequate", f"FCF yield ~{pct:.1f}%.")
    if pct > 0:
        return _Assessment("caution", "Watch", f"FCF yield ~{pct:.1f}% — thin.")
    return _Assessment("negative", "Stressed", f"FCF yield ~{pct:.1f}%.")


def _assess_margin_decimal(margin_decimal: Optional[float], name: str) -> _Assessment:
    if margin_decimal is None:
        return _Assessment("neutral", "N/A", f"{name} unavailable.")
    return assess_gross_margin(margin_decimal * 100.0)


def _assess_price_to_sales(ps: Optional[float]) -> _Assessment:
    if ps is None:
        return _Assessment("neutral", "N/A", "Price/sales unavailable.")
    if ps <= 3:
        return _Assessment("positive", "Attractive", f"P/S {ps:.1f}× — modest sales multiple.")
    if ps <= 8:
        return _Assessment("neutral", "Fair", f"P/S {ps:.1f}× — typical for growth businesses.")
    return _Assessment("caution", "Stretched", f"P/S {ps:.1f}× — high revenue multiple.")


def _assess_dividend_yield_decimal(yield_decimal: Optional[float]) -> _Assessment:
    if yield_decimal is None:
        return _Assessment("neutral", "N/A", "Dividend yield unavailable.")
    pct = yield_decimal * 100.0
    if pct >= 4:
        return _Assessment("positive", "Healthy", f"Dividend yield ~{pct:.1f}%.")
    if pct >= 1:
        return _Assessment("neutral", "Adequate", f"Dividend yield ~{pct:.1f}%.")
    return _Assessment("caution", "Watch", f"Dividend yield ~{pct:.1f}% — low income return.")


def build_stock_fundamentals_health(
    metrics: Dict[str, Any],
    *,
    market_regime: Optional[str] = None,
) -> Dict[str, Any]:
    panel, metric_models = assess_financial_metrics(metrics, market_regime=market_regime)
    return {
        "fundamental_health": panel,
        "metrics": metric_models,
    }
