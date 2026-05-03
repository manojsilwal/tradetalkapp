"""
Empirically grounded strategy presets for the Strategy Lab backtester.

Each preset maps to :class:`~backend.schemas.StrategyRules` with optional
``rank_by_metric`` (see ``backtest_engine._metric``). The live universe is the
curated ~40-name ``SP500_UNIVERSE`` (Yahoo rate limits) unless noted — results
are illustrative, not full-index reproduction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schemas import FilterRule, StrategyRules
from .connectors.backtest_data import SP500_UNIVERSE

# Excludes typical financials / brokers for Magic Formula–style screens
_FIN_EXCLUDE = {"JPM", "BAC", "GS", "MS", "BLK", "SPGI", "C", "USB", "PNC", "TFC"}
MAGIC_UNIVERSE = [t for t in SP500_UNIVERSE if t not in _FIN_EXCLUDE]

# Dow Jones Industrial Average (30) — liquid US names
DOW_30 = [
    "AAPL", "AMGN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS", "DOW",
    "GS", "HD", "HON", "IBM", "INTC", "JNJ", "JPM", "KO", "MCD", "MMM",
    "MRK", "MSFT", "NKE", "PG", "TRV", "UNH", "V", "VZ", "WBA", "WMT",
]


@dataclass
class StrategyConfig:
    name: str
    description: str
    universe: list[str]
    filters: list[FilterRule]
    rank_by_metric: str | None = None
    rank_higher_is_better: bool = True
    select_top_n: int = 12
    rebalance_months: int = 3
    strategy_type: str = "mixed"
    strategy_category: str = "Factor"
    preset_id: str = ""


def _base(config: StrategyConfig) -> StrategyRules:
    return StrategyRules(
        name=config.name,
        description=config.description,
        filters=config.filters,
        sell_filters=[],
        holding_period_months=max(12, config.rebalance_months),
        rebalance_months=config.rebalance_months,
        universe=config.universe,
        start_date="2010-01-01",
        end_date="2024-01-01",
        strategy_type=config.strategy_type,
        rank_by_metric=config.rank_by_metric,
        rank_higher_is_better=config.rank_higher_is_better,
        select_top_n=config.select_top_n,
        strategy_category=config.strategy_category,
        preset_id=config.preset_id or None,
    )


_PRESET_BUILDERS: dict[str, Any] = {}


def _register(pid: str, builder):
    _PRESET_BUILDERS[pid] = builder


def _ff_qmj():
    return _base(StrategyConfig(
        preset_id="ff_quality_value",
        name="Fama-French Quality + Value",
        description=(
            "Quality gates (ROE, gross margin, leverage) then rank by low P/B. "
            "Inspired by QMJ + HML; see Fama-French & Asness quality literature."
        ),
        universe=list(SP500_UNIVERSE),
        filters=[
            FilterRule(metric="roe", op=">", value=12.0),
            FilterRule(metric="gross_margins", op=">", value=20.0),
            FilterRule(metric="debt_to_equity", op="<", value=120.0),
        ],
        rank_by_metric="pb_ratio",
        rank_higher_is_better=False,
        select_top_n=8,
        rebalance_months=3,
        strategy_category="Factor",
    ))


def _mom_12_1():
    return _base(StrategyConfig(
        preset_id="momentum_12_1",
        name="Momentum (12-1 month)",
        description=(
            "Rank by ~11-month return skipping the most recent month (Jegadeesh & Titman 1993). "
            "Monthly rebalance, equal-weight top names."
        ),
        universe=list(SP500_UNIVERSE),
        filters=[],
        rank_by_metric="momentum_12_1",
        rank_higher_is_better=True,
        select_top_n=8,
        rebalance_months=1,
        strategy_type="momentum",
        strategy_category="Momentum",
    ))


def _low_vol():
    return _base(StrategyConfig(
        preset_id="low_volatility",
        name="Low Volatility",
        description=(
            "Hold the lowest 252-day realized-vol names (Baker, Bradley & Wurgler 2011 style). "
            "Lower vol = better."
        ),
        universe=list(SP500_UNIVERSE),
        filters=[],
        rank_by_metric="realized_vol_252",
        rank_higher_is_better=False,
        select_top_n=8,
        rebalance_months=1,
        strategy_category="Factor",
    ))


def _magic_formula():
    return _base(StrategyConfig(
        preset_id="magic_formula",
        name="Magic Formula",
        description=(
            "Earnings yield + quality heuristic; excludes common financials. "
            "Annual rebalance; see Greenblatt, The Little Book That Beats the Market."
        ),
        universe=list(MAGIC_UNIVERSE),
        filters=[],
        rank_by_metric="magic_formula_score",
        rank_higher_is_better=True,
        select_top_n=12,
        rebalance_months=12,
        strategy_type="fundamental",
        strategy_category="Value",
    ))


def _dual_momentum():
    return _base(StrategyConfig(
        preset_id="dual_momentum",
        name="Dual Momentum",
        description=(
            "Absolute vs T-bills (SHY) then relative SPY vs EFA; holds one ETF. "
            "See Antonacci, Dual Momentum."
        ),
        universe=["SPY", "EFA", "SHY"],
        filters=[],
        rank_by_metric="dual_momentum_rank",
        rank_higher_is_better=True,
        select_top_n=1,
        rebalance_months=1,
        strategy_type="momentum",
        strategy_category="Macro",
    ))


def _dogs_dow():
    return _base(StrategyConfig(
        preset_id="dogs_of_the_dow",
        name="Dogs of the Dow",
        description=(
            "Highest dividend yields among Dow 30; annual rebalance. "
            "Classic contrarian income screen."
        ),
        universe=list(DOW_30),
        filters=[],
        rank_by_metric="dividend_yield",
        rank_higher_is_better=True,
        select_top_n=10,
        rebalance_months=12,
        strategy_category="Income",
    ))


def _week52_high():
    return _base(StrategyConfig(
        preset_id="week52_high",
        name="52-Week High",
        description=(
            "Stocks closest to 52-week highs (George & Hwang 2004). "
            "Score = price / 52w high (higher = stronger)."
        ),
        universe=list(SP500_UNIVERSE),
        filters=[],
        rank_by_metric="price_to_52w_high",
        rank_higher_is_better=True,
        select_top_n=10,
        rebalance_months=1,
        strategy_category="Momentum",
    ))


def _shareholder_yield():
    return _base(StrategyConfig(
        preset_id="shareholder_yield",
        name="Shareholder Yield",
        description=(
            "Ranks by dividend yield (buybacks not in free data — scaled proxy). "
            "Meb Faber / shareholder yield literature."
        ),
        universe=list(SP500_UNIVERSE),
        filters=[],
        rank_by_metric="shareholder_yield_proxy",
        rank_higher_is_better=True,
        select_top_n=10,
        rebalance_months=3,
        strategy_category="Value",
    ))


def _value_mom_combo():
    return _base(StrategyConfig(
        preset_id="value_momentum_aqr",
        name="Value + Momentum",
        description=(
            "Combined value + 12-1 momentum score (AQR multi-factor spirit). "
            "Monthly rebalance."
        ),
        universe=list(SP500_UNIVERSE),
        filters=[],
        rank_by_metric="value_momentum_combo",
        rank_higher_is_better=True,
        select_top_n=10,
        rebalance_months=1,
        strategy_category="Blended",
    ))


def _buffett_garp():
    return _base(StrategyConfig(
        preset_id="buffett_garp",
        name="Quality GARP",
        description=(
            "ROE > 15%, revenue growth > 8% YoY, then cheapest by P/E. "
            "Semi-annual rebalance; GARP / compounder screen."
        ),
        universe=list(SP500_UNIVERSE),
        filters=[
            FilterRule(metric="roe", op=">", value=15.0),
            FilterRule(metric="revenue_growth_yoy", op=">", value=0.08),
        ],
        rank_by_metric="pe_ratio",
        rank_higher_is_better=False,
        select_top_n=12,
        rebalance_months=6,
        strategy_type="fundamental",
        strategy_category="Quality",
    ))


_register("ff_quality_value", _ff_qmj)
_register("momentum_12_1", _mom_12_1)
_register("low_volatility", _low_vol)
_register("magic_formula", _magic_formula)
_register("dual_momentum", _dual_momentum)
_register("dogs_of_the_dow", _dogs_dow)
_register("week52_high", _week52_high)
_register("shareholder_yield", _shareholder_yield)
_register("value_momentum_aqr", _value_mom_combo)
_register("buffett_garp", _buffett_garp)


def list_preset_summaries() -> list[dict[str, Any]]:
    """Lightweight catalog for GET /strategies/presets."""
    meta = [
        ("ff_quality_value", "Fama-French Quality + Value", "Factor",
         "Quality filters + low P/B rank; Fama-French / Asness lineage.", "Quarterly"),
        ("momentum_12_1", "Momentum (12-1 month)", "Momentum",
         "Classic price momentum skipping last month (Jegadeesh & Titman).", "Monthly"),
        ("low_volatility", "Low Volatility", "Factor",
         "Low realized vol wins on risk-adjusted basis (Baker et al.).", "Monthly"),
        ("magic_formula", "Magic Formula", "Value",
         "Earnings yield + ROE quality; excludes many financials.", "Annual"),
        ("dual_momentum", "Dual Momentum", "Macro",
         "SPY vs EFA vs SHY tactical rotation (Antonacci).", "Monthly"),
        ("dogs_of_the_dow", "Dogs of the Dow", "Income",
         "Top dividend yields in Dow 30.", "Annual"),
        ("week52_high", "52-Week High", "Momentum",
         "Proximity to 52-week high (George & Hwang).", "Monthly"),
        ("shareholder_yield", "Shareholder Yield", "Value",
         "Dividend-led yield proxy (buybacks where unavailable).", "Quarterly"),
        ("value_momentum_aqr", "Value + Momentum", "Blended",
         "Combined value & momentum score (AQR-style blend).", "Monthly"),
        ("buffett_garp", "Quality GARP", "Quality",
         "High ROE, revenue growth, cheap P/E; semi-annual.", "Semi-annual"),
    ]
    out = []
    for pid, title, cat, blurb, freq in meta:
        out.append({
            "preset_id": pid,
            "name": title,
            "category": cat,
            "short_description": blurb,
            "rebalance_freq": freq,
        })
    return out


def get_preset_rules(preset_id: str, start_date: str, end_date: str) -> StrategyRules:
    """Return a concrete StrategyRules instance with user dates."""
    builder = _PRESET_BUILDERS.get(preset_id)
    if not builder:
        raise KeyError(f"Unknown preset_id: {preset_id}")
    rules: StrategyRules = builder()
    return rules.model_copy(update={"start_date": start_date, "end_date": end_date})
