"""
Shared "truthful data" error contract.

Policy: no user-facing surface may return a final verdict/analysis built on
fabricated, placeholder, or silently-degraded data. When a required live data
source (yfinance, FinCrawler, FRED, news, LLM provider, ...) cannot deliver,
producers raise :class:`InsufficientDataError` instead of substituting
defaults. The FastAPI handler registered in ``backend/main.py`` converts it
into an HTTP 503 with a stable JSON body::

    {
        "error": "insufficient_data",
        "source": "yfinance",
        "message": "...",
        "ticker": "AAPL",
        "missing": ["price_history_6mo"]
    }
"""
from __future__ import annotations

from typing import List, Optional


class InsufficientDataError(Exception):
    """Raised when required live data could not be fetched or is incomplete."""

    def __init__(
        self,
        source: str,
        message: str,
        *,
        ticker: Optional[str] = None,
        missing: Optional[List[str]] = None,
    ) -> None:
        self.source = source
        self.message = message
        self.ticker = ticker.upper() if ticker else None
        self.missing = list(missing or [])
        super().__init__(message)

    def to_payload(self) -> dict:
        payload = {
            "error": "insufficient_data",
            "source": self.source,
            "message": self.message,
        }
        if self.ticker:
            payload["ticker"] = self.ticker
        if self.missing:
            payload["missing"] = self.missing
        return payload
