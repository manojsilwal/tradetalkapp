import asyncio
import yfinance as yf
from typing import Dict, Any
from .base import DataConnector

class FundamentalsConnector(DataConnector):
    """
    Fetches fundamental financial data for a stock, including:
    - Total Cash Reserves
    - Total Debt
    """
    
    async def fetch_data(self, **kwargs) -> Dict[str, Any]:
        ticker_sym = kwargs.get("ticker", "GME")
        
        def get_fundamentals() -> Dict[str, Any]:
            ticker = yf.Ticker(ticker_sym)
            info = ticker.info
            
            # yfinance provides totalCash and totalDebt in the info dictionary
            total_cash = info.get("totalCash", 0)
            total_debt = info.get("totalDebt", 0)
            
            return {
                "total_cash": total_cash,
                "total_debt": total_debt
            }
            
        try:
            # Run blocking yfinance call in a thread
            data = await asyncio.to_thread(get_fundamentals)
        except Exception as e:
            # Fallback values if API fails
            data = {"total_cash": 0, "total_debt": 0}
            
        # Calculate ratio right away for convenience
        cash = float(data["total_cash"])
        debt = float(data["total_debt"])
        
        ratio = 0.0
        if debt > 0:
            ratio = cash / debt
        elif cash > 0 and debt == 0:
            ratio = 999.0 # Effectively infinite/very healthy
            
        data["cash_to_debt_ratio"] = round(ratio, 2)
        return data
