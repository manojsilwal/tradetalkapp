from abc import ABC, abstractmethod
from typing import Dict, Any

class DataConnector(ABC):
    """
    Abstract base class for all external data connectors in TradeTalk.
    Enforces a standard asynchronous data fetching method.
    """
    
    @abstractmethod
    async def fetch_data(self, **kwargs) -> Dict[str, Any]:
        """
        Fetches data from an external API or mock source.
        Returns a dictionary customized for the respective AgentPair.
        """
        pass


def clean_dividend_yield(raw_val: Any) -> float:
    """
    Standardizes yfinance dividendYield value to percentage scale (e.g. 3.21 for 3.21%).
    Historically, yfinance returned dividendYield as a ratio (0.0321 = 3.21%).
    Newer versions return it as a percentage (3.21 = 3.21%).
    """
    if raw_val is None:
        return 0.0
    try:
        val = float(raw_val)
        if val <= 0.0:
            return 0.0
        # If it's already > 0.25 (e.g. 3.21), it is definitely in percentage form
        if val > 0.25:
            return val
        
        # Check yfinance version
        import yfinance as yf
        ver = getattr(yf, "__version__", "0.0.0")
        parts = [int(p) for p in ver.split(".") if p.isdigit()]
        if parts:
            if parts[0] >= 1:
                return val
            if parts[0] == 0:
                if len(parts) >= 2 and parts[1] >= 3:
                    return val
                if len(parts) >= 3 and parts[1] == 2 and parts[2] >= 30:
                    return val
        
        # Fallback for older versions returning ratios
        return val * 100.0
    except Exception:
        return 0.0

