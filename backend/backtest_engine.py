"""
Backtest Engine — simulates a StrategyRules over a user-defined date range.
Uses yFinance historical data. Annual rebalancing. Computes CAGR, Sharpe,
Max Drawdown, Win Rate vs SPY. Calls Gemini for plain-English explanation.
"""
import asyncio
import logging
import math
from datetime import datetime, date
from typing import Optional
from .schemas import StrategyRules, BacktestAction, BacktestResult, FilterRule

logger = logging.getLogger(__name__)

INITIAL_VALUE = 10_000.0   # $10,000 starting portfolio
SPY_TICKER    = "SPY"


async def run_backtest(rules: StrategyRules, llm, ks) -> BacktestResult:
    """
    Full backtest pipeline:
    1. Fetch historical data for universe
    2. Simulate rebalancing loop
    3. Compute statistics
    4. Fetch SPY benchmark
    5. Generate Gemini explanation
    6. Return BacktestResult
    """
    from .connectors.backtest_data import fetch_backtest_data

    tickers = rules.universe if rules.universe else []
    if not tickers:
        from .connectors.backtest_data import SP500_UNIVERSE
        tickers = SP500_UNIVERSE

    logger.info(f"[BacktestEngine] Fetching data for {len(tickers)} tickers ({rules.start_date} → {rules.end_date})")

    # Fetch universe data + SPY benchmark concurrently
    universe_task = fetch_backtest_data(tickers, rules.start_date, rules.end_date)
    spy_task      = fetch_backtest_data([SPY_TICKER], rules.start_date, rules.end_date)
    universe_data, spy_data = await asyncio.gather(universe_task, spy_task)

    # Run simulation
    actions, portfolio_series = await asyncio.to_thread(
        _simulate, rules, universe_data
    )

    # Build benchmark series
    benchmark_series = _build_series(spy_data.get(SPY_TICKER, {}).get("prices", []))

    # Compute stats
    stats = _compute_stats(portfolio_series, benchmark_series, actions)

    # RAG context for explanation
    context_docs = ks.query("strategy_backtests", rules.description, n_results=2)
    context_docs += ks.query("macro_snapshots", f"macro conditions {rules.start_date[:4]}", n_results=1)
    context = ks.format_context(context_docs)

    # Gemini explanation
    explanation = await llm.generate_backtest_explanation(rules.name, stats, context)

    return BacktestResult(
        strategy=rules,
        actions=actions,
        cagr=stats["cagr"],
        sharpe_ratio=stats["sharpe"],
        max_drawdown=stats["max_drawdown"],
        win_rate=stats["win_rate"],
        total_trades=stats["total_trades"],
        benchmark_cagr=stats["benchmark_cagr"],
        outperformed=stats["cagr"] > stats["benchmark_cagr"],
        best_period=stats["best_period"],
        worst_period=stats["worst_period"],
        portfolio_value_series=portfolio_series,
        benchmark_value_series=benchmark_series,
        gemini_explanation=explanation,
        knowledge_context=context[:500] if context else "",
    )


