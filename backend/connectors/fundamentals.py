import asyncio
import yfinance as yf
from typing import Dict, Any
from ..data_errors import InsufficientDataError
from .base import DataConnector
from ..connector_cache import get_cached, set_cached

class FundamentalsConnector(DataConnector):
    """
    Fetches fundamental financial data for a stock, including:
    - Total Cash Reserves
    - Total Debt
    """
    
    async def fetch_data(self, **kwargs) -> Dict[str, Any]:
        ticker_sym = kwargs.get("ticker", "GME")
        ticker = ticker_sym.upper()
        cached = get_cached("fundamentals", ticker)
        if cached is not None:
            return cached

        def get_fundamentals() -> Dict[str, Any]:
            t = yf.Ticker(ticker_sym)
            info = t.info or {}

            # yfinance provides totalCash and totalDebt in the info dictionary
            total_cash = info.get("totalCash")
            total_debt = info.get("totalDebt")
            if total_cash is None and total_debt is None:
                raise InsufficientDataError(
                    "yfinance",
                    f"Live balance-sheet data (cash/debt) unavailable for {ticker}.",
                    ticker=ticker,
                    missing=["total_cash", "total_debt"],
                )

            return {
                "total_cash": total_cash or 0,
                "total_debt": total_debt or 0,
            }

        try:
            # Run blocking yfinance call in a thread
            data = await asyncio.to_thread(get_fundamentals)
        except InsufficientDataError:
            raise
        except Exception as e:
            raise InsufficientDataError(
                "yfinance",
                f"Live fundamentals fetch failed for {ticker}: {e}",
                ticker=ticker,
                missing=["total_cash", "total_debt"],
            ) from e
            
        # Calculate ratio right away for convenience
        cash = float(data["total_cash"])
        debt = float(data["total_debt"])
        
        ratio = 0.0
        if debt > 0:
            ratio = cash / debt
        elif cash > 0 and debt == 0:
            ratio = 999.0 # Effectively infinite/very healthy
            
        data["cash_to_debt_ratio"] = round(ratio, 2)
        set_cached("fundamentals", data, ticker)
        return data
