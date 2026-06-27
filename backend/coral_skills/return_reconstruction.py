"""
CORAL Hub Skill: Return Reconstruction Engine

Responsible for:
- Fetching daily adjusted prices using yfinance.
- Constructing '13f_investable' clone returns without look-ahead bias.
- Calculating metrics: CAGR, ROIC proxy, Alpha vs SPY, Sharpe, Max Drawdown.
"""
import asyncio
import logging
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from backend.coral_agents import hub_add_note

logger = logging.getLogger(__name__)


def _quarter_freq() -> str:
    """Quarter-end resample alias ('QE' on pandas>=2.2, 'Q' on older versions)."""
    try:
        pd.tseries.frequencies.to_offset("QE")
        return "QE"
    except Exception:
        return "Q"


_Q_FREQ = _quarter_freq()

async def fetch_historical_prices(tickers: List[str], start_date: str, end_date: str) -> pd.DataFrame:
    """
    Fetch adjusted close prices for a list of tickers.
    """
    logger.info(f"[Return Reconstruction] Fetching prices for {len(tickers)} tickers from {start_date} to {end_date}")

    if not tickers:
        return pd.DataFrame()

    try:
        # yfinance download is blocking, so we wrap it
        def _download():
            return yf.download(tickers, start=start_date, end=end_date, auto_adjust=True, group_by='ticker')

        data = await asyncio.to_thread(_download)

        if data is None or data.empty:
            return pd.DataFrame()

        # Downstream uses xs('Close', level=1, axis=1), so normalize to a
        # (ticker, field) MultiIndex. Current yfinance returns that shape for
        # both single and multi ticker requests under group_by='ticker', but
        # older versions return flat columns for a single ticker.
        if isinstance(data.columns, pd.MultiIndex):
            return data
        data = data.copy()
        data.columns = pd.MultiIndex.from_product([[tickers[0]], data.columns])
        return data
    except Exception as e:
        logger.error(f"[Return Reconstruction] Error fetching prices: {e}")
        return pd.DataFrame()

