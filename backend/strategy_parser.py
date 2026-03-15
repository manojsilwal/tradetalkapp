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
    "forward_pe", "pe_ratio", "trailing_pe", "pb_ratio", "roe", "roa",
    "price_return_1m", "price_return_3m", "price_return_6m", "price_return_1y",
    "above_ma_200", "above_ma_50",
    "dividend_yield", "gross_margins",
]

# Metric aliases — normalise common user phrasings to canonical names
METRIC_ALIASES = {
    "pe":              "forward_pe",
    "p/e":             "forward_pe",
    "price_to_earnings": "forward_pe",
    "forward_p/e":     "forward_pe",
    "trailing_p/e":    "pe_ratio",
    "trailing_pe":     "pe_ratio",
}


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

    # Ask LLM to extract structured rules
    try:
        result = await llm.generate_strategy_rules(strategy_text, context)
        rules = _parse_llm_output(result, strategy_text, start_date, end_date)
        # Accept LLM output only if it produced at least one filter — otherwise heuristic is better
        if rules and rules.filters:
            logger.info(f"[StrategyParser] LLM extracted {len(rules.filters)} buy + {len(rules.sell_filters)} sell filters")
            return rules
        logger.warning("[StrategyParser] LLM returned no usable filters — using heuristic fallback")
    except Exception as e:
        logger.warning(f"[StrategyParser] LLM parse failed: {e}")

    # Heuristic fallback
    return _heuristic_parse(strategy_text, start_date, end_date)


def _parse_filter_list(raw: list) -> list:
    """Parse a list of filter dicts into FilterRule objects, normalising metric aliases."""
    filters = []
    for f in (raw or []):
        if not isinstance(f, dict):
            continue
        metric = METRIC_ALIASES.get(str(f.get("metric", "")).lower(), str(f.get("metric", "")).lower())
        op     = str(f.get("op", ">"))
        try:
            value = float(f.get("value", 0))
        except (TypeError, ValueError):
            continue
        if metric in SUPPORTED_METRICS:
            filters.append(FilterRule(metric=metric, op=op, value=value))
    return filters


def _parse_llm_output(result: dict, strategy_text: str, start: str, end: str) -> Optional[StrategyRules]:
    try:
        filters      = _parse_filter_list(result.get("filters", []))
        sell_filters = _parse_filter_list(result.get("sell_filters", []))

        holding_months   = int(result.get("holding_period_months", 12))
        rebalance_months = int(result.get("rebalance_months", 12))
        strategy_type    = result.get("strategy_type", "fundamental")
        universe_hint    = result.get("universe_hint", "")
        name             = result.get("name", "Custom Strategy")

        # Use 1-month rebalance when there are explicit sell conditions (continuous monitoring)
        if sell_filters:
            rebalance_months = 1

        universe = _resolve_universe(universe_hint, strategy_text)

        return StrategyRules(
            name=name,
            description=strategy_text,
            filters=filters,
            sell_filters=sell_filters,
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
    Also handles PE buy/sell thresholds.
    """
    text = strategy_text.lower()
    filters      = []
    sell_filters = []

    if "revenue" in text and ("grow" in text or "growth" in text):
        pct = _extract_pct(text, default=15.0)
        filters.append(FilterRule(metric="revenue_growth_yoy", op=">", value=pct / 100))

    # PE buy/sell conditions — detect two-sided PE strategy
    if any(k in text for k in ("p/e", "pe ratio", "forward pe", "pe <", "pe below")):
        buy_pe  = _extract_number_near(text, ["less than", "below", "under", "< "], default=25.0)
        sell_pe = _extract_number_near(text, ["more than", "above", "over", "> ", "greater"], default=35.0)
        filters.append(FilterRule(metric="forward_pe", op="<", value=buy_pe))
        if "sell" in text and sell_pe != buy_pe:
            sell_filters.append(FilterRule(metric="forward_pe", op=">", value=sell_pe))

    elif "p/e" in text or "pe ratio" in text or "price to earnings" in text:
        pct = _extract_number_near(text, ["p/e", "pe"], default=25.0)
        op  = "<" if ("below" in text or "low" in text or "less" in text) else ">"
        filters.append(FilterRule(metric="forward_pe", op=op, value=pct))

    if "dividend" in text:
        pct = _extract_pct(text, default=2.0)
        filters.append(FilterRule(metric="dividend_yield", op=">", value=pct / 100))

    if "200" in text and ("moving" in text or "ma" in text or "average" in text):
        filters.append(FilterRule(metric="above_ma_200", op=">", value=0))

    if "debt" in text and ("low" in text or "below" in text):
        filters.append(FilterRule(metric="debt_to_equity", op="<", value=1.0))

    if not filters:
        filters = [
            FilterRule(metric="revenue_growth_yoy", op=">", value=0.10),
            FilterRule(metric="forward_pe", op="<", value=30),
        ]

    metric_names    = [f.metric for f in filters]
    has_fundamental = any(m in ("revenue_growth_yoy", "forward_pe", "pe_ratio", "roe") for m in metric_names)
    has_momentum    = any(m.startswith("price_return") or "ma_" in m for m in metric_names)
    stype = "mixed" if (has_fundamental and has_momentum) else ("momentum" if has_momentum else "fundamental")

    holding = 12
    for label, months in (("3 year", 36), ("5 year", 60), ("6 month", 6)):
        if label in text:
            holding = months
            break

    # Monthly checks if we have sell conditions
    rebalance = 1 if sell_filters else 12

    universe = _resolve_universe("", strategy_text)

    return StrategyRules(
        name=_infer_name(strategy_text),
        description=strategy_text,
        filters=filters,
        sell_filters=sell_filters,
        holding_period_months=holding,
        rebalance_months=rebalance,
        universe=universe,
        start_date=start,
        end_date=end,
        strategy_type=stype,
    )


def _resolve_universe(universe_hint: str, strategy_text: str) -> list:
    """
    Check if the strategy names specific tickers or well-known universes,
    otherwise fall back to the curated S&P 500 universe.
    """
    from .connectors.backtest_data import MAG7_UNIVERSE

    combined = (universe_hint + " " + strategy_text).lower()
    if any(k in combined for k in ("mag7", "magnificent 7", "magnificent seven", "mag 7")):
        return MAG7_UNIVERSE

    # Look for explicit tickers (2-5 uppercase chars)
    STOPWORDS = {
        "BUY", "SELL", "HOLD", "ETF", "SPY", "AND", "FOR", "THE", "WITH",
        "FROM", "THAT", "YEAR", "EACH", "ONLY", "THAN", "HIGH", "LOW",
        "RATE", "CASH", "FREE", "FLOW", "DEBT", "CAGR", "ROE", "ROA", "EPS",
        "PE", "PB", "MA", "USD", "USA", "SEC", "IPO", "CEO", "CFO",
    }
    found = re.findall(r'\b([A-Z]{2,5})\b', strategy_text)
    valid_tickers = [t for t in found if t not in STOPWORDS]
    if valid_tickers:
        return valid_tickers[:20]
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
