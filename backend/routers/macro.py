"""Macro, metrics, and gold advisor endpoints."""
from fastapi import APIRouter, Depends
from ..schemas import MacroDataResponse, InvestorMetricsResponse, GoldAdvisorResponse
from ..auth import get_optional_user
from ..rate_limiter import rate_limit
from ..deps import macro_connector, investor_metrics_connector, llm_client, up

router = APIRouter(tags=["macro"])

_rl_expensive = rate_limit("expensive")


@router.get("/macro", response_model=MacroDataResponse)
async def get_macro_data():
    """Global Macro Analysis Endpoint."""
    data = await macro_connector.fetch_data()
    ind = data["indicators"]
    return MacroDataResponse(
        vix_level=ind["vix_level"],
        credit_stress_index=ind["credit_stress_index"],
        market_regime="BULL_NORMAL" if ind["credit_stress_index"] <= 1.1 else "BEAR_STRESS",
        sectors=data["sectors"],
        consumer_spending=data["consumer_spending"],
        capital_flows=data["capital_flows"],
        cash_reserves=data["cash_reserves"],
        usd_broad_index=ind.get("usd_broad_index"),
        usd_index_change_5d_pct=ind.get("usd_index_change_5d_pct"),
        usd_strength_label=ind.get("usd_strength_label") or "unknown",
        dxy_level=ind.get("dxy_level"),
        dxy_change_5d_pct=ind.get("dxy_change_5d_pct"),
        dxy_strength_label=ind.get("dxy_strength_label") or "unknown",
        treasury_2y=ind.get("treasury_2y"),
        treasury_10y=ind.get("treasury_10y"),
        yield_curve_spread_10y_2y=ind.get("yield_curve_spread_10y_2y"),
        fed_funds_rate=ind.get("fed_funds_rate"),
        cpi_yoy=ind.get("cpi_yoy"),
        unemployment_rate=ind.get("unemployment"),
        macro_narrative=ind.get("macro_narrative") or "",
        fred_fetched_at=ind.get("fred_fetched_at"),
    )


@router.get("/metrics/{ticker}", response_model=InvestorMetricsResponse)
async def get_investor_metrics(ticker: str):
    """Fetches live fundamental metrics."""
    data = await investor_metrics_connector.fetch_data(ticker=ticker)
    if "error" in data:
        return InvestorMetricsResponse(ticker=ticker.upper(), metrics={})
    return InvestorMetricsResponse(ticker=ticker.upper(), metrics=data["metrics"])


@router.get("/advisor/gold", response_model=GoldAdvisorResponse, dependencies=[Depends(_rl_expensive)])
async def gold_advisor_snapshot(_auth_user=Depends(get_optional_user)):
    """Gold allocator snapshot: FRED real yields, VIX, DXY, gold futures, LLM briefing."""
    from ..gold_advisor_service import run_gold_advisor
    result = await run_gold_advisor(macro_connector, llm_client)
    if _auth_user:
        try:
            up.award_xp(_auth_user.id, "gold_advisor", note="gold_snapshot")
        except Exception:
            pass
    return GoldAdvisorResponse(context=result["context"], briefing=result["briefing"])