def next_trading_day(date_str: str, days_buffer: int = 1) -> str:
    """
    Helper to simulate finding the next trading day.
    For 13F investable, we usually buy the day after the filing date.
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        dt += timedelta(days=days_buffer)
        # Skip weekends
        while dt.weekday() > 4:
            dt += timedelta(days=1)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return date_str

def calculate_clone_returns(
    portfolio_snapshots: List[Dict[str, Any]],
    prices_df: pd.DataFrame,
    benchmark_df: pd.DataFrame
) -> Dict[str, Any]:
    """
    Given a series of quarterly holdings and daily prices, compute the
    investable clone return.

    portfolio_snapshots expects:
    [
        {
            "filing_date": "2023-05-15",
            "report_period": "2023-03-31",
            "holdings": [{"ticker": "AAPL", "weight": 0.5}, ...]
        }, ...
    ]
    sorted chronologically.
    """
    if not portfolio_snapshots or prices_df.empty:
        return {"error": "Missing data for return calculation"}

    logger.info("[Return Reconstruction] Calculating 13F Investable Returns")

    # Sort chronologically by filing date
    snapshots = sorted(portfolio_snapshots, key=lambda x: x["filing_date"])

    # Initialize series tracking
    daily_returns = []
    dates = []

    for i, snapshot in enumerate(snapshots):
        # Investable mode: Rebalance on next trading day after filing date
        rebalance_date = next_trading_day(snapshot["filing_date"])

        # End date is the next rebalance date or today if it's the last snapshot
        if i < len(snapshots) - 1:
            end_date = next_trading_day(snapshots[i+1]["filing_date"])
        else:
            end_date = datetime.now().strftime("%Y-%m-%d")

        holdings = [h for h in snapshot["holdings"] if h.get("ticker") and h.get("weight", 0) > 0]

        # Rescale weights for mapped/priced subset
        total_mapped_weight = sum(h["weight"] for h in holdings)
        if total_mapped_weight == 0:
            continue

        rescaled_holdings = {h["ticker"]: h["weight"] / total_mapped_weight for h in holdings}

        try:
            # Extract relevant price window
            mask = (prices_df.index >= pd.to_datetime(rebalance_date)) & (prices_df.index < pd.to_datetime(end_date))
            period_prices = prices_df.loc[mask]

            if period_prices.empty:
                continue

            # Calculate daily returns for each asset
            asset_returns = period_prices.xs('Close', level=1, axis=1).pct_change().fillna(0)

            # Compute portfolio daily return
            port_returns = pd.Series(0.0, index=asset_returns.index)
            for ticker, weight in rescaled_holdings.items():
                if ticker in asset_returns.columns:
                    port_returns += asset_returns[ticker] * weight

            daily_returns.extend(port_returns.tolist())
            dates.extend(port_returns.index.tolist())
        except Exception as e:
            logger.warning(f"[Return Reconstruction] Period calc error {rebalance_date}-{end_date}: {e}")

    if not daily_returns:
        return {"error": "Return series computation failed or empty"}

    port_series = pd.Series(daily_returns, index=pd.to_datetime(dates))

    # Calculate Benchmark (SPY) Returns
    bench_returns = pd.Series(0.0, index=port_series.index)
    if not benchmark_df.empty:
        try:
            b_prices = benchmark_df.xs('Close', level=1, axis=1).pct_change().fillna(0)
            b_col = b_prices.columns[0]
            # Align indices
            aligned_b = b_prices[b_col].reindex(port_series.index).fillna(0)
            bench_returns = aligned_b
        except Exception as e:
            logger.warning(f"[Return Reconstruction] Benchmark alignment error: {e}")

    # Metrics Calculation
    cumulative_port = (1 + port_series).cumprod()
    cumulative_bench = (1 + bench_returns).cumprod()

    total_return = cumulative_port.iloc[-1] - 1
    bench_total_return = cumulative_bench.iloc[-1] - 1

    years = (port_series.index[-1] - port_series.index[0]).days / 365.25
    cagr = (cumulative_port.iloc[-1] ** (1 / years) - 1) if years > 0 else 0
    bench_cagr = (cumulative_bench.iloc[-1] ** (1 / years) - 1) if years > 0 else 0

    alpha = cagr - bench_cagr

    # Risk metrics
    rf = 0.04 # Risk free rate assumption
    daily_rf = rf / 252
    excess_returns = port_series - daily_rf
    sharpe = np.sqrt(252) * (excess_returns.mean() / port_series.std()) if port_series.std() != 0 else 0

    downside_returns = excess_returns[excess_returns < 0]
    sortino = np.sqrt(252) * (excess_returns.mean() / downside_returns.std()) if len(downside_returns) > 0 and downside_returns.std() != 0 else 0

    running_max = cumulative_port.cummax()
    drawdowns = (cumulative_port / running_max) - 1
    max_drawdown = drawdowns.min()

    # Positive quarters proxy (using 63 trading days per quarter)
    quarterly_returns = port_series.resample(_Q_FREQ).apply(lambda x: (1 + x).prod() - 1)
    pos_quarters = (quarterly_returns > 0).sum() / len(quarterly_returns) if len(quarterly_returns) > 0 else 0

    # Quarter-end downsampled series for charting (cumulative growth of $1).
    series = []
    try:
        q_port = cumulative_port.resample(_Q_FREQ).last()
        q_bench = cumulative_bench.reindex(q_port.index, method='ffill')
        q_running_max = q_port.cummax()
        q_drawdown = (q_port / q_running_max) - 1
        for idx in q_port.index:
            series.append({
                "periodEnd": idx.strftime("%Y-%m-%d"),
                "cumulativeValue": float(q_port.loc[idx]),
                "benchmarkCumulativeValue": float(q_bench.loc[idx]) if idx in q_bench.index else None,
                "drawdown": float(q_drawdown.loc[idx]),
            })
    except Exception as e:
        logger.warning(f"[Return Reconstruction] Series downsample error: {e}")

    hub_add_note(
        "technical",
        f"Reconstructed return series: CAGR={cagr:.2%}, Alpha={alpha:.2%}, Sharpe={sharpe:.2f}"
    )

    return {
        "metrics": {
            "cagr": float(cagr),
            "roicProxy": float(cumulative_port.iloc[-1]),
            "alphaVsBenchmark": float(alpha),
            "sharpe": float(sharpe),
            "sortino": float(sortino),
            "maxDrawdown": float(max_drawdown),
            "positiveQuarterRate": float(pos_quarters),
            "cumulativeReturn": float(total_return),
            "benchmarkCumulativeReturn": float(bench_total_return),
        },
        "cumulative_return": float(total_return),
        "benchmark_cumulative_return": float(bench_total_return),
        "series": series,
        "start_date": dates[0].strftime("%Y-%m-%d"),
        "end_date": dates[-1].strftime("%Y-%m-%d")
    }
