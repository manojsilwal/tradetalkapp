"""
Backtest Engine v2 — condition-based buy/sell triggers with share-level P&L tracking.

Key improvements over v1:
  - forward_pe / pe_ratio computed from quarterly EPS history (trailing 12-month)
  - Separate sell_filters: sell when condition is met, not just at rebalance
  - Share-level tracking: exact shares bought, position value, dollar P&L per trade
  - Monthly checks when sell_filters present (event-driven exits)
  - Returns initial_investment, final_value, total_return_pct, total_return_dollars
  - Supports up to 20-year price history; PE accuracy limited to available EPS data (~5-8yr)
"""
import asyncio
import logging
import math
from datetime import datetime, date, timedelta
from typing import Optional
from .schemas import StrategyRules, BacktestAction, BacktestResult, FilterRule

logger = logging.getLogger(__name__)

INITIAL_VALUE = 10_000.0
SPY_TICKER    = "SPY"
MAX_POSITIONS = 30   # cap to avoid over-concentration


async def run_backtest(rules: StrategyRules, llm, ks) -> BacktestResult:
    from .connectors.backtest_data import fetch_backtest_data

    tickers = rules.universe if rules.universe else []
    if not tickers:
        from .connectors.backtest_data import SP500_UNIVERSE
        tickers = SP500_UNIVERSE

    logger.info(f"[BacktestEngine] {rules.name}: {len(tickers)} tickers | "
                f"{rules.start_date} → {rules.end_date} | "
                f"buy_filters={len(rules.filters)} sell_filters={len(rules.sell_filters)}")

    universe_task = fetch_backtest_data(tickers, rules.start_date, rules.end_date)
    spy_task      = fetch_backtest_data([SPY_TICKER], rules.start_date, rules.end_date)
    universe_data, spy_data = await asyncio.gather(universe_task, spy_task)

    actions, portfolio_series, final_value = await asyncio.to_thread(
        _simulate, rules, universe_data
    )

    benchmark_series = _build_benchmark_series(spy_data.get(SPY_TICKER, {}).get("prices", []))
    stats = _compute_stats(portfolio_series, benchmark_series, actions)

    total_return_pct     = round(((final_value / INITIAL_VALUE) - 1) * 100, 2)
    total_return_dollars = round(final_value - INITIAL_VALUE, 2)

    context_docs  = ks.query("strategy_backtests", rules.description, n_results=2)
    context_docs += ks.query("macro_snapshots", f"macro conditions {rules.start_date[:4]}", n_results=1)
    context = ks.format_context(context_docs)

    explanation = await llm.generate_backtest_explanation(rules.name, {**stats, "total_return_pct": total_return_pct, "final_value": final_value}, context)

    return BacktestResult(
        strategy=rules,
        actions=actions,
        initial_investment=INITIAL_VALUE,
        final_value=round(final_value, 2),
        total_return_pct=total_return_pct,
        total_return_dollars=total_return_dollars,
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


# ── Core simulation ───────────────────────────────────────────────────────────

def _simulate(rules: StrategyRules, universe_data: dict) -> tuple:
    """
    Condition-based simulation loop — runs synchronously (called via asyncio.to_thread).

    Logic:
      - Check interval = 1 month if sell_filters present, else rebalance_months
      - At rebalance dates: sell all (if no sell_filters), then re-screen for buys
      - At every check: evaluate sell_filters for current holdings → sell if triggered
      - Track exact shares, entry value, dollar P&L per trade

    Returns: (actions, portfolio_value_series, final_value)
    """
    start = _parse_date(rules.start_date)
    end   = _parse_date(rules.end_date)

    has_sell_filters = bool(rules.sell_filters)
    # Monthly checks for event-driven strategies; interval-based for periodic ones
    check_interval   = 1 if has_sell_filters else rules.rebalance_months
    check_dates      = _generate_dates(start, end, check_interval)
    rebalance_set    = {str(d) for d in _generate_dates(start, end, rules.rebalance_months)}

    # holdings: {ticker: {shares, entry_price, entry_value}}
    holdings: dict = {}
    cash     = INITIAL_VALUE
    actions  = []
    portfolio_series = []

    for check_date in check_dates:
        date_str = str(check_date)
        pv_start = _portfolio_value(holdings, cash, universe_data, check_date)

        # ── 1. Sell triggers (event-driven) ───────────────────────────────
        if has_sell_filters:
            for ticker in list(holdings.keys()):
                data = universe_data.get(ticker, {})
                if _passes_filters(rules.sell_filters, data, check_date):
                    sell_price = _price_on(data, check_date)
                    if sell_price and sell_price > 0:
                        h = holdings.pop(ticker)
                        shares     = h["shares"]
                        sell_value = shares * sell_price
                        pnl_dol    = sell_value - h["entry_value"]
                        pnl_pct    = (sell_price - h["entry_price"]) / h["entry_price"] * 100
                        cash      += sell_value
                        pv_after   = _portfolio_value(holdings, cash, universe_data, check_date)
                        reason     = _build_filter_reason(rules.sell_filters, data, check_date, prefix="Sell: ")
                        actions.append(BacktestAction(
                            action="SELL", ticker=ticker, date=date_str,
                            price=round(sell_price, 2), shares=round(shares, 4),
                            position_value=round(sell_value, 2),
                            profit_loss_dollars=round(pnl_dol, 2),
                            reason=reason, return_pct=round(pnl_pct, 2),
                            portfolio_value_after=round(pv_after, 2),
                        ))

        # ── 2. Periodic full rebalance sell (only when no sell_filters) ───
        if not has_sell_filters and date_str in rebalance_set and holdings:
            for ticker, h in list(holdings.items()):
                sell_price = _price_on(universe_data.get(ticker, {}), check_date)
                if sell_price and sell_price > 0:
                    shares     = h["shares"]
                    sell_value = shares * sell_price
                    pnl_dol    = sell_value - h["entry_value"]
                    pnl_pct    = (sell_price - h["entry_price"]) / h["entry_price"] * 100
                    cash      += sell_value
                    actions.append(BacktestAction(
                        action="SELL", ticker=ticker, date=date_str,
                        price=round(sell_price, 2), shares=round(shares, 4),
                        position_value=round(sell_value, 2),
                        profit_loss_dollars=round(pnl_dol, 2),
                        reason="Periodic rebalance",
                        return_pct=round(pnl_pct, 2),
                        portfolio_value_after=round(cash, 2),
                    ))
            holdings = {}

        # ── 3. Buy screen at rebalance dates ──────────────────────────────
        if date_str in rebalance_set and cash > 50:
            already_held = set(holdings.keys())
            candidates   = _screen(rules.filters, universe_data, check_date)
            new_buys     = [t for t in candidates if t not in already_held]

            if new_buys:
                alloc_per = cash / len(new_buys)
                for ticker in new_buys:
                    data      = universe_data.get(ticker, {})
                    buy_price = _price_on(data, check_date)
                    if buy_price and buy_price > 0:
                        shares = alloc_per / buy_price
                        holdings[ticker] = {
                            "shares":      shares,
                            "entry_price": buy_price,
                            "entry_value": alloc_per,
                        }
                        cash -= alloc_per
                        pv_after = _portfolio_value(holdings, cash, universe_data, check_date)
                        reason   = _build_filter_reason(rules.filters, data, check_date, prefix="Buy: ")
                        actions.append(BacktestAction(
                            action="BUY", ticker=ticker, date=date_str,
                            price=round(buy_price, 2), shares=round(shares, 4),
                            position_value=round(alloc_per, 2),
                            profit_loss_dollars=0.0, reason=reason,
                            return_pct=0.0,
                            portfolio_value_after=round(pv_after, 2),
                        ))
            elif not holdings:
                pv_after = _portfolio_value(holdings, cash, universe_data, check_date)
                actions.append(BacktestAction(
                    action="HOLD_CASH", ticker="CASH", date=date_str,
                    price=1.0, shares=round(cash, 2),
                    position_value=round(cash, 2),
                    profit_loss_dollars=0.0, reason="No stocks passed screening filters",
                    return_pct=0.0, portfolio_value_after=round(pv_after, 2),
                ))

        # ── 4. Monthly portfolio value snapshot ───────────────────────────
        pv = _portfolio_value(holdings, cash, universe_data, check_date)
        portfolio_series.append({"date": date_str, "value": round(pv, 2)})

    # Close-out: mark to market on end date
    final_value = _portfolio_value(holdings, cash, universe_data, end)
    return actions, portfolio_series, final_value


# ── Screening & filtering ──────────────────────────────────────────────────────

def _screen(filters: list, universe_data: dict, as_of: date) -> list:
    selected = [
        ticker for ticker, data in universe_data.items()
        if _passes_filters(filters, data, as_of)
    ]
    return selected[:MAX_POSITIONS]


def _passes_filters(filters: list, data: dict, as_of: date) -> bool:
    if not data.get("prices"):
        return False
    for f in filters:
        val = _metric(f.metric, data, as_of)
        if val is None:
            return False
        if not _op(val, f.op, f.value):
            return False
    return True


def _metric(metric: str, data: dict, as_of: date) -> Optional[float]:
    """Compute a single metric value as of a specific date."""
    info         = data.get("info", {})
    prices       = data.get("prices", [])
    q_eps        = data.get("quarterly_eps", [])
    ann_fin      = data.get("annual_financials", {})
    fundamentals = _fundamentals_as_of(ann_fin, as_of)
    current      = fundamentals.get("current", {})
    prev         = fundamentals.get("prev", {})

    # ── PE (trailing 12-month from quarterly EPS) ─────────────────────────
    if metric in ("forward_pe", "pe_ratio", "trailing_pe", "pe"):
        return _trailing_pe(prices, q_eps, as_of, info)

    # ── Income statement metrics ──────────────────────────────────────────
    if metric == "revenue_growth_yoy":
        cur_r, prv_r = current.get("total_revenue"), prev.get("total_revenue")
        if cur_r and prv_r and prv_r != 0:
            return (cur_r - prv_r) / abs(prv_r)
        return None

    if metric == "net_income_growth_yoy":
        cur_n, prv_n = current.get("net_income"), prev.get("net_income")
        if cur_n is not None and prv_n and prv_n != 0:
            return (cur_n - prv_n) / abs(prv_n)
        return None

    # ── Balance sheet / ratio metrics ────────────────────────────────────
    if metric == "debt_to_equity":
        v = info.get("debtToEquity")
        return float(v) / 100 if v else None

    if metric == "pb_ratio":
        v = info.get("priceToBook")
        return float(v) if v else None

    if metric == "roe":
        v = info.get("returnOnEquity")
        return float(v) * 100 if v else None

    if metric == "roa":
        v = info.get("returnOnAssets")
        return float(v) * 100 if v else None

    if metric == "dividend_yield":
        v = info.get("dividendYield")
        return float(v) * 100 if v else None

    if metric == "gross_margins":
        v = info.get("grossMargins")
        return float(v) * 100 if v else None

    # ── Price return metrics ──────────────────────────────────────────────
    period_map = {
        "price_return_1m":  21,
        "price_return_3m":  63,
        "price_return_6m":  126,
        "price_return_1y":  252,
    }
    if metric in period_map:
        periods = period_map[metric]
        before = [p for p in prices if _parse_date(p["date"]) <= as_of]
        if len(before) < periods + 1:
            return None
        end_p   = before[-1]["close"]
        start_p = before[-(periods + 1)]["close"]
        return ((end_p / start_p) - 1) * 100 if start_p else None

    # ── Moving average metrics ────────────────────────────────────────────
    if metric in ("above_ma_200", "above_ma_50"):
        window = 200 if "200" in metric else 50
        before = [p for p in prices if _parse_date(p["date"]) <= as_of]
        if len(before) < window:
            return None
        ma = sum(p["close"] for p in before[-window:]) / window
        return before[-1]["close"] - ma

    return None


def _trailing_pe(prices: list, quarterly_eps: list, as_of: date, info: dict) -> Optional[float]:
    """
    Compute trailing 12-month P/E ratio as of `as_of`.

    Uses the sum of the 4 most recent quarters of EPS available before `as_of`.
    yFinance quarterly data covers roughly the last 4-8 years, so PE signals
    are reliable for check dates within that window.

    For very recent check dates (within 1 year of today) we also fall back
    to the static forwardPE / trailingPE from yFinance info as a last resort.
    We deliberately avoid using static info for old historical dates because
    current-day PE is a poor proxy for PE 5+ years ago.
    """
    from datetime import date as date_cls
    price = _price_from_list(prices, as_of)
    if not price or price <= 0:
        return None

    # Get 4 most recent quarters of EPS reported before or on as_of
    available = sorted(
        [q for q in quarterly_eps if _parse_date(q["date"]) <= as_of],
        key=lambda q: q["date"]
    )[-4:]

    if len(available) >= 4:
        ttm_eps = sum(q["eps"] for q in available)
        if ttm_eps > 0:
            return round(price / ttm_eps, 2)
        # Negative or zero EPS → no meaningful PE

    # Only use static info PE for recent dates (within 1 year of today)
    # Using today's PE for 2019 data would give wrong signals.
    one_year_ago = date_cls(date_cls.today().year - 1, date_cls.today().month, 1)
    if as_of >= one_year_ago:
        for key in ("forwardPE", "trailingPE"):
            v = info.get(key)
            if v and float(v) > 0:
                return round(float(v), 2)
    return None


def _op(value: float, op: str, threshold: float) -> bool:
    if op == ">":  return value > threshold
    if op == ">=": return value >= threshold
    if op == "<":  return value < threshold
    if op == "<=": return value <= threshold
    return False


# ── Portfolio helpers ─────────────────────────────────────────────────────────

def _portfolio_value(holdings: dict, cash: float, universe_data: dict, as_of: date) -> float:
    total = cash
    for ticker, h in holdings.items():
        p = _price_on(universe_data.get(ticker, {}), as_of)
        if p and p > 0:
            total += h["shares"] * p
    return max(total, cash)


def _price_on(data: dict, target: date) -> Optional[float]:
    return _price_from_list(data.get("prices", []), target)


def _price_from_list(prices: list, target: date) -> Optional[float]:
    candidates = [p for p in prices if _parse_date(p["date"]) <= target]
    if not candidates:
        return None
    return candidates[-1]["close"]


# ── Utility ───────────────────────────────────────────────────────────────────

def _fundamentals_as_of(annual_financials: dict, as_of: date) -> dict:
    available = [y for y in annual_financials.keys() if int(y) <= as_of.year]
    if not available:
        return {}
    latest = max(available)
    prev   = str(int(latest) - 1)
    return {
        "current": annual_financials.get(latest, {}),
        "prev":    annual_financials.get(prev, {}),
        "year":    latest,
    }


def _build_filter_reason(filters: list, data: dict, as_of: date, prefix: str = "") -> str:
    parts = []
    for f in filters:
        val = _metric(f.metric, data, as_of)
        if val is not None:
            label = f.metric.replace("_", " ").title()
            parts.append(f"{label} {f.op} {f.value} (actual: {val:.2f})")
    return prefix + ("; ".join(parts) or "condition met")


def _generate_dates(start: date, end: date, interval_months: int) -> list:
    dates, current = [], start
    while current < end:
        dates.append(current)
        month = current.month + interval_months
        year  = current.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        try:
            current = date(year, month, 1)
        except ValueError:
            break
    return dates


def _parse_date(date_str: str) -> date:
    return datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()


# ── Statistics ────────────────────────────────────────────────────────────────

def _build_benchmark_series(prices: list) -> list:
    if not prices:
        return []
    start_price = prices[0]["close"]
    if not start_price:
        return []
    return [
        {"date": p["date"], "value": round(INITIAL_VALUE * (p["close"] / start_price), 2)}
        for p in prices[::21]
        if p["close"]
    ]


def _compute_stats(portfolio_series: list, benchmark_series: list, actions: list) -> dict:
    stats = {
        "cagr": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
        "win_rate": 0.0, "total_trades": 0,
        "benchmark_cagr": 0.0, "best_period": "N/A", "worst_period": "N/A",
    }

    if len(portfolio_series) >= 2:
        start_val = portfolio_series[0]["value"]
        end_val   = portfolio_series[-1]["value"]
        # Use actual date range so CAGR is correct regardless of check interval
        start_dt  = _parse_date(portfolio_series[0]["date"])
        end_dt    = _parse_date(portfolio_series[-1]["date"])
        n_years   = max((end_dt - start_dt).days / 365.25, 0.01)
        if start_val > 0:
            stats["cagr"] = round((((end_val / start_val) ** (1 / n_years)) - 1) * 100, 2)

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

        peak = portfolio_series[0]["value"]
        max_dd, best_pct, worst_pct = 0.0, float("-inf"), float("inf")
        best_label = worst_label = ""
        for i in range(len(portfolio_series)):
            v    = portfolio_series[i]["value"]
            peak = max(peak, v)
            dd   = (v - peak) / peak * 100
            max_dd = min(max_dd, dd)
            if i > 0:
                prev_v = portfolio_series[i - 1]["value"]
                pct    = (v - prev_v) / prev_v * 100
                label  = portfolio_series[i]["date"][:7]
                if pct > best_pct:
                    best_pct   = pct
                    best_label = f"{label}: +{pct:.1f}%"
                if pct < worst_pct:
                    worst_pct   = pct
                    worst_label = f"{label}: {pct:.1f}%"
        stats["max_drawdown"] = round(max_dd, 2)
        stats["best_period"]  = best_label or "N/A"
        stats["worst_period"] = worst_label or "N/A"

    sells = [a for a in actions if a.action == "SELL"]
    stats["total_trades"] = len(sells)
    if sells:
        stats["win_rate"] = round(sum(1 for a in sells if a.return_pct > 0) / len(sells) * 100, 1)

    if len(benchmark_series) >= 2:
        bstart   = benchmark_series[0]["value"]
        bend     = benchmark_series[-1]["value"]
        bstart_dt = _parse_date(benchmark_series[0]["date"])
        bend_dt   = _parse_date(benchmark_series[-1]["date"])
        n_years   = max((bend_dt - bstart_dt).days / 365.25, 0.01)
        if bstart > 0:
            stats["benchmark_cagr"] = round((((bend / bstart) ** (1 / n_years)) - 1) * 100, 2)

    return stats