def _simulate(rules: StrategyRules, universe_data: dict) -> tuple:
    """
    Core simulation loop — runs synchronously (called via asyncio.to_thread).
    Returns (actions, portfolio_value_series).
    """
    start = _parse_date(rules.start_date)
    end   = _parse_date(rules.end_date)
    actions = []
    current_holdings: dict[str, float] = {}   # ticker → entry_price
    cash = INITIAL_VALUE
    portfolio_value = INITIAL_VALUE
    portfolio_series = []

    # Generate rebalance dates
    rebalance_dates = _generate_rebalance_dates(start, end, rules.rebalance_months)

    for i, rebalance_date in enumerate(rebalance_dates):
        date_str = str(rebalance_date)
        next_date = rebalance_dates[i + 1] if i + 1 < len(rebalance_dates) else end

        # Close existing positions at this rebalance date
        sell_proceeds = 0.0
        for ticker, entry_price in list(current_holdings.items()):
            sell_price = _get_price_on_date(universe_data.get(ticker, {}), rebalance_date)
            if sell_price and sell_price > 0:
                ret = ((sell_price - entry_price) / entry_price) * 100
                actions.append(BacktestAction(
                    action="SELL",
                    ticker=ticker,
                    date=date_str,
                    price=round(sell_price, 2),
                    reason=f"Rebalance — held from previous period",
                    return_pct=round(ret, 2),
                ))
                sell_proceeds += sell_price * (portfolio_value / len(current_holdings) / entry_price)
        if current_holdings:
            cash = sell_proceeds if sell_proceeds > 0 else cash
        current_holdings = {}

        # Screen universe using filters as of rebalance_date
        selected = _screen_universe(rules.filters, universe_data, rebalance_date)

        if not selected:
            actions.append(BacktestAction(
                action="HOLD_CASH",
                ticker="CASH",
                date=date_str,
                price=1.0,
                reason="No stocks passed the screening filters",
                return_pct=0.0,
            ))
        else:
            # Equal-weight allocation
            alloc_per_stock = cash / len(selected)
            for ticker in selected:
                buy_price = _get_price_on_date(universe_data.get(ticker, {}), rebalance_date)
                if buy_price and buy_price > 0:
                    reason = _build_reason(ticker, rules.filters, universe_data.get(ticker, {}), rebalance_date)
                    actions.append(BacktestAction(
                        action="BUY",
                        ticker=ticker,
                        date=date_str,
                        price=round(buy_price, 2),
                        reason=reason,
                        return_pct=0.0,
                    ))
                    current_holdings[ticker] = buy_price

        # Track portfolio value at end of period
        portfolio_value = _compute_portfolio_value(current_holdings, cash, universe_data, next_date)
        portfolio_series.append({
            "date": str(next_date),
            "value": round(portfolio_value, 2),
        })

    return actions, portfolio_series


def _screen_universe(filters: list, universe_data: dict, as_of: date) -> list:
    """Apply all filters to universe data and return passing tickers."""
    selected = []
    for ticker, data in universe_data.items():
        if _ticker_passes_filters(ticker, filters, data, as_of):
            selected.append(ticker)
    return selected[:30]  # cap at 30 per rebalance to avoid over-concentration


def _ticker_passes_filters(ticker: str, filters: list, data: dict, as_of: date) -> bool:
    if not data.get("prices"):
        return False
    info = data.get("info", {})
    fundamentals = _get_fundamentals_as_of(data.get("annual_financials", {}), as_of)

    for f in filters:
        val = _get_metric_value(f.metric, info, fundamentals, data.get("prices", []), as_of)
        if val is None:
            return False
        if not _apply_op(val, f.op, f.value):
            return False
    return True


def _get_fundamentals_as_of(annual_financials: dict, as_of: date) -> dict:
    """Get the most recent annual financials available as of a given date."""
    available_years = [y for y in annual_financials.keys() if int(y) <= as_of.year]
    if not available_years:
        return {}
    latest_year = max(available_years)
    current = annual_financials.get(latest_year, {})
    prev_year = str(int(latest_year) - 1)
    prev = annual_financials.get(prev_year, {})
    return {"current": current, "prev": prev, "year": latest_year}


