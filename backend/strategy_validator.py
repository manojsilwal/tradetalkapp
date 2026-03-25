"""
Strategy Validator — lightweight pre-flight check before running expensive backtests.

Rejects strategies that:
  - Are too short or vague (< 10 chars with no preset)
  - Contain no recognizable financial concepts
  - Request impossible date ranges
  - Appear to be gibberish / prompt injection
"""
import re
import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

FINANCIAL_TERMS = {
    "buy", "sell", "hold", "stock", "stocks", "share", "shares",
    "pe", "p/e", "price", "earnings", "revenue", "income", "profit",
    "dividend", "yield", "growth", "debt", "equity", "ratio",
    "momentum", "moving average", "ma", "trend", "breakout",
    "value", "quality", "return", "volatility", "risk",
    "roe", "roa", "eps", "ebitda", "margin", "cash flow",
    "sector", "market cap", "index", "s&p", "dow", "nasdaq",
    "rebalance", "portfolio", "allocation", "weight",
    "above", "below", "less than", "greater than", "under", "over",
    "annual", "monthly", "quarterly", "weekly",
    "bull", "bear", "neutral", "high", "low",
    "fundamental", "technical", "factor",
}

GIBBERISH_PATTERNS = [
    r"(?:asdf|qwerty|test123|lorem ipsum|foo bar)",
    r"^[^a-zA-Z]*$",
    r"(?:ignore|forget|disregard)\s+(?:previous|above|all)",
]


class StrategyValidationError:
    def __init__(self, reason: str, suggestion: str = ""):
        self.reason = reason
        self.suggestion = suggestion

    def to_dict(self):
        d = {"valid": False, "reason": self.reason}
        if self.suggestion:
            d["suggestion"] = self.suggestion
        return d


def validate_strategy(
    strategy_text: str,
    start_date: str,
    end_date: str,
    preset_id: str = "",
) -> dict:
    """
    Validate a strategy before running a backtest.
    Returns {"valid": True} or {"valid": False, "reason": ..., "suggestion": ...}
    """
    if preset_id and preset_id.strip():
        return {"valid": True, "source": "preset"}

    text = (strategy_text or "").strip()

    if len(text) < 10:
        return StrategyValidationError(
            "Strategy description is too short. Please describe your investing thesis in at least a sentence.",
            "Example: 'Buy stocks with PE below 20 and revenue growth above 10%, rebalance annually'"
        ).to_dict()

    if len(text) > 2000:
        return StrategyValidationError(
            "Strategy description is too long (max 2000 characters). Keep it concise.",
        ).to_dict()

    for pattern in GIBBERISH_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return StrategyValidationError(
                "Strategy doesn't appear to describe a meaningful investment approach.",
                "Describe what to buy, when to buy/sell, and what metrics matter."
            ).to_dict()

    text_lower = text.lower()
    found_terms = sum(1 for term in FINANCIAL_TERMS if term in text_lower)
    if found_terms < 2:
        return StrategyValidationError(
            "Strategy doesn't contain enough recognizable financial concepts. "
            "Include investment criteria like metrics (PE, revenue growth, dividend yield), "
            "actions (buy, sell), or strategy types (momentum, value).",
            "Example: 'Buy high dividend yield stocks above 3% with low debt-to-equity ratio'"
        ).to_dict()

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        if start >= end:
            return StrategyValidationError("Start date must be before end date.").to_dict()
        if start < date(2010, 1, 1):
            return StrategyValidationError(
                "Historical data only available from 2010-01-01.",
                "Set start date to 2010-01-01 or later."
            ).to_dict()
        if end > date.today():
            return StrategyValidationError("End date cannot be in the future.").to_dict()
        if (end - start).days < 180:
            return StrategyValidationError(
                "Backtest period must be at least 6 months for meaningful results.",
                "Use a wider date range for statistically significant results."
            ).to_dict()
    except ValueError:
        return StrategyValidationError("Invalid date format. Use YYYY-MM-DD.").to_dict()

    return {"valid": True, "source": "custom", "financial_terms_found": found_terms}
