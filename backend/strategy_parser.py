"""
Strategy Parser — converts plain-English investing strategies into structured
StrategyRules using Gemini. Falls back to simple heuristic extraction if
no LLM is available.
"""
import logging
import re
from typing import Optional
from .schemas import StrategyRules, FilterRule
from .connectors.backtest_data import SP500_UNIVERSE

logger = logging.getLogger(__name__)

SUPPORTED_METRICS = [
    "revenue_growth_yoy", "net_income_growth_yoy",
    "debt_to_equity", "cash_to_debt",
    "pe_ratio", "pb_ratio", "roe", "roa",
    "price_return_1m", "price_return_3m", "price_return_6m", "price_return_1y",
    "above_ma_200", "above_ma_50",
    "dividend_yield",
    "gross_margins",
]


async def parse_strategy(
    strategy_text: str,
    start_date: str,
    end_date: str,
    llm,
    ks,
) -> StrategyRules:
    """
    Parse plain-English strategy into StrategyRules.
    Steps:
      1. Query knowledge base for similar strategies
      2. Ask Gemini to extract rules
      3. Validate and sanitise the output
      4. Fall back to heuristic extraction on failure
    """
    # RAG: look for similar past strategies
    context_docs = ks.query("strategy_backtests", strategy_text, n_results=2)
    context = ks.format_context(context_docs)

    # Ask Gemini
    try:
        result = await llm.generate_strategy_rules(strategy_text, context)
        rules = _parse_llm_output(result, strategy_text, start_date, end_date)
        if rules:
            return rules
    except Exception as e:
        logger.warning(f"[StrategyParser] LLM parse failed: {e}")

    # Heuristic fallback
    return _heuristic_parse(strategy_text, start_date, end_date)


def _parse_llm_output(result: dict, strategy_text: str, start: str, end: str) -> Optional[StrategyRules]:
    try:
        raw_filters = result.get("filters", [])
        filters = []
        for f in raw_filters:
            metric = str(f.get("metric", "")).lower()
            op     = str(f.get("op", ">"))
            value  = float(f.get("value", 0))
            if metric in SUPPORTED_METRICS:
                filters.append(FilterRule(metric=metric, op=op, value=value))

        holding_months  = int(result.get("holding_period_months", 12))
        rebalance_months = int(result.get("rebalance_months", 12))
        strategy_type   = result.get("strategy_type", "fundamental")
        universe_hint   = result.get("universe_hint", "")
        name            = result.get("name", "Custom Strategy")

        # Resolve universe — specific tickers or full S&P500
        universe = _resolve_universe(universe_hint, strategy_text)

        return StrategyRules(
            name=name,
            description=strategy_text,
            filters=filters,
            holding_period_months=max(1, min(holding_months, 120)),
            rebalance_months=max(1, min(rebalance_months, 60)),
            universe=universe,
            start_date=start,
            end_date=end,
            strategy_type=strategy_type if strategy_type in ("fundamental", "momentum", "mixed") else "fundamental",
        )
    except Exception as e:
        logger.warning(f"[StrategyParser] LLM output parse error: {e}")
        return None


def _heuristic_parse(strategy_text: str, start: str, end: str) -> StrategyRules:
    """
    Simple keyword-based extraction as last-resort fallback.
    """
    text = strategy_text.lower()
    filters = []

    if "revenue" in text and ("grow" in text or "growth" in text):
        pct = _extract_pct(text, default=15.0)
        filters.append(FilterRule(metric="revenue_growth_yoy", op=">", value=pct / 100))

    if "p/e" in text or "pe ratio" in text or "price to earnings" in text:
        pct = _extract_number_near(text, ["p/e", "pe"], default=25.0)
        op = "<" if "below" in text or "low" in text else ">"
        filters.append(FilterRule(metric="pe_ratio", op=op, value=pct))

    if "dividend" in text:
        pct = _extract_pct(text, default=2.0)
        filters.append(FilterRule(metric="dividend_yield", op=">", value=pct / 100))

    if "200" in text and ("moving" in text or "ma" in text or "average" in text):
        filters.append(FilterRule(metric="above_ma_200", op=">", value=0))

    if "debt" in text and ("low" in text or "below" in text):
        filters.append(FilterRule(metric="debt_to_equity", op="<", value=1.0))

    if "momentum" in text or "return" in text or "price" in text:
        filters.append(FilterRule(metric="price_return_3m", op=">", value=5.0))

    if not filters:
        # Default: basic quality filter
        filters = [
            FilterRule(metric="revenue_growth_yoy", op=">", value=0.10),
            FilterRule(metric="pe_ratio", op="<", value=30),
        ]

    # Determine strategy type
    metric_names = [f.metric for f in filters]
    has_fundamental = any(m in ("revenue_growth_yoy", "pe_ratio", "roe", "debt_to_equity") for m in metric_names)
    has_momentum    = any(m.startswith("price_return") or "ma_" in m for m in metric_names)

    if has_fundamental and has_momentum:
        stype = "mixed"
    elif has_momentum:
        stype = "momentum"
    else:
        stype = "fundamental"

    # Holding period
    holding = 12
    if "3 year" in text or "36 month" in text:
        holding = 36
    elif "5 year" in text or "60 month" in text:
        holding = 60
    elif "6 month" in text:
        holding = 6

    universe = _resolve_universe("", strategy_text)

    return StrategyRules(
        name=_infer_name(strategy_text),
        description=strategy_text,
        filters=filters,
        holding_period_months=holding,
        rebalance_months=12,
        universe=universe,
        start_date=start,
        end_date=end,
        strategy_type=stype,
    )


def _resolve_universe(universe_hint: str, strategy_text: str) -> list:
    """
    Check if the strategy names specific tickers, otherwise use S&P 500 universe.
    """
    # Look for explicit tickers in the strategy text (e.g. "buy TSLA, AAPL")
    found = re.findall(r'\b([A-Z]{2,5})\b', strategy_text)
    valid_tickers = [t for t in found if len(t) >= 2 and t not in (
        "BUY", "SELL", "HOLD", "ETF", "SPY", "AND", "FOR", "THE", "WITH",
        "FROM", "THAT", "YEAR", "EACH", "ONLY", "THAN", "HIGH", "LOW",
        "RATE", "CASH", "FREE", "FLOW", "DEBT", "CAGR", "ROE", "ROA", "EPS",
    )]
    if valid_tickers:
        return valid_tickers[:20]  # cap at 20 explicit tickers
    return SP500_UNIVERSE


def _extract_pct(text: str, default: float) -> float:
    matches = re.findall(r'(\d+(?:\.\d+)?)\s*%', text)
    if matches:
        return float(matches[0])
    return default


def _extract_number_near(text: str, keywords: list, default: float) -> float:
    for kw in keywords:
        idx = text.find(kw)
        if idx >= 0:
            snippet = text[max(0, idx - 20):idx + 20]
            nums = re.findall(r'\d+(?:\.\d+)?', snippet)
            if nums:
                return float(nums[0])
    return default


def _infer_name(text: str) -> str:
    text = text.strip()
    if len(text) <= 60:
        return text.capitalize()
    # Use first sentence or first 60 chars
    first = text.split(".")[0]
    return (first[:57] + "...") if len(first) > 60 else first.capitalize()
