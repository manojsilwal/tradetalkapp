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
