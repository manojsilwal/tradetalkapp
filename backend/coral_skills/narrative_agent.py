"""
CORAL Hub Skill: AI Narrative Explanation Agent

Responsible for:
- Generating source-grounded summaries using the LLM client.
- Explaining latest sector bets, top holdings, buys, and return drivers.
- Enforcing constraints to never make unsupported claims.
"""
import logging
from typing import Dict, Any, List

from backend.deps import llm_client

logger = logging.getLogger(__name__)

async def generate_fund_narrative(
    fund_name: str,
    report_period: str,
    filing_date: str,
    portfolio_summary: Dict[str, Any],
    return_metrics: Dict[str, Any],
    confidence_label: str
) -> str:
    """
    Constructs a prompt with the fund's factual data and asks the LLM to generate
    a grounded, professional intelligence summary.
    """
    logger.info(f"[Narrative Agent] Generating narrative for {fund_name}")

    # Format the data cleanly for the LLM
    top_sector = portfolio_summary.get("top_sector", "N/A")
    top_sector_weight = portfolio_summary.get("top_sector_weight", 0)
    top_10 = portfolio_summary.get("top_10_weight", 0)
    total_val = portfolio_summary.get("total_market_value_usd", 0)

    changes = portfolio_summary.get("position_changes_summary", {})
    new_buys = changes.get("new_buys", 0)
    sold_out = changes.get("sold_out", 0)
    increased = changes.get("increased", 0)
    reduced = changes.get("reduced", 0)

    cagr = return_metrics.get("cagr", 0)
    alpha = return_metrics.get("alphaVsBenchmark", 0)

    prompt = f"""
You are an institutional intelligence analyst. Write a concise, factual summary (1-2 paragraphs) of the latest 13F filing and public performance estimation for {fund_name}.

Rules:
- You must mention the Report Period ({report_period}) and Filing Date ({filing_date}).
- You must state that returns are a 13F-inferred public long-book clone and not actual fund performance.
- DO NOT make unsupported claims about the manager's intentions.
- Use a professional, objective tone.

Fact Sheet:
- Total 13F Market Value: ${total_val:,.0f}
- Top Sector: {top_sector} ({top_sector_weight:.1%} of portfolio)
- Top 10 Holdings Concentration: {top_10:.1%}
- Quarter-over-Quarter Moves: {new_buys} new buys, {sold_out} sold out, {increased} increased, {reduced} reduced.
- 10-Year Estimated CAGR: {cagr:.1%}
- 10-Year Estimated Alpha vs SPY: {alpha:.1%}
- Data Confidence Level: {confidence_label}

Return exactly the summary paragraph(s). No markdown headings, no introductory filler.
"""

    try:
        response = await llm_client.generate_text(prompt, model="default")
        return response.strip()
    except Exception as e:
        logger.error(f"[Narrative Agent] LLM generation failed: {e}")
        return f"Latest public 13F portfolio ({report_period}, filed {filing_date}) is concentrated in {top_sector} with a 10Y estimated CAGR of {cagr:.1%}. Returns are based on a long-book clone, not actual performance. Note: AI narrative unavailable due to system error."