def _get_metric_value(metric: str, info: dict, fundamentals: dict, prices: list, as_of: date):
    current = fundamentals.get("current", {})
    prev    = fundamentals.get("prev", {})

    if metric == "revenue_growth_yoy":
        cur_rev  = current.get("total_revenue")
        prev_rev = prev.get("total_revenue")
        if cur_rev and prev_rev and prev_rev != 0:
            return (cur_rev - prev_rev) / abs(prev_rev)
        return None

    if metric == "net_income_growth_yoy":
        cur  = current.get("net_income")
        prv  = prev.get("net_income")
        if cur is not None and prv and prv != 0:
            return (cur - prv) / abs(prv)
        return None

    if metric == "debt_to_equity":
        val = info.get("debtToEquity")
        return float(val) / 100 if val else None  # yFinance returns as percentage

    if metric == "pe_ratio":
        val = info.get("trailingPE")
        return float(val) if val else None

    if metric == "pb_ratio":
        val = info.get("priceToBook")
        return float(val) if val else None

    if metric == "roe":
        val = info.get("returnOnEquity")
        return float(val) * 100 if val else None

    if metric == "roa":
        val = info.get("returnOnAssets")
        return float(val) * 100 if val else None

    if metric == "dividend_yield":
        val = info.get("dividendYield")
        return float(val) * 100 if val else None

    if metric == "gross_margins":
        val = info.get("grossMargins")
        return float(val) * 100 if val else None

    if metric in ("price_return_1m", "price_return_3m", "price_return_6m", "price_return_1y"):
        period_map = {"price_return_1m": 21, "price_return_3m": 63, "price_return_6m": 126, "price_return_1y": 252}
        periods = period_map[metric]
        prices_before = [p for p in prices if _parse_date(p["date"]) <= as_of]
        if len(prices_before) < periods + 1:
            return None
        end_price = prices_before[-1]["close"]
        start_price = prices_before[-(periods + 1)]["close"]
        return ((end_price / start_price) - 1) * 100 if start_price else None

    if metric == "above_ma_200":
        prices_before = [p for p in prices if _parse_date(p["date"]) <= as_of]
        if len(prices_before) < 200:
            return None
        ma200 = sum(p["close"] for p in prices_before[-200:]) / 200
        current_price = prices_before[-1]["close"]
        return current_price - ma200  # > 0 means above MA

    if metric == "above_ma_50":
        prices_before = [p for p in prices if _parse_date(p["date"]) <= as_of]
        if len(prices_before) < 50:
            return None
        ma50 = sum(p["close"] for p in prices_before[-50:]) / 50
        current_price = prices_before[-1]["close"]
        return current_price - ma50

    return None


def _apply_op(value: float, op: str, threshold: float) -> bool:
    if op == ">":
        return value > threshold
    if op == ">=":
        return value >= threshold
    if op == "<":
        return value < threshold
    if op == "<=":
        return value <= threshold
    return False


def _get_price_on_date(data: dict, target_date: date) -> Optional[float]:
    prices = data.get("prices", [])
    if not prices:
        return None
    # Get closest price on or before target date
    candidates = [p for p in prices if _parse_date(p["date"]) <= target_date]
    if not candidates:
        return None
    return candidates[-1]["close"]


def _compute_portfolio_value(holdings: dict, cash: float, universe_data: dict, as_of: date) -> float:
    if not holdings:
        return cash
    total = 0.0
    alloc_per = cash / len(holdings) if holdings else 0
    for ticker, entry_price in holdings.items():
        current_price = _get_price_on_date(universe_data.get(ticker, {}), as_of)
        if current_price and entry_price:
            shares = alloc_per / entry_price
            total += shares * current_price
    return total if total > 0 else cash


def _build_series(prices: list) -> list:
    """Convert a ticker's price list into a value series starting at $10,000."""
    if not prices:
        return []
    start_price = prices[0]["close"]
    if not start_price:
        return []
    return [
        {"date": p["date"], "value": round(INITIAL_VALUE * (p["close"] / start_price), 2)}
        for p in prices[::21]  # monthly approximation
        if p["close"]
    ]


def _compute_stats(portfolio_series: list, benchmark_series: list, actions: list) -> dict:
    """Compute CAGR, Sharpe, Max Drawdown, Win Rate."""
    stats = {
        "cagr": 0.0,
        "sharpe": 0.0,
        "max_drawdown": 0.0,
        "win_rate": 0.0,
        "total_trades": 0,
        "benchmark_cagr": 0.0,
        "best_period": "N/A",
        "worst_period": "N/A",
    }

    if len(portfolio_series) >= 2:
        start_val = portfolio_series[0]["value"]
        end_val   = portfolio_series[-1]["value"]
        n_years   = len(portfolio_series) / 12.0
        if start_val > 0 and n_years > 0:
            stats["cagr"] = round((((end_val / start_val) ** (1 / n_years)) - 1) * 100, 2)

        # Monthly returns for Sharpe
        returns = []
        for i in range(1, len(portfolio_series)):
            prev = portfolio_series[i - 1]["value"]
            curr = portfolio_series[i]["value"]
            if prev > 0:
                returns.append((curr - prev) / prev)

        if returns:
            avg_r = sum(returns) / len(returns)
            std_r = math.sqrt(sum((r - avg_r) ** 2 for r in returns) / len(returns))
            stats["sharpe"] = round((avg_r / std_r * math.sqrt(12)) if std_r > 0 else 0.0, 2)

        # Max drawdown
        peak = portfolio_series[0]["value"]
        max_dd = 0.0
        best_pct = float("-inf")
        worst_pct = float("inf")
        best_label = ""
        worst_label = ""
        for i in range(len(portfolio_series)):
            v = portfolio_series[i]["value"]
            peak = max(peak, v)
            dd = (v - peak) / peak * 100
            max_dd = min(max_dd, dd)
            if i > 0:
                prev_v = portfolio_series[i - 1]["value"]
                pct = (v - prev_v) / prev_v * 100
                period_label = portfolio_series[i]["date"][:7]
                if pct > best_pct:
                    best_pct = pct
                    best_label = f"{period_label}: +{pct:.1f}%"
                if pct < worst_pct:
                    worst_pct = pct
                    worst_label = f"{period_label}: {pct:.1f}%"
        stats["max_drawdown"] = round(max_dd, 2)
        stats["best_period"]  = best_label or "N/A"
        stats["worst_period"] = worst_label or "N/A"

    # Win rate from SELL actions
    sells = [a for a in actions if a.action == "SELL"]
    stats["total_trades"] = len(sells)
    if sells:
        winners = sum(1 for a in sells if a.return_pct > 0)
        stats["win_rate"] = round(winners / len(sells) * 100, 1)

    # Benchmark CAGR
    if len(benchmark_series) >= 2:
        bstart = benchmark_series[0]["value"]
        bend   = benchmark_series[-1]["value"]
        n_years = len(benchmark_series) / 12.0
        if bstart > 0 and n_years > 0:
            stats["benchmark_cagr"] = round((((bend / bstart) ** (1 / n_years)) - 1) * 100, 2)

    return stats


def _build_reason(ticker: str, filters: list, data: dict, as_of: date) -> str:
    """Build human-readable reason why a ticker passed the filters."""
    parts = []
    info = data.get("info", {})
    fundamentals = _get_fundamentals_as_of(data.get("annual_financials", {}), as_of)
    for f in filters:
        val = _get_metric_value(f.metric, info, fundamentals, data.get("prices", []), as_of)
        if val is not None:
            label = f.metric.replace("_", " ").title()
            parts.append(f"{label} {f.op} {f.value} (actual: {val:.2f})")
    return "; ".join(parts) if parts else "Passed all screening filters"


def _generate_rebalance_dates(start: date, end: date, interval_months: int) -> list:
    """Generate rebalance dates spaced interval_months apart."""
    dates = []
    current = start
    from datetime import timedelta
    while current < end:
        dates.append(current)
        # Advance by interval_months
        month = current.month + interval_months
        year  = current.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        try:
            current = date(year, month, 1)
        except ValueError:
            break
    return dates


def _parse_date(date_str: str) -> date:
    """Parse YYYY-MM-DD string to date object."""
    return datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
